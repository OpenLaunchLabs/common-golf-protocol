# Rust reference crate

The `common-golf-protocol` crate provides QUIC connections, DNS-SD
discovery, persistent Ed25519 identities and protocol message handling.
It supports standard channels, trust tiers, listener menus, custom JSON,
raw streams and sequenced datagrams.

## Build

Run from the repository root:

```sh
cargo build --manifest-path implementations/rust/Cargo.toml
cargo test --manifest-path implementations/rust/Cargo.toml
```

To use the crate as a local dependency:

```toml
[dependencies]
common-golf-protocol = { path = "../common-golf-protocol/implementations/rust" }
```

## Drafts

| Draft | ALPN | Protocol versions |
|---|---|---|
| `Draft::One` | `common-golf/draft-1` | 0.1.0, 0.1.1 |
| `Draft::Two` | `common-golf/draft-2` | 0.2.0 |
| `Draft::Three` | `common-golf/draft-3` | 0.3.0 |

Pass the accepted drafts to `Identity::server_config` or `client_config`.
Draft 3 requires a separate TLS configuration from drafts 1 and 2 because
rustls selects certificate types per configuration. Drafts 1 and 2 can
share a configuration.

When sending draft 1 messages, the library removes `estimated` from
channels 1 and 3. For draft 2, it removes `reduced_accuracy` from channel 1.
The library does not convert between these fields.

The `serde_json::Value` API preserves unknown fields. `Message::decode_for`
uses `draft3::Message` for draft 3, preserving JSON fields and validating
channel names and UUIDv7 identities. Generate a shot identity with
`draft3::new_shot_uuid()` and reuse it across related channels, updates and
reconnects.

The typed `message::Message` variants for drafts 1 and 2 discard unknown
fields. Their status and state patches use `Patch<T>` to distinguish
absent, null and set values.

## Identity and trust

`Identity::load_or_create(directory)` stores the Ed25519 key as
`identity.pem` (mode 0600 on Unix). The fingerprint is the raw 32-byte
public key in lowercase hex.

Draft 3 uses mutual Ed25519 raw public keys, verifying both signatures.
`endpoint::connect` rejects a missing, malformed or mismatched discovery
`key` before opening channels. Drafts 1 and 2 use self-signed certificates:
the verifier checks the key type and signature, and ignores dates, issuers
and names. Listeners for these drafts request an optional client identity.
Missing or unlisted keys map to `trust::Tier::Maker`.

## Usage

1. Load an `identity::Identity`.
2. For a listener, call `endpoint::Listener::bind` to bind an ephemeral
   port and advertise `_common-golf._udp.local.`. Keep the `Listener` alive
   while accepting connections from its `endpoint`. For a connector, call
   `discovery::browse`, pass a `Discovered` result to `endpoint::connect`,
   and keep the returned `Connected` alive.
3. Create a `Peer` per connection with its role and info message;
   `Peer::new` sends info first. `Role::DeviceController` combines the
   device and controller roles.
4. Send with `peer.send(channel, &json)` and receive from
   `peer.incoming.recv()`.
5. Call `peer.finish(channel)` to finish a channel stream before reopening
   it, or `peer.close()` to close the connection.

### Custom channels (draft 3)

Use `Peer::with_contracts(connection, role, info, contracts, expected_key)`
and register each supported `custom::Contract` with its format, opener and
direction. The listener lists up to 16 offers in `info.contracts`.
The connector passes the discovered key as `expected_key` and calls
`wait_for_menu` before sending custom traffic. Standard channels are
available independently of the menu. `menu()` returns the fixed offers.
Custom streams received before the menu are held until it arrives.

- `send` carries framed custom JSON.
- `open_raw` returns bounded, cancellable raw stream handles. Incoming raw
  streams arrive in `custom_streams`. At most 16 are active per connection.
- `send_datagram` uses per-channel sequences, bounded queues and
  round-robin service; received datagrams arrive in `datagrams`. Oversize
  datagrams and full queues return errors, and `max_datagram_payload`
  reports the current limit. Drain `datagram_errors`, which reports
  dispatch failures by channel and sequence. A full error queue blocks
  datagram sends; reliable channels remain available.
- `Contract::open_connect()` registers the Open Connect contract. Its
  events carry a `Reply`; call `send` on it once per reply.

Drafts 1 and 2 use channel 2 for Open Connect, with the same `Reply` API.
Private JSON channels must be configured with matching IDs on both
endpoints. Applications manage shot counters for these drafts and must
send each shot on only one of channels 1 and 2.

## Stream rules

- Frames carry up to 65,535 payload bytes.
- STOP_SENDING 2 indicates an invalid frame or a missing channel prefix
  after 10 seconds.
- STOP_SENDING 1 indicates incorrect ownership or direction, an unknown or
  unconfigured channel, a duplicate live channel, or more than 64 concurrent
  incoming streams.

Rejections affect only that stream.

## Diagnostic peer

`target/debug/cgp-peer` is the diagnostic peer used by the compatibility
tests. It writes JSONL output. Select a mode with
`--role listen|connect|discover` and a draft with `--draft 1|2|3` (default 3).
Direct addresses are supplied through `--host` and `--port` (default 0).

A listener prints `KEY <fingerprint>` followed by `READY <port>`. Draft 3
connectors require `--expected-key <fingerprint>`. Additional fixture,
identity, timing and output options are defined in
[`cgp-peer.rs`](src/bin/cgp-peer.rs). Applications should use discovery;
see the [Rust example](../../examples/rust/README.md).

## C ABI (experimental)

The crate also builds a `cdylib` with a C ABI for C# and Windows. It
supports connections to draft 2 and draft 3 peers using standard channels.
Custom channels are unsupported. See
[examples/c-ffi](../../examples/c-ffi/README.md) and
[`include/cgp_rust.h`](include/cgp_rust.h).
