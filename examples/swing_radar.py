"""A swing-speed radar: club data with no ball data. Channel 3 stands alone."""
import asyncio
import random

import common_golf as cg


class Radar(cg.Connection):
    READS = frozenset({0, 6, 7})   # info, state, result: what a simulator may open toward a device

    def on_connected(self):
        self.publish(cg.CHANNEL["info"], {"type": "info", "protocol_version": cg.PROTOCOL_VERSION,
                                          "name": "swing-radar", "channels": [0, 3]})
        self.club = self.open(cg.CHANNEL["club"])
        asyncio.ensure_future(self.swing_forever())

    async def swing_forever(self):
        n = 0
        while True:
            await asyncio.sleep(3)
            n += 1
            self.send(self.club, {"type": "club", "swing_number": n,
                                  "club_speed_meters_per_second": round(random.uniform(40, 52), 1)})
            print(f"[swing-radar] swing {n} sent")


if __name__ == "__main__":
    asyncio.run(cg.listen("swing-radar", Radar, {"vendor": "Example", "model": "Radar"}))
