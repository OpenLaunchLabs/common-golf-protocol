# C reference library

A static C11 library for POSIX hosts, also used by the
[ESP32 demo](../../examples/esp32/README.md). It handles QUIC/TLS, framing,
stream lifecycle, UTF-8 JSON validation, Ed25519 identities, and private JSON
channels. Applications supply callbacks and data.

`cgp_options.draft` selects draft 1 (sends 0.1.1) or draft 2 (0.2.0).

## Build

```sh
python3 implementations/c/tools/fetch_deps.py
cmake -S implementations/c -B implementations/c/build -DCMAKE_BUILD_TYPE=Debug
cmake --build implementations/c/build --parallel
ctest --test-dir implementations/c/build --output-on-failure
```

`fetch_deps.py` downloads ngtcp2, wolfSSL, and cJSON at pinned, SHA-256
verified revisions. The `cgp_discovery` target is built when Bonjour (macOS)
or `libavahi-compat-libdnssd-dev` (Linux, with the Avahi daemon running) is
available. For ASan/UBSan, configure a separate build directory with
`-DCGP_SANITIZE=ON`.

To embed, `add_subdirectory(implementations/c)` and link
`common_golf::common_golf`. On hosts, also link `cgp_posix` (clock,
randomness, logging) and `cgp_discovery` (DNS-SD).

## API

See `include/cgp.h`. Fill `cgp_options` with the role (`CGP_LAUNCH_MONITOR`,
`CGP_CONTROL_BOX`, or `CGP_SIMULATOR`), listener or connector, draft, name,
local address, identity directory, and callbacks, then call
`cgp_create_options`. Listeners advertise `cgp_port` over DNS-SD; connectors
pass discovered addresses to `cgp_connect`. Call `cgp_poll` every 10 ms, and
`cgp_destroy` when done. All calls and callbacks must be on one thread.

- In the `connected` callback, publish info on channel 0 first with
  `cgp_publish`.
- `cgp_publish` returns false for malformed or forbidden messages, declined
  channels, or a full queue.
- Channel 2 is a device-opened bidirectional stream. The simulator replies with
  `cgp_publish(peer, 2, reply_json)`, without a channel prefix.
- `cgp_finish_channel` sends FIN after queued bytes; `cgp_close_channel` resets
  the stream and drops them.
- The `message` callback borrows the JSON bytes until it returns. Messages may
  arrive in any order across streams, including before info.

Applications own shot counters, status/state patching, and shot replacement as
described in the RFC, and must not send the same shot on channels 1 and 2.

### Trust and private channels

The `trust_tier` callback maps a peer fingerprint (32-byte Ed25519 public key,
lowercase hex) to `internal`, `developer`, or `maker` (the default).
`accept_channel` applies receive permissions.

For a private channel, both endpoints call `cgp_bind_private` with a channel
number of 1000 or higher and an agreed identifier, usually in the `connected`
callback. Nothing is negotiated on the wire. `cgp_private_matches` and
`cgp_channel_contract` inspect the binding. Unbound private channels are
declined.

### Identity

Host identities are stored as `identity.der` in the given directory (file mode
0600, directory 0700). A damaged file is an error, not replaced. A NULL
directory creates an ephemeral identity for tests. Certificates are
self-signed; expiry and chains are ignored.

## Limits

| Resource | Limit |
|---|---|
| Peers | 8 (`CONFIG_CGP_MAX_CONNECTIONS`) |
| Streams per peer | 64: 24 peer-opened unidirectional, 24 peer-opened bidirectional, 16 local |
| Private bindings per peer | 16 |
| Send queue per peer | 262,172 bytes, including buffer headers |
| Frame payload | 65,535 bytes |

Sockets are IPv4 only, with no Windows support. Embedded targets supply their
own clock, randomness, logging, and DNS-SD; see
`examples/esp32/main/app_main.c`.

## Executables

- `cgp-demo`: the [C example](../../examples/c/README.md).
- `cgp-peer`: the compatibility-test peer. It accepts direct `--host`/`--port`
  and `--fixtures tests/fixtures/conformance.json`; see `tools/peer.c` for all
  options.

## License

The library is Apache-2.0. ngtcp2 and cJSON are MIT. **wolfSSL is
GPL-3.0-or-later** (commercial licenses are available), and binaries that link
it must comply with that license.
