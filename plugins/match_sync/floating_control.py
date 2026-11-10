"""Floating control. Session state remains on the Viewer facade."""
from __future__ import annotations

from .canvas import Canvas
from .floating import FloatingCrop
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout
from PySide6.QtCore import QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QRegion
from ddm import theme

def _crop_source_policy(viewer, row):
    if row.room_id not in viewer.floating_crops:
        return 'source'
    if row.room_id != viewer.alignment.reference:
        return 'balanced'
    return 'source'


def _crop_quality_changed(viewer):
    for floating in viewer.floating_crops.values():
        floating.set_quality_mode(viewer.crop_quality.currentData())
    viewer._apply_crop_quality()
    viewer.changed()


def _apply_crop_quality(viewer):
    if not viewer.running:
        return
    for key, row in viewer.rows.items():
        seed = getattr(row.decoder, 'seed', {})
        if row.decoder is None or seed.get('crop_quality_policy', 'source') == viewer._crop_source_policy(row):
            continue
        floating = viewer.floating_crops.get(key)
        if floating is not None:
            floating.set_frame(None)
        if key == viewer.alignment.reference:
            viewer._reload_picture()
        else:
            viewer._stop_row(row)
            viewer.sources.pop(key, None)
            viewer._start_row(row)
            if not viewer.alignment.manual_locked:
                viewer.alignment.lags.pop(key, None)
            viewer.alignment.candidates.pop(key, None)
            viewer.generation += 1


def _close_floating_crop(viewer, key):
    viewer.floating_crops.pop(key, None)
    viewer._apply_crop_quality()


def floating_surface(viewer):
    if viewer.fullscreen_dialog is not None:
        return viewer.fullscreen_dialog
    host = viewer.context.window if viewer.embedded else viewer
    central = getattr(host, 'centralWidget', None)
    return (central() if callable(central) else None) or host


def reparent_floating_crops(viewer, parent):
    for floating in viewer.floating_crops.values():
        geometry = floating.geometry()
        floating.parentWidget().removeEventFilter(floating)
        floating.setParent(parent)
        parent.installEventFilter(floating)
        floating.fit_geometry(geometry.x(), geometry.y(), geometry.width())
        floating.show()
        floating.raise_()


def restore_crop_background(viewer, floating, exposed):
    parent = floating.parentWidget()
    # Restore actual video/text surfaces even without a new source frame.
    # During gestures coalesce mouse bursts before repainting those surfaces.
    surfaces = [viewer.canvas, viewer.panel.body.viewport()]
    surfaces.extend(card[2] for card in viewer.comparison_panel.cards.values())
    for surface in surfaces:
        if surface.isVisible() and surface.window() is parent.window():
            local = exposed.translated(-surface.mapTo(parent, QPoint())).intersected(surface.rect())
            if not local.isEmpty():
                if floating.drag is None:
                    surface.repaint(local)
                else:
                    viewer.crop_repaint_regions[surface] = viewer.crop_repaint_regions.get(surface, QRegion()).united(QRegion(local))
    if viewer.crop_repaint_regions and not viewer.crop_repaint_timer.isActive():
        viewer.crop_repaint_timer.start()


def flush_crop_background(viewer):
    viewer.crop_repaint_timer.stop()
    regions, viewer.crop_repaint_regions = viewer.crop_repaint_regions, {}
    surfaces = [viewer.canvas, viewer.panel.body.viewport()]
    surfaces.extend(card[2] for card in viewer.comparison_panel.cards.values())
    for surface in surfaces:
        region = regions.get(surface)
        if region is not None and surface.isVisible():
            surface.repaint(region)


def show_floating_crop(viewer, row, crop, frame):
    floating = viewer.floating_crops.get(row.room_id)
    if floating is None:
        floating = FloatingCrop(viewer.floating_surface(), row.label(), crop)
        viewer.floating_crops[row.room_id] = floating
        floating.closed.connect(lambda key=row.room_id: viewer._close_floating_crop(key))
        floating.background_exposed.connect(lambda rect, item=floating: viewer.restore_crop_background(item, rect))
        floating.gesture_finished.connect(viewer.flush_crop_background)
        floating.recrop.connect(lambda: viewer.choose_crop(row, floating=True))
        floating.move(40 + len(viewer.floating_crops) * 24, 40 + len(viewer.floating_crops) * 24)
    floating.picture_widget = viewer.picture
    viewer.picture.installEventFilter(floating)
    floating.crop = QRectF(*crop)
    floating.reset_enhancement()
    floating.set_quality_mode(viewer.crop_quality.currentData())
    pixmap = row.avatar.pixmap()
    floating.set_avatar(pixmap if pixmap is not None and not pixmap.isNull() else row.avatar.grab())
    floating.frame_key = None
    floating.set_frame(frame)
    floating.fit_geometry(floating.x(), floating.y(), floating.width() if floating.isVisible() else 320)
    floating.show()
    floating.raise_()
    viewer._apply_crop_quality()
    return floating


def render_floating_crops(viewer, clock, shifts):
    for key, floating in viewer.floating_crops.items():
        row = viewer.rows.get(key)
        if row is not None and row.decoder is not None and not row.paused and key in shifts:
            floating.label = row.label()
            floating.set_avatar(row.avatar.pixmap())
            frame = row.decoder.picture_at(clock + shifts[key])
            if frame is not None:
                floating.set_frame(frame)
        elif key not in shifts:
            floating.set_frame(None)


