#!/usr/bin/env python3
"""Exercise stream isolation and boundary cases against every real QUIC server.

The Python package is the test driver; each target is a separate process using
its own transport implementation. No target source is imported or mocked.
"""
import argparse
import asyncio
from contextlib import AsyncExitStack
from functools import partial
import json
from pathlib import Path
import shlex
import struct
import sys
import tempfile
import time

from aioquic.asyncio import connect
from common_golf import Connection, Draft, Identity, frame
from common_golf.contracts import Contract, OPEN_CONNECT

from run import ROOT, stop

EXPECTED = {
    'baseline_channels', 'open_connect_reply_without_prefix', 'reserved_stop_1',
    'wrong_owner_stop_1', 'duplicate_stop_1', 'oversize_stop_2', 'zero_stop_2',
    'nonobject_stop_2', 'invalid_json_stop_2', 'nonfinite_stop_2', 'truncated_fin_discard_and_reopen',
    'maximum_65535_and_fragmented_header', 'multiple_independent_clients',
    'connection_survives_stream_errors',
}


def records(path):
    # The server may be halfway through a 65 KiB log write. Only newline-
    # terminated records are committed; never parse its in-progress final line.
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_bytes().split(b"\n")[:-1] if line.strip()]


class Probe(Connection):
    def __init__(self, *args, **kwargs):
        if int(kwargs.get('draft', 3)) == 3:
            contracts = [Contract('io.common-golf.interop-json-v1'), OPEN_CONNECT]
            options = {'contracts': {contract.identifier: contract for contract in contracts}}
        else:
            options = {'private_channels': {1000: 65535}}
        super().__init__(*args, role='sim', **options, **kwargs)
        self.received = []
        self.stops = {}
        self.terminated = None

    def on_message(self, channel, message, stream_id):
        self.received.append((channel, message))
        if channel in (2, 1003) and 'Code' not in message:
            # Reply on the same bidirectional stream: send() adds a length only.
            self.send(stream_id, {'Code': 200, 'Message': 'adversarial reply'})

    def on_declined(self, channel, stream_id, code):
        self.stops[stream_id] = code

    def on_disconnected(self, code, reason):
        self.terminated = (code, reason)

    def inject(self, channel, body, *, fin=False):
        sid = self._quic.get_next_available_stream_id(is_unidirectional=True)
        self._quic.send_stream_data(sid, struct.pack('>I', channel) + body, end_stream=fin)
        self.transmit()
        return sid


async def until(predicate, description, timeout=4):
    async def wait():
        while not predicate():
            await asyncio.sleep(.01)
    try:
        await asyncio.wait_for(wait(), timeout)
    except asyncio.TimeoutError as error:
        raise AssertionError('Timed out: ' + description) from error


def marked(path, marker):
    return [r for r in records(path) if r.get('message', {}).get('probe') == marker]


