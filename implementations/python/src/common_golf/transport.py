"""QUIC adapter with bounded per-connection state for drafts 1, 2, and 3."""
from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass, field

from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.quic.events import (HandshakeCompleted, StreamDataReceived, StreamReset,
                                StopSendingReceived, ConnectionTerminated, DatagramFrameReceived)

from . import compat
from .identity import fingerprint, tier_of
from .protocol import (Draft, StreamDecoder, ProtocolError, frame,
                       MAX_MESSAGE_BYTES, NOT_INTERESTED, validate_draft3_message, validate_shot_uuid)
from .contracts import validate_menu, validate_reads, decode_datagram, DatagramSequences, OPEN_CONNECT

_SENDS = {"device": {0, 1, 2, 3, 4, 5, 8}, "controller": {0, 5}, "sim": {0, 6, 7}}
_READS = {"device": {0, 6, 7}, "controller": {0, 6, 7}, "sim": {0, 1, 2, 3, 4, 5, 8}}


@dataclass
class _Stream:
    channel: object = None
    local: bool = False
    prefix: bytearray = field(default_factory=bytearray)
    decoder: object = None
    recv_closed: bool = False
    send_closed: bool = False
    rejected: bool = False
    stopped: bool = False
    raw: bool = False
    raw_received: int = 0
    raw_sent: int = 0
    pending: list = field(default_factory=list)


