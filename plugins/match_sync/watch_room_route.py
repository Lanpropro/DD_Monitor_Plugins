"""Register or resolve a six-digit room code before connecting to its match service."""
import asyncio
from contextlib import AsyncExitStack, suppress
import re
import secrets
import time

import aiohttp

from .watch_party_link import RoomLink as DirectRoomLink, room_proxy
from .watch_room_host import local_room

DIRECTORY = "https://watch.ddmonitor.top"


class RoomFailure(Exception):
    pass


async def directory_request(session, method, url, **kwargs):
    async with session.request(method, url, proxy=room_proxy(url), allow_redirects=False,
                               timeout=aiohttp.ClientTimeout(total=15), **kwargs) as response:
        if response.status == 404:
            raise RoomFailure("room_missing")
        response.raise_for_status()
        return await response.json()


class RoutedRoomLink(DirectRoomLink):
    def __init__(self, code, notify, *, mode="local", directory=DIRECTORY):
        super().__init__(code, notify)
        self.mode = mode
        self.directory = directory.rstrip("/")

    async def serve(self):
        # Cancellation also covers the first download and tunnel startup before a seat exists.
        session_task = asyncio.create_task(self.room_session())
        try:
            while not self.stopped.is_set() and not session_task.done():
                await asyncio.sleep(.1)
            if session_task.done():
                await session_task
        except (aiohttp.ClientError, TimeoutError, OSError, ValueError, RoomFailure) as exc:
            reason = str(exc) if isinstance(exc, (RoomFailure, ValueError)) else "connect_failed"
            self.notify(dict(type="error", reason=reason))
        finally:
            session_task.cancel()
            await asyncio.gather(session_task, return_exceptions=True)

    async def room_session(self):
        async with aiohttp.ClientSession() as session, AsyncExitStack() as stack:
            base = self.directory + "/rooms"
            process = lease = None
            hosting = self.code is None
            if hosting:
                data = {"mode": self.mode}
                if self.mode == "local":
                    self.host_key = secrets.token_urlsafe(24)
                    def progress(message):
                        self.notify(dict(type="network", text=message))
                    self.endpoint, data["origin"], process = await stack.enter_async_context(
                        local_room(session, self.host_key, progress))
                record = await directory_request(session, "POST", base, json=data)
                self.code = record["code"]
                if self.mode == "local":
                    lease = {"Authorization": "Bearer " + record["owner"]}
                else:
                    self.host_key = record["owner"]
            url = base + "/" + self.code
            if not hosting:
                route = await directory_request(session, "GET", url)
                self.mode = route["mode"]
                if self.mode == "local":
                    origin = route.get("origin")
                    if not isinstance(origin, str) or not re.fullmatch(r"https://[a-z0-9-]+\.trycloudflare\.com", origin):
                        raise RoomFailure("invalid_route")
                    self.endpoint = "wss://" + origin[8:] + "/live"
            if self.mode == "cloud":
                self.endpoint = url.replace("https://","wss://",1).replace("http://","ws://",1) + "/live"

            async def supervise():
                if process is not None:
                    last_success = time.monotonic()
                    while process.returncode is None:
                        try:
                            await asyncio.wait_for(process.wait(), timeout=45)
                        except TimeoutError:
                            try:
                                await directory_request(session, "PUT", url, headers=lease)
                                last_success = time.monotonic()
                            except (aiohttp.ClientError, TimeoutError):
                                if time.monotonic() - last_success > 120:
                                    raise RoomFailure("lease_lost")
                    raise RoomFailure("tunnel_stopped")
                # A guest's old tunnel may disappear when the host exits. Check the
                # directory during reconnects instead of retrying the old address forever.
                while True:
                    await asyncio.sleep(15)
                    if self.offset is None and not hosting:
                        try:
                            await directory_request(session, "GET", url)
                        except (aiohttp.ClientError, TimeoutError):
                            pass

            tasks = [asyncio.create_task(self.connections(session)), asyncio.create_task(supervise())]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    await task
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if lease:
                    with suppress(aiohttp.ClientError, TimeoutError, RoomFailure):
                        await directory_request(session, "DELETE", url, headers=lease)
