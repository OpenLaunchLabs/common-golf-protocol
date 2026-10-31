# wolfSSL component

Builds wolfSSL 5.8.2 (`decea12e223869c8f8f3ab5a53dc90b69f436eb2`) with QUIC,
TLS 1.3, X25519, and Ed25519, using software cryptography and the ESP32
hardware RNG. PSRAM is not required.

Run `python3 scripts/fetch_deps.py` from `examples/esp32` to fetch the
sources. It verifies the archive's SHA-256 and extracts it, with the upstream
license files, to `third_party/wolfssl`.

**License:** wolfSSL is GPL-3.0-or-later, with commercial licenses available.
Firmware that links it must comply; see the upstream `COPYING` and
`LICENSING` files.
