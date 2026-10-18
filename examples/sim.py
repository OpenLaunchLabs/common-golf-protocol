"""A simulator: discovers every device, connects, validates and prints what arrives.

Reads channels 0, 1, 3, 4, 5, 8 and any private channel. Answers Open Connect
bodies on channel 2 with Code 200. Publishes state on channel 6 when a device
connects and a result on channel 7 for every shot. Exits non-zero if any
message fails its schema, so running a device against this script is the test.

    python sim.py [--trusted fingerprints.json] [--once]
"""
import argparse
import asyncio
import json
import pathlib
import sys

import jsonschema

import common_golf as cg

SCHEMA_DIR = pathlib.Path(__file__).resolve().parent.parent / "schema"
SCHEMA = {cg.CHANNEL[name]: json.loads((SCHEMA_DIR / f"{name}.json").read_text()) for name in cg.CHANNEL}
NAME = {v: k for k, v in cg.CHANNEL.items()}

args = argparse.ArgumentParser()
args.add_argument("--trusted", help="fingerprint list; without it every device is a maker")
args.add_argument("--once", action="store_true", help="exit after one discovery round")
ARGS = args.parse_args()
TRUSTED = json.loads(pathlib.Path(ARGS.trusted).read_text()) if ARGS.trusted else {}
failures = 0


class Sim(cg.Connection):
    READS = frozenset({0, 1, 2, 3, 4, 5, 8})   # everything a device or controller may open toward a simulator

    def __init__(self, *args, device: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.device = device

    def on_connected(self):
        self.tier = cg.tier_of(self.peer_fingerprint, TRUSTED)
        print(f"[sim] connected to {self.device} fingerprint={self.peer_fingerprint} tier={self.tier}")
        self.publish(cg.CHANNEL["info"], {"type": "info", "protocol_version": cg.PROTOCOL_VERSION,
                                          "name": "example-sim", "channels": [0, 6, 7]})
        self.results = self.open(cg.CHANNEL["result"])
        self.publish(cg.CHANNEL["state"], {"type": "state", "ready": True, "mode": "range",
                                           "player": {"name": "Player 1", "dexterity": "right"},
                                           "club": {"id": "7I", "name": "7 iron"}})

    def on_message(self, channel, msg, stream_id):
        global failures
        label = NAME.get(channel, f"private:{channel}")
        if channel in SCHEMA:
            try:
                jsonschema.validate(msg, SCHEMA[channel])
            except jsonschema.ValidationError as e:
                failures += 1
                print(f"[sim] SCHEMA FAIL on {label}: {e.message}\n      {msg}")
                return
        print(f"[sim] {self.device} {label}: {json.dumps(msg)}")
        if channel == cg.CHANNEL["shot"] and msg.get("final", True):
            self.send(self.results, {"type": "result", "shot_number": msg["shot_number"], "accepted": True,
                                     "carry_distance_meters": round(msg["ball_speed_meters_per_second"] * 2.6, 1),
                                     "carry_side_meters": round(msg["horizontal_launch_angle_degrees"] * 2.0, 1)})
        elif channel == cg.CHANNEL["open_connect"] and "DeviceID" in msg:
            reply = {"Code": 200, "Message": "Shot received"}
            if not msg["ShotDataOptions"].get("ContainsBallData"):
                reply = {"Code": 201, "Message": "Player info", "Player": {"Handed": "RH", "Club": "7I"}}
            self.send(stream_id, reply)


async def attach(name, ip, port):
    try:
        async with cg.dial(ip, port, "example-sim", lambda *a, **k: Sim(*a, device=name, **k)) as conn:
            await conn.connected
            await conn.wait_closed()
    except ConnectionError:
        # No shared ALPN string is the usual cause: the peer speaks another draft (section 3.8).
        print(f"[sim] {name}: handshake failed; it does not speak {cg.ALPN}")


async def main():
    seen = set()
    while True:
        for name, ip, port, txt in await cg.discover():
            if name not in seen:
                seen.add(name)
                print(f"[sim] discovered {name} {txt}")
                asyncio.ensure_future(attach(name, ip, port))
        if ARGS.once:
            await asyncio.sleep(10)
            return


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    print(f"[sim] {failures} schema failures")
    sys.exit(1 if failures else 0)
