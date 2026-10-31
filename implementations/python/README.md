# Python reference package

```sh
python -m pip install ./implementations/python
```

Supports draft 3 (0.3.0), draft 1 (0.1.0, 0.1.1) and draft 2 (0.2.0). Both
endpoints must choose the same draft; mismatched ALPNs fail the handshake.

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

`role` is `device`, `controller` or `sim`; each role sends only on the
channels it owns. A simulator finds peers with `discover()` and connects
with `async with dial(peer, identity, factory, draft=...)`. Each connection
gets its own `Connection` instance.

Override `on_connected`, `on_message(channel, message, stream_id)`,
`on_declined`, `on_stream_error`, `on_stream_cancelled` (the peer reset a
stream) and `on_disconnected` as needed.

## Identity and trust

Keys are stored with mode 0600. The fingerprint is the 32-byte Ed25519
public key in lowercase hex. In draft 3, `listen` advertises the DNS-SD
`key`, and `dial` checks that it matches the handshake key before sending
anything. The low-level `Identity.configuration(is_client=True, draft=3,
expected_key=...)` also requires the key.

Pass `trusted={fingerprint: tier}` with tier `"internal"`, `"developer"` or
`"maker"` (or `{"tier": ...}`, as in `examples/fingerprints.example.json`).
Unknown peers and tiers are `maker`. Read `peer_tier` in `on_connected` to
decide which channels to open.

## Channels and streams

- Info is sent before any other channel opens.
- `publish(channel, message)` sends on the channel's outgoing stream.
- `open`, `send` and `finish` manage streams directly. Sending on a stream
  the peer declined returns `False`.
- `cancel(stream_id)` resets a stream with code 1, declining the channel
  for the connection. `cancel(stream_id, code=0)` cancels just that stream,
  for example one raw record.
- Each connection has a 256 KiB send budget (`max_send_buffer`). When it is
  full, sends raise `BufferError`; yield and retry.

Messages are dictionaries, and unknown fields are kept. Create
`shot_uuid = new_shot_uuid()` once per shot and reuse it for related
channels, updates and replays. `Snapshots` tracks received state: it
replaces whole shot snapshots per channel and UUID, and applies
`device_status`/`simulator_state` patches. Pass `stream_open=True` when a
new status stream starts.

## Custom channels (draft 3)

Register `Contract` definitions on both peers with
`contracts={definition.identifier: definition}`. A definition has a format
(`json`, `raw`, `datagram` or `raw_datagram`), opener roles, direction, a
payload bound, and optional `allowed_fingerprints`. `OPEN_CONNECT` is the
built-in Open Connect contract.

The listener supplies `menu=[{"channel": 1000, "contract":
"com.example.diagnostics-v1"}]`, or a callable `menu(fingerprint, tier)`.
On the connector, override `on_menu_ready` to start custom traffic;
`channels_for_contract(name)` returns the handles for a contract. Standard
channels don't wait for the menu.

Custom streams that arrive before the menu are held, with fixed QUIC flow
credit, until it is validated; early custom datagrams are dropped. An
invalid initial menu leaves custom channels unavailable.

- Open Connect replies use `send(stream_id, reply)` on the device's stream.
- `open_raw` and `send_raw` write raw streams; `on_raw_stream` and
  `on_raw_data` receive them. At most 16 raw streams are active.
- `send_datagram` and `on_datagram` carry sequenced datagrams. Queues hold
  16 entries per channel and 64 per connection and raise `BufferError` when
  full; nothing queued is evicted, so drop samples before enqueueing.
  `datagram_payload_limit(channel)` is the current payload limit, excluding
  the 12-byte header; larger sends raise `ValueError`. Rejected sends
  consume no sequence number.

Drafts 1 and 2 configure private JSON channels on both endpoints with
`private_channels={1000: max_bytes}`.

## Diagnostic peer

`cgp-python-peer` (or `python -m common_golf`) is the compatibility-test
peer. It takes direct `--host`/`--port`, exchanges every standard channel,
and writes received messages and events as NDJSON. It defaults to draft 3.
Listeners print `KEY <hex>` and then `READY <port>`; draft 3 connectors
need `--expected-key <hex>`. See `--help`. Products use discovery, not
direct addresses.

## Development

```sh
python -m pip install -e './implementations/python[test]'
python -m pytest implementations/python/tests
python -m build implementations/python
```

aioquic is pinned to 1.2.0. Raw-key negotiation, SPKI encoding, mutual-key
requests, pending-menu flow credit and stream-lifetime checks use its
private API, confined to `compat.py`, which refuses other versions.
`compat.py` also works around two aioquic 1.2.0 bugs: send-only stream
retirement and duplicate STOP_SENDING handling.

See the [Python examples](../../examples/python/README.md).
