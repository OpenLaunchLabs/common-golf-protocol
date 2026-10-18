"""Shared helpers for the Common Golf Protocol examples.

Framing, channel prefix, self-signed Ed25519 identity, fingerprinting,
and discovery. Everything a device or simulator script needs that is not
the script's own behaviour.
"""
import asyncio
import datetime
import json
import pathlib
import socket
import ssl
import struct

from aioquic.asyncio import connect, serve
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.connection import QuicConnection
from aioquic.quic.events import HandshakeCompleted, StopSendingReceived, StreamDataReceived, StreamReset
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import NameOID
from zeroconf import IPVersion, ServiceInfo, ServiceListener
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

ALPN = "common-golf/draft-2"  # each draft has its own; becomes common-golf/1 at 1.0
SERVICE = "_common-golf._udp.local."
PROTOCOL_VERSION = "0.2.0"
CHANNEL = {"info": 0, "shot": 1, "open_connect": 2, "club": 3, "status": 4,
           "control": 5, "state": 6, "result": 7, "flight": 8}
NOT_INTERESTED = 1  # STOP_SENDING code: "I do not read this channel"
MALFORMED = 2       # STOP_SENDING code: bad length, bad JSON, FIN inside a frame
MAX_MESSAGE_BYTES = 65535  # ceiling for channels 0-8; private channels choose their own
IDLE_TIMEOUT_SECONDS = 90.0  # spec floor is 60; minutes pass between shots
KEEP_ALIVE_SECONDS = 15.0    # QUIC PING; aioquic has no built-in keep-alive

HERE = pathlib.Path(__file__).resolve().parent


# ---- framing: 4-byte big-endian length, then one JSON object ----------------

def frame(obj) -> bytes:
    body = json.dumps(obj, separators=(",", ":")).encode()
    return struct.pack(">I", len(body)) + body


def unframe(buf: bytearray, limit=MAX_MESSAGE_BYTES):
    """Yield every complete JSON object in buf, leaving the remainder.

    Raises ValueError on a length above limit or on invalid JSON.
    """
    while len(buf) >= 4:
        (n,) = struct.unpack(">I", buf[:4])
        if limit and n > limit:
            raise ValueError(f"message of {n} bytes exceeds {limit}")
        if len(buf) < 4 + n:
            return
        msg = json.loads(bytes(buf[4:4 + n]))
        if not isinstance(msg, dict):
            raise ValueError("message is not a JSON object")
        yield msg
        del buf[:4 + n]


# ---- identity: self-signed Ed25519 cert, cached next to the script ----------

def identity(name: str):
    """Return (cert_path, key_path, fingerprint). Generated once per name."""
    d = HERE / ".identity"
    d.mkdir(exist_ok=True)
    cert_path, key_path = d / f"{name}.crt", d / f"{name}.key"
    if not cert_path.exists():
        key = ed25519.Ed25519PrivateKey.generate()
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder()
                .subject_name(subject).issuer_name(subject)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=36500))
                .sign(key, None))
        key_path.write_bytes(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()))
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    return cert_path, key_path, fingerprint(cert)


def fingerprint(cert: x509.Certificate) -> str:
    """The peer's Ed25519 public key itself, 32 bytes as hex. Not a hash of anything."""
    return cert.public_key().public_bytes(serialization.Encoding.Raw,
                                          serialization.PublicFormat.Raw).hex()


def configuration(name: str, is_client: bool) -> QuicConfiguration:
    cert_path, key_path, _ = identity(name)
    cfg = QuicConfiguration(alpn_protocols=[ALPN], is_client=is_client,
                            verify_mode=ssl.CERT_NONE, idle_timeout=IDLE_TIMEOUT_SECONDS)
    cfg.load_cert_chain(cert_path, key_path)
    return cfg


def tier_of(peer_fingerprint, trusted: dict) -> str:
    """Trust tier for a peer. Anyone not on the list, or with no cert, is a maker."""
    entry = trusted.get(peer_fingerprint or "")
    return entry["tier"] if isinstance(entry, dict) else "maker"


