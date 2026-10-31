# C FFI example (experimental)

A C program that calls the Rust crate through its C ABI (`cdylib`). This is a
fallback for C# and Windows applications that lack a suitable QUIC library; CGP
does not require it.

## Scope

- Draft 2 only.
- Connector only: no listening or advertising.
- Standard channels 0, 1, and 3–8. Channel 2 (Open Connect), private channels,
  and channels 9–999 are rejected with STOP_SENDING 1.

## Build (Windows)

Requires a Rust MSVC toolchain, Visual Studio C++ build tools, CMake, and NASM
(for aws-lc-rs; see its
[Windows requirements](https://aws.github.io/aws-lc-rs/requirements/windows)).

```powershell
cargo build --locked --release --target x86_64-pc-windows-msvc --manifest-path implementations/rust/Cargo.toml
$lib = "$PWD/implementations/rust/target/x86_64-pc-windows-msvc/release"
cmake -S examples/c-ffi -B examples/c-ffi/build -A x64 "-DCGP_RUST_LIBRARY=$lib/common_golf_protocol.dll.lib"
cmake --build examples/c-ffi/build --config Release
Copy-Item "$lib/common_golf_protocol.dll" examples/c-ffi/build/Release/
ctest --test-dir examples/c-ffi/build -C Release --output-on-failure
```

The DLL depends on the MSVC runtime. On macOS or Linux, pass the built `.dylib`
or `.so` as `CGP_RUST_LIBRARY`. CI builds the x64 DLL, runs this program and a
loopback QUIC test, and runs the .NET 8 P/Invoke smoke test in `examples/csharp`.

To run the wire test against a built example:

```sh
CGP_C_EXAMPLE=/absolute/path/to/cgp-c-ffi cargo test --locked --manifest-path implementations/rust/Cargo.toml --test ffi -- --ignored
```

## Usage

- No arguments: a lifecycle smoke test.
- `--discover`: browse DNS-SD for a bounded time. Each result is
  `{name,addresses,properties}`; pass it unchanged to `cgp_connect`.
- `<descriptor-json> <key-hex>`: connect to that peer, exchange standard
  messages, and exit.

## ABI

[`cgp_rust.h`](../../implementations/rust/include/cgp_rust.h) is the reference.
Functions are cdecl and take UTF-8 strings, fixed-width integers, and opaque
handles. Return values: `0` success, `1` poll timeout, `-1` error. Read the
error text right away, on the same thread; it is thread-local. Rust panics are
returned as errors.

**Threading.** Calls block. Use one worker thread per endpoint. Callbacks run
only inside `cgp_discover` and `cgp_poll`, on the calling thread, and must not
call back into the endpoint. In callbacks, copy the borrowed bytes, catch
managed exceptions, and return quickly. In C#, keep delegates rooted for the
whole native call and wrap handles in `SafeHandle`.

**Timeouts.** The receive queue holds 128 events; reliable traffic backs up
when polling falls behind. Each send takes a deadline, and a timed-out send
closes the connection. A successful send means the transport accepted the
data, not that the peer received it. Connect times out after 10 seconds.
Discover, poll, and send timeouts must be 1–60000 ms. There is no asynchronous
cancel.

**Identity.** Connect checks the peer's Ed25519 key against the pin you pass
before sending info. Get the pin from a trusted pairing step: the DNS-SD `key`
property is not authenticated. Fingerprints are lowercase hex of the raw 32-byte
key. Don't share an identity directory between processes; on Windows, protect
it with ACLs.

## Not yet tested

- Managed callbacks under load, GC pressure, repeated connect/dispose, Unity
  IL2CPP/AOT, and domain reload.
- Clean Windows installs, DLL search paths, ARM64/x86, and static CRT.
- LAN discovery across firewall profiles, VPNs, multiple adapters, IPv6 scope
  IDs, sleep/resume, and network changes.
