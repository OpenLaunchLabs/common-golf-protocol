# Common Golf Protocol

One connection between golf launch monitors, swing radars, control boxes, and
simulator software, over QUIC. SI units spelled out in every field name.
Discovery instead of IP addresses. Encrypted and authenticated by the
transport, so no application-level crypto. Channels work like topics: one
owner, one direction, no acknowledgements.

Status: draft 1. Everything here is provisional.

## Run the examples first

Two terminals on one machine. Nothing takes an address or a port; the
scripts find each other by discovery, which is the only way the protocol
allows.

```sh
cd examples
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

.venv/bin/python sim.py                    # terminal 1: a simulator
.venv/bin/python device_minimum.py         # terminal 2: the smallest launch monitor
```

The simulator validates every message it receives against `schema/` and
exits non-zero if anything fails. Swap the device for any of these, or run
them all at once. Run two simulators too: every connection is independent
and a device serves all of them.

| Script | What it shows |
|---|---|
| `device_minimum.py` | Three-number shots on channel 1. Shortest conforming device. |
| `device_full.py` | Status, shots with spin and metadata (preliminary then final), club data, computed flight, a private diagnostics channel. Gates channels by the simulator's trust tier. |
| `device_open_connect.py` | A GSPro Open Connect v1 device moved to QUIC with its JSON unchanged. Needs a simulator that offers channel 2; this one does. |
| `swing_radar.py` | Club speed with no ball data. |
| `control_box.py` | Virtual button presses. |

To see trust tiers, print the simulator's fingerprint and list it:

```sh
.venv/bin/python common_golf.py example-sim        # prints its 64-hex Ed25519 public key
# put it in a copy of fingerprints.example.json, then
.venv/bin/python device_full.py --trusted my-fingerprints.json
```

`common_golf.py` is the only shared code: framing, the channel prefix,
self-signed Ed25519 identity, fingerprints, and discovery. Read it after the
device scripts.

## Then read the spec

[`rfc/0001-common-golf-protocol.md`](rfc/0001-common-golf-protocol.md) is
the whole protocol. `schema/` holds one JSON Schema per channel.

## Changing it

[CONTRIBUTING.md](CONTRIBUTING.md) has the two rules that keep this small,
and the policy that everything here is written by people.
[KNOWN_PRIVATE_CHANNELS.md](KNOWN_PRIVATE_CHANNELS.md) lists private
channels others have built.

## License

Apache-2.0. See [LICENSE](LICENSE).
