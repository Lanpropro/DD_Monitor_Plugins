"""Floating control. Session state remains on the Viewer facade."""
from __future__ import annotations

from contextlib import nullcontext

from .canvas import Canvas
from .floating import FloatingCrop
from .panels import console_button_style
from PySide6.QtWidgets import (QApplication, QButtonGroup, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                               QPushButton, QToolButton, QVBoxLayout, QWidget)
from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPixmap, QRegion, QShortcut
from ddm import theme

def _crop_source_policy(viewer, row):
    if row.room_id not in viewer.floating_crops:
        return 'source'
    if row.room_id != viewer.alignment.reference:
        return 'balanced'
    return 'source'


def _crop_quality_changed(viewer):
    for floating in viewer.all_floating_crops():
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
        for floating in viewer.floating_crops.get(key, {}).values():
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


def _close_floating_crop(viewer, key, slot):
    group = viewer.floating_crops[key]
    group.pop(slot)
    if not group:
        viewer.floating_crops.pop(key)
    viewer._apply_crop_quality()


def floating_surface(viewer):
    if viewer.fullscreen_dialog is not None:
        return viewer.fullscreen_dialog
    if viewer.workspace_crops:
        return viewer
    host = viewer.context.window if viewer.embedded else viewer
    central = getattr(host, 'centralWidget', None)
    return (central() if callable(central) else None) or host


def schedule_crop_raise(viewer, watched):
    if (isinstance(watched, QWidget) and not isinstance(watched, FloatingCrop)
            and not watched.isWindow() and watched.parentWidget() is viewer.floating_surface()
            and not viewer.crop_stack_timer.isActive()):
        # Wait until the sibling's native show or restack has finished.
        viewer.crop_stack_timer.start(0)


def raise_floating_crops(viewer):
    # Qt keeps children in stacking order, so preserve the order between crops.
    for child in viewer.floating_surface().children():
        if isinstance(child, FloatingCrop) and child.isVisible():
            child.raise_()


def reparent_floating_crops(viewer, parent):
    for floating in viewer.all_floating_crops():
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


def show_floating_crop(viewer, row, crop, frame, *, slot=0):
    group = viewer.floating_crops.setdefault(row.room_id, {})
    floating = group.get(slot)
    if floating is None:
        # Windows gives native playback children priority over non-native siblings.
        native = viewer.workspace_crops or QApplication.platformName() == 'windows'
        floating = FloatingCrop(viewer.floating_surface(), row.label(), crop, native=native)
        quick = getattr(viewer, 'quick_danmaku', None)
        if quick is not None:
            quick.watch_crop(floating)
        floating.playback_probe = getattr(viewer, 'playback_probe', None)
        group[slot] = floating
        floating.closed.connect(lambda: _close_floating_crop(viewer, row.room_id, slot))
        floating.background_exposed.connect(lambda rect, item=floating: viewer.restore_crop_background(item, rect))
        floating.gesture_finished.connect(viewer.flush_crop_background)
        floating.recrop.connect(lambda: viewer.choose_crop(row, floating=True, current=slot))
        offset = 40 + sum(len(crops) for crops in viewer.floating_crops.values()) * 24
        floating.move(offset, offset)
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
    probe = getattr(viewer, 'playback_probe', None)
    timeline = probe.gui_timeline if probe is not None else None
    for key, group in viewer.floating_crops.items():
        row = viewer.rows.get(key)
        if row is not None and row.decoder is not None and not row.paused and key in shifts:
            if timeline is None:
                frame = row.decoder.picture_at(clock + shifts[key])
            else:
                with timeline.span('pip.picture_lookup'):
                    frame = row.decoder.picture_at(clock + shifts[key], timeline=timeline)
            for floating in group.values():
                with timeline.span('pip.avatar') if timeline is not None else nullcontext():
                    floating.label = row.label()
                    floating.set_avatar(row.avatar.pixmap())
                if frame is not None:
                    with timeline.span('pip.set_frame') if timeline is not None else nullcontext():
                        floating.set_frame(frame)
        elif key not in shifts:
            for floating in group.values():
                floating.set_frame(None)


def selection_color(slot):
    return QColor.fromHsv((198 + slot * 137) % 360, 190, 250)


