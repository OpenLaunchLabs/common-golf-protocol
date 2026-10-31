# Python reference package

```sh
python -m pip install ./implementations/python
```

Supports draft 1 (0.1.0, 0.1.1) and draft 2 (0.2.0). Choose the draft on each
endpoint; mismatched ALPNs fail the TLS handshake.

```python
import asyncio
from functools import partial
from common_golf import Connection, Draft, Identity, listen

class LaunchMonitor(Connection):
    def on_connected(self):
        self.publish(1, {
            "type": "shot", "shot_number": 1,
            "ball_speed_meters_per_second": 65,
            "vertical_launch_angle_degrees": 12,
            "horizontal_launch_angle_degrees": -1,
        })

async def main():
    identity = Identity("./identity", "Garage launch monitor")
    factory = partial(LaunchMonitor, role="device", draft=Draft.TWO)
    async with listen("Garage launch monitor", identity, factory, draft=Draft.TWO):
        await asyncio.Event().wait()

asyncio.run(main())
```

A simulator subclasses `Connection` with `role="sim"`, implements
`on_message(channel, message, stream_id)`, finds peers with `discover()`, and
connects with `async with dial(peer, identity, factory, draft=...)`. Each
connection gets its own `Connection` instance with its own channels, snapshots,
peer key, and trust tier.

## Identity and trust

Keys persist with mode 0600. The fingerprint is the 32-byte Ed25519 public key
in lowercase hex. Certificate chains and validity dates are ignored; TLS proves
key possession. Unknown peers get the `maker` tier. Pass
`trusted={fingerprint: "developer"}` (or `"internal"`) and read `peer_tier` in
`on_connected` to make channel decisions.

## Channels and streams

- `publish(channel, message)` sends on the channel's outgoing stream.
- `open`, `send`, and `finish` control stream lifetimes directly.
- Channel 2 (Open Connect) uses bidirectional streams. The responder replies
  with `send(stream_id, reply)`, without a channel prefix.
- `cancel(stream_id)` cancels one stream; `on_stream_cancelled` lets the
  application release its own buffers.
- The send budget is 256 KiB per connection. When it is full, sends raise
  `BufferError`; yield and retry.

Messages are dictionaries, and unknown fields are kept. Applications maintain
their own shot and swing counters, and must not send the same shot on both
channel 1 and channel 2. `Snapshots` implements shot replacement and
status/state patching; pass `stream_open=True` when a new status or state
stream starts.

Private JSON channels must be configured on both endpoints, e.g.
`private_channels={1000: max_bytes}`. Unconfigured private channels are
declined. Open a bidirectional private stream with
`open(1000, bidirectional=True)`.

## Diagnostic peer

`cgp-python-peer` (or `python -m common_golf.peer`) is a test harness that takes
`--host`/`--port` directly, exchanges every standard channel, and writes
received messages as NDJSON. Products should use DNS-SD discovery instead.

## Development

```sh
python -m pip install -e './implementations/python[test]'
python -m pytest implementations/python/tests
python -m build implementations/python
```

aioquic is pinned to 1.2.0 because client-certificate requests and stream
lifetime inspection use its private API. That access is confined to
`compat.py`, which fails clearly on other versions. It also fixes two aioquic
1.2.0 defects: send-only stream retirement and duplicate STOP_SENDING handling.

See the [Python applications](../../examples/python/README.md) for complete
examples.