# aioquic only requests a client certificate through a flag it keeps for its
# own tests. Set it on every server-side TLS context so both peers are identified.
_orig_initialize = QuicConnection._initialize


def _initialize(self, peer_cid):
    _orig_initialize(self, peer_cid)
    if not self._is_client:
        self.tls._request_client_certificate = True


QuicConnection._initialize = _initialize


# ---- connection: one object per peer, channels on top of streams -------------

class Connection(QuicConnectionProtocol):
    """Reads channel-prefixed streams and dispatches whole JSON messages.

    Subclass and override on_connected() and on_message(channel, msg, stream_id).
    Set READS to the standard channels this side reads; anything else below
    1000 is declined (code 1). That is how channel ownership is enforced: a
    device never lists channel 1, so a simulator cannot open it toward one.
    """

    READS = frozenset()          # standard channels (< 1000) this side reads
    READS_PRIVATE = True         # accept 1000+ streams and hand them to on_message

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._buffers = {}      # stream_id -> bytearray
        self._channels = {}     # stream_id -> channel id once the prefix is read
        self._incoming = {}     # channel -> stream_id currently open from the peer
        self._dead = set()      # streams we declined or reset; ignore further data
        self._stopped = set()   # peer stopped our sending half; never write to it again
        self.peer_fingerprint = None
        self.connected = asyncio.get_event_loop().create_future()

    # --- sending
    def open(self, channel: int, bidirectional=False) -> int:
        stream_id = self._quic.get_next_available_stream_id(is_unidirectional=not bidirectional)
        self._quic.send_stream_data(stream_id, struct.pack(">I", channel))
        self._channels[stream_id] = channel
        self.transmit()
        return stream_id

    def send(self, stream_id: int, msg) -> None:
        # aioquic resets the sender before delivering STOP_SENDING. Optional
        # channels may be declined without interrupting the other channels.
        if stream_id in self._stopped:
            return
        self._quic.send_stream_data(stream_id, frame(msg))
        self.transmit()

    def publish(self, channel: int, msg) -> int:
        """Open a unidirectional stream for channel and send one message on it."""
        stream_id = self.open(channel)
        self.send(stream_id, msg)
        return stream_id

    def decline(self, stream_id: int) -> None:
        """Code 1: I do not read this channel, or you may not open it."""
        self._quic.stop_stream(stream_id, NOT_INTERESTED)
        self._close_incoming(stream_id)
        self.transmit()

    def reset(self, stream_id: int, *, ended=False) -> None:
        """Code 2: peer sent something unparseable. Drop the stream, keep the connection."""
        if not ended:
            self._quic.stop_stream(stream_id, MALFORMED)
        self._close_incoming(stream_id)
        self.transmit()

    def _close_incoming(self, stream_id: int) -> None:
        self._dead.add(stream_id)
        self._buffers.pop(stream_id, None)
        channel = self._channels.get(stream_id)
        if self._incoming.get(channel) == stream_id:
            del self._incoming[channel]   # the peer may reopen this channel on a new stream

    # --- receiving
    def quic_event_received(self, event):
        if isinstance(event, HandshakeCompleted):
            cert = self._quic.tls._peer_certificate
            self.peer_fingerprint = fingerprint(cert) if cert else None
            self.on_connected()
            if not self.connected.done():
                self.connected.set_result(True)
            asyncio.ensure_future(self._keep_alive())
        elif isinstance(event, StreamDataReceived):
            sid = event.stream_id
            if sid in self._dead:
                return
            buf = self._buffers.setdefault(sid, bytearray())
            buf += event.data
            if sid not in self._channels:
                if len(buf) < 4:
                    if event.end_stream:
                        self.reset(sid, ended=True)
                    return
                channel = struct.unpack(">I", buf[:4])[0]
                del buf[:4]
                self._channels[sid] = channel
                wanted = channel in self.READS or (channel >= 1000 and self.READS_PRIVATE)
                if not wanted or channel in self._incoming:
                    why = "already open" if channel in self._incoming else "not read here"
                    print(f"[common_golf] declining channel {channel} ({why})")
                    self.decline(sid)
                    return
                self._incoming[channel] = sid
            channel = self._channels[sid]
            try:
                for msg in unframe(buf, MAX_MESSAGE_BYTES if channel < 1000 else 0):
                    self.on_message(channel, msg, sid)
                if event.end_stream and buf:
                    raise ValueError("stream ended inside a frame")
            except ValueError as e:
                print(f"[common_golf] malformed on channel {channel}: {e}; discarding stream")
                self.reset(sid, ended=event.end_stream)
                return
            if event.end_stream:
                self._close_incoming(sid)
        elif isinstance(event, StopSendingReceived):
            self._stopped.add(event.stream_id)
            self.on_declined(self._channels.get(event.stream_id), event.stream_id, event.error_code)
        elif isinstance(event, StreamReset):
            self._close_incoming(event.stream_id)

    async def _keep_alive(self):
        while not self._closed.is_set():
            await asyncio.sleep(KEEP_ALIVE_SECONDS)
            if not self._closed.is_set():
                try:
                    await self.ping()
                except Exception:
                    return

    def on_connected(self):
        pass

    def on_message(self, channel: int, msg, stream_id: int):
        pass

    def on_declined(self, channel, stream_id, code):
        pass


