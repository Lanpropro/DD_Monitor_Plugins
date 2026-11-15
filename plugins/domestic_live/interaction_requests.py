"""Cancellable interaction request thread; owned and retired by its controller."""
import threading
import time
import requests
from PySide6.QtCore import QThread, Signal
from .interaction_models import InteractionError


class Request(QThread):
    result = Signal(object, object, object)
    phase = Signal(object, str)

    def __init__(self, key, operation):
        super().__init__()
        self.key, self.operation = key, operation
        self.cancelled = threading.Event()
        self.started_at = time.monotonic()

    def run(self):
        try:
            value = self.operation(self.cancelled.is_set, lambda stage: self.phase.emit(self, stage))
        except Exception as error:
            room = self.key[-1] if isinstance(self.key, tuple) and self.key and isinstance(self.key[-1], dict) else {}
            platform = {"douyu": "斗鱼", "huya": "虎牙", "douyin": "抖音"}.get(room.get("platform"), "斗鱼")
            value = error if isinstance(error, InteractionError) else InteractionError(
                f"{platform}网络请求失败，请稍后重试" if isinstance(error, requests.RequestException)
                else f"{platform}互动请求失败，请刷新后重试")
        self.result.emit(self, self.key, value)
