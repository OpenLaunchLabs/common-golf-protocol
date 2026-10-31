# Common Golf Protocol

One connection between golf launch monitors, swing radars, control boxes, and
simulator software, over QUIC. SI units spelled out in every field name.
Discovery instead of IP addresses. Encrypted and authenticated by the
transport, so no application-level crypto. Channels work like topics: one
owner, one direction, no acknowledgements.

Status: draft 2. Everything here is provisional.

## Reference implementations

| Implementation | Install/build | Transport |
|---|---|---|
| [Python package](implementations/python/README.md) | `pip install ./implementations/python` | aioquic |
| [Rust crate](implementations/rust/README.md) | `cargo build --manifest-path implementations/rust/Cargo.toml` | noq / rustls |
| [C library](implementations/c/README.md) | CMake; also used by the ESP32 demo | ngtcp2 / wolfSSL |

All three support draft 1 and draft 2; new applications should use draft 2.
The Python package and Rust crate are not yet published. The
[implementation guide](implementations/README.md#build-and-test) covers
building and the [cross-language tests](tests/compatibility/README.md).

## Run the examples first

Two terminals on one machine. Nothing takes an address or a port; the
scripts find each other by discovery, which is the only way the protocol
allows.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install ./implementations/python

.venv/bin/python examples/sim.py            # terminal 1: a simulator
.venv/bin/python examples/launch_monitor_minimum.py # terminal 2: the smallest launch monitor
```

Swap the device for any of these, or run
them all at once. Run two simulators too: every connection is independent
and a device serves all of them.

| Script | What it shows |
|---|---|
| `launch_monitor_minimum.py` | Three-number shots on channel 1. Shortest conforming device. |
| `launch_monitor_full.py` | Status, preliminary/final shots with spin and metadata, club data, computed flight. Gates club/flight by the simulator's trust tier. |
| `launch_monitor_open_connect.py` | A GSPro Open Connect v1 device moved to QUIC with its JSON unchanged. Needs a simulator that offers channel 2; this one does. |
| `swing_speed_radar.py` | Club speed with no ball data. |
| `control_box.py` | Virtual button presses. |

To see trust tiers, print the simulator's fingerprint and list it:

```sh
.venv/bin/python -c 'from common_golf import Identity; print(Identity(".cgp-identities/example-sim", "example-sim").fingerprint)'
# put it in a copy of examples/fingerprints.example.json, then
.venv/bin/python examples/launch_monitor_full.py --trusted my-fingerprints.json
```

The examples use the Python package in `implementations/python`, and their
measurements are synthetic. They default to draft 2; pass `--draft 1` to both
ends for draft 1. Rust and C versions are in `examples/rust` and `examples/c`.

## Try ESP32 hardware

The [ESP-WROOM-32 demo](examples/esp32/README.md) builds three firmware roles:
a synthetic launch monitor, a random control box, and a simulator that
discovers both.

## Then read the spec

[`rfc/0001-common-golf-protocol.md`](rfc/0001-common-golf-protocol.md) is
the whole protocol. `schema/` holds one JSON Schema per channel.

## Changing it

[CONTRIBUTING.md](CONTRIBUTING.md) has the two rules that keep this small,
and the policy that everything here is written by people.
[KNOWN_PRIVATE_CHANNELS.md](KNOWN_PRIVATE_CHANNELS.md) explains how to
document private channels, with clearly marked placeholder examples.

## License

Apache-2.0. See [LICENSE](LICENSE).
