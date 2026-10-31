#!/usr/bin/env python3
"""DNS-SD peers for testing one physical ESP32, without serial or Wi-Fi access.

For simulator firmware: hardware_peer.py listen --duration 60
Idle/reconnect check:   hardware_peer.py listen --duration 115 --quiet-after 8
Stress workload:       hardware_peer.py listen --duration 60 --shot-interval .1 --control-interval .05
For device firmware:    hardware_peer.py probe --role launch-monitor --duration 60
                       hardware_peer.py probe --role control-box --duration 60

Requires the common-golf-protocol Python package. Certificates live in a temporary directory.
"""
import argparse
import asyncio
from collections import Counter
import contextlib
import json
import math
from pathlib import Path
import sys
import struct
import tempfile
import time

import jsonschema
from aioquic.quic.events import ConnectionTerminated

EXAMPLES = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EXAMPLES))
import common_golf as cg

VALIDATORS = {
    number: jsonschema.Draft202012Validator(
        json.loads((EXAMPLES.parent / "schema" / f"{name}.json").read_text()))
    for name, number in {"info": 0, "shot": 1, "club": 3, "device_status": 4,
                         "control": 5, "simulator_state": 6, "result": 7, "flight": 8}.items()
}
COMMANDS = "left right up down reset_aim club_up club_down putter_toggle mulligan rehit drop tee_left tee_right switch_hand flyover shot_cam scorecard heatmap map green_grid pin hide_ui select back".split()


