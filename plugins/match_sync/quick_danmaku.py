"""Opt-in Qt Quick scrolling overlay for the QWidget match picture."""
import logging
import os

from PySide6.QtCore import QEvent, QObject, QPoint, QTimer
from PySide6.QtGui import QRegion
from PySide6.QtWidgets import QApplication


def install(viewer):
    if (os.environ.get('DDM_MATCH_QUICK_DANMAKU', '1') == '1'
            and QApplication.platformName() == 'windows'
            and viewer.split_presentation
            and hasattr(viewer.video_danmaku, 'attach_quick_layer')):
        return WindowedDanmaku(viewer)
    return None


class WindowedDanmaku(QObject):
    def __init__(self, viewer, *, make_layer=None):
        super().__init__(viewer)
        self.viewer = viewer
        self.picture = viewer.picture
        self.overlay = viewer.video_danmaku
        self.layer = None
        self.set_layer = getattr(self.overlay, 'set_windowed_quick_layer', self._set_legacy_layer)
        self.legacy = not hasattr(self.overlay, 'set_windowed_quick_layer')
        if self.legacy:
            self.overlay.installEventFilter(self)
        self.failed = False
        self.make_layer = make_layer or self._make_layer
        self.refresh = QTimer(self)
        self.refresh.setSingleShot(True)
        self.refresh.timeout.connect(self.sync)
        for widget in (viewer, self.picture, viewer.body_split, viewer.left_pane, viewer.replay_bar.preview):
            widget.installEventFilter(self)
        self.refresh.start(0)

    def _set_legacy_layer(self, layer, occluders=None):
        """Use the existing Quick surface hooks on host 0.9.1."""
        overlay = self.overlay
        if layer is overlay._quick_layer:
            return
        overlay._stop_timer()
        overlay._last_tick = 0
        if layer is None:
            overlay._quick_requested = False
            if overlay._quick_group is not None:
                overlay._quick_layer.remove_group(overlay._quick_group)
            overlay._quick_layer = overlay._quick_group = None
            overlay._quick_mask_signature = None
            del overlay.update_quick_mask
            overlay.setMask(overlay._quick_video_mask)
            overlay.update()
        else:
            overlay._quick_video_mask = overlay.mask()
            overlay._quick_requested = True
            update_mask = overlay.update_quick_mask
            overlay.update_quick_mask = lambda widgets: update_mask((*widgets, *occluders()))
            overlay.attach_quick_layer(layer)
            overlay._suppress_quick_raster_surface()
        if overlay.comments and overlay.isVisible() and not overlay.paused:
            overlay._start_timer()

    def _make_layer(self):
        from ddm.video_danmaku_quick import QuickDanmakuLayer
        return QuickDanmakuLayer(self.picture)

    def watch_crop(self, crop):
        crop.installEventFilter(self)
        self.refresh.start(0)

    def occluders(self):
        yield self.viewer.replay_bar.preview
        if not hasattr(self.layer, 'set_child_occlusion'):
            yield from self.viewer.all_floating_crops()

    def crop_occlusion(self):
        region = QRegion()
        origin = self.picture.mapToGlobal(QPoint())
        for crop in self.viewer.all_floating_crops():
            if crop.isVisible():
                mask = crop.mask()
                if mask.isEmpty():
                    mask = QRegion(crop.rect())
                region |= mask.translated(crop.mapToGlobal(QPoint()) - origin)
        return region.intersected(QRegion(self.picture.rect()))

    def eventFilter(self, watched, event):
        kind = event.type()
        if watched is self.picture and kind == QEvent.ParentAboutToChange:
            self.set_layer(None)
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
            self.set_layer(self.layer, self.occluders)
            if self.legacy and self.overlay._quick_group is not None:
                self.layer.set_group_visible(self.overlay._quick_group, self.overlay.isVisible())
            self.picture._update_quick_danmaku_mask()
            self.layer.resize()
            if hasattr(self.layer, 'set_child_occlusion'):
                self.layer.set_child_occlusion(self.crop_occlusion())
        except (AttributeError, ImportError, OSError, RuntimeError):
            logging.getLogger(__name__).exception('Match Sync Qt Quick danmaku fell back to raster')
            self.fallback()

    def fallback(self):
        self.failed = True
        self.set_layer(None)
        if self.layer is not None:
            self.layer.window.hide()
        if self.viewer.d3d11_picture is not None:
            self.viewer.d3d11_picture.overlay_failed()
