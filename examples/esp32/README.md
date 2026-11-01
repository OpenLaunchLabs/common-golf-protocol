# ESP32 demo

This demo runs a synthetic launch monitor, control box, or simulator on an
ESP-WROOM-32 board using the [C library](../../implementations/c/README.md).
It supports the original `esp32` target, not C3 or S3. Each board runs one
role and requires no external sensors, buttons, or wiring.

Devices advertise `_common-golf._udp` on an ephemeral port. The simulator
discovers them and connects over QUIC with Ed25519 identities. `CGP_DRAFT`
selects draft 3 (default), 2, or 1; all boards must use the same draft.
Without a synchronized UTC clock, the boards omit `measured_at` from shots.

| Role | Sends | Receives | Behavior |
|---|---|---|---|
| `launch_monitor` | 0 info, 1 shot, 3 club, 4 device_status, 8 flight | 0 info, 6 simulator_state, 7 result | Synthetic shot every 3–7 s, marked `estimated` (`reduced_accuracy` in draft 1). |
| `control_box` | 0 info, 5 control | 0 info, 6 simulator_state, 7 result | Random command every 0.5–3 s. Arrow holds send `down`, then `up` 100–500 ms later. |
| `simulator` | 0 info, 6 simulator_state, 7 result | 0 info, 1 shot, 3 club, 4 device_status, 5 control, 8 flight | Connects to both devices, logs received JSON, and returns placeholder results. |

Reserved and custom channels are declined with `not_interested`. Flight and
result messages contain placeholder values.

By default, the simulator connects only to devices whose mDNS `vendor` is
`Common Golf example`, `CGP hardware test`, or `Example`. Disable **Only
connect to demo/example devices** in menuconfig to allow other vendors.
This filter selects devices; all connected peers receive `maker` trust.

## Build and flash

Use one USB data cable per board and a 2.4 GHz Wi-Fi network that allows
multicast and traffic between clients. CP2102 boards usually appear as
`/dev/cu.SLAB_USBtoUART` on macOS, `/dev/ttyUSB0` on Linux, or a COM port on
Windows. Install the CP210x driver if the serial port does not appear.

Install [ESP-IDF 5.4.2](https://docs.espressif.com/projects/esp-idf/en/v5.4.2/esp32/get-started/index.html):

```sh
git clone --branch v5.4.2 --recursive https://github.com/espressif/esp-idf.git ~/esp-idf-v5.4.2
cd ~/esp-idf-v5.4.2
./install.sh esp32
. ./export.sh   # in every new terminal
```

From `examples/esp32`, fetch dependencies once, then configure, build, and
flash each board. Replace `launch_monitor` with `control_box` or `simulator`
for the other roles, and use the board's serial port:

```sh
python3 scripts/fetch_deps.py            # once
./scripts/build.sh launch_monitor menuconfig
./scripts/build.sh launch_monitor build
./scripts/build.sh launch_monitor -p /dev/cu.SLAB_USBtoUART flash monitor
```

In menuconfig, open **Common Golf Protocol demo** and set the Wi-Fi SSID and
password. Firmware with an empty SSID stops at boot. Exit the serial monitor
with **Ctrl+]**. If flashing stalls at "Connecting", hold BOOT until the
connection starts.

Each role has a separate `sdkconfig.<role>` and `build-<role>/`. Both are
ignored by Git and contain Wi-Fi credentials; keep them private. Use a new
build directory when changing roles.

The default configuration uses 240 MHz, 4 MB DIO flash, a 2 MiB app partition,
no OTA or PSRAM, and two connections per board. Menuconfig allows up to four
connections. Firmware images are approximately 1.1 MiB.

The board generates an Ed25519 identity on first boot and stores it in NVS.
Reflashing preserves it; `./scripts/build.sh <role> -p <port> erase-flash`
deletes it and causes the next boot to generate a new fingerprint.

## Runtime behavior

The simulator logs `CONNECTED` for each device, then `RX` lines with received
JSON. Devices log incoming state and results. Service names include a
MAC-derived serial number.

Every 10 seconds, a `resources` line reports peer count, free and minimum
internal heap, largest free block, main-task stack headroom in bytes, and
Wi-Fi RSSI in dBm. Logs also include message counts, queued bytes, and
handshake times. Run `./scripts/build.sh <role> size` for static flash and
RAM usage.

Draft 3 uses a 5-second QUIC PING interval and a 30-second idle timeout;
drafts 1 and 2 use 15 and 90 seconds. The simulator repeats discovery about
every 5 seconds. A stale connection occupies its slot until the idle timeout.

### Hardware validation

1. Run all three roles. Verify that the simulator maintains both connections
   and receives shot, club, status, flight, and control messages. Verify that
   the launch monitor receives state and results.
2. Run for at least 15 minutes. Check for resets, failed handshakes, dropped
   connections, or a sustained decrease in free heap.
3. Power-cycle a device and verify that it reconnects with the same
   fingerprint. Restart Wi-Fi and verify that both connections recover.
4. Connect two simulators to one device using a fourth board or
   `examples/sim.py`. Measure heap usage before increasing the peer limit.

## Resource measurements

These draft 2 measurements use an ESP32-D0WD-V3 (rev 3.1) at 240 MHz with
4 MB flash, no PSRAM, and two Python peers over Wi-Fi. Draft 3 has not been
measured on hardware.

