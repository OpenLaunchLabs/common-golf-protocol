# Rust reference crate

The `common-golf-protocol` crate: typed standard messages, framing, persistent
Ed25519 identities, QUIC listeners and connectors, DNS-SD, trust tiers, and
private JSON channels.

```sh
cargo build --manifest-path implementations/rust/Cargo.toml
cargo test --manifest-path implementations/rust/Cargo.toml
```

As a path dependency:

```toml
[dependencies]
common-golf-protocol = { path = "../common-golf-protocol/implementations/rust" }
```

## Drafts

`Draft::One` is `common-golf/draft-1` (0.1.0, 0.1.1); `Draft::Two` is
`common-golf/draft-2` (0.2.0). Pass the accepted drafts to
`Identity::server_config` or `client_config`. When sending, draft 1 strips
`estimated` from channels 1 and 3, and draft 2 strips `reduced_accuracy` from
channel 1. Neither field is derived from the other.

The `serde_json::Value` API keeps unknown fields; typed `message::Message`
drops them. `Patch<T>` distinguishes absent, null, and set values in status
and state patches.

## Identity and trust

`Identity::load_or_create(directory)` stores the Ed25519 key as `identity.pem`
(mode 0600 on Unix). The fingerprint is the raw 32-byte public key in
lowercase hex. The TLS verifier checks the key type and signature and ignores
dates, issuers, and names. Listeners request but do not require a client
identity. Missing or unlisted keys map to `trust::Tier::Maker`.

## Usage

1. Load an `identity::Identity`.
2. Listener: `endpoint::Listener::bind` binds an ephemeral port and advertises
   `_common-golf._udp.local.`. Keep the `Listener` alive and accept connections
   from its `endpoint`.
   Connector: `discovery::browse`, then `endpoint::connect` with a
   `Discovered`; keep the returned `Connected` alive.
3. Create a `Peer` per connection with its role, info message, and private
   channel IDs (1000 or higher). `Peer::new` sends info first.
   `Role::DeviceController` combines the device and controller roles.
4. Send with `peer.send(channel, &json)` and receive from
   `peer.incoming.recv()`. Channel 2 events carry a `Reply`; call `send` on it
   once per Open Connect message.
5. `peer.finish(channel)` closes a channel so it can be reopened;
   `peer.close()` closes the connection.

Applications own shot counters and must not send the same shot on channels 1
and 2.

## Stream rules

- Frames carry up to 65,535 payload bytes.
- STOP_SENDING 2: an invalid frame, or no channel prefix within 10 s.
- STOP_SENDING 1: wrong ownership or direction, an unknown or unconfigured
  channel, a duplicate live channel, or more than 64 concurrent incoming
  streams.

Rejections affect only that stream. Private channels must be configured the
same way on both endpoints; the application decides which peers may use them.

## Diagnostic peer

`target/debug/cgp-peer` drives the compatibility tests. Options: `--role
listen|connect|discover`, `--draft 1|2` (default 2), `--version`, `--host`,
`--port` (default 0), `--duration`, `--output`, `--identity`, `--fixtures`,
and `--advertise NAME`. A listener prints `READY <port>`. Output is JSONL.
Products should use discovery; see the
[Rust example](../../examples/rust/README.md).

## C ABI (experimental)

The crate also builds a `cdylib` with a C ABI as a fallback for C# and
Windows. It supports draft 2 standard channels only, as a connector. See
[examples/c-ffi](../../examples/c-ffi/README.md) and
[`include/cgp_rust.h`](include/cgp_rust.h).