class CropSelections(QWidget):
    """Keep all edits as drafts until the picker is confirmed."""

    def __init__(self, canvas, group, current, parent):
        super().__init__(parent)
        self.setStyleSheet(console_button_style('QToolButton'))
        self.canvas = canvas
        self.rects = {slot: QRectF(item.crop) for slot, item in group.items()}
        self.active = None
        self.buttons = {}
        self.bar = QHBoxLayout(self)
        self.bar.setContentsMargins(0, 0, 0, 0)
        self.group = QButtonGroup(self)
        self.add_button = QToolButton()
        self.add_button.setText('＋')
        self.add_button.setAccessibleName('新增画中画')
        self.add_button.setToolTip('新增画中画选区')
        self.add_button.clicked.connect(self.add_selection)
        self.bar.addWidget(self.add_button)
        self.remove_button = QToolButton()
        self.remove_button.setText('×')
        self.remove_button.setAccessibleName('删除当前选区')
        self.remove_button.setToolTip('删除当前选区（Delete）')
        self.remove_button.clicked.connect(self.remove_selection)
        self.bar.addWidget(self.remove_button)
        self.bar.addStretch()
        QShortcut(QKeySequence(Qt.Key_Delete), parent, activated=self.remove_selection)
        self.arrangement_toggle = QToolButton(parent)
        self.arrangement_toggle.setText('排列预设')
        self.arrangement_toggle.setCheckable(True)
        self.arrangement_toggle.setToolTip('展开画中画排列预设')
        self.arrangement_toggle.hide()
        self.arrangement_toggle.toggled.connect(self.refresh_arrangements)
        try:
            from ddm.pip_arrangements import PipArrangements, FREE_LAYOUT
        except ImportError:
            self.arrangements = None
        else:
            self.arrangements = PipArrangements(FREE_LAYOUT, parent, compact=True)
            self.arrangements.changed.connect(self.refresh_arrangements)
        canvas.selection_finished.connect(self.save_active)
        for slot in self.rects:
            self.insert_button(slot)
        if self.rects:
            self.select(current if current in self.rects else next(iter(self.rects)))
        else:
            self.add_selection()

    def insert_button(self, slot):
        button = QToolButton()
        label = f'选区 {slot + 1}'
        swatch = QPixmap(12, 12)
        swatch.fill(selection_color(slot))
        button.setIcon(QIcon(swatch))
        button.setText(str(slot + 1))
        button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        button.setCheckable(True)
        button.setAccessibleName(label)
        button.setToolTip(label)
        button.clicked.connect(lambda: self.select(slot))
        self.group.addButton(button)
        self.bar.insertWidget(self.bar.indexOf(self.remove_button), button)
        self.buttons[slot] = button

    def save_active(self):
        if self.active is not None:
            self.rects[self.active] = QRectF(self.canvas.crop) if self.canvas.selection_ready else None
        self.refresh_arrangements()

    def refresh_arrangements(self):
        if self.arrangements is not None:
            count = sum(rect is not None for rect in self.rects.values())
            self.arrangement_toggle.setVisible(count >= 2)
            self.arrangements.update_context(count)
            self.arrangements.setVisible(count >= 2 and self.arrangement_toggle.isChecked())

    def select(self, slot):
        self.save_active()
        self.active = slot
        rect = self.rects.get(slot)
        self.canvas.crop = QRectF(rect) if rect is not None else QRectF()
        self.canvas.selection_ready = rect is not None
        self.canvas.anchor = None
        self.canvas.setEnabled(slot is not None)
        self.remove_button.setEnabled(slot is not None)
        if slot is not None:
            self.buttons[slot].setChecked(True)
            self.canvas.selection_color = selection_color(slot)
        self.canvas.other_selections = tuple((other, selection_color(key)) for key, other in self.rects.items()
                                             if key != slot and other is not None)
        self.canvas.update()

    def add_selection(self):
        slot = next(index for index in range(len(self.rects) + 1) if index not in self.rects)
        self.rects[slot] = None
        self.insert_button(slot)
        self.select(slot)

    def remove_selection(self):
        if self.active is None:
            return
        slot, self.active = self.active, None
        self.rects.pop(slot)
        button = self.buttons.pop(slot)
        button.hide()
        button.deleteLater()
        self.select(next(reversed(self.rects), None))

    def use_full_frame(self):
        if self.active is not None:
            self.canvas.reset_selection()
            self.canvas.selection_ready = True
            self.save_active()


