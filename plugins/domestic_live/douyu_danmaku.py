"""Douyu receive identity and receipt-confirmed own messages on the GUI thread."""
import asyncio
import time
from collections import deque

from PySide6.QtCore import QObject, Signal

from ddm.live_danmaku import (LiveDanmakuClient, douyu_packet, douyu_messages,
                              DOUYU_HEARTBEAT, DOUYU_RECEIVE_TIMEOUT)
from .douyu_interaction import packet, web_cookies


class ChatRelay(QObject):
    confirmed = Signal(str, object)


def relay_for(platform):
    if not hasattr(platform, "_douyu_chat_relay"):
        platform._douyu_chat_relay = ChatRelay()
    return platform._douyu_chat_relay


class DouyuDanmakuClient(LiveDanmakuClient):
    broadcast = Signal(dict)

    def __init__(self, room_id, platform, parent=None):
        super().__init__(room_id, platform, parent)
        self.canonical_room = self.room_id
        self._broadcasts = deque(maxlen=256)
        self._receipts = deque(maxlen=256)
        self._own_uid = ""
        self._identity_deadline = 0.0
        self.broadcast.connect(self.receive_broadcast)
        relay_for(platform).confirmed.connect(self.receive_confirmed)

    def _match(self, recent, uid, text):
        now = time.monotonic()
        while recent and now - recent[0][0] > 30:
            recent.popleft()
        for item in recent:
            if item[1:] == (uid, text):
                recent.remove(item)
                return True
        return False

    def receive_broadcast(self, data):
        if self._stopped.is_set():
            return
        uid, text = str(data.get("uid") or ""), str(data.get("txt") or "")
        if not text:
            return
        if uid and self._match(self._receipts, uid, text):
            return
        # Parsing (and on saved sessions decrypting) cookies for every chat
        # blocks the GUI during bursts; identity alone may lag by at most 1s.
        now = time.monotonic()
        if now >= self._identity_deadline:
            cookies = web_cookies(self.platform._playback_cookies(), self.room_id)
            self._own_uid = cookies.get("acf_uid", "")
            self._identity_deadline = now + 1.0
        own = bool(uid) and uid == self._own_uid
        self._broadcasts.append((time.monotonic(), uid, text))
        self.message.emit({"kind": "danmaku", "uid": uid, "uname": data.get("nn", ""),
                           "text": text, "self_sent": own, "delivery": "broadcast"})

    def receive_confirmed(self, room, receipt):
        if self._stopped.is_set() or room not in (self.room_id, self.canonical_room):
            return
        if self._match(self._broadcasts, receipt.uid, receipt.text):
            return
        self._receipts.append((time.monotonic(), receipt.uid, receipt.text))
        self.message.emit({"kind": "danmaku", "uid": receipt.uid, "uname": receipt.uname,
                           "text": receipt.text, "self_sent": True, "delivery": "send_receipt"})

    # Same public subscription as the host, retaining UID before normalisation.
    async def _douyu(self, retries=0):
        # The public URL can be a vanity number; subscription requires the actual room.
        info = await asyncio.to_thread(self.platform.room_info, self.room_id)
        if self._stopped.is_set():
            return
        raw = self.platform.normalize(info.room_id).split(":", 1)[1]
        self.canonical_room = "douyu:" + raw
        reader, writer = await asyncio.wait_for(asyncio.open_connection(
            "danmuproxy.douyu.com", 8601 + retries % 2), 5)
        try:
            writer.write(douyu_packet(f"type@=loginreq/roomid@={raw}/"))
            await writer.drain()
            buffer = bytearray()
            authenticated = False
            heartbeat = asyncio.get_running_loop().time()
            last_received = heartbeat
            while not self._stopped.is_set():
                now = asyncio.get_running_loop().time()
                if now >= heartbeat:
                    writer.write(douyu_packet("type@=mrkl/"))
                    await writer.drain()
                    heartbeat = now + DOUYU_HEARTBEAT
                try:
                    deadline = last_received + (DOUYU_RECEIVE_TIMEOUT if authenticated else 5)
                    chunk = await asyncio.wait_for(reader.read(65536), max(.01, min(heartbeat, deadline) - now))
                except asyncio.TimeoutError:
                    if not authenticated or asyncio.get_running_loop().time() - last_received >= DOUYU_RECEIVE_TIMEOUT:
                        raise ValueError("Douyu response timeout")
                    continue
                if not chunk:
                    raise OSError("Douyu connection closed")
                last_received = asyncio.get_running_loop().time()
                buffer.extend(chunk)
                for data in douyu_messages(buffer):
                    if data.get("type") == "loginres":
                        if data.get("error", "0") != "0":
                            raise ValueError("Douyu login rejected")
                        writer.write(douyu_packet(f"type@=joingroup/rid@={raw}/gid@=-9999/"))
                        await writer.drain()
                        authenticated = True
                        self._connected = True
                        self.status.emit("已连接")
                    elif data.get("type") == "pingreq":
                        # Server probes are independent of our periodic mrkl.
                        writer.write(packet({"type": "pingres", "tick": data.get("tick", "")}))
                        await writer.drain()
                    elif data.get("type") == "error":
                        raise ValueError("Douyu server rejected subscription")
                    elif data.get("type") == "chatmsg" and data.get("txt"):
                        self.broadcast.emit(data)
        finally:
            writer.close()
            await writer.wait_closed()
