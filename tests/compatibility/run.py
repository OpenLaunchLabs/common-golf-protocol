#!/usr/bin/env python3
"""Real loopback QUIC matrix; executables use the diagnostic peer contract.

Run with the installed Python package, built Rust binary, and built C binary.
The harness is deliberately separate from all three implementations.
"""
import argparse
import asyncio
import itertools
import json
from pathlib import Path
import re
import shlex
import sys
import tempfile
import time

from schema_check import check_message

ROOT = Path(__file__).resolve().parents[2]
VERSIONS = {1: ['0.1.0', '0.1.1'], 2: ['0.2.0'], 3: ['0.3.0']}
FIXTURES = ROOT / 'tests/fixtures/conformance.json'


def records(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_bytes().split(b'\n')[:-1] if line.strip()]


def validate(messages, role, draft, version):
    identities = [r for r in messages if r.get('kind') == 'connected']
    assert identities and all(re.fullmatch('[0-9a-f]{64}', r.get('fingerprint') or '') for r in identities), 'Missing authenticated Ed25519 peer identity'
    channels = set()
    for record in messages:
        if 'message' not in record:
            continue
        channel, message = record['channel'], record['message']
        channels.add(channel)
        if channel < 9 or (draft == 3 and channel == 1003):
            check_message(draft, channel, message, contract='io.common-golf.open-connect-v1' if channel == 1003 else None)
        if channel == 0:
            assert message['protocol_version'] == version, message
        if channel in (1, 3):
            forbidden = 'estimated' if draft == 1 else 'reduced_accuracy'
            assert forbidden not in message, (draft, message)
            for field in message.get('estimated', []):
                assert field in message and isinstance(message[field], (int, float)), message
    expected = {0, 2, 6, 7} if role == 'listen' else {0, 1, 2, 3, 4, 5, 8, 1000}
    if draft == 3:
        expected.remove(2)
        expected.add(1003)
    assert expected <= channels, f'{role}: missing channels {expected - channels}; got {messages}'
    source_role = 'connect' if role == 'listen' else 'listen'
    for fixture in json.loads(FIXTURES.read_text())['drafts'][str(draft)][source_role]:
        assert any(r.get('channel') == fixture['channel'] and r.get('message') == fixture['message']
                   for r in messages), f'Changed/missing shared fixture: {fixture}'
    if draft == 3:
        assert 2 not in channels and 9 not in channels, 'Reserved channels carried application data'
        for kind, channel in [('raw', 1001), ('datagram', 1002)]:
            selected = [row for row in messages if row.get('kind') == kind and row.get('channel') == channel]
            assert any(row.get('payload_hex') == '00ff8000616263' for row in selected), f'{role}: missing {kind} fixture'
            if kind == 'datagram':
                assert all(type(row.get('sequence')) is int and 0 <= row['sequence'] <= 2**64 - 1 for row in selected)
        for row in messages:
            if row.get('channel') in (1, 3, 7, 8) and 'message' in row:
                assert not {'shot_number', 'swing_number', 'final'} & row['message'].keys()


async def stop(process):
    if process.returncode is None:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 3)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()


