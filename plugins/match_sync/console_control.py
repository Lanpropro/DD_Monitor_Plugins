"""Console control. State and Qt callback ownership stay on Viewer."""
from __future__ import annotations

import time
from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation
from ddm import motion


def set_settings_visible(viewer, visible):
    if visible and viewer.fullscreen_dialog is not None:
        viewer.fullscreen_dialog.close()
    if not visible:
        viewer.settings_height = viewer.settings_panel.height()
        viewer.compare.setChecked(False)
    viewer.settings_panel.setVisible(visible)
    viewer.send_console.sync_height()
    viewer.restore_settings.setVisible(not visible)
    viewer._layout_restore_control()
    if visible:
        viewer.left_pane.layout().activate()
        viewer.body_split.setSizes([
            max(1, viewer.body_split.height() - viewer.settings_height -
                (viewer.comparison_panel.height() if viewer.compare.isChecked() else 0)),
            viewer.comparison_panel.height() if viewer.compare.isChecked() else 0, viewer.settings_height])


def _layout_restore_control(viewer):
    tile = viewer.picture
    tile._layout_bottom_controls()
    if viewer.restore_settings.isHidden():
        return
    viewer.restore_settings.setFixedWidth(58 if tile.width() < 420 else 90)
    viewer.restore_settings.setText("展开" if tile.width() < 420 else "展开设置")
    layout = tile.bottom.layout()
    fixed = [layout.itemAt(i).widget() for i in range(layout.count())]
    fixed = [widget for widget in fixed if widget is not None and not widget.isHidden()
             and widget not in (tile.volume_slider, tile.status_label)]
    margins = layout.contentsMargins()
    remaining = tile.width() - margins.left() - margins.right() - layout.spacing() * (len(fixed) + 1) - 8
    remaining -= sum(widget.sizeHint().width() if widget is tile.recording_time else widget.width() for widget in fixed)
    tile.volume_slider.setFixedWidth(max(18, min(86, remaining)))
    layout.activate()


def set_comparison_visible(viewer, visible):
    sizes = viewer.body_split.sizes()
    viewer.comparison_panel.setVisible(visible)
    viewer.generation += 1
    height = 240 if visible else 0
    if len(sizes) == 3:
        viewer.body_split.setSizes([max(1, sizes[0] + sizes[1] - height), height, sizes[2]])
    if visible:
        viewer.comparison_panel.render(viewer.live_clock(), viewer.shifts())


def reorder_room(viewer, room_id, target_id, after=False):
    if room_id == target_id or room_id not in viewer.rows or target_id not in viewer.rows:
        return
    keys = list(viewer.rows)
    keys.remove(room_id)
    keys.insert(keys.index(target_id) + int(after), room_id)
    viewer.rows = {key: viewer.rows[key] for key in keys}
    for index, row in enumerate(viewer.rows.values()):
        viewer.rows_layout.insertWidget(index, row)
    viewer.update_main_choices()
    viewer.changed()


def align_room_controls(viewer):
    rows = list(viewer.rows.values())
    if viewer.dragged_row is not None:
        return
    if not rows:
        viewer.queue_console_height()
        return
    name_width = min(140, max(row.name_label.fontMetrics().horizontalAdvance(row.name_label.text()) for row in rows))
    for row in rows:
        if row.name_label.width() != name_width or row.name_label.minimumWidth() != name_width:
            row.name_label.setFixedWidth(name_width)
    widths = [max(min(widget.maximumWidth(), max(widget.minimumWidth(), widget.sizeHint().width()))
                  for widget in widgets) for widgets in zip(*(row.control_widgets for row in rows))]
    compact = viewer.controls.viewport().width() < sum(widths) + 12 * 5 + 16
    for index, row in enumerate(rows):
        row.layout_controls(compact, index == 0)
        for column in set(row.control_columns):
            needed = max(w for c, w in zip(row.control_columns, widths) if c == column)
            if row.controls_grid.columnMinimumWidth(column) != needed:
                row.controls_grid.setColumnMinimumWidth(column, needed)
    viewer.queue_console_height()


def queue_console_height(viewer):
    if viewer.body_split.count() != 3:
        return
    height = viewer.settings_height
    if height != getattr(viewer, 'console_content_height', None) or viewer.settings_panel.height() != height:
        viewer.console_content_height = height
        viewer.settings_height = height
        viewer.console_size_timer.start(0)


