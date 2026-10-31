# Python reference package

Install locally with `python -m pip install ./implementations/python`. The
package is independent of `examples/`. JSON Schema conformance checks live in
the repository tests and are not runtime dependencies.
Draft 1 supports revisions 0.1.0 and 0.1.1; draft 2 supports 0.2.0.
Select the draft explicitly on each endpoint: different ALPNs fail the TLS handshake.
`info.protocol_version` is informational and never drives receive behavior.

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

For a simulator subclass `Connection` with `role="sim"`, implement
`on_message(channel, message, stream_id)`, call `discover()`, and use
`async with dial(peer, identity, factory, draft=...)`. A listener accepts many
connections; each factory invocation owns its channels, snapshots, peer key,
and trust tier. A connector may open multiple independent `dial` contexts.

The adapter sends info first, requests client certificates, verifies Ed25519
identity type and TLS proof of possession, ignores CA chains/validity dates,
uses a 90-second idle timeout and 15-second QUIC PINGs. Keys persist with mode
0600. Unknown certificates receive maker tier. Pass `trusted={fingerprint:
"developer"}` (or `"internal"`), and make application channel policy decisions
from `peer_tier` in `on_connected` / your `read_channels` configuration. A
fingerprint is the 32-byte public key in lowercase hex, never a certificate hash.

`publish` reuses an outgoing channel. `open`, `send`, and `finish` expose stream
lifetimes directly; channel 2 alone uses bidirectional standard streams. Its
responder calls `send(stream_id, reply)` without another channel prefix.
Applications choose distinct shot numbers if using both shot and Open Connect
channels and maintain their own monotonically increasing shot/swing counters.
Standard messages are dictionaries. Runtime framing checks JSON encoding and
size; repository tests check schema conformance and draft-specific accuracy
fields. Applications consume the fields they need; unknown fields are retained
in the dictionary and can be ignored. `Snapshots` implements whole shot replacement and status/state
patch semantics; mark `stream_open=True` for a new status/state stream.

Private JSON channels use explicit configuration on both endpoints:
`private_channels={1000: max_bytes}`. The application supplies matching channel
numbers, payload semantics, and permissions. Unknown private channels are declined
without affecting standard channels. A private bidirectional stream can be opened
with `open(1000, bidirectional=True)`; replies use the same JSON framing without
another channel prefix.

`cancel(stream_id)` cancels one stream. `on_stream_cancelled` lets applications
release their own buffers. The default reliable send budget is 256 KiB per
connection. Backpressure raises `BufferError`; callers should yield and retry.
Completed streams release SDK state; aioquic manages QUIC lifetime bookkeeping.

The [Python reference applications](../../examples/python/README.md) demonstrate
launch monitors, Open Connect, a swing-speed radar, a control box, and a simulator.

`cgp-python-peer` / `python -m common_golf.peer` is a **diagnostic test harness**
with direct host/port arguments. Product code should use DNS-SD discovery;
manual addressing is not a product connection flow. The harness emits received
messages and peer fingerprints as NDJSON and exchanges every standard channel.

```sh
python -m pip install -e './implementations/python[test]'
python -m pytest implementations/python/tests
python -m build implementations/python
```

The aioquic version is pinned because client-certificate requests and
queue/lifetime inspection require private API access. All
access is isolated in `compat.py`, which fails clearly for unsupported versions. This package
does not globally monkey-patch aioquic. The adapter also corrects two pinned
1.2.0 lifecycle defects: send-only stream retirement and duplicate STOP_SENDING
reset handling. Live tests cover both, including raw aioquic protocol users of
the adapter and the SDK. Other transport exceptions remain visible.