async def case(commands, listener, connector, ldraft, cdraft, lversion, cversion, defaults=False):
    label = f'{listener}({lversion}) <- {connector}({cversion})'
    if defaults:
        label += ' [default profiles]'
    with tempfile.TemporaryDirectory(prefix='cgp-matrix-') as temp:
        work = Path(temp)
        common = ['--host', '127.0.0.1', '--fixtures', str(FIXTURES)]
        lcmd = commands[listener] + common + ['--role', 'listen', '--port', '0',
            '--duration', '20', '--output', str(work/'listener.jsonl'), '--identity', str(work/'listener-id')]
        if not defaults:
            lcmd += ['--draft', str(ldraft), '--version', lversion]
        log = []
        listener_key = None
        peer = await asyncio.create_subprocess_exec(*lcmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        client = None
        drain = None
        async def ready():
            nonlocal listener_key
            while line := await peer.stdout.readline():
                text = line.decode(errors='replace').strip()
                log.append(text)
                if text.startswith('KEY '):
                    listener_key = text.split()[1]
                if text.startswith('READY '):
                    return int(text.split()[1])
            raise AssertionError('Listener exited before READY: ' + '\n'.join(log))
        try:
            port = await asyncio.wait_for(ready(), 15)
            drain = asyncio.create_task(peer.stdout.read())
            ccmd = commands[connector] + common + ['--role', 'connect', '--port', str(port),
                '--duration', '1', '--output', str(work/'connector.jsonl'), '--identity', str(work/'connector-id')]
            if not defaults:
                ccmd += ['--draft', str(cdraft), '--version', cversion]
            if cdraft == 3:
                assert listener_key and re.fullmatch('[0-9a-f]{64}', listener_key), 'Missing listener key'
                ccmd += ['--expected-key', listener_key]
            client = await asyncio.create_subprocess_exec(*ccmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            output, _ = await asyncio.wait_for(client.communicate(), 15)
            output = output.decode(errors='replace')
            if ldraft != cdraft:
                assert client.returncode != 0, 'Different ALPNs connected successfully'
                assert not [r for r in records(work/'connector.jsonl') if 'message' in r], 'Data crossed ALPN boundary'
                assert not [r for r in records(work/'listener.jsonl') if 'message' in r], 'Listener received data across ALPN boundary'
                reason = r'alpn|application protocol|certificate|raw.public.key|unsupported|0x178|\b120\b'
                if 3 in (ldraft, cdraft):
                    # TLS alert43 is unsupported_certificate: the selected profile
                    # can fail RPK/X509 negotiation before ALPN is reported.
                    reason += r'|cryptographic handshake failed: error 43'
                assert re.search(reason, output, re.I), output
            else:
                assert client.returncode == 0, output
                await asyncio.sleep(.1)
                validate(records(work/'listener.jsonl'), 'listen', ldraft, cversion)
                validate(records(work/'connector.jsonl'), 'connect', cdraft, lversion)
            assert peer.returncode is None, f'Listener exited unexpectedly: {peer.returncode}'
            return {'case': label, 'result': 'passed', 'mismatch': ldraft != cdraft}
        except Exception as error:
            return {'case': label, 'result': 'failed', 'error': str(error), 'listener_log': log,
                    'listener_records': records(work/'listener.jsonl'), 'connector_records': records(work/'connector.jsonl')}
        finally:
            if client:
                await stop(client)
            await stop(peer)
            if drain:
                remainder = (await drain).decode(errors='replace')
                if remainder:
                    log.extend(remainder.splitlines())


async def main(args):
    commands = {'python': shlex.split(args.python), 'rust': shlex.split(args.rust), 'c': shlex.split(args.c)}
    commands = {name: cmd for name, cmd in commands.items() if name in args.languages.split(',')}
    cases = []
    drafts = [int(d) for d in args.drafts.split(',')]
    assert drafts and all(draft in VERSIONS for draft in drafts), 'Unknown or empty draft selection'
    assert commands, 'Unknown or empty language selection'
    assert args.jobs > 0, '--jobs must be positive'
    for listener, connector in itertools.product(commands, repeat=2):
        for draft in drafts:
            # Includes both draft-1 patch versions in both directions.
            for lv, cv in itertools.product(VERSIONS[draft], repeat=2):
                cases.append((listener, connector, draft, draft, lv, cv))
        if 3 in drafts:
            # Omit both CLI version flags: explicit draft-3 runs cannot detect
            # peers accidentally retaining a legacy default.
            cases.append((listener, connector, 3, 3, '0.3.0', '0.3.0', True))
        if not args.no_mismatch:
            for ld, cd in itertools.permutations(drafts, 2):
                cases.append((listener, connector, ld, cd, VERSIONS[ld][-1], VERSIONS[cd][-1]))
    semaphore = asyncio.Semaphore(args.jobs)
    async def run(values):
        async with semaphore:
            result = await case(commands, *values)
            print(f"{result['result'].upper():6} {result['case']}" + (f": {result['error']}" if result['result'] == 'failed' else ''), flush=True)
            return result
    start = time.monotonic()
    results = await asyncio.gather(*(run(values) for values in cases))
    report = {'seconds': round(time.monotonic()-start, 2), 'cases': results,
              'passed': sum(r['result'] == 'passed' for r in results), 'total': len(results)}
    destination = Path(args.report)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + '\n')
    print(f"{report['passed']}/{report['total']} passed; report: {destination}")
    return 0 if report['passed'] == report['total'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', default=f'{shlex.quote(sys.executable)} -m common_golf')
    parser.add_argument('--rust', default=str(ROOT/'implementations/rust/target/debug/cgp-peer'))
    parser.add_argument('--c', default=str(ROOT/'implementations/c/build/cgp-peer'))
    parser.add_argument('--languages', default='python,rust,c')
    parser.add_argument('--drafts', default='1,2,3')
    parser.add_argument('--jobs', type=int, default=3)
    parser.add_argument('--no-mismatch', action='store_true')
    parser.add_argument('--report', default=str(ROOT/'test-results/compatibility.json'))
    sys.exit(asyncio.run(main(parser.parse_args())))