async def exercise(port, draft, directory, server_output, expected_key=None):
    checks = set()
    factory = partial(Probe, draft=draft)
    open_connect = 1003 if draft == 3 else 2
    state_type = 'simulator_state' if draft == 3 else 'state'
    shot_identity = {'shot_uuid': '019a0000-0000-7000-8000-000000000001'} if draft == 3 else {'shot_number': 1}
    first_id = Identity(directory / 'probe-first')
    second_id = Identity(directory / 'probe-second')
    async with AsyncExitStack() as stack:
        first = await stack.enter_async_context(connect(
            '127.0.0.1', port, configuration=first_id.configuration(is_client=True, draft=draft, expected_key=expected_key if draft == 3 else None),
            create_protocol=factory))
        await until(lambda: {0, 1, open_connect, 3, 4, 5, 8, 1000} <= {ch for ch, _ in first.received},
                    'server baseline channels')
        checks.add('baseline_channels')
        await until(lambda: any(r.get('channel') == open_connect and r.get('message', {}).get('Message') == 'adversarial reply'
                               for r in records(server_output)), 'Open Connect reply decoded by server')
        checks.add('open_connect_reply_without_prefix')

        # Keep one valid simulator-owned channel open for duplicate and survival checks.
        state_sid = first.publish(6, {'type': state_type, 'ready': True, 'probe': 'initial-state'})
        await until(lambda: marked(server_output, 'initial-state'), 'initial state')
        for label, channel, body, expected in [
            ('reserved_stop_1', 999, frame({}), 1),
            ('wrong_owner_stop_1', 1, frame({'type': 'shot'}), 1),
            ('duplicate_stop_1', 6, frame({'type': state_type}), 1),
            ('oversize_stop_2', 7, struct.pack('>I', 65536), 2),
            ('zero_stop_2', 7, struct.pack('>I', 0), 2),
            ('nonobject_stop_2', 7, struct.pack('>I', 2) + b'[]', 2),
            ('invalid_json_stop_2', 7, struct.pack('>I', 2) + b'{!', 2),
            ('nonfinite_stop_2', 7, struct.pack('>I', 11) + b'{"n":1e999}', 2),
        ]:
            sid = first.inject(channel, body)
            await until(lambda: sid in first.stops, f'{label}: STOP_SENDING for stream {sid}')
            assert first.stops[sid] == expected, (label, first.stops[sid], expected)
            assert first.terminated is None, (label, first.terminated)
            checks.add(label)

        # FIN inside a body must discard that stream. FIN has already closed its
        # receiving half, so no STOP_SENDING is required. A valid new stream for
        # the same channel demonstrates that the reservation was released.
        truncated_sid = first.inject(7, struct.pack('>I', 3) + b'{}', fin=True)
        await asyncio.sleep(.1)
        assert not any(r.get('channel') == 7 and 'message' in r for r in records(server_output)), \
            'Malformed result frames reached the server application'
        result_sid = first.publish(7, {'type': 'result', **shot_identity, 'accepted': True,
                                       'probe': 'after-truncated-fin'})
        await until(lambda: marked(server_output, 'after-truncated-fin'), 'reopen after truncated FIN')
        assert result_sid != truncated_sid
        assert result_sid not in first.stops
        checks.add('truncated_fin_discard_and_reopen')

        # Exactly 65535 UTF-8 bytes, with an unknown extension field. Write the
        # frame header in separate sends, then let QUIC fragment the large body.
        maximum = {'type': state_type, 'ready': True, 'probe': 'maximum-state', 'padding': ''}
        maximum['padding'] = 'x' * (65535 - (len(frame(maximum)) - 4))
        encoded = frame(maximum)
        assert len(encoded) == 65539
        for part in (encoded[:1], encoded[1:3], encoded[3:4], encoded[4:]):
            first._quic.send_stream_data(state_sid, part)
            first.transmit()
            await asyncio.sleep(.01)
        await until(lambda: marked(server_output, 'maximum-state'), '65535-byte state object')
        got = marked(server_output, 'maximum-state')[-1]['message']
        assert got == maximum, 'Maximum-size message changed or lost its unknown field'
        assert state_sid not in first.stops
        checks.add('maximum_65535_and_fragmented_header')

        # A second connection can use the same channel number while the first
        # continues publishing. It has a distinct certificate and state machine.
        second = await stack.enter_async_context(connect(
            '127.0.0.1', port, configuration=second_id.configuration(is_client=True, draft=draft, expected_key=expected_key if draft == 3 else None),
            create_protocol=factory))
        await until(lambda: {0, 1, open_connect} <= {ch for ch, _ in second.received}, 'second connection baseline')
        second.publish(6, {'type': state_type, 'ready': False, 'probe': 'second-client'})
        first.send(state_sid, {'type': state_type, 'ready': True, 'probe': 'first-client-still-active'})
        await until(lambda: marked(server_output, 'second-client') and marked(server_output, 'first-client-still-active'),
                    'independent simultaneous state streams')
        assert not second.stops, second.stops
        assert first.peer_fingerprint and first.peer_fingerprint == second.peer_fingerprint
        assert first_id.fingerprint != second_id.fingerprint
        checks.add('multiple_independent_clients')
        assert first.terminated is None and second.terminated is None, (first.terminated, second.terminated)
        checks.add('connection_survives_stream_errors')
    assert checks == EXPECTED, ('missing checks', sorted(EXPECTED - checks))
    return sorted(checks)


