# Reference libraries

The reference libraries provide Common Golf Protocol transport and message
handling for applications. Each library includes a diagnostic peer for the
[compatibility tests](../tests/compatibility/README.md).

- [Python](python/README.md): `common-golf-protocol` package using aioquic;
  diagnostic command `cgp-python-peer`.
- [Rust](rust/README.md): `common-golf-protocol` crate using noq and rustls;
  diagnostic command `cgp-peer`.
- [C](c/README.md): static library using ngtcp2 and wolfSSL, shared with the
  [ESP32 demo](../examples/esp32/README.md); diagnostic command `cgp-peer`.

For application examples, see [`examples/`](../examples/).

Connection roles follow QUIC: a client initiates a connection and a server
accepts it. Either endpoint can initiate streams; a stream initiator's peer
can reply on the same bidirectional stream. Device, controller and simulator
are separate application roles. See [roles in the RFC](../rfc/0001-common-golf-protocol.md#31-roles),
including the role of a desktop bridge application.

Diagnostic peers use `--role listen` to act as a QUIC server and
`--role connect` to act as a QUIC client.

## Supported drafts

| ALPN | Protocol versions | Accuracy field |
|---|---|---|
| `common-golf/draft-1` | 0.1.0, 0.1.1 | `shot.reduced_accuracy` |
| `common-golf/draft-2` | 0.2.0 | `shot.estimated`, `club.estimated` |
| `common-golf/draft-3` | 0.3.0 | `shot.estimated`, `club.estimated` |

The negotiated ALPN determines protocol behavior; `info.protocol_version`
is informational. Draft 3 uses mutual Ed25519 raw public keys and verifies
the discovery key before application traffic. It supports server menus
for JSON, raw streams and datagrams, and identifies shot snapshots with
UUIDv7. See the [RFC](../rfc/0001-common-golf-protocol.md) for the full
specification.

Drafts 1 and 2 remain available for compatibility. They use X.509 identities,
with the transport, private channel and shot rules defined by their
respective drafts. Rust requires separate TLS configurations for draft 3
and earlier drafts.

Applications supply measurements and local access policies. The
libraries enforce transport, menu and shot-identity rules. The test harness
provides full schema and cross-field validation.

## Build and test

Building and testing all three libraries requires Python 3.9+, Rust 1.90+,
CMake 3.16+, and a C11 compiler. C dependencies are pinned and verified with
SHA-256 checksums. Native discovery requires Bonjour on macOS, or
`libavahi-compat-libdnssd-dev` and a running Avahi daemon on Linux.

Run these commands from the repository root:

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

The Rust tests include a live mDNS test. The following cross-language
discovery tests also require local multicast and run separately from CI:

```sh
.venv/bin/python tests/compatibility/discovery.py
.venv/bin/python tests/compatibility/reference_apps.py --discovery
```

Test reports are written to `test-results/`. The
[compatibility test guide](../tests/compatibility/README.md) describes the
coverage and runner options.

The [CI workflow](../.github/workflows/reference-implementations.yml) also
runs the ESP32 host tests. Its Windows job builds the Rust DLL and runs the
[C FFI example](../examples/c-ffi/README.md) and a C# smoke test.

After changing a transport dependency, rerun the build, test and packaging
steps. After changing shared C or TLS configuration, also rebuild the ESP32
firmware.
