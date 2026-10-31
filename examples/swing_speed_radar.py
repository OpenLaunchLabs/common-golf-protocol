"""Swing-speed radar: club channel 3 without ball measurements."""
import argparse
import asyncio
from functools import partial
import json
import random
from pathlib import Path

from common_golf import Connection, Draft, Identity, listen, new_shot_uuid


class Radar(Connection):
    def on_connected(self):
        print(f"connected fingerprint={self.peer_fingerprint} tier={self.peer_tier}", flush=True)
        self._publish_task = asyncio.create_task(self.publish_measurements())

    async def publish_measurements(self):
        number = 1
        while True:
            self.publish(3, {
                "type": "club",
                **({"shot_uuid": new_shot_uuid()} if self.draft == Draft.THREE else {"swing_number": number}),
                "club_speed_meters_per_second": round(random.uniform(42.0, 46.0), 2),
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
    arguments.add_argument("--trusted", help="JSON mapping of public-key fingerprint to tier")
    arguments.add_argument("--duration", type=float, default=0, help="seconds; zero keeps running")
    args = arguments.parse_args()

    async def run():
        draft = Draft(args.draft)
        identity = Identity(args.identity or ".cgp-identities/swing-radar", "swing-radar")
        trusted = json.loads(Path(args.trusted).read_text()) if args.trusted else {}
        factory = partial(Radar, role="device", draft=draft,
                          trusted=trusted, info={"name": "swing-radar"})
        async with listen("swing-radar", identity, factory, draft=draft):
            print(f"swing-radar: advertised via DNS-SD ({draft.alpn})", flush=True)
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
