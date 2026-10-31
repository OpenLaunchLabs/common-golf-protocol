# Common Golf Protocol

One connection between golf launch monitors, swing radars, control boxes, and
simulator software, over QUIC. SI units spelled out in every field name.
Discovery instead of IP addresses. Encrypted and authenticated by the
transport, so no application-level crypto. Channels work like topics: one
owner, one direction, no acknowledgements.

Status: draft 2. Everything here is provisional.

## Reference implementations

Three reusable libraries live separately from the reference applications:

| Implementation | Install/build | Transport |
|---|---|---|
| [Python package](implementations/python/README.md) | `pip install ./implementations/python` | aioquic |
| [Rust crate](implementations/rust/README.md) | `cargo build --manifest-path implementations/rust/Cargo.toml` | noq / rustls |
| [Standalone C library](implementations/c/README.md) | CMake; also shared with the ESP32 demo | ngtcp2 / wolfSSL |

They support draft 1 (0.1.0 and 0.1.1) and draft 2 (0.2.0), including
explicitly configured private JSON channels. The Python package and Rust crate
are local packages, not published releases. Draft 1 support lets the tests check
older peers and ALPN boundaries; new applications should use draft 2.

Follow the [implementation guide](implementations/README.md#build-and-test)
to build the libraries and run their unit tests plus a
[63-case cross-language QUIC matrix](tests/compatibility/README.md),
malformed-stream probes, and the reference application tests. The guide also
covers optional multicast DNS-SD tests and package verification.

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

The simulator consumes messages using ordinary JSON field access. Schema
conformance is checked by the repository tests and CI. Swap the device for any of these, or run
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

The examples consume the Python package in `implementations/python`; framing,
TLS, identities, and discovery have one implementation. Shot and swing
measurements vary within bounded random ranges around typical values. The full
launch monitor keeps preliminary/final shots consistent and scales synthetic
club speed and carry with ball speed. Use `--draft 1` or `--draft 2` (default)
and select the same draft in the simulator.

## Try ESP32 hardware

The [ESP-WROOM-32 demo](examples/esp32/README.md) builds three firmware roles:
a synthetic launch monitor, a random control box, and a simulator that discovers
and logs both over QUIC. It includes flashing instructions and resource telemetry
for measuring the protocol on boards without PSRAM.

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
