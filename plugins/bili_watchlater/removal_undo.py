"""Recent successful removals and serial server-side re-add, owned by the page."""
from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication
from .undo_notice import UndoNotice


class RemovalUndo(QObject):
    def __init__(self, page):
        super().__init__(page)
        self.controller = page.list_controller
        self.controller.removed.connect(self.record)
        self.controller.operation_finished.connect(self.show_result)
        self.controller.clearing.connect(self.clear)
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
        self.restore_order = lambda keys, restored: None

    def record(self, item):
        if not self.entries:
            self.baseline = [entry.key for entry in self.controller.items]
            self.order_keys = self.snapshot_order()
        self.entries[item.key] = item

    def show_result(self, message=None):
        if not self.entries:
            return
        busy = bool(self.controller.busy_operation or self.controller.remove_queue)
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
        if self.controller.closing or self.controller.busy_operation or self.controller.remove_queue or not self.entries:
            return
        if not self.controller.has_csrf or not self.controller.has_session:
            self.clear()
            return
        if key is not None and key not in self.entries:
            return
        self.timer.stop()
        self.queue = list(self.entries.values()) if key is None else [self.entries[key]]
        self._start_next()

    def _start_next(self):
        if self.controller.closing or not self.queue:
            return
        self.notice.set_items(list(self.entries.values()))
        self.notice.present('正在撤回移除…')
        if not self.controller.start_restore(self.queue[0],self.completed,self.failed):
            self.queue.clear()
            self.show_result('撤回失败，可重试')

    def accepts(self, request_id, generation):
        return bool(self.queue) and self.controller.current_operation(request_id,generation,'restore')

    def completed(self, request_id, generation, payload):
        if not self.accepts(request_id,generation):
            return
        item = self.queue.pop(0)
        self.entries.pop(item.key,None)
        if not self.controller.complete_restore(request_id,generation,item,self.baseline,has_more=bool(self.queue)):
            return
        self.restore_order(self.order_keys,item.bvid)
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
        self.controller.operation_failed(request_id,generation,error)
        self.show_result('撤回未完成，可重试')
