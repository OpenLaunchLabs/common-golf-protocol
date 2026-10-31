# Python reference applications

The scripts in `examples/` consume the installable
[Python reference package](../../implementations/python/README.md).
Run these commands from the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install ./implementations/python
.venv/bin/python examples/sim.py                    # terminal 1
.venv/bin/python examples/launch_monitor_minimum.py # terminal 2
```

The simulator discovers listeners through DNS-SD. Each connection uses a
persistent Ed25519 identity and independent channel state. Both ends default
to draft 2; use `--draft 1` on each end to exercise the older wire profile.

| Script | Behavior |
|---|---|
| `launch_monitor_minimum.py` | Three-number shot messages. |
| `launch_monitor_full.py` | Preliminary/final shots and status; trusted simulators also receive club and flight data. |
| `launch_monitor_open_connect.py` | Open Connect v1 JSON and replies over channel 2. |
| `swing_speed_radar.py` | Club speed without ball data. |
| `control_box.py` | Synthetic button presses. |
| `sim.py` | State publication, shot results, and Open Connect replies. |

Each script is self-contained application code. Framing, transport, identities,
and discovery live in the package. Measurements are synthetic. Use `--help` for
identity, trust-list, and run-duration options. Schema conformance is checked by
repository tests; applications use normal JSON field access.
