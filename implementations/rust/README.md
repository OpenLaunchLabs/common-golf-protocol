# Rust reference crate

The `common-golf-protocol` crate: typed standard messages, JSON framing,
persistent Ed25519 identities, QUIC listeners and connectors, DNS-SD, trust
tiers, and private JSON channels.

```sh
cargo build --manifest-path implementations/rust/Cargo.toml
cargo test --manifest-path implementations/rust/Cargo.toml
```

Use it as a path dependency:

```toml
[dependencies]
common-golf-protocol = { path = "../common-golf-protocol/implementations/rust" }
```

## Drafts

`Draft::One` uses `common-golf/draft-1` (0.1.0 and 0.1.1); `Draft::Two` uses
`common-golf/draft-2` (0.2.0). Pass the drafts explicitly to
`Identity::server_config` / `client_config`. Draft 1 sends `reduced_accuracy`
and draft 2 sends `estimated`; neither is synthesized when the other is absent.

The `serde_json::Value` API keeps unknown fields; typed `message::Message`
drops them on decode. `Patch<T>` distinguishes absent, null, and set values in
status and state patches.

## Identity and trust

`Identity::load_or_create(directory)` stores a PKCS#8 Ed25519 key (mode 0600 on
Unix). The fingerprint is the raw 32-byte public key in lowercase hex. The TLS
verifier checks key type and the CertificateVerify signature and ignores dates,
issuers, and DNS names. Listeners request but do not require a client identity.
Missing or unlisted keys map to `trust::Tier::Maker`.

## Application API

1. Load an `identity::Identity`.
2. A listener calls `endpoint::Listener::bind`, which binds an ephemeral port
   and advertises `_common-golf._udp.local.`. Keep the `Listener` alive and
   accept connections from its `endpoint`. A connector calls
   `discovery::browse`, then `endpoint::connect` with a `Discovered` result,
   and keeps the returned `Connected` alive.
3. Create a `Peer` per connection with its role, info message, and private
   channel IDs (1000 or higher). `Peer::new` sends info before anything else.
   `Role::DeviceController` combines the device and controller roles.
4. Send with `peer.send(channel, &json)`; receive from `peer.incoming.recv()`.
   Channel 2 events carry a `Reply`; call its `send` once per accepted Open
   Connect message.
5. `peer.finish(channel)` closes a channel so it can be reopened.
   `peer.close()` closes the connection. Shot counters are per connection and
   continue across reopened streams; don't send the same shot on channels 1
   and 2.

Frames carry up to 65,535 payload bytes on standard and private channels.
Invalid frames get STOP_SENDING 2. Wrong ownership, wrong direction, unknown
channels, and duplicate live channels get STOP_SENDING 1. Rejections affect only
that stream. Private channels must be configured identically on both endpoints;
the application decides which peers may use them.

## Diagnostic peer

`target/debug/cgp-peer` drives the compatibility tests. Options: `--role
listen|connect|discover`, `--draft 1|2`, `--version`, `--host`, `--port`,
`--duration`, `--output`, `--identity`, `--fixtures PATH`, and `--advertise
NAME`. With `--port 0`, a listener prints `READY <port>`. Output is JSONL.
Products should use discovery; see the [Rust example](../../examples/rust/README.md).

## C ABI (experimental)

The crate also builds a `cdylib` with a C ABI, intended as a fallback for C# and
Windows. It supports draft 2 standard channels only, without Open Connect or
private channels. See [examples/c-ffi](../../examples/c-ffi/README.md).
