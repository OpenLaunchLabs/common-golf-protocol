"""A launch monitor using every device-side channel, gated by the peer's trust tier.

    python device_full.py [--trusted fingerprints.json]

Makers get info, status and shots. Developers and internal peers also get
club data, computed flight, and a private diagnostics channel (1000).
Prints the simulator's state and result messages as they arrive.
"""
import argparse
import asyncio
import json
import pathlib
import random
import time
import uuid

import common_golf as cg

args = argparse.ArgumentParser()
args.add_argument("--trusted", help="fingerprint list; without it every simulator is a maker")
ARGS = args.parse_args()
TRUSTED = json.loads(pathlib.Path(ARGS.trusted).read_text()) if ARGS.trusted else {}
START = time.monotonic()


class Device(cg.Connection):
    READS = frozenset({0, 6, 7})   # info, state, result: what a simulator may open toward a device

    def on_connected(self):
        self.tier = cg.tier_of(self.peer_fingerprint, TRUSTED)
        print(f"[full-device] simulator fingerprint={self.peer_fingerprint} tier={self.tier}")
        channels = [0, 1, 4] + ([3, 8, 1000] if self.tier != "maker" else [])
        self.publish(cg.CHANNEL["info"], {"type": "info", "protocol_version": cg.PROTOCOL_VERSION,
                                          "name": "full-device", "vendor": "Example", "model": "Full",
                                          "serial": "F-0001", "firmware_version": "1.0.0", "channels": channels})
        self.status = self.open(cg.CHANNEL["status"])
        self.send(self.status, {"type": "status", "ready": True, "ball_detected": False, "battery_percent": 87})
        self.shots = self.open(cg.CHANNEL["shot"])
        if self.tier != "maker":
            self.club = self.open(cg.CHANNEL["club"])
            self.flight = self.open(cg.CHANNEL["flight"])
            self.diag = self.open(1000)
            self.send(self.diag, {"type": "private", "name": "com.example.diagnostics", "version": "1.0.0"})
        asyncio.ensure_future(self.hit_forever())

    def on_message(self, channel, msg, stream_id):
        print(f"[full-device] from simulator on channel {channel}: {json.dumps(msg)}")

    async def hit_forever(self):
        n = 0
        while True:
            await asyncio.sleep(4)
            n += 1
            self.send(self.status, {"type": "status", "ball_detected": True})
            speed = round(random.uniform(55, 75), 1)
            hla = round(random.uniform(-4, 4), 1)
            base = {"type": "shot", "shot_number": n, "measurement_id": str(uuid.uuid4()),
                    "measured_at_nanoseconds": time.monotonic_ns(), "player_dexterity": "right",
                    "tee_position": "tee", "shot_kind": "full",
                    "ball_speed_meters_per_second": speed,
                    "vertical_launch_angle_degrees": round(random.uniform(10, 14), 1),
                    "horizontal_launch_angle_degrees": hla}
            # Preliminary: launch numbers plus a placeholder spin, named in `estimated`.
            self.send(self.shots, {**base, "final": False,
                                   "total_spin_revolutions_per_minute": 2700, "spin_axis_degrees": 0,
                                   "estimated": ["total_spin_revolutions_per_minute", "spin_axis_degrees"]})
            await asyncio.sleep(0.3)
            # Final: same shot_number, spin added. Replaces the preliminary whole; nothing is merged.
            self.send(self.shots, {**base, "final": True,
                                   "total_spin_revolutions_per_minute": round(random.uniform(2200, 3200)),
                                   "spin_axis_degrees": round(random.uniform(-8, 8), 1)})
            print(f"[full-device] shot {n} sent (preliminary then final)")
            if self.tier == "maker":
                continue
            self.send(self.club, {"type": "club", "shot_number": n, "player_dexterity": "right",
                                  "club_speed_meters_per_second": round(speed / 1.45, 1),
                                  "angle_of_attack_degrees": round(random.uniform(-4, 2), 1),
                                  "club_path_degrees": round(random.uniform(-3, 3), 1),
                                  "face_angle_degrees": round(hla * 0.8, 1),
                                  "smash_factor": 1.45})
            self.send(self.flight, {"type": "flight", "shot_number": n,
                                    "carry_distance_meters": round(speed * 2.55, 1),
                                    "carry_side_meters": round(hla * 1.9, 1),
                                    "max_height_meters": round(random.uniform(18, 30), 1),
                                    "hang_time_seconds": round(random.uniform(5, 7), 2)})
            self.send(self.diag, {"radar_temperature_celsius": 41.2, "uptime_seconds": int(time.monotonic() - START)})


if __name__ == "__main__":
    asyncio.run(cg.listen("full-device", Device, {"vendor": "Example", "model": "Full", "serial": "F-0001"}))
