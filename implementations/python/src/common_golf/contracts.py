"""Draft 3 exact contracts, immutable listener menus, and datagram framing."""
from dataclasses import dataclass
from collections import deque
import re
import struct

from .protocol import ProtocolError


def valid_identifier(value):
    return isinstance(value, str) and re.fullmatch(r"[a-z0-9._-]{1,128}", value) is not None


@dataclass(frozen=True)
class Contract:
    identifier: str
    format: str = "json"
    openers: frozenset = frozenset({"device", "sim", "controller"})
    bidirectional: bool = False
    max_bytes: int = 65535
    allowed_fingerprints: object = None

    def __post_init__(self):
        if not valid_identifier(self.identifier) or self.format not in {"json", "raw", "datagram", "raw_datagram"}:
            raise ValueError("invalid contract identifier or format")
        if type(self.max_bytes) is not int or not 0 < self.max_bytes <= 0xffffffff:
            raise ValueError("contract payload limit must be a positive uint32")
        if not self.openers or not set(self.openers) <= {"device", "sim", "controller"}:
            raise ValueError("unknown contract opener role")
        object.__setattr__(self, "openers", frozenset(self.openers))
        if self.allowed_fingerprints is not None:
            object.__setattr__(self, "allowed_fingerprints", frozenset(self.allowed_fingerprints))

    def permits(self, fingerprint):
        return self.allowed_fingerprints is None or fingerprint in self.allowed_fingerprints


OPEN_CONNECT = Contract("io.common-golf.open-connect-v1", openers=frozenset({"device"}), bidirectional=True)


def validate_menu(entries):
    if not isinstance(entries, list) or len(entries) > 16:
        raise ProtocolError("menu accepts at most 16 channels")
    result = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ProtocolError("invalid menu entry")
        channel, identifier = entry.get("channel"), entry.get("contract")
        if type(channel) is not int or not 1000 <= channel <= 0xffffffff or channel in result:
            raise ProtocolError("invalid or duplicate menu channel")
        if not valid_identifier(identifier):
            raise ProtocolError("invalid contract identifier")
        result[channel] = identifier
    return result


def validate_reads(values):
    if not isinstance(values, list) or any(not valid_identifier(value) for value in values):
        raise ProtocolError("invalid reads_contracts hint")


def decode_datagram(data):
    if len(data) < 12:
        raise ProtocolError("truncated datagram header")
    channel, sequence = struct.unpack(">IQ", data[:12])
    if channel < 1000:
        raise ProtocolError("datagrams require custom channel IDs")
    return channel, sequence, bytes(data[12:])


class DatagramSequences:
    def __init__(self):
        self._next = {}

    def encode(self, channel, payload, *, limit):
        sequence = self._next.get(channel, 0)
        if sequence > 0xffffffffffffffff:
            raise OverflowError("datagram sequence exhausted")
        data = struct.pack(">IQ", channel, sequence) + bytes(payload)
        if len(data) > limit:
            raise ValueError("datagram exceeds transport payload budget")
        self._next[channel] = sequence + 1
        return data


class DatagramQueue:
    """Bounded per-channel FIFO queues, drained round-robin by aioquic."""
    def __init__(self, connection_limit=64, channel_limit=16):
        self.connection_limit, self.channel_limit = connection_limit, channel_limit
        self._channels, self._ready, self._size = {}, deque(), 0

    def __len__(self):
        return self._size

    def check_capacity(self, channel):
        if self._size >= self.connection_limit:
            raise BufferError("connection datagram queue is full")
        if len(self._channels.get(channel, ())) >= self.channel_limit:
            raise BufferError("channel datagram queue is full")

    def append(self, data):
        channel = int.from_bytes(data[:4], "big")
        self.check_capacity(channel)
        if channel not in self._channels:
            self._channels[channel] = deque()
            self._ready.append(channel)
        self._channels[channel].append(data)
        self._size += 1

    def __getitem__(self, index):
        if index != 0:
            raise IndexError(index)
        return self._channels[self._ready[0]][0]

    def popleft(self):
        channel = self._ready.popleft()
        data = self._channels[channel].popleft()
        self._size -= 1
        if self._channels[channel]:
            self._ready.append(channel)
        else:
            del self._channels[channel]
        return data
