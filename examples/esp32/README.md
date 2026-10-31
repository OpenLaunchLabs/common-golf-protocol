# ESP32 demo

Firmware for ESP-WROOM-32 boards (original `esp32` target, not C3 or S3) built
on the [C library](../../implementations/c/README.md). Each board runs one
role. No sensors, buttons, or wiring are needed.

Devices advertise `_common-golf._udp` on an ephemeral port; the simulator
discovers them and connects over QUIC/TLS 1.3 with Ed25519 identities. The
default is draft 2 (`common-golf/draft-2`, 0.2.0); the `CGP_DRAFT` menuconfig
option selects draft 1. Every peer on a connection must use the same draft.

| Role | Sends | Receives | Behavior |
|---|---|---|---|
| `launch_monitor` | 0 info, 1 shot, 3 club, 4 status, 8 flight | 0 info, 6 state, 7 result | Synthetic shot every 3–7 s, marked `estimated` (`reduced_accuracy` on draft 1). |
| `control_box` | 0 info, 5 control | 0 info, 6 state, 7 result | Random command every 0.5–3 s. Arrow holds send `down`, then `up` 100–500 ms later. |
| `simulator` | 0 info, 6 state, 7 result | 0 info, 1 shot, 3 club, 4 status, 5 control, 8 flight | Connects to both devices, logs received JSON, and returns placeholder shot results. |

Channel 2 (Open Connect), reserved channels, and private channels are declined
with `not_interested`. Flight and result values are placeholders, not ball
physics.

By default the simulator connects only to devices whose mDNS `vendor` is
`Common Golf example`, `CGP hardware test`, or `Example`, so it ignores real
launch monitors on the network. Turn off **Only connect to demo/example
devices** in menuconfig to remove this filter. The filter doesn't affect trust:
every peer is `maker`.

## Build and flash

You need three USB data cables and a 2.4 GHz Wi-Fi network that allows
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

In menuconfig, open **Common Golf Protocol demo** and enter the Wi-Fi SSID and
password. Firmware with an empty SSID stops at boot. Exit the serial monitor
with **Ctrl+]**. If flashing hangs at "Connecting", hold BOOT until it
connects. `build.sh` is a Bash script.

Each role has its own `sdkconfig.<role>` and `build-<role>/`, both ignored by
Git. They contain the Wi-Fi password, so don't publish them. Don't change the
role after the first configuration.

Defaults: 240 MHz, 4 MB DIO flash, 2 MiB app partition, no OTA, no PSRAM,
two connections per board (up to four in menuconfig). Images are about 1.08 MiB.

The Ed25519 identity is generated on first boot and stored in NVS, so it
survives reflashing. `./scripts/build.sh <role> -p <port> erase-flash` deletes
it and the board gets a new fingerprint.

## What to expect

The simulator logs `CONNECTED` for each device, then `RX` lines with received
JSON. Devices log incoming state and results. Service names include a
MAC-derived serial number.

Every 10 seconds, a `resources` line reports connected peers, free and minimum
internal heap, the largest free block, main-task stack headroom, and Wi-Fi
RSSI, all in bytes. Logs also include transport message counts, queued bytes,
and handshake times. `./scripts/build.sh <role> size` reports static flash and
RAM use only.

The transport sends QUIC PINGs every 15 seconds with a 90-second idle timeout.
The simulator rediscovers devices about every 5 seconds; a stale connection can
hold its slot until the idle timeout expires.

### Hardware checklist

1. With all three boards powered, the simulator holds both connections and
   receives shots, club, status, flight, and control; the launch monitor
   receives state and results.
2. Over at least 15 minutes, there are no resets, failed handshakes, or
   dropped connections, and free heap doesn't trend down. Record minimum heap,
   largest free block, and stack headroom.
3. After power-cycling a device, it reconnects with the same fingerprint. After
   restarting Wi-Fi, both connections recover.
4. A device serves two simulators at once (a fourth board, or `examples/sim.py`
   on the same network). Measure heap before raising the peer limit.

## Measurements

One ESP32-D0WD-V3 (rev 3.1) board, 240 MHz, 4 MB flash, no PSRAM, reflashed for
each role and connected over Wi-Fi to two Python peers. These are short runs,
not soak tests.

| Role | Run | Free heap, two peers | Minimum heap | Minimum stack headroom |
|---|---|---:|---:|---:|
| Launch monitor | 120 s, 51 shots | 130,576–131,144 B | 40,928 B¹ | 6,412 B |
| Control box | 60 s, 71 controls | 131,712–131,716 B | 108,112 B | 6,604 B |
| Simulator | 120 s, 54 shot/result pairs, 125 controls | 126,368–128,288 B | 100,908 B | 8,364 B |

