# Compatibility tests

Build the libraries first; see the
[implementation guide](../../implementations/README.md#build-and-test). Run
everything from the repository root. Reports go to `test-results/`.

| Script | Checks |
|---|---|
| `fixtures.py` | The [message corpus](../fixtures/conformance.json) validates against the frozen draft schemas. |
| `test_conformance.py` | Invalid messages fail the schema checks (pytest). |
| `run.py` | Live QUIC between Python, Rust, and C peers. |
| `adversarial.py` | Malformed and hostile streams against each library. |
| `reference_apps.py` | `examples/sim.py` against each example device. |
| `discovery.py` | DNS-SD between every advertiser/browser pair, draft 2. Needs multicast; not run in CI. |

`schema_check.py` holds the schema and cross-field checks shared by these
scripts.

## `run.py`

Runs all nine ordered language pairs, including each language with itself.
Each pair runs draft 1 with every 0.1.0/0.1.1 combination (4 sessions), draft 2
(1 session), and both ALPN mismatch directions, which must fail without
delivering messages (2 handshakes): 63 cases in total.

Each session sends every standard channel, the channel 2 reply, and a private
JSON channel. The runner checks fingerprints, validates every received message
against the schema, and compares it with the corpus.

```sh
.venv/bin/python tests/compatibility/run.py
.venv/bin/python tests/compatibility/run.py --languages python,rust --drafts 2 --no-mismatch
```

`--python`, `--rust`, and `--c` override the peer commands.

## `adversarial.py`

For each language and draft: malformed and declined streams, duplicate
ownership, truncated FIN, maximum-size and fragmented frames, reopening,
multiple clients, and the channel 2 reply.

## `reference_apps.py`

Runs `examples/sim.py` with each of the five example devices on both drafts
(10 sessions), covering Open Connect replies, preliminary-shot metadata, and
the trusted club and flight channels. `--discovery` connects through DNS-SD
instead of direct addresses.

All of these run on loopback. They don't cover real networks or ESP32
hardware.
