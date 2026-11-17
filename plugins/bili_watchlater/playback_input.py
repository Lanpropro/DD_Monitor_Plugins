"""Playback key/click routing and application filter lifetime; no media state ownership."""
from __future__ import annotations
import time
from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtGui import QCursor, QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QAbstractSpinBox, QLineEdit, QPlainTextEdit, QTextEdit, QWidget

#: 跨屏事件（Qt6 的 ScreenChangeInternal）；缺失时退回 DPR 变化事件。
_SCREEN_CHANGE = getattr(QEvent.Type, "ScreenChangeInternal",
                         QEvent.Type.DevicePixelRatioChange)


class _PipClickFilter(QObject):
    """v1.0 需求：：多路小窗（PiP）里恢复单击暂停/播放（仅点播格）。

    宿主 ``PictureInPictureWindow`` 在 QApplication 上装了应用级过滤器，
    对 tile.video 的左键 Press grabMouse、Release/DblClick 全部消费，导致
    插件装在 tile.video 上的对象级过滤器收不到点击。应用级过滤器按「后装
    先执行」排序：本过滤器在 PiP 激活时才安装（晚于 PiP window），因此先
    于 PiP 看到事件。Press 不消费（保留 PiP 拖拽重排）；Release 时若未发生
    拖拽位移则排队切换暂停，但仍放行 Release，让宿主完整清理 gesture
    与鼠标抓取；插件不替宿主结束拖拽。
    """

    def __init__(self, plugin) -> None:
        super().__init__()
        self._plugin = plugin
        self._press = None  # (tile, QPoint)

    def eventFilter(self, watched, event):  # noqa: N802
        plugin = self._plugin
        if plugin._shutting_down:
            return False
        kind = event.type()
        # v1.0 需求：：组合画中画选择框出现时补齐点播格的 UP 头像。
        if kind == QEvent.Type.Show and \
                hasattr(watched, "checks") and hasattr(watched, "rooms"):
            try:
                plugin._fill_pip_choice_avatars(watched)
            except Exception:
                pass
            return False
        # 跨 DPI 白屏 → 纯黑遮罩盖住重载过程，不引入加载等待。
        # 事件可能落在浮窗顶层 / tile / video 任意一层，统一交给插件解析。
        if kind in (QEvent.Type.DevicePixelRatioChange, _SCREEN_CHANGE):
            try:
                plugin._on_dpi_event(watched)
            except Exception:
                pass
            return False
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.LeftButton:
            tile = self._vod_tile_of(watched)
            self._press = ((tile, event.globalPosition().toPoint())
                           if tile is not None else None)
            return False
        if kind == QEvent.Type.MouseMove and (event.buttons() & Qt.LeftButton):
            if self._press is not None:
                tile, start = self._press
                if (event.globalPosition().toPoint() - start).manhattanLength() \
                        >= QApplication.startDragDistance():
                    self._press = None  # 拖拽重排，交还 PiP
            return False
        if kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.LeftButton:
            press = self._press
            self._press = None
            if press is None:
                return False
            tile, start = press
            if (event.globalPosition().toPoint() - start).manhattanLength() \
                    >= QApplication.startDragDistance():
                return False  # 拖拽结束，交还 PiP 完成重排
            binding = plugin._binding_for_tile(tile)
            if binding is None or binding.state not in ("playing", "paused"):
                return False
            QTimer.singleShot(0, lambda t=tile, b=binding:
                              plugin._single_vod_click(t, b))
            return False  # 宿主必须收到 Release 来清理 gesture/reorder/grab
        return False

    def _vod_tile_of(self, watched):
        if getattr(watched, "room", None) is not None:
            return None
        try:
            parent = watched.parentWidget()
        except Exception:
            return None
        if parent is None or watched not in (
            getattr(parent, "video", None), getattr(parent, "pause_overlay", None),
            getattr(parent, "cover", None), getattr(parent, "spinner", None),
        ):
            return None
        room = getattr(parent, "room", None)
        if not isinstance(room, dict) or not room.get("watchlater_bvid"):
            return None
        return parent


