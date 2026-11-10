"""Shared crop UI used by the isolated worker and older-host compatibility."""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtWidgets import QDialogButtonBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout
from ddm import theme
from .canvas import Canvas
from .crop_dialog import CropDialog


class CropPicker(CropDialog):
    def __init__(self, parent, snapshot, *, native_caption=False):
        super().__init__(parent, native_caption=native_caption)
        self.frame = None
        self.setWindowModality(Qt.NonModal)
        self.setWindowTitle(snapshot['title'])
        self.resize(900, 600)
        self.canvas = Canvas(self, selecting=True)
        self.canvas.crop = QRectF(*snapshot['crop'])
        self.canvas.selection_ready = snapshot['selection_ready']
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setObjectName('MatchSyncCropConfirm')
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        reset = QPushButton('使用整个画面')
        reset.clicked.connect(self.canvas.reset_selection)
        layout = QVBoxLayout(self)
        hint = QLabel('Ctrl＋滚轮：缩放　滚轮：上下平移　Shift＋滚轮：左右平移'
                      if snapshot['floating'] else '自动匹配始终使用完整画面；框选仅用于放大对照、人工微调偏移。'
                      '两路请选择相同计时器，框选不会改变自动匹配或读取计时器数字。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addWidget(self.canvas, 1)
        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(reset)
        actions.addWidget(self.buttons)
        layout.addLayout(actions)
        self.setStyleSheet(f"""
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
        self.set_frame(None)
        self.finished.connect(self.deleteLater)

    @property
    def crop(self):
        return self.canvas.crop

    def set_frame(self, frame):
        self.frame = frame
        self.canvas.set_frame(frame)
        self.canvas.waiting = frame is None
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(frame is not None)
