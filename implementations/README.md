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
| `common-golf/draft-3` (proposal) | 0.3.0 | `shot.estimated`, `club.estimated` |

The [RFC](../rfc/0001-common-golf-protocol.md) and [draft 3 profile](DRAFT3.md)
describe the unreleased discussion proposal implemented here. Drafts 1 and 2
remain separate compatibility profiles. Behavior follows the ALPN;
`info.protocol_version` is informational.

Draft 3 uses mutual Ed25519 raw public keys, requires the discovery TXT key
and checks it before application traffic, and uses 5 s keep-alives with a 30 s
idle timeout. All three libraries implement listener menus, custom JSON and
Open Connect contracts, raw streams, datagrams, and UUIDv7 shot identities.
Drafts 1/2 retain their 15 s / 90 s timing and private-channel behavior.
The Rust transport keeps draft 3 and legacy TLS profiles in separate endpoint
configurations; it does not offer both profiles in one handshake.

Applications supply measurements, trust lists, and channel policy. Libraries
enforce transport, menu, and shot-identity rules; full message-schema and
cross-field validation remains in the test harness. Shot snapshots replace
earlier snapshots for the same UUID and channel, without a `final` field.

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