# ---- discovery: DNS-SD over mDNS, ephemeral port, no addresses anywhere -----

def _local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 1))  # no packet is sent; picks the default route
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


async def listen(name: str, create_protocol, txt: dict):
    """Bind an ephemeral UDP port, serve QUIC on it, advertise it. Runs forever."""
    server = await serve("0.0.0.0", 0, configuration=configuration(name, is_client=False),
                         create_protocol=create_protocol)
    port = server._transport.get_extra_info("sockname")[1]
    ip = _local_ip()
    info = ServiceInfo(SERVICE, f"{name}.{SERVICE}", addresses=[socket.inet_aton(ip)],
                       port=port, properties=txt, server=f"{name}.local.")
    zc = AsyncZeroconf(ip_version=IPVersion.V4Only)
    await zc.async_register_service(info)
    print(f"[{name}] advertising {SERVICE} (port chosen by the OS, found by discovery only)")
    try:
        await asyncio.Event().wait()
    finally:
        await zc.async_unregister_service(info)
        await zc.async_close()
        server.close()


async def discover(seconds: float = 3.0):
    """Browse for every Common Golf Protocol listener. Returns [(name, ip, port, txt)]."""
    found = {}
    zc = AsyncZeroconf(ip_version=IPVersion.V4Only)

    class Listener(ServiceListener):
        def add_service(self, z, type_, name):
            asyncio.ensure_future(resolve(name))

        def update_service(self, z, type_, name):
            pass

        def remove_service(self, z, type_, name):
            found.pop(name, None)

    async def resolve(name):
        info = AsyncServiceInfo(SERVICE, name)
        if await info.async_request(zc.zeroconf, 2000):
            txt = {k.decode(): v.decode() for k, v in info.properties.items() if v}
            found[name] = (name.removesuffix("." + SERVICE), info.parsed_addresses()[0], info.port, txt)

    browser = AsyncServiceBrowser(zc.zeroconf, SERVICE, listener=Listener())
    await asyncio.sleep(seconds)
    await browser.async_cancel()
    await zc.async_close()
    return list(found.values())


def dial(ip: str, port: int, name: str, create_protocol):
    """Async context manager yielding a connected protocol."""
    return connect(ip, port, configuration=configuration(name, is_client=True),
                   create_protocol=create_protocol)


if __name__ == "__main__":
    # python common_golf.py example-sim   -> prints that identity's fingerprint for a trust list
    import sys
    print(identity(sys.argv[1])[2])
