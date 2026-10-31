"""Exercise transport lifecycle on real UDP/QUIC, not mocked parser callbacks."""
import asyncio
import datetime
import struct
from functools import partial
import pytest

from aioquic.asyncio import connect, serve
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import NameOID

from common_golf import Connection, Identity, frame

SHOT = {"type": "shot", "shot_number": 1, "ball_speed_meters_per_second": 50,
        "vertical_launch_angle_degrees": 12, "horizontal_launch_angle_degrees": 0}


async def until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.01)
    await asyncio.wait_for(wait(), 3)


class Recorder(Connection):
    def __init__(self, *args, peers=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.messages, self.errors, self.declined = [], [], []
        if peers is not None:
            peers.append(self)

    def on_message(self, channel, message, stream_id):
        self.messages.append((channel, message))

    def on_stream_error(self, stream_id, code):
        self.errors.append(code)

    def on_declined(self, channel, stream_id, code):
        self.declined.append(code)


class ResendOnDecline(Recorder):
    """Sending on a stopped stream is a declined send, not an exception."""
    def on_declined(self, channel, stream_id, code):
        super().on_declined(channel, stream_id, code)
        self.resent = getattr(self, "resent", []) + [self.send(stream_id, {"type": "info"})]


def test_channel_failures_are_stream_local_and_connections_independent(tmp_path):
    async def run():
        identity = Identity(tmp_path / "server")
        client_identity = Identity(tmp_path / "client")
        # Both peers must authenticate even when neither has a reliable clock.
        for ident in (identity, client_identity):
            key = serialization.load_pem_private_key(ident.key_path.read_bytes(), password=None)
            subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "expired")])
            cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                    .public_key(key.public_key()).serial_number(1)
                    .not_valid_before(datetime.datetime(2000, 1, 1))
                    .not_valid_after(datetime.datetime(2001, 1, 1)).sign(key, None))
            ident.cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        peers = []
        server = await serve("127.0.0.1", 0, configuration=identity.configuration(is_client=False),
                             create_protocol=partial(Recorder, role="sim", peers=peers))
        port = server._transport.get_extra_info("sockname")[1]
        try:
            async with connect("127.0.0.1", port, configuration=client_identity.configuration(is_client=True),
                               create_protocol=partial(Recorder, role="device")) as a:
                async with connect("127.0.0.1", port, configuration=client_identity.configuration(is_client=True),
                                   create_protocol=partial(Recorder, role="device")) as b:
                    a.publish(1, SHOT)
                    b.publish(1, SHOT)
                    await until(lambda: len(peers) == 2 and all(any(ch == 1 for ch, _ in p.messages) for p in peers))
                    def inject(channel, payload, *, fin=False, bidi=False):
                        sid = a._quic.get_next_available_stream_id(is_unidirectional=not bidi)
                        a._quic.send_stream_data(sid, struct.pack(">I", channel) + payload, end_stream=fin)
                        a.transmit()
                    inject(1, frame(SHOT))  # duplicate open channel
                    inject(999, frame({}))  # reserved
                    inject(6, frame({"type": "state"}))  # wrong owner
                    inject(2, frame({}))  # wrong stream direction
                    inject(3, struct.pack(">I", 65536))  # oversized before allocation
                    inject(4, struct.pack(">I", 3) + b"{}", fin=True)
                    await until(lambda: len(peers[0].errors) >= 6)
                    inject(4, struct.pack(">I", 2) + b"{!")  # invalid UTF-8 JSON object
                    inject(8, struct.pack(">I", 0))  # zero-length body
                    await until(lambda: len(peers[0].errors) >= 8)
                    assert peers[0].errors.count(1) == 4
                    assert peers[0].errors.count(2) == 4
                    await until(lambda: len(a.declined) >= 7)
                    assert a.declined.count(1) == 4
                    # Reopen the malformed channel and continue the existing shot channel.
                    a.publish(3, {"type": "club", "swing_number": 1, "club_speed_meters_per_second": 40})
                    a.publish(1, dict(SHOT, shot_number=2))
                    b.publish(1, dict(SHOT, shot_number=2))
                    await until(lambda: all(any(ch == 1 and msg["shot_number"] == 2 for ch, msg in p.messages) for p in peers))
                    assert any(ch == 3 for ch, _ in peers[0].messages)
                    assert not peers[1].errors
                    maximum = dict(SHOT, shot_number=3, padding="")
                    maximum["padding"] = "a" * (65535 - (len(frame(maximum)) - 4))
                    assert len(frame(maximum)) == 65539
                    a.publish(1, maximum)
                    await until(lambda: any(ch == 1 and msg["shot_number"] == 3 for ch, msg in peers[0].messages))
                    assert all(p.peer_fingerprint == client_identity.fingerprint for p in peers)
                    assert a.peer_fingerprint == identity.fingerprint
        finally:
            server.close()
            await asyncio.sleep(0.1)
    asyncio.run(run())


