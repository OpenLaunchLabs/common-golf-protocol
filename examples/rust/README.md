# Rust reference application

This application uses the independent crate in `implementations/rust` to run a
draft 2 launch monitor or simulator. It advertises and discovers devices through
DNS-SD; users select a service name rather than an address or port.

From the repository root, build once and run these in separate terminals:

```sh
cargo build --locked --manifest-path examples/rust/Cargo.toml
examples/rust/target/debug/cgp-demo --role listen
examples/rust/target/debug/cgp-demo --role connect
```

The monitor emits synthetic shots and status. The simulator sends state and a
result for each shot. `--name NAME` changes the advertised/selected service name,
`--identity DIR` selects persistent Ed25519 key storage, and `--duration SECONDS`
sets the run time (default 60 seconds). Fingerprints are printed for inspection;
this demonstration accepts discovered peers without a product enrollment policy.
Each accepted connection has its own channel streams and shot counter.

For diagnostic host/port inputs, draft 1 compatibility, fixtures, and cross-language
tests, use the library's `cgp-peer` executable instead. See the
[Rust library guide](../../implementations/rust/README.md).