def arrange_crops(crops, arrangements, primary):
    if arrangements is None or len(crops) < 2:
        return
    from ddm.pip_composition import FREE_LAYOUT
    from ddm.pip_geometry import pane_rectangles
    if arrangements.mode == FREE_LAYOUT:
        return
    first = crops[0]
    bounds = first.movement_bounds()
    for crop in crops[1:]:
        bounds = bounds.intersected(crop.movement_bounds())
    if bounds.isEmpty():
        return
    width = min(bounds.width(), 2 * max(crop.width() for crop in crops))
    height = min(bounds.height(), 2 * max(crop.height() for crop in crops))
    area = QRect(bounds)
    area.setWidth(width)
    area.setHeight(height)
    area.moveTo(max(bounds.x(), min(first.x(), bounds.right() + 1 - width)),
                max(bounds.y(), min(first.y(), bounds.bottom() + 1 - height)))
    primary = next((index for index, item in enumerate(crops) if item is primary), 0)
    for crop, rect in zip(crops, pane_rectangles(len(crops), area, arrangements.mode,
                                                [item.aspect for item in crops], primary)):
        gap = min(4, rect.width() // 4, rect.height() // 4)
        rect.adjust(gap, gap, -gap, -gap)
        width = min(rect.width(), round(rect.height() * crop.aspect))
        height = round(width / crop.aspect)
        crop.minimum_crop_width = min(120, max(1, width))
        crop.fit_geometry(rect.center().x() - width / 2, rect.center().y() - height / 2,
                          width)


def choose_crop(viewer, row, floating=False, *, current=None):
    group = viewer.floating_crops.get(row.room_id, {})
    frozen = None
    frame = None
    if row.room_id == viewer.alignment.reference:
        frame = viewer.canvas.frame
    elif viewer.compare.isChecked() and row.room_id in viewer.comparison_panel.shown_rooms:
        frame = viewer.comparison_panel.cards[row.room_id][2].frame
    if frame is None and floating and group:
        crop = group.get(current) or next(iter(group.values()))
        frame = crop.input_frame
    if frame is None and row.decoder is not None:
        history = row.decoder.history
        frame = history.frame_at(viewer.live_clock() + viewer.shifts()[row.room_id])
        if frame is None and floating:
            frame = history.latest()
        if frame is not None and hasattr(row.decoder, "picture_at"):
            frozen = (history.segment_at(frame[0]) if hasattr(history, 'segment_at') else history, frame)
            frame = row.decoder.picture_at(frame[0], exact=True)
    if frame is None and frozen is None:
        viewer.notice.setText("请先开始观看，收到画面后再选择对照区域")
        return
    dialog = QDialog(viewer)
    dialog.setWindowTitle(f"{row.label()}：" + ('框选悬浮画面' if floating else '拖选计时器或共同内容，仅用于放大对照'))
    dialog.resize(900, 600)
    canvas = Canvas(selecting=True)
    canvas.set_frame(frame)
    canvas.waiting = frame is None
    picker = None
    if floating:
        picker = CropSelections(canvas, group, current, dialog)
    else:
        canvas.crop = QRectF(*row.crop)
        canvas.selection_ready = canvas.crop != QRectF(0, 0, 1, 1)
    reset = QPushButton("使用整个画面")
    reset.clicked.connect(picker.use_full_frame if picker is not None else canvas.reset_selection)
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
            ready = row.decoder.pictures.get(*frozen, exact=True) if row.decoder is not None else None
            if ready is not None:
                canvas.set_frame(ready)
                canvas.waiting = False
                buttons.button(QDialogButtonBox.Ok).setEnabled(True)
                prepare.stop()

        prepare.timeout.connect(show_frozen)
        prepare.start()
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout = QVBoxLayout(dialog)
    hint = QLabel('Ctrl＋滚轮：缩放　滚轮：上下平移　Shift＋滚轮：左右平移'
                  if floating else "自动匹配始终使用完整画面；框选仅用于放大对照、人工微调偏移。"
                  "两路请选择相同计时器，框选不会改变自动匹配或读取计时器数字。")
    hint.setWordWrap(True)
    layout.addWidget(hint)
    if picker is not None:
        layout.addWidget(picker, 0, Qt.AlignLeft)
    layout.addWidget(canvas, 1)
    if picker is not None and picker.arrangements is not None:
        layout.addWidget(picker.arrangement_toggle, 0, Qt.AlignLeft)
        layout.addWidget(picker.arrangements, 0, Qt.AlignLeft)
    actions = QHBoxLayout()
    actions.addStretch(1)
    actions.addWidget(reset)
    actions.addWidget(buttons)
    layout.addLayout(actions)
    if dialog.exec() == QDialog.Accepted:
        if viewer.rows.get(row.room_id) is not row:
            return
        if picker is not None:
            picker.save_active()
            selected = {slot: rect for slot, rect in picker.rects.items() if rect is not None}
            for slot, item in tuple(group.items()):
                if slot not in selected:
                    item.close()
            crops = {}
            for slot, rect in selected.items():
                item = group.get(slot)
                if item is None or rect != item.crop:
                    item = viewer.show_floating_crop(row, rect.getRect(), canvas.frame, slot=slot)
                crops[slot] = item
            if picker.arrangement_toggle.isChecked():
                arrange_crops(list(crops.values()), picker.arrangements, crops.get(picker.active))
            return
        rect = canvas.crop
        if rect.isEmpty():
            return
        row.crop = (rect.x(), rect.y(), rect.width(), rect.height())
        viewer.comparison_panel.zoom.setChecked(True)
        viewer.show_comparison(row.room_id)
        viewer.changed()
