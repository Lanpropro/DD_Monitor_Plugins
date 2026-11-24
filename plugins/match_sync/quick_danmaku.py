"""Opt-in Qt Quick scrolling overlay for the QWidget match picture."""
import logging
import os

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import QApplication


def install(viewer):
    if (os.environ.get('DDM_MATCH_QUICK_DANMAKU') == '1'
            and QApplication.platformName() == 'windows'
            and viewer.split_presentation
            and hasattr(viewer.video_danmaku, 'set_windowed_quick_layer')):
        return WindowedDanmaku(viewer)
    return None


class WindowedDanmaku(QObject):
    def __init__(self, viewer, *, make_layer=None):
        super().__init__(viewer)
        self.viewer = viewer
        self.picture = viewer.picture
        self.overlay = viewer.video_danmaku
        self.layer = None
        self.failed = False
        self.make_layer = make_layer or self._make_layer
        self.refresh = QTimer(self)
        self.refresh.setSingleShot(True)
        self.refresh.timeout.connect(self.sync)
        for widget in (viewer, self.picture, viewer.body_split, viewer.left_pane, viewer.replay_bar.preview):
            widget.installEventFilter(self)
        self.refresh.start(0)

    def _make_layer(self):
        from ddm.video_danmaku_quick import QuickDanmakuLayer
        return QuickDanmakuLayer(self.picture)

    def watch_crop(self, crop):
        crop.installEventFilter(self)
        self.refresh.start(0)

    def occluders(self):
        yield self.viewer.replay_bar.preview
        yield from self.viewer.all_floating_crops()

    def eventFilter(self, watched, event):
        kind = event.type()
        if watched is self.picture and kind == QEvent.ParentAboutToChange:
            self.overlay.set_windowed_quick_layer(None)
        if kind in (QEvent.Show, QEvent.Hide, QEvent.Close, QEvent.Move, QEvent.Resize,
                    QEvent.ParentChange):
            self.refresh.start(0)
        return False

    def sync(self):
        owner = self.viewer.window()
        if (self.failed or not self.picture.isVisible()
                or self.picture.window() is not owner or owner.windowHandle() is None):
            return
        try:
            if self.layer is None:
                self.layer = self.make_layer()
                self.layer.setProperty('matchWindowedDanmaku', True)
            self.overlay.set_windowed_quick_layer(self.layer, self.occluders)
            self.picture._update_quick_danmaku_mask()
            self.layer.resize()
        except (ImportError, OSError, RuntimeError):
            self.failed = True
            self.overlay.set_windowed_quick_layer(None)
            if self.layer is not None:
                self.layer.window.hide()
            logging.getLogger(__name__).exception('Match Sync Qt Quick danmaku fell back to raster')
