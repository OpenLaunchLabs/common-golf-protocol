# Reference libraries

Protocol libraries, separate from the applications in `examples/`.

- [Python](python/README.md): `common-golf-protocol` package on aioquic.
- [Rust](rust/README.md): `common-golf-protocol` crate on noq/rustls.
- [C](c/README.md): static library on ngtcp2/wolfSSL, shared with the
  [ESP32 demo](../examples/esp32/README.md).

Each library also builds a diagnostic peer for the compatibility tests:
`cgp-python-peer`, and `cgp-peer` for Rust and C.

| ALPN | Protocol versions | Accuracy field |
|---|---|---|
| `common-golf/draft-1` | 0.1.0, 0.1.1 | `shot.reduced_accuracy` |
| `common-golf/draft-2` | 0.2.0 | `shot.estimated`, `club.estimated` |

The [RFC](../rfc/0001-common-golf-protocol.md) defines draft 2, and new
applications should use it. Draft 1 support lets the tests cover older peers
and ALPN mismatches. Behavior follows the ALPN; `info.protocol_version` is
informational.

All three libraries provide persistent Ed25519 identities with raw-public-key
fingerprints, 15 s QUIC keep-alives, a 90 s idle timeout, channel ownership
checks, per-stream rejection, and explicitly configured private JSON channels.
Applications supply measurements, shot counters, trust lists, and channel
policy. JSON Schemas are used by the tests only; the libraries do not validate
message content.

## Build and test

Requirements: Python 3.9+ (CI uses 3.12), Rust 1.90+, CMake 3.16+, and a C11
compiler. C dependencies are fetched at pinned, SHA-256-verified revisions.
Native discovery needs Bonjour on macOS, or `libavahi-compat-libdnssd-dev` and
a running Avahi daemon on Linux.

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

The Rust test suite includes a live mDNS advertise/browse test. The
cross-language discovery tests need local multicast and are not run in CI:

```sh
.venv/bin/python tests/compatibility/discovery.py
.venv/bin/python tests/compatibility/reference_apps.py --discovery
```

Reports go to `test-results/`. See the
[compatibility tests](../tests/compatibility/README.md).

CI (`.github/workflows/reference-implementations.yml`) also runs the ESP32 host
tests and a Windows job that builds the Rust DLL and runs the
[C FFI example](../examples/c-ffi/README.md) and a C# smoke test.

## Updating dependencies

After changing a transport dependency, re-run the build, test, and packaging
steps. After changing shared C or TLS configuration, also rebuild the ESP32
firmware.
