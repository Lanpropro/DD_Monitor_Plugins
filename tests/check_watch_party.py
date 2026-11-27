"""Exercise the same room protocol over loopback or a public HTTPS tunnel."""
import asyncio
import sys
from pathlib import Path

from aiohttp import ClientSession

MARKER = "123456789abcdef0" * 8


async def receive(ws, kind):
    msg = await ws.receive_json(timeout=15)
    assert msg["type"] == kind, msg
    return msg


async def state(ws, online, ready, scheduled=False):
    msg = await receive(ws, "state")
    assert msg["online"] == online and sum(bool(m) for m in msg["markers"]) == ready, msg
    assert (msg["start_ms"] is not None) == scheduled, msg
    return msg["start_ms"]


async def guest(base, code):
    async with ClientSession() as session:
        async with session.ws_connect(base + "/ws") as ws:
            await ws.send_json(dict(type="join", code=code))
            seat = await receive(ws, "seat")
            await state(ws, [True, True], 0)
            await ws.send_json(dict(type="ready", marker="f" * 128))
            await state(ws, [True, True], 1)
            await state(ws, [True, True], 2)  # Two different markers cannot start playback.
            await ws.send_json(dict(type="ready", marker=MARKER))
            first = await state(ws, [True, True], 2, True)
        for _ in range(10):
            async with session.ws_connect(base + "/ws") as ws:
                await ws.send_json(dict(type="resume", code=code, token=seat["token"]))
                answer = await ws.receive_json(timeout=15)
                if answer["type"] == "seat":
                    assert answer["token"] == seat["token"]
                    await state(ws, [True, True], 0)
                    await ws.send_json(dict(type="ready", marker=MARKER))
                    await state(ws, [True, True], 1)
                    second = await state(ws, [True, True], 2, True)
                    print(first, second)
                    return
            await asyncio.sleep(.15)
        raise AssertionError("guest could not resume")


async def check(base):
    async with ClientSession() as session:
        async with session.get(base + "/health") as resp:
            assert await resp.json() == {"service":"match-watch-party", "ok":True}
        async with session.get(base + "/") as resp:
            assert "好友同看连接试验" in await resp.text()
        async with session.ws_connect(base + "/ws") as other, session.ws_connect(base + "/ws") as host:
            for socket in (host, other):
                await socket.send_json(dict(type="create"))
                seat = await receive(socket, "seat")
                assert len(seat["code"]) == 6
                await state(socket, [True], 0)
                if socket is host:
                    owner = seat
            assert seat["code"] != owner["code"]
            for data in [dict(type="join", code="missing"),
                         dict(type="resume", code=seat["code"], token=owner["token"])]:
                async with session.ws_connect(base + "/ws") as bad:
                    await bad.send_json(data)
                    await receive(bad, "error")
            await host.send_json(dict(type="ready", marker=MARKER))
            assert (await receive(host, "error"))["reason"] == "wait_for_peer"
            await host.send_json(dict(type="ready", marker=MARKER, extra="unexpected"))
            await receive(host, "error")
            proc = await asyncio.create_subprocess_exec(sys.executable, str(Path(__file__).resolve()),
                                                        base, owner["code"], stdout=asyncio.subprocess.PIPE)
            try:
                await state(host, [True, True], 0)
                await state(host, [True, True], 1)
                async with session.ws_connect(base + "/ws") as third:
                    await third.send_json(dict(type="join", code=owner["code"]))
                    await receive(third, "error")
                await host.send_json(dict(type="ready", marker=MARKER))
                await state(host, [True, True], 2)
                first = await state(host, [True, True], 2, True)
                await state(host, [True, False], 0)
                await state(host, [True, True], 0)
                await state(host, [True, True], 1)
                await host.send_json(dict(type="ready", marker=MARKER))
                second = await state(host, [True, True], 2, True)
                stdout, _ = await asyncio.wait_for(proc.communicate(), 20)
                assert proc.returncode == 0 and list(map(int, stdout.split())) == [first, second]
                assert second >= first  # Loopback can rejoin within one server clock tick.
                await other.send_json(dict(type="ping"))
                await receive(other, "pong")  # Other room saw none of the host's updates.
            finally:
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()
        async with session.ws_connect(base + "/ws") as host:
            await host.send_json(dict(type="create"))
            code = (await receive(host, "seat"))["code"]
            await state(host, [True], 0)
            async with session.ws_connect(base + "/ws") as first:
                await first.send_json(dict(type="join", code=code))
                old = await receive(first, "seat")
                await state(host, [True, True], 0)
                async with session.ws_connect(base + "/ws") as duplicate:
                    await duplicate.send_json(dict(type="resume", code=code, token=old["token"]))
                    assert (await receive(duplicate, "error"))["reason"] == "seat_online"
            await state(host, [True, False], 0)
            async with session.ws_connect(base + "/ws") as replacement:
                await replacement.send_json(dict(type="join", code=code))
                new = await receive(replacement, "seat")
                assert new["token"] != old["token"]
                await state(host, [True, True], 0)
                async with session.ws_connect(base + "/ws") as third:
                    await third.send_json(dict(type="join", code=code))
                    assert (await receive(third, "error"))["reason"] == "room_full"
            await state(host, [True, False], 0)
            async with session.ws_connect(base + "/ws") as stale:
                await stale.send_json(dict(type="resume", code=code, token=old["token"]))
                await receive(stale, "error")
        async with session.ws_connect(base + "/ws") as late:
            await late.send_json(dict(type="join", code=code))
            assert (await receive(late, "error"))["reason"] == "host_offline"
        async with session.ws_connect(base + "/ws") as unknown:
            await unknown.send_json(dict(type="join", code="missing"))
            assert (await receive(unknown, "error"))["reason"] == "room_missing"
    print("PASS: two clients, room isolation, common deadline, reconnect and guest re-entry by code")


if __name__ == "__main__":
    asyncio.run(guest(*sys.argv[1:]) if len(sys.argv) == 3 else check(sys.argv[1]))
