"""Chat clients. Existing component ownership and lifecycle are preserved."""
from __future__ import annotations

import asyncio
import http.cookies
import threading
from PySide6.QtCore import QObject
from .media_signals import Events
from ddm import bili, danmaku


class PlatformChat(QObject):
    """Keep the host platform's QThread alive until it finishes."""

    def __init__(self, room_id, platform, parent):
        super().__init__(parent)
        self.events = Events()
        self.stopped = False
        self.unavailable = "此平台暂不支持弹幕，直播画面与声音可正常播放"
        try:
            self.client = platform.danmaku_client(room_id, self)
        except (ImportError, AttributeError):
            self.client = None
            self.unavailable = "平台弹幕组件不可用，请更新支持多平台弹幕的主程序"
        if self.client is not None:
            self.client.status.connect(self.events.state.emit)
            self.client.message.connect(self.events.message.emit)
            self.client.finished.connect(self._finished)

    def _finished(self):
        client, self.client = self.client, None
        client.deleteLater()
        if self.stopped:
            self.deleteLater()

    def start(self):
        if self.client is None:
            self.events.state.emit(self.unavailable)
        else:
            self.client.start()

    def stop(self):
        self.stopped = True
        if self.client is not None:
            self.client.stop()
            if self.client.isRunning():
                self.client.wait(2000)
        else:
            self.deleteLater()


class Chat:
    """Reuse the host's Bilibili protocol in a plain Python thread, not a QThread."""

    def __init__(self, room_id: str):
        self.room_id = room_id
        self.events = Events()
        self.cancelled = threading.Event()
        self.loop = None
        self.task = None
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._run,
                                       name=f"match-sync-chat-{self.room_id}", daemon=True)
        self.thread.start()

    def stop(self):
        self.cancelled.set()
        loop, task = self.loop, self.task
        if loop is not None and task is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass

    def _run(self):
        if danmaku.blivedm is None:
            self.events.state.emit("弹幕组件不可用")
            return
        while not self.cancelled.is_set():
            try:
                asyncio.run(self._main())
            except asyncio.CancelledError:
                return
            except Exception:
                if not self.cancelled.is_set():
                    self.events.state.emit("弹幕连接失败，正在重试")
            if self.cancelled.wait(2):
                return

    async def _main(self):
        self.loop = asyncio.get_running_loop()
        self.task = asyncio.current_task()
        if self.cancelled.is_set():
            return
        cookies = http.cookies.SimpleCookie()
        if bili.SESSION_DATA:
            cookies["SESSDATA"] = bili.SESSION_DATA
            cookies["SESSDATA"]["domain"] = "bilibili.com"
        self.events.state.emit("弹幕连接中…")
        async with danmaku.aiohttp.ClientSession(
                timeout=danmaku.aiohttp.ClientTimeout(total=None, connect=15)) as session:
            session.cookie_jar.update_cookies(cookies)
            real_id, token, hosts = await asyncio.to_thread(bili.danmaku_conf, self.room_id)
            if self.cancelled.is_set():
                return
            if not hosts:
                self.events.state.emit("无法取得弹幕服务器")
                return
            client = danmaku._Client(real_id or int(self.room_id), session=session,
                                     hosts=hosts, token=token, on_status=self.events.state.emit)
            client.set_handler(danmaku._Handler(self.events.message.emit))
            client.start()
            try:
                await client.join()
            finally:
                await client.stop_and_close()
