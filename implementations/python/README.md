# Python reference package

```sh
python -m pip install ./implementations/python
```

Supports draft 1 (0.1.0, 0.1.1) and draft 2 (0.2.0). Both endpoints must
choose the same draft; mismatched ALPNs fail the TLS handshake.

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

`role` is `device`, `controller`, or `sim`, and each role may send only on the
channels it owns. A simulator finds peers with `discover()` and connects with
`async with dial(peer, identity, factory, draft=...)`. Each connection gets its
own `Connection` instance.

Override `on_connected`, `on_message(channel, message, stream_id)`,
`on_declined`, `on_stream_error`, `on_stream_cancelled` (the peer reset a
stream), and `on_disconnected` as needed.

## Identity and trust

Keys are stored with mode 0600. The fingerprint is the 32-byte Ed25519 public
key in lowercase hex. Certificate chains and validity dates are ignored; TLS
proves key possession.

Pass `trusted={fingerprint: tier}`, where the tier is `"internal"`,
`"developer"`, or `"maker"` (or `{"tier": ...}`, as in
`examples/fingerprints.example.json`). Unknown peers and unknown tiers are
`maker`. Read `peer_tier` in `on_connected` to decide which channels to open.

## Channels and streams

- Info is sent before any other channel opens.
- `publish(channel, message)` sends on the channel's outgoing stream.
- `open`, `send`, and `finish` manage streams directly. Sending on a stream the
  peer declined returns `False`.
- Channel 2 (Open Connect) uses bidirectional streams. The simulator replies
  with `send(stream_id, reply)`, without a channel prefix.
- `cancel(stream_id)` cancels one stream.
- Each connection has a 256 KiB send budget (`max_send_buffer`). When it is
  full, sends raise `BufferError`; yield and retry.

Messages are dictionaries, and unknown fields are kept. Applications keep their
own shot and swing counters and must not send the same shot on channels 1 and
2. The optional `Snapshots` class applies shot replacement and status/state
patches; pass `stream_open=True` when a new status or state stream starts.

Private JSON channels (1000 and up) must be configured on both endpoints, for
example `private_channels={1000: max_bytes}`. Unconfigured private channels are
declined. Use `open(1000, bidirectional=True)` for a bidirectional private
stream.

## Diagnostic peer

`cgp-python-peer` (or `python -m common_golf`) is the compatibility-test peer.
It accepts direct `--host`/`--port`, exchanges every standard channel, and
writes received messages and events as NDJSON. Run it with `--help` for all
options. Products should use DNS-SD discovery instead of direct addresses.

## Development

```sh
python -m pip install -e './implementations/python[test]'
python -m pytest implementations/python/tests
python -m build implementations/python
```

aioquic is pinned to 1.2.0. Client-certificate requests and stream-lifetime
checks use its private API, confined to `compat.py`, which raises an error on
other versions. `compat.py` also works around two aioquic 1.2.0 bugs:
send-only stream retirement and duplicate STOP_SENDING handling.

See the [Python examples](../../examples/python/README.md).
