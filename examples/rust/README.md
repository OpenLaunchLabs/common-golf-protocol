# Rust example

A draft 2 launch monitor and simulator built on the
[Rust crate](../../implementations/rust/README.md). Devices are found by DNS-SD
service name.

```sh
cargo build --locked --manifest-path examples/rust/Cargo.toml
examples/rust/target/debug/cgp-demo --role listen    # terminal 1: launch monitor
examples/rust/target/debug/cgp-demo --role connect   # terminal 2: simulator
```

The launch monitor sends synthetic shots and status; the simulator sends state
and a result for each shot.

| Option | Default |
|---|---|
| `--name NAME` | `Rust launch monitor` (advertised or selected service name) |
| `--identity DIR` | Ed25519 key directory |
| `--duration SECONDS` | `60` |

Fingerprints are printed but not checked; the demo trusts any discovered peer.
For draft 1, fixtures, or direct addresses, use the crate's `cgp-peer`.
