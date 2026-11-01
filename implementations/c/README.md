# C reference library

The C reference library provides QUIC transport for POSIX hosts and the
[ESP32 demo](../../examples/esp32/README.md). This static C11 library handles
TLS, framing, stream lifecycle, UTF-8 JSON validation, Ed25519 identities,
server menus, and custom JSON, raw streams and datagrams. Applications
supply data and handle connection and message callbacks.

`cgp_options.draft` selects draft 1 (0.1.1), draft 2 (0.2.0) or draft 3
(0.3.0). An omitted or zero value selects draft 3.

## Build

Run from the repository root:

```sh
python3 implementations/c/tools/fetch_deps.py
cmake -S implementations/c -B implementations/c/build -DCMAKE_BUILD_TYPE=Debug
cmake --build implementations/c/build --parallel
ctest --test-dir implementations/c/build --output-on-failure
```

`fetch_deps.py` downloads pinned revisions of ngtcp2, wolfSSL and cJSON and
verifies their SHA-256 checksums. The `cgp_discovery` target is built when Bonjour
(macOS) or `libavahi-compat-libdnssd-dev` (Linux, with the Avahi daemon
running) is available. For ASan/UBSan, configure a separate build directory
with `-DCGP_SANITIZE=ON`.

To include the library in a CMake project, use
`add_subdirectory(implementations/c)` and link `common_golf::common_golf`.
On POSIX hosts, also link `cgp_posix` for clock, randomness and logging
support, and `cgp_discovery` for DNS-SD.

## API

The public API is declared in [`include/cgp.h`](include/cgp.h).

1. Populate `cgp_options` with the role (`CGP_LAUNCH_MONITOR`,
   `CGP_CONTROL_BOX` or `CGP_SIMULATOR`), `is_server` (`true` for a QUIC server,
   `false` for a QUIC client), draft, name, local address, identity directory
   and callbacks. The application role is independent of the QUIC role.
2. Create the engine with `cgp_create_options`.
3. For a server, advertise the port returned by `cgp_port` over DNS-SD.
   For a client, pass the discovered address and TXT `key` to
   `cgp_connect_key`. The keyless `cgp_connect` is available for drafts 1
   and 2.
4. Call `cgp_poll` every 10 ms. Call `cgp_destroy` to release the engine.

All API calls and callbacks must run on the same thread.

- In the `connected` callback, use `cgp_publish` to send info on channel 0
  before publishing to other channels.
- `cgp_publish` returns false for malformed or forbidden messages, declined
  channels, or a full queue.
- `cgp_finish_channel` sends FIN after queued bytes; `cgp_close_channel`
  resets the stream and drops them.
- JSON bytes passed to the `message` callback remain valid only until the
  callback returns. Messages on separate streams may arrive in any order,
  including before info.

The transport validates JSON grammar, framing and the `shot_uuid` format.
Applications handle other message semantics, including state patches and
snapshot replacement. For draft 3, generate one `shot_uuid` per shot with
`cgp_shot_uuid`, using UTC milliseconds, and reuse it for related messages.

### Trust and custom channels

The `trust_tier` callback maps a peer fingerprint (the 32-byte Ed25519
public key, lowercase hex) to `internal`, `developer` or `maker` (the
default). `accept_channel` decides which incoming channels to read.

In draft 3, the server registers each custom channel with
`cgp_bind_private` before publishing its first info message. Each binding
specifies a channel number of 1000 or higher, a contract identifier and a
wire format. `cgp_publish` includes these bindings in the info menu.

On the client, the `contract` callback identifies supported contracts
and the `menu` callback reports menu availability. Use
`cgp_private_matches` and `cgp_channel_contract` to inspect the bindings.
Info may also include the optional `reads` and `reads_contracts` hints.

- Open Connect (`io.common-golf.open-connect-v1`) uses a device-opened
  bidirectional stream; the simulator replies with `cgp_publish` on that
  channel.
- `cgp_open_raw`, `cgp_send_raw_on_stream` and `cgp_finish_stream` handle
  raw streams.
- `cgp_send_datagram` queues one sequenced datagram. The receiver's
  `datagram` callback provides its sequence and payload. Oversize sends return
  false, and `cgp_max_datagram_payload` gives the current limit. If a queued
  send later fails, `datagram_error` reports its channel, sequence and
  error.
- Custom stream data received before the menu is buffered, up to 4,096
  bytes per stream; early datagrams are dropped.

In drafts 1 and 2, both endpoints call `cgp_bind_private` for static private
JSON channels, and Open Connect uses channel 2.

### Identity

Host identities are stored as `identity.der` in the configured directory,
with file mode 0600 and directory mode 0700. Loading a damaged identity
file fails without overwriting it. A NULL directory creates an ephemeral
identity for tests.

Draft 3 uses Ed25519 raw public keys in both directions. The client
checks the advertised key before invoking application callbacks. Drafts 1
and 2 use the same key in a self-signed X.509 certificate.

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

The POSIX transport supports IPv4 and does not support Windows. Embedded
targets supply their own clock, randomness, logging and DNS-SD; see
[`app_main.c`](../../examples/esp32/main/app_main.c).

## Executables

- `cgp-demo`: the [C example](../../examples/c/README.md).
- `cgp-peer`: the compatibility-test peer. It accepts `--host`, `--port`
  and `--fixtures tests/fixtures/conformance.json`. Draft 3 clients require
  `--expected-key` from the server's `KEY` line. See
  [`tools/peer.c`](tools/peer.c).

## License

The library is Apache-2.0. ngtcp2 and cJSON are MIT. **wolfSSL is
GPL-3.0-or-later** (commercial licenses are available), and binaries that
link it must comply with that license.
