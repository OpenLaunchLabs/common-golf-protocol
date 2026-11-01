# Python examples

The scripts in `examples/` demonstrate launch monitors, a swing-speed radar,
a control box, and a simulator using the
[Python package](../../implementations/python/README.md). All measurements
and button presses are synthetic.

## Run

Install the package from the repository root, then run the simulator and a
device in separate terminals:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install ./implementations/python
.venv/bin/python examples/sim.py                    # terminal 1
.venv/bin/python examples/launch_monitor_minimum.py # terminal 2
```

The simulator discovers devices through DNS-SD. All scripts default to
draft 3. To use draft 1 or 2, pass `--draft 1` or `--draft 2` to both the
simulator and the device.

## Scripts

| Script | Behavior |
|---|---|
| `launch_monitor_minimum.py` | Shots containing ball speed, launch angle, and launch direction. |
| `launch_monitor_full.py` | Device status and updated shot snapshots; trusted simulators also receive club and flight data. |
| `launch_monitor_open_connect.py` | Open Connect JSON on custom channel 1003, or channel 2 in drafts 1 and 2. |
| `swing_speed_radar.py` | Club speed without ball data. |
| `control_box.py` | Synthetic button presses. |
| `sim.py` | Publishes simulator state, returns shot results, and responds to Open Connect requests. |

Run any script with `--help` for its options, including identity, trust list,
and duration.
