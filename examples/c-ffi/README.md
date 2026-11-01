# C FFI example (experimental)

This C program demonstrates the Rust crate's C ABI (`cdylib`), which also
supports integration with C# and other languages that can call C functions.

## Scope

- Supports drafts 2 and 3; this example uses draft 3.
- Connects to discovered devices. Listening and advertising are not supported.
- Supports standard channels 0, 1, and 3–8, subject to the endpoint's role.
  `cgp_send` rejects other channels, and incoming streams on those channels
  receive `STOP_SENDING` with error code 1.

## Build on Windows

Install a Rust MSVC toolchain, Visual Studio C++ build tools, CMake, and
NASM (required by aws-lc-rs; see its
[Windows requirements](https://aws.github.io/aws-lc-rs/requirements/windows)).
Run these commands from the repository root:

```powershell
cargo build --locked --release --target x86_64-pc-windows-msvc --manifest-path implementations/rust/Cargo.toml
$lib = "$PWD/implementations/rust/target/x86_64-pc-windows-msvc/release"
cmake -S examples/c-ffi -B examples/c-ffi/build -A x64 "-DCGP_RUST_LIBRARY=$lib/common_golf_protocol.dll.lib"
cmake --build examples/c-ffi/build --config Release
Copy-Item "$lib/common_golf_protocol.dll" examples/c-ffi/build/Release/
ctest --test-dir examples/c-ffi/build -C Release --output-on-failure
```

On macOS or Linux, pass the built `.dylib` or `.so` as `CGP_RUST_LIBRARY`.

Windows CI builds the x64 DLL and runs this example, a loopback QUIC test,
and the .NET 8 smoke test in `examples/csharp`. The `cgp-windows-x64-sdk`
artifact contains the DLL, import library, header, and example.

To run the loopback QUIC test against a built example:

```sh
CGP_C_EXAMPLE=/absolute/path/to/cgp-c-ffi cargo test --locked --manifest-path implementations/rust/Cargo.toml --test ffi -- --ignored
```

## Usage

| Arguments | Behavior |
|---|---|
| None | Run a lifecycle smoke test. |
| `--discover` | Browse DNS-SD for one second and print device descriptors. |
| `<descriptor-json> <key-hex>` | Connect to the device, exchange standard messages, and exit. |

Each discovery result contains `name`, `addresses`, and `properties`. Pass
the complete descriptor to `cgp_connect` with an independently trusted key.

## ABI

The ABI is defined in
[`cgp_rust.h`](../../implementations/rust/include/cgp_rust.h). Functions use
the cdecl calling convention, UTF-8 strings, fixed-width integers, and opaque
handles. Status codes are `0` for success, `1` for a poll timeout, and `-1`
for an error. Error text is thread-local; copy it immediately on the same
thread. Rust panics are reported as errors.

### Threading and memory

Calls block. Serialize all calls for an endpoint, including destruction, on
a worker thread. Callbacks run synchronously on the calling thread inside
`cgp_discover` and `cgp_poll`; they must not call back into the endpoint.
Copy borrowed bytes before returning, catch managed exceptions, and keep
callback work short. In C#, keep delegates alive for the entire native call
and wrap handles in `SafeHandle`.

### Limits and timeouts

| Resource | Limit |
|---|---|
| Connections | One per endpoint |
| Addresses in `cgp_connect` | 1–16 |
| Send payload | 1–65,535 bytes of valid JSON |
| Receive queue | 128 events |
| Discovery, poll, and send timeouts | 1–60,000 ms |
| Connection timeout | 10 seconds |

The receive queue applies backpressure when polling falls behind. A send
timeout closes the connection. Successful sends confirm transport
acceptance, not peer receipt. Asynchronous cancellation is not supported.

### Identity

`cgp_connect` verifies the peer's Ed25519 key against the supplied pin before
sending `info`. Draft 3 also requires the descriptor's `properties.key` to
match. Obtain the pin through trusted pairing; the DNS-SD `key` is
unauthenticated.

Fingerprints encode the raw 32-byte key as lowercase hexadecimal.
`cgp_peer_fingerprint` requires a buffer of at least 65 bytes. Use a separate
identity directory for each process and protect it with ACLs on Windows.

## Validation scope

Automated tests cover the ABI lifecycle, loopback QUIC, and .NET smoke test.
Managed runtime stress, Unity, other Windows architectures, and LAN recovery
have not been tested.
