# Rust reference crate

The `common-golf-protocol` crate: typed standard messages, framing,
persistent Ed25519 identities, QUIC listeners and connectors, DNS-SD, trust
tiers, listener menus, custom JSON and raw streams, and sequenced
datagrams.

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

`Draft::One` is `common-golf/draft-1` (0.1.0, 0.1.1), `Draft::Two` is
`common-golf/draft-2` (0.2.0), and `Draft::Three` is `common-golf/draft-3`
(0.3.0). Pass the accepted drafts to `Identity::server_config` or
`client_config`. A configuration can't mix draft 3 with drafts 1/2,
because rustls selects certificate-type policy per configuration; drafts 1
and 2 can share one. When sending, draft 1 strips `estimated` from channels
1 and 3 and draft 2 strips `reduced_accuracy` from channel 1; neither is
derived from the other.

The `serde_json::Value` API keeps unknown fields; the legacy typed
`message::Message` drops them. `Message::decode_for` dispatches draft 3 to
`draft3::Message`, which keeps its JSON fields and validates channel names
and UUIDv7 identities. Generate shot identities with
`draft3::new_shot_uuid()` and reuse them across related channels, updates
and reconnects. For legacy typed status and state patches, `Patch<T>`
distinguishes absent, null and set values.

## Identity and trust

`Identity::load_or_create(directory)` stores the Ed25519 key as
`identity.pem` (mode 0600 on Unix). The fingerprint is the raw 32-byte
public key in lowercase hex.

Draft 3 uses mutual Ed25519 raw public keys, verifying both signatures.
`endpoint::connect` rejects a missing, malformed or mismatched discovery
`key` before any channel opens. Drafts 1/2 use self-signed certificates;
the verifier checks key type and signature and ignores dates, issuers and
names, and listeners request but do not require a client identity. Missing
or unlisted keys map to `trust::Tier::Maker`.

## Usage

1. Load an `identity::Identity`.
2. Listener: `endpoint::Listener::bind` binds an ephemeral port and
   advertises `_common-golf._udp.local.`. Keep the `Listener` alive and
   accept connections from its `endpoint`. Connector: `discovery::browse`,
   then `endpoint::connect` with a `Discovered`; keep the returned
   `Connected` alive.
3. Create a `Peer` per connection with its role and info message;
   `Peer::new` sends info first. `Role::DeviceController` combines the
   device and controller roles.
4. Send with `peer.send(channel, &json)` and receive from
   `peer.incoming.recv()`.
5. `peer.finish(channel)` closes a channel so it can be reopened;
   `peer.close()` closes the connection.

### Custom channels (draft 3)

Use `Peer::with_contracts(connection, role, info, contracts, expected_key)`
and register each supported `custom::Contract` with its format, opener and
direction. The listener puts its offers in `info.contracts` (at most 16).
The connector passes the discovered key as `expected_key` and calls
`wait_for_menu` before custom sends; standard traffic doesn't wait.
`menu()` returns the fixed offers. Custom streams that arrive before the
menu wait for it.

- `send` carries framed custom JSON.
- `open_raw` returns bounded, cancellable raw stream handles; incoming ones
  arrive in `custom_streams`. At most 16 are active per connection.
- `send_datagram` uses per-channel sequences, bounded queues and
  round-robin service; received datagrams arrive in `datagrams`. Oversize
  datagrams and full queues return errors, and `max_datagram_payload`
  reports the current limit. Drain `datagram_errors`, which reports
  dispatch failures with channel and sequence; it is bounded and blocks
  datagram sends when full, while reliable channels continue.
- `Contract::open_connect()` registers the Open Connect contract. Its
  events carry a `Reply`; call `send` on it once per reply.

Drafts 1/2 use channel 2 for Open Connect (events carry a `Reply` the same
way) and private JSON channels configured identically on both endpoints by
ID. Applications own shot counters there and don't send the same shot on
channels 1 and 2.

## Stream rules

- Frames carry up to 65,535 payload bytes.
- STOP_SENDING 2: an invalid frame, or no channel prefix within 10 s.
- STOP_SENDING 1: wrong ownership or direction, an unknown or unconfigured
  channel, a duplicate live channel, or more than 64 concurrent incoming
  streams.

Rejections affect only that stream.

## Diagnostic peer

`target/debug/cgp-peer` drives the compatibility tests. Options: `--role
listen|connect|discover`, `--draft 1|2|3` (default 2), `--version`,
`--host`, `--port` (default 0), `--duration`, `--output`, `--identity`,
`--fixtures`, `--advertise NAME` and `--expected-key`. A listener prints
`KEY <fingerprint>` then `READY <port>`; draft 3 connectors need
`--expected-key`. Output is JSONL. Products use discovery; see the
[Rust example](../../examples/rust/README.md).

## C ABI (experimental)

The crate also builds a `cdylib` with a C ABI for C# and Windows. It is a
connector for draft 2 and draft 3 standard channels, without custom
channels. See [examples/c-ffi](../../examples/c-ffi/README.md) and
[`include/cgp_rust.h`](include/cgp_rust.h).
