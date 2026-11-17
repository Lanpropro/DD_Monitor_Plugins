"""Recent successful removals and serial server-side re-add, owned by the page."""
import time
from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication
from .undo_notice import UndoNotice
from .workers import AddRequest


class RemovalUndo(QObject):
    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.notice = UndoNotice(page)
        self.notice.undo_requested.connect(self.undo)
        self.notice.details.undo_requested.connect(self.undo)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.expire)
        self.entries = {}
        self.baseline = []
        self.queue = []
        self.order_keys = []
        self.snapshot_order = lambda: []
        self.restore_order = lambda keys: None

    def record(self, item):
        if not self.entries:
            self.baseline = [entry.key for entry in self.page._items]
            self.order_keys = self.snapshot_order()
        self.entries[item.key] = item

    def show_result(self, message=None):
        if not self.entries:
            return
        busy = bool(self.page._busy_operation or self.page._multi_remove_queue)
        self.notice.set_items(list(self.entries.values()))
        self.notice.present(message or f'已移除 {len(self.entries)} 个视频', enabled=not busy)
        self.timer.stop()
        if not busy:
            self.timer.start(10000)

    def expire(self):
        focus = QApplication.focusWidget()
        if self.notice.isVisible() and (self.notice.underMouse() or focus is self.notice or
                focus is not None and self.notice.isAncestorOf(focus)):
            self.timer.start(1000)
            return
        self.clear(animated=True)

    def clear(self, *, animated=False):
        self.timer.stop()
        self.queue.clear()
        self.entries.clear()
        self.baseline.clear()
        self.order_keys = []
        self.notice.dismiss(animated=animated)

    def undo(self, key=None):
        page = self.page
        if page._shutting_down or page._busy_operation or page._multi_remove_queue or not self.entries:
            return
        if not page._has_csrf or not page._has_session:
            self.clear()
            return
        if key is not None and key not in self.entries:
            return
        self.timer.stop()
        self.queue = list(self.entries.values()) if key is None else [self.entries[key]]
        self._start_next()

    def _start_next(self):
        page = self.page
        if page._shutting_down or not self.queue:
            return
        page._cancel_refresh_for_operation()
        page._operation_seq += 1
        page._busy_operation = 'restore'
        page._set_refreshing(False)
        page._update_destructive_controls()
        page.error_bar.clear_error()
        self.notice.set_items(list(self.entries.values()))
        self.notice.present('正在撤回移除…')
        worker = AddRequest(page._cookies,page._generation,page._operation_seq,self.queue[0].aid)
        page._active_operation = worker
        worker.completed.connect(self.completed)
        worker.failed.connect(self.failed)
        page._track(worker)
        if page._active_operation is None:
            self.queue.clear()
            self.show_result('撤回失败，可重试')

    def accepts(self, request_id, generation):
        page = self.page
        return (not page._shutting_down and request_id == page._operation_seq and
                generation == page._generation and page._busy_operation == 'restore' and bool(self.queue))

    def completed(self, request_id, generation, payload):
        if not self.accepts(request_id,generation):
            return
        page = self.page
        item = self.queue.pop(0)
        self.entries.pop(item.key,None)
        if not any(current.key == item.key for current in page._items):
            page._items.append(item)
            rank = {key:i for i,key in enumerate(self.baseline)}
            page._items.sort(key=lambda current:rank.get(current.key,len(rank)))
        page._items_version += 1
        page._loaded_at, page._last_update_epoch = time.monotonic(), time.time()
        if not self.queue:
            page._end_operation()
        page._render_after_remove()
        if page._shutting_down or generation != page._generation:
            return
        page._publish_to_wall()
        self.restore_order(self.order_keys)
        if self.queue:
            QTimer.singleShot(0,self._start_next)
        elif self.entries:
            self.show_result(f'已撤回该视频，另有 {len(self.entries)} 个可撤回')
        else:
            self.clear()
            self.notice.present('已撤回移除',action=False)
            self.timer.start(3000)

    def failed(self, request_id, generation, error):
        if not self.accepts(request_id,generation):
            return
        self.queue.clear()
        self.page._operation_failed(request_id,generation,error)
        self.show_result('撤回未完成，可重试')
