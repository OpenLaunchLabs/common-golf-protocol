# C example

`cgp-demo` uses the [C library](../../implementations/c/README.md) to run a
draft 2 device or simulator. The device advertises itself and sends one
synthetic shot after info. The simulator discovers it, sends state, and prints
what it receives.

Build from the repository root (requires Bonjour or Avahi development headers):

```sh
python3 implementations/c/tools/fetch_deps.py
cmake -S implementations/c -B implementations/c/build
cmake --build implementations/c/build --parallel
```

Run each in its own terminal:

```sh
implementations/c/build/cgp-demo --role device --name "C example" --identity .cgp-identities/c-device
implementations/c/build/cgp-demo --role simulator --discover "C example" --identity .cgp-identities/c-simulator
```

`demo_app.c` has the message callbacks; `main.c` handles identity, discovery,
and polling. Peer fingerprints are logged after the handshake, and every peer is
treated as `maker`.
