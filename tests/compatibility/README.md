# Cross-language QUIC compatibility

`run.py` launches independent Python, Rust, and C processes and exchanges actual
QUIC/TLS traffic on loopback. It checks all nine ordered language pairs, including
self-pairs. Each pair exercises:

- 0.1.0 ↔ 0.1.0, 0.1.1 ↔ 0.1.1, and both draft-1 patch-version combinations;
- draft 2 (0.2.0);
- all standard JSON channels, including the unprefixed channel-2 reply;
- explicitly configured private JSON channels;
- both ordered ALPN mismatches, which must fail without delivering messages.

That is 63 cases: 45 positive sessions and 18 negative handshakes. Every received
standard message is validated against the appropriate published JSON Schema.
The runner checks authenticated Ed25519 fingerprints, channel coverage and
draft-specific accuracy metadata. Every implementation reads the same
[message corpus](../fixtures/conformance.json), and the runner compares received
messages with it. `fixtures.py` validates it against the published draft
schema snapshots before the live tests. `schema_check.py` supplies test-only
schema and cross-field checks shared by fixtures and captured traffic checks.
Run `python -m pytest tests/compatibility/test_conformance.py` to exercise invalid
message conformance cases.

`adversarial.py` additionally probes each language on both drafts, with
13 required checks per case (78 total). These exercise malformed and declined
streams, duplicate ownership, truncated FIN, maximum frames, reopening, independent
clients, and the channel-2 response path. It runs in CI and writes
`test-results/adversarial.json`. `discovery.py` checks the nine DNS-SD combinations
separately because it requires a multicast-capable network.

`reference_apps.py` pairs the actual simulator class with all five reference
device classes on each draft (10 sessions), including Open Connect responses,
draft-specific preliminary-shot metadata, and trusted club/flight channels.
It runs in CI; `--discovery` additionally resolves only the test's own
unique DNS-SD service names.

Follow the [implementation guide](../../implementations/README.md#build-and-test-locally)
for setup, builds, and the complete test sequence. After building, invoke the
matrix from the repository root:

```sh
.venv/bin/python tests/compatibility/run.py
```

For a focused run, use `--languages python,rust --drafts 2 --no-mismatch`.
Executable commands can be overridden with `--python`, `--rust`, and `--c`.
The default machine-readable report is `test-results/compatibility.json`.
No test contacts a launch monitor or modifies a hardware identity. Direct
addresses are confined to the diagnostic peers; product integrations should
use the libraries' DNS-SD discovery APIs.

This matrix is transport compatibility testing, not proof of every application
behavior or RF conditions on an ESP32. Hardware flashing, resource measurements,
and multicast behavior on a deployment network require separate verification.
