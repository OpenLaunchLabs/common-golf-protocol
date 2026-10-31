"""QUIC adapter with bounded per-connection state for drafts 1 and 2."""
from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass, field

from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.quic.events import (HandshakeCompleted, StreamDataReceived, StreamReset,
                                StopSendingReceived, ConnectionTerminated)

from . import compat
from .identity import fingerprint, tier_of
from .protocol import (Draft, StreamDecoder, ProtocolError, frame,
                       MAX_MESSAGE_BYTES, NOT_INTERESTED)

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


class Connection(QuicConnectionProtocol):
    """Subclass callbacks and configure private JSON channels explicitly.

    Each connection owns its streams, peer identity, and application callbacks.
    """
    def __init__(self, *args, role="device", draft=Draft.TWO, info=None,
                 read_channels=None, private_channels=None, trusted=None,
                 max_send_buffer=262144, **kwargs):
        super().__init__(*args, **kwargs)
        if role not in _SENDS:
            raise ValueError("role must be device, controller, or sim")
        self.role, self.draft = role, Draft(draft)
        if max_send_buffer <= 0:
            raise ValueError("send buffer bound must be positive")
        self.max_send_buffer = max_send_buffer
        self.private_channels = dict(private_channels or {})
        if any(type(k) is not int or not 1000 <= k <= 0xffffffff or type(v) is not int or not 0 < v <= 0xffffffff
               for k, v in self.private_channels.items()):
            raise ValueError("private channels need uint32 IDs >=1000 and positive uint32 limits")
        self.read_channels = set(_READS[role] if read_channels is None else read_channels)
        if not self.read_channels <= _READS[role]:
            raise ValueError("read_channels violates channel ownership")
        if 0 not in self.read_channels or (role == "sim" and 1 not in self.read_channels):
            raise ValueError("info and the simulator baseline shot channel are required")
        self.info = {"type": "info", "protocol_version": self.draft.version, **(info or {})}
        self.trusted, self.peer_fingerprint, self.peer_tier = trusted or {}, None, "maker"
        self._streams, self._incoming, self._outgoing = {}, {}, {}
        self._info_sent = False
        self._keep_alive_task = self._cleanup_task = None
        compat.configure_connection(self._quic)

    def _collect_streams(self):
        for sid, state in list(self._streams.items()):
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
        if channel not in _SENDS[self.role] and channel not in self.private_channels:
            raise ValueError("channel is not owned by this role or not configured")
        if channel in self._outgoing:
            raise ValueError("channel already open")
        if bidirectional is None:
            bidirectional = channel == 2
        if channel < 1000 and bidirectional != (channel == 2):
            raise ValueError("channel 2 uses a bidirectional stream; others are unidirectional")
        sid = self._quic.get_next_available_stream_id(is_unidirectional=not bidirectional)
        self._streams[sid] = _Stream(channel=channel, local=True, recv_closed=not bidirectional)
        self._outgoing[channel] = sid
        self._quic.send_stream_data(sid, struct.pack(">I", channel))
        self.transmit()
        return sid

    def _limit(self, channel):
        return self.private_channels.get(channel, MAX_MESSAGE_BYTES)

    def _sending(self, sid):
        state = self._streams[sid]
        if state.rejected:
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

    def send(self, stream_id, message, *, end_stream=False):
        state = self._sending(stream_id)
        if state is None:
            return False
        channel = state.channel
        self._write(stream_id, frame(message, self._limit(channel)), end_stream)
        if channel == 0:
            self._info_sent = True
        return True

    def publish(self, channel, message, *, end_stream=False):
        frame(message, self._limit(channel))
        sid = self._outgoing.get(channel)
        if sid is None:
            sid = self.open(channel)
        self.send(sid, message, end_stream=end_stream)
        return sid

    def finish(self, stream_id):
        state = self._streams.get(stream_id)
        if state is None or state.rejected or state.send_closed:
            return
        self._sending(stream_id)
        self._write(stream_id, b"", True)

    def cancel(self, stream_id, code=NOT_INTERESTED):
        """Cancel a stream without affecting the rest of the connection."""
        state = self._streams.get(stream_id)
        if state is None or state.rejected:
            return
        if not state.recv_closed:
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
        wanted = channel in self.read_channels or channel in self.private_channels
        if channel < 1000 and bool(sid & 2) == (channel == 2):
            wanted = False
        if not wanted or channel in self._incoming:
            raise ProtocolError("channel not read, wrong stream type, or already open", NOT_INTERESTED)
        self._incoming[channel] = sid

    def quic_event_received(self, event):
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
            self.peer_tier = tier_of(self.peer_fingerprint, self.trusted)
            self.publish(0, self.info)
            self._keep_alive_task = asyncio.create_task(self._keep_alive())
            self._cleanup_task = asyncio.create_task(self._cleanup())
            self.on_connected()
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
                    self._accept_channel(sid, channel)
                channel = state.channel
                if state.decoder is None:
                    state.decoder = StreamDecoder(channel=channel, limit=self._limit(channel))
                for message in state.decoder.feed(data, fin=event.end_stream):
                    self.on_message(channel, message, sid)
                if event.end_stream:
                    state.recv_closed = True
                    state.decoder = None
                    if self._incoming.get(channel) == sid:
                        del self._incoming[channel]
            except ProtocolError as exc:
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
            self.on_declined(state.channel if state else None, event.stream_id, event.error_code)
            if state:
                state.send_closed = True
                self._release(event.stream_id)
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
                await asyncio.sleep(15)
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
