This wrapper builds wolfSSL 5.8.2, pinned to
`decea12e223869c8f8f3ab5a53dc90b69f436eb2`, with QUIC, TLS 1.3,
X25519 and Ed25519. It uses software cryptography and the ESP32 hardware RNG;
PSRAM is not required. Client and server connections share one event-loop task.

Run `python3 scripts/fetch_deps.py` from the ESP32 example to fetch the sources.
The fetcher verifies the SHA-256 of the immutable source archive and leaves
upstream license notices intact under `third_party/wolfssl`.

**Dependency license:** this wolfSSL release is GPL-3.0-or-later, with a
commercial licensing alternative. The example's Apache-2.0 license does not
replace wolfSSL's license. Distributing firmware linked with this dependency
requires complying with its license. See upstream `COPYING` and `LICENSING`.

The configuration is intentionally for an interoperability experiment. Its
dependency pins make the experiment reproducible; update and retest them
before using it in a maintained product.
