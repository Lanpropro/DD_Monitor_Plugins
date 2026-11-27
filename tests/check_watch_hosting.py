"""Exercise the actual code lookup and match transports using isolated synthetic clients."""
import argparse
import asyncio
import importlib
import multiprocessing
import queue
import sys
import time
from pathlib import Path
from random import Random
from types import ModuleType, SimpleNamespace

import aiohttp

ROOT = Path(__file__).resolve().parents[1]


def routing(source):
    package = ModuleType("test_match_plugin")
    package.__path__ = [str(source)]
    sys.modules[package.__name__] = package
    return importlib.import_module("test_match_plugin.watch_room_route")


def play(source, directory, mode, code, output, stop):
    route = routing(source)
    link = route.RoutedRoomLink(code, output.put, mode=mode, directory=directory)
    link.worker.start()
    try:
        while not stop.wait(.085) and link.worker.is_alive():
            now = time.monotonic()
            tick = int(now / .09)
            if not link.locked:
                link.points.append((now, Random(tick).getrandbits(512), "screen"))
    finally:
        link.stopped.set()
        link.worker.join(25)
        assert not link.worker.is_alive()


async def lookup_status(directory, code, proxy):
    async with aiohttp.ClientSession() as session, session.get(directory+"/rooms/"+code, proxy=proxy) as response:
        return response.status


async def check_retry(host):
    from contextlib import asynccontextmanager
    from unittest.mock import patch

    events = []
    @asynccontextmanager
    async def attempt(*args):
        events.append("open")
        try:
            if events.count("open") == 1:
                raise ValueError("tunnel_start")
            yield "room"
        finally:
            events.append("close")

    with patch.object(host, "start_local_room", attempt):
        async with host.local_room(None, "fixture", lambda _: None) as room:
            assert room == "room" and events == ["open", "close", "open"]
    assert events == ["open", "close", "open", "close"]


def independent_host(source, directory):
    ctx = multiprocessing.get_context("spawn")
    processes, boxes, stops = [], [], []

    def open_client(code):
        stop, box = ctx.Event(), ctx.Queue()
        proc = ctx.Process(target=play,args=(source,directory,"local",code,box,stop))
        proc.start()
        processes.append(proc)
        boxes.append(box)
        stops.append(stop)

    def wait_for(box, predicate, timeout):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            try:
                message = box.get(timeout=2)
            except queue.Empty:
                continue
            assert message["type"] != "error", message
            if predicate(message):
                return message
        raise AssertionError("room event timed out")

    try:
        open_client(None)
        code = wait_for(boxes[0], lambda m:m["type"]=="seat", 260)["code"]
        for _ in range(2):
            open_client(code)
        for box in boxes:
            wait_for(box, lambda m:len(m.get("members",[]))==3, 55)
            wait_for(box, lambda m:m.get("locked"), 90)
        stops[0].set()
        processes[0].join(30)
        assert processes[0].exitcode == 0
        assert asyncio.run(lookup_status(directory,code,routing(source).room_proxy(directory))) == 404
    finally:
        for stop in stops:
            stop.set()
        for proc in processes:
            proc.join(32)
            if proc.is_alive():
                proc.terminate()
                proc.join()
            assert proc.exitcode == 0
    print("PASS: separate host and two friend processes, public code lookup, fresh three-person lock and expired invitation")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", required=True)
    parser.add_argument("--source", type=Path, default=ROOT/"plugins/match_sync")
    parser.add_argument("--mode", choices=("cloud","local"), required=True)
    args = parser.parse_args()
    sys.path.insert(0,str(ROOT/"tools/watch_party"))
    route = routing(args.source.resolve())
    from unittest.mock import patch
    transport = sys.modules[route.DirectRoomLink.__module__]
    with patch.object(transport, "getproxies", return_value={"https":"http://proxy.test:9900"}), patch.object(
            transport, "proxy_bypass", return_value=False):
        assert transport.room_proxy("wss://watch.example/live") == "http://proxy.test:9900"
        assert transport.room_proxy("https://watch.example/health") == "http://proxy.test:9900"
        assert transport.room_proxy("ws://127.0.0.1:18769/live") is None
    with patch.object(transport, "proxy_bypass", return_value=True):
        assert transport.room_proxy("wss://watch.example/live") is None
    asyncio.run(check_retry(sys.modules[route.local_room.__module__]))
    target = route.DIRECTORY if args.directory=="packaged" else args.directory
    if args.mode=="local":
        independent_host(args.source.resolve(),target)
    else:
        from watch_party_group import check_group
        factory = lambda code, notify, endpoint: route.RoutedRoomLink(code,notify,mode="cloud",directory=endpoint)
        check_group(SimpleNamespace(RoomLink=factory),target)


if __name__ == "__main__":
    main()