def close_crop_dialog(viewer, row=None):
    dialog = viewer.crop_dialog
    if dialog is not None and (row is None or dialog.crop_row is row):
        dialog.reject()


def choose_crop(viewer, row, floating=False):
    if viewer.crop_dialog is not None:
        viewer.crop_dialog.raise_()
        viewer.crop_dialog.activateWindow()
        return
    if viewer.rows.get(row.room_id) is not row:
        return
    decoder = row.decoder
    history = decoder.history if decoder is not None else None
    reference = viewer.alignment.reference

    def source_valid():
        return (viewer.rows.get(row.room_id) is row and row.decoder is decoder
                and (decoder is None or decoder.history is history)
                and viewer.alignment.reference == reference)

    frozen = None
    if row.room_id == viewer.alignment.reference:
        frame = viewer.canvas.frame
    elif viewer.compare.isChecked() and row.room_id in viewer.comparison_panel.shown_rooms:
        frame = viewer.comparison_panel.cards[row.room_id][2].frame
    else:
        frame = (row.decoder.history.frame_at(viewer.live_clock() + viewer.shifts()[row.room_id])
                 if row.decoder is not None else None)
        if frame is not None and hasattr(row.decoder, "picture_at"):
            history = row.decoder.history
            frozen = (history.segment_at(frame[0]) if hasattr(history, 'segment_at') else history, frame)
            frame = row.decoder.picture_at(frame[0], exact=True)
    if frame is None and frozen is None:
        viewer.notice.setText("请先开始观看，收到画面后再选择对照区域")
        return
    dialog = QDialog(viewer)
    viewer.crop_dialog = dialog
    dialog.crop_row = row
    dialog.setWindowModality(Qt.NonModal)
    dialog.setWindowTitle(f"{row.label()}：" + ('框选悬浮画面' if floating else '拖选计时器或共同内容，仅用于放大对照'))
    dialog.resize(900, 600)
    canvas = Canvas(selecting=True)
    canvas.set_frame(frame)
    canvas.waiting = frame is None
    canvas.crop = (QRectF(viewer.floating_crops[row.room_id].crop) if floating and row.room_id in viewer.floating_crops
                   else QRectF(0, 0, 1, 1) if floating else QRectF(*row.crop))
    canvas.selection_ready = (floating and row.room_id in viewer.floating_crops) or canvas.crop != QRectF(0, 0, 1, 1)
    reset = QPushButton("使用整个画面")
    reset.clicked.connect(canvas.reset_selection)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.button(QDialogButtonBox.Ok).setObjectName("MatchSyncCropConfirm")
    dialog.setStyleSheet(f"""
            QPushButton {{
                background: {theme.CONTENT_HOVER}; color: {theme.TEXT1};
                border: 1px solid {theme.BORDER_STRONG}; border-radius: 6px;
                padding: 6px 16px;
            }}
            QPushButton:hover {{ background: {theme.ELEVATED}; border-color: {theme.ACCENT}; }}
            QPushButton:pressed {{ background: {theme.CONTENT}; }}
            QPushButton:focus {{ border: 2px solid {theme.ACCENT}; }}
            QPushButton#MatchSyncCropConfirm {{ background: #006a92; color: #ffffff; border-color: #006a92; }}
            QPushButton#MatchSyncCropConfirm:hover {{ background: #007ba8; }}
            QPushButton#MatchSyncCropConfirm:pressed {{ background: #005778; }}
            QPushButton:disabled, QPushButton#MatchSyncCropConfirm:disabled {{
                background: {theme.CONTENT_HOVER}; color: {theme.TEXT3}; border-color: {theme.BORDER};
            }}
        """)
    if frame is None:
        buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        prepare = QTimer(dialog)
        prepare.setInterval(16)

        def show_frozen():
            if not source_valid():
                dialog.reject()
                return
            ready = decoder.pictures.get(*frozen, exact=True)
            if ready is not None:
                canvas.set_frame(ready)
                canvas.waiting = False
                buttons.button(QDialogButtonBox.Ok).setEnabled(True)
                prepare.stop()

        prepare.timeout.connect(show_frozen)
        dialog.finished.connect(prepare.stop)
        prepare.start()
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout = QVBoxLayout(dialog)
    hint = QLabel('Ctrl＋滚轮：缩放　滚轮：上下平移　Shift＋滚轮：左右平移'
                  if floating else "自动匹配始终使用完整画面；框选仅用于放大对照、人工微调偏移。"
                  "两路请选择相同计时器，框选不会改变自动匹配或读取计时器数字。")
    hint.setWordWrap(True)
    layout.addWidget(hint)
    layout.addWidget(canvas, 1)
    actions = QHBoxLayout()
    actions.addStretch(1)
    actions.addWidget(reset)
    actions.addWidget(buttons)
    layout.addLayout(actions)
    def finished(result):
        viewer.crop_dialog = None
        dialog.deleteLater()
        if (result != QDialog.Accepted or not source_valid()
                or canvas.crop.width() <= 0 or canvas.crop.height() <= 0):
            return
        rect = canvas.crop
        if floating:
            viewer.show_floating_crop(row, (rect.x(), rect.y(), rect.width(), rect.height()), canvas.frame)
            return
        row.crop = (rect.x(), rect.y(), rect.width(), rect.height())
        viewer.comparison_panel.zoom.setChecked(True)
        viewer.show_comparison(row.room_id)
        viewer.changed()

    dialog.finished.connect(finished)
    dialog.show()
