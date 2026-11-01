# Rust example

`cgp-demo` runs a draft 3 launch monitor or simulator using the
[Rust crate](../../implementations/rust/README.md).

## Build and run

Build from the repository root, then run the two roles in separate terminals:

```sh
cargo build --locked --manifest-path examples/rust/Cargo.toml
examples/rust/target/debug/cgp-demo --role listen    # terminal 1: launch monitor
examples/rust/target/debug/cgp-demo --role connect   # terminal 2: simulator
```

The launch monitor advertises itself through DNS-SD, sends an initial device
status, and publishes a synthetic shot every two seconds. The simulator
discovers the named service, verifies its advertised key during the
handshake, sends simulator state, and returns a result for each shot.

## Options

| Option | Default |
|---|---|
| `--role listen\|connect` | `listen` |
| `--name NAME` | `Rust launch monitor` (service name to advertise or discover) |
| `--identity DIR` | `.cgp-identities/rust-<role>` |
| `--duration SECONDS` | `60` |

Use the crate's `cgp-peer` executable to test other drafts, send fixtures, or
connect to a specific address.
