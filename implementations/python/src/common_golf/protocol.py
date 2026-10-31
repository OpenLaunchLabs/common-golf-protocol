"""Transport-independent framing, versions and snapshot semantics."""
from __future__ import annotations

import copy
import enum
import json
import math
import struct

MAX_MESSAGE_BYTES = 65535
NOT_INTERESTED = 1
MALFORMED = 2
SERVICE = "_common-golf._udp.local."
CHANNELS = {"info": 0, "shot": 1, "open_connect": 2, "club": 3, "status": 4,
            "control": 5, "state": 6, "result": 7, "flight": 8}
NAMES = {v: k for k, v in CHANNELS.items()}
VERSIONS = {"0.1.0": 1, "0.1.1": 1, "0.2.0": 2}


class Draft(enum.IntEnum):
    ONE = 1
    TWO = 2

    @property
    def alpn(self):
        return f"common-golf/draft-{self.value}"

    @property
    def version(self):
        return {1: "0.1.1", 2: "0.2.0"}[self.value]

    @classmethod
    def for_version(cls, version):
        return cls(VERSIONS[version])


class ProtocolError(ValueError):
    """A stream-local failure; transport adapters send STOP_SENDING(code)."""
    def __init__(self, message, code=MALFORMED):
        super().__init__(message)
        self.code = code


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
        elif channel in (1, 3, 8):
            key = (channel, "shot", message.get("shot_number"))
            if key[2] is None:
                key = (channel, "swing", message.get("swing_number"))
            self.shots[key] = copy.deepcopy(message)
        return copy.deepcopy(message)
