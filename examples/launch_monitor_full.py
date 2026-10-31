"""Shot refinements, device status, club and flight; legacy final flags on drafts 1/2."""
import argparse
import asyncio
from functools import partial
import json
import random
from pathlib import Path

from common_golf import Connection, Draft, Identity, listen, new_shot_uuid


class Device(Connection):

    def on_connected(self):
        print(f"connected fingerprint={self.peer_fingerprint} tier={self.peer_tier}", flush=True)
        self._publish_task = asyncio.create_task(self.publish_measurements())

    async def publish_measurements(self):
        number = 1
        while True:
            ball_speed = round(random.uniform(62.0, 68.0), 2)
            spin = round(random.uniform(2200.0, 2800.0), 2)
            spin_axis = round(random.uniform(-3.0, 7.0), 2)
            shot_identity = {"shot_uuid": new_shot_uuid()} if self.draft == Draft.THREE else {"shot_number": number}
            base = {"type": "shot", **shot_identity, "ball_speed_meters_per_second": ball_speed,
                    "vertical_launch_angle_degrees": round(random.uniform(10.0, 14.0), 2),
                    "horizontal_launch_angle_degrees": round(random.uniform(-3.0, 1.0), 2)}
            preliminary = dict(base, total_spin_revolutions_per_minute=round(spin + random.uniform(-200.0, 200.0), 2),
                               spin_axis_degrees=round(spin_axis + random.uniform(-2.0, 2.0), 2), final=False)
            if self.draft == Draft.ONE:
                preliminary["reduced_accuracy"] = True
            else:
                preliminary["estimated"] = ["total_spin_revolutions_per_minute", "spin_axis_degrees"]
            messages = [(4, {"type": "device_status" if self.draft == Draft.THREE else "status", "ready": True, "ball_detected": True, "battery_percent": 87}),
                        (1, preliminary), (1, dict(base, total_spin_revolutions_per_minute=spin, spin_axis_degrees=spin_axis, final=True))]
            if self.peer_tier != "maker":
                messages += [(3, {"type": "club", **shot_identity, "club_speed_meters_per_second": round(ball_speed / 1.48, 2), "final": True}),
                             (8, {"type": "flight", **shot_identity, "carry_distance_meters": round(200.0 * (ball_speed / 65.0) ** 2, 2), "final": True})]
            for channel, message in messages:
                if self.draft == Draft.THREE:
                    message.pop("final", None)
                self.publish(channel, message)
            number += 1
            await asyncio.sleep(3)

    def on_message(self, channel, message, stream_id):
        print(json.dumps({"channel": channel, "message": message}), flush=True)

    def on_disconnected(self, code, reason):
        if getattr(self, "_publish_task", None):
            self._publish_task.cancel()


def main():
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("--draft", type=int, choices=(1, 2, 3), default=3)
    arguments.add_argument("--identity", help="persistent identity directory")
    arguments.add_argument("--trusted", help="JSON mapping of public-key fingerprint to tier")
    arguments.add_argument("--duration", type=float, default=0, help="seconds; zero keeps running")
    args = arguments.parse_args()

    async def run():
        draft = Draft(args.draft)
        identity = Identity(args.identity or ".cgp-identities/full-device", "full-device")
        trusted = json.loads(Path(args.trusted).read_text()) if args.trusted else {}
        factory = partial(Device, role="device", draft=draft,
                          trusted=trusted, info={"name": "full-device"})
        async with listen("full-device", identity, factory, draft=draft):
            print(f"full-device: advertised via DNS-SD ({draft.alpn})", flush=True)
            if args.duration:
                await asyncio.sleep(args.duration)
            else:
                await asyncio.Event().wait()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