@pytest.mark.parametrize("sdk", [False, True])
def test_duplicate_stop_sending_is_idempotent_over_real_quic(tmp_path, sdk):
    """Legal repeated STOP frames must not hit aioquic's repeated-reset assert."""
    async def run():
        from aioquic.asyncio.protocol import QuicConnectionProtocol
        from aioquic.quic.events import StreamDataReceived, StopSendingReceived
        from common_golf.compat import configure_connection
        class RepeatedStop(QuicConnectionProtocol):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                configure_connection(self._quic)
                self.seen = set()
            def quic_event_received(self, event):
                if isinstance(event, StreamDataReceived) and event.stream_id not in self.seen:
                    self.seen.add(event.stream_id)
                    # Two separately encrypted packets, before processing any
                    # reply. This forces duplicate frames over the real path.
                    for _ in range(2):
                        self._quic.stop_stream(event.stream_id, 1)
                        self.transmit()
        class PlainProbe(QuicConnectionProtocol):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                configure_connection(self._quic)
                self.stops = []
            def quic_event_received(self, event):
                if isinstance(event, StopSendingReceived):
                    self.stops.append(event.stream_id)
        server_id, client_id = Identity(tmp_path / "server"), Identity(tmp_path / "client")
        errors = []
        loop = asyncio.get_running_loop()
        original_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _, context: errors.append(context))
        server = await serve("127.0.0.1", 0, configuration=server_id.configuration(is_client=False), create_protocol=RepeatedStop)
        try:
            factory = partial(ResendOnDecline, role="device") if sdk else PlainProbe
            async with connect("127.0.0.1", server._transport.get_extra_info("sockname")[1],
                               configuration=client_id.configuration(is_client=True), create_protocol=factory) as client:
                if not sdk:
                    sid = client._quic.get_next_available_stream_id(is_unidirectional=True)
                    client._quic.send_stream_data(sid, b"probe")
                    client.transmit()
                await until(lambda: len(client.declined if sdk else client.stops) >= 2)
                if sdk:
                    assert client.resent and not any(client.resent), client.resent
                # Unrelated transport work keeps progressing after both STOPs.
                await asyncio.wait_for(client.ping(), 1)
                assert not errors, errors
        finally:
            server.close()
            await asyncio.sleep(0.1)
            loop.set_exception_handler(original_handler)
        assert not errors, errors
    asyncio.run(run())


def test_runtime_delivers_json_without_schema_conformance(tmp_path):
    async def run():
        identity = Identity(tmp_path / "server")
        client_identity = Identity(tmp_path / "client")
        peers = []
        server = await serve("127.0.0.1", 0, configuration=identity.configuration(is_client=False),
                             create_protocol=partial(Recorder, role="sim", peers=peers))
        try:
            port = server._transport.get_extra_info("sockname")[1]
            async with connect("127.0.0.1", port, configuration=client_identity.configuration(is_client=True),
                               create_protocol=partial(Recorder, role="device")) as sender:
                messages = [{}, {"type": "shot", "ball_speed_meters_per_second": -1},
                            {"type": "shot", "estimated": ["missing"], "future": {"x": True}}]
                for message in messages:
                    sender.publish(1, message)
                await until(lambda: peers and len([m for ch, m in peers[0].messages if ch == 1]) == len(messages))
                assert [m for ch, m in peers[0].messages if ch == 1] == messages
                assert not peers[0].errors
        finally:
            server.close()
    asyncio.run(run())


@pytest.mark.parametrize("draft", [1, 2])
def test_private_json_bidirectional_reopen_and_stream_cleanup(tmp_path, draft):
    async def run():
        class Echo(Recorder):
            def on_message(self, channel, message, stream_id):
                super().on_message(channel, message, stream_id)
                if channel == 1000 and self.role == "sim":
                    self.send(stream_id, message, end_stream=True)
        identity = Identity(tmp_path / "server")
        client_identity = Identity(tmp_path / "client")
        peers = []
        options = {"draft": draft, "private_channels": {1000: 70000}}
        server = await serve("127.0.0.1", 0,
            configuration=identity.configuration(is_client=False, draft=draft),
            create_protocol=partial(Echo, role="sim", peers=peers, **options))
        try:
            async with connect("127.0.0.1", server._transport.get_extra_info("sockname")[1],
                configuration=client_identity.configuration(is_client=True, draft=draft),
                create_protocol=partial(Echo, role="device", **options)) as sender:
                with pytest.raises(ValueError, match="not configured"):
                    sender.open(1001)
                for number in range(16):
                    message = {"number": number, "padding": "x" * (66000 if number == 0 else 10)}
                    sid = sender.open(1000, bidirectional=True)
                    sender.send(sid, message, end_stream=True)
                    await until(lambda: (1000, message) in sender.messages)
                    await until(lambda: sid not in sender._streams and sid not in peers[0]._streams)
                assert len(sender._streams) == 2  # both info streams remain open
                assert not peers[0].errors and not sender.errors
                sender.publish(1, SHOT)
                await until(lambda: (1, SHOT) in peers[0].messages)
        finally:
            server.close()
            await asyncio.sleep(0.1)
    asyncio.run(run())