¹ Includes receiving a 65,535-byte state message during the run.

Free heap returns to about 201–205 KB after peers disconnect. Handshakes take
about 2.2 s for one connection and 2.5 s and 4.3 s for two concurrent ones. A
simulator connection with 97 seconds of no application traffic stayed open on
QUIC PINGs alone.

CPU use with two connections, averaged over steady-state windows (handshakes
excluded). Totals include Wi-Fi, logging, and instrumentation.

| Workload | Payload logging | Core 0 | Core 1 | Whole chip |
|---|---|---:|---:|---:|
| Control box, random commands | On | 11.8% | 0.9% | 6.4% |
| Simulator, idle | Off | 12.3% | 0.7% | 6.5% |
| Simulator, 1 shot/2 s + 1 command/s | Off | 14.9% | 0.7% | 7.8% |
| Simulator, same | On | 18.3% | 0.7% | 9.5% |
| Simulator, 10 shots/s + 20 commands/s | Off | 63.7% | 1.2% | 32.5% |

Handshakes hold one core at 100% for their duration. Run time-sensitive work,
such as button sampling, in a separate FreeRTOS task from the transport loop.
The loop polls every 10 ms even when idle, so these figures describe this
implementation, not a protocol minimum.

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

The harness prints PASS/FAIL with per-channel counts and exits nonzero on
failure. Its identities are temporary and don't affect the board's key. Use
`--name` to pick one of several boards, and stop other simulators first so both
connection slots are free.

- `--max-frame` sends a 65,535-byte state message. Confirm the board then logs
  the following `"mode":"putting"` state.
- `--quiet-after N` stops application messages N seconds after connecting.
  `application_idle_seconds` should exceed 90 with both connections still up.
- Opening a CP2102 serial monitor resets the board. To test reconnection,
  restart the harness while leaving the monitor open.

### CPU profiling

1. In menuconfig, under **Component config → FreeRTOS → Kernel**, enable
   `configGENERATE_RUN_TIME_STATS` and set `configRUN_TIME_COUNTER_TYPE` to
   `uint64_t`.
2. Under **FreeRTOS → Port**, set the run-time stats clock to **ESP Timer**.
3. Under **Common Golf Protocol demo**, enable `CGP_CPU_PROFILING`.

The firmware then logs a `CPU` line about every 2 seconds. Core and task
percentages treat one core as 100%; `chip_busy_pct` treats both cores together
as 100%, and `accounted_core_pct` should be close to 200%. Interrupt time is
charged to the interrupted task, so the figures are scheduler estimates.
`profiler_core_pct` is the profiler's own cost. Toggle `CGP_LOG_PAYLOADS` to
measure serial logging separately.

```sh
.venv/bin/python tests/hardware_peer.py listen --duration 120 --quiet-after 30
.venv/bin/python tests/hardware_peer.py listen --duration 60 --shot-interval .1 --control-interval .05
.venv/bin/python tests/analyze_cpu.py serial.log --start 30 --end 60
```

Capture one boot per log. `--start` and `--end` are seconds since boot; choose
a steady-state window without handshakes.

## Host tests

These compile the firmware's transport, TLS, framing, and mock sources for the
host and run them over loopback. They don't exercise Wi-Fi or mDNS. From
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

- CTest: the C library tests plus the firmware JSON extraction test.
- `tests/integration/test_c_mock_schema.py`: validates `mock_samples` output
  against the schemas.
- `tests/integration/test_c_python_interop.py`: runs `cgp_host` against Python
  peers over QUIC, including concurrent connections and stream errors.
- `tests/test_analyze_cpu.py`: tests the CPU log analyzer.

Pass `--native-build-dir DIR` to pytest to use a different build. For
ASan/UBSan:

```sh
cmake -S tests -B build-native-sanitize -DCMAKE_BUILD_TYPE=Debug -DCGP_SANITIZE=ON
cmake --build build-native-sanitize --parallel
ctest --test-dir build-native-sanitize --output-on-failure
.venv/bin/python -m pytest tests --native-build-dir build-native-sanitize
```

## Dependencies

`scripts/fetch_deps.py` downloads pinned ngtcp2 and wolfSSL archives and
verifies their SHA-256. The ESP-IDF component manager fetches
`espressif/mdns` 1.8.2. TLS uses software cryptography and the hardware RNG.

**wolfSSL is GPL-3.0-or-later** (commercial licenses are available). See the
[wolfSSL component notes](components/wolfssl/README.md) before distributing
firmware.
