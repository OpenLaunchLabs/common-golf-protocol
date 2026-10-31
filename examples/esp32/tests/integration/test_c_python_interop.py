"""Launch native C subprocesses and exchange real QUIC traffic with Python peers.

The cgp_host executable uses draft 3; cgp_host_draft2 retains the explicitly
selected legacy firmware transport profile. Python
controls subprocesses and loopback connections; it does not call C through FFI.
These tests require no board and do not exercise Wi-Fi or DNS-SD discovery.
"""
import asyncio
import json
import os
import pathlib
import re
import struct
import sys

import jsonschema
from aioquic.asyncio import serve, connect
from functools import partial

EXAMPLES = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(EXAMPLES))
import common_golf as cg

SCHEMAS = {number: json.loads((EXAMPLES.parent / "schema" / "draft-2" / f"{name}.json").read_text())
           for name, number in cg.CHANNELS.items()}


class Native:
    def __init__(self, executable, role, *ports, draft):
        self.arguments = [str(executable), role, *map(str, ports)]
        self.schemas = SCHEMAS if draft == 2 else {
            number: json.loads((EXAMPLES.parent / "schema" / f"{name}.json").read_text())
            for name, number in {"info": 0, "shot": 1, "club": 3, "device_status": 4,
                                 "control": 5, "simulator_state": 6, "result": 7, "flight": 8}.items()
        }
        self.key = None
        self.lines = []
        self.process = None

    async def __aenter__(self):
        self.process = await asyncio.create_subprocess_exec(
            *self.arguments, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        self.port_ready = asyncio.get_running_loop().create_future()
        self.reader = asyncio.create_task(self.read())
        try:
            self.port = await asyncio.wait_for(self.port_ready, 10)
        except BaseException:
            await self.__aexit__(None, None, None)
            raise
        return self

    async def read(self):
        while line := await self.process.stdout.readline():
            text = line.decode(errors="replace").rstrip()
            self.lines.append(text)
            if os.environ.get("CGP_TEST_VERBOSE"):
                print(self.arguments[1], text, flush=True)
            if text.startswith("KEY "):
                self.key = text.split()[1]
            if text.startswith("PORT ") and not self.port_ready.done():
                self.port_ready.set_result(int(text.split()[1]))
        if not self.port_ready.done():
            self.port_ready.set_exception(RuntimeError("\n".join(self.lines)))

    async def __aexit__(self, *_):
        if self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        await self.reader

    def messages(self):
        found = []
        for line in self.lines:
            match = re.search(r"RX peer=.* channel=(\d+) (\{.*\})$", line)
            if match:
                channel = int(match[1])
                message = json.loads(match[2])
                jsonschema.validate(message, self.schemas[channel])
                found.append((channel, message))
        return found

    async def until(self, predicate, seconds=15):
        deadline = asyncio.get_running_loop().time() + seconds
        while asyncio.get_running_loop().time() < deadline:
            if predicate():
                return
            if self.process.returncode is not None:
                break
            await asyncio.sleep(.05)
        raise AssertionError("Native process did not meet expectation:\n" + "\n".join(self.lines))


class Probe(cg.Connection):
    READS = frozenset({0, 1, 3, 4, 5, 8})
    READS_PRIVATE = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, role="sim", draft=cg.Draft.TWO, read_channels=self.READS, **kwargs)
        self.messages = []
        self.errors = []
        self.declined = {}

    def on_connected(self):
        self.state = self.publish(6, {"type": "state", "ready": True, "mode": "range"})
        self.results = self.open(7)

    def on_message(self, channel, message, stream_id):
        try:
            jsonschema.validate(message, SCHEMAS[channel])
        except Exception as error:
            self.errors.append(str(error))
        self.messages.append((channel, message))
        if channel == 1 and message.get("final", True):
            self.send(self.results, {"type": "result", "shot_number": message["shot_number"],
                                     "accepted": True, "carry_distance_meters": 150})

    def on_declined(self, channel, stream_id, code):
        self.declined[stream_id] = code


async def wait_until(predicate, seconds=15):
    deadline = asyncio.get_running_loop().time() + seconds
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(.05)
    raise AssertionError("Timed out waiting for protocol events")


