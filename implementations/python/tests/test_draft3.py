"""Draft 3 wire identity, server-menu, and connection-lifetime regressions."""
import asyncio
from functools import partial
import struct

import pytest
from aioquic.asyncio import connect, serve
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.quic.events import StreamDataReceived
from common_golf import (Connection, Contract, Draft, Identity, OPEN_CONNECT,
                         ProtocolError, Snapshots, frame, new_shot_uuid, validate_shot_uuid)
from common_golf.compat import RawPublicKey
from common_golf.contracts import validate_menu
from common_golf.discovery import DiscoveredPeer, dial, listen


async def until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(.01)
    await asyncio.wait_for(wait(), 3)


class Recorder(Connection):
    def __init__(self, *args, peers=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.messages, self.raw, self.datagrams, self.errors = [], [], [], []
        if peers is not None:
            peers.append(self)

    def on_message(self, channel, message, sid):
        self.messages.append((channel, message))
        if channel == 1003 and self.role == 'sim' and 'Code' not in message:
            self.send(sid, {'Code': 200, 'Message': 'Shot received'})

    def on_raw_data(self, channel, sid, data, fin):
        self.raw.append((channel, data, fin))
        if fin and self.role == 'sim':
            self.send_raw(sid, b'reply', end_stream=True)

    def on_datagram(self, channel, sequence, payload):
        self.datagrams.append((channel, sequence, payload))

    def on_stream_error(self, sid, code):
        self.errors.append(code)


CONTRACTS = {c.identifier: c for c in [
    Contract('io.common-golf.interop-json-v1'),
    Contract('io.common-golf.interop-raw-v1', 'raw', bidirectional=True),
    Contract('io.common-golf.interop-datagram-v1', 'datagram'), OPEN_CONNECT]}
MENU = [{'channel': 1000 + index, 'contract': name} for index, name in enumerate(CONTRACTS)]


def test_default_apis_advertise_key_and_exchange_raw_keys_and_menu(tmp_path, monkeypatch):
    # Keep this a loopback transport test; capture registration instead of
    # depending on multicast routing for the API-default regression.
    advertisements = []
    class Registration:
        def __init__(self, **kwargs):
            pass
        async def async_register_service(self, info):
            advertisements.append(info)
        async def async_unregister_service(self, info):
            pass
        async def async_close(self):
            pass
    monkeypatch.setattr('common_golf.discovery.AsyncZeroconf', Registration)
    monkeypatch.setattr('common_golf.discovery.local_ip', lambda: '127.0.0.1')

    async def run():
        server_id, client_id = Identity(tmp_path/'server'), Identity(tmp_path/'client')
        with pytest.raises(ValueError, match='advertised'):
            client_id.configuration(is_client=True)
        with pytest.raises(ValueError, match='discovery key'):
            dial(DiscoveredPeer('missing-key', '127.0.0.1', 1, {}), client_id, Recorder)
        peers = []
        async with listen('default-device', server_id,
                partial(Recorder, role='device', contracts=CONTRACTS, menu=MENU, peers=peers)):
            info = advertisements[0]
            properties = {key.decode(): value.decode() for key, value in info.properties.items()}
            assert properties['key'] == server_id.fingerprint
            assert properties['alpn'] == 'draft-3'
            peer = DiscoveredPeer('default-device', '127.0.0.1', info.port, properties)
            async with dial(peer, client_id, partial(Recorder, role='sim', contracts=CONTRACTS)) as client:
                await until(lambda: peers and client.menu_ready)
                device = peers[0]
                assert client.draft == device.draft == Draft.THREE
                assert client._quic.configuration.alpn_protocols == ['common-golf/draft-3']
                assert client._quic.configuration.idle_timeout == 30
                assert isinstance(client._quic.tls._peer_certificate, RawPublicKey)
                assert isinstance(device._quic.tls._peer_certificate, RawPublicKey)
                assert client.peer_fingerprint == server_id.fingerprint
                assert device.peer_fingerprint == client_id.fingerprint
                assert client.channels_for_contract('io.common-golf.interop-json-v1') == [1000]
                device.publish(1000, {'defaults': True})
                await until(lambda: (1000, {'defaults': True}) in client.messages)
                assert client.info['protocol_version'] == device.info['protocol_version'] == '0.3.0'
    asyncio.run(run())


def test_menu_callback_applies_application_policy_to_authenticated_keys(tmp_path):
    async def run():
        server_id = Identity(tmp_path/'server')
        permitted_id = Identity(tmp_path/'permitted', 'same-name')
        other_id = Identity(tmp_path/'other', 'same-name')
        peers, selected_keys = [], []
        contract = Contract('com.example.diagnostics-v1')
        entries = [{'channel': 1000, 'contract': contract.identifier}]

        def select_menu(peer_key):
            selected_keys.append(peer_key)
            return entries if peer_key == permitted_id.fingerprint else []

        server = await serve('127.0.0.1', 0,
            configuration=server_id.configuration(is_client=False),
            create_protocol=partial(Recorder, role='device', peers=peers,
                                    contracts={contract.identifier: contract}, menu=select_menu))
        try:
            for identity in (permitted_id, other_id):
                async with connect('127.0.0.1', server._transport.get_extra_info('sockname')[1],
                    configuration=identity.configuration(is_client=True, expected_key=server_id.fingerprint),
                    create_protocol=partial(Recorder, role='sim', contracts={contract.identifier: contract})) as client:
                    await until(lambda: client.menu_ready)
                    device = peers[-1]
                    assert device.peer_fingerprint == identity.fingerprint
                    assert client.channels_for_contract(contract.identifier) == (
                        [1000] if identity is permitted_id else [])
                    if identity is permitted_id:
                        device.publish(1000, {'diagnostics': True})
                        await until(lambda: (1000, {'diagnostics': True}) in client.messages)
                    # A peer excluded from this menu can still receive standard data.
                    shot = {'type': 'shot', 'shot_uuid': new_shot_uuid(),
                            'ball_speed_meters_per_second': 60,
                            'vertical_launch_angle_degrees': 12, 'horizontal_launch_angle_degrees': 0}
                    device.publish(1, shot)
                    await until(lambda: (1, shot) in client.messages)
            assert selected_keys == [permitted_id.fingerprint, other_id.fingerprint]
        finally:
            server.close()
            await asyncio.sleep(.1)
    asyncio.run(run())


def test_raw_public_key_menu_json_raw_datagram_and_snapshot_exchange(tmp_path):
    async def run():
        server_id, client_id = Identity(tmp_path/'server'), Identity(tmp_path/'client')
        peers = []
        server = await serve('127.0.0.1', 0,
            configuration=server_id.configuration(is_client=False, draft=3),
            create_protocol=partial(Recorder, role='device', draft=3, contracts=CONTRACTS, menu=MENU, peers=peers))
        try:
            config = client_id.configuration(is_client=True, draft=3, expected_key=server_id.fingerprint)
            assert config.idle_timeout == 30 and config.max_datagram_frame_size == 1200
            async with connect('127.0.0.1', server._transport.get_extra_info('sockname')[1], configuration=config,
                create_protocol=partial(Recorder, role='sim', draft=3, contracts=CONTRACTS)) as client:
                await until(lambda: peers and client._menu_ready)
                device = peers[0]
                assert isinstance(client._quic.tls._peer_certificate, RawPublicKey)
                assert isinstance(device._quic.tls._peer_certificate, RawPublicKey)
                assert client.peer_fingerprint == server_id.fingerprint
                assert device.peer_fingerprint == client_id.fingerprint
                assert dict(client._quic.tls.received_extensions)[19] == b'\x02'
                assert dict(client._quic.tls.received_extensions)[20] == b'\x02'
                device.publish(1000, {'sample': 1})
                sid = device.open_raw(1001)
                device.send_raw(sid, b'\x00\xff\x80abc', end_stream=True)
                client.send_datagram(1002, b'\x00\xff')
                device.publish(1003, {'DeviceID': 'sample', 'ShotNumber': 1})
                identity = new_shot_uuid()
                first = {'type': 'shot', 'shot_uuid': identity, 'ball_speed_meters_per_second': 60,
                         'vertical_launch_angle_degrees': 12, 'horizontal_launch_angle_degrees': 0, 'estimated': ['ball_speed_meters_per_second']}
                device.publish(1, first)
                device.publish(1, {key: value for key, value in first.items() if key != 'estimated'})
                await until(lambda: len([m for ch, m in client.messages if ch == 1]) == 2
                            and device.datagrams and device.raw
                            and any(ch == 1003 and m.get('Code') == 200 for ch, m in device.messages))
                assert client.raw == [(1001, b'\x00\xff\x80abc', True)]
                assert device.raw == [(1001, b'reply', True)]
                assert device.datagrams == [(1002, 0, b'\x00\xff')]
                snapshots = Snapshots()
                for ch, message in client.messages:
                    snapshots.receive(ch, message)
                assert 'estimated' not in snapshots.shots[(1, 'uuid', identity)]
                assert not device.errors and not client.errors
                with pytest.raises(ValueError, match='reserved'):
                    device.open(2)
                with pytest.raises(ProtocolError, match='fixed'):
                    device.publish(0, {'type': 'info', 'protocol_version': '0.3.0', 'contracts': []})
        finally:
            server.close()
            await asyncio.sleep(.1)
    asyncio.run(run())


def test_key_requirement_and_pin_failure_precede_application_channels(tmp_path):
    async def run():
        server_id, client_id = Identity(tmp_path/'server'), Identity(tmp_path/'client')
        with pytest.raises(ValueError, match='advertised'):
            client_id.configuration(is_client=True, draft=3)
        with pytest.raises(ValueError, match='lowercase'):
            client_id.configuration(is_client=True, draft=3, expected_key='G'*64)
        peers = []
        server = await serve('127.0.0.1', 0, configuration=server_id.configuration(is_client=False, draft=3),
                             create_protocol=partial(Recorder, role='device', draft=3, peers=peers))
        try:
            async with connect('127.0.0.1', server._transport.get_extra_info('sockname')[1],
                configuration=client_id.configuration(is_client=True, draft=3, expected_key='0'*64),
                create_protocol=partial(Recorder, role='sim', draft=3)) as client:
                await asyncio.wait_for(client.wait_closed(), 2)
                assert not client._info_sent
                # An event queued behind HandshakeCompleted must remain gated.
                client.quic_event_received(StreamDataReceived(stream_id=3,
                    data=struct.pack('>I', 0) + frame({'type': 'info', 'protocol_version': '0.3.0'}),
                    end_stream=False))
                assert not client.messages
                assert not any(peer.messages for peer in peers)
        finally:
            server.close()
    asyncio.run(run())


def test_no_x509_fallback(tmp_path):
    async def run():
        server_id, client_id = Identity(tmp_path/'server'), Identity(tmp_path/'client')
        # A plain aioquic endpoint advertises the right ALPN but never negotiates RPK.
        server = await serve('127.0.0.1', 0, configuration=server_id.configuration(is_client=False, draft=3),
                             create_protocol=QuicConnectionProtocol)
        try:
            with pytest.raises(ConnectionError):
                async with connect('127.0.0.1', server._transport.get_extra_info('sockname')[1],
                    configuration=client_id.configuration(is_client=True, draft=3, expected_key=server_id.fingerprint),
                    create_protocol=partial(Recorder, role='sim', draft=3)):
                    pytest.fail('X.509 peer connected')
        finally:
            server.close()
    asyncio.run(run())


@pytest.mark.parametrize('menu', [
    [{'channel': value, 'contract': 'example.valid-v1'}] for value in [999, 2**32, 1000.0, '1000', True]
] + [[{'channel': 1000, 'contract': value}] for value in ['', 'A', 'foo bar', 'x'*129, 'ß']]
  + [[{'channel': 1000, 'contract': 'foo'}, {'channel': 1000, 'contract': 'bar'}]])
def test_malformed_menu_rejected_atomically(menu):
    with pytest.raises(ProtocolError):
        validate_menu(menu)


def test_menu_unknown_fields_and_duplicate_identifiers_are_allowed():
    assert validate_menu([{'channel': 1000, 'contract': 'foo', 'future': True},
                          {'channel': 1001, 'contract': 'foo'}]) == {1000: 'foo', 1001: 'foo'}


def test_uuid7_canonical_identity():
    for _ in range(20):
        value = new_shot_uuid()
        validate_shot_uuid(value)
        with pytest.raises(ProtocolError):
            validate_shot_uuid(value.replace('-', ''))
    assert Draft.THREE.alpn == 'common-golf/draft-3'


def test_custom_stream_overtakes_info_without_a_decline(tmp_path):
    async def run():
        class DelayedInfo(Recorder):
            def publish(self, channel, message, **kwargs):
                if channel == 0 and not self._info_sent:
                    sid = self.open(0)
                    self._info_sent = True
                    # Force the same arrival order as independently delivered QUIC streams.
                    asyncio.get_running_loop().call_later(.1, self.send, sid, message)
                    return sid
                return super().publish(channel, message, **kwargs)
            def on_connected(self):
                self.publish(1000, {'overtook_info': True})
        server_id, client_id = Identity(tmp_path/'server'), Identity(tmp_path/'client')
        peers = []
        server = await serve('127.0.0.1', 0, configuration=server_id.configuration(is_client=False, draft=3),
            create_protocol=partial(DelayedInfo, role='device', draft=3, contracts=CONTRACTS, menu=MENU, peers=peers))
        try:
            async with connect('127.0.0.1', server._transport.get_extra_info('sockname')[1],
                configuration=client_id.configuration(is_client=True, draft=3, expected_key=server_id.fingerprint),
                create_protocol=partial(Recorder, role='sim', draft=3, contracts=CONTRACTS)) as client:
                await until(lambda: any(state.pending for state in client._streams.values()))
                assert not client.messages and not client.errors
                await until(lambda: (1000, {'overtook_info': True}) in client.messages)
                assert client._menu_ready and not client.errors
                assert not client._quic._cgp_pending_menu
        finally:
            server.close()
    asyncio.run(run())


def test_code_one_decline_is_permanent_for_draft3(tmp_path):
    async def run():
        server_id, client_id = Identity(tmp_path/'server'), Identity(tmp_path/'client')
        peers=[]
        server=await serve('127.0.0.1',0,configuration=server_id.configuration(is_client=False,draft=3),
            create_protocol=partial(Recorder,role='device',draft=3,contracts=CONTRACTS,menu=MENU,peers=peers))
        try:
            async with connect('127.0.0.1',server._transport.get_extra_info('sockname')[1],
                configuration=client_id.configuration(is_client=True,draft=3,expected_key=server_id.fingerprint),
                create_protocol=partial(Recorder,role='sim',draft=3)) as client:
                await until(lambda: peers and client._menu_ready)
                device=peers[0]
                device.publish(1000,{'unsupported':True})
                await until(lambda: 1000 in device._declined_channels)
                with pytest.raises(ValueError,match='declined'):
                    device.open(1000)
                await asyncio.wait_for(device.ping(),1)
        finally:
            server.close()
    asyncio.run(run())


def test_application_decline_is_permanent_for_draft3(tmp_path):
    async def run():
        server_id, client_id = Identity(tmp_path/'server'), Identity(tmp_path/'client')
        peers = []
        server = await serve('127.0.0.1', 0, configuration=server_id.configuration(is_client=False, draft=3),
            create_protocol=partial(Recorder, role='device', draft=3, contracts=CONTRACTS, menu=MENU, peers=peers))
        try:
            async with connect('127.0.0.1', server._transport.get_extra_info('sockname')[1],
                configuration=client_id.configuration(is_client=True, draft=3, expected_key=server_id.fingerprint),
                create_protocol=partial(Recorder, role='sim', draft=3, contracts=CONTRACTS)) as client:
                await until(lambda: peers and client.menu_ready)
                device = peers[0]
                device.publish(1000, {'first': True})
                await until(lambda: (1000, {'first': True}) in client.messages)
                client.cancel(client._incoming[1000])
                await until(lambda: 1000 in device._declined_channels)
                assert 1000 in client._refused_channels
                # A hostile sender bypassing the API still cannot reopen it.
                sid = device._quic.get_next_available_stream_id(is_unidirectional=True)
                device._quic.send_stream_data(sid, struct.pack('>I', 1000) + frame({'reopened': True}))
                device.transmit()
                await until(lambda: 1 in client.errors)
                assert (1000, {'reopened': True}) not in client.messages
                await asyncio.wait_for(device.ping(), 1)
        finally:
            server.close()
    asyncio.run(run())


def test_incoming_measurement_identity_errors_are_stream_local(tmp_path):
    async def run():
        server_id, client_id = Identity(tmp_path/'server'), Identity(tmp_path/'client')
        peers = []
        server = await serve('127.0.0.1', 0, configuration=server_id.configuration(is_client=False, draft=3),
            create_protocol=partial(Recorder, role='device', draft=3, peers=peers))
        try:
            async with connect('127.0.0.1', server._transport.get_extra_info('sockname')[1],
                configuration=client_id.configuration(is_client=True, draft=3, expected_key=server_id.fingerprint),
                create_protocol=partial(Recorder, role='sim', draft=3)) as client:
                await until(lambda: peers and client.menu_ready)
                device = peers[0]
                identity = '017f22e2-79b0-7cc3-98c4-dc0c0c07398f'
                for channel in (1, 3, 7, 8):
                    sender, receiver = (client, device) if channel == 7 else (device, client)
                    invalid = [{'shot_uuid': value} for value in (identity.upper(), identity.replace('-7cc3-', '-4cc3-'), 123)]
                    if channel != 3:
                        invalid.append({})
                    invalid.extend({'shot_uuid': identity, 'measured_at': value} for value in
                                   ('2026-10-30T12:00:00-05:00', '2026-02-30T12:00:00Z', 'uptime:123', None))
                    for message in invalid:
                        old_errors = len(receiver.errors)
                        old_messages = len(receiver.messages)
                        sid = sender._quic.get_next_available_stream_id(is_unidirectional=True)
                        # Bypass local validation to exercise the receiving wire path.
                        payload = frame(message)
                        if message is invalid[-1]:
                            payload += b'\x00' * 4  # invalid object followed by invalid frame
                        sender._quic.send_stream_data(sid, struct.pack('>I', channel) + payload)
                        sender.transmit()
                        await until(lambda: len(receiver.errors) > old_errors)
                        assert receiver.errors[-1] == 2
                        assert len(receiver.messages) == old_messages
                    good = {'shot_uuid': identity, 'measured_at': '2026-10-30T12:00:00.123+00:00'}
                    sender.publish(channel, good, end_stream=True)
                    await until(lambda: (channel, good) in receiver.messages)
                device.publish(3, {'club_speed_meters_per_second': 30}, end_stream=True)
                await until(lambda: (3, {'club_speed_meters_per_second': 30}) in client.messages)
                await asyncio.wait_for(device.ping(), 1)
        finally:
            server.close()
    asyncio.run(run())


def test_datagram_congestion_bounds_round_robin_and_reliable_progress(tmp_path):
    async def run():
        definition = Contract('com.example.samples-v1', 'datagram')
        definitions = {definition.identifier: definition}
        menu = [{'channel': channel, 'contract': definition.identifier} for channel in range(1000, 1005)]
        server_id, client_id = Identity(tmp_path/'server'), Identity(tmp_path/'client')
        peers = []
        server = await serve('127.0.0.1', 0, configuration=server_id.configuration(is_client=False, draft=3),
            create_protocol=partial(Recorder, role='device', draft=3, contracts=definitions, menu=menu, peers=peers))
        try:
            async with connect('127.0.0.1', server._transport.get_extra_info('sockname')[1],
                configuration=client_id.configuration(is_client=True, draft=3, expected_key=server_id.fingerprint),
                create_protocol=partial(Recorder, role='sim', draft=3, contracts=definitions)) as client:
                await until(lambda: peers and client.menu_ready)
                device = peers[0]
                transmit = device.transmit
                device.transmit = lambda: None  # model a blocked network sender
                payload = b'x' * 1000
                for _ in range(16):
                    device.send_datagram(1000, payload)
                with pytest.raises(BufferError, match='channel'):
                    device.send_datagram(1000, payload)
                assert device._datagram_sequences._next[1000] == 16
                for channel in range(1001, 1004):
                    for _ in range(16):
                        device.send_datagram(channel, payload)
                with pytest.raises(BufferError, match='connection'):
                    device.send_datagram(1004, payload)
                assert 1004 not in device._datagram_sequences._next
                with pytest.raises(ValueError, match='payload budget'):
                    device.send_datagram(1004, b'x' * (device.datagram_payload_limit(1004) + 1))
                assert len(device._quic._datagrams_pending) == 64

                emitted, stream_packets = [], []
                write_datagram = device._quic._write_datagram_frame
                write_stream = device._quic._write_stream_frame
                def record_datagram(builder, data, frame_type):
                    result = write_datagram(builder, data, frame_type)
                    emitted.append((int.from_bytes(data[:4], 'big'), builder.packet_number))
                    return result
                def record_stream(**kwargs):
                    result = write_stream(**kwargs)
                    if result and kwargs['stream'].stream_id == reliable_sid:
                        stream_packets.append(kwargs['builder'].packet_number)
                    return result
                device._quic._write_datagram_frame = record_datagram
                device._quic._write_stream_frame = record_stream
                message = {'shot_uuid': new_shot_uuid(), 'opaque': 'x' * 12000}
                reliable_sid = device.publish(1, message, end_stream=True)
                device.transmit = transmit
                device.transmit()
                await until(lambda: len(emitted) == 64 and (1, message) in client.messages)
                assert [channel for channel, _ in emitted[:8]] == list(range(1000, 1004)) * 2
                assert not device._quic._datagrams_pending
                assert stream_packets and min(stream_packets) < emitted[-1][1]
                assert set(stream_packets) - {packet for _, packet in emitted}
        finally:
            server.close()
    asyncio.run(run())


def test_pre_menu_stream_count_credit_and_fragment_metadata_are_bounded(tmp_path):
    async def run():
        class DelayedFlood(Recorder):
            def publish(self, channel, message, **kwargs):
                if channel == 0 and not self._info_sent:
                    sid = self.open(0)
                    self._info_sent = True
                    asyncio.get_running_loop().call_later(.5, self.send, sid, message)
                    return sid
                return super().publish(channel, message, **kwargs)
            def on_connected(self):
                for index in range(200):
                    message = {'sample': index}
                    if index == 0:
                        message['fragmented'] = 'x' * 20000
                    # Flood distinct, mostly unbound handles without using the
                    # local API's already-validated menu admission.
                    sid = self._quic.get_next_available_stream_id(is_unidirectional=True)
                    self._quic.send_stream_data(sid, struct.pack('>I', 1000 + index) + frame(message), end_stream=True)
                self.transmit()
        server_id, client_id = Identity(tmp_path/'server'), Identity(tmp_path/'client')
        peers = []
        server = await serve('127.0.0.1', 0, configuration=server_id.configuration(is_client=False, draft=3),
            create_protocol=partial(DelayedFlood, role='device', draft=3, contracts=CONTRACTS, menu=MENU, peers=peers))
        try:
            async with connect('127.0.0.1', server._transport.get_extra_info('sockname')[1],
                configuration=client_id.configuration(is_client=True, draft=3, expected_key=server_id.fingerprint),
                create_protocol=partial(Recorder, role='sim', draft=3, contracts=CONTRACTS)) as client:
                await until(lambda: sum(bool(state.pending) for state in client._streams.values()) >= 100)
                assert not client.menu_ready and not client.errors
                assert client._quic._local_max_streams_uni.value == 128
                assert client._quic._local_max_streams_bidi.value == 128
                assert client._quic._local_max_data.value == client._quic.configuration.max_data
                assert sum(bool(state.pending) for state in client._streams.values()) <= 127
                assert all(len(state.pending) <= 1 for state in client._streams.values())
                await until(lambda: len(client.errors) >= 199)
                assert all(code == 1 for code in client.errors)
                assert client.menu_ready
                assert [(ch, message['sample']) for ch, message in client.messages if ch == 1000] == [(1000, 0)]
        finally:
            server.close()
    asyncio.run(run())
