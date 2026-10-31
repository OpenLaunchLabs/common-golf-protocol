# Rust reference crate

An independently buildable `common-golf-protocol` crate, separate from the
examples. It supplies typed standard messages, strict JSON framing, persistent
Ed25519 identity, QUIC listeners/connectors, DNS-SD, channel ownership and
lifecycle, trust tiers, and explicitly arranged private JSON channels.

```sh
cargo test --manifest-path implementations/rust/Cargo.toml
cargo build --manifest-path implementations/rust/Cargo.toml
```

For a local application, add a path dependency pointing at this directory:

```toml
[dependencies]
common-golf-protocol = { path = "../common-golf-protocol/implementations/rust" }
```

## Drafts and identities

`Draft::One` uses `common-golf/draft-1` and supports both 0.1.0 and 0.1.1;
`Draft::Two` uses `common-golf/draft-2` for 0.2.0. Explicitly choose the drafts
passed to `Identity::server_config` / `client_config`. ALPN selects behavior;
the informational `protocol_version` does not. Different sole offers fail TLS.

Draft 1 sends `reduced_accuracy`, while draft 2 sends `estimated`. No accuracy
is invented when translating messages. Unknown fields remain intact when using
the `serde_json::Value` transport API; typed `message::Message` ignores unknown
fields on decode. Sender `estimated` entries must name present numeric fields.
Status/state `Patch<T>` represents absent, null, and set values separately.

`Identity::load_or_create(directory)` persists a PKCS#8 Ed25519 key, with mode
0600 on Unix. Its fingerprint is the raw 32-byte public key in lowercase hex.
The custom TLS verifier checks key type and CertificateVerify signatures while
ignoring certificate dates, issuer chains, and DNS names. Clients present their
identities; listeners request but do not require a client identity. Missing or
unlisted keys map to `trust::Tier::Maker`.

## Application API

1. Create/load an `identity::Identity`.
2. Listeners use `endpoint::Listener::bind` to bind an ephemeral port and publish
   `_common-golf._udp.local.`. Keep the `Listener` alive and accept connections
   from its `endpoint` concurrently. Connectors use `discovery::browse` followed
   by `endpoint::connect` on a selected `Discovered` result; keep its `Connected`
   owner alive. Loopback and ordinary network interfaces are enabled for DNS-SD.
3. Construct a `Peer` for each QUIC connection with the hardware role and info
   message and explicitly arranged private JSON channel IDs (1000 or greater). `Peer::new` sends
   info before opening other streams. Device and controller roles are separate;
   `Role::DeviceController` combines them for devices with buttons.
4. Call `peer.send(channel, &json)` to publish. Receive complete objects through
   `peer.incoming.recv()`. Channel 2 events include a `Reply` whose `send` method
   writes a framed response without a second channel prefix. Reply once to every
   Open Connect body accepted by your simulator.
5. `peer.finish(channel)` permits reopening. Application shot counters continue
   across streams and remain per connection. Do not publish the same shot on
   both channel 1 and channel 2. `peer.close()` closes the QUIC connection.

Every standard stream accepts up to 65,535 payload bytes. Streams are parsed
independently, including fragmented headers/payloads and coalesced frames.
Invalid frames get STOP_SENDING 2, while wrong ownership, stream direction,
unknown channels and duplicate live channels get code 1. Either rejection leaves
other channels usable. Transport idle timeout is 90 seconds, keepalive 15 seconds.
Private JSON channels share the 65,535-byte bound and ordinary framing.
Both endpoints must configure the same channel IDs and message contracts before
connecting. An application decides which peers may use each private channel.

## Diagnostic peer

`target/debug/cgp-peer` is a deterministic interoperability tool. It supports
`--role listen|connect`, `--draft 1|2`, `--version`, `--host`, `--port`,
`--duration`, `--output`, `--identity`, and `--fixtures PATH`. The fixture option
publishes messages from the shared independent corpus. Listener port zero prints
`READY <actual-port>`; logs are JSONL. A listener is a combined device/controller;
a connector is a simulator. They exercise all standard channels, separate shot
numbers on channels 1/2, and an arranged private JSON channel 1000.
`--advertise NAME` publishes the listener. `--role discover` writes discovered
name/port records. Product applications should use discovery; see the
[Rust example](../../examples/rust/README.md).

## Validation

Runtime transport parses JSON objects and enforces framing and stream rules.
The optional typed API uses normal Serde decoding errors and defaults.
Repository tests check emitted messages against the canonical schemas.

Tests cover framing boundaries, metadata per draft, full-width timestamps,
persistent fingerprints, mutual TLS, ALPN mismatch, malformed/duplicate/illegal
stream recovery, maximum payloads, independent connections, patch semantics,
private JSON channels, Open Connect replies, and DNS-SD. The repository's
compatibility runner exercises Rust against independently implemented Python and
C peers.

## Experimental Windows C ABI

The crate also builds a `cdylib` as an experimental fallback for C#/Windows
consumers who cannot find a suitable QUIC library. Use whichever library fits your
application; CGP does not require Rust or this DLL. See the
[C consumer and ABI contract](../../examples/c-ffi/README.md)
for the draft 2 standard-channel subset, Windows CI, P/Invoke smoke, and remaining
platform verification. This ABI does not expose Open Connect channel 2 or private
channels.
