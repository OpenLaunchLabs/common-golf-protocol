# ESP32 Common Golf Protocol demo

Flash one of three roles onto each ESP-WROOM-32 development board. The CP2102
is the USB-to-serial adapter; select the **original `esp32` target**, not C3 or
S3. No sensors, buttons, display, or wiring between boards are required.

This runs real QUIC with TLS 1.3 and Ed25519 identities, using ALPN
`common-golf/draft-2` and protocol version `0.2.0`. Devices advertise
`_common-golf._udp` on an ephemeral port; the simulator discovers and connects
to them automatically. By default, the simulator selects only devices whose
mDNS `vendor` is exactly `Common Golf example`, `CGP hardware test`, or `Example`.
This keeps unrelated launch monitors on the same network out of the demo. To
include other discovered devices, disable **Only connect to demo/example
devices** in the simulator's menuconfig. This selects devices; it does not
establish trust. All connected peers default to makers.

| Role | Sends | Receives | Behavior |
|---|---|---|---|
| `launch_monitor` | 0 info, 1 shot, 3 club, 4 status, 8 flight | 0 info, 6 state, 7 result | Synthetic shot every 3–7 seconds; generated measurements are marked `estimated`. |
| `control_box` | 0 info, 5 control | 0 info, 6 state, 7 result | Random command every 0.5–3 seconds, including arrows and all standard commands. Arrow holds pair `down` with `up` after 100–500 ms. |
| `simulator` | 0 info, 6 state, 7 result | 0 info, 1 shot, 3 club, 4 status, 5 control, 8 flight | Connects to both devices, prints received JSON, and publishes illustrative shot results. |

Together the roles exercise all native standard channels. Optional channel 2
(GSPro Open Connect compatibility), reserved channels, and private channels are
declined with `not_interested`; no shot is sent twice through compatibility.
The simulator logs controls; it does not render a game or inject computer keys.
The flight and result numbers are demonstration values, not ball physics.

## Build and flash three boards

Use three USB data cables and a shared **2.4 GHz Wi-Fi network** that permits
multicast and traffic between clients. Guest Wi-Fi/client isolation can prevent
discovery. CP2102 ports commonly appear as `/dev/cu.SLAB_USBtoUART` on macOS,
`/dev/ttyUSB0` on Linux, or a COM port on Windows. Install the board's CP210x
VCP driver if no serial port appears.

