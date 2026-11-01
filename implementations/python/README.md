# Python reference package

The `common-golf-protocol` package provides an asyncio API for QUIC
connections, discovery, identities and protocol messages. It supports
draft 3 (0.3.0), draft 1 (0.1.0 and 0.1.1), and draft 2 (0.2.0).
Both endpoints must select the same draft; an ALPN mismatch fails the
handshake.

## Install and run

Install from the repository root:

```sh
python -m pip install ./implementations/python
```

This QUIC server advertises a launch monitor and sends a synthetic shot when
a simulator connects as a QUIC client:

```python
import asyncio
from functools import partial
from common_golf import Connection, Draft, Identity, listen, new_shot_uuid

class LaunchMonitor(Connection):
    def on_connected(self):
        self.publish(1, {
            "type": "shot", "shot_uuid": new_shot_uuid(),
            "ball_speed_meters_per_second": 65,
            "vertical_launch_angle_degrees": 12,
            "horizontal_launch_angle_degrees": -1,
        })

async def main():
    identity = Identity("./identity", "Garage launch monitor")
    factory = partial(LaunchMonitor, role="device", draft=Draft.THREE)
    async with listen("Garage launch monitor", identity, factory, draft=Draft.THREE):
        await asyncio.Event().wait()

asyncio.run(main())
```

Set `role` to `device`, `controller` or `sim`. Each role can send only on
the channels it owns. A simulator discovers peers with `discover()` and
connects using `async with dial(peer, identity, factory, draft=...)`. The
factory creates a separate `Connection` instance for each connection.

Handle received messages in `on_message(channel, message, stream_id)`.
Additional callbacks report connection establishment (`on_connected`),
declined channels (`on_declined`), stream errors (`on_stream_error`), peer
stream resets (`on_stream_cancelled`) and disconnection (`on_disconnected`).

## Identity

Identity keys are stored with mode 0600. The fingerprint is the 32-byte Ed25519
public key in lowercase hex. In draft 3, `listen` advertises the DNS-SD
`key`, and `dial` checks that it matches the handshake key before sending
application traffic. The low-level `Identity.configuration(is_client=True, draft=3,
expected_key=...)` also requires the key.

`on_connected` exposes the authenticated `peer_fingerprint`.

## Channels and streams

- The library sends info before opening other channels.
- `publish(channel, message)` sends on the channel's outgoing stream.
- `open`, `send` and `finish` manage streams directly. Sending on a stream
  the peer declined returns `False`.
- `cancel(stream_id)` resets a stream with code 1 and declines the channel
  for the connection. `cancel(stream_id, code=0)` cancels only that stream.
- Each connection has a 256 KiB send budget (`max_send_buffer`). When it is
  full, sends raise `BufferError`. Yield to the event loop before retrying.

Messages are dictionaries that preserve unknown fields. For draft 3, create
`shot_uuid = new_shot_uuid()` once per shot and reuse it for related
channels, updates and replays. `Snapshots` tracks received state: it
replaces shot snapshots by channel and UUID, and applies `device_status`
and `simulator_state` patches. Pass `stream_open=True` when a new status
stream starts.

## Custom channels (draft 3)

Register `Contract` definitions on both peers with
`contracts={definition.identifier: definition}`. A definition has a format
(`json`, `raw`, `datagram` or `raw_datagram`), permitted application roles in
`initiators`, direction, payload limit, and optional `allowed_fingerprints`.
Stream initiation is independent of the QUIC client/server role.
`OPEN_CONNECT` provides the built-in Open Connect contract.

Configure the server with `menu=[{"channel": 1000, "contract":
"com.example.diagnostics-v1"}]`, or a callable `menu(fingerprint)` that
selects entries for the authenticated peer.
On the client, start custom traffic in `on_menu_ready`.
`channels_for_contract(name)` returns the handles for a contract. Standard
channels are available independently of the menu.

Custom streams received before the menu are held with fixed QUIC flow
credit until the menu is validated. Custom datagrams received before the
menu are dropped. An invalid initial menu leaves custom channels unavailable.

- Open Connect replies use `send(stream_id, reply)` on the device's stream.
- `open_raw` and `send_raw` write raw streams; `on_raw_stream` and
  `on_raw_data` receive them. At most 16 raw streams are active.
- `send_datagram` and `on_datagram` carry sequenced datagrams. Queues hold
  16 entries per channel and 64 per connection and raise `BufferError` when
  full. Queued entries are retained; applications must discard unwanted
  samples before enqueueing them.
  `datagram_payload_limit(channel)` is the current payload limit, excluding
  the 12-byte header; larger sends raise `ValueError`. Rejected sends
  consume no sequence number.

Drafts 1 and 2 configure private JSON channels on both endpoints with
`private_channels={1000: max_bytes}`.

## Diagnostic peer

`cgp-python-peer` (or `python -m common_golf`) exchanges every standard
channel and writes received messages and events as NDJSON. It defaults to
draft 3 and accepts direct addresses through `--host` and `--port` for
compatibility testing. Servers print `KEY <hex>` followed by
`READY <port>`; draft 3 clients require `--expected-key <hex>`. Use
`--help` for the full option list. Applications should use discovery.

## Development

```sh
python -m pip install -e './implementations/python[test]'
python -m pytest implementations/python/tests
python -m build implementations/python
```

aioquic is pinned to 1.2.0. The adapter in
[`compat.py`](src/common_golf/compat.py) uses private aioquic APIs for raw
public keys, menu flow control and stream lifecycle handling. It also
includes compatibility fixes for stream retirement and duplicate
STOP_SENDING events. The adapter rejects other aioquic versions.

See the [Python examples](../../examples/python/README.md).