class Connection(QuicConnectionProtocol):
    """Subclass callbacks and configure private JSON channels explicitly.

    Each connection owns its streams, peer identity, and application callbacks.
    """
    def __init__(self, *args, role="device", draft=Draft.THREE, info=None,
                 read_channels=None, private_channels=None, trusted=None,
                 max_send_buffer=262144, contracts=None, menu=None, expected_key=None, **kwargs):
        super().__init__(*args, **kwargs)
        if role not in _SENDS:
            raise ValueError("role must be device, controller, or sim")
        self.role, self.draft = role, Draft(draft)
        if max_send_buffer <= 0:
            raise ValueError("send buffer bound must be positive")
        self.max_send_buffer = max_send_buffer
        self._listener = not self._quic.configuration.is_client
        self.expected_key = expected_key or getattr(self._quic.configuration, "_cgp_expected_key", None)
        self.contracts = dict(contracts or {})
        if any(name != contract.identifier for name, contract in self.contracts.items()):
            raise ValueError("contract dictionary keys must match exact identifiers")
        if self.draft != Draft.THREE and (contracts or menu is not None):
            raise ValueError("menus require draft 3")
        if self.draft == Draft.THREE and private_channels:
            raise ValueError("draft 3 uses listener menu contracts")
        self._menu_option = menu
        self._menu = None
        self._menu_ready = False
        self._menu_failed = False
        self._bindings = {}
        self._declined_channels, self._refused_channels = set(), set()
        self._datagram_sequences = DatagramSequences()
        self.private_channels = dict(private_channels or {})
        if any(type(k) is not int or not 1000 <= k <= 0xffffffff or type(v) is not int or not 0 < v <= 0xffffffff
               for k, v in self.private_channels.items()):
            raise ValueError("private channels need uint32 IDs >=1000 and positive uint32 limits")
        self.read_channels = set(_READS[role] if read_channels is None else read_channels)
        if self.draft == Draft.THREE:
            self.read_channels.discard(2)
        if not self.read_channels <= _READS[role]:
            raise ValueError("read_channels violates channel ownership")
        if 0 not in self.read_channels or (role == "sim" and 1 not in self.read_channels):
            raise ValueError("info and the simulator baseline shot channel are required")
        self.info = {"type": "info", "protocol_version": self.draft.version, **(info or {})}
        self.trusted, self.peer_fingerprint, self.peer_tier = trusted or {}, None, "maker"
        self._streams, self._incoming, self._outgoing = {}, {}, {}
        self._info_sent = False
        self._application_ready = False
        self._keep_alive_task = self._cleanup_task = None
        compat.configure_connection(self._quic)

    def _collect_streams(self):
        for sid, state in list(self._streams.items()):
            if state.pending and not self._menu_ready:
                continue  # QUIC FIN does not consume an unread pre-menu record.
            if (state.recv_closed or state.send_closed or state.rejected) and compat.stream_finished(self._quic, sid):
                self._release(sid)
                del self._streams[sid]

    def _release(self, sid):
        state = self._streams.get(sid)
        if state:
            if self._incoming.get(state.channel) == sid:
                del self._incoming[state.channel]
            if self._outgoing.get(state.channel) == sid:
                del self._outgoing[state.channel]

    def open(self, channel, *, bidirectional=None):
        if type(channel) is not int or not 0 <= channel <= 0xffffffff:
            raise ValueError("channel must fit uint32")
        if not self._info_sent and channel != 0:
            raise ValueError("send info before opening other channels")
        contract = self._bindings.get(channel) if self.draft == Draft.THREE else None
        if self.draft == Draft.THREE:
            if channel in self._declined_channels:
                raise ValueError("channel was declined for this connection")
            if channel >= 1000:
                if not self._menu_ready or contract is None:
                    raise ValueError("custom channel is not available in the listener menu")
                if self.role not in contract.openers or contract.format == "datagram":
                    raise ValueError("contract does not permit this stream opener")
                if bidirectional is None:
                    bidirectional = contract.bidirectional
                if bidirectional != contract.bidirectional:
                    raise ValueError("stream direction differs from the contract")
            elif channel == 2:
                raise ValueError("channel 2 is reserved")
        if contract is None:
            if channel not in _SENDS[self.role] and channel not in self.private_channels:
                raise ValueError("channel is not owned by this role or not configured")
            if bidirectional is None:
                bidirectional = channel == 2
            if channel < 1000 and bidirectional != (channel == 2):
                raise ValueError("channel 2 uses a bidirectional stream; others are unidirectional")
        raw = contract is not None and contract.format in ("raw", "raw_datagram")
        if not raw and channel in self._outgoing:
            raise ValueError("channel already open")
        if raw and sum(state.raw and not state.rejected for state in self._streams.values()) >= 16:
            raise BufferError("active raw stream limit reached")
        sid = self._quic.get_next_available_stream_id(is_unidirectional=not bidirectional)
        self._streams[sid] = _Stream(channel=channel, local=True, recv_closed=not bidirectional, raw=raw)
        if not raw:
            self._outgoing[channel] = sid
        self._quic.send_stream_data(sid, struct.pack(">I", channel))
        self.transmit()
        return sid

    def _limit(self, channel):
        contract = self._bindings.get(channel)
        return contract.max_bytes if contract else self.private_channels.get(channel, MAX_MESSAGE_BYTES)

    def _sending(self, sid):
        state = self._streams[sid]
        if state.rejected or state.stopped:
            return None
        if state.send_closed:
            raise ValueError("stream sending half has ended")
        if not state.local and sid & 2:
            raise ValueError("cannot reply on a unidirectional stream")
        return state

    def _write(self, sid, data, end_stream=False):
        if compat.queued_stream_bytes(self._quic) + len(data) > self.max_send_buffer:
            raise BufferError("connection reliable send buffer is full")
        self._quic.send_stream_data(sid, data, end_stream=end_stream)
        if end_stream:
            self._streams[sid].send_closed = True
            channel = self._streams[sid].channel
            if self._outgoing.get(channel) == sid:
                del self._outgoing[channel]
        self.transmit()

    def _send_frame(self, stream_id, data, end_stream):
        state = self._sending(stream_id)
        if state is None:
            return False
        self._write(stream_id, data, end_stream)
        if state.channel == 0:
            self._info_sent = True
        return True

    def send(self, stream_id, message, *, end_stream=False):
        state = self._sending(stream_id)
        if state is None:
            return False
        if state.raw:
            raise ValueError("use send_raw for raw streams")
        self._validate_outgoing(state.channel, message)
        return self._send_frame(stream_id, frame(message, self._limit(state.channel)), end_stream)

    def publish(self, channel, message, *, end_stream=False):
        self._validate_outgoing(channel, message)
        data = frame(message, self._limit(channel))  # validate before opening a stream
        sid = self._outgoing.get(channel)
        if sid is None:
            sid = self.open(channel)
        self._send_frame(sid, data, end_stream)
        return sid

    def finish(self, stream_id):
        state = self._streams.get(stream_id)
        if state is None or state.rejected or state.send_closed:
            return
        self._sending(stream_id)
        self._write(stream_id, b"", True)

    def cancel(self, stream_id, code=NOT_INTERESTED):
        """Cancel a stream; code 0 cancels one record, default 1 declines its channel."""
        state = self._streams.get(stream_id)
        if state is None or state.rejected:
            return
        if not state.recv_closed:
            if self.draft == Draft.THREE and code == NOT_INTERESTED:
                self._refused_channels.add(state.channel)
            self._quic.stop_stream(stream_id, code)
        if not state.send_closed and (state.local or not stream_id & 2):
            self._quic.reset_stream(stream_id, code)
            state.send_closed = True
        state.rejected = True
        self._release(stream_id)
        self.transmit()

    def _accept_channel(self, sid, channel):
        state = self._streams[sid]
        state.channel = channel
        contract = self._bindings.get(channel) if self.draft == Draft.THREE else None
        if self.draft == Draft.THREE and channel >= 1000:
            remote_role = "sim" if self.role in ("device", "controller") else "device"
            wanted = (contract is not None and remote_role in contract.openers
                      and contract.format != "datagram" and bool(sid & 2) != contract.bidirectional)
            state.raw = contract is not None and contract.format in ("raw", "raw_datagram")
            if state.raw and sum(s.raw and not s.rejected for s in self._streams.values()) > 16:
                wanted = False
        else:
            wanted = channel in self.read_channels or channel in self.private_channels
            if channel < 1000 and bool(sid & 2) == (channel == 2):
                wanted = False
        if (not wanted or channel in self._refused_channels
                or (not state.raw and channel in self._incoming)):
            raise ProtocolError("channel not read, wrong stream type, or already open", NOT_INTERESTED)
        if state.raw:
            self.on_raw_stream(channel, sid, not bool(sid & 2))
        else:
            self._incoming[channel] = sid

    def _validate_outgoing(self, channel, message):
        if self.draft != Draft.THREE:
            return
        contract = self._bindings.get(channel)
        if contract is not None and contract.format != "json":
            raise ValueError("JSON publish requires a JSON contract")
        validate_draft3_message(channel, message)
        self._validate_custom_identity(channel, message)
        if channel == 0:
            self._validate_info_hints(message)
            if self._listener and self._menu is not None:
                if validate_menu(message.get("contracts", [])) != self._menu:
                    raise ProtocolError("listener menu is fixed for the connection")

    def _install_menu(self, message):
        if self._menu_failed:
            raise ProtocolError("initial menu was invalid")
        try:
            mapping = validate_menu(message.get("contracts", []))
            if self._menu is not None and mapping != self._menu:
                raise ProtocolError("listener menu is fixed for the connection")
        except ProtocolError:
            if self._menu is None:
                self._menu_failed = True
                self._menu_ready = True
                self._flush_pending()
            raise
        self._menu = mapping
        self._bindings = {channel: self.contracts[name] for channel, name in mapping.items()
                          if name in self.contracts and self.contracts[name].permits(self.peer_fingerprint)}
        first = not self._menu_ready
        self._menu_ready = True
        self._flush_pending()
        if first:
            self.on_menu_ready()

    def _flush_pending(self):
        compat.release_menu_streams(self._quic)
        for sid, state in list(self._streams.items()):
            pending, state.pending = state.pending, []
            if pending:
                # Admission was deferred after decoding only the channel prefix.
                state.channel = None
                channel = pending[0][0]
                for index, (_, data, fin) in enumerate(pending):
                    self.quic_event_received(StreamDataReceived(
                        stream_id=sid, data=(struct.pack(">I", channel) if index == 0 else b"") + data,
                        end_stream=fin))

    def _receive_message(self, channel, message, sid):
        if self.draft == Draft.THREE:
            validate_draft3_message(channel, message)
            self._validate_custom_identity(channel, message)
            if channel == 0:
                self._validate_info_hints(message)
                if not self._listener:
                    self._install_menu(message)
        self.on_message(channel, message, sid)

    def _validate_custom_identity(self, channel, message):
        contract = self._bindings.get(channel)
        if contract and contract.identifier == OPEN_CONNECT.identifier and "shot_uuid" in message:
            validate_shot_uuid(message["shot_uuid"])

    @staticmethod
    def _validate_info_hints(message):
        if "reads_contracts" in message:
            validate_reads(message["reads_contracts"])
        if "reads" in message:
            values = message["reads"]
            if not isinstance(values, list) or any(type(value) is not int or value not in (0, 1, 3, 4, 5, 6, 7, 8) for value in values):
                raise ProtocolError("invalid standard-channel reads hint")

    @property
    def menu_ready(self):
        return self._menu_ready and not self._menu_failed

    def channels_for_contract(self, identifier):
        """Supported handles for an exact identifier, after the listener menu arrives."""
        return sorted(channel for channel, contract in self._bindings.items() if contract.identifier == identifier)

    def open_raw(self, channel):
        contract = self._bindings.get(channel)
        if contract is None or contract.format not in ("raw", "raw_datagram"):
            raise ValueError("channel has no raw-stream contract")
        return self.open(channel, bidirectional=contract.bidirectional)

    def send_raw(self, stream_id, payload, *, end_stream=False):
        state = self._sending(stream_id)
        if state is None:
            return False
        data = bytes(payload)
        if not state.raw or state.raw_sent + len(data) > self._limit(state.channel):
            raise ValueError("raw stream contract payload limit exceeded")
        self._write(stream_id, data, end_stream)
        state.raw_sent += len(data)
        return True

    def send_datagram(self, channel, payload):
        contract = self._bindings.get(channel)
        if (not self._menu_ready or contract is None or contract.format not in ("datagram", "raw_datagram")
                or self.role not in contract.openers or channel in self._declined_channels):
            raise ValueError("channel has no writable datagram contract")
        payload = bytes(payload)
        limit = self.datagram_payload_limit(channel)
        if len(payload) > limit:
            raise ValueError("datagram exceeds transport payload budget")
        # Check before sequence allocation: a rejected send consumes no sequence.
        compat.check_datagram_capacity(self._quic, channel)
        data = self._datagram_sequences.encode(channel, payload, limit=limit + 12)
        self._quic.send_datagram_frame(data)
        self.transmit()

    def datagram_payload_limit(self, channel):
        """Current maximum application payload, excluding the 12-byte header."""
        contract = self._bindings.get(channel)
        if contract is None or contract.format not in ("datagram", "raw_datagram"):
            raise ValueError("channel has no datagram contract")
        return max(0, min(compat.datagram_limit(self._quic) - 12, contract.max_bytes))

    def quic_event_received(self, event):
        if (self.draft == Draft.THREE and not self._application_ready
                and isinstance(event, (StreamDataReceived, DatagramFrameReceived))):
            # QUIC can queue application events in the same receive batch as a
            # handshake-policy failure. Closing must not expose those bytes.
            return
        if isinstance(event, HandshakeCompleted):
            if event.alpn_protocol != self.draft.alpn:
                self.close(error_code=0x178, reason_phrase="unexpected ALPN")
                return
            certificate = compat.peer_certificate(self._quic)
            try:
                self.peer_fingerprint = fingerprint(certificate) if certificate else None
            except ValueError:
                self.close(error_code=0x12a, reason_phrase="Ed25519 identity required")
                return
            if self.draft == Draft.THREE:
                if (self.peer_fingerprint is None or not compat.remote_supports_datagrams(self._quic)
                        or (not self._listener and self.peer_fingerprint != self.expected_key)):
                    self.close(error_code=0x12a, reason_phrase="draft 3 identity/key/datagram requirement failed")
                    return
            self.peer_tier = tier_of(self.peer_fingerprint, self.trusted)
            if self.draft == Draft.THREE and self._listener:
                entries = self._menu_option(self.peer_fingerprint, self.peer_tier) if callable(self._menu_option) else self._menu_option
                if entries is not None:
                    self.info["contracts"] = entries
                self._menu = validate_menu(self.info.get("contracts", []))
                self._bindings = {channel: self.contracts[name] for channel, name in self._menu.items()
                                  if name in self.contracts and self.contracts[name].permits(self.peer_fingerprint)}
                if len(self._bindings) != len(self._menu):
                    self.close(error_code=2, reason_phrase="listener offered unsupported or unauthorized contract")
                    return
                self._menu_ready = True
            self._application_ready = True
            self.publish(0, self.info)
            self._keep_alive_task = asyncio.create_task(self._keep_alive())
            self._cleanup_task = asyncio.create_task(self._cleanup())
            self.on_connected()
            if self.draft == Draft.THREE and self._listener:
                self.on_menu_ready()
        elif isinstance(event, DatagramFrameReceived):
            if self.draft != Draft.THREE or not self._menu_ready:
                return
            try:
                channel, sequence, payload = decode_datagram(event.data)
            except ProtocolError:
                return
            contract = self._bindings.get(channel)
            remote_role = "sim" if self.role in ("device", "controller") else "device"
            if (contract and contract.format in ("datagram", "raw_datagram")
                    and remote_role in contract.openers and len(payload) <= contract.max_bytes):
                self.on_datagram(channel, sequence, payload)
        elif isinstance(event, StreamDataReceived):
            sid = event.stream_id
            state = self._streams.setdefault(sid, _Stream(send_closed=bool(sid & 2)))
            if state.rejected or state.recv_closed:
                return
            try:
                data = event.data
                if state.channel is None:
                    take = min(4 - len(state.prefix), len(data))
                    state.prefix.extend(data[:take])
                    data = data[take:]
                    if len(state.prefix) < 4:
                        if event.end_stream:
                            raise ProtocolError("FIN inside channel prefix")
                        return
                    channel = struct.unpack(">I", state.prefix)[0]
                    state.prefix.clear()
                    if self.draft == Draft.THREE and channel >= 1000 and not self._menu_ready:
                        state.channel = channel
                    else:
                        self._accept_channel(sid, channel)
                channel = state.channel
                if self.draft == Draft.THREE and channel >= 1000 and not self._menu_ready:
                    compat.pending_menu_stream(self._quic, sid)
                    # Coalesce chunks while credit is frozen: metadata remains
                    # proportional to bounded streams, not packet fragmentation.
                    if state.pending:
                        payload = state.pending[0][1]
                        payload.extend(data)
                        state.pending[0] = (channel, payload, event.end_stream)
                    else:
                        state.pending.append((channel, bytearray(data), event.end_stream))
                    return
                if state.raw:
                    state.raw_received += len(data)
                    if state.raw_received > self._limit(channel):
                        raise ProtocolError("raw stream exceeds contract limit")
                    if data or event.end_stream:
                        self.on_raw_data(channel, sid, bytes(data), event.end_stream)
                    if event.end_stream:
                        state.recv_closed = True
                    return
                if state.decoder is None:
                    state.decoder = StreamDecoder(channel=channel, limit=self._limit(channel))
                for message in state.decoder.feed(data, fin=event.end_stream):
                    self._receive_message(channel, message, sid)
                if event.end_stream:
                    state.recv_closed = True
                    state.decoder = None
                    if self._incoming.get(channel) == sid:
                        del self._incoming[channel]
            except ProtocolError as exc:
                if self.draft == Draft.THREE and exc.code == NOT_INTERESTED:
                    self._refused_channels.add(state.channel)
                # Frames before the malformed one were valid; deliver them first.
                for message in exc.messages:
                    try:
                        self._receive_message(state.channel, message, sid)
                    except ProtocolError as message_error:
                        # Framing succeeded, but draft-specific identity/menu
                        # validation may still reject an earlier object.
                        exc = message_error
                        break
                state.recv_closed = event.end_stream
                if not event.end_stream:
                    self._quic.stop_stream(sid, exc.code)
                    self.transmit()
                if not sid & 2 and not state.send_closed:
                    self._quic.reset_stream(sid, exc.code)
                    state.send_closed = True
                    self.transmit()
                state.rejected = True
                state.decoder = None
                state.prefix.clear()
                self._release(sid)
                self.on_stream_error(sid, exc.code)
        elif isinstance(event, StopSendingReceived):
            state = self._streams.get(event.stream_id)
            if state:
                if self.draft == Draft.THREE and event.error_code == NOT_INTERESTED:
                    self._declined_channels.add(state.channel)
                # Later sends on this stream return False, as for declined streams.
                state.send_closed = state.stopped = True
                self._release(event.stream_id)
            self.on_declined(state.channel if state else None, event.stream_id, event.error_code)
        elif isinstance(event, StreamReset):
            state = self._streams.get(event.stream_id)
            if state:
                state.recv_closed = True
                state.decoder = None
                state.prefix.clear()
                self._release(event.stream_id)
                self.on_stream_cancelled(state.channel, event.stream_id, event.error_code)
        elif isinstance(event, ConnectionTerminated):
            for task in (self._keep_alive_task, self._cleanup_task):
                if task:
                    task.cancel()
            self._streams.clear()
            self._incoming.clear()
            self._outgoing.clear()
            self.on_disconnected(event.error_code, event.reason_phrase)

    async def _cleanup(self):
        try:
            while not self._closed.is_set():
                await asyncio.sleep(0.05)
                self._collect_streams()
        except asyncio.CancelledError:
            pass

    async def _keep_alive(self):
        try:
            while not self._closed.is_set():
                await asyncio.sleep(5 if self.draft == Draft.THREE else 15)
                if not self._closed.is_set():
                    await self.ping()
        except (ConnectionError, asyncio.CancelledError):
            pass

    def on_connected(self):
        """TLS identity ready and local info sent; channels may now be opened."""
    def on_message(self, channel, message, stream_id):
        """Complete validated JSON object."""
    def on_declined(self, channel, stream_id, code):
        """Peer declined this sending half; connection remains usable."""
    def on_stream_cancelled(self, channel, stream_id, code):
        """Release any application buffers for this reset receiving half."""
    def on_stream_error(self, stream_id, code):
        """Malformed or unsupported incoming stream was discarded."""
    def on_disconnected(self, code, reason):
        """Transport closed."""

    def on_menu_ready(self):
        """Listener menu is fixed; supported custom channels can now be opened."""
    def on_raw_stream(self, channel, stream_id, bidirectional):
        """Incoming bounded raw stream; chunks follow through on_raw_data."""
    def on_raw_data(self, channel, stream_id, payload, end_stream):
        """Incremental raw bytes, including a possible empty final chunk."""
    def on_datagram(self, channel, sequence, payload):
        """Unreliable custom payload; loss/reorder/duplicate policy belongs to the contract."""