class _VodKeyFilter(QObject):
    """应用级按键/跨屏过滤器：只接管点播格，不 patch 任何宿主类。

    宿主的全局快捷键是挂在主窗口上的 ``QShortcut(Qt.WindowShortcut)``：一旦
    全屏格窗口或画中画浮窗成为活动窗口，这些快捷键就不再派发（全屏左右键
    seek 失效、PiP 里 B 失效）；而空格落在全屏底栏里获得焦点的按钮上时会被
    当成「点一下」（表现为调画质）。这一层过滤器：

    * ``ShortcutOverride``：只对我们接管的键 accept，压掉宿主/插件自己的
      QShortcut，保证紧随其后的 KeyPress 一定先送到这一层；
    * ``KeyPress``：直接执行暂停/播放、±5 秒进度、弹幕开关，并吞掉事件，
      底栏按钮（尤其画质按钮）不会再被空格触发；
    * ``KeyRelease``：把本轮按住的键一并吞掉，避免 QAbstractButton 松开时
      补一次 click；
    * DPR / 换屏事件：交给插件的黑屏遮罩逻辑统一解析受影响格子。
    """

    def __init__(self, plugin) -> None:
        super().__init__()
        self._plugin = plugin

    def eventFilter(self, watched, event):  # noqa: N802 (Qt override)
        plugin = self._plugin
        if plugin._shutting_down:
            return False
        kind = event.type()
        if kind == QEvent.Type.ShortcutOverride:
            if plugin._vod_key_action(event, watched) is None:
                return False
            try:
                event.accept()
            except Exception:
                pass
            return True
        if kind == QEvent.Type.KeyPress:
            try:
                return plugin._handle_vod_key(event, watched)
            except Exception:
                return False
        if kind == QEvent.Type.KeyRelease:
            try:
                key = event.key()
                if key in plugin._key_down:
                    plugin._key_down.discard(key)
                    event.accept()
                    return True
            except Exception:
                pass
            return False
        if kind in (QEvent.Type.DevicePixelRatioChange, _SCREEN_CHANGE):
            try:
                plugin._on_dpi_event(watched)
            except Exception:
                pass
        return False



