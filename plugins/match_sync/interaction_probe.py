"""Exercise input handlers in the diagnostic viewer without moving the desktop cursor."""
from time import perf_counter, time

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid


class InteractionProbe(QObject):
    def __init__(self, bridge):
        super().__init__(bridge)
        self.bridge = bridge
        self.result = {}
        self.target = None
        self.started = 0.

    def start(self, action, source, slot):
        viewer = self.bridge.viewer
        if self.result.get('pending') or self.bridge.panels.active or QApplication.activeModalWidget():
            raise ValueError('Another interaction is in progress')
        if action == 'page':
            sidebar = getattr(viewer.window(), 'sidebar', None)
            record = sidebar.workspace_controls.entries.get(('match_sync', 'entry')) if sidebar else None
            entry = record[1] if record else None
            if entry is None or not entry.isVisible() or not entry.isEnabled():
                raise ValueError('Match page entry unavailable')
            self.queue(action, lambda: self.page(entry))
        else:
            if type(source) is not int or not 0 <= source < len(viewer.rows):
                raise ValueError('Invalid source')
            key = tuple(viewer.rows)[source]
            if action == 'sender':
                console = viewer.send_console
                rooms = console.rooms
                item = next((rooms.item(i) for i in range(rooms.count())
                             if rooms.item(i).data(Qt.UserRole) == key), None)
                if (item is None or not item.flags() & Qt.ItemIsUserCheckable
                        or not item.flags() & Qt.ItemIsEnabled or console.batch_panel.timer.isActive()
                        or not rooms.isVisible() or not rooms.viewport().rect().contains(rooms.visualItemRect(item).center())):
                    raise ValueError('Sender card unavailable')
                self.queue(action, lambda: self.sender(rooms, item))
            elif action == 'drag':
                if type(slot) is not int:
                    raise ValueError('Invalid crop slot')
                item = viewer.floating_crops.get(key, {}).get(slot)
                if item is None or not item.isVisible() or item.lock_mode == 2 or item.drag is not None:
                    raise ValueError('Movable crop unavailable')
                self.queue(action, lambda: self.drag(item))
            else:
                raise ValueError('Unknown interaction')
        return dict(queued=True)

    def queue(self, action, run):
        self.result = dict(action=action, pending=True, started_at=time())
        QTimer.singleShot(0, self, run)

    def watch(self, widget):
        self.target = widget
        self.started = perf_counter()
        widget.installEventFilter(self)

    def finish(self, **result):
        if self.target is not None and isValid(self.target):
            self.target.removeEventFilter(self)
        self.target = None
        self.result.update(pending=False, **result)

    def eventFilter(self, watched, event):
        if watched is self.target and event.type() == QEvent.Paint:
            elapsed = round((perf_counter() - self.started) * 1000, 3)
            self.result.setdefault('first_paint_ms', elapsed)
            if self.result['action'] == 'drag':
                self.result['paints'].append(elapsed)
            elif self.result['action'] == 'sender':
                bounds = event.region().boundingRect()
                self.result['paints'].append(dict(at_ms=elapsed, bounds=list(bounds.getRect())))
        return False

    def sender(self, rooms, item):
        initial = item.checkState()
        current = rooms.currentItem()
        selected = rooms.selectedItems()
        viewport = rooms.viewport()
        self.result.update(paints=[], card_bounds=list(rooms.visualItemRect(item).getRect()))
        self.watch(viewport)
        QTest.mouseClick(viewport, Qt.LeftButton, pos=rooms.visualItemRect(item).center())
        self.result.update(handler_ms=round((perf_counter() - self.started) * 1000, 3),
                           changed=item.checkState() != initial)

        def restore():
            if not isValid(rooms) or not isValid(item):
                self.finish(restored=False)
                return
            item.setCheckState(initial)
            rooms.setCurrentItem(current if current is not None and isValid(current) else None)
            rooms.clearSelection()
            for selected_item in selected:
                if isValid(selected_item):
                    selected_item.setSelected(True)
            self.finish(restored=item.checkState() == initial)

        QTimer.singleShot(240, self, restore)

    def drag(self, item):
        original = item.pos()
        bounds = item.movement_bounds()
        room_left = original.x() - bounds.left()
        room_right = bounds.right() - item.geometry().right()
        distance = min(96, max(room_left, room_right))
        if not distance:
            self.finish(moved=False, restored=True)
            return
        distance *= -1 if room_left > room_right else 1
        origin = item.mapToGlobal(item.rect().center())
        quick = self.bridge.viewer.quick_danmaku
        self.result.update(moves=[], paints=[],
                           native_occlusion=quick is not None and hasattr(quick.layer, 'set_child_occlusion'))
        self.watch(item)

        def send(kind, offset, button, buttons):
            pos = origin + QPoint(offset, 0)
            event = QMouseEvent(kind, QPointF(item.mapFromGlobal(pos)), QPointF(pos), button, buttons, Qt.NoModifier)
            started = perf_counter()
            QApplication.sendEvent(item, event)
            return round((perf_counter() - started) * 1000, 3)

        def move(step):
            if not isValid(item) or not item.isVisible():
                self.finish(restored=False)
                return
            if step == 13:
                self.result['release_ms'] = send(QEvent.MouseButtonRelease, 0, Qt.LeftButton, Qt.NoButton)
                self.finish(moved=any(row['offset'] for row in self.result['moves']), restored=item.pos() == original)
                return
            offset = round(distance * min(step, 12 - step) / 6)
            elapsed = round((perf_counter() - self.started) * 1000, 3)
            duration = send(QEvent.MouseMove, offset, Qt.NoButton, Qt.LeftButton)
            self.result['moves'].append(dict(at_ms=elapsed, handler_ms=duration, offset=item.x() - original.x()))
            QTimer.singleShot(16, self, lambda: move(step + 1))

        send(QEvent.MouseButtonPress, 0, Qt.LeftButton, Qt.LeftButton)
        QTimer.singleShot(16, self, lambda: move(1))

    def page(self, entry):
        viewer = self.bridge.viewer
        action = entry.defaultAction()
        # Auto-resume opens the viewer directly, before the entry has been clicked.
        blocked = action.blockSignals(True)
        action.setChecked(viewer.isVisible())
        action.blockSignals(blocked)
        self.result['leave'] = self.bridge.buttons.click(entry)

        def reopen():
            self.result['left'] = not viewer.isVisible()
            if self.result['left'] and isValid(entry) and entry.isVisible():
                self.result['enter'] = self.bridge.buttons.click(entry)
            QTimer.singleShot(500, self, lambda: self.finish(restored=viewer.isVisible()))

        QTimer.singleShot(1500, self, reopen)
