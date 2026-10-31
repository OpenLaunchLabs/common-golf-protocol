# C consumer of the Rust library (experimental ABI 1)

Use whichever C# or Windows QUIC library fits your application and implement CGP
on top. If you cannot find a suitable library, this repository also provides an
experimental fallback: the Rust library exposed through a C FFI DLL that you can
call with P/Invoke. CGP does not require Rust, this DLL, or a particular QUIC
implementation. Every integration follows the same ALPN, TLS identity, discovery,
framing, and channel rules.

This example compiles as C under MSVC and links the Rust `cdylib`. It does not
port the POSIX implementation in `implementations/c` or the existing C demo.
The Windows Actions job builds an x64 DLL, compiles/runs this consumer, tests a
real loopback QUIC exchange and wrong-pin rejection, and runs a .NET 8 P/Invoke
smoke from `examples/csharp`. The SDK artifact contains the DLL, import library,
header and C executable. Windows execution is verified by CI, not a macOS build.

## Scope

The ABI accepts **draft 2 only** (`draft: 2`). It offers standard JSON channels
0, 1, and 3–8. Open Connect channel 2 is not exposed by this ABI; channels 9–999
are reserved. Tests verify that incoming streams on 2 and 9 receive
STOP_SENDING 1 and leave supported traffic working. The Rust and standalone C
libraries also support draft 1 for compatibility testing.

This connector API does not expose private channels, Open Connect replies, or
listening/advertising. Use the Rust library directly for those APIs.

## Build

Build the DLL with a Rust MSVC toolchain and Visual Studio C++ build tools:

```powershell
cargo build --locked --release --target x86_64-pc-windows-msvc --manifest-path implementations/rust/Cargo.toml
$lib = "$PWD/implementations/rust/target/x86_64-pc-windows-msvc/release"
cmake -S examples/c-ffi -B examples/c-ffi/build -A x64 "-DCGP_RUST_LIBRARY=$lib/common_golf_protocol.dll.lib"
cmake --build examples/c-ffi/build --config Release
Copy-Item "$lib/common_golf_protocol.dll" examples/c-ffi/build/Release/
ctest --test-dir examples/c-ffi/build -C Release --output-on-failure
```

CI installs NASM and uses the runner's CMake/MSVC tools. aws-lc-rs includes native
crypto code; this is not an entirely pure-Rust dependency tree. Requirements
vary by locked aws-lc version; consult its
[Windows build requirements](https://aws.github.io/aws-lc-rs/requirements/windows).
The current non-FIPS instructions do not require CMake for aws-lc itself; this C
example does. End users do not need Rust, NASM, Bonjour, Avahi, or a separate QUIC
library. This build uses the normal MSVC runtime: do not promise a DLL with no
runtime dependencies. Verify redistributable requirements on a clean Windows VM.

On macOS/Linux pass the built `.dylib`/`.so` as `CGP_RUST_LIBRARY`. For the wire test:

```sh
CGP_C_EXAMPLE=/absolute/path/to/cgp-c-ffi cargo test --locked --manifest-path implementations/rust/Cargo.toml --test ffi -- --ignored
```

Run the C example without arguments for a deterministic lifecycle smoke, or with
`--discover` for bounded DNS-SD. With two arguments it accepts a discovery JSON
descriptor and trusted key hex, exchanges standard messages, then exits. Discovery
returns `{name,addresses,properties}`; preserve the descriptor for `cgp_connect`.

## ABI and C# contract

The [header](../../implementations/rust/include/cgp_rust.h) is authoritative.
All functions use cdecl, fixed-width integer lengths/statuses, UTF-8 strings and
opaque pointers. Ten exports cover creation, destruction, discovery, connection,
sending, polling, peer fingerprint, disconnect, error text and ABI version.
`0` means success, `1` means poll timeout, `-1` means error. Copy the thread-local
error string immediately on the calling thread. Rust panics at exported operation
boundaries become errors; invalid caller pointers remain undefined behavior.

Use one serialized worker per endpoint; calls block. Callbacks occur only inside
`cgp_discover`/`cgp_poll`, on that calling thread. They cannot reenter the endpoint.
Copy the borrowed bytes before returning, catch managed exceptions inside the
callback, then dispatch copied events to Unity's main thread. Root delegates for
the entire native call; see [Microsoft's callback guidance](https://learn.microsoft.com/en-us/dotnet/standard/native-interop/how-to-implement-callback-functions).
Use `SafeHandle` in a production wrapper and serialize disposal with operations.
There are no callbacks after an operation returns or destruction completes.
A callback that never returns also prevents disposal; callback work must be short.

The receive queue is bounded to 128 events. Reliable traffic backpressures when
polling falls behind. Sends have a caller-supplied deadline; a timeout closes the
connection because cancelling a partial framed write is unsafe to resume. Success
means transport acceptance, not an application acknowledgement. Connect has a
10-second total deadline. Discovery/poll/send intervals are 1–60000 ms. Cancellation
is currently bounded by these deadlines; no asynchronous cancel export exists.

Connect verifies the expected Ed25519 key before sending info. Obtain that pin
from a trusted enrollment flow; copying a DNS-SD `key` into the trusted pin without
confirmation is not authentication. Fingerprints are lowercase hex of the raw
32-byte key, not certificate hashes. Identity storage persists across reconnects;
protect the directory with Windows ACLs and do not share it between concurrent
creators. No early application data/0-RTT API is exposed.

## Remaining release checks

- Test actual managed receive callbacks, GC pressure, repeated connect/dispose,
  Unity IL2CPP/AOT callbacks and domain reload; the .NET sample is only a smoke.
- Exercise clean Windows installs, x64 packaging, DLL search paths and runtime
  imports. ARM64/x86 and static CRT delivery are not covered by this job.
- Test LAN discovery and QUIC across Windows Firewall profiles, VPNs, multiple
  adapters, IPv6 scope IDs, sleep/resume and network changes. Hosted loopback CI
  cannot establish those properties.
- Keep per-connection shot counters and preliminary/final shot updates in the
  application layer. Preserve full-width timestamps when decoding JSON numbers.