class PlaybackInput:
    def __init__(self, owner):
        self.owner = owner
        self.click_timers: dict[int, QTimer] = {}
        self.space_shortcut: QShortcut | None = None
        self.danmaku_shortcut: QShortcut | None = None
        self.seek_left: QShortcut | None = None
        self.seek_right: QShortcut | None = None
        self.seek_host = None
        self.danmaku_host = None
        self.key_filter: _VodKeyFilter | None = None
        self.key_down: set[int] = set()
        self.key_dedup: dict[str, float] = {}
        self.fullscreen_active = False
        self.space_host = None
        self.vod_press: dict[int, QPoint] = {}
        self.pip_filter: _PipClickFilter | None = None
        self.pip_active = False

    def stop_click_timers(self):
        for timer in list(self.click_timers.values()):
            try:
                timer.stop()
                timer.deleteLater()
            except RuntimeError:
                pass
        self.click_timers.clear()

    def dispose_shortcuts(self):
        if self.space_shortcut is not None:
            try:
                self.space_shortcut.setEnabled(False)
                self.space_shortcut.deleteLater()
            except RuntimeError:
                pass
            self.space_shortcut = None
        if self.danmaku_shortcut is not None:
            try:
                self.danmaku_shortcut.setEnabled(False)
                self.danmaku_shortcut.deleteLater()
            except RuntimeError:
                pass
            self.danmaku_shortcut = None
        for attr in ("seek_left", "seek_right"):
            sc = getattr(self, attr, None)
            if sc is not None:
                try:
                    sc.setEnabled(False)
                    sc.deleteLater()
                except RuntimeError:
                    pass
            setattr(self, attr, None)
        self.seek_host = None
        self.danmaku_host = None

    def remove_key_filter(self):
        if self.key_filter is not None:
            app = QApplication.instance()
            if app is not None:
                try:
                    app.removeEventFilter(self.key_filter)
                except Exception:
                    pass
            self.key_filter = None

    def sync_pip_filter(self) -> None:
        """v1.0 需求：：PiP 激活时安装应用级点击过滤器（晚于 PiP window
        安装 → 先于它执行），退出时移除。在节拍器里按状态变化收敛。"""
        window = self.owner._host_window()
        pip = getattr(window, "picture_in_picture", None)
        active_fn = getattr(pip, "active", None)
        active = False
        if callable(active_fn):
            try:
                active = bool(active_fn())
            except Exception:
                active = False
        active = active and bool(self.owner.playback.bindings) and not self.owner._shutting_down
        floating = getattr(pip, "floating", None) if active else None
        identity = id(floating) if floating is not None else active
        if active == self.pip_active and identity == getattr(self, "pip_identity", None):
            return
        self.pip_identity = identity
        self.pip_active = active
        # 画中画窗口此时才装上自己的应用级过滤器；我们的按键过滤器要重装
        # 一次，保持「后装先执行」，否则按键会先被浮窗过滤器吃掉。
        self.bump_key_filter()
        self.vod_press.clear()
        if self.pip_filter is not None:
            self.pip_filter._press = None
        app = QApplication.instance()
        if app is None:
            return
        if active:
            if self.pip_filter is None:
                self.pip_filter = _PipClickFilter(self.owner)
            try:
                app.installEventFilter(self.pip_filter)
            except Exception:
                pass
        else:
            if self.pip_filter is not None:
                try:
                    app.removeEventFilter(self.pip_filter)
                except Exception:
                    pass
            self.pip_filter = None

    def sync_shortcut_hosts(self, host) -> None:
        """四个兜底 QShortcut 跟随活动窗口（全屏窗口 / 画中画浮窗 / 主窗）。"""
        if host is None:
            return
        for attr, host_attr in (
            ("space_shortcut", "space_host"),
            ("seek_left", "seek_host"),
            ("seek_right", "seek_host"),
            ("danmaku_shortcut", "danmaku_host"),
        ):
            shortcut = getattr(self, attr, None)
            if shortcut is None or getattr(self, host_attr, None) is host:
                continue
            try:
                shortcut.setParent(host)
                setattr(self, host_attr, host)
            except Exception:
                pass

    def on_vod_click(self, tile, binding) -> None:
        """B 站语义（需求：）：单击=暂停/播放，双击=全屏。

        第一次左键释放起 250ms 判别窗；窗内第二次释放=双击，取消单击。
        拖拽结束（宿主已清 _press_pos）不会走到这里（过滤器已挡）。
        """
        key = id(tile)
        timer = self.click_timers.get(key)
        if timer is not None and timer.isActive():
            timer.stop()
            self.click_timers.pop(key, None)
            signal = getattr(tile, "fullscreenRequested", None)
            if signal is not None:
                try:
                    signal.emit(tile)
                except Exception:
                    pass
            return
        timer = QTimer()
        timer.setSingleShot(True)
        timer.setInterval(250)
        timer.timeout.connect(
            lambda t=tile, b=binding: self.single_vod_click(t, b)
        )
        self.click_timers[key] = timer
        timer.start()

    def single_vod_click(self, tile, binding) -> None:
        self.click_timers.pop(id(tile), None)
        if (self.owner._shutting_down or binding.tile is not tile
                or self.owner.playback.bindings.get(binding.bvid) is not binding):
            return
        if binding.state not in ("playing", "paused"):
            return
        signal = getattr(tile, "pauseToggled", None)
        if signal is not None:
            try:
                signal.emit(tile.room)
            except Exception:
                pass

    def ensure_space_shortcut(self) -> None:
        """空格暂停（需求：）：宿主从未绑定空格，插件补一个。

        WindowShortcut 上下文：只在宿主窗口（或全屏格子窗口）激活时生效；
        弹窗搜索框聚焦时天然不触发。处理器再检查输入控件焦点让路。
        """
        if self.space_shortcut is not None or self.owner._shutting_down:
            return
        window = self.owner._host_window()
        if window is None:
            return
        try:
            shortcut = QShortcut(QKeySequence(Qt.Key_Space), window)
            shortcut.setContext(Qt.WindowShortcut)
            shortcut.activated.connect(self.on_space)
            self.space_shortcut = shortcut
            self.space_host = window
        except Exception as error:
            self.owner._log("space shortcut failed type=" + type(error).__name__)

    def tile_under_cursor(self):
        """鼠标下的点播格：墙面 + 全屏窗口 + 画中画浮窗一起找。"""
        try:
            pos = QCursor.pos()
        except Exception:
            return None
        for tile in self.vod_tiles_anywhere():
            try:
                if tile.isVisible() and tile.rect().contains(tile.mapFromGlobal(pos)):
                    return tile
            except Exception:
                continue
        return None

    def bump_key_filter(self) -> None:
        """（重新）安装应用级过滤器，保证它比宿主 PiP 过滤器更晚装、更早执行。

        QApplication 的事件过滤器按「后装先执行」调用。画中画窗口是在插件
        加载后才创建并安装自己的应用级过滤器，所以每次形态切换都把本过滤器
        摘下再装一次，确保按键先到我们手里。
        """
        if self.owner._shutting_down:
            return
        app = QApplication.instance()
        if app is None:
            return
        if self.key_filter is None:
            self.key_filter = _VodKeyFilter(self.owner)
        try:
            app.removeEventFilter(self.key_filter)
        except Exception:
            pass
        try:
            app.installEventFilter(self.key_filter)
        except Exception as error:
            self.owner._log("key filter install failed type=" + type(error).__name__)

    def vod_tiles_anywhere(self):
        """当前所有点播格：主墙 + 全屏窗口 + 画中画浮窗（按 id 去重）。"""
        found: dict[int, object] = {}
        for tile in self.owner._host_tiles():
            try:
                room = getattr(tile, "room", None)
                if isinstance(room, dict) and room.get("watchlater_bvid"):
                    found[id(tile)] = tile
            except Exception:
                continue
        app = QApplication.instance()
        main = self.owner._host_window()
        if app is not None:
            for top in app.topLevelWidgets():
                if top is main:
                    continue
                try:
                    if not top.isVisible():
                        continue
                    children = top.findChildren(QWidget)
                except Exception:
                    continue
                for child in children:
                    try:
                        room = getattr(child, "room", None)
                        if (isinstance(room, dict) and room.get("watchlater_bvid")
                                and hasattr(child, "video") and hasattr(child, "bottom")):
                            found[id(child)] = child
                    except Exception:
                        continue
        return list(found.values())

    def any_tile_under_cursor(self):
        """鼠标下的任意格子（含直播格），用于画中画里的弹幕快捷键。"""
        try:
            pos = QCursor.pos()
        except Exception:
            return None
        candidates = list(self.owner._host_tiles())
        app = QApplication.instance()
        main = self.owner._host_window()
        if app is not None:
            for top in app.topLevelWidgets():
                if top is main:
                    continue
                try:
                    if not top.isVisible():
                        continue
                    for child in top.findChildren(QWidget):
                        if (hasattr(child, "room") and hasattr(child, "video")
                                and hasattr(child, "bottom")):
                            candidates.append(child)
                except Exception:
                    continue
        seen: set[int] = set()
        for tile in candidates:
            if tile is None or id(tile) in seen:
                continue
            seen.add(id(tile))
            try:
                if tile.isVisible() and tile.rect().contains(tile.mapFromGlobal(pos)):
                    return tile
            except Exception:
                continue
        return None

    def tile_in_pip_window(self, tile) -> bool:
        """这个格子是否真的被搬进了画中画浮窗（而不是主墙里的格子）。"""
        if tile is None:
            return False
        try:
            if bool(tile.property("pictureInPicture")):
                return True
        except Exception:
            pass
        try:
            pip = getattr(self.owner._host_window(), "picture_in_picture", None)
            floating = getattr(pip, "floating", None)
            if floating is not None and (
                tile.window() is floating or floating.isAncestorOf(tile)
            ):
                return True
        except Exception:
            pass
        return False

    def active_vod_tile(self):
        """当前应响应按键的点播格：全屏 > 鼠标下 > 活动浮窗里的格子。"""
        tiles = self.vod_tiles_anywhere()
        for tile in tiles:
            try:
                if bool(tile.property("fullscreen")) or (
                    tile.window() is not None and tile.window().isFullScreen()
                ):
                    return tile
            except Exception:
                continue
        tile = self.tile_under_cursor()
        if tile is not None:
            return tile
        try:
            active = QApplication.activeWindow()
        except Exception:
            active = None
        if active is not None and active is not self.owner._host_window():
            for candidate in tiles:
                try:
                    if candidate.window() is active:
                        return candidate
                except Exception:
                    continue
        return None

    def key_window_ok(self, watched=None) -> bool:
        """按键所在窗口必须是主窗口 / 全屏窗口 / 画中画浮窗之一。

        稍后再看弹窗（搜索框、按钮）或其它对话框里的同名按键一律放行，
        不能误把弹窗里的空格当成播放暂停。
        """
        try:
            active = QApplication.activeWindow()
        except Exception:
            active = None
        if active is None and isinstance(watched, QWidget):
            try:
                active = watched.window()
            except Exception:
                active = None
        if active is None:
            return False
        main = self.owner._host_window()
        if active is main:
            return True
        try:
            pip = getattr(main, "picture_in_picture", None)
            floating = getattr(pip, "floating", None)
            if floating is not None and (
                active is floating or floating.isAncestorOf(active)
            ):
                return True
        except Exception:
            pass
        for tile in self.vod_tiles_anywhere():
            try:
                if tile.window() is active or active is tile:
                    return True
            except Exception:
                continue
        return False

    def danmaku_key_text(self) -> str:
        window = self.owner._host_window()
        try:
            return str((getattr(window, "shortcuts", None) or {}).get(
                "video_danmaku") or "B")
        except Exception:
            return "B"

    def danmaku_combo(self):
        """宿主的弹幕快捷键（默认 B）拆成 (键码, 修饰键)。"""
        text = self.danmaku_key_text()
        try:
            seq = QKeySequence(text)
            if not seq.isEmpty():
                combo = seq[0]
                if hasattr(combo, "key"):
                    return int(combo.key()), combo.keyboardModifiers().value
                return int(combo) & 0x01FFFFFF, Qt.NoModifier.value
        except Exception:
            pass
        return int(Qt.Key_B), Qt.NoModifier.value

    def vod_key_action(self, event, watched=None):
        """本次按键是否该由插件接管；返回 (动作, 格子, 绑定) 或 None。"""
        try:
            key = int(event.key())
            modifiers = event.modifiers().value
        except Exception:
            return None
        danmaku_key, danmaku_mod = self.danmaku_combo()
        action = None
        if key in (int(Qt.Key_Space), int(Qt.Key_Left), int(Qt.Key_Right)):
            if modifiers & ~Qt.KeypadModifier.value:
                return None
            action = {
                int(Qt.Key_Space): "space",
                int(Qt.Key_Left): "left",
                int(Qt.Key_Right): "right",
            }[key]
        elif key == danmaku_key and modifiers == danmaku_mod:
            action = "danmaku"
        if action is None or not self.key_window_ok(watched):
            return None
        focus = QApplication.focusWidget()
        if focus is not None and isinstance(
            focus, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)
        ):
            return None  # 输入框里的空格/方向键是打字和移动光标
        if action == 'danmaku' and self.host_danmaku_shortcut_reachable(watched):
            return None
        tile = self.active_vod_tile()
        binding = self.owner._binding_for_tile(tile)
        if action == "danmaku":
            # 画中画里直播格也允许用宿主同款弹幕键（宿主快捷键挂在主窗口，
            # 浮窗成为活动窗口后收不到），只要它确实在浮窗内且有弹幕按钮。
            if tile is None or binding is None:
                tile = self.any_tile_under_cursor()
                binding = self.owner._binding_for_tile(tile)
                if (tile is None or not self.tile_in_pip_window(tile)
                        or getattr(tile, "danmaku_button", None) is None):
                    return None
        elif binding is None or binding.state not in ("playing", "paused"):
            return None
        return action, tile, binding

    def claim_key(self, name: str) -> bool:
        """同一次按键只允许一条链路（应用级过滤器 / QShortcut）处理一次。"""
        now = time.monotonic()
        last = self.key_dedup.get(name, 0.0)
        if now - last < 0.012:
            return False
        self.key_dedup[name] = now
        if len(self.key_dedup) > 16:
            for stale in [k for k, v in self.key_dedup.items()
                          if now - v > 1.0]:
                self.key_dedup.pop(stale, None)
        return True

    def handle_vod_key(self, event, watched=None) -> bool:
        info = self.vod_key_action(event, watched)
        if info is None:
            return False
        action, tile, binding = info
        try:
            self.key_down.add(int(event.key()))
        except Exception:
            pass
        if not self.claim_key(action):
            # 同一按键已被本插件另一条链路处理，仍然吞掉，别让按钮再响应。
            try:
                event.accept()
            except Exception:
                pass
            return True
        if action == "space":
            self.toggle_pause(tile, binding)
        elif action in ("left", "right"):
            step = -5.0 if action == "left" else 5.0
            self.owner._seek_binding_relative(binding, step)
        elif action == "danmaku":
            self.toggle_danmaku(tile, binding)
        try:
            event.accept()
        except Exception:
            pass
        return True

    def toggle_pause(self, tile, binding) -> None:
        if binding is None or binding.state not in ("playing", "paused"):
            return
        signal = getattr(tile, "pauseToggled", None)
        if signal is None and binding.tile is not None:
            tile = binding.tile
            signal = getattr(tile, "pauseToggled", None)
        if signal is None:
            return
        try:
            signal.emit(getattr(tile, "room", None))
        except Exception:
            pass

    def toggle_danmaku(self, tile, binding) -> None:
        button = getattr(tile, "danmaku_button", None)
        if button is None and binding is not None and binding.tile is not None:
            button = getattr(binding.tile, "danmaku_button", None)
        if button is None:
            return
        try:
            before = bool(button.isChecked())
        except Exception:
            return

        def finish() -> None:
            if self.owner._shutting_down:
                return
            try:
                if bool(button.isChecked()) == before:
                    button.toggle()   # 宿主这一路没处理 → 插件补上
            except RuntimeError:
                pass

        QTimer.singleShot(0, finish)

    def ensure_seek_shortcuts(self) -> None:
        """需求：：左右方向键控制视频进度（B 站网页语义：±5 秒，可按住连发）。

        只占左右方向键，不动上下（那是宿主的音量）。全屏时把快捷键挂到全屏
        窗口（和空格同一套 _sync 逻辑），输入框聚焦时不生效。
        """
        if self.seek_left is not None or self.owner._shutting_down:
            return
        window = self.owner._host_window()
        if window is None:
            return
        try:
            left = QShortcut(QKeySequence(Qt.Key_Left), window)
            left.setContext(Qt.WindowShortcut)
            left.activated.connect(lambda: self.seek_by_keys(-5.0))
            right = QShortcut(QKeySequence(Qt.Key_Right), window)
            right.setContext(Qt.WindowShortcut)
            right.activated.connect(lambda: self.seek_by_keys(5.0))
            self.seek_left, self.seek_right = left, right
            self.seek_host = window
        except Exception as error:
            self.owner._log("seek shortcut failed type=" + type(error).__name__)

    def seek_by_keys(self, delta: float, tile=None) -> None:
        """QShortcut 兜底链路（主窗口）；全屏/画中画由应用级过滤器直接调用。"""
        focus = QApplication.focusWidget()
        if focus is not None and isinstance(
            focus, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)
        ):
            return
        if not self.claim_key("left" if delta < 0 else "right"):
            return
        if tile is None:
            tile = self.active_vod_tile()
        binding = self.owner._binding_for_tile(tile)
        if binding is None or binding.state not in ("playing", "paused"):
            return
        self.owner._seek_binding_relative(binding, float(delta))

    def ensure_danmaku_shortcut(self) -> None:
        """The host owns this QShortcut; floating VOD windows use our key filter."""
        return

    def host_danmaku_shortcut_reachable(self, watched=None) -> bool:
        window = self.owner._host_window()
        target = watched.window() if isinstance(watched, QWidget) else QApplication.activeWindow()
        sequence = QKeySequence(self.danmaku_key_text())
        for shortcut in getattr(window, '_window_shortcuts', ()):
            try:
                if (shortcut.isEnabled() and shortcut.key() == sequence
                        and shortcut.parent().window() is target):
                    return True
            except RuntimeError:
                continue
        return False

    def on_danmaku_shortcut(self) -> None:
        focus = QApplication.focusWidget()
        if focus is not None and isinstance(
            focus, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)
        ):
            return
        if not self.claim_key("danmaku"):
            return
        tile = self.active_vod_tile()
        binding = self.owner._binding_for_tile(tile)
        if binding is None:
            return
        self.toggle_danmaku(tile, binding)

    def on_space(self) -> None:
        """空格兜底链路：无论鼠标在哪，全屏 / 画中画 / 墙面都只做暂停播放。"""
        if self.owner._shutting_down:
            return
        focus = QApplication.focusWidget()
        if focus is not None and isinstance(
            focus, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)
        ):
            return  # 输入框里的空格是打字，不是暂停
        if not self.claim_key("space"):
            return
        tile = self.active_vod_tile()
        binding = self.owner._binding_for_tile(tile)
        if binding is None or binding.state not in ("playing", "paused"):
            return
        self.toggle_pause(tile, binding)