async def case(command, language, draft):
    label = f'{language} draft-{draft}'
    with tempfile.TemporaryDirectory(prefix='cgp-adversarial-') as temporary:
        directory = Path(temporary)
        output = directory / 'server.jsonl'
        args = command + ['--role', 'listen', '--host', '127.0.0.1', '--port', '0', '--draft', str(draft),
                          '--version', Draft(draft).version, '--duration', '45', '--output', str(output),
                          '--identity', str(directory / 'server-identity')]
        process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                      stderr=asyncio.subprocess.STDOUT)
        logs, drain = [], None
        server_key = None
        async def ready():
            nonlocal server_key
            while line := await process.stdout.readline():
                text = line.decode(errors='replace').strip()
                logs.append(text)
                if text.startswith('KEY '):
                    server_key = text.split()[1]
                if text.startswith('READY '):
                    return int(text.split()[1])
            raise AssertionError('Server exited before READY: ' + '\n'.join(logs))
        try:
            port = await asyncio.wait_for(ready(), 15)
            drain = asyncio.create_task(process.stdout.read())
            checks = await asyncio.wait_for(exercise(port, draft, directory, output, server_key), 35)
            assert process.returncode is None, f'Server exited unexpectedly: {process.returncode}'
            return {'case': label, 'result': 'passed', 'checks': checks}
        except Exception as error:
            return {'case': label, 'result': 'failed', 'error': f'{type(error).__name__}: {error}',
                    'server_log': logs}
        finally:
            await stop(process)
            if drain:
                remainder = (await drain).decode(errors='replace')
                logs.extend(remainder.splitlines()[-20:])


async def main(args):
    commands = {'python': shlex.split(args.python), 'rust': shlex.split(args.rust), 'c': shlex.split(args.c)}
    selected = args.languages.split(',')
    assert selected and all(name in commands for name in selected), 'Unknown or empty language selection'
    drafts = [int(value) for value in args.drafts.split(',')]
    assert drafts and all(draft in (1, 2, 3) for draft in drafts), 'Unknown or empty draft selection'
    assert args.jobs > 0, '--jobs must be positive'
    semaphore = asyncio.Semaphore(args.jobs)
    async def run(language, draft):
        async with semaphore:
            result = await case(commands[language], language, draft)
            print(f"{result['result'].upper():6} {result['case']}" +
                  (f": {result['error']}" if result['result'] == 'failed' else ''), flush=True)
            return result
    start = time.monotonic()
    results = await asyncio.gather(*(run(language, draft) for language in selected for draft in drafts))
    report = {'seconds': round(time.monotonic() - start, 2), 'cases': results,
              'checks_per_case': len(EXPECTED), 'passed': sum(r['result'] == 'passed' for r in results),
              'total': len(results)}
    destination = Path(args.report)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + '\n')
    print(f"{report['passed']}/{report['total']} passed; report: {destination}")
    return 0 if report['passed'] == report['total'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', default=f'{shlex.quote(sys.executable)} -m common_golf')
    parser.add_argument('--rust', default=str(ROOT / 'implementations/rust/target/debug/cgp-peer'))
    parser.add_argument('--c', default=str(ROOT / 'implementations/c/build/cgp-peer'))
    parser.add_argument('--languages', default='python,rust,c')
    parser.add_argument('--drafts', default='1,2,3')
    parser.add_argument('--jobs', type=int, default=3)
    parser.add_argument('--report', default=str(ROOT / 'test-results/adversarial.json'))
    sys.exit(asyncio.run(main(parser.parse_args())))
