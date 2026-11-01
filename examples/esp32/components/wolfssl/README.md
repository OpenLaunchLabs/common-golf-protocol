# wolfSSL component

This component builds wolfSSL 5.8.2 at commit
`decea12e223869c8f8f3ab5a53dc90b69f436eb2` with QUIC, TLS 1.3, X25519, and
Ed25519 support. It uses software cryptography and the ESP32 hardware random
number generator. PSRAM is not required.

Run `python3 scripts/fetch_deps.py` from `examples/esp32` to fetch the
sources. The script verifies the archive's SHA-256 checksum and extracts the
sources and upstream license files to `third_party/wolfssl`.

wolfSSL is licensed under GPL-3.0-or-later; commercial licenses are also
available. See the upstream `COPYING` and `LICENSING` files for terms that
apply to firmware linking this component.
