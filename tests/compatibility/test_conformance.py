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


UUID = '019a0000-0000-7000-8000-000000000001'


def draft3_shot(**fields):
    return {'type': 'shot', 'shot_uuid': UUID, 'ball_speed_meters_per_second': 2.1,
            'vertical_launch_angle_degrees': 0, 'horizontal_launch_angle_degrees': .4,
            **fields}


@pytest.mark.parametrize('channel', [1, 3, 7, 8])
@pytest.mark.parametrize('identity', [UUID.upper(), UUID.replace('-7000-', '-4000-'),
                                    UUID.replace('-8000-', '-c000-'), UUID + '\n', 1, None])
def test_draft3_invalid_shot_identity(channel, identity):
    message = draft3_shot(shot_uuid=identity) if channel == 1 else {
        'type': {3: 'club', 7: 'result', 8: 'flight'}[channel], 'shot_uuid': identity}
    with pytest.raises(ValidationError):
        check_message(3, channel, message)


def test_draft3_identity_and_measurement_time_are_independent():
    check_message(3, 1, draft3_shot(measured_at='2022-02-22T19:22:21.950Z'))
    check_message(3, 3, {'type': 'club', 'club_speed_meters_per_second': 42})
    with pytest.raises(ValidationError):
        check_message(3, 1, {key: value for key, value in draft3_shot().items() if key != 'shot_uuid'})
    with pytest.raises(ValidationError):
        check_message(3, 1, draft3_shot(measured_at='2022-02-22T19:22:21-05:00'))


@pytest.mark.parametrize('menu', [
    [{'channel': 2, 'contract': 'com.example.a'}],
    [{'channel': 4294967296, 'contract': 'com.example.a'}],
    [{'channel': 1000.0, 'contract': 'com.example.a'}],
    [{'channel': 1000, 'contract': 'com.example.a'}] * 2,
    [{'channel': 1000, 'contract': name} for name in ['com.example.a', 'com.example.b']],
    [{'channel': 1000 + i, 'contract': 'com.example.a'} for i in range(17)],
])
def test_draft3_invalid_menu(menu):
    with pytest.raises(ValidationError):
        check_message(3, 0, {'type': 'info', 'protocol_version': '0.3.0', 'contracts': menu})


@pytest.mark.parametrize('identifier', ['', 'A', 'com.example.a\n', 'com.example.é', 'a' * 129])
def test_draft3_invalid_contract_identifier(identifier):
    with pytest.raises(ValidationError):
        check_message(3, 0, {'type': 'info', 'protocol_version': '0.3.0',
                             'contracts': [{'channel': 1000, 'contract': identifier}]})


def test_draft3_menu_reuses_identifiers_but_not_channel_numbers():
    check_message(3, 0, {'type': 'info', 'protocol_version': '0.3.0',
        'contracts': [{'channel': 1000 + i, 'contract': 'com.example.a', 'future_field': True} for i in range(16)],
        'reads': [0, 1, 3], 'reads_contracts': ['com.example.a']})
    with pytest.raises(ValueError):
        check_message(3, 2, {'Code': 200, 'Message': 'not a standard channel'})
    check_message(3, 1003, {'Code': 200, 'Message': 'custom reply'}, contract='io.common-golf.open-connect-v1')


@pytest.mark.parametrize('channel,message', [
    (4, {'type': 'device_status', 'capture_mode': 'putting', 'ball_position': {'x_meters': 0, 'y_meters': .1}}),
    (4, {'type': 'device_status', 'capture_mode': None, 'ball_position': None}),
    (6, {'type': 'simulator_state', 'requested_capture_mode': 'short', 'distance_to_target_meters': 3.048}),
    (6, {'type': 'simulator_state', 'requested_capture_mode': None, 'player': None}),
])
def test_draft3_state_context(channel, message):
    check_message(3, channel, message)


@pytest.mark.parametrize('channel,message', [
    (4, {'type': 'status'}), (6, {'type': 'state'}),
    (4, {'type': 'device_status', 'capture_mode': 'course'}),
    (4, {'type': 'device_status', 'ball_position': {'x_meters': 0}}),
    (6, {'type': 'simulator_state', 'distance_to_target_meters': -1}),
])
def test_draft3_invalid_state_context(channel, message):
    with pytest.raises(ValidationError):
        check_message(3, channel, message)


@pytest.mark.parametrize('lie', ['tee', 'fairway', 'putting_green', 'sand', 'rough'])
def test_draft3_hitting_area_lie(lie):
    check_message(3, 1, draft3_shot(ball_lie=lie))


def test_proposal_json_examples_match_working_schemas():
    import json
    import re
    from jsonschema import FormatChecker
    for filename in ['rfc/0001-common-golf-protocol.md', 'implementations/DRAFT3.md']:
        text = (schema_check.ROOT / filename).read_text()
        for block in re.findall(r'```json\n(.*?)\n```', text, re.S):
            message = json.loads(block)
            name = message.get('type', 'open_connect')
            schema = json.loads((schema_check.ROOT / 'schema' / (name + '.json')).read_text())
            schema_check.Draft202012Validator(schema, format_checker=FormatChecker()).validate(message)
