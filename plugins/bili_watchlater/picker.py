"""Item picker dialog used by the wall menu.

Read-only selection over the page's in-memory list.  No network, no writes.
"""
from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .models import WatchLaterItem, format_duration, matches_query


class WatchLaterPicker(QDialog):
    """Pick one watch-later item for a tile."""

    def __init__(
        self,
        items: Sequence[WatchLaterItem],
        parent: QWidget | None = None,
        *,
        title: str = "选择稍后再看视频",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(560, 460)
        self._items = list(items)
        self._selected: WatchLaterItem | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 12)
        layout.setSpacing(8)

        self.search = QLineEdit(self)
        self.search.setPlaceholderText("搜索标题 / UP主 / BV号")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("搜索稍后再看")
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)

        self.list = QListWidget(self)
        self.list.setAccessibleName("稍后再看列表")
        self.list.itemDoubleClicked.connect(lambda _item: self._accept_current())
        layout.addWidget(self.list, 1)

        self.hint = QLabel("", self)
        self.hint.setTextFormat(Qt.PlainText)
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._accept_current)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._populate(self._items)

    # ------------------------------------------------------------------ data
    def _populate(self, items: Sequence[WatchLaterItem]) -> None:
        self.list.clear()
        for item in items:
            meta = [item.owner_name]
            if item.duration > 0:
                meta.append(format_duration(item.duration))
            if item.is_multi_part:
                meta.append(f"共 {item.parts} P")
            if item.progress_text:
                meta.append(item.progress_text)
            entry = QListWidgetItem(f"{item.title}\n{' · '.join(meta)}")
            entry.setData(Qt.UserRole, item)
            entry.setToolTip(item.title)
            self.list.addItem(entry)
        if items:
            self.list.setCurrentRow(0)
            self.hint.setText("双击可直接选择。")
        else:
            self.hint.setText("没有匹配的视频。")

    def _filter(self, text: str) -> None:
        query = (text or "").strip()
        if not query:
            self._populate(self._items)
            return
        self._populate([item for item in self._items if matches_query(item, query)])

    def _accept_current(self) -> None:
        entry = self.list.currentItem()
        if entry is None:
            return
        item = entry.data(Qt.UserRole)
        if isinstance(item, WatchLaterItem):
            self._selected = item
            self.accept()

    # ------------------------------------------------------------------ API
    def selected_item(self) -> WatchLaterItem | None:
        return self._selected

    @staticmethod
    def pick(
        items: Sequence[WatchLaterItem], parent: QWidget | None = None
    ) -> WatchLaterItem | None:
        dialog = WatchLaterPicker(items, parent)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                return dialog.selected_item()
            return None
        finally:
            dialog.deleteLater()
