#!/usr/bin/env python3
"""Cross-language DNS-SD discovery and draft-3 advertised-key checks.

Diagnostic browser records contain name/port only. A separate TXT resolver
checks each draft-3 advertisement against the listener's actual identity key.
"""
import argparse
import asyncio
import json
from pathlib import Path
import re
import sys
import tempfile
import uuid

from run import ROOT, records, stop
from zeroconf import IPVersion
from zeroconf.asyncio import AsyncServiceInfo, AsyncZeroconf


SERVICE = '_common-golf._udp.local.'


async def main(args):
    commands = {'python': [sys.executable, '-m', 'common_golf'],
                'rust': [str(ROOT/'implementations/rust/target/debug/cgp-peer')],
                'c': [str(ROOT/'implementations/c/build/cgp-peer')]}
    processes, readers, advertised = [], [], {}
    results = []
    with tempfile.TemporaryDirectory(prefix='cgp-discovery-') as temp:
        root = Path(temp)
        try:
            for language, command in commands.items():
                name = f'cgp-test-{language}-{uuid.uuid4().hex[:8]}'
                process = await asyncio.create_subprocess_exec(*command, '--role', 'listen',
                    '--draft', str(args.draft), '--duration', '40', '--advertise', name, '--port', '0',
                    '--output', str(root/f'{language}-listener.jsonl'), '--identity', str(root/language),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
                processes.append(process)
                async def ready():
                    log = []
                    key = None
                    while line := await process.stdout.readline():
                        text = line.decode().strip()
                        log.append(text)
                        if text.startswith('KEY '):
                            key = text.split()[1]
                        if text.startswith('READY '):
                            return int(text.split()[1]), key
                    raise RuntimeError('\n'.join(log))
                port, key = await asyncio.wait_for(ready(), 15)
                advertised[language] = (name, port, key)
                readers.append(asyncio.create_task(process.stdout.read()))
            await asyncio.sleep(1)
            async def check_txt():
                # Resolve independently of the implementations under test: the
                # diagnostics intentionally omit TXT properties from their logs.
                zc = AsyncZeroconf(ip_version=IPVersion.V4Only)
                try:
                    for advertiser, (name, port, key) in advertised.items():
                        result = {'draft': args.draft, 'check': 'txt-key',
                                  'advertiser': advertiser, 'observer': 'zeroconf', 'result': 'passed'}
                        try:
                            assert isinstance(key, str) and re.fullmatch('[0-9a-f]{64}', key), 'Listener did not print a valid identity key'
                            info = AsyncServiceInfo(SERVICE, f'{name}.{SERVICE}')
                            assert await info.async_request(zc.zeroconf, 5000), 'Could not resolve TXT record'
                            assert info.port == port, 'Resolved port differs from listener port'
                            txt_key = info.properties.get(b'key')
                            assert isinstance(txt_key, bytes) and re.fullmatch(b'[0-9a-f]{64}', txt_key), 'TXT key is absent or malformed'
                            assert txt_key.decode('ascii') == key, 'TXT key differs from listener identity'
                            # ALPN is optional discovery guidance, not negotiation.
                            alpn = info.properties.get(b'alpn')
                            if alpn is not None:
                                assert b'draft-3' in alpn.split(b','), 'TXT ALPN hint omits draft 3'
                        except (AssertionError, ValueError) as error:
                            result.update(result='failed', error=str(error))
                        print(f"{result['result'].upper()} draft-{args.draft} {advertiser} TXT key", flush=True)
                        results.append(result)
                finally:
                    await zc.async_close()
            async def browse(language, command):
                path = root/f'{language}-discovered.jsonl'
                process = await asyncio.create_subprocess_exec(*command, '--role', 'discover',
                    '--draft', str(args.draft), '--duration', '5', '--output', str(path),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
                processes.append(process)
                output, _ = await asyncio.wait_for(process.communicate(), 15)
                if process.returncode:
                    raise RuntimeError(f'{language} browser failed: {output.decode()}')
                discovered = records(path)
                for advertiser, (name, port, _) in advertised.items():
                    found = any(r.get('name', '').split('._common-golf')[0] == name and r.get('port') == port for r in discovered)
                    result = {'draft': args.draft, 'check': 'browse', 'advertiser': advertiser, 'browser': language,
                              'result': 'passed' if found else 'failed'}
                    if not found:
                        result['records'] = discovered
                    print(f"{result['result'].upper()} draft-{args.draft} {advertiser} -> {language}", flush=True)
                    results.append(result)
            checks = [browse(language, command) for language, command in commands.items()]
            if args.draft == 3:
                checks.append(check_txt())
            await asyncio.gather(*checks)
        finally:
            for process in processes:
                await stop(process)
            await asyncio.gather(*readers)
    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(results, indent=2)+'\n')
    print(f"{sum(r['result'] == 'passed' for r in results)}/{len(results)} passed; report: {report}")
    return 0 if all(r['result'] == 'passed' for r in results) else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--draft', type=int, choices=(1, 2, 3), default=3,
                        help='advertised listener profile; draft 3 requires a matching TXT key')
    parser.add_argument('--report', default=str(ROOT/'test-results/discovery.json'))
    sys.exit(asyncio.run(main(parser.parse_args())))
