# Rust example

A draft 2 launch monitor and simulator built on the
[Rust crate](../../implementations/rust/README.md).

```sh
cargo build --locked --manifest-path examples/rust/Cargo.toml
examples/rust/target/debug/cgp-demo --role listen    # terminal 1: launch monitor
examples/rust/target/debug/cgp-demo --role connect   # terminal 2: simulator
```

The launch monitor advertises itself, sends one status, then a synthetic shot
every 2 seconds. The simulator browses for the service name, connects, sends
state, and returns a result for each shot.

| Option | Default |
|---|---|
| `--role listen\|connect` | `listen` |
| `--name NAME` | `Rust launch monitor` (advertised or looked-up service name) |
| `--identity DIR` | `.cgp-identities/rust-<role>` |
| `--duration SECONDS` | `60` |

Fingerprints are printed but not checked. For draft 1, fixtures, or direct
addresses, use the crate's `cgp-peer`.
