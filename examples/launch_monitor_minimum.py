"""The smallest launch monitor: automatic info and three-number shots."""
import argparse
import asyncio
from functools import partial
import json
import random

from common_golf import Connection, Draft, Identity, listen, new_shot_uuid


class Device(Connection):
    """A minimum CGP launch monitor with simulated measurements."""

    def on_connected(self):
        print(f"connected fingerprint={self.peer_fingerprint}", flush=True)
        self._publish_task = asyncio.create_task(self.publish_measurements())

    async def publish_measurements(self):
        number = 1
        while True:
            self.publish(1, {
                "type": "shot",
                **({"shot_uuid": new_shot_uuid()} if self.draft == Draft.THREE else {"shot_number": number}),
                "ball_speed_meters_per_second": round(random.uniform(62.0, 68.0), 2),
                "vertical_launch_angle_degrees": round(random.uniform(10.0, 14.0), 2),
                "horizontal_launch_angle_degrees": round(random.uniform(-3.0, 1.0), 2),
            })
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
    arguments.add_argument("--duration", type=float, default=0, help="seconds; zero keeps running")
    args = arguments.parse_args()

    async def run():
        draft = Draft(args.draft)
        identity = Identity(args.identity or ".cgp-identities/minimum-device", "minimum-device")
        factory = partial(Device, role="device", draft=draft,
                          info={"name": "minimum-device"})
        async with listen("minimum-device", identity, factory, draft=draft):
            print(f"minimum-device: advertised via DNS-SD ({draft.alpn})", flush=True)
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
