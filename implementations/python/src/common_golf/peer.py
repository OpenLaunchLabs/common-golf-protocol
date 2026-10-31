"""Direct-address diagnostic peer for automated cross-language tests only."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from aioquic.asyncio import connect, serve

from .identity import Identity
from .protocol import Draft
from .transport import Connection
from .discovery import listen, discover


def samples(draft, role):
    if role == "sim":
        return [(6, {"type": "state", "ready": True, "player": {"name": "Interop", "dexterity": "right"}, "mode": "range"}),
                (7, {"type": "result", "shot_number": 1, "accepted": True, "carry_distance_meters": 200.0})]
    shot = {"type": "shot", "shot_number": 1, "ball_speed_meters_per_second": 65.0,
            "vertical_launch_angle_degrees": 12.0, "horizontal_launch_angle_degrees": -1.0,
            "total_spin_revolutions_per_minute": 2500.0, "spin_axis_degrees": 2.0, "final": True}
    club = {"type": "club", "shot_number": 1, "club_speed_meters_per_second": 44.0, "final": True}
    if draft == Draft.ONE:
        shot["reduced_accuracy"] = True
    else:
        shot["estimated"] = ["ball_speed_meters_per_second"]
        club["estimated"] = ["club_speed_meters_per_second"]
    return [(1, shot), (3, club), (4, {"type": "status", "ready": True, "battery_percent": 80}),
            (5, {"type": "control", "command": "left", "event": "press"}),
            (8, {"type": "flight", "shot_number": 1, "carry_distance_meters": 200.0, "final": True}),
            (1000, {"type": "private", "name": "io.common-golf.interop-json-v1", "message": "opaque JSON ☃"}),
            (2, {"DeviceID": "Python reference", "Units": "Yards", "ShotNumber": 2, "APIversion": "1",
                 "BallData": {"Speed": 150.0, "SpinAxis": 2.0, "TotalSpin": 2500.0, "HLA": -1.0, "VLA": 12.0},
                 "ShotDataOptions": {"ContainsBallData": True, "ContainsClubData": False}})]


async def run(args):
    if args.role == "discover":
        handle = open(args.output, "a", encoding="utf-8") if args.output else sys.stdout
        try:
            for peer in await discover(args.duration):
                print(json.dumps({"kind": "discovered", "name": peer.name, "port": peer.port}), file=handle, flush=True)
        finally:
            if args.output:
                handle.close()
        return
    if not args.output or not args.identity:
        raise ValueError("listen/connect require --output and --identity")
    draft = Draft(args.draft)
    identity = Identity(args.identity, "Python reference")
    role = "device" if args.role == "listen" else "sim"
    output = open(args.output, "a", encoding="utf-8")
    def record(value):
        output.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
        output.flush()
    fixtures = json.loads(Path(args.fixtures).read_text())["drafts"][str(int(draft))][args.role] if args.fixtures else None
    fixture_messages = [(entry["channel"], entry["message"]) for entry in fixtures] if fixtures is not None else samples(draft, role)
    class Peer(Connection):
        def on_connected(self):
            record({"kind": "connected", "draft": int(self.draft), "fingerprint": self.peer_fingerprint,
                    "tier": self.peer_tier})
            for channel, message in fixture_messages:
                if channel != 0:
                    self.publish(channel, message)
        def on_message(self, channel, message, stream_id):
            record({"channel": channel, "message": message})
            if self.role == "sim" and channel == 2 and "Code" not in message:
                self.send(stream_id, {"Code": 200, "Message": "Shot received"})
        def on_stream_error(self, stream_id, code):
            record({"kind": "stream_error", "stream": stream_id, "code": code})
        def on_declined(self, channel, stream_id, code):
            record({"kind": "declined", "channel": channel, "code": code})
    def factory(*a, **kw):
        return Peer(*a, **kw, role=role, draft=draft,
                    info={"name": "Python reference", "protocol_version": args.version or draft.version},
                    private_channels={1000: 65535})
    try:
        config = identity.configuration(is_client=args.role == "connect", draft=draft)
        if args.role == "listen" and args.advertise:
            async with listen(args.advertise, identity, factory, draft=draft) as server:
                print("READY", server._transport.get_extra_info("sockname")[1], flush=True)
                await asyncio.sleep(args.duration)
        elif args.role == "listen":
            server = await serve(args.host, args.port, configuration=config, create_protocol=factory)
            print("READY", server._transport.get_extra_info("sockname")[1], flush=True)
            try:
                await asyncio.sleep(args.duration)
            finally:
                server.close()
                await asyncio.sleep(0.1)
        else:
            async def session():
                async with connect(args.host, args.port, configuration=config, create_protocol=factory):
                    await asyncio.sleep(args.duration)
            await asyncio.wait_for(session(), args.duration + 8)
    finally:
        output.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft", type=int, choices=(1, 2), default=2)
    parser.add_argument("--version", choices=("0.1.0", "0.1.1", "0.2.0"))
    parser.add_argument("--role", choices=("listen", "connect", "discover"), required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--output")
    parser.add_argument("--identity")
    parser.add_argument("--advertise")
    parser.add_argument("--fixtures")
    parser.add_argument("--duration", type=float, default=3)
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except (ConnectionError, asyncio.TimeoutError) as exc:
        print(f"QUIC handshake/ALPN application protocol failed: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
