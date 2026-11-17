"""Canonical list/account/operations and QThread ownership; the page renders results."""
from __future__ import annotations
from dataclasses import dataclass
import time
from typing import Any
from PySide6.QtCore import QObject, QTimer, Qt, Signal
from .bili_api import BiliAPIError, describe_error
from .models import WatchLaterItem, WatchLaterSnapshot, safe_int
from .workers import AddRequest, ClearRequest, ListRequest, RemoveRequest
from .request_retirement import dispose_finished_request, retire_page_request

CACHE_TTL_SECONDS = 300.0

@dataclass(slots=True)
class Account:
    cookies: dict[str, str]
    generation: int
    has_session: bool
    has_csrf: bool
    error: BaseException | None = None


class ListController(QObject):
    removed = Signal(object)
    operation_finished = Signal()
    clearing = Signal()

    def __init__(self, view, context):
        super().__init__(view)
        self.view = view
        self.context = context
        self.closing = False
        self.items: list[WatchLaterItem] = []
        self.items_version = 0
        self.cookies: dict[str, str] = {}
        self.has_session = False
        self.has_csrf = False
        self.generation = 0
        self.loaded_once = False
        self.loaded_at = 0.0
        self.last_update_epoch = 0.0
        self.request_seq = 0
        self.operation_seq = 0
        self.active_list: ListRequest | None = None
        self.active_operation: Any = None
        self.busy_operation = ""
        self.pending_remove: WatchLaterItem | None = None
        self.requests: set[Any] = set()
        self.remove_queue: list[str] = []

    def read_account(self) -> Account:
        try:
            snapshot = self.context.credentials("bilibili")
        except Exception as error:
            return Account({}, self.generation, False, False, error)
        if snapshot is None:
            return Account({}, self.generation, False, False, None)
        values = getattr(snapshot, "values", None)
        if values is None and isinstance(snapshot, dict):
            values = snapshot
        cookies: dict[str, str] = {}
        try:
            if values is not None:
                for key, value in dict(values).items():
                    if value is not None:
                        cookies[str(key)] = str(value)
        except (TypeError, ValueError) as error:
            return Account({}, self.generation, False, False, error)
        generation = safe_int(getattr(snapshot, "generation", 0))
        return Account(
            cookies=cookies,
            generation=generation,
            has_session=bool(cookies.get("SESSDATA", "").strip()),
            has_csrf=bool(cookies.get("bili_jct", "").strip()),
            error=None,
        )

    def clear_account_data(self, *, clear_credentials: bool = True) -> None:
        self.clearing.emit()
        self.view.card_preview.unavailable.clear()
        self.items.clear()
        self.items_version += 1
        self.view._clear_rows()
        self.view.covers.clear()
        # v1.0 需求：：账号数据作废时弹幕数缓存一并清空。
        if self.view._danmaku is not None:
            self.view._danmaku.clear()
        self.loaded_once = False
        self.loaded_at = 0.0
        self.last_update_epoch = 0.0
        self.pending_remove = None
        self.view.error_bar.clear_error()
        if clear_credentials:
            self.cookies = {}
            self.has_session = False
            self.has_csrf = False
        self.view._clear_status()
        self.view._set_refreshing(False)
        self.view._update_header(0, 0)
        self.view._update_destructive_controls()
        self.view.items_changed.emit([])

    def on_account_changed(self, generation: Any = None) -> None:
        if self.closing:
            return
        self.clearing.emit()
        self.view.card_preview.unavailable.clear()
        self.view.browse_state.clear()
        self.request_seq += 1
        self.operation_seq += 1
        self.cancel_request(self.active_list)
        self.cancel_request(self.active_operation)
        self.active_list = None
        self.active_operation = None
        self.busy_operation = ""
        self.items.clear()
        self.items_version += 1
        self.view._clear_rows()
        self.view.covers.clear()
        # v1.0 需求：：账号数据作废时弹幕数缓存一并清空。
        if self.view._danmaku is not None:
            self.view._danmaku.clear()
        self.loaded_once = False
        self.loaded_at = 0.0
        self.last_update_epoch = 0.0
        self.pending_remove = None
        self.view.error_bar.clear_error()
        self.view._clear_status()
        self.view._set_refreshing(False)
        if self.view.wall is not None:
            try:
                self.view.wall.clear_bindings()
            except Exception:
                pass

        account = self.read_account()
        if account.generation:
            self.generation = account.generation
        else:
            self.generation = safe_int(generation)
        self.cookies = account.cookies
        self.has_session = account.has_session
        self.has_csrf = account.has_csrf
        self.view._login_expired = False
        self.view._set_refreshing(False)
        self.view._update_destructive_controls()

        self.view.items_changed.emit([])
        if not self.view._visible:
            return
        if account.error is not None:
            self.view._show_list_error(
                "无法读取登录信息：" + describe_error(account.error),
                login_expired=False,
            )
            return
        if not account.has_session:
            self.view._show_login_state(expired=False)
            return
        self.load(manual=False)

    def cache_fresh(self) -> bool:
        return (
            self.loaded_once
            and time.monotonic() - self.loaded_at < CACHE_TTL_SECONDS
        )

    def load(self, *, manual: bool) -> None:
        # 按钮、菜单、F5、画面墙共用单飞守卫，不取消后重开同一请求。
        if self.closing or self.busy_operation or self.active_list is not None:
            return
        if manual:
            self.view.card_preview.unavailable.clear()
        self.view._clear_status()
        account = self.read_account()
        if account.error is not None:
            self.view._show_list_error(
                "无法读取登录信息：" + describe_error(account.error),
                login_expired=False,
            )
            return
        if not account.has_session:
            self.clear_account_data()
            self.view._show_login_state(expired=False)
            return
        if account.generation and account.generation != self.generation:
            self.generation = account.generation
            self.clear_account_data(clear_credentials=False)
        self.cookies = account.cookies
        self.has_session = True
        self.has_csrf = account.has_csrf
        self.view._update_destructive_controls()

        self.request_seq += 1
        request_id = self.request_seq
        self.cancel_request(self.active_list)
        self.active_list = None

        self.view.error_bar.clear_error()
        self.view._set_refreshing(True)
        if not self.loaded_once:
            self.view._show_loading()

        worker = ListRequest(
            self.cookies, self.generation, request_id
        )
        self.active_list = worker
        worker.completed.connect(self.list_completed)
        worker.failed.connect(self.list_failed)
        self.track(worker)

    def list_completed(
        self, request_id: int, generation: int, snapshot: Any
    ) -> None:
        if self.closing:
            return
        if request_id != self.request_seq or generation != self.generation:
            return
        self.active_list = None
        self.view._set_refreshing(False)
        if not isinstance(snapshot, WatchLaterSnapshot):
            self.list_failed(
                request_id,
                generation,
                BiliAPIError("数据格式异常", format_error=True),
            )
            return
        self.items = list(snapshot.items)
        self.items_version += 1
        self.loaded_once = True
        self.loaded_at = time.monotonic()
        self.last_update_epoch = time.time()
        self.view.error_bar.clear_error()
        self.view._log(
            "list endpoint=%s status=%s code=0 count=%s total=%s"
            % (
                snapshot.endpoint,
                snapshot.http_status,
                len(snapshot.items),
                snapshot.total,
            )
        )
        self.view._render()
        self.view._publish_to_wall()

    def list_failed(
        self, request_id: int, generation: int, error: Any
    ) -> None:
        if self.closing:
            return
        if request_id != self.request_seq or generation != self.generation:
            return
        self.active_list = None
        self.view._set_refreshing(False)
        if not isinstance(error, BaseException):
            error = BiliAPIError("请求失败，请重试")
        self.view._log(
            "list failed endpoint=%s status=%s code=%s"
            % (
                getattr(error, "endpoint", ""),
                getattr(error, "http_status", ""),
                getattr(error, "code", ""),
            )
        )
        login_expired = bool(
            isinstance(error, BiliAPIError) and error.login_required
        )
        self.view._show_list_error(
            describe_error(error), login_expired=login_expired
        )

    def remove_reason(self, item: WatchLaterItem) -> str:
        if self.closing:
            return "插件正在关闭"
        if not self.has_session:
            return "请先登录 B 站"
        if item.aid <= 0:
            return "该条目缺少 aid，请到网页版处理"
        if not self.has_csrf:
            return "登录信息不完整，请重新登录后再试"
        if self.busy_operation:
            return "正在执行其他操作，请稍候"
        return ""

    def begin_remove(self, item: WatchLaterItem) -> None:
        if self.busy_operation or not self.has_csrf:
            return
        self.cancel_refresh_for_operation()
        self.operation_seq += 1
        request_id = self.operation_seq
        self.pending_remove = item
        self.busy_operation = "remove"
        self.view.error_bar.clear_error()
        self.view._set_refreshing(False)
        self.view._update_destructive_controls()
        worker = RemoveRequest(
            self.cookies,
            self.generation,
            request_id,
            item.aid,
        )
        self.active_operation = worker
        worker.completed.connect(self.remove_completed)
        worker.failed.connect(self.operation_failed)
        self.track(worker)

    def remove_completed(
        self, request_id: int, generation: int, _payload: Any
    ) -> None:
        if self.closing:
            return
        if (
            request_id != self.operation_seq
            or generation != self.generation
            or self.busy_operation != "remove"
        ):
            return
        item = self.pending_remove
        if item is not None:
            self.removed.emit(item)
            self.items = [
                current
                for current in self.items
                if current.key != item.key
            ]
        self.pending_remove = None
        self.items_version += 1
        self.loaded_at = time.monotonic()
        self.last_update_epoch = time.time()
        self.end_operation()
        self.view._render_after_remove()
        self.view._publish_to_wall()
        self.operation_finished.emit()
        self.view._log("remove endpoint=del status=200 code=0 count=1")
        # v1.0 需求 10：多选删除串行推进下一个（队列空时立即返回）。
        QTimer.singleShot(0, self.pump_multi_remove)

    def begin_clear(self) -> None:
        if self.busy_operation or not self.has_csrf:
            return
        self.clearing.emit()
        self.cancel_refresh_for_operation()
        self.operation_seq += 1
        request_id = self.operation_seq
        self.busy_operation = "clear"
        self.view.error_bar.clear_error()
        self.view._set_refreshing(False)
        self.view._update_destructive_controls()
        worker = ClearRequest(
            self.cookies, self.generation, request_id
        )
        self.active_operation = worker
        worker.completed.connect(self.clear_completed)
        worker.failed.connect(self.operation_failed)
        self.track(worker)

    def clear_completed(
        self, request_id: int, generation: int, _payload: Any
    ) -> None:
        if self.closing:
            return
        if (
            request_id != self.operation_seq
            or generation != self.generation
            or self.busy_operation != "clear"
        ):
            return
        self.items.clear()
        self.items_version += 1
        self.view.covers.clear()
        self.loaded_once = True
        self.loaded_at = time.monotonic()
        self.last_update_epoch = time.time()
        self.end_operation()
        self.view._render()
        self.view._publish_to_wall()
        self.view._flash_status("已清空")
        self.view._log("clear endpoint=clear status=200 code=0 count=0")

    def operation_failed(
        self, request_id: int, generation: int, error: Any
    ) -> None:
        if self.closing:
            return
        if (
            request_id != self.operation_seq
            or generation != self.generation
            or not self.busy_operation
        ):
            return
        if not isinstance(error, BaseException):
            error = BiliAPIError("操作失败，请重试")
        self.view._log(
            "operation failed endpoint=%s status=%s code=%s"
            % (
                getattr(error, "endpoint", ""),
                getattr(error, "http_status", ""),
                getattr(error, "code", ""),
            )
        )
        login_expired = bool(
            isinstance(error, BiliAPIError) and error.login_required
        )
        self.end_operation()
        self.remove_queue.clear()  # v1.0：失败即停止后续多选删除
        if login_expired:
            self.clear_account_data()
            self.view._show_login_state(expired=True)
            return
        self.view.error_bar.show_error("操作失败：" + describe_error(error))
        self.operation_finished.emit()

    def end_operation(self) -> None:
        self.busy_operation = ""
        self.pending_remove = None
        self.view._set_refreshing(False)
        self.view._update_destructive_controls()

    def cancel_refresh_for_operation(self) -> None:
        self.request_seq += 1
        self.cancel_request(self.active_list)
        self.active_list = None

    def pump_multi_remove(self) -> None:
        if (self.closing or self.busy_operation
                or not self.remove_queue):
            return
        key = self.remove_queue.pop(0)
        item = next((it for it in self.items if it.key == key), None)
        if item is None:
            QTimer.singleShot(0, self.pump_multi_remove)
            return
        self.begin_remove(item)

    @staticmethod
    def cancel_request(worker: Any) -> None:
        if worker is None:
            return
        try:
            worker.cancel()
        except Exception:
            pass

    def track(self, worker: Any) -> None:
        self.requests.add(worker)
        worker.finished.connect(
            self.request_finished,
            Qt.QueuedConnection,
        )
        try:
            worker.start()
        except Exception as error:
            self.requests.discard(worker)
            list_failed = self.active_list is worker
            if self.active_list is worker:
                self.active_list = None
            if self.active_operation is worker:
                self.active_operation = None
            dispose_finished_request(worker)
            self.busy_operation = ""
            self.view._set_refreshing(False)
            self.view._update_destructive_controls()
            if list_failed:
                self.view._show_list_error(
                    "请求失败：" + describe_error(error),
                    login_expired=False,
                )
            else:
                self.view.error_bar.show_error(
                    "操作失败：" + describe_error(error)
                )

    def request_finished(self) -> None:
        worker = self.sender()
        if worker is not None:
            self.retire(worker)

    def retire(self, worker: Any) -> None:
        self.requests.discard(worker)
        if self.active_list is worker:
            self.active_list = None
        if self.active_operation is worker:
            self.active_operation = None
        dispose_finished_request(worker)

    def current_operation(self, request_id, generation, kind):
        return (not self.closing and request_id == self.operation_seq
                and generation == self.generation and self.busy_operation == kind)

    def start_restore(self, item, completed, failed):
        self.cancel_refresh_for_operation()
        self.operation_seq += 1
        self.busy_operation = 'restore'
        self.view._set_refreshing(False)
        self.view._update_destructive_controls()
        self.view.error_bar.clear_error()
        worker = AddRequest(self.cookies,self.generation,self.operation_seq,item.aid)
        self.active_operation = worker
        worker.completed.connect(completed)
        worker.failed.connect(failed)
        self.track(worker)
        return self.active_operation is not None

    def complete_restore(self, request_id, generation, item, baseline, *, has_more):
        if not self.current_operation(request_id,generation,'restore'):
            return False
        if not any(current.key == item.key for current in self.items):
            self.items.append(item)
            rank = {key:i for i,key in enumerate(baseline)}
            self.items.sort(key=lambda current:rank.get(current.key,len(rank)))
        self.items_version += 1
        self.loaded_at, self.last_update_epoch = time.monotonic(), time.time()
        if not has_more:
            self.end_operation()
        self.view._render_after_remove()
        if self.closing or generation != self.generation:
            return False
        self.view._publish_to_wall()
        return True

    def shutdown(self):
        self.request_seq += 1
        self.operation_seq += 1
        self.cancel_request(self.active_list)
        self.cancel_request(self.active_operation)
        for worker in tuple(self.requests):
            self.cancel_request(worker)
        for worker in tuple(self.requests):
            worker.finished.disconnect(self.request_finished)
            for name in ('completed','failed'):
                signal = getattr(worker,name,None)
                if signal is not None:
                    signal.disconnect()
            retire_page_request(worker)
            self.requests.discard(worker)
        self.active_list = self.active_operation = None
        self.busy_operation = ''
        self.pending_remove = None

    def remove_many(self, items):
        self.remove_queue = [item.key for item in items]
        self.pump_multi_remove()

    def cancel_pending_removals(self):
        self.remove_queue.clear()

    def clear_shutdown_data(self):
        self.items.clear()
        self.items_version += 1
        self.view._clear_rows()
        self.cookies.clear()
        self.has_session = self.has_csrf = False
        self.view._update_destructive_controls()
