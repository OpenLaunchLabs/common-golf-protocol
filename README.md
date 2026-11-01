# Common Golf Protocol

Common Golf Protocol connects golf launch monitors, swing radars, and control
boxes to simulator software over QUIC. It provides automatic device discovery,
encrypted connections, and authentication. Measurement fields include their
units in their names, and independent channels carry measurements, device
status, and simulator commands.

Draft 3 is in development. The reference implementations also support
published drafts 1 and 2.

## Reference implementations

| Implementation | Build | Transport |
|---|---|---|
| [Python package](implementations/python/README.md) | `pip install ./implementations/python` | aioquic |
| [Rust crate](implementations/rust/README.md) | `cargo build --manifest-path implementations/rust/Cargo.toml` | noq / rustls |
| [C library](implementations/c/README.md) | CMake; also used by the ESP32 demo | ngtcp2 / wolfSSL |

All three libraries support drafts 1, 2, and 3 and are installed from this
repository. See the [implementation guide](implementations/README.md#build-and-test)
for build instructions and the [compatibility test guide](tests/compatibility/README.md)
for cross-language testing.

## Run the examples

Run the following commands from the repository root. Start the simulator and
launch monitor in separate terminals; they connect through automatic discovery.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install ./implementations/python

.venv/bin/python examples/sim.py                    # Terminal 1
.venv/bin/python examples/launch_monitor_minimum.py # Terminal 2
```

The examples use synthetic measurements. Multiple devices and simulators can
run at the same time, with an independent connection for each pair.

| Device script in `examples/` | Features |
|---|---|
| `launch_monitor_minimum.py` | Ball speed and launch angles on channel 1. |
| `launch_monitor_full.py` | Device status, shot updates, spin, club data, and computed flight. Club and flight data are restricted to trusted simulators. |
| `launch_monitor_open_connect.py` | GSPro Open Connect v1 messages over QUIC using the Open Connect custom contract. |
| `swing_speed_radar.py` | Club speed without ball data. |
| `control_box.py` | Virtual button presses. |

To enable trusted channels in the full launch monitor example, print the
simulator's fingerprint:

```sh
.venv/bin/python -c 'from common_golf import Identity; print(Identity(".cgp-identities/example-sim", "example-sim").fingerprint)'
```

Copy `examples/fingerprints.example.json` to `my-fingerprints.json`, add an
entry keyed by the fingerprint with `"tier": "developer"`, and start the device:

```sh
.venv/bin/python examples/launch_monitor_full.py --trusted my-fingerprints.json
```

The examples use draft 3 by default. Pass `--draft 1` or `--draft 2` to both
endpoints to use an earlier draft. See also the [Rust](examples/rust/README.md)
and [C](examples/c/README.md) examples.

## ESP32 hardware

The [ESP-WROOM-32 demo](examples/esp32/README.md) builds three firmware
roles: a synthetic launch monitor, a random control box, and a simulator
that discovers both.

## Specification

The [protocol specification](rfc/0001-common-golf-protocol.md) defines the
transport, channels, and message formats. The [JSON Schemas](schema/README.md)
cover draft 3 and include frozen copies for drafts 1 and 2.

## Contributing

Read the [contribution guidelines](CONTRIBUTING.md) before proposing changes.
Vendor custom contracts can be registered in the
[private channel directory](KNOWN_PRIVATE_CHANNELS.md).

## License

Apache-2.0. See [LICENSE](LICENSE).
