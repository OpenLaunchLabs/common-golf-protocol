# Reference libraries

Reusable protocol libraries, separate from the applications in `examples/`.
Each has a library API and a diagnostic peer executable (`cgp-peer`) used by
the compatibility tests.

- [Python](python/README.md): `common-golf-protocol` package on aioquic.
- [Rust](rust/README.md): `common-golf-protocol` crate on noq/rustls.
- [C](c/README.md): static library on ngtcp2/wolfSSL, shared with the
  [ESP32 demo](../examples/esp32/README.md).

| ALPN | Protocol versions | Accuracy fields |
|---|---|---|
| `common-golf/draft-1` | 0.1.0, 0.1.1 | `shot.reduced_accuracy` |
| `common-golf/draft-2` | 0.2.0 | `shot.estimated`, `club.estimated` |

The [RFC](../rfc/0001-common-golf-protocol.md) defines draft 2; new
applications should use it. Draft 1 exists so the tests can cover older peers
and ALPN mismatch. The ALPN selects behavior; `info.protocol_version` is
informational.

All three libraries provide persistent Ed25519 identities with raw-public-key
fingerprints, 15-second QUIC keep-alives, a 90-second idle timeout, standard
channel ownership, per-stream rejection, and explicitly configured private JSON
channels. Applications supply measurements, shot counters, trust lists, and
channel policy.

JSON Schemas are test tools, not runtime dependencies. Applications parse the
fields they use; the libraries handle framing, limits, and stream admission.

For C# or Windows, implement CGP on any suitable QUIC library. The
[experimental Rust FFI DLL](../examples/c-ffi/README.md) is a fallback.

## Build and test

Requirements: Python 3.9+, Rust 1.90+, CMake 3.16+, and a C11 compiler. C
dependencies are fetched at pinned, SHA-256-verified revisions. Native
discovery uses Bonjour on macOS or `libavahi-compat-libdnssd-dev` with a
running Avahi daemon on Linux.

From the repository root:

```sh
# Setup
python3 -m venv .venv
.venv/bin/python -m pip install -e 'implementations/python[test]'
.venv/bin/python implementations/c/tools/fetch_deps.py

# Build
cargo build --locked --manifest-path implementations/rust/Cargo.toml
cargo build --locked --manifest-path examples/rust/Cargo.toml
cmake -S implementations/c -B implementations/c/build -DCMAKE_BUILD_TYPE=Debug
cmake --build implementations/c/build --parallel 4

# Unit and conformance tests
.venv/bin/python tests/compatibility/fixtures.py
.venv/bin/python -m pytest tests/compatibility/test_conformance.py
.venv/bin/python -m pytest implementations/python/tests
cargo test --locked --manifest-path implementations/rust/Cargo.toml
cargo test --locked --manifest-path examples/rust/Cargo.toml
ctest --test-dir implementations/c/build --output-on-failure

# Cross-language tests
.venv/bin/python tests/compatibility/run.py
.venv/bin/python tests/compatibility/adversarial.py
.venv/bin/python tests/compatibility/reference_apps.py

# Packaging
.venv/bin/python -m build implementations/python
cargo package --locked --allow-dirty --manifest-path implementations/rust/Cargo.toml
```

Discovery tests need local multicast and are not run in CI:

```sh
.venv/bin/python tests/compatibility/discovery.py
.venv/bin/python tests/compatibility/reference_apps.py --discovery
```

Reports go to `test-results/`. See the
[compatibility guide](../tests/compatibility/README.md) for coverage.

## Dependency updates

Python pins aioquic and confines private API use to `compat.py`. The C library
builds the same transport for host and ESP32. After changing a transport
dependency, re-run the build, test, and packaging steps above; after changing
shared C or TLS configuration, also rebuild the ESP32 firmware.
