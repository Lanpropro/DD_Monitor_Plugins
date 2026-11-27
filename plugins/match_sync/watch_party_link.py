"""Background WebSocket client for the opt-in shared playback test."""
import asyncio
from collections import deque
from threading import Event, Thread
import time
from urllib.request import getproxies, proxy_bypass
from urllib.parse import urlsplit

import aiohttp


ENDPOINT = "wss://indexes-experimental-luke-tablets.trycloudflare.com/live"


def room_proxy(url):
    """Apply the system HTTP(S) proxy to room traffic, including WebSocket handshakes."""
    address = urlsplit(url)
    if address.hostname in {"localhost", "127.0.0.1", "::1"} or proxy_bypass(address.hostname):
        return None
    proxies = getproxies()
    scheme = {"ws": "http", "wss": "https"}.get(address.scheme, address.scheme)
    return proxies.get(address.scheme) or proxies.get(scheme)


def basic_profile(profile):
    return {key: profile[key] for key in ("platform", "id")} if profile else None


class RoomLink:
    def __init__(self, code, notify, endpoint=ENDPOINT):
        self.code, self.notify, self.endpoint = code, notify, endpoint
        self.stopped = Event()
        self.points = deque(maxlen=16)
        self.commands = deque(maxlen=12)
        self.basis = "screen"
        self.reset_requested = Event()
        self.locked = False
        self.profile = None
        self.host_key = None
        self.token = None
        self.online = False
        self.offset = None
        self.worker = Thread(target=self.run, daemon=True, name="match-watch-room")

    def run(self):
        try:
            asyncio.run(self.serve())
        except RuntimeError:
            if not self.stopped.is_set():
                raise

    async def serve(self):
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=6, connect=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            await self.connections(session)

    async def connections(self, session):
        while not self.stopped.is_set():
            self.locked = False
            self.offset = None
            self.online = False
            try:
                async with session.ws_connect(self.endpoint, heartbeat=12, proxy=room_proxy(self.endpoint)) as ws:
                    if not await self.session(ws):
                        return
            except (aiohttp.ClientError, TimeoutError, OSError):
                pass
            self.online = False
            if not self.stopped.is_set():
                self.notify({"type":"network", "text":"连接中断，正在重新连接…"})
                await asyncio.sleep(1)

    async def session(self, ws):
        command = (dict(type="resume", code=self.code, token=self.token) if self.token else
                   dict(type="join", code=self.code) if self.code else dict(type="create"))
        if self.host_key and not self.token:
            command = dict(type="create", code=self.code, host_key=self.host_key)
        # Older rooms require exactly the two original fields, including on resume.
        profile = basic_profile(self.profile)
        rich_profile = False
        command["profile"] = profile
        revision = -1
        await ws.send_json(command)
        clocks = deque(maxlen=16)
        sent = None
        next_ping = next_batch = 0
        self.points.clear()
        self.commands.clear()
        while not self.stopped.is_set():
            now = time.monotonic()
            if sent is None and now >= next_ping:
                sent = now
                await ws.send_json({"type":"ping"})
            if sent is not None and now - sent > 8:
                return True
            current_profile = self.profile if rich_profile else basic_profile(self.profile)
            if revision >= 0 and current_profile != profile:
                profile = current_profile
                await ws.send_json(dict(type="profile", profile=profile))
            if self.online and self.reset_requested.is_set():
                self.reset_requested.clear()
                self.points.clear()
                await ws.send_json(dict(type="reset", revision=revision))
            while self.online and self.commands:
                item = self.commands.popleft()
                if item["type"] == "manual":
                    item = dict(item, revision=revision)
                await ws.send_json(item)
            if self.online and not self.locked and self.offset is not None and now >= next_batch:
                points = []
                basis_now = self.basis
                while self.points and len(points) < 16:
                    stamp, signature, basis = self.points.popleft()
                    if now - stamp < 2 and basis == basis_now:
                        points.append([round(stamp * 1000 + self.offset), format(signature, "0128x")])
                await ws.send_json(dict(type="frames", revision=revision, points=points, rtt=min(clocks)[0], basis=basis_now))
                next_batch = now + .5
            try:
                message = await ws.receive(timeout=.1)
            except TimeoutError:
                continue
            if message.type != aiohttp.WSMsgType.TEXT:
                return True
            data = message.json()
            kind = data["type"]
            if kind == "pong" and sent is not None:
                rich_profile = data.get("rich_profile") is True
                end = time.monotonic()
                clocks.append((round((end - sent) * 1000), data["server_ms"] - (sent + end) * 500))
                self.offset = min(clocks)[1] if min(clocks)[0] <= 2000 else None
                next_ping = end + (.12 if len(clocks) < 5 else 3)
                sent = None
            elif kind == "seat":
                self.code, self.token = data["code"], data["token"]
                self.notify(dict(type="seat", code=self.code, side=data["side"]))
            elif kind == "state" and data["revision"] > revision:
                revision = data["revision"]
                self.online = len(data["members"]) >= 2
                self.locked = False
                self.points.clear()
                self.commands.clear()
                self.reset_requested.clear()
                self.notify(dict(type="state", both=self.online, members=data["members"]))
            elif kind == "members" and data["revision"] == revision:
                self.notify(data)
            elif kind == "measurement" and self.online and data["revision"] == revision:
                self.locked = data.get("locked", False)
                if self.locked or data.get("reset"):
                    self.points.clear()
                self.notify(data)
            elif kind == "error":
                if data["reason"] == "seat_online":
                    return True
                self.notify(dict(type="error", reason=data["reason"]))
                return False
        return False
