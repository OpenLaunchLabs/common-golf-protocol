# Compatibility tests

These tests validate message schemas and interoperability between the
Python, Rust, and C implementations. Build the libraries using the
[implementation guide](../../implementations/README.md#build-and-test), then
run the tests from the repository root. Integration reports are written to
`test-results/`.

| Script | Coverage |
|---|---|
| `fixtures.py` | Validates the [message corpus](../fixtures/conformance.json) against each draft's schemas and checks datagram headers. |
| `test_conformance.py` | Tests schema acceptance and rejection, and validates JSON examples in the RFC. Run with pytest. |
| `run.py` | Tests live QUIC connections between Python, Rust, and C peers. |
| `adversarial.py` | Tests malformed streams and protocol violations against each library. |
| `reference_apps.py` | Runs `examples/sim.py` against each example device. |
| `discovery.py` | Tests DNS-SD between every advertiser/browser pair. Requires multicast and is not run in CI. |

`schema_check.py` provides the shared schema and cross-field validation.
Tests run on loopback or the local network; ESP32 hardware is outside their
scope.

## Schema validation

```sh
.venv/bin/python tests/compatibility/fixtures.py
.venv/bin/python -m pytest tests/compatibility/test_conformance.py
```

## Cross-language interoperability

`run.py` tests all nine ordered language pairs, including each language
with itself. It covers every combination of versions 0.1.0 and 0.1.1 on
draft 1, plus drafts 2 and 3. It also verifies that every ordered draft
mismatch fails without delivering messages.

Each session exchanges messages on every standard channel and a custom
JSON channel. Draft 3 sessions also test raw streams, datagrams, and
listener key pinning. The runner checks authenticated fingerprints,
validates messages against the schemas, and compares them with the corpus.

```sh
.venv/bin/python tests/compatibility/run.py
.venv/bin/python tests/compatibility/run.py --languages python,rust --drafts 2 --no-mismatch
```

Use `--python`, `--rust`, and `--c` to override the peer commands.

Draft 3 uses the following contract menu:

| Channel | Contract | Format |
|---|---|---|
| 1000 | `io.common-golf.interop-json-v1` | One framed JSON stream per direction |
| 1001 | `io.common-golf.interop-raw-v1` | Independent unidirectional raw streams |
| 1002 | `io.common-golf.interop-datagram-v1` | Datagrams |
| 1003 | `io.common-golf.open-connect-v1` | Open Connect |

Raw stream and datagram payloads include `00 ff 80 00 61 62 63` to detect
text conversion and NUL-termination errors.

## Protocol error handling

`adversarial.py` tests each language and draft for malformed and declined
streams, duplicate channel ownership, truncated FIN, maximum-size and
fragmented frames, non-finite numbers, stream reopening, multiple clients,
and Open Connect replies.

## Reference applications

`reference_apps.py` runs the simulator with all five example devices on all
three drafts. It covers Open Connect replies, shot updates, and trusted
club and flight channels. Pass `--discovery` to connect through DNS-SD
instead of direct addresses.

## Discovery

`discovery.py` defaults to draft 3. It resolves each advertiser's TXT record
and checks its `key` against the listener identity. Pass `--draft 1` or
`--draft 2` to test an earlier draft.
