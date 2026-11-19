"""PiP cross-screen cover scheduling; playback bindings remain with the coordinator."""
from __future__ import annotations
import time
from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtWidgets import QApplication, QWidget


class PipDpiController:
    def __init__(self, owner, is_deleted):
        self.owner = owner
        self.is_deleted = is_deleted
        #: v1.0 需求：：PiP 跨 DPI 时的黑色遮罩（按 tile 复用，避免反复建窗）。
        self.covers = {}
        self.cover_timers: dict[int, QTimer] = {}
        self.cover_deadline: dict[int, float] = {}
        self.pending = False
        self.handling = False
        self.ignore_until = 0.0
        self.flush_timer: QTimer | None = None

    def stop_flush(self):
        if self.flush_timer is not None:
            self.flush_timer.stop()
            self.flush_timer.deleteLater()
            self.flush_timer = None
        self.pending = False

    def dispose_covers(self):
        for timer in self.cover_timers.values():
            try:
                timer.stop()
                timer.deleteLater()
            except RuntimeError:
                pass
        self.cover_timers.clear()
        self.cover_deadline.clear()
        for cover in self.covers.values():
            try:
                cover.hide()
                cover.deleteLater()
            except RuntimeError:
                pass
        self.covers.clear()

    def on_dpi_event(self, watched) -> None:
        """跨 DPI / 换屏事件入口：只登记，绝不在事件派发栈里做重活。

        换屏时 Qt 会给窗口树里几乎每个 QObject 投递 ScreenChangeInternal /
        DevicePixelRatioChange，插件的三个事件过滤器会收到成百上千次。旧实现
        在每次事件里 ``win.findChildren(QWidget)`` 全树扫描并同步创建 / show
        原生黑遮罩，遮罩自身又产生同类事件 → 事件风暴与重入；GUI 线程长时间
        停在扫描循环里（日志 faulthandler 反复抓到 plugin.py 的 getattr 行），
        宿主看门狗判为卡死，Windows 侧还可能 native access violation。

        现在无论事件落在浮窗顶层、QWindow、tile 还是 video，都只做一次合并：
        120ms 后的单次定时器里，仅从插件自己维护的 ``_bindings`` 取点播格
        （不再扫控件树），统一盖遮罩 + 排队几何同步。处理期间与之后 350ms
        的重入/冷却事件一律忽略，从根上掐断自激循环。
        """
        if (self.owner._shutting_down or self.handling
                or not self.owner.playback.bindings):
            return
        if self.pip_drag_active():
            # 拖动浮窗时 Move/DPR 事件风暴会让 900ms 遮罩反复续期 → 黑屏。
            # 拖动期间不盖、已盖的立即撤掉，并给一小段冷却吞掉松手后的
            # 残余事件；真正的跨 DPI 变化（非拖动）仍走原来的保护逻辑。
            self.hide_all_dpi_covers()
            self.pending = False
            try:
                timer = self.flush_timer
                if timer is not None and not self.is_deleted(timer):
                    timer.stop()
            except Exception:
                pass
            self.ignore_until = time.monotonic() + 0.25
            return
        # 自己创建的原生黑遮罩（及其 windowHandle）引发的同类事件不再排队。
        try:
            if getattr(watched, "objectName", lambda: "")() == "watchlaterDpiCover":
                return
        except Exception:
            pass
        now = time.monotonic()
        if now < self.ignore_until or self.pending:
            return
        self.pending = True
        try:
            timer = self.flush_timer
            if timer is None or self.is_deleted(timer):
                timer = QTimer()          # Plugin 不是 QObject，不能用 self 作 parent
                timer.setSingleShot(True)
                timer.timeout.connect(self.flush_dpi_event)
                self.flush_timer = timer
            timer.stop()
            timer.start(120)
        except Exception:
            self.pending = False

    def flush_dpi_event(self) -> None:
        """合并后的跨屏处理：盖遮罩 + 对真实点播格排队一次几何同步。"""
        self.pending = False
        if self.owner._shutting_down or not self.owner.playback.bindings:
            return
        if self.pip_drag_active():
            self.hide_all_dpi_covers()
            return
        self.handling = True
        try:
            # 绑定表就是插件自己的「点播格清单」：主墙 / 全屏 / 画中画的
            # tile 都在里面，不需要（也不允许）遍历整个宿主控件树。
            vod_tiles = []
            for binding in list(self.owner.playback.bindings.values()):
                tile = binding.tile
                if tile is None:
                    continue
                try:
                    if not self.is_deleted(tile):
                        vod_tiles.append(tile)
                except Exception:
                    continue
            # 黑遮罩只盖点播格；直播格绝不盖。
            for tile in vod_tiles:
                try:
                    self.cover_pip_dpi(tile)
                except Exception:
                    continue
            # 几何/浮层同步保留原来的覆盖面（所有格子），但合并成对真实
            # tile 各排一次，不给 video / badge / overlay 等子控件各排一次。
            try:
                all_tiles = self.owner._host_tiles()
            except Exception:
                all_tiles = []
            seen: set[int] = set()
            for tile in vod_tiles + all_tiles:
                if tile is None or id(tile) in seen:
                    continue
                seen.add(id(tile))
                try:
                    if not self.is_deleted(tile):
                        self.owner._queue_tile_sync(tile)
                except Exception:
                    continue
        finally:
            self.handling = False
            # show()/raise_()/重排触发的后续事件（含异步投递的）全部落进冷却。
            self.ignore_until = time.monotonic() + 0.35

    def pip_drag_active(self) -> bool:
        """浮窗是否正在被鼠标拖动（左键按住移动）。"""
        try:
            if QApplication.mouseButtons() & Qt.LeftButton:
                return True
        except Exception:
            pass
        return False

    def hide_all_dpi_covers(self) -> None:
        """拖动开始 / 进行中：立刻撤掉所有黑遮罩并停掉续期定时器。

        旧逻辑每来一次 DPI / Move 事件就 ``timer.start(900)`` 续期，拖动时
        事件连续不断，遮罩因此一直不 hide，纯黑 QWidget 盖在视频上就是
        「拖动浮窗时视频黑屏」。
        """
        for cover in list(self.covers.values()):
            try:
                if not self.is_deleted(cover):
                    cover.hide()
            except Exception:
                pass
        for timer in list(self.cover_timers.values()):
            try:
                if not self.is_deleted(timer):
                    timer.stop()
            except Exception:
                pass
        self.cover_deadline.clear()

    def hide_dpi_cover(self, tile) -> None:
        cover = self.covers.get(id(tile))
        if cover is None:
            return
        try:
            if self.is_deleted(cover):
                return
            cover.hide()
        except Exception:
            pass

    def raise_dpi_cover(self, tile, cover) -> None:
        try:
            if self.owner._shutting_down or self.is_deleted(cover) or cover.isHidden():
                return
            if self.covers.get(id(tile)) is not cover:
                return
            cover.raise_()
        except Exception:
            pass

    def cover_pip_dpi(self, tile) -> None:
        """跨分辨率屏的白屏重载期间盖一层纯黑（只盖画中画里的点播格）。"""
        if tile is None or self.owner._shutting_down:
            return
        if self.pip_drag_active():
            self.hide_dpi_cover(tile)
            return
        try:
            in_pip = self.owner._tile_in_pip_window(tile) or (
                self.owner._tile_in_pip(tile)
                and tile.window() is not self.owner._host_window())
        except Exception:
            return
        if not in_pip:
            return
        try:
            cover = self.covers.get(id(tile))
            if cover is None or self.is_deleted(cover):
                cover = QWidget(tile)
                cover.setObjectName("watchlaterDpiCover")
                cover.setAttribute(Qt.WA_NativeWindow, True)
                cover.setStyleSheet(
                    "QWidget#watchlaterDpiCover { background: #000000; }")
                cover.setFocusPolicy(Qt.NoFocus)
                cover.setAttribute(Qt.WA_TransparentForMouseEvents, True)
                cover.hide()
                self.covers[id(tile)] = cover
            video = getattr(tile, "video", None)
            rect = tile.rect()
            if video is not None and not self.is_deleted(video):
                try:
                    box = video.geometry()
                    if video.parentWidget() is tile:
                        rect = QRect(box)
                    else:
                        rect = QRect(video.mapTo(tile, QPoint(0, 0)), box.size())
                except Exception:
                    rect = tile.rect()
            cover.setGeometry(rect)
            cover.show()
            cover.raise_()
            # 原生视频窗口重绘时可能重新盖到遮罩之上，前 600ms 分几次抬起。
            now = time.monotonic()
            if now >= self.cover_deadline.get(id(tile), 0.0) - 0.3:
                self.cover_deadline[id(tile)] = now + 0.9
                for delay in (60, 180, 360, 600):
                    QTimer.singleShot(
                        delay,
                        lambda c=cover, t=tile: self.raise_dpi_cover(t, c),
                    )
            timer = self.cover_timers.get(id(tile))
            if timer is None or self.is_deleted(timer):
                timer = QTimer()
                timer.setSingleShot(True)
                timer.timeout.connect(lambda t=tile: self.hide_dpi_cover(t))
                self.cover_timers[id(tile)] = timer
            timer.start(900)   # 重复事件自动续期，直到这一轮重载结束
        except Exception:
            return
