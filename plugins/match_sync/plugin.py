"""Package entry point loaded by DD Monitor CE's single-file plugin loader."""
import os
import time
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QEvent, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QStyle, QStyleOptionToolButton, QToolButton, QWidget

from ddm import plugins as api
from ddm import motion
from ddm import config as config_module

# Keep sibling imports local to this plugin, including in the frozen EXE.
__path__ = [os.path.dirname(__file__)]
__package__ = __name__

from .viewer import Viewer
from .diagnostics import SwitchProbe


class EntryStretch(QWidget):
    """Paint the button's transition without retaining its old layout space."""
    def __init__(self, entry, width):
        super().__init__(entry.parentWidget())
        self.entry = entry
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setGeometry(entry.x(), entry.y(), max(entry.width(), round(width)), entry.height())

    def paintEvent(self, event):
        self.entry.sync_transition()
        if self.entry._paint_width is not None:
            painter = QPainter(self)
            self.entry._paint_button(painter, self.entry._paint_width)
            painter.end()
            self.entry._feedback_painted()


class EntryButton(QToolButton):
    """Use the original horizontal entry and a separate portrait icon."""
    feedback_painted = Signal()
    transition_finished = Signal()

    def __init__(self, sidebar, action):
        super().__init__(sidebar)
        self.sidebar = sidebar
        self._top = None
        self._mode = False
        self._paint_width = None
        self._transition = None
        self._overlay = None
        self._feedback_pending = False
        self._stretch = QVariantAnimation(self)
        self._stretch.setDuration(160)
        self._stretch.setEasingCurve(getattr(motion, 'THEME_WAVE_EASING', QEasingCurve.OutCubic))
        self._stretch.valueChanged.connect(self._stretch_frame)
        self._stretch.finished.connect(self._stretch_finished)
        self.setDefaultAction(action)
        action.setToolTip("比赛二路：开启或关闭比赛二路模式")
        self.setAccessibleName("比赛二路")
        pixmap = QPixmap(36, 36)
        pixmap.setDevicePixelRatio(2)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#dde3eb"), 1.5))
        painter.drawRoundedRect(2, 3, 8, 12, 2, 2)
        painter.drawRoundedRect(12, 6, 4, 9, 1, 1)
        painter.end()
        action.setIcon(QIcon(pixmap))
        self.setIconSize(QSize(18, 18))
        self._placement = QTimer(self)
        self._placement.setSingleShot(True)
        self._placement.timeout.connect(self._place)
        self._set_side_original = getattr(sidebar, "set_side", None)
        if self._set_side_original is not None:
            sidebar.set_side = self._set_side
        sidebar.installEventFilter(self)
        sidebar.layout_button.installEventFilter(self)
        self._place()

    def set_mode(self, active):
        if self._mode == bool(active):
            return
        self.sync_transition()
        old_width = self._paint_width if self._paint_width is not None else self.width()
        old_parent = self.parentWidget()
        self._clear_transition()
        self._mode = bool(active)
        self._place()
        self.parentWidget().layout().activate()
        target = self.width()
        if (self.isVisible() and motion.enabled() and target != old_width
                and self.parentWidget() is old_parent):
            self._transition = (old_width, target, time.perf_counter())
            self._paint_width = round(old_width)
            self._overlay = EntryStretch(self, max(old_width, target))
            self._overlay.show()
            self._overlay.raise_()
            self._stretch.setStartValue(0.)
            self._stretch.setEndValue(1.)
            self._stretch.start()
        self.update()

    def sync_transition(self):
        if self._transition is None:
            return
        start, target, started = self._transition
        progress = min(1., max(0., (time.perf_counter() - started) * 1000 / self._stretch.duration()))
        if progress >= 1.:
            self._stretch_finished()
        else:
            eased = self._stretch.easingCurve().valueForProgress(progress)
            self._paint_width = round(start + (target - start) * eased)

    def _stretch_frame(self, _value):
        self.sync_transition()
        if self._overlay is not None:
            self._overlay.update()
        self.update()

    def _clear_transition(self):
        self._stretch.stop()
        self._transition = None
        self._paint_width = None
        if self._overlay is not None:
            self._overlay.hide()
            self._overlay.deleteLater()
            self._overlay = None

    def _stretch_finished(self):
        active = self._transition is not None
        self._clear_transition()
        self.update()
        if active:
            self.transition_finished.emit()

    def _paint_button(self, painter, width):
        option = QStyleOptionToolButton()
        self.initStyleOption(option)
        option.rect.setWidth(width)
        painter.setClipRect(option.rect)
        self.style().drawComplexControl(QStyle.CC_ToolButton, option, painter, self)

    def paintEvent(self, event):
        self.sync_transition()
        if self._overlay is None:
            super().paintEvent(event)
            self._feedback_painted()

    def _feedback_painted(self):
        if self._feedback_pending:
            self._feedback_pending = False
            self.feedback_painted.emit()

    def _set_side(self, side):
        self._stretch.stop()
        self._stretch_finished()
        # The host assumes two tool buttons while constructing its portrait row.
        # Remove only our entry before that construction, then place it afterward.
        if side == "top" and self.parentWidget() is self.sidebar.tool_row:
            self.sidebar.tool_row.layout().removeWidget(self)
            self.setParent(self.sidebar)
        self._set_side_original(side)
        self._place()

    def eventFilter(self, watched, event):
        if watched is self.sidebar.layout_button and event.type() == QEvent.Show and self._mode:
            watched.hide()
        if watched is self.sidebar and event.type() in (
                QEvent.LayoutRequest, QEvent.Resize, QEvent.Show):
            if event.type() == QEvent.Resize:
                self._stretch_finished()
            self._placement.start(0)
        return super().eventFilter(watched, event)

    def hideEvent(self, event):
        self._stretch_finished()
        super().hideEvent(event)

    def _place(self):
        sidebar = self.sidebar
        occupied = self._mode
        top = getattr(sidebar, "side", "left") == "top" and not occupied
        sidebar.layout_button.setVisible(not occupied)
        if top:
            parent = sidebar._bar_row
            box = sidebar._bar_row_box
            anchor = sidebar.toggle_button
        else:
            parent = sidebar.tool_row
            box = parent.layout()
            anchor = None
        if self._top != top:
            self._top = top
            self.setObjectName("BarIcon" if top else "IconButton")
            self.setToolButtonStyle(Qt.ToolButtonIconOnly if top else Qt.ToolButtonTextOnly)
            self.setMinimumSize(QSize(30, 30) if top else QSize(0, 0))
            self.setMaximumSize(QSize(30, 30) if top else QSize(16777215, 16777215))
            self.setSizePolicy(QSizePolicy.Fixed if top else QSizePolicy.Preferred,
                               QSizePolicy.Fixed)
            self.style().unpolish(self)
            self.style().polish(self)
        index = box.indexOf(anchor) if top else 0
        if self.parentWidget() is not parent or box.indexOf(self) != index - (1 if top else 0):
            old = self.parentWidget().layout()
            if old is not None:
                old.removeWidget(self)
            self.setParent(parent)
            box.insertWidget(box.indexOf(anchor) if top else 0, self)
        self.setVisible(top or not getattr(sidebar, "collapsed", False))
        if not top:
            box.setStretch(box.indexOf(self), int(occupied))

    def detach(self):
        if self._set_side_original is not None and self.sidebar.set_side == self._set_side:
            self.sidebar.set_side = self._set_side_original
        self.sidebar.removeEventFilter(self)
        self.sidebar.layout_button.removeEventFilter(self)
        self.sidebar.layout_button.show()
        self._clear_transition()
        self._placement.stop()
        if self.parentWidget().layout() is not None:
            self.parentWidget().layout().removeWidget(self)
        self.hide()
        self.deleteLater()