async def python_to_native(executable, identity_dir):
    def dial(port, name):
        identity = cg.Identity(identity_dir / name, name)
        return connect("127.0.0.1", port, configuration=identity.configuration(is_client=True, draft=cg.Draft.TWO), create_protocol=Probe)
    async with Native(executable, "launch_monitor", draft=2) as launch:
        # Two independent authenticated connections to one listener.
        async with dial(launch.port, "probe-a") as a, \
                   dial(launch.port, "probe-b") as b:
            await wait_until(lambda: {0, 1, 3, 4, 8}.issubset({ch for ch, _ in a.messages})
                             and any(ch == 1 for ch, _ in b.messages))
            assert not a.errors + b.errors, a.errors + b.errors
            assert a.peer_fingerprint == b.peer_fingerprint
            assert a.peer_fingerprint and len(a.peer_fingerprint) == 64
            # Wrong owner, duplicate and optional legacy channel => not_interested.
            def inject(channel, bidi=False):
                sid = a._quic.get_next_available_stream_id(is_unidirectional=not bidi)
                a._quic.send_stream_data(sid, struct.pack(">I", channel))
                a.transmit()
                return sid
            wrong = inject(1)
            duplicate = inject(6)
            legacy = inject(2, True)
            await wait_until(lambda: all(s in a.declined for s in (wrong, duplicate, legacy)))
            assert all(a.declined[s] == 1 for s in (wrong, duplicate, legacy))
            # Largest permitted message, incrementally delivered through a 4 KB
            # flow-control window; follows with a marker to prove it was consumed.
            body = json.dumps({"type": "state", "padding": ""}, separators=(",", ":"))
            large = body.replace('"padding":""', '"padding":"' + "x" * (65535 - len(body)) + '"')
            assert len(large) == 65535
            a._quic.send_stream_data(a.state, struct.pack(">I", len(large)) + large.encode())
            a.send(a.state, {"type": "state", "mode": "putting"})
            a.transmit()
            await launch.until(lambda: any('"mode":"putting"' in s or '"mode": "putting"' in s
                                          for s in launch.lines))
            # Malformed JSON stops only this stream. Reopening restores the channel.
            a._quic.send_stream_data(a.state, struct.pack(">I", 5) + b"[1,2]")
            a.transmit()
            await wait_until(lambda: a.state in a.declined)
            assert a.declined[a.state] == 2
            reopened = a.publish(6, {"type": "state", "mode": "course"})
            await launch.until(lambda: any('"mode":"course"' in s or '"mode": "course"' in s
                                          for s in launch.lines))
            assert reopened not in a.declined
            await a.ping()
            await b.ping()
            print("PASS Python <-> native launch monitor: schemas, 2 clients, identity, 64 KB, decline/reopen")
    async with Native(executable, "control_box", draft=2) as box:
        async with dial(box.port, "probe-controls") as probe:
            await wait_until(lambda: any(ch == 5 for ch, _ in probe.messages))
            assert not probe.errors, probe.errors
            print("PASS Python <-> native control box: channel 5 schema")


async def three_native(executable, *, draft):
    async with Native(executable, "launch_monitor", draft=draft) as launch, \
               Native(executable, "control_box", draft=draft) as box:
        targets = [launch.port, launch.key, box.port, box.key] if draft == 3 else [launch.port, box.port]
        async with Native(executable, "simulator", *targets, draft=draft) as sim:
            await sim.until(lambda: {0, 1, 3, 4, 5, 8}.issubset({ch for ch, _ in sim.messages()}))
            await launch.until(lambda: {0, 6, 7}.issubset({ch for ch, _ in launch.messages()}))
            assert {0, 6}.issubset({ch for ch, _ in box.messages()})
            assert sum("CONNECTED peer=" in line for line in sim.lines) == 2
            print("PASS all 3 native roles: concurrent connections and bidirectional standard channels")


async def native_to_python(executable, identity_dir):
    # Exercise the reference applications over QUIC.
    import launch_monitor_minimum
    import launch_monitor_full
    import control_box
    import swing_speed_radar
    for classes in ((launch_monitor_minimum.Device, control_box.Box), (launch_monitor_full.Device, swing_speed_radar.Radar)):
        servers = []
        try:
            for index, cls in enumerate(classes):
                server = await serve("127.0.0.1", 0,
                                     configuration=cg.Identity(identity_dir / f"python-device-{index}").configuration(is_client=False, draft=cg.Draft.TWO),
                                     create_protocol=partial(cls, draft=cg.Draft.TWO, role="controller" if cls is control_box.Box else "device"))
                servers.append(server)
            ports = [s._transport.get_extra_info("sockname")[1] for s in servers]
            async with Native(executable, "simulator", *ports, draft=2) as sim:
                expected = {0, 1, 5} if classes[0] is launch_monitor_minimum.Device else {0, 1, 3, 4}
                await sim.until(lambda: expected.issubset({ch for ch, _ in sim.messages()})
                                and any(ch == 1 and message.get("final", True)
                                        for ch, message in sim.messages()))
                if classes[0] is launch_monitor_full.Device:
                    shots = [message for ch, message in sim.messages() if ch == 1]
                    preliminary = next(message for message in shots if message.get("final") is False)
                    final = next(message for message in shots if message.get("final") is True)
                    assert preliminary["shot_number"] == final["shot_number"]
                    assert "estimated" in preliminary and "estimated" not in final
                print("PASS native simulator <-> Python", ", ".join(c.__module__ for c in classes))
        finally:
            for server in servers:
                server.close()


def test_python_peers_with_c_devices(cgp_host_draft2, tmp_path):
    asyncio.run(python_to_native(cgp_host_draft2, tmp_path))


def test_c_roles_with_each_other(cgp_host_draft2):
    asyncio.run(three_native(cgp_host_draft2, draft=2))


def test_c_simulator_with_python_devices(cgp_host_draft2, tmp_path):
    asyncio.run(native_to_python(cgp_host_draft2, tmp_path))


def test_draft3_c_roles_with_each_other(cgp_host):
    asyncio.run(three_native(cgp_host, draft=3))
