import pytest
from jsonschema import ValidationError
from schema_check import check_message, validator
import schema_check

CASES = [(4, {'type': 'status', 'battery_percent': None, 'future_field': {'anything': True}}, True),
 (4, {'type': 'status', 'battery_percent': 101}, False),
 (4, {'type': 'status', 'ready': 1}, False),
 (6, {'type': 'state', 'player': None, 'mode': None}, True),
 (6, {'type': 'state', 'player': {'dexterity': 'both'}}, False),
 (6, {'type': 'state', 'ball_zone': {'x_meters': 0}}, False),
 (8, {'type': 'flight', 'shot_number': 1, 'landing_angle_degrees': 91}, False),
 (7, {'type': 'result', 'shot_number': 1.5}, False),
 (5, {'type': 'control', 'command': 'future-command'}, True),
 (5, {'type': 'control', 'command': 'left', 'event': 'bad'}, False),
 (3, {'type': 'club', 'estimated': ['smash_factor', 'smash_factor'], 'smash_factor': 1}, False),
 (3, {'type': 'club', 'estimated': ['smash_factor']}, False),
 (0, {'type': 'info', 'protocol_version': '1.2'}, False),
 (0, {'type': 'info', 'protocol_version': '99.42.123'}, True),
 (2, {'Code': 200, 'Message': 'Shot received'}, True),
 (2, {'Code': 200}, False),
 (1,
  {'type': 'shot',
   'shot_number': 1,
   'ball_speed_meters_per_second': 60,
   'vertical_launch_angle_degrees': 12,
   'horizontal_launch_angle_degrees': 0,
   'total_spin_revolutions_per_minute': 2000},
  False)]

@pytest.mark.parametrize("channel,message,valid", CASES)
def test_conformance(channel, message, valid):
    if valid:
        check_message(2, channel, message)
    else:
        with pytest.raises(ValidationError):
            check_message(2, channel, message)

@pytest.mark.parametrize("draft,channel,message", [
    (1, 3, {"type": "club", "estimated": []}),
    (2, 3, {"type": "club", "estimated": ["missing"]}),
    (2, 3, {"type": "club", "estimated": ["final"], "final": True}),
])
def test_sender_metadata(draft, channel, message):
    with pytest.raises(ValidationError):
        check_message(draft, channel, message)


@pytest.mark.parametrize("draft,metadata", [
    (1, {"estimated": []}), (2, {"reduced_accuracy": True}),
    (2, {"estimated": ["missing"]}),
])
def test_shot_metadata_per_draft(draft, metadata):
    identity = {"shot_number": 1}
    message = {"type": "shot", **identity, "ball_speed_meters_per_second": 50,
               "vertical_launch_angle_degrees": 12, "horizontal_launch_angle_degrees": 0,
               **metadata}
    with pytest.raises(ValidationError):
        check_message(draft, 1, message)


@pytest.mark.parametrize('draft', [1, 2])
def test_schema_snapshot_does_not_follow_working_schemas(tmp_path, monkeypatch, draft):
    """Published-draft validation must survive edits to the working schemas."""
    schema_dir = tmp_path / 'schema'
    snapshot = schema_dir / f'draft-{draft}'
    snapshot.mkdir(parents=True)
    (snapshot / 'shot.json').write_bytes(
        (schema_check.ROOT / 'schema' / f'draft-{draft}' / 'shot.json').read_bytes())
    (schema_dir / 'shot.json').write_text('false')
    monkeypatch.setattr(schema_check, 'ROOT', tmp_path)
    validator.cache_clear()
    try:
        shot = {'type': 'shot', 'shot_number': 1, 'ball_speed_meters_per_second': 50,
                'vertical_launch_angle_degrees': 12, 'horizontal_launch_angle_degrees': 0}
        check_message(draft, 1, shot)
        with pytest.raises(ValidationError):
            check_message(draft, 1, {**shot, 'shot_number': 0})
    finally:
        validator.cache_clear()


@pytest.mark.parametrize('draft', [1, 2])
@pytest.mark.parametrize('channel', range(9))
def test_published_schema_is_valid(draft, channel):
    selected = validator(draft, channel)
    selected.check_schema(selected.schema)


def test_unsupported_draft_is_not_silently_validated_as_latest():
    with pytest.raises(ValueError, match='Unsupported draft'):
        check_message(99, 0, {'type': 'info', 'protocol_version': '99.0.0'})
