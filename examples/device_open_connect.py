"""A launch monitor that already speaks GSPro Open Connect v1, moved onto QUIC unchanged.

Channel 2 is bidirectional: the device writes Open Connect bodies, the
simulator writes Open Connect replies (Code 200/201) on the same stream.
Nothing about the JSON changes; only the socket does.
"""
import asyncio
import json
import random

import common_golf as cg


class Device(cg.Connection):
    READS = frozenset({0, 6, 7})   # info, state, result: what a simulator may open toward a device

    def on_connected(self):
        self.publish(cg.CHANNEL["info"], {"type": "info", "protocol_version": cg.PROTOCOL_VERSION,
                                          "name": "open-connect-device", "channels": [0, 2]})
        self.oc = self.open(cg.CHANNEL["open_connect"], bidirectional=True)
        asyncio.ensure_future(self.hit_forever())

    def on_message(self, channel, msg, stream_id):
        print(f"[open-connect-device] channel {channel}: {json.dumps(msg)}")

    async def hit_forever(self):
        n = 0
        while True:
            await asyncio.sleep(3)
            n += 1
            self.send(self.oc, {
                "DeviceID": "open-connect-device", "Units": "Yards", "ShotNumber": n, "APIversion": "1",
                "BallData": {"Speed": round(random.uniform(120, 165), 1), "SpinAxis": round(random.uniform(-10, 10), 1),
                             "TotalSpin": round(random.uniform(2200, 3200)), "HLA": round(random.uniform(-4, 4), 1),
                             "VLA": round(random.uniform(10, 14), 1)},
                "ShotDataOptions": {"ContainsBallData": True, "ContainsClubData": False},
            })
            print(f"[open-connect-device] shot {n} sent")


if __name__ == "__main__":
    asyncio.run(cg.listen("open-connect-device", Device, {"vendor": "Example", "model": "OpenConnect"}))
