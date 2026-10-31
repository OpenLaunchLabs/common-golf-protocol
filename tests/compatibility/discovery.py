#!/usr/bin/env python3
"""Advertise with each implementation, discover all three with each browser."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import uuid

from run import ROOT, records, stop


async def main():
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
                    '--draft', '2', '--duration', '40', '--advertise', name, '--port', '0',
                    '--output', str(root/f'{language}-listener.jsonl'), '--identity', str(root/language),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
                processes.append(process)
                async def ready():
                    log = []
                    while line := await process.stdout.readline():
                        text = line.decode().strip()
                        log.append(text)
                        if text.startswith('READY '):
                            return int(text.split()[1])
                    raise RuntimeError('\n'.join(log))
                advertised[language] = (name, await asyncio.wait_for(ready(), 15))
                readers.append(asyncio.create_task(process.stdout.read()))
            await asyncio.sleep(1)
            async def browse(language, command):
                path = root/f'{language}-discovered.jsonl'
                process = await asyncio.create_subprocess_exec(*command, '--role', 'discover',
                    '--duration', '5', '--output', str(path),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
                processes.append(process)
                output, _ = await asyncio.wait_for(process.communicate(), 15)
                if process.returncode:
                    raise RuntimeError(f'{language} browser failed: {output.decode()}')
                discovered = records(path)
                for advertiser, (name, port) in advertised.items():
                    found = any(r.get('name', '').split('._common-golf')[0] == name and r.get('port') == port for r in discovered)
                    result = {'advertiser': advertiser, 'browser': language,
                              'result': 'passed' if found else 'failed'}
                    if not found:
                        result['records'] = discovered
                    print(f"{result['result'].upper()} {advertiser} -> {language}", flush=True)
                    results.append(result)
            await asyncio.gather(*(browse(language, command) for language, command in commands.items()))
        finally:
            for process in processes:
                await stop(process)
            await asyncio.gather(*readers)
    report = ROOT/'test-results/discovery.json'
    report.parent.mkdir(exist_ok=True)
    report.write_text(json.dumps(results, indent=2)+'\n')
    return 0 if all(r['result'] == 'passed' for r in results) else 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