| Role | Run | Free heap, two peers | Minimum heap | Minimum stack headroom |
|---|---|---:|---:|---:|
| Launch monitor | 120 s, 51 shots | 130,576–131,144 B | 40,928 B¹ | 6,412 B |
| Control box | 60 s, 71 controls | 131,712–131,716 B | 108,112 B | 6,604 B |
| Simulator | 120 s, 54 shot/result pairs, 125 controls | 126,368–128,288 B | 100,908 B | 8,364 B |

¹ Includes receiving a 65,535-byte state message.

Free heap returns to approximately 201–205 KB after disconnection. A single
handshake takes about 2.2 s; two concurrent handshakes complete in about
2.5 s and 4.3 s.

CPU usage below averages steady-state windows with two connections and no
handshakes. Totals include Wi-Fi, logging, and instrumentation.

| Workload | Payload logging | Core 0 | Core 1 | Whole chip |
|---|---|---:|---:|---:|
| Control box, random commands | On | 11.8% | 0.9% | 6.4% |
| Simulator, idle | Off | 12.3% | 0.7% | 6.5% |
| Simulator, 1 shot/2 s + 1 command/s | Off | 14.9% | 0.7% | 7.8% |
| Simulator, 1 shot/2 s + 1 command/s | On | 18.3% | 0.7% | 9.5% |
| Simulator, 10 shots/s + 20 commands/s | Off | 63.7% | 1.2% | 32.5% |

A handshake occupies one core at 100%. Run time-sensitive work, such as
button sampling, in a separate FreeRTOS task. The transport loop polls every
10 ms, including while idle.

## One-board testing

With one board, run `tests/hardware_peer.py` on a computer on the same
network to provide the other roles. The harness uses draft 3; configure the
board to match. Run from `examples/esp32`:

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

The harness prints PASS/FAIL and per-channel counts, and exits nonzero on
failure. It uses temporary identities. Stop other simulators before running
`probe` so both connection slots are available.

- `probe --name` selects a specific board. `probe --max-frame` sends a
  65,535-byte state message; verify that the board then logs the subsequent
  `"requested_capture_mode":"putting"` state.
- `listen --quiet-after N` stops application messages N seconds after
  connecting. `application_idle_seconds` should exceed 30 with both
  connections still up.
- Opening a CP2102 serial monitor resets the board. Leave the monitor open
  and restart the harness when testing reconnection.

### CPU profiling

1. In menuconfig, under **Component config → FreeRTOS → Kernel**, enable
   `configGENERATE_RUN_TIME_STATS` and set `configRUN_TIME_COUNTER_TYPE` to
   `uint64_t`.
2. Under **FreeRTOS → Port**, set the run-time stats clock to **ESP Timer**.
3. Under **Common Golf Protocol demo**, enable `CGP_CPU_PROFILING`.

The firmware logs a `CPU` line about every 2 seconds. Core and task
percentages use one core as 100%; `chip_busy_pct` uses both cores together
as 100%. `accounted_core_pct` should be near 200%. These scheduler estimates
charge interrupt time to the interrupted task. `profiler_core_pct` reports
the profiler's overhead. Toggle `CGP_LOG_PAYLOADS` to isolate serial logging
costs.

```sh
.venv/bin/python tests/hardware_peer.py listen --duration 120 --quiet-after 30
.venv/bin/python tests/hardware_peer.py listen --duration 60 --shot-interval .1 --control-interval .05
.venv/bin/python tests/analyze_cpu.py serial.log --start 30 --end 60
```

Capture one boot per log. `--start` and `--end` specify seconds since boot;
select a steady-state interval without handshakes.

## Native checks

Native tests compile the firmware's transport, TLS, framing, and mock sources
for the host and exercise them over loopback. They run in CI and do not cover
Wi-Fi or mDNS. Run from `examples/esp32`:

```sh
python3 ../../implementations/c/tools/fetch_deps.py
cmake -S tests -B build-native
cmake --build build-native --parallel
ctest --test-dir build-native --output-on-failure
python3 -m venv .venv
.venv/bin/python -m pip install -e '../../implementations/python[test]'
.venv/bin/python -m pytest tests
```

| Test suite | Coverage |
|---|---|
| CTest | C library tests and firmware JSON extraction, including draft 3 authentication, contract menus, streams, and datagrams. |
| `tests/integration/test_c_mock_schema.py` | Mock messages against all three draft schema sets. |
| `tests/integration/test_c_python_interop.py` | Draft 3 firmware roles together, plus draft 2 Python interoperability, concurrent connections, and stream errors. |
| `tests/test_analyze_cpu.py` | CPU log analyzer. |

The `cgp_host` executable defaults to draft 3 and accepts client arguments
as `PORT KEY` pairs. Use `cgp_host_draft2` for draft 2. `mock_samples` defaults
to draft 3; pass `1` or `2` for earlier schema sets.

Pass `--native-build-dir DIR` to pytest to select another build directory.
To run with ASan and UBSan:

```sh
cmake -S tests -B build-native-sanitize -DCMAKE_BUILD_TYPE=Debug -DCGP_SANITIZE=ON
cmake --build build-native-sanitize --parallel
ctest --test-dir build-native-sanitize --output-on-failure
.venv/bin/python -m pytest tests --native-build-dir build-native-sanitize
```

## Dependencies

`scripts/fetch_deps.py` downloads pinned ngtcp2 and wolfSSL archives and
verifies their SHA-256 checksums. The ESP-IDF component manager fetches
`espressif/mdns` 1.8.2. TLS uses software cryptography and the hardware random
number generator.

wolfSSL is licensed under GPL-3.0-or-later, with commercial licenses
available. See the
[wolfSSL component notes](components/wolfssl/README.md) before distributing
firmware.
