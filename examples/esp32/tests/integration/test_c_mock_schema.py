"""Validate JSON from the C firmware mock, launched as a native subprocess.

Python reads mock_samples stdout and checks schemas; no C FFI is involved.
"""
import json
from pathlib import Path
import subprocess

import jsonschema
import pytest


@pytest.mark.parametrize("draft", [None, 1, 2, 3])
def test_c_mock_messages_match_schemas(mock_samples, draft):
    arguments = [str(mock_samples)] + ([] if draft is None else [str(draft)])
    draft = 3 if draft is None else draft
    schemas = Path(__file__).resolve().parents[4] / "schema"
    if draft < 3:
        schemas /= f"draft-{draft}"
    names = {0: "info", 1: "shot", 3: "club", 4: "status", 5: "control",
             6: "state", 7: "result", 8: "flight"}
    if draft == 3:
        names.update({4: "device_status", 6: "simulator_state"})
    validators = {
        channel: jsonschema.Draft202012Validator(json.loads((schemas / f"{name}.json").read_text()))
        for channel, name in names.items()
    }
    result = subprocess.run(arguments, check=True, capture_output=True, text=True, timeout=30)
    counts = {channel: 0 for channel in names}
    commands = set()
    for line in result.stdout.splitlines():
        channel_text, payload = line.split("\t", 1)
        channel = int(channel_text)
        message = json.loads(payload)
        validators[channel].validate(message)
        counts[channel] += 1
        if draft >= 2 and channel in (1, 3):
            synthetic_numbers = {key for key, value in message.items()
                                 if type(value) in (int, float) and key != "shot_number"}
            assert set(message["estimated"]) == synthetic_numbers
        if channel == 5:
            commands.add(message["command"])
    expected_commands = set("left right up down reset_aim club_up club_down putter_toggle "
                            "mulligan rehit drop tee_left tee_right switch_hand flyover shot_cam "
                            "scorecard heatmap map green_grid pin hide_ui select back".split())
    assert commands == expected_commands, (commands, expected_commands)
    assert all(counts.values()), counts
    print(f"Mock messages: {sum(counts.values())} schema-valid messages across {len(counts)} channels; "
          f"all {len(commands)} control commands exercised")
