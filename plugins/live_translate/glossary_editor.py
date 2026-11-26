"""Room/language glossary editing; committed values use the host settings API."""
from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import QPlainTextEdit, QVBoxLayout, QWidget
from ddm.ui import HintLabel
from .panel_controls import RoundedAction, StableSelect
from .glossary import parse_terms, room_key


class GlossaryEditor(QWidget):
    changed = Signal(object)

    def __init__(self, values, parent=None):
        super().__init__(parent)
        self.values = dict(values or {})
        self.language = '简体中文'
        self._context = None
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        self.room = StableSelect(self)
        self.room.setAccessibleName('术语所属直播间')
        self.room.setMinimumWidth(0)
        box.addWidget(self.room)
        self.editor = QPlainTextEdit(self)
        self.editor.setFixedHeight(112)
        self.editor.setMinimumWidth(0)
        self.editor.setAccessibleName('直播间术语')
        self.editor.setPlaceholderText('每行：原词 = 译法\n例：ぺこら = 佩克拉')
        self.editor.setToolTip('每间直播、每种字幕语言最多 20 条，各 1–40 字。原词与译法相同表示保留专名。编辑后保存，停止后生效。')
        box.addWidget(self.editor)
        self.note = HintLabel('选择直播间后编辑', self)
        box.addWidget(self.note)
        self.save = RoundedAction('保存术语', self)
        self.save.clicked.connect(self.commit)
        box.addWidget(self.save)
        self.room.currentIndexChanged.connect(self._load)

    def refresh(self, sources):
        previous = self.room.currentData()
        items = []
        seen = set()
        for source in sources.values():
            key = room_key(source)
            if key not in seen:
                items.append((source.uname or source.room_id, key))
                seen.add(key)
        if items != [(self.room.itemText(i), self.room.itemData(i)) for i in range(self.room.count())]:
            blocker = QSignalBlocker(self.room)
            self.room.clear()
            for title, key in items:
                self.room.addItem(title, key)
            self.room.setCurrentIndex(max(0, self.room.findData(previous)))
            del blocker
        self._load()

    def set_language(self, language):
        self.language = language.strip() or '简体中文'
        self._load()

    def _load(self, _index=None):
        key = self.room.currentData()
        context = (key, self.language)
        if context == self._context:
            return
        self._context = context
        entries = self.values.get(key, {}).get(self.language, ())
        self.editor.setPlainText('\n'.join(' = '.join(entry) for entry in entries))
        self.editor.setEnabled(key is not None)
        self.save.setEnabled(key is not None)
        self.note.setText(f'字幕语言：{self.language} · 保存后用于此直播间' if key else '请先播放直播间')

    def commit(self):
        key = self.room.currentData()
        if key is None:
            return
        try:
            entries = parse_terms(self.editor.toPlainText())
        except ValueError as error:
            self.note.setText(str(error))
            return
        profiles = dict(self.values.get(key, {}))
        profiles[self.language] = [list(entry) for entry in entries]
        self.values = dict(self.values, **{key: profiles})
        self.changed.emit(self.values)
        self.note.setText(f'已保存 {len(entries)} 条 · {self.language}')
