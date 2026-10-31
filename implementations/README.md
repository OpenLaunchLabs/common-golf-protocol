# Reference libraries

These implementations are independent of the reference applications in `examples/`.
Each exposes a reusable protocol API and a diagnostic peer executable. The C
library also supplies the ESP32 demo's shared transport implementation.

| Wire profile | Protocol versions | Accuracy metadata |
|---|---|---|
| `common-golf/draft-1` | 0.1.0, 0.1.1 | `shot.reduced_accuracy` |
| `common-golf/draft-2` | 0.2.0 | `shot.estimated`, `club.estimated` |

New applications should use draft 2. Draft 1 support lets the test suite check
older peers and verify that different ALPNs fail the handshake. Versions 0.1.0
and 0.1.1 interoperate because they share an ALPN. The `info` version is
descriptive and is not used to select a parser. The
[published RFC](../rfc/0001-common-golf-protocol.md) defines draft 2.

Use the language-specific guides for library APIs and integration examples:

- [Python](python/README.md): installable `common-golf-protocol` package,
  asynchronous connection callbacks, discovery, and snapshot helpers.
- [Rust](rust/README.md): `common-golf-protocol` crate, typed standard messages,
  async endpoints, and explicitly configured private JSON channels.
- [C](c/README.md): CMake static library, callback/polling API, POSIX platform
  helpers, DNS-SD adapter, and an ESP-IDF adapter. No ESP32 SDK is needed for
  the host library or executable.

For C# and Windows, use whichever QUIC library fits your application and implement
CGP on top. If you cannot find a suitable library, the
[experimental Rust FFI DLL](../examples/c-ffi/README.md) is available as a fallback,
with a C header and P/Invoke example. CGP does not require a particular library or
language. See the FFI guide for its current scope.

All three use persistent Ed25519 identities, raw-public-key fingerprints,
QUIC keep-alives, independent connections, stream-local rejection, standard
channel ownership, and explicitly configured private JSON channels. Trust lists
and private-channel permissions are application policy; no vendor cloud service
is needed.
Applications supply measurements, shot counters, state, and controls. An
Open Connect simulator application supplies the appropriate reply for each body.
The diagnostic peers provide synthetic data to exercise the transports.

Schemas are development and CI conformance tools. Applications use ordinary
JSON parsing or typed decoding, established defaults, and normal language errors.
Runtime libraries handle transport framing, limits, and stream admission.
Repository conformance helpers live in
`tests/compatibility/schema_check.py`.

## Build and test locally

Requirements: Python 3.9+, a current Rust toolchain (minimum 1.90), CMake 3.16+,
and a C11 compiler. C dependencies are fetched at fixed, SHA-256-verified
revisions. Native discovery uses Bonjour on macOS or Avahi's `dns_sd` compatibility
library on Linux (`libavahi-compat-libdnssd-dev` and a running Avahi daemon).

Run these commands from the repository root, in order.

### Setup

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e 'implementations/python[test]'
.venv/bin/python implementations/c/tools/fetch_deps.py
```

### Build

```sh
cargo build --locked --manifest-path implementations/rust/Cargo.toml
cargo build --locked --manifest-path examples/rust/Cargo.toml
cmake -S implementations/c -B implementations/c/build -DCMAKE_BUILD_TYPE=Debug
cmake --build implementations/c/build --parallel 4
```

### Test

Run fixture and schema checks, then language tests:

```sh
.venv/bin/python tests/compatibility/fixtures.py
.venv/bin/python -m pytest tests/compatibility/test_conformance.py
.venv/bin/python -m pytest implementations/python/tests
cargo test --locked --manifest-path implementations/rust/Cargo.toml
cargo test --locked --manifest-path examples/rust/Cargo.toml
ctest --test-dir implementations/c/build --output-on-failure
```

Run the cross-language QUIC matrix, malformed-stream probes, and reference
applications:

```sh
.venv/bin/python tests/compatibility/run.py
.venv/bin/python tests/compatibility/adversarial.py
.venv/bin/python tests/compatibility/reference_apps.py
```

### Package verification

```sh
.venv/bin/python -m build implementations/python
cargo package --locked --allow-dirty --manifest-path implementations/rust/Cargo.toml
```

### Optional discovery

These commands need local multicast. Discovery checks all nine advertiser/browser
pairs, and the reference application suite can also use DNS-SD:

```sh
.venv/bin/python tests/compatibility/discovery.py
.venv/bin/python tests/compatibility/reference_apps.py --discovery
```

Reports go to ignored `test-results/`. The GitHub workflow performs setup,
builds, tests, and package verification; multicast discovery remains optional.

The [compatibility guide](../tests/compatibility/README.md) describes coverage.
Direct host/port arguments belong to diagnostic tools only. Product integrations
should advertise ephemeral ports and let users choose discovered devices.

The local Python wheel/sdist and Rust `.crate` can be inspected under their
respective `dist/` and `target/package/` directories after package verification.
Nothing has been published to PyPI, crates.io, GitHub, or hardware by these build
commands. The existing [ESP32 guide](../examples/esp32/README.md) covers flashing
and measurements, which remain separate from host compatibility tests.

## Dependency maintenance

The transport integrations have explicit boundaries. Python pins aioquic and
isolates private API access in `compat.py`, including narrowly tested fixes for
unidirectional stream retirement and duplicate STOP_SENDING frames. Rust uses
typed messages over independently framed JSON streams. C counts payload and
buffer-header allocations against its transmit budget, and builds the same
transport for the host and
ESP32. Its TLS settings include dynamically allocated ticket nonces for
interoperability with the other TLS stacks.

Re-run the build, test, and package verification commands above when changing a
transport dependency, and re-run ESP32 builds when changing shared C or TLS
configuration. The shared corpus and
live cross-language tests cover protocol maintenance; RF loss, runtime board
memory, and end-to-end latency still require hardware measurements.
