"""Open Connect over a draft 3 custom channel, or legacy channel 2."""
import argparse
import asyncio
from functools import partial
import json
import random

from common_golf import Connection, Draft, Identity, listen, OPEN_CONNECT, new_shot_uuid


class Device(Connection):
    """Open Connect device; replies share the opening stream without a second prefix."""

    def __init__(self, *args, **kwargs):
        if Draft(kwargs.get("draft", Draft.THREE)) == Draft.THREE:
            kwargs["contracts"] = {OPEN_CONNECT.identifier: OPEN_CONNECT}
            kwargs["menu"] = [{"channel": 1003, "contract": OPEN_CONNECT.identifier}]
        super().__init__(*args, **kwargs)

    def on_connected(self):
        print(f"connected fingerprint={self.peer_fingerprint}", flush=True)
        self._publish_task = asyncio.create_task(self.publish_measurements())

    async def publish_measurements(self):
        number = 1
        while True:
            self.publish(1003 if self.draft == Draft.THREE else 2, {
                **({"shot_uuid": new_shot_uuid()} if self.draft == Draft.THREE else {}),
                "DeviceID": "open-connect-device",
                "Units": "Yards",
                "ShotNumber": number,
                "APIversion": "1",
                "BallData": {
                    # Open Connect uses mph with Units="Yards".
                    "Speed": round(random.uniform(140.0, 152.0), 2),
                    "SpinAxis": round(random.uniform(-3.0, 7.0), 2),
                    "TotalSpin": round(random.uniform(2200.0, 2800.0), 2),
                    "HLA": round(random.uniform(-3.0, 1.0), 2),
                    "VLA": round(random.uniform(10.0, 14.0), 2),
                },
                "ShotDataOptions": {
                    "ContainsBallData": True,
                    "ContainsClubData": False,
                },
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
        identity = Identity(args.identity or ".cgp-identities/open-connect-device", "open-connect-device")
        factory = partial(Device, role="device", draft=draft,
                          info={"name": "open-connect-device"})
        async with listen("open-connect-device", identity, factory, draft=draft):
            print(f"open-connect-device: advertised via DNS-SD ({draft.alpn})", flush=True)
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
