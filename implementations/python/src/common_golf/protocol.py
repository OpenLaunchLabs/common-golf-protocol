"""Transport-independent framing, versions and snapshot semantics."""
from __future__ import annotations

import copy
import enum
import json
import math
import struct
import secrets
import time
import uuid
import re
import datetime

MAX_MESSAGE_BYTES = 65535
NOT_INTERESTED = 1
MALFORMED = 2
SERVICE = "_common-golf._udp.local."
CHANNELS = {"info": 0, "shot": 1, "open_connect": 2, "club": 3, "status": 4,
            "control": 5, "state": 6, "result": 7, "flight": 8}
NAMES = {v: k for k, v in CHANNELS.items()}
VERSIONS = {"0.1.0": 1, "0.1.1": 1, "0.2.0": 2, "0.3.0": 3}


class Draft(enum.IntEnum):
    ONE = 1
    TWO = 2
    THREE = 3

    @property
    def alpn(self):
        return f"common-golf/draft-{self.value}"

    @property
    def version(self):
        return {1: "0.1.1", 2: "0.2.0", 3: "0.3.0"}[self.value]

    @classmethod
    def for_version(cls, version):
        return cls(VERSIONS[version])


class ProtocolError(ValueError):
    """A stream-local failure; transport adapters send STOP_SENDING(code)."""
    def __init__(self, message, code=MALFORMED):
        super().__init__(message)
        self.code = code
        self.messages = []


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ProtocolError("non-finite JSON number")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProtocolError("JSON object keys must be strings")
            _finite(item)
    elif isinstance(value, list):
        for item in value:
            _finite(item)


def decode_object(payload):
    try:
        # Explicit UTF-8 prevents json.loads from silently accepting UTF-16/32.
        value = json.loads(payload.decode("utf-8"), parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
        if not isinstance(value, dict):
            raise ValueError("message must be a JSON object")
        _finite(value)
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ProtocolError(f"invalid JSON object: {exc}") from exc


def frame(message, limit=MAX_MESSAGE_BYTES):
    if not isinstance(message, dict):
        raise ProtocolError("message must be a JSON object")
    try:
        _finite(message)
        body = json.dumps(message, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ProtocolError(f"invalid JSON object: {exc}") from exc
    if not 0 < len(body) <= limit:
        raise ProtocolError("message exceeds channel limit")
    return struct.pack(">I", len(body)) + body


class StreamDecoder:
    """Incremental bounded decoder; one instance per stream receiving half.

    Pass channel=2 for a response half (no channel prefix). on_channel is
    called as soon as the prefix arrives and may raise ProtocolError(code=1).
    Errors latch, FIN closes the parser, and subsequent data is ignored.
    A ProtocolError raised by feed() carries the messages decoded before the
    bad frame in its ``messages`` attribute; they are valid and should be
    delivered.
    """
    def __init__(self, channel=None, limit=MAX_MESSAGE_BYTES, on_channel=None):
        self.channel = channel
        self.limit = limit
        self.on_channel = on_channel
        self._header = bytearray()
        self._payload = bytearray()
        self._length = None
        self.ended = False
        self.error = None

    def feed(self, data, fin=False):
        if self.ended:
            return []
        if self.error:
            self.error.messages = []  # already returned with the first raise
            raise self.error
        messages = []
        cursor = 0
        try:
            while cursor < len(data):
                if self.channel is None or self._length is None:
                    take = min(4 - len(self._header), len(data) - cursor)
                    self._header.extend(data[cursor:cursor + take])
                    cursor += take
                    if len(self._header) < 4:
                        break
                    value = struct.unpack(">I", self._header)[0]
                    self._header.clear()
                    if self.channel is None:
                        self.channel = value
                        if self.on_channel:
                            self.on_channel(value)
                        continue
                    if not 0 < value <= self.limit:
                        raise ProtocolError("invalid frame length")
                    self._length = value
                take = min(self._length - len(self._payload), len(data) - cursor)
                self._payload.extend(data[cursor:cursor + take])
                cursor += take
                if len(self._payload) == self._length:
                    messages.append(decode_object(bytes(self._payload)))
                    self._payload.clear()
                    self._length = None
            if fin:
                if self.channel is None or self._header or self._length is not None:
                    raise ProtocolError("FIN inside a frame or channel prefix")
                self.ended = True
            return messages
        except ProtocolError as exc:
            self.error = exc
            self._payload.clear()
            exc.messages = messages
            raise


def apply_patch(snapshot, patch):
    """Status/state semantics: absent unchanged, null clears, nested replaces."""
    result = copy.deepcopy(snapshot)
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = copy.deepcopy(value)
    return result


class Snapshots:
    """Optional consumer state. Each shot update replaces its prior whole object."""
    def __init__(self):
        self.state = {}
        self.shots = {}

    def receive(self, channel, message, *, stream_open=False):
        if channel in (4, 6):
            self.state[channel] = apply_patch({} if stream_open else self.state.get(channel, {}), message)
        elif channel in (1, 3, 8) or (channel == 7 and "shot_uuid" in message):
            if "shot_uuid" in message:
                validate_shot_uuid(message["shot_uuid"])
                self.shots[(channel, "uuid", message["shot_uuid"])] = copy.deepcopy(message)
                return copy.deepcopy(message)
            key = (channel, "shot", message.get("shot_number"))
            if key[2] is None:
                key = (channel, "swing", message.get("swing_number"))
            self.shots[key] = copy.deepcopy(message)
        return copy.deepcopy(message)


def new_shot_uuid():
    """Create a UUIDv7 once at the measurement source; retain it with the event."""
    milliseconds = time.time_ns() // 1_000_000
    bits = ((milliseconds & ((1 << 48) - 1)) << 80) | (7 << 76)
    bits |= secrets.randbits(12) << 64 | (2 << 62) | secrets.randbits(62)
    return str(uuid.UUID(int=bits))


def validate_shot_uuid(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", value) is None:
        raise ProtocolError("shot_uuid must be canonical UUIDv7")


def validate_draft3_message(channel, message):
    if channel in (1, 7, 8) and "shot_uuid" not in message:
        raise ProtocolError("shot_uuid is required")
    if channel in (1, 3, 7, 8) and "shot_uuid" in message:
        validate_shot_uuid(message["shot_uuid"])
    if channel in (1, 3, 7, 8) and "measured_at" in message:
        value = message["measured_at"]
        match = (re.fullmatch(r"([0-9]{4})-([0-9]{2})-([0-9]{2})[Tt]([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.[0-9]+)?(?:[Zz]|\+00:00)", value)
                 if isinstance(value, str) else None)
        if match is None:
            raise ProtocolError("measured_at must be a UTC RFC 3339 timestamp")
        year, month, day, hour, minute, second = map(int, match.groups())
        try:
            datetime.date(year, month, day)
            if hour > 23 or minute > 59 or second > 60 or (second == 60 and (hour, minute) != (23, 59)):
                raise ValueError("invalid time")
        except ValueError as exc:
            raise ProtocolError("measured_at must be a UTC RFC 3339 timestamp") from exc
