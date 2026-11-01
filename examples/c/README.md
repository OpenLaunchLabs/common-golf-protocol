# C example

`cgp-demo` uses the [C library](../../implementations/c/README.md) to run a
draft 3 launch monitor or simulator. The launch monitor advertises itself
through DNS-SD and sends one synthetic shot after exchanging `info` messages.
The simulator discovers the device, verifies its advertised key during the
handshake, and sends simulator state. Both roles log received messages.

## Build

Build from the repository root:

```sh
python3 implementations/c/tools/fetch_deps.py
cmake -S implementations/c -B implementations/c/build
cmake --build implementations/c/build --parallel
```

Install Bonjour or the Avahi development headers before building. Without
discovery support, `cgp-demo` builds but cannot run.

## Run

Run the launch monitor and simulator in separate terminals:

```sh
implementations/c/build/cgp-demo --role device --name "C example" --identity .cgp-identities/c-device
implementations/c/build/cgp-demo --role simulator --discover "C example" --identity .cgp-identities/c-simulator
```

Message callbacks are in `demo_app.c`; identity, discovery, and polling are
in `main.c`. The example logs peer fingerprints after the handshake and
assigns every peer the `maker` trust tier.
