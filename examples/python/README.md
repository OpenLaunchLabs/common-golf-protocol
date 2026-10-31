# Python examples

The scripts in `examples/` use the
[Python package](../../implementations/python/README.md). From the repository
root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install ./implementations/python
.venv/bin/python examples/sim.py                    # terminal 1
.venv/bin/python examples/launch_monitor_minimum.py # terminal 2
```

The simulator finds devices with DNS-SD. Everything uses draft 2 by default;
pass `--draft 1` to both ends for draft 1.

| Script | Behavior |
|---|---|
| `launch_monitor_minimum.py` | Three-number shots. |
| `launch_monitor_full.py` | Status and preliminary/final shots; trusted simulators also get club and flight data. |
| `launch_monitor_open_connect.py` | Open Connect v1 JSON on channel 2. |
| `swing_speed_radar.py` | Club speed without ball data. |
| `control_box.py` | Synthetic button presses. |
| `sim.py` | Publishes state, returns shot results, and answers Open Connect. |

Measurements are synthetic. Run a script with `--help` for its options,
including identity, trust list, and duration.