def remember_console_height(viewer, _position, _index):
    if not viewer.settings_panel.isHidden() and viewer.dragged_row is None:
        viewer.settings_height = viewer.console_content_height = viewer.settings_panel.height()


def fit_console_height(viewer):
    if viewer.settings_panel.isHidden() or viewer.dragged_row is not None:
        return
    sizes = viewer.body_split.sizes()
    # Fullscreen reparents the picture out of this splitter temporarily.
    if len(sizes) != 3:
        return
    height = viewer.console_content_height
    viewer.body_split.setSizes([max(1, sizes[0] + sizes[2] - height), sizes[1], height])


def begin_row_drag(viewer, room_id):
    viewer.console_size_timer.stop()
    viewer.dragged_row = room_id
    viewer.row_preview = list(viewer.rows)
    viewer.row_slots = {key: row.geometry() for key, row in viewer.rows.items()}
    viewer.rows_layout.setEnabled(False)
    viewer.rows[room_id].hide()


def preview_row_drag(viewer, point):
    if viewer.dragged_row is None:
        return
    for animation in viewer.row_animations.values():
        elapsed = round((time.perf_counter() - animation.property('started_at')) * 1000)
        animation.setCurrentTime(min(animation.duration(), max(animation.currentTime(), elapsed)))
    order = [key for key in viewer.rows if key != viewer.dragged_row]
    index = sum(point.y() >= viewer.row_slots[key].center().y() for key in order)
    order.insert(index, viewer.dragged_row)
    if order != viewer.row_preview:
        viewer.row_preview = order
        y = 0
        for key in order:
            row = viewer.rows[key]
            if key != viewer.dragged_row:
                previous = viewer.row_animations.pop(key, None)
                if previous is not None:
                    previous.stop()
                    previous.deleteLater()
                target = QPoint(viewer.row_slots[key].x(), y)
                if motion.enabled():
                    animation = QPropertyAnimation(row, b"pos", viewer)
                    animation.setDuration(150)
                    animation.setEasingCurve(QEasingCurve.OutCubic)
                    animation.setStartValue(row.pos())
                    animation.setEndValue(target)
                    animation.setProperty('started_at', time.perf_counter())
                    viewer.row_animations[key] = animation
                    animation.start()
                else:
                    row.move(target)
            y += viewer.row_slots[key].height() + viewer.rows_layout.spacing()
    viewport = viewer.controls.viewport()
    local = viewer.controls.widget().mapTo(viewport, point)
    bar = viewer.controls.verticalScrollBar()
    if local.y() < 24:
        bar.setValue(bar.value() - 12)
    elif local.y() > viewport.height() - 24:
        bar.setValue(bar.value() + 12)


def finish_row_drag(viewer, point):
    if viewer.dragged_row is None:
        return
    viewer.preview_row_drag(point)
    viewer.rows = {key: viewer.rows[key] for key in viewer.row_preview}
    viewer.end_row_drag()
    viewer.update_main_choices()
    viewer.changed()


def end_row_drag(viewer):
    for animation in viewer.row_animations.values():
        animation.stop()
        animation.deleteLater()
    viewer.row_animations.clear()
    if viewer.dragged_row in viewer.rows:
        viewer.rows[viewer.dragged_row].show()
    viewer.dragged_row = None
    for index, row in enumerate(viewer.rows.values()):
        viewer.rows_layout.insertWidget(index, row)
    viewer.rows_layout.setEnabled(True)
    viewer.rows_layout.activate()


def show_comparison(viewer, room_id):
    viewer.compare.setChecked(True)
    viewer.comparison_panel.render(viewer.live_clock(), viewer.shifts())
    index = viewer.comparison_panel.target.findData(room_id)
    if index >= 0:
        viewer.comparison_panel.target.setCurrentIndex(index)


def set_chat_visible(viewer, visible):
    if visible == (not viewer.chat_collapsed):
        return
    viewer.chat_collapsed = not visible
    viewer.panel.setVisible(visible)
    viewer.chat_space.setVisible(not visible)
    viewer.restore_chat.setVisible(not visible)


def position_removal_notice(viewer):
    viewer.removal_notice.move(max(0, (viewer.settings_panel.width() - viewer.removal_notice.width()) // 2),
        max(0, viewer.settings_panel.height() - viewer.console_footer.height() - viewer.removal_notice.height() - 8))
