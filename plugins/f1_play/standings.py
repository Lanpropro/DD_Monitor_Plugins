"""Rankings panel whose clock is anchored to the displayed picture."""
import json
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QInputDialog,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from .timing import Leaderboard


OFFICIAL_BOARD = 'https://www.formula1.com/en/timing/f1-live-lite#live-leaderboard'
TYRES = {'HARD': ('H', '硬胎', '#dce3ec'), 'MEDIUM': ('M', '中性胎', '#e6b931'),
         'SOFT': ('S', '软胎', '#f7646c'), 'INTERMEDIATE': ('I', '半雨胎', '#54b777'),
         'WET': ('W', '全雨胎', '#66aff0')}


class StandingsPane(QFrame):
    def __init__(self, picture_position):
        super().__init__()
        self.position = picture_position
        self.history = None
        self.anchor = None
        self.last_snapshot = None
        self.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        title = QLabel('即时排行')
        title.setObjectName('SettingsTitle')
        header.addWidget(title, 1)
        self.lap = QLabel()
        header.addWidget(self.lap)
        layout.addLayout(header)
        self.session = QLabel('载入 OpenF1 历史数据后可随画面更新')
        self.session.setWordWrap(True)
        self.session.setTextFormat(Qt.PlainText)
        layout.addWidget(self.session)
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumHeight(72)
        self.set_headers(True)
        layout.addWidget(self.table, 1)
        actions = QHBoxLayout()
        load = QPushButton('载入数据')
        load.setObjectName('IconButton')
        load.clicked.connect(self.choose_data)
        actions.addWidget(load)
        self.align = QPushButton('对齐')
        self.align.setObjectName('IconButton')
        self.align.setToolTip('将当前画面对应到 OpenF1 场次时间')
        self.align.clicked.connect(self.choose_anchor)
        self.align.setEnabled(False)
        actions.addWidget(self.align)
        official = QPushButton('官网免费榜 ↗')
        official.setObjectName('IconButton')
        official.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(OFFICIAL_BOARD)))
        actions.addWidget(official)
        layout.addLayout(actions)

    def set_headers(self, race):
        headers = ['名次', '车手', '距榜首', '轮胎'] if race else ['名次', '车手', '最快圈', '距榜首', '轮胎']
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        for index, width in ((0, 44), (1, 52), (len(headers) - 1, 46)):
            header.setSectionResizeMode(index, QHeaderView.Fixed)
            self.table.setColumnWidth(index, width)

    def choose_data(self):
        filename, _ = QFileDialog.getOpenFileName(self, '载入 OpenF1 历史数据', '', 'OpenF1 数据 (*.json)')
        if filename:
            self.load_data(filename)

    def load_data(self, filename):
        try:
            data = json.loads(Path(filename).read_text(encoding='utf-8-sig'))
            history = Leaderboard(data)
        except (OSError, ValueError, OverflowError, TypeError, AttributeError, KeyError):
            self.session.setText('读取失败，请选择有效的 OpenF1 场次数据文件。')
            return False
        self.history = history
        self.set_headers(history.race)
        self.align.setEnabled(True)
        self.reset_anchor()
        return True

    def reset_anchor(self):
        self.anchor = None
        self.apply_snapshot(None, [])
        if self.history is not None:
            self.session.setText(f'{self.history.title} · 等待对齐')

    def choose_anchor(self):
        position = self.position()
        if position is None:
            self.session.setText('等主画面出画后再对齐。')
            return
        elapsed, accepted = QInputDialog.getInt(
            self, '对齐排行', '当前画面距离 OpenF1 场次起点的秒数', 0, 0, 86400)
        if accepted:
            self.set_anchor(position, elapsed)

    def set_anchor(self, picture_seconds, race_seconds):
        self.anchor = race_seconds - picture_seconds
        self.session.setText(f'{self.history.title} · 随主画面更新')
        self.update_picture()

    def update_picture(self):
        if self.anchor is None or not self.isVisible():
            return
        position = self.position()
        if position is not None:
            self.apply_snapshot(*self.history.at(position + self.anchor))

    def apply_snapshot(self, lap, rows):
        if self.last_snapshot == (lap, rows):
            return
        self.last_snapshot = lap, rows
        self.lap.setText(f'第 {lap} 圈' if lap is not None else '')
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(rows))
        for y, row in enumerate(rows):
            for x, value in enumerate(row):
                tyre = TYRES.get(value) if x == len(row) - 1 else None
                item = QTableWidgetItem(tyre[0] if tyre else value)
                item.setTextAlignment(Qt.AlignCenter)
                if tyre:
                    item.setToolTip(tyre[1])
                    item.setForeground(QColor(tyre[2]))
                self.table.setItem(y, x, item)
        self.table.setUpdatesEnabled(True)
