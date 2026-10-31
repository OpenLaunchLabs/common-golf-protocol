"""Discover devices, consume common messages and respond to shots/Open Connect."""
import argparse
import asyncio
from functools import partial
import json
from pathlib import Path

from common_golf import Connection, Draft, Identity, dial, discover


class Sim(Connection):
    failures = 0

    def on_connected(self):
        print(f"connected fingerprint={self.peer_fingerprint} tier={self.peer_tier}", flush=True)
        self.publish(6, {"type": "state", "ready": True, "mode": "range",
                         "player": {"name": "Player 1", "dexterity": "right"}})

    def on_message(self, channel, message, stream_id):
        print(json.dumps({"channel": channel, "message": message}), flush=True)
        if channel == 1 and message.get("final", True):
            # Missing or unusable measurements cannot produce a carry estimate.
            ball_speed = message.get("ball_speed_meters_per_second")
            shot_number = message.get("shot_number")
            if (isinstance(ball_speed, bool) or not isinstance(ball_speed, (int, float))
                    or isinstance(shot_number, bool) or not isinstance(shot_number, int)):
                return
            self.publish(7, {"type": "result", "shot_number": shot_number, "accepted": True,
                             "carry_distance_meters": round(200.0 * (ball_speed / 65.0) ** 2, 2)})
        elif channel == 2 and "DeviceID" in message:
            self.send(stream_id, {"Code": 200, "Message": "Shot received"})

    def on_stream_error(self, stream_id, code):
        if code == 2:
            Sim.failures += 1
        print(f"stream {stream_id} rejected: code {code}", flush=True)


def main():
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("--draft", type=int, choices=(1, 2), default=2)
    arguments.add_argument("--identity", help="persistent identity directory")
    arguments.add_argument("--trusted", help="JSON mapping of public-key fingerprint to tier")
    arguments.add_argument("--duration", type=float, default=0, help="seconds; zero keeps running")
    arguments.add_argument("--once", action="store_true", help="one discovery round, then observe for ten seconds")
    args = arguments.parse_args()
    async def run():
        identity = Identity(args.identity or ".cgp-identities/example-sim", "example-sim")
        trusted = json.loads(Path(args.trusted).read_text()) if args.trusted else {}
        factory = partial(Sim, role="sim", draft=Draft(args.draft), trusted=trusted, info={"name": "example-sim"})
        tasks, seen = set(), set()
        async def attach(peer):
            try:
                async with dial(peer, identity, factory, draft=Draft(args.draft)) as connection:
                    await connection.wait_closed()
            except ConnectionError:
                print(f"{peer.name}: TLS/ALPN connection failed", flush=True)
            finally:
                seen.discard(peer.name)  # rediscover a device that reboots or drops
        try:
            async def browse():
                while True:
                    for peer in await discover(2):
                        if peer.name not in seen:
                            seen.add(peer.name)
                            task = asyncio.create_task(attach(peer))
                            tasks.add(task)
                    if args.once:
                        await asyncio.sleep(args.duration or 10)
                        return
            if args.duration and not args.once:
                try:
                    await asyncio.wait_for(browse(), args.duration)
                except asyncio.TimeoutError:
                    pass
            else:
                await browse()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    print(f"{Sim.failures} stream errors")
    raise SystemExit(bool(Sim.failures))


if __name__ == "__main__":
    main()
