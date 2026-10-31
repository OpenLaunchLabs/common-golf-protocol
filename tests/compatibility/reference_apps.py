#!/usr/bin/env python3
"""Exercise the actual reference device and simulator classes on every draft."""
import argparse
import asyncio
from schema_check import check_message
from contextlib import AsyncExitStack
from functools import partial
import json
from pathlib import Path
import sys
import tempfile
import uuid

from aioquic.asyncio import serve
from common_golf import Draft, Identity, dial, discover, listen
from common_golf.discovery import DiscoveredPeer

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'examples'))
import launch_monitor_minimum
import launch_monitor_full
import launch_monitor_open_connect
import control_box
import swing_speed_radar
from sim import Sim

CASES = [(launch_monitor_minimum.Device, 'device', {0,1}, {0,6,7}),
         (launch_monitor_full.Device, 'device', {0,1,3,4,8}, {0,6,7}),
         (launch_monitor_open_connect.Device, 'device', {0,2}, {0,2,6}),
         (control_box.Box, 'controller', {0,5}, {0,6}),
         (swing_speed_radar.Radar, 'device', {0,3}, {0,6})]


async def run_draft(draft, directory, use_discovery):
    identity = Identity(directory/'sim', 'reference-sim')
    prefix = 'cgp-reference-'+uuid.uuid4().hex[:8]
    devices = {}
    def tracked(cls, name):
        class Device(cls):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.received=[]
                devices[name]=self
            def on_message(self, channel, message, stream_id):
                check_message(draft, channel, message)
                self.received.append((channel,message))
        return Device
    class RecordingSim(Sim):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.received=[]
        def on_message(self, channel, message, stream_id):
            check_message(draft, channel, message)
            self.received.append((channel,message))
            super().on_message(channel,message,stream_id)
    async with AsyncExitStack() as stack:
        peers={}
        for cls, role, _, _ in CASES:
            name=prefix+'-'+cls.__module__
            device_identity=Identity(directory/cls.__module__, name)
            factory=partial(tracked(cls, name), role=role, draft=draft,
                            trusted={identity.fingerprint:'developer'})
            if use_discovery:
                await stack.enter_async_context(listen(name, device_identity, factory, draft=draft))
            else:
                server=await serve('127.0.0.1', 0,
                    configuration=device_identity.configuration(is_client=False, draft=draft),
                    create_protocol=factory)
                stack.callback(server.close)
                peers[name]=DiscoveredPeer(name,'127.0.0.1',server._transport.get_extra_info('sockname')[1],{})
        if use_discovery:
            peers={p.name:p for p in await discover(3) if p.name.startswith(prefix+'-')}
        assert len(peers)==len(CASES), f'Only discovered {len(peers)} reference devices'
        connections={}
        for name,peer in peers.items():
            connections[name]=await stack.enter_async_context(dial(peer,identity,
                partial(RecordingSim,role='sim',draft=draft),draft=draft))
        async def complete():
            for cls, _, expected_sim, expected_device in CASES:
                name=prefix+'-'+cls.__module__
                if name not in devices or not expected_sim<={ch for ch,_ in connections[name].received}:
                    return False
                if not expected_device<={ch for ch,_ in devices[name].received}:
                    return False
            return True
        deadline=asyncio.get_running_loop().time()+8
        while not await complete():
            assert asyncio.get_running_loop().time()<deadline, 'Reference application messages or simulator replies missing'
            await asyncio.sleep(.02)
        full=connections[prefix+'-launch_monitor_full'].received
        shots=[msg for ch,msg in full if ch==1]
        preliminary=next(msg for msg in shots if msg.get('final') is False)
        final=next(msg for msg in shots if msg.get('final') is True)
        assert preliminary['shot_number']==final['shot_number']
        assert ('reduced_accuracy' in preliminary)==(draft==Draft.ONE)
        assert ('estimated' in preliminary)==(draft!=Draft.ONE)
        assert not Sim.failures, f'{Sim.failures} stream errors'
        return [{'draft':int(draft),'example':cls.__module__,'result':'passed'} for cls,_,_,_ in CASES]


async def main(args):
    results=[]
    with tempfile.TemporaryDirectory(prefix='cgp-reference-') as temporary:
        for draft in Draft:
            results.extend(await run_draft(draft,Path(temporary)/str(int(draft)),args.discovery))
    report=Path(args.report);report.parent.mkdir(parents=True,exist_ok=True)
    report.write_text(json.dumps({'cases':results},indent=2)+'\n')
    print(f'{len(results)}/{len(results)} reference application/draft sessions passed; report: {report}')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--discovery',action='store_true')
    parser.add_argument('--report',default=str(ROOT/'test-results/reference_apps.json'))
    asyncio.run(main(parser.parse_args()))
