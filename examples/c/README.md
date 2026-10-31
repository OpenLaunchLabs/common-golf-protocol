# C example

`cgp-demo` runs a draft 2 device or simulator on the
[C library](../../implementations/c/README.md). The device advertises itself
and sends one synthetic shot after info. The simulator discovers it and sends
state. Both print what they receive.

Build from the repository root:

```sh
python3 implementations/c/tools/fetch_deps.py
cmake -S implementations/c -B implementations/c/build
cmake --build implementations/c/build --parallel
```

Discovery needs Bonjour or the Avahi development headers at build time;
without them `cgp-demo` builds but exits with an error.

Run each in its own terminal:

```sh
implementations/c/build/cgp-demo --role device --name "C example" --identity .cgp-identities/c-device
implementations/c/build/cgp-demo --role simulator --discover "C example" --identity .cgp-identities/c-simulator
```

`demo_app.c` has the message callbacks; `main.c` handles identity, discovery,
and polling. Peer fingerprints are logged after the handshake, and every peer
is treated as `maker`.
