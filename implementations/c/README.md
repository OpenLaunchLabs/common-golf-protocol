# C reference library

A static C11 library for POSIX hosts, also used by the
[ESP32 demo](../../examples/esp32/README.md). It handles QUIC/TLS, framing,
stream lifecycle, UTF-8 JSON validation, Ed25519 identities, listener
menus, custom JSON, raw streams and datagrams. Applications supply
callbacks and data.

`cgp_options.draft` selects draft 1 (0.1.1), draft 2 (0.2.0) or draft 3
(0.3.0).

## Build

```sh
python3 implementations/c/tools/fetch_deps.py
cmake -S implementations/c -B implementations/c/build -DCMAKE_BUILD_TYPE=Debug
cmake --build implementations/c/build --parallel
ctest --test-dir implementations/c/build --output-on-failure
```

`fetch_deps.py` downloads ngtcp2, wolfSSL and cJSON at pinned,
SHA-256-verified revisions. The `cgp_discovery` target is built when Bonjour
(macOS) or `libavahi-compat-libdnssd-dev` (Linux, with the Avahi daemon
running) is available. For ASan/UBSan, configure a separate build directory
with `-DCGP_SANITIZE=ON`.

To embed, `add_subdirectory(implementations/c)` and link
`common_golf::common_golf`. On hosts, also link `cgp_posix` (clock,
randomness, logging) and `cgp_discovery` (DNS-SD).

## API

See `include/cgp.h`. Fill `cgp_options` with the role (`CGP_LAUNCH_MONITOR`,
`CGP_CONTROL_BOX` or `CGP_SIMULATOR`), listener or connector, draft, name,
local address, identity directory and callbacks, then call
`cgp_create_options`. Listeners advertise `cgp_port` over DNS-SD.
Connectors pass the discovered address and TXT `key` to `cgp_connect_key`
(`cgp_connect`, without a key, is for drafts 1/2). Call `cgp_poll` every
10 ms and `cgp_destroy` when done. All calls and callbacks must be on one
thread.

- In the `connected` callback, publish info on channel 0 first with
  `cgp_publish`.
- `cgp_publish` returns false for malformed or forbidden messages, declined
  channels, or a full queue.
- `cgp_finish_channel` sends FIN after queued bytes; `cgp_close_channel`
  resets the stream and drops them.
- The `message` callback borrows the JSON bytes until it returns. Messages
  may arrive in any order across streams, including before info.

The transport validates JSON grammar, framing and the `shot_uuid` format.
Applications own other message semantics, including state patching and
snapshot replacement. Generate one `shot_uuid` per shot with
`cgp_shot_uuid` (from UTC milliseconds) and reuse it for related messages.

### Trust and custom channels

The `trust_tier` callback maps a peer fingerprint (the 32-byte Ed25519
public key, lowercase hex) to `internal`, `developer` or `maker` (the
default). `accept_channel` decides which incoming channels to read.

In draft 3, the listener calls `cgp_bind_private` before its first info
with a channel of 1000 or higher, a contract identifier and its wire kind;
`cgp_publish` adds the menu to info. On the connector, the `contract`
callback says which contracts it supports and the `menu` callback signals
that the menu arrived. `cgp_private_matches` and `cgp_channel_contract`
inspect the mapping. Optional `reads` and `reads_contracts` hints can be
added to info.

- Open Connect (`io.common-golf.open-connect-v1`) uses a device-opened
  bidirectional stream; the simulator replies with `cgp_publish` on that
  channel.
- `cgp_open_raw`, `cgp_send_raw_on_stream` and `cgp_finish_stream` handle
  raw streams.
- `cgp_send_datagram` queues one sequenced datagram; receivers get the
  sequence and payload in the `datagram` callback. Oversize sends return
  false, and `cgp_max_datagram_payload` gives the current limit. If a queued
  send later fails, `datagram_error` reports its channel, sequence and
  error.
- Custom stream data that arrives before the menu is held, up to 4,096
  bytes per stream; early datagrams are dropped.

In drafts 1/2, both endpoints call `cgp_bind_private` for static private
JSON channels, and Open Connect uses channel 2.

### Identity

Host identities are stored as `identity.der` in the given directory (file
mode 0600, directory 0700). A damaged file is an error, not replaced. A NULL
directory creates an ephemeral identity for tests. Draft 3 uses Ed25519 raw
public keys in both directions, and the connector checks the advertised key
before any application callback. Drafts 1/2 use the same key in a
self-signed X.509 certificate.

`cgp_discovery_start_key` advertises `key`, `alpn` and `caps`, and passes
only results with a valid `key` to its callback.

## Limits

| Resource | Limit |
|---|---|
| Peers | 8 (`CONFIG_CGP_MAX_CONNECTIONS`) |
| Streams per peer | 64: 24 peer-opened unidirectional, 24 peer-opened bidirectional, 16 local |
| Menu bindings per peer | 16 |
| Active raw streams per peer | 16 |
| Held bytes per pre-menu stream | 4,096 |
| Datagram payload | Up to 1,100 bytes |
| Send queue per peer | 262,172 bytes, including buffer headers |
| Frame payload | 65,535 bytes |

Sockets are IPv4 only, with no Windows support. Embedded targets supply
their own clock, randomness, logging and DNS-SD; see
`examples/esp32/main/app_main.c`.

## Executables

- `cgp-demo`: the [C example](../../examples/c/README.md).
- `cgp-peer`: the compatibility-test peer. It takes direct `--host`/`--port`
  and `--fixtures tests/fixtures/conformance.json`; draft 3 connectors need
  `--expected-key` from the listener's `KEY` line. See `tools/peer.c`.

## License

The library is Apache-2.0. ngtcp2 and cJSON are MIT. **wolfSSL is
GPL-3.0-or-later** (commercial licenses are available), and binaries that
link it must comply with that license.
