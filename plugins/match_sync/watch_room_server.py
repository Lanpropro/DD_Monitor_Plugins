"""Room rendezvous for live watch parties and the two-person web demo. Only the demo page and room protocol are public."""
import asyncio
import json
from dataclasses import dataclass, field
import re
import secrets
import time

from aiohttp import WSMsgType, web
if __package__:
    from .watch_room_match import LiveMatch
else:
    from watch_room_match import LiveMatch


def now_ms():
    return time.monotonic_ns() // 1_000_000


@dataclass
class Seat:
    token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    socket: web.WebSocketResponse | None = None
    marker: str | None = None
    profile: dict | None = None


@dataclass
class Room:
    seats: list = field(default_factory=lambda: [Seat()])
    start_ms: int | None = None
    revision: int = 0
    live: LiveMatch | None = None
    last_change: float = field(default_factory=time.monotonic)

    def reset(self):
        self.start_ms = None
        for seat in self.seats:
            seat.marker = None

    def members(self):
        return [dict(side=i, profile=s.profile) for i, s in enumerate(self.seats) if s.socket is not None]

    async def broadcast(self, snapshot):
        for seat in self.seats:
            if seat.socket is not None:
                try:
                    await seat.socket.send_json(snapshot)
                except ConnectionError:
                    pass

    async def publish(self):
        self.revision += 1
        self.last_change = time.monotonic()
        snapshot = dict(type="state", revision=self.revision, start_ms=self.start_ms,
                        online=[s.socket is not None for s in self.seats],
                        markers=[s.marker for s in self.seats])
        if self.live:
            snapshot["members"] = self.members()
            self.live.active = tuple(m["side"] for m in snapshot["members"])
            self.live.reset(now_ms())
        await self.broadcast(snapshot)


