import json
import struct

import pytest

from common_golf import (Draft, ProtocolError, StreamDecoder, frame,
                         apply_patch, Snapshots, Identity)

SHOT = {"type": "shot", "shot_number": 1, "ball_speed_meters_per_second": 50,
        "vertical_launch_angle_degrees": 12, "horizontal_launch_angle_degrees": 0}


@pytest.mark.parametrize("draft,version", [(1, "0.1.0"), (1, "0.1.1"), (2, "0.2.0")])
def test_revisions(draft, version):
    assert Draft.for_version(version) == draft
    assert Draft(draft).alpn == f"common-golf/draft-{draft}"


def test_every_split_and_multiple_frames():
    message = {"snowman": "☃", "extra": [1, None, True]}
    wire = struct.pack(">I", 1) + frame(message) + frame({})
    for split in range(len(wire) + 1):
        parser = StreamDecoder()
        assert parser.feed(wire[:split]) + parser.feed(wire[split:], fin=True) == [message, {}]
        assert parser.channel == 1
        assert parser.feed(b"ignored", fin=True) == []
    parser = StreamDecoder()
    messages = []
    for byte in wire:
        messages += parser.feed(bytes([byte]))
    assert messages == [message, {}]
    assert parser.feed(b"", fin=True) == []


def test_channel_two_reply_has_no_prefix():
    assert StreamDecoder(channel=2).feed(frame({"Code": 200, "Message": "ok"}), fin=True) == [{"Code": 200, "Message": "ok"}]


@pytest.mark.parametrize("payload", [b"[]", b"null", b"42", b"", b"{}{}", b'{"a":NaN}', b'{"a":Infinity}', b'{"a":1e999}', b'{"a":"\xff"}', b'{}\x00', '{}'.encode("utf-16")])
def test_bad_json(payload):
    parser = StreamDecoder()
    with pytest.raises(ProtocolError) as error:
        parser.feed(b"\0\0\0\1" + struct.pack(">I", len(payload)) + payload)
    assert error.value.code == 2
    with pytest.raises(ProtocolError):
        parser.feed(b"\0")


def test_messages_before_malformed_frame_are_kept():
    shot = {"type": "shot", "shot_number": 1}
    parser = StreamDecoder(channel=1)
    with pytest.raises(ProtocolError) as error:
        parser.feed(frame(shot) + struct.pack(">I", 2) + b"{!")
    assert error.value.messages == [shot]
    with pytest.raises(ProtocolError) as error:
        parser.feed(frame(shot))
    assert error.value.messages == []


@pytest.mark.parametrize("wire", [b"", b"\0", b"\0\0\0", b"\0\0\0\1\0", b"\0\0\0\1\0\0\0\3{}"])
def test_truncated_fin(wire):
    with pytest.raises(ProtocolError):
        StreamDecoder().feed(wire, fin=True)


def test_limit_boundaries_and_early_rejection():
    msg = {"x": "a" * (65535 - 8)}
    assert len(frame(msg)) == 65539
    assert StreamDecoder(channel=1).feed(frame(msg), fin=True) == [msg]
    with pytest.raises(ProtocolError):
        frame({"x": "a" * (65536 - 8)})
    with pytest.raises(ProtocolError):
        StreamDecoder(channel=1).feed(struct.pack(">I", 65536))


def test_private_limit_and_decline_before_payload():
    parser = StreamDecoder(limit=70000)
    message = {"x": "a" * 66000}
    assert parser.feed(struct.pack(">I", 1000) + frame(message, 70000)) == [message]
    def decline(channel):
        raise ProtocolError("unsupported", 1)
    with pytest.raises(ProtocolError) as error:
        StreamDecoder(on_channel=decline).feed(struct.pack(">I", 999))
    assert error.value.code == 1


@pytest.mark.parametrize("message", [{}, dict(SHOT, ball_speed_meters_per_second=-1),
    dict(SHOT, estimated=["missing"], future_data={}), {"type": "status", "battery_percent": 101}])
def test_framing_does_not_enforce_schemas(message):
    assert StreamDecoder(channel=1).feed(frame(message)) == [message]


def test_finite_numbers():
    for message in [[], {1: "bad key"}, {"value": float("nan")}, {"value": float("inf")}]:
        with pytest.raises(ProtocolError):
            frame(message)


def test_patch_and_snapshot_replace():
    old = {"type": "state", "player": {"name": "A", "dexterity": "left"}, "ready": True}
    patch = {"type": "state", "player": {"name": "B"}, "ready": None}
    assert apply_patch(old, patch) == {"type": "state", "player": {"name": "B"}}
    assert old["player"]["name"] == "A"
    store = Snapshots()
    store.receive(1, dict(SHOT, final=False, optional=2))
    store.receive(1, dict(SHOT, final=True))
    assert "optional" not in store.shots[(1, "shot", 1)]
    store.receive(6, old)
    store.receive(6, {"type": "state", "ready": False}, stream_open=True)
    assert "player" not in store.state[6]


def test_identity_persistence(tmp_path):
    identity = Identity(tmp_path)
    assert len(identity.fingerprint) == 64
    assert Identity(tmp_path, "another name").fingerprint == identity.fingerprint
    assert (tmp_path / "identity.key").stat().st_mode & 0o777 == 0o600
    for draft in (1, 2):
        config = identity.configuration(is_client=True, draft=draft)
        assert config.idle_timeout == 90
        assert config.alpn_protocols == [Draft(draft).alpn]
