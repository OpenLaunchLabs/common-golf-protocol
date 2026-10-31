# wolfSSL component

Builds wolfSSL 5.8.2 (`decea12e223869c8f8f3ab5a53dc90b69f436eb2`) with QUIC,
TLS 1.3, X25519, and Ed25519, using software cryptography and the ESP32
hardware RNG. PSRAM is not required.

Fetch the sources with `python3 scripts/fetch_deps.py` from `examples/esp32`.
The script verifies the archive's SHA-256 and extracts it to
`third_party/wolfssl` with upstream license files intact.

**License:** wolfSSL is GPL-3.0-or-later, with commercial licenses available.
Firmware that links it must comply with that license; see upstream `COPYING`
and `LICENSING`.