class MatchSyncPlugin(api.Plugin):
    def __init__(self):
        super().__init__()
        self.sources = {}
        self.viewer = None
        self.entry = None
        self.button = None
        self._pending_enabled = None
        self._applying = False
        self.switch_probe = None
        self._switch_timer = None

    def on_load(self, context):
        self.context = context
        host = context.window
        self.switch_probe = SwitchProbe(host, Path(config_module.REPO) / 'logs' / 'match-switch.jsonl')
        self._switch_timer = QTimer(host)
        self._switch_timer.setSingleShot(True)
        self._switch_timer.timeout.connect(self._apply_enabled)
        sidebar = getattr(host, "sidebar", None)
        if hasattr(host, "_content") and hasattr(sidebar, "tool_row"):
            self.button = QAction("比赛二路", host)
            self.button.setCheckable(True)
            self.button.toggled.connect(self.set_enabled)
            self.entry = EntryButton(sidebar, self.button)
            self.entry.feedback_painted.connect(self._entry_painted)
            self.entry._stretch.valueChanged.connect(self.switch_probe.animation_tick)
            self.entry.transition_finished.connect(self.switch_probe.animation_finished)
        context.log("比赛二路同步已就绪；点击关注栏「比赛二路」入口开启")

    def on_event(self, event, payload):
        if event == api.EVENT_STREAM_RESOLVED:
            source = payload["source"]
            platform = self.context.manager.platform_for(str(source.room_id))
            if str(source.room_id).isdigit() or platform is not None:
                room_id = platform.normalize(str(source.room_id)) if platform else str(source.room_id)
                self.sources[room_id] = {
                    "url": source.url, "headers": dict(source.headers), "uname": source.uname,
                    "quality": source.quality or 250, "title": source.title}
        elif event == api.EVENT_CLOSING:
            self.on_unload()

    def tile_actions(self, tile):
        if self.button is not None:
            return []
        room = tile.room or {}
        room_id = str(room.get("room_id") or "")
        if room_id.isdigit() or self.context.manager.platform_for(room_id) is not None:
            return [("比赛二路同步…", lambda: self.open_viewer(dict(room)))]
        return []

    def set_enabled(self, enabled):
        if self._pending_enabled is not None:
            self.switch_probe.record('superseded')
        self._pending_enabled = bool(enabled)
        self.switch_probe.begin(enabled)
        if self.entry is not None:
            self.entry._feedback_pending = True
            self.entry.set_mode(enabled)
            self.entry.update()
        # Usually the first button paint releases this at 0ms. Hidden entries
        # still switch after one frame; never wait for the entire animation.
        self._switch_timer.start(16)

    def _entry_painted(self):
        self.switch_probe.button_paint()
        if self._pending_enabled is not None:
            self._switch_timer.start(0)

    def _apply_enabled(self):
        if self._applying or self._pending_enabled is None:
            return
        enabled, self._pending_enabled = self._pending_enabled, None
        self._applying = True
        try:
            with self.switch_probe.span('apply_mode'):
                self._change_mode(enabled)
                if self.entry is not None:
                    self.entry.sync_transition()
            self.switch_probe.finish(enabled)
        finally:
            self._applying = False
            if self._pending_enabled is not None:
                self._switch_timer.start(0)

    def _change_mode(self, enabled):
        if enabled:
            self.open_viewer({})
        elif self.viewer is not None:
            self.switch_probe.watch_target(getattr(self.context.window, 'wall', None))
            with self.switch_probe.span('close_viewer'):
                self.viewer.close()

    def _closed(self):
        if self.entry is not None:
            self.entry.set_mode(False)
        if self.button is not None:
            self.button.blockSignals(True)
            self.button.setChecked(False)
            self.button.blockSignals(False)

    def open_viewer(self, room):
        if self.viewer is None:
            with self.switch_probe.span('construct_viewer'):
                self.viewer = Viewer(self.context, self.sources, room)
            self.viewer.switch_probe = self.switch_probe
            if self.button is not None:
                with self.switch_probe.span('embed_viewer'):
                    self.viewer.embed(self.context.window._content)
                self.viewer.finished.connect(lambda _result: self._closed())
        elif room.get("room_id") and str(room["room_id"]) not in self.viewer.rows:
            self.viewer.add_room(str(room["room_id"]), {"alias": room.get("uname") or ""})
        self.switch_probe.watch_target(self.viewer)
        with self.switch_probe.span('show_viewer'):
            self.viewer.show()
            self.viewer.raise_()
        if self.entry is not None:
            self.entry.set_mode(True)
        if self.button is not None and self.viewer.rows and not self.viewer.running:
            with self.switch_probe.span('start_sources'):
                self.viewer.toggle_running()
        if self.button is None:
            self.viewer.activateWindow()

    def on_unload(self):
        self._pending_enabled = None
        if self._switch_timer is not None:
            self._switch_timer.stop()
        if self.viewer is not None:
            self.viewer.close()
        if self.entry is not None:
            self.entry.detach()
            self.entry = None
            self.button.deleteLater()
            self.button = None
        if self.switch_probe is not None:
            self.switch_probe.close()
            self.switch_probe.deleteLater()
            self.switch_probe = None
        if self._switch_timer is not None:
            self._switch_timer.deleteLater()
            self._switch_timer = None


plugin = MatchSyncPlugin()
