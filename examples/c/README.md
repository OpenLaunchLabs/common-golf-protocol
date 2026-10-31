# Portable C reference example

`cgp-demo` uses the C reference library's public API to advertise a device or
discover it as a simulator. Both roles use draft 2. The device sends one
synthetic shot after info; the simulator sends state and prints received messages.

Build from the repository root with Bonjour or Avahi development headers:

```sh
python3 implementations/c/tools/fetch_deps.py
cmake -S implementations/c -B implementations/c/build
cmake --build implementations/c/build --parallel
```

Run one command per terminal:

```sh
implementations/c/build/cgp-demo --role device --name "C example" --identity .cgp-identities/c-device
implementations/c/build/cgp-demo --role simulator --discover "C example" --identity .cgp-identities/c-simulator
```

Stop either process with Ctrl+C. `demo_app.c` holds the message callbacks;
`main.c` handles identity, discovery, and polling. The examples do not infer trust
from an advertised name. Persistent Ed25519 fingerprints are logged after TLS
handshake; the default policy treats peers as makers.

The separate `cgp-peer` diagnostic executable accepts explicit loopback addresses
for automated tests and can select draft 1 or 2. Run its interoperability matrix
with `.venv/bin/python tests/compatibility/run.py`, and native library tests with
`ctest --test-dir implementations/c/build --output-on-failure`.