def public_profile(value):
    if value is None:
        return None
    if (not isinstance(value, dict) or not {"platform", "id"} <= value.keys()
            or value.keys() - {"platform", "id", "nickname", "avatar"}
            or not isinstance(value["platform"], str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", value["platform"])
            or not isinstance(value["id"], str) or not 1 <= len(value["id"]) <= 100
            or not value["id"].isprintable()):
        raise ValueError("invalid")
    if "nickname" in value:
        nickname = value["nickname"]
        if not isinstance(nickname, str) or not 1 <= len(nickname) <= 48 or not nickname.isprintable():
            raise ValueError("invalid")
    if "avatar" in value:
        avatar = value["avatar"]
        if (not isinstance(avatar, str) or not 4 <= len(avatar) <= 2048 or len(avatar) % 4
                or not re.fullmatch(r"(?:[a-zA-Z0-9+/]{4})*(?:[a-zA-Z0-9+/]{2}==|[a-zA-Z0-9+/]{3}=)?", avatar)):
            raise ValueError("invalid")
    return dict(value)


def make_app(observations=None, *, page=None, host_key=None):
    namespaces = {"/ws": {}, "/live": {}}
    sockets = set()


    async def index(request):
        return web.Response(text=page, content_type="text/html", headers={"Cache-Control": "no-store"})

    async def health(request):
        return web.json_response({"service": "match-watch-party", "ok": True})

    async def connect(request):
        if len(sockets) >= 32:
            raise web.HTTPServiceUnavailable()
        live = request.path == "/live"
        rooms = namespaces[request.path]
        ws = web.WebSocketResponse(heartbeat=15, max_msg_size=4096 if live else 1024)
        sockets.add(ws)
        room = seat = None
        try:
            await ws.prepare(request)
            while True:
                msg = await ws.receive(timeout=90 if seat else 10)
                if msg.type != WSMsgType.TEXT:
                    break
                try:
                    data = json.loads(msg.data)
                    if not isinstance(data, dict):
                        raise ValueError("invalid")
                    op = data.get("type")
                    if not isinstance(op, str):
                        raise ValueError("invalid")
                    if op == "ping" and data.keys() == {"type"}:
                        pong = {"type": "pong", "server_ms": now_ms()}
                        if live:
                            pong["rich_profile"] = True
                        await ws.send_json(pong)
                        continue
                    if seat is None:
                        profile = public_profile(data.pop("profile", None)) if live else None
                        code = data.get("code")
                        if host_key and op == "create":
                            supplied = data.pop("host_key", None)
                            if supplied != host_key or rooms or not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
                                raise ValueError("unavailable")
                            del data["code"]
                        if op == "create" and data.keys() == {"type"} and len(rooms) < 64:
                            code = code if host_key else f"{secrets.randbelow(1_000_000):06d}"
                            while code in rooms:
                                code = f"{secrets.randbelow(1_000_000):06d}"
                            room = rooms[code] = Room(live=LiveMatch() if live else None)
                            seat = room.seats[0]
                        elif isinstance(code, str) and code in rooms:
                            candidate = rooms[code]
                            if op == "join" and data.keys() == {"type", "code"}:
                                if candidate.seats[0].socket is None:
                                    raise ValueError("host_offline")
                                vacancy = next((i for i, s in enumerate(candidate.seats) if i and s.socket is None), None)
                                if vacancy is None and len(candidate.seats) >= (3 if live else 2):
                                    raise ValueError("room_full")
                                seat = Seat()
                                if vacancy is None:
                                    candidate.seats.append(seat)
                                else:
                                    candidate.seats[vacancy] = seat
                            elif op == "resume" and data.keys() == {"type", "code", "token"}:
                                existing = next((s for s in candidate.seats if s.token == data["token"]), None)
                                if existing is not None and existing.socket is not None:
                                    raise ValueError("seat_online")
                                seat = existing
                            if seat is None:
                                raise ValueError("unavailable")
                            room = candidate
                        else:
                            if op == "join" and isinstance(code, str) and code not in rooms:
                                raise ValueError("room_missing")
                            raise ValueError("unavailable")
                        seat.profile = profile
                        seat.socket = ws
                        await ws.send_json(dict(type="seat", code=code, token=seat.token, side=room.seats.index(seat)))
                    elif live and op == "profile" and set(data) == {"type", "profile"}:
                        seat.profile = public_profile(data["profile"])
                        await room.broadcast(dict(type="members", members=room.members(), revision=room.revision))
                        continue
                    elif live and op in {"diagnostic", "applied"}:
                        fields = ({"duration_ms", "video_packets", "decoded", "submitted", "presented",
                                   "gui_gap_ms", "buffer_ms", "fps", "buffering", "native", "background",
                                   "decode_gap_ms", "gpu_submit_gap_ms", "paint_gap_ms",
                                   "rate_ppm", "device_rate_ppm", "progress_rate_ppm"}
                                  if op == "diagnostic" else {"at", "ok"})
                        if (data.keys() != fields | {"type"} or
                                any(type(data[k]) is not int or abs(data[k]) >= 2**53 for k in fields
                                    if not (k in {"device_rate_ppm", "progress_rate_ppm"} and data[k] is None))
                                or any(data[k] < 0 for k in fields - {"progress_rate_ppm", "device_rate_ppm"})):
                            raise ValueError("invalid")
                        if observations is not None:
                            print(json.dumps(dict(room=code, time_ms=now_ms(), side=room.seats.index(seat), **data)),
                                  file=observations, flush=True)
                        continue
                    elif live and op in {"frames", "reset", "manual"}:
                        if data.pop("revision", None) != room.revision:
                            continue
                        if op in {"reset", "manual"} and data.keys() == {"type"}:
                            room.live.reset(now_ms())
                            result = dict(type="measurement", lag=None, reset=True, manual=op == "manual")
                        elif op == "frames" and len(room.live.active) >= 2:
                            result = room.live.observe(room.seats.index(seat), data, now_ms())
                        else:
                            continue
                        if result is not None:
                            result["revision"] = room.revision
                            await room.broadcast(result)
                            if observations is not None:
                                print(json.dumps({"room": code, "time_ms": now_ms(), **result}),
                                      file=observations, flush=True)
                        continue
                    elif not live and op == "ready" and data.keys() == {"type", "marker"} and isinstance(data["marker"], str) and re.fullmatch("[0-9a-f]{128}", data["marker"]):
                        if len(room.seats) != 2 or any(s.socket is None for s in room.seats):
                            raise ValueError("wait_for_peer")
                        if room.start_ms is not None:
                            raise ValueError("already_started")
                        seat.marker = data["marker"]
                        if all(s.marker == seat.marker for s in room.seats):
                            room.start_ms = now_ms() + 2000
                    else:
                        raise ValueError("invalid")
                    await room.publish()
                except ValueError as error:
                    reason = str(error)
                    await ws.send_json(dict(type="error", reason=reason if reason in
                                            {"unavailable", "wait_for_peer", "already_started", "room_full",
                                             "room_missing", "host_offline", "seat_online"} else "invalid"))
                    if seat is None:
                        break
        except (TimeoutError, ConnectionError):
            pass
        finally:
            sockets.discard(ws)
            if seat is not None:
                seat.socket = None
                room.reset()
                await room.publish()
        return ws

    async def lifecycle(app):
        async def expire_empty():
            while True:
                await asyncio.sleep(30)
                for rooms in namespaces.values():
                    for code, room in list(rooms.items()):
                        if all(s.socket is None for s in room.seats) and time.monotonic() - room.last_change > 120:
                            del rooms[code]
        task = asyncio.create_task(expire_empty())
        yield
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    app = web.Application()
    app.cleanup_ctx.append(lifecycle)
    async def close_connections(app):
        await asyncio.gather(*(socket.close() for socket in list(sockets)), return_exceptions=True)

    app.on_shutdown.append(close_connections)
    app.add_routes([web.get("/health", health), web.get("/live", connect)])
    if page is not None:
        app.add_routes([web.get("/", index), web.get("/ws", connect)])
    return app
