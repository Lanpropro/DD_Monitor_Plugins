"""Read ordinary chat from the official website's public guest subscription."""
import asyncio
from itertools import count
import json
import re
import struct
from threading import Event
from uuid import uuid4

import aiohttp
from PySide6.QtCore import QThread, Signal

from .platform import ORIGIN, USER_AGENT


DISCOVERY_URL = 'https://p7api.qiecdn.com/api/v1/comet/list'
NODE = re.compile(r'(?:[a-z0-9-]+\.)+qiecdn\.com', re.IGNORECASE)
HEADER = struct.Struct('!IHHII')
HEARTBEAT_SECONDS = 30
OP_HEARTBEAT = 2
OP_SUBSCRIBE = 7
OP_SUBSCRIBED = 8
OP_BATCH = 9
OP_DANMAKU = 2000


def packet(operation, sequence, data=b''):
    return HEADER.pack(HEADER.size + len(data), HEADER.size, 1, operation, sequence) + data


def _frames(data):
    offset = 0
    while offset < len(data):
        if len(data) - offset < HEADER.size:
            raise ValueError('Incomplete Qie frame')
        size, header_size, _version, operation, _sequence = HEADER.unpack_from(data, offset)
        if not HEADER.size <= header_size <= size <= len(data) - offset:
            raise ValueError('Invalid Qie frame length')
        yield operation, data[offset + header_size:offset + size]
        offset += size


def frames(data):
    for operation, body in _frames(data):
        if operation == OP_BATCH:
            yield from _frames(body)
        else:
            yield operation, body


def chat_event(body):
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    message, user = payload.get('msg'), payload.get('from')
    if not isinstance(message, dict) or not isinstance(user, dict):
        return None
    text = message.get('content')
    if not isinstance(text, str) or not text.strip():
        return None
    event = {'kind': 'danmaku', 'text': text, 'uid': str(user.get('u') or ''),
             'uname': str(user.get('n') or '')}
    color = message.get('color')
    if isinstance(color, str) and re.fullmatch(r'#?[0-9a-fA-F]{6}', color):
        event['color'] = '#' + color.lstrip('#')
    return event


class QieDanmakuClient(QThread):
    message = Signal(dict)
    status = Signal(str)

    def __init__(self, number, parent=None):
        super().__init__(parent)
        self.number = number
        self._guest_id = uuid4().hex
        self._stopped = Event()
        self._runner = None

    def stop(self):
        self._stopped.set()
        runner = self._runner
        if runner is not None:
            loop, task = runner
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass

    def run(self):
        try:
            asyncio.run(self._main())
        except asyncio.CancelledError:
            pass

    async def _main(self):
        loop = asyncio.get_running_loop()
        self._runner = (loop, asyncio.current_task())
        try:
            headers = {'User-Agent': USER_AGENT, 'Referer': ORIGIN + self.number}
            async with aiohttp.ClientSession(headers=headers, trust_env=True,
                                             timeout=aiohttp.ClientTimeout(total=12, connect=5)) as session:
                for attempt in count():
                    if self._stopped.is_set():
                        return
                    self.status.emit('连接中…' if attempt == 0 else '弹幕重连中…')
                    try:
                        await self._subscribe(session, attempt)
                    except PermissionError:
                        self.status.emit('企鹅体育暂未开放此房间的访客弹幕')
                        return
                    except (aiohttp.ClientError, OSError, ValueError):
                        if not self._stopped.is_set():
                            self.status.emit('弹幕连接中断，准备重连…')
                    if not self._stopped.is_set():
                        await asyncio.sleep(min(2 + attempt, 10))
        finally:
            self._runner = None

    async def _subscribe(self, session, attempt):
        async with session.get(DISCOVERY_URL, params={'client_type': 'pc_web'}) as response:
            response.raise_for_status()
            discovery = await response.json(content_type=None)
        published = discovery.get('nodes') if isinstance(discovery, dict) else None
        if not isinstance(published, list):
            raise ValueError('Invalid Qie chat discovery')
        nodes = [node for node in published if isinstance(node, str) and NODE.fullmatch(node)]
        if not nodes:
            raise ValueError('Qie did not return a published chat node')
        async with session.ws_connect('wss://' + nodes[attempt % len(nodes)] + '/sub',
                                      origin=ORIGIN.rstrip('/'), max_msg_size=1024 * 1024) as socket:
            await self._receive(socket)

    async def _receive(self, socket):
        sequence = count()
        guest = {'uid': 0, 'token': '', 'roomId': 'online://' + self.number,
                 'deviceId': self._guest_id, 'platform': 'pc_web', 'unAccepts': []}
        await socket.send_bytes(packet(OP_SUBSCRIBE, next(sequence), json.dumps(guest).encode('utf-8')))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 8
        next_heartbeat = deadline
        connected = False
        while not self._stopped.is_set():
            now = loop.time()
            if now >= deadline:
                raise TimeoutError('Qie chat service stopped responding')
            if connected and now >= next_heartbeat:
                await socket.send_bytes(packet(OP_HEARTBEAT, next(sequence)))
                next_heartbeat = now + HEARTBEAT_SECONDS
            try:
                received = await socket.receive(timeout=min(next_heartbeat, deadline) - now)
            except TimeoutError:
                continue
            if received.type != aiohttp.WSMsgType.BINARY:
                raise OSError('Qie websocket closed')
            if connected:
                deadline = loop.time() + 2 * HEARTBEAT_SECONDS + 5
            for operation, body in frames(received.data):
                if operation == OP_SUBSCRIBED and not connected:
                    reply = json.loads(body)
                    if not isinstance(reply, dict) or str(reply.get('status')) != '0':
                        raise PermissionError('Qie did not accept a guest subscription')
                    connected = True
                    deadline = loop.time() + 2 * HEARTBEAT_SECONDS + 5
                    next_heartbeat = loop.time()
                    self.status.emit('已连接')
                elif connected and operation == OP_DANMAKU and (event := chat_event(body)):
                    self.message.emit(event)
