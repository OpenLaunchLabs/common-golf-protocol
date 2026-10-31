"""A control box with virtual button presses and no shot data."""
import argparse
import asyncio
from functools import partial
import json
from pathlib import Path

from common_golf import Connection, Draft, Identity, listen

SEQUENCE = [("left", "down"), ("left", "up"), ("club_up", "press"),
            ("reset_aim", "press"), ("heatmap", "press"), ("mulligan", "press")]


class Box(Connection):
    """Deterministic virtual button sequence on common channel 5."""

    def on_connected(self):
        print(f"connected fingerprint={self.peer_fingerprint} tier={self.peer_tier}", flush=True)
        self._publish_task = asyncio.create_task(self.publish_controls())

    async def publish_controls(self):
        number = 1
        while True:
            command, event = SEQUENCE[(number - 1) % len(SEQUENCE)]
            self.publish(5, {
                "type": "control",
                "command": command,
                "event": event,
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
    arguments.add_argument("--draft", type=int, choices=(1, 2), default=2)
    arguments.add_argument("--identity", help="persistent identity directory")
    arguments.add_argument("--trusted", help="JSON mapping of public-key fingerprint to tier")
    arguments.add_argument("--duration", type=float, default=0, help="seconds; zero keeps running")
    args = arguments.parse_args()

    async def run():
        draft = Draft(args.draft)
        identity = Identity(args.identity or ".cgp-identities/control-box", "control-box")
        trusted = json.loads(Path(args.trusted).read_text()) if args.trusted else {}
        factory = partial(Box, role="controller", draft=draft,
                          trusted=trusted, info={"name": "control-box"})
        async with listen("control-box", identity, factory, draft=draft):
            print(f"control-box: advertised via DNS-SD ({draft.alpn})", flush=True)
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
