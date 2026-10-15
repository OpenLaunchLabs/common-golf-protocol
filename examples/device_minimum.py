"""The smallest conforming launch monitor: info on channel 0, three-number shots on channel 1."""
import asyncio
import random

import common_golf as cg


class Device(cg.Connection):
    READS = frozenset({0, 6, 7})   # info, state, result: what a simulator may open toward a device

    def on_connected(self):
        self.publish(cg.CHANNEL["info"], {"type": "info", "protocol_version": cg.PROTOCOL_VERSION,
                                          "name": "minimum-device", "channels": [0, 1]})
        self.shots = self.open(cg.CHANNEL["shot"])
        asyncio.ensure_future(self.hit_forever())

    async def hit_forever(self):
        n = 0
        while True:
            await asyncio.sleep(3)
            n += 1
            self.send(self.shots, {
                "type": "shot",
                "shot_number": n,
                "ball_speed_meters_per_second": round(random.uniform(30, 75), 1),
                "vertical_launch_angle_degrees": round(random.uniform(8, 20), 1),
                "horizontal_launch_angle_degrees": round(random.uniform(-5, 5), 1),
            })
            print(f"[minimum-device] shot {n} sent")


if __name__ == "__main__":
    asyncio.run(cg.listen("minimum-device", Device, {"vendor": "Example", "model": "Minimum"}))