Install [ESP-IDF 5.4.2](https://docs.espressif.com/projects/esp-idf/en/v5.4.2/esp32/get-started/index.html)
with its prerequisites. For a new command-line installation on macOS/Linux:

```sh
git clone --branch v5.4.2 --recursive https://github.com/espressif/esp-idf.git ~/esp-idf-v5.4.2
cd ~/esp-idf-v5.4.2
./install.sh esp32
. ./export.sh
```

In each new terminal, source that `export.sh` before using `idf.py`. The wrapper
below is a Bash script; Windows users can use a Bash-capable ESP-IDF environment.

From this directory (`examples/esp32`):

```sh
python3 scripts/fetch_deps.py
./scripts/build.sh launch_monitor menuconfig
./scripts/build.sh launch_monitor build
./scripts/build.sh launch_monitor -p /dev/cu.SLAB_USBtoUART flash monitor
```

In **Common Golf Protocol demo**, enter the Wi-Fi SSID and password. Keep the
launch monitor role selected for the first board. Replace the example serial
port with the board's actual port. Exit the monitor with **Ctrl+]**. If flashing
stalls at “Connecting,” hold BOOT while the tool connects, then release it.

Repeat for the other two boards using their ports:

```sh
./scripts/build.sh control_box menuconfig
./scripts/build.sh control_box build
./scripts/build.sh control_box -p /dev/cu.SLAB_USBtoUART flash monitor

./scripts/build.sh simulator menuconfig
./scripts/build.sh simulator build
./scripts/build.sh simulator -p /dev/cu.SLAB_USBtoUART flash monitor
```

Enter the same Wi-Fi credentials for **each** role. Profiles select the role on
first configuration; avoid changing the role choice afterward. Each profile has
its own ignored `sdkconfig.<role>` and `build-<role>/`. Configuration files and
firmware binaries contain the Wi-Fi credentials; do not publish them. A build
with an empty SSID prints a configuration reminder and stops at boot.

The defaults use 240 MHz, 4 MB DIO flash, a 2 MiB application partition, and no
PSRAM. Two simultaneous connections are enabled by default on every role; the
menu permits up to four for further measurement. There is no OTA partition.

An Ed25519 identity is generated on first use and saved in NVS. Normal reflashing
preserves it. Erasing flash, including to recover from an NVS initialization
error, removes that identity and changes the fingerprint. If that reset is
intended, run `./scripts/build.sh <role> -p <port> erase-flash`, then flash again.

## Measure the boards

The simulator should print `CONNECTED` for both devices, followed by `RX` JSON
for their channels. Device serial output should show incoming simulator state
and shot results. Names include the board's MAC-derived serial so multiple
copies can be distinguished.

Every ten seconds, `resources` reports connected peers, free/minimum internal
heap, largest free allocation, main-task stack headroom, and Wi-Fi RSSI.
Transport statistics include message counts and queued bytes; connection logs
include handshake time. All memory values are bytes. Record these after startup,
after the first connection, and with both connections active. `./scripts/build.sh <role> size` reports static flash/RAM
usage; it does not measure live QUIC/TLS heap requirements.

For a useful hardware demonstration:

1. Power all three boards and verify the simulator maintains two connections
   while receiving shots, club/status/flight data, and controls. Verify the
   launch monitor receives state and results.
2. Capture at least 15 minutes of serial output. Check for resets, failed
   handshakes, dropped connections, and declining free heap. Record minimum
   heap, largest free block, and stack headroom, not just the initial free heap.
3. Power-cycle a device. Discovery repeats approximately every five seconds;
   an old connection may occupy its slot until the 90-second QUIC timeout.
   Verify automatic reconnection and the same persisted identity fingerprint.
   Restart Wi-Fi and check that both connections recover too.
4. To test a listener with two simulators, use a fourth board or the repository's
   Python `examples/sim.py` on the same network. Check both receive shots without
   disconnecting each other. Increase the peer limit only after measuring heap.

The transport uses QUIC PING keep-alives every 15 seconds and a 90-second idle
timeout. The regular mock traffic already prevents most idle periods; the
one-board harness below can pause simulator fixture traffic for an idle test.
No application heartbeat or manual address entry is used by the firmware.

All three roles have been cross-compiled for the original ESP32. Images are
approximately **1.08 MiB**, leaving about 46% of the 2 MiB app partition free.

## Hardware measurements

Tested on **2026-10-25** with one ESP32-D0WD-V3 revision 3.1 board, 4 MB flash,
240 MHz, and no PSRAM. The board was reflashed between roles and communicated
with Python peers over Wi-Fi. Each measured role held two simultaneous QUIC
connections; this was not a run with three physical boards.

| Role | Observation | Free heap with two peers | Minimum heap since boot | Minimum main-task stack headroom |
|---|---|---:|---:|---:|
| Launch monitor | 120 seconds; 51 shot messages across two clients | 130,576–131,144 B | 40,928 B | 6,412 B |
| Control box | 60 seconds; 71 control events across two clients | 131,712–131,716 B | 108,112 B | 6,604 B |
| Simulator | 120 seconds; 54 shot/result pairs and 125 control events | 126,368–128,288 B | 100,908 B | 8,364 B |

The launch-monitor run also accepted a **65,535-byte** state JSON object while
both clients were connected, then logged the following `putting` marker on the
same stream. Its heap minimum includes that stress test. Control events included
63 presses and four paired arrow holds; free heap returned to 205,456 bytes after
disconnection. The simulator discovered both host fixtures, received channels
0/1/3/4/5/8, and sent state and results. Its concurrent connection setup took
2.521 and 4.318 seconds; free heap returned to 201,268 bytes after the fixtures
closed. The board retained the same Ed25519 identity across all three role
flashes. The observed traffic passed the repository JSON schemas.

A separate **115-second simulator run** passed with application traffic idle for
97.927 seconds on the launch-monitor connection and 96.447 seconds on the
control connection. QUIC PING kept both alive beyond the negotiated 90-second
idle timeout, and both final PING checks succeeded. Minimum heap was 99,848 B,
minimum stack headroom was 8,316 B, and free heap after fixture shutdown was
201,272 B. Opening the CP2102 serial monitor reset the board before this run;
automatic discovery after boot and the persisted identity were verified.

The later CPU run also restarted both host fixtures without resetting the board.
The simulator initially retried their old ports and logged two handshake
timeouts, then discovered the new ports and reconnected automatically. The
subsequent stress workload passed. This does not replace the Wi-Fi recovery
and power-cycle tests above.

These are short hardware runs. Complete the 15-minute soak and three-board
procedure above before drawing conclusions about sustained operation or network
recovery.

### CPU measurements

Additional runs on the same 240 MHz, dual-core board used FreeRTOS runtime
accounting with 64-bit ESP Timer counters. Each row below held two established
QUIC connections. Values are duration-weighted averages of complete two-second
windows, excluding startup and handshakes. They include Wi-Fi, the mock
application, transport, and instrumentation; they are not isolated QUIC costs.

| Workload | Payload logs | Measured window | Core 0 busy | Core 1 busy | Whole chip busy |
|---|---|---:|---:|---:|---:|
| Control box, random commands to two simulators | On | 42 s | 11.8% | 0.9% | **6.4%** |
| Simulator, connected with application traffic paused | Off | 28 s | 12.3% | 0.7% | **6.5%** |
| Simulator, one shot every 2 s and one command/s | Off | 18 s | 14.9% | 0.7% | **7.8%** |
| Simulator, same normal workload | On | 20 s | 18.3% | 0.7% | **9.5%** |
| Simulator, 10 shots/s and 20 commands/s | Off | 34 s | 63.7% | 1.2% | **32.5%** |

The control run delivered 59 control events. The stress run achieved 9.990
shots/s and 19.989 command starts/s, returned all 466 shot results, and sent
1,088 control events (932 commands, with additional arrow releases). Both peers
passed their final PING checks. The largest two-second whole-chip sample during
the selected stress window was 33.8%. Free heap settled at 108,040 B during that
window; its minimum since boot was 42,276 B. Profiling itself adds memory use.

Connection setup is substantially heavier: one core reached 100% busy across
two-second samples. The other core remained below 1% during those samples.
Control-box handshakes took 2.200/2.202 s; concurrent simulator handshakes took
approximately 2.5/4.3 s. Keep button sampling and time-sensitive application work
in separate FreeRTOS tasks rather than the synchronous transport/handshake loop.
The observed idle capacity supports other modest work, but does not establish
latency guarantees for an additional workload.

The current loop polls every 10 ms, including when no messages are ready. These
numbers describe this implementation, not a minimum CPU requirement imposed by
the protocol. The profiler task itself used about 0.44–0.47% of one core in these
windows; IPC and runtime-accounting overhead are included elsewhere. ISR time
is charged to the interrupted task, so these are scheduler estimates, not
hardware instruction or cycle counts. Full window summaries and workload
evidence are in [the CPU measurement record](measurements/cpu-2026-10-25.json).

### Reproduce with one board

From this directory, prepare a Python environment on a computer on the same
multicast-capable Wi-Fi network:

```sh
python3 -m venv .venv
.venv/bin/pip install -e ../../implementations/python
```

After flashing the corresponding role, run one of these commands. `probe`
discovers the board and opens **two** simulator connections; `listen` advertises
a fake launch monitor and control box for simulator firmware to discover.

```sh
.venv/bin/python tests/hardware_peer.py probe --role launch-monitor --duration 120 --max-frame
.venv/bin/python tests/hardware_peer.py probe --role control-box --duration 60
.venv/bin/python tests/hardware_peer.py listen --duration 120
```

Keep the board's serial monitor open to capture the resource readings. With
`--max-frame`, confirm the subsequent `"mode":"putting"` state appears in the
board log; a successful host PING alone does not prove the payload was processed.
If several matching boards are present, pass `--name` with the discovered service
name. Stop other simulator clients before a probe so both connection slots are
available. The harness prints a PASS/FAIL result and per-channel counts, and
exits nonzero on failure. Its temporary identities do not change the board's
NVS key.

For a simulator idle test, stop the previous harness and run:

```sh
.venv/bin/python tests/hardware_peer.py listen --duration 115 --quiet-after 8
```

This stops fixture application messages eight seconds after connection while
keeping QUIC PING active. Check the reported `application_idle_seconds` exceeds
90 for both peers and both connections remain live. For a separate live
reconnection test, restart `listen` while keeping the existing serial monitor
open: opening a new CP2102 monitor can reset the board. The CPU run exercised
this restart, including the stale-port retry delay described above.

### Optional CPU profiling

In the role's `menuconfig`, open **Component config → FreeRTOS → Kernel**,
enable `configGENERATE_RUN_TIME_STATS`, and choose `uint64_t` for
`configRUN_TIME_COUNTER_TYPE`. Under **FreeRTOS → Port**, choose **ESP Timer**
for the runtime statistics clock. Then enable **Report per-core CPU usage every
two seconds** (`CGP_CPU_PROFILING`) under **Common Golf Protocol demo**. Profiling
is off by default; rebuild and flash after changing these options.

Repeat a workload with **Print received JSON payloads to serial**
(`CGP_LOG_PAYLOADS`) enabled and disabled to separate verbose serial logging from
protocol processing. Connection and resource logs remain enabled in both cases.

Each `CPU` line summarizes roughly two seconds. Core and task percentages use
one core as 100%; `chip_busy_pct` uses both cores together as 100%.
`accounted_core_pct` includes idle tasks and should be near 200% on this board.
Sample maxima are maxima of two-second averages, not instantaneous peaks.

IPC callbacks force each core's current idle runtime to be booked before
sampling. Interrupt runtime is charged to the interrupted task, including idle;
these are scheduler estimates, not instruction or cycle counts. Profiling adds
overhead: `profiler_core_pct` covers the profiler task, while IPC and scheduler
accounting costs also appear elsewhere.

Use the one-board harness for active-to-idle and stress workloads:

```sh
.venv/bin/python tests/hardware_peer.py listen --duration 120 --quiet-after 30
.venv/bin/python tests/hardware_peer.py listen --duration 60 --shot-interval .1 --control-interval .05
.venv/bin/python tests/analyze_cpu.py serial.log --start 30 --end 60
```

Run workloads separately and capture one boot per serial log. Analyzer bounds
are **seconds since board boot**; replace the example bounds with the observed
steady-state active or idle interval, excluding handshakes. It uses only complete
sample windows and weights averages by their duration. The harness reports
actual workload rates alongside its configured intervals.

## Native checks

The demo consumes the standalone [C reference library](../../implementations/c/README.md).
No ESP-IDF activation is needed to compile the same transport, TLS, framing,
and mock sources for the host. Fetch the standalone dependencies first. These tests use loopback endpoints;
they do not test ESP32 Wi-Fi or mDNS discovery.

```sh
python3 ../../implementations/c/tools/fetch_deps.py
cmake -S tests -B build-native
cmake --build build-native --parallel
ctest --test-dir build-native --output-on-failure
python3 -m venv .venv
.venv/bin/python -m pip install -e '../../implementations/python[test]'
.venv/bin/python -m pytest tests
```

Run these commands from `examples/esp32`. CTest runs the reference library
tests (including framing and JSON guards) and the firmware JSON-extraction test.
Python tests run through pytest:

- `tests/integration/test_c_mock_schema.py` launches the compiled `mock_samples`
  executable and validates its JSON stdout against the repository schemas.
- `tests/integration/test_c_python_interop.py` launches compiled `cgp_host`
  processes and checks real QUIC traffic between C roles and Python peers,
  including concurrent connections and stream error handling.
- `tests/test_analyze_cpu.py` tests the Python CPU log analyzer directly.

The integration tests use subprocesses, not C FFI, and require no board. They
use `build-native` by default; pass `--native-build-dir /path/to/build` to pytest
to use another build. Missing executables produce an explicit test error.
A failing test exits nonzero.

For AddressSanitizer and UndefinedBehaviorSanitizer checks, configure a separate
host build with `CGP_SANITIZE=ON`. This instruments the C library, its dependencies,
and the native test programs; it requires a compiler supporting both sanitizers.

```sh
cmake -S tests -B build-native-sanitize -DCMAKE_BUILD_TYPE=Debug -DCGP_SANITIZE=ON
cmake --build build-native-sanitize --parallel
ctest --test-dir build-native-sanitize --output-on-failure
.venv/bin/python -m pytest tests --native-build-dir build-native-sanitize
```

## Dependencies

The fetch script verifies SHA-256 hashes of pinned ngtcp2 and wolfSSL source
archives; ESP-IDF's component manager fetches pinned `espressif/mdns` 1.8.2.
Downloaded dependencies, build outputs, and local configurations are ignored by
Git. The TLS wrapper uses software cryptography and the ESP32 hardware RNG.

**wolfSSL is GPL-3.0-or-later with a commercial licensing alternative.** The
example's Apache-2.0 license does not replace dependency licenses. See the
[wolfSSL wrapper notes](components/wolfssl/README.md) before distributing linked
firmware. These dependency pins are for a reproducible experiment; review and
retest updates before using the code in a maintained product.

The firmware `CGP_DRAFT` menuconfig option selects draft 1 or draft 2 (default).
Synthetic draft-1 shots use `reduced_accuracy`; draft 2 uses `estimated` in
shot and club messages. All peers on a connection must use the same draft.
