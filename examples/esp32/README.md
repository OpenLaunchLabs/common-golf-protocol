# ESP32 demo

Firmware for ESP-WROOM-32 boards (the original `esp32` target, not C3 or S3)
built on the [C library](../../implementations/c/README.md). Each board runs
one role. No sensors, buttons, or wiring are needed.

Devices advertise `_common-golf._udp` on an ephemeral port. The simulator
discovers them and connects over QUIC with Ed25519 identities. `CGP_DRAFT`
selects draft 3 (default), 2 or 1; all boards must use the same draft. Shots
omit `measured_at` because the boards have no synchronized UTC clock.

| Role | Sends | Receives | Behavior |
|---|---|---|---|
| `launch_monitor` | 0 info, 1 shot, 3 club, 4 device_status, 8 flight | 0 info, 6 simulator_state, 7 result | Synthetic shot every 3–7 s, marked `estimated` (`reduced_accuracy` on draft 1). |
| `control_box` | 0 info, 5 control | 0 info, 6 simulator_state, 7 result | Random command every 0.5–3 s. Arrow holds send `down`, then `up` 100–500 ms later. |
| `simulator` | 0 info, 6 simulator_state, 7 result | 0 info, 1 shot, 3 club, 4 device_status, 5 control, 8 flight | Connects to both devices, logs received JSON, and returns placeholder results. |

Reserved and custom channels are declined with `not_interested`. Flight and
result values are placeholders, not ball physics.

By default the simulator connects only to devices whose mDNS `vendor` is
`Common Golf example`, `CGP hardware test`, or `Example`, so it ignores real
launch monitors on the network. Turn off **Only connect to demo/example
devices** in menuconfig to remove the filter. Every peer is trusted as `maker`
either way.

## Build and flash

