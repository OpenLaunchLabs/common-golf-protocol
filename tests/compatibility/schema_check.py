"""Test-only sender conformance checks against the canonical repository schemas."""
import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).resolve().parents[2]
NAMES = ['info', 'shot', 'open_connect', 'club', 'status', 'control', 'state', 'result', 'flight']


@lru_cache(maxsize=18)
def validator(draft, channel):
    if draft not in (1, 2):
        raise ValueError(f'Unsupported draft: {draft}')
    if not 0 <= channel < len(NAMES):
        raise ValueError(f'No standard schema for channel: {channel}')
    path = ROOT / 'schema' / f'draft-{draft}' / (NAMES[channel] + '.json')
    return Draft202012Validator(json.loads(path.read_text()))


def check_message(draft, channel, message):
    if draft not in (1, 2):
        raise ValueError(f'Unsupported draft: {draft}')
    if channel >= 1000:
        return
    validator(draft, channel).validate(message)
    if channel in (1, 3):
        if draft == 1 and 'estimated' in message:
            raise ValidationError('estimated requires draft 2')
        if draft == 2 and channel == 1 and 'reduced_accuracy' in message:
            raise ValidationError('draft 2 removed reduced_accuracy')
        for name in message.get('estimated', []):
            value = message.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValidationError('estimated must name present numeric fields')
    if channel == 1 and message.get('total_spin_revolutions_per_minute') == 0:
        if message.get('spin_axis_degrees') != 0:
            raise ValidationError('zero total spin requires zero spin axis')