class Run:
    def __init__(self):
        self.peers = []
        self.errors = []
        self.stopping = False

    def check(self):
        if self.errors:
            raise AssertionError("; ".join(self.errors))

    async def observe(self, duration):
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            self.check()
            await asyncio.sleep(min(.25, max(0, deadline - time.monotonic())))
        self.check()

    async def cleanup(self):
        self.stopping = True
        for peer in self.peers:
            if peer.observation_finished is None:
                peer.observation_finished = time.monotonic()
        tasks = [task for peer in self.peers for task in peer.tasks]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class Tracked(cg.Connection):
    READS_PRIVATE = False

    def __init__(self, *args, run, label, quiet_after=None,
                 shot_interval=2, control_interval=1, **kwargs):
        super().__init__(*args, read_channels=self.READS, draft=cg.Draft.THREE, **kwargs)
        self.run = run
        self.label = label
        self.received = Counter()
        self.sent = Counter()
        self.outgoing = {}
        self.tasks = []
        self.peer_info = None
        self.was_connected = False
        self.quiet_after = quiet_after
        self.connected_at = None
        self.quiet_started = None
        self.last_application_activity = None
        self.observation_finished = None
        self.shot_interval = shot_interval
        self.control_interval = control_interval
        self.commands_sent = 0
        self.workload = None
        run.peers.append(self)

    def begin(self, channels):
        self.was_connected = True
        self.connected_at = time.monotonic()
        print(f"CONNECTED {self.label} fingerprint={self.peer_fingerprint}", flush=True)
        # The SDK sent info before on_connected.
        self.sent[0] = 1
        self.last_application_activity = time.monotonic()

    def emit(self, channel, message):
        if self.quiet_started is not None:
            raise AssertionError(f"{self.label}: application send attempted during quiet period")
        VALIDATORS[channel].validate(message)
        if channel not in self.outgoing:
            self.outgoing[channel] = self.open(channel)
        self.send(self.outgoing[channel], message)
        self.sent[channel] += 1
        self.last_application_activity = time.monotonic()

    def application_time_left(self):
        if self.quiet_after is None:
            return float("inf")
        return max(0, self.connected_at + self.quiet_after - time.monotonic())

    async def wait_to_publish(self, seconds):
        """Stop fixture application traffic at a deadline per connection.

        The base connection's independent QUIC PING task remains active.
        """
        await asyncio.sleep(min(seconds, self.application_time_left()))
        if self.application_time_left() == 0:
            self.quiet_started = time.monotonic()
            print(f"QUIET {self.label} after_seconds={self.quiet_after:g}; QUIC keepalive remains active", flush=True)
            return False
        return True

    async def publication_ticks(self, interval):
        """Schedule command/shot starts, excluding time spent formatting or holding.

        Skip missed slots if the host cannot keep up instead of sending a burst.
        """
        deadline = time.monotonic() + interval
        while await self.wait_to_publish(max(0, deadline - time.monotonic())):
            yield
            deadline += interval
            if deadline < time.monotonic():
                deadline = time.monotonic() + interval

    def start_task(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.append(task)

        def finished(future):
            if not future.cancelled() and future.exception():
                self.run.errors.append(f"{self.label}: {future.exception()}")

        task.add_done_callback(finished)

    def quic_event_received(self, event):
        if isinstance(event, ConnectionTerminated) and self.was_connected and not self.run.stopping:
            self.run.errors.append(f"{self.label}: unexpected disconnect {event.error_code}: {event.reason_phrase}")
        super().quic_event_received(event)

    def on_message(self, channel, message, stream_id):
        try:
            VALIDATORS[channel].validate(message)
        except Exception as error:
            self.run.errors.append(f"{self.label}: invalid channel {channel}: {error}")
            return
        self.received[channel] += 1
        self.last_application_activity = time.monotonic()
        if channel == 0:
            self.peer_info = message
        print(f"RX {self.label} channel={channel} {json.dumps(message, separators=(',', ':'))}", flush=True)
        self.received_valid(channel, message)

    def received_valid(self, channel, message):
        pass

    def on_declined(self, channel, stream_id, code):
        self.run.errors.append(f"{self.label}: channel {channel} unexpectedly declined with {code}")

    def summary(self):
        summary = {"peer": self.label, "fingerprint": self.peer_fingerprint,
                   "rx": dict(self.received), "tx": dict(self.sent)}
        if self.quiet_started is not None:
            summary["quiet_seconds"] = round(time.monotonic() - self.quiet_started, 3)
            summary["application_idle_seconds"] = round(time.monotonic() - self.last_application_activity, 3)
        if self.workload is not None and self.connected_at is not None:
            end = self.quiet_started or self.observation_finished or time.monotonic()
            active_seconds = max(end - self.connected_at, .000001)
            summary["workload"] = self.workload
            summary["active_seconds"] = round(active_seconds, 3)
            if "shot_interval_seconds" in self.workload:
                summary["observed_shots_per_second"] = round(self.sent[1] / active_seconds, 3)
            else:
                summary["commands_sent"] = self.commands_sent
                summary["observed_commands_per_second"] = round(self.commands_sent / active_seconds, 3)
                summary["observed_control_events_per_second"] = round(self.sent[5] / active_seconds, 3)
        return summary


class SimulatorProbe(Tracked):
    READS = frozenset({0, 1, 3, 4, 5, 8})

    def on_connected(self):
        self.begin([0, 6, 7])
        self.emit(6, {"type": "simulator_state", "ready": True, "requested_capture_mode": "full",
                      "player": {"name": "Hardware test", "dexterity": "right"}})

    def received_valid(self, channel, message):
        if channel == 1:
            self.emit(7, {"type": "result", "shot_uuid": message["shot_uuid"],
                          "accepted": True, "carry_distance_meters": 155.0,
                          "total_distance_meters": 165.0, "max_height_meters": 24.0})


class LaunchFixture(Tracked):
    READS = frozenset({0, 6, 7})

    def on_connected(self):
        self.workload = {"shot_interval_seconds": self.shot_interval,
                         "target_shots_per_second": 1 / self.shot_interval}
        self.begin([0, 1, 3, 4, 8])
        self.emit(4, {"type": "device_status", "ready": True, "ball_detected": False,
                      "message": "Synthetic hardware-test launch monitor"})
        self.start_task(self.shots())

    async def shots(self):
        number = 0
        async for _ in self.publication_ticks(self.shot_interval):
            number += 1
            shot_uuid = cg.new_shot_uuid()
            self.emit(4, {"type": "device_status", "ball_detected": True})
            shot = {"type": "shot", "shot_uuid": shot_uuid,
                    "player_dexterity": "right",
                    "ball_speed_meters_per_second": 60.0,
                    "vertical_launch_angle_degrees": 13.0,
                    "horizontal_launch_angle_degrees": -2.0,
                    "total_spin_revolutions_per_minute": 2800,
                    "spin_axis_degrees": -3.0}
            shot["estimated"] = [key for key, value in shot.items()
                                 if type(value) in (int, float)]
            self.emit(1, shot)
            self.emit(3, {"type": "club", "shot_uuid": shot_uuid,
                          "club_speed_meters_per_second": 41.4,
                          "club_path_degrees": -1.0,
                          "estimated": ["club_speed_meters_per_second", "club_path_degrees"]})
            self.emit(8, {"type": "flight", "shot_uuid": shot_uuid,
                          "carry_distance_meters": 153.0, "total_distance_meters": 162.0,
                          "carry_side_meters": -3.8, "max_height_meters": 24.0,
                          "hang_time_seconds": 5.5})
            self.emit(4, {"type": "device_status", "ball_detected": False})


class ControlFixture(Tracked):
    READS = frozenset({0, 6, 7})

    def on_connected(self):
        self.workload = {"control_interval_seconds": self.control_interval,
                         "target_commands_per_second": 1 / self.control_interval}
        self.begin([0, 5])
        self.start_task(self.controls())

    async def controls(self):
        number = 0
        async for _ in self.publication_ticks(self.control_interval):
            command = COMMANDS[number % len(COMMANDS)]
            number += 1
            self.commands_sent += 1
            # Complete an arrow release before entering the quiet period.
            hold_seconds = min(.15, self.control_interval / 2)
            if command in COMMANDS[:4] and self.application_time_left() > hold_seconds + .05:
                self.emit(5, {"type": "control", "command": command, "event": "down"})
                await asyncio.sleep(hold_seconds)
                self.emit(5, {"type": "control", "command": command, "event": "up"})
            else:
                self.emit(5, {"type": "control", "command": command, "event": "press"})


async def listen(args, run, identity_dir):
    listeners = []
    for label, cls in (("hardware-test-launch", LaunchFixture), ("hardware-test-control", ControlFixture)):
        def factory(*positional, cls=cls, label=label, **keywords):
            return cls(*positional, run=run, label=label, role="controller" if cls is ControlFixture else "device",
                       info={"name": label, "vendor": "CGP hardware test"},
                       quiet_after=args.quiet_after, shot_interval=args.shot_interval,
                       control_interval=args.control_interval, **keywords)
        async def serve_fixture(label=label, factory=factory):
            identity = cg.Identity(identity_dir / label, label)
            async with cg.listen(label, identity, factory, draft=cg.Draft.THREE, properties={"vendor": "CGP hardware test", "model": label}):
                await asyncio.Event().wait()
        task = asyncio.create_task(serve_fixture())
        listeners.append(task)
    print(f"LISTENING for ESP32 simulator for {args.duration:g} seconds via {cg.SERVICE}", flush=True)
    print("WORKLOAD_CONFIG " + json.dumps({"shot_interval_seconds": args.shot_interval,
          "control_interval_seconds": args.control_interval, "quiet_after_seconds": args.quiet_after},
          sort_keys=True), flush=True)
    try:
        await run.observe(args.duration)
        for task in listeners:
            if task.done():
                task.result()
                raise AssertionError("Fixture listener stopped unexpectedly")
        for label, required in (("hardware-test-launch", {0, 6, 7}),
                                ("hardware-test-control", {0, 6})):
            peers = [p for p in run.peers if p.label == label and p.was_connected]
            assert len(peers) == 1, f"Expected one stable board connection to {label}; got {len(peers)}"
            peer = peers[0]
            assert required <= peer.received.keys(), f"{label}: missing channels {required - peer.received.keys()}"
            assert peer.peer_info.get("model") == "ESP-WROOM-32", f"{label}: connected peer is not ESP32 mock"
            assert peer.peer_fingerprint, f"{label}: board supplied no Ed25519 identity"
            assert not peer._closed.is_set(), f"{label}: connection closed before final PING"
            if args.quiet_after is not None:
                assert peer.quiet_started is not None, f"{label}: quiet period never began"
                # A run long enough to test the negotiated idle timeout must
                # actually observe it, even if discovery took unexpectedly long.
                if args.duration - args.quiet_after >= 30.0:
                    idle = time.monotonic() - peer.last_application_activity
                    assert idle >= 30.0, f"{label}: only {idle:.1f}s application-idle; need {30.0:g}s"
            await asyncio.wait_for(peer.ping(), 5)
            print("PING_OK " + json.dumps(peer.summary(), sort_keys=True), flush=True)
        assert len({p.peer_fingerprint for p in run.peers if p.was_connected}) == 1, "Fixtures saw different board identities"
        run.check()
        print("PASS hardware simulator: discovered both fixtures, held two connections, sent state/results", flush=True)
    finally:
        await run.cleanup()
        for task in listeners:
            task.cancel()
        await asyncio.gather(*listeners, return_exceptions=True)


async def find_board(args):
    deadline = time.monotonic() + args.discovery_timeout
    while time.monotonic() < deadline:
        discovered = await cg.discover(min(3, max(.5, deadline - time.monotonic())))
        candidates = [item for item in discovered if item.name.startswith("ESP32 ")
                      and item.properties.get("model") in ("launch-monitor", "control-box")
                      and (not args.role or item.properties.get("model") == args.role)
                      and (not args.name or item.name == args.name)]
        if len(candidates) > 1:
            raise AssertionError("Several boards discovered; select their service name with --name: "
                                 + ", ".join(item.name for item in candidates))
        if candidates:
            return candidates[0]
    raise AssertionError("No matching ESP32 listener found through DNS-SD")


async def probe(args, run, identity_dir):
    discovered = await find_board(args)
    name, role = discovered.name, discovered.properties["model"]
    print(f"DISCOVERED {name} role={role}; opening two simulator connections", flush=True)
    try:
        async with contextlib.AsyncExitStack() as stack:
            peers = []
            for index in range(2):
                label = f"hardware-probe-{index + 1}"
                def factory(*positional, label=label, **keywords):
                    return SimulatorProbe(*positional, run=run, label=label, role="sim", info={"name": label}, **keywords)
                identity = cg.Identity(identity_dir / label, label)
                peer = await asyncio.wait_for(stack.enter_async_context(cg.dial(discovered, identity, factory, draft=cg.Draft.THREE)), 25)
                peers.append(peer)
            required = {0, 1, 3, 4, 8} if role == "launch-monitor" else {0, 5}
            if args.max_frame:
                deadline = time.monotonic() + 20
                while not all(required <= peer.received.keys() for peer in peers):
                    run.check()
                    if time.monotonic() >= deadline:
                        raise AssertionError("No initial role traffic before maximum-frame test")
                    await asyncio.sleep(.1)
                initial = json.dumps({"type": "simulator_state", "padding": ""}, separators=(",", ":"))
                message = {"type": "simulator_state", "padding": "x" * (65535 - len(initial))}
                VALIDATORS[6].validate(message)
                body = json.dumps(message, separators=(",", ":")).encode()
                assert len(body) == 65535
                first = peers[0]
                first._quic.send_stream_data(first.outgoing[6], struct.pack(">I", len(body)) + body)
                first.sent[6] += 1
                first.emit(6, {"type": "simulator_state", "requested_capture_mode": "putting"})
                print("SENT_MAX_FRAME peer=hardware-probe-1 bytes=65535 marker=putting; confirm marker in board serial log", flush=True)
                await asyncio.wait_for(first.ping(), 15)
            await run.observe(args.duration)
            for peer in peers:
                assert required <= peer.received.keys(), f"{peer.label}: missing channels {required - peer.received.keys()}"
                assert peer.peer_info.get("model") == "ESP-WROOM-32", "Peer is not ESP32 mock firmware"
                assert peer.peer_fingerprint, "Board supplied no Ed25519 identity"
                if role == "launch-monitor":
                    assert peer.sent[7] > 0, "No shots received"
                await asyncio.wait_for(peer.ping(), 5)
            assert peers[0].peer_fingerprint == peers[1].peer_fingerprint, "Board identity differs across connections"
            run.check()
            print(f"PASS hardware {role}: two simultaneous authenticated connections, schemas, sustained traffic", flush=True)
            run.stopping = True
    finally:
        await run.cleanup()


async def main(args):
    run = Run()
    with tempfile.TemporaryDirectory(prefix="cgp-hardware-identity-") as temporary:
        identity_dir = Path(temporary)
        try:
            await (listen(args, run, identity_dir) if args.mode == "listen" else probe(args, run, identity_dir))
        finally:
            for peer in run.peers:
                print("COUNTS " + json.dumps(peer.summary(), sort_keys=True), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=("listen", "probe"))
    parser.add_argument("--duration", type=float, default=60, help="traffic observation time in seconds (default: 60)")
    parser.add_argument("--discovery-timeout", type=float, default=20)
    parser.add_argument("--role", choices=("launch-monitor", "control-box"), help="restrict discovered device role")
    parser.add_argument("--name", help="select a discovered ESP32 service instance by its display name")
    parser.add_argument("--max-frame", action="store_true",
                        help="probe only: send a 65,535-byte state object while both connections are active")
    parser.add_argument("--quiet-after", type=float,
                        help="listen only: stop application traffic this many seconds after each connection; keep QUIC PING active")
    parser.add_argument("--shot-interval", type=float, default=2,
                        help="listen only: seconds between shot starts (default: 2)")
    parser.add_argument("--control-interval", type=float, default=1,
                        help="listen only: seconds between command starts; arrow releases are extra events (default: 1)")
    options = parser.parse_args()
    if any(not math.isfinite(value) or value <= 0 for value in
           (options.duration, options.discovery_timeout, options.shot_interval, options.control_interval)):
        parser.error("durations and workload intervals must be positive finite numbers")
    if options.max_frame and options.mode != "probe":
        parser.error("--max-frame requires probe mode")
    if options.quiet_after is not None and (options.mode != "listen" or
            not math.isfinite(options.quiet_after) or options.quiet_after <= 0):
        parser.error("--quiet-after must be positive and requires listen mode")
    try:
        asyncio.run(main(options))
    except (AssertionError, ConnectionError, asyncio.TimeoutError) as error:
        print(f"FAIL hardware test: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1)
