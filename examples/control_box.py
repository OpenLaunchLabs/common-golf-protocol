"""A standalone control box: virtual button presses on channel 5. No ball data at all."""
import asyncio

import common_golf as cg

SEQUENCE = [("left", "down"), ("left", "up"), ("club_up", "press"), ("reset_aim", "press"),
            ("heatmap", "press"), ("mulligan", "press")]


class Box(cg.Connection):
    READS = frozenset({0, 6, 7})   # info, state, result: what a simulator may open toward a device

    def on_connected(self):
        self.publish(cg.CHANNEL["info"], {"type": "info", "protocol_version": cg.PROTOCOL_VERSION,
                                          "name": "control-box", "channels": [0, 5]})
        self.buttons = self.open(cg.CHANNEL["control"])
        asyncio.ensure_future(self.press_forever())

    async def press_forever(self):
        while True:
            for command, event in SEQUENCE:
                await asyncio.sleep(1.5)
                self.send(self.buttons, {"type": "control", "command": command, "event": event})
                print(f"[control-box] {command} {event}")


if __name__ == "__main__":
    asyncio.run(cg.listen("control-box", Box, {"vendor": "Example", "model": "Box"}))
