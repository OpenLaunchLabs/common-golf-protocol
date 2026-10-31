# Compatibility tests

Build the libraries first; see the
[implementation guide](../../implementations/README.md#build-and-test). Run
everything from the repository root. Reports go to `test-results/`.

| Script | Checks |
|---|---|
| `fixtures.py` | The [message corpus](../fixtures/conformance.json) validates against frozen draft 1/2 schemas and working draft 3 schemas, with datagram header vectors. |
| `test_conformance.py` | Invalid messages fail the schema checks (pytest). |
| `run.py` | Live QUIC between Python, Rust, and C peers. |
| `adversarial.py` | Malformed and hostile streams against each library. |
| `reference_apps.py` | `examples/sim.py` against each example device. |
| `discovery.py` | DNS-SD between every advertiser/browser pair, defaulting to draft 3. Needs multicast; not run in CI. |

`schema_check.py` holds the schema and cross-field checks shared by these
scripts.

Draft 3 discovery also resolves each advertiser's TXT record independently
and checks its required key against the listener identity: nine browse checks
and three key checks. Pass `--draft 1` or `--draft 2` for legacy discovery.

## `run.py`

Runs all nine ordered language pairs, including each language with itself.
Each pair runs draft 1 with every 0.1.0/0.1.1 combination (4 sessions), draft 2
and draft 3 (1 session each), and all six ordered draft mismatch directions,
which must fail without delivering messages: 108 cases in total.

Each session sends every standard channel and custom JSON. Drafts 1/2 use
channel 2 for Open Connect; draft 3 uses a listener-menu contract and also
exercises raw streams and datagrams. The runner checks authenticated
fingerprints, validates messages against the applicable schemas, and compares
them with the corpus. Draft 3 connectors pin the listener's advertised raw key.

```sh
.venv/bin/python tests/compatibility/run.py
.venv/bin/python tests/compatibility/run.py --languages python,rust --drafts 2 --no-mismatch
```

`--python`, `--rust`, and `--c` override the peer commands.

## `adversarial.py`

For each language and draft: malformed and declined streams, duplicate
ownership, truncated FIN, maximum-size and fragmented frames, non-finite
numbers, reopening, multiple clients, and Open Connect replies. The nine
language/draft combinations each run 14 checks.

## `reference_apps.py`

Runs `examples/sim.py` with each of the five example devices on all three
drafts (15 sessions), covering Open Connect replies, shot updates, and the
trusted club and flight channels. `--discovery` connects through DNS-SD
instead of direct addresses.

All of these run on loopback. They don't cover real networks or ESP32
hardware.
