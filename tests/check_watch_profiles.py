"""Exercise public roster updates against the Python server and a local Worker."""
import argparse
import asyncio
import importlib
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

from aiohttp import ClientSession, web

ROOT = Path(__file__).resolve().parents[1]
BASE = {"platform": "bilibili", "id": "fixture"}
PROFILE = {**BASE, "nickname": "岸边的猫", "avatar": "dGVzdA=="}


async def roster_case(make_link, rich):
    links = []

    async def open_link(code=None, profile=None):
        messages = asyncio.Queue()
        loop = asyncio.get_running_loop()
        link = make_link(code, lambda message: loop.call_soon_threadsafe(messages.put_nowait, message))
        link.profile = profile
        links.append(link)
        link.worker.start()
        await expect(messages, lambda message: message["type"] == "seat")
        return link, messages

    async def expect(messages, condition):
        async with asyncio.timeout(10):
            while True:
                message = await messages.get()
                assert message["type"] != "error", message
                if condition(message):
                    return message

    try:
        host, host_messages = await open_link(profile=PROFILE)
        await expect(host_messages, lambda m: m.get("members") == [
            {"side": 0, "profile": PROFILE if rich else BASE}])
        guest, messages = await open_link(host.code)
        expected = PROFILE if rich else BASE
        await expect(messages, lambda m: m.get("members") == [
            {"side": 0, "profile": expected}, {"side": 1, "profile": None}])
        guest.profile = {**BASE, "id": "updated", "nickname": "七分钟"}
        updated = guest.profile if rich else {"platform": "bilibili", "id": "updated"}
        await expect(host_messages, lambda m: m.get("members") == [
            {"side": 0, "profile": expected}, {"side": 1, "profile": updated}])
        guest.profile = None
        await expect(host_messages, lambda m: m.get("members") == [
            {"side": 0, "profile": expected}, {"side": 1, "profile": None}])
    finally:
        for link in links:
            link.stopped.set()
        for link in links:
            await asyncio.to_thread(link.worker.join, 5)
            assert not link.worker.is_alive()


async def local_case(server, link_type, rich):
    original_send = web.WebSocketResponse.send_json
    validate = server.public_profile

    async def old_send(socket, data, *args, **kwargs):
        if data.get("type") == "pong":
            data = {key: data[key] for key in ("type", "server_ms")}
        await original_send(socket, data, *args, **kwargs)

    def old_profile(value):
        if value is not None and set(value) != {"platform", "id"}:
            raise ValueError("invalid")
        return validate(value)

    runner = web.AppRunner(server.make_app())
    await runner.setup()
    try:
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        endpoint = f"ws://127.0.0.1:{runner.addresses[0][1]}/live"
        if rich:
            await roster_case(lambda code, notify: link_type(code, notify, endpoint), rich)
        else:
            with patch.object(web.WebSocketResponse, "send_json", old_send), patch.object(server, "public_profile", old_profile):
                await roster_case(lambda code, notify: link_type(code, notify, endpoint), rich)
    finally:
        await runner.cleanup()


async def main(args):
    package = ModuleType("fixture_match_sync")
    package.__path__ = [str(args.source.resolve())]
    sys.modules[package.__name__] = package
    server = importlib.import_module("fixture_match_sync.watch_room_server")
    route = importlib.import_module("fixture_match_sync.watch_room_route")
    assert server.public_profile(PROFILE) == PROFILE and server.public_profile(BASE) == BASE
    for invalid in ({"nickname": "猫" * 49}, {"nickname": "\n"}, {"avatar": "https://example.com/face"},
                    {"avatar": "a" * 2052}, {"cookie": "fixture"}):
        try:
            server.public_profile({**BASE, **invalid})
        except ValueError:
            pass
        else:
            raise AssertionError(invalid)
    await local_case(server, route.DirectRoomLink, True)
    await local_case(server, route.DirectRoomLink, False)
    print("PASS: public profile updates and guest fallback against current and legacy local rooms")
    if args.cloud:
        async with ClientSession() as session:
            async with session.post(args.cloud + "/rooms", json={"mode": "cloud"}) as response:
                response.raise_for_status()
                room = await response.json()
            endpoint = args.cloud + f"/rooms/{room['code']}/live"
            async with session.ws_connect(endpoint) as socket:
                # Cancel a join while the Worker handles a pending room request.
                await socket.send_json({"type": "join", "code": room["code"]})
                await socket.send_json({"type": "ping"})
        await roster_case(lambda code, notify: route.RoutedRoomLink(
            code, notify, mode="cloud", directory=args.cloud), True)
        print("PASS: public profile updates and guest fallback against the local Cloudflare Worker")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT / "plugins/match_sync")
    parser.add_argument("--cloud", help="Local Cloudflare Worker URL")
    asyncio.run(main(parser.parse_args()))