You need one USB data cable per board and a 2.4 GHz Wi-Fi network that allows
multicast and client-to-client traffic (guest networks often don't). CP2102
boards usually appear as `/dev/cu.SLAB_USBtoUART` (macOS), `/dev/ttyUSB0`
(Linux), or a COM port (Windows); install the CP210x driver if none appears.

Install [ESP-IDF 5.4.2](https://docs.espressif.com/projects/esp-idf/en/v5.4.2/esp32/get-started/index.html):

```sh
git clone --branch v5.4.2 --recursive https://github.com/espressif/esp-idf.git ~/esp-idf-v5.4.2
cd ~/esp-idf-v5.4.2
./install.sh esp32
. ./export.sh   # in every new terminal
```

From `examples/esp32`, for each role (`launch_monitor`, `control_box`,
`simulator`):

```sh
python3 scripts/fetch_deps.py            # once
./scripts/build.sh launch_monitor menuconfig
./scripts/build.sh launch_monitor build
./scripts/build.sh launch_monitor -p /dev/cu.SLAB_USBtoUART flash monitor
```

In menuconfig, open **Common Golf Protocol demo** and set the Wi-Fi SSID and
password; firmware with an empty SSID stops at boot. Exit the serial monitor
with **Ctrl+]**. If flashing hangs at "Connecting", hold BOOT until it
connects.

Each role has its own `sdkconfig.<role>` and `build-<role>/`, both ignored by
Git. They contain the Wi-Fi password, so don't publish them. Don't change a
build directory's role after its first configuration.

Defaults: 240 MHz, 4 MB DIO flash, 2 MiB app partition, no OTA, no PSRAM, two
connections per board (up to four in menuconfig). Images are about 1.1 MiB.

The Ed25519 identity is generated on first boot and stored in NVS, so it
survives reflashing. `./scripts/build.sh <role> -p <port> erase-flash` deletes
it, and the board gets a new fingerprint.

## What to expect

The simulator logs `CONNECTED` for each device, then `RX` lines with received
JSON. Devices log incoming state and results. Service names include a
MAC-derived serial number.

Every 10 seconds a `resources` line reports connected peers, free and minimum
internal heap, largest free block, and main-task stack headroom (bytes), and
Wi-Fi RSSI (dBm). Logs also include message counts, queued bytes, and
handshake times. `./scripts/build.sh <role> size` reports static flash and RAM
use.

QUIC PINGs go out every 5 seconds with a 30-second idle timeout (15 and 90
seconds on drafts 1/2). The simulator rediscovers devices about every 5
seconds; a stale connection holds its slot until the idle timeout.

### Hardware checklist

1. With all three boards running, the simulator holds both connections and
   receives shot, club, status, flight, and control; the launch monitor
   receives state and results.
2. Over at least 15 minutes there are no resets, failed handshakes, or dropped
   connections, and free heap doesn't trend down.
3. A power-cycled device reconnects with the same fingerprint. After a Wi-Fi
   restart, both connections recover.
4. A device serves two simulators at once (a fourth board, or `examples/sim.py`
   on the same network). Measure heap before raising the peer limit.

## Measurements

One ESP32-D0WD-V3 (rev 3.1), 240 MHz, 4 MB flash, no PSRAM, reflashed for each
role and connected over Wi-Fi to two Python peers. Measured on draft 2;
draft 3 has not been measured on hardware yet.

| Role | Run | Free heap, two peers | Minimum heap | Minimum stack headroom |
|---|---|---:|---:|---:|
| Launch monitor | 120 s, 51 shots | 130,576–131,144 B | 40,928 B¹ | 6,412 B |
| Control box | 60 s, 71 controls | 131,712–131,716 B | 108,112 B | 6,604 B |
| Simulator | 120 s, 54 shot/result pairs, 125 controls | 126,368–128,288 B | 100,908 B | 8,364 B |

¹ Includes receiving a 65,535-byte state message.

Free heap returns to about 201–205 KB after peers disconnect. A handshake
takes about 2.2 s alone, and 2.5 s and 4.3 s for two concurrent ones. A
connection with no application traffic for 97 seconds stayed open on QUIC
PINGs alone.

CPU use with two connections, averaged over steady-state windows without
handshakes. Totals include Wi-Fi, logging, and instrumentation.

| Workload | Payload logging | Core 0 | Core 1 | Whole chip |
|---|---|---:|---:|---:|
| Control box, random commands | On | 11.8% | 0.9% | 6.4% |
| Simulator, idle | Off | 12.3% | 0.7% | 6.5% |
| Simulator, 1 shot/2 s + 1 command/s | Off | 14.9% | 0.7% | 7.8% |
| Simulator, same | On | 18.3% | 0.7% | 9.5% |
| Simulator, 10 shots/s + 20 commands/s | Off | 63.7% | 1.2% | 32.5% |

A handshake holds one core at 100% while it runs, so put time-sensitive work
such as button sampling in a separate FreeRTOS task. The transport loop polls
every 10 ms even when idle; these figures describe this implementation, not a
protocol minimum.

## One-board testing

`tests/hardware_peer.py` stands in for the other boards from a computer on the
same network:

```sh
python3 -m venv .venv
.venv/bin/pip install -e ../../implementations/python

# Launch monitor or control box firmware: open two simulator connections
.venv/bin/python tests/hardware_peer.py probe --role launch-monitor --duration 120 --max-frame
.venv/bin/python tests/hardware_peer.py probe --role control-box --duration 60

# Simulator firmware: advertise a fake launch monitor and control box
.venv/bin/python tests/hardware_peer.py listen --duration 120
.venv/bin/python tests/hardware_peer.py listen --duration 115 --quiet-after 8   # idle test
```

It prints PASS/FAIL with per-channel counts and exits nonzero on failure. Its
identities are temporary. Stop other simulators first so both connection
slots are free.

- `probe --name` picks one of several boards. `probe --max-frame` sends a
  65,535-byte state message; the board should then log the following
  `"requested_capture_mode":"putting"` state.
- `listen --quiet-after N` stops application messages N seconds after
  connecting. `application_idle_seconds` should exceed 30 with both
  connections still up.
- Opening a CP2102 serial monitor resets the board. To test reconnection,
  restart the harness and leave the monitor open.

### CPU profiling

1. In menuconfig, under **Component config → FreeRTOS → Kernel**, enable
   `configGENERATE_RUN_TIME_STATS` and set `configRUN_TIME_COUNTER_TYPE` to
   `uint64_t`.
2. Under **FreeRTOS → Port**, set the run-time stats clock to **ESP Timer**.
3. Under **Common Golf Protocol demo**, enable `CGP_CPU_PROFILING`.

The firmware then logs a `CPU` line about every 2 seconds. Core and task
percentages count one core as 100%; `chip_busy_pct` counts both cores together
as 100%, and `accounted_core_pct` should be near 200%. Interrupt time is
charged to the interrupted task, so these are scheduler estimates.
`profiler_core_pct` is the profiler's own cost. Toggle `CGP_LOG_PAYLOADS` to
measure serial logging separately.

```sh
.venv/bin/python tests/hardware_peer.py listen --duration 120 --quiet-after 30
.venv/bin/python tests/hardware_peer.py listen --duration 60 --shot-interval .1 --control-interval .05
.venv/bin/python tests/analyze_cpu.py serial.log --start 30 --end 60
```

Capture one boot per log. `--start` and `--end` are seconds since boot; pick a
steady-state window without handshakes.

## Native checks

These compile the firmware's transport, TLS, framing, and mock sources for the
host and run them over loopback, without Wi-Fi or mDNS. CI runs them. From
`examples/esp32`:

```sh
python3 ../../implementations/c/tools/fetch_deps.py
cmake -S tests -B build-native
cmake --build build-native --parallel
ctest --test-dir build-native --output-on-failure
python3 -m venv .venv
.venv/bin/python -m pip install -e '../../implementations/python[test]'
.venv/bin/python -m pytest tests
```

- CTest: the C library tests and the firmware JSON extraction test.
- `tests/integration/test_c_mock_schema.py`: validates mock message output
  against all three draft schema sets.
- `tests/integration/test_c_python_interop.py`: runs the host build against
  explicitly selected draft 2 Python peers using `cgp_host_draft2`, including
  concurrent connections and stream errors. The default `cgp_host` uses draft 3
  (connector arguments are `PORT KEY` pairs) and tests all three firmware roles
  together. `mock_samples` also defaults to draft 3; pass `1` or `2` explicitly
  for archived-schema samples. The C library tests cover draft 3 raw-key
  authentication, menu, streams, and datagrams.
- `tests/test_analyze_cpu.py`: tests the CPU log analyzer.

Pass `--native-build-dir DIR` to pytest to use another build. For ASan/UBSan:

```sh
cmake -S tests -B build-native-sanitize -DCMAKE_BUILD_TYPE=Debug -DCGP_SANITIZE=ON
cmake --build build-native-sanitize --parallel
ctest --test-dir build-native-sanitize --output-on-failure
.venv/bin/python -m pytest tests --native-build-dir build-native-sanitize
```

## Dependencies

`scripts/fetch_deps.py` downloads pinned ngtcp2 and wolfSSL archives and
verifies their SHA-256. The ESP-IDF component manager fetches `espressif/mdns`
1.8.2. TLS uses software cryptography and the hardware RNG.

**wolfSSL is GPL-3.0-or-later** (commercial licenses are available). Read the
[wolfSSL component notes](components/wolfssl/README.md) before distributing
firmware.
