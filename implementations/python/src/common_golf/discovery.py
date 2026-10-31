"""DNS-SD discovery and managed QUIC endpoint lifetimes."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import socket

from aioquic.asyncio import connect, serve
from zeroconf import IPVersion, ServiceInfo, ServiceListener
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

from .protocol import Draft, SERVICE
from .identity import validate_key


@dataclass(frozen=True)
class DiscoveredPeer:
    name: str
    host: str
    port: int
    properties: dict


def local_ip():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("192.0.2.1", 1))
            return sock.getsockname()[0]
        except OSError:
            return "127.0.0.1"


@asynccontextmanager
async def listen(name, identity, create_protocol, *, draft=Draft.THREE, properties=None):
    """Bind an ephemeral port, advertise it, serve independent connections."""
    server = await serve("0.0.0.0", 0, configuration=identity.configuration(is_client=False, draft=draft),
                         create_protocol=create_protocol)
    port = server._transport.get_extra_info("sockname")[1]
    zc = AsyncZeroconf(ip_version=IPVersion.V4Only)
    properties = dict(properties or {})
    if Draft(draft) == Draft.THREE:
        properties["key"] = identity.fingerprint
        properties.setdefault("alpn", "draft-3")
    info = ServiceInfo(SERVICE, f"{name}.{SERVICE}", addresses=[socket.inet_aton(local_ip())],
                       port=port, properties=properties, server=f"cgp-{identity.fingerprint[:12]}.local.")
    registered = False
    try:
        await zc.async_register_service(info)
        registered = True
        yield server
    finally:
        if registered:
            await zc.async_unregister_service(info)
        await zc.async_close()
        server.close()


async def discover(seconds=3.0):
    """Browse discovered listeners. Applications choose a name, never an IP field."""
    found = {}
    tasks = set()
    zc = AsyncZeroconf(ip_version=IPVersion.V4Only)
    async def resolve(name):
        info = AsyncServiceInfo(SERVICE, name)
        if await info.async_request(zc.zeroconf, 2000):
            addresses = info.parsed_addresses()
            if addresses:
                found[name] = DiscoveredPeer(name.removesuffix("." + SERVICE), addresses[0], info.port,
                    {k.decode("utf-8", "replace"): (v or b"").decode("utf-8", "replace")
                     for k, v in info.properties.items()})
    class Listener(ServiceListener):
        def add_service(self, z, type_, name):
            task = asyncio.create_task(resolve(name))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        def update_service(self, z, type_, name):
            self.add_service(z, type_, name)
        def remove_service(self, z, type_, name):
            found.pop(name, None)
    browser = AsyncServiceBrowser(zc.zeroconf, SERVICE, listener=Listener())
    try:
        await asyncio.sleep(seconds)
    finally:
        await browser.async_cancel()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await zc.async_close()
    return list(found.values())


def dial(peer, identity, create_protocol, *, draft=Draft.THREE):
    """Connect to a DiscoveredPeer; use one context per independent listener."""
    expected_key = validate_key(peer.properties.get("key")) if Draft(draft) == Draft.THREE else None
    return connect(peer.host, peer.port, configuration=identity.configuration(is_client=True, draft=draft, expected_key=expected_key),
                   create_protocol=create_protocol)
