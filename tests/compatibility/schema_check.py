"""Test-only sender conformance checks against the repository schemas."""
import json
import re
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

ROOT = Path(__file__).resolve().parents[2]
NAMES = ['info', 'shot', 'open_connect', 'club', 'status', 'control', 'state', 'result', 'flight']
NAMES_THREE = ['info', 'shot', None, 'club', 'device_status', 'control', 'simulator_state', 'result', 'flight']
OPEN_CONNECT = 'io.common-golf.open-connect-v1'


@lru_cache(maxsize=28)
def validator(draft, channel):
    if draft not in (1, 2, 3):
        raise ValueError(f'Unsupported draft: {draft}')
    if draft == 3 and channel == OPEN_CONNECT:
        name = 'open_connect'
    else:
        names = NAMES_THREE if draft == 3 else NAMES
        if type(channel) is not int or not 0 <= channel < len(names) or names[channel] is None:
            raise ValueError(f'No standard schema for channel: {channel}')
        name = names[channel]
    directory = ROOT / 'schema' if draft == 3 else ROOT / 'schema' / f'draft-{draft}'
    return Draft202012Validator(json.loads((directory / (name + '.json')).read_text()),
                               format_checker=FormatChecker() if draft == 3 else None)


def check_message(draft, channel, message, *, contract=None):
    if draft not in (1, 2, 3):
        raise ValueError(f'Unsupported draft: {draft}')
    if channel >= 1000:
        if draft == 3 and contract == OPEN_CONNECT:
            validator(draft, OPEN_CONNECT).validate(message)
        return
    validator(draft, channel).validate(message)
    if draft == 3 and channel == 0:
        seen = set()
        for entry in message.get('contracts', []):
            value = entry['channel']
            if type(value) is not int or value in seen:
                raise ValidationError('menu channels must be unique decimal integer literals')
            seen.add(value)
    if draft == 3 and channel == 1 and 'measured_at' in message:
        if not re.search(r'(?:[Zz]|\+00:00)$', message['measured_at']):
            raise ValidationError('measured_at must be a UTC timestamp')
    if channel in (1, 3):
        if draft == 1 and 'estimated' in message:
            raise ValidationError('estimated requires draft 2 or later')
        if draft >= 2 and channel == 1 and 'reduced_accuracy' in message:
            raise ValidationError('reduced_accuracy was removed after draft 1')
        for name in message.get('estimated', []):
            value = message.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValidationError('estimated must name present numeric fields')
    if channel == 1 and message.get('total_spin_revolutions_per_minute') == 0:
        if message.get('spin_axis_degrees') != 0:
            raise ValidationError('zero total spin requires zero spin axis')
