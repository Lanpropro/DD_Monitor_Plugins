"""Manual caption inspector; project document owns all editable caption state."""
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFontComboBox,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)
from .subtitles import anchor_cues, default_style, placed_cues, set_cue
from .subtitle_srt import parse_srt, srt_text


class SubtitlePanel(QWidget):
    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        box = QVBoxLayout(self)
        box.setContentsMargins(2, 2, 2, 2)
        row = QHBoxLayout()
        self.action('添加字幕', self.add, row)
        self.action('导入SRT', self.import_srt, row)
        self.action('导出SRT', self.export_srt, row)
        box.addLayout(row)
        self.rows = QTreeWidget()
        self.rows.setHeaderLabels(['时间', '字幕'])
        self.rows.setRootIsDecorated(False)
        self.rows.setMinimumHeight(100)
        self.rows.setMaximumHeight(220)
        self.rows.setAccessibleName('工程字幕列表')
        self.rows.itemClicked.connect(lambda item: self.pick(item, False))
        self.rows.itemDoubleClicked.connect(lambda item: self.pick(item, True))
        box.addWidget(self.rows)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setObjectName('SettingsHint')
        box.addWidget(self.hint)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        box.addWidget(scroll, 1)
        content = QWidget()
        form = QFormLayout(content)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.start, self.end = QDoubleSpinBox(), QDoubleSpinBox()
        for field, title in [(self.start, '字幕入点'), (self.end, '字幕出点')]:
            field.setRange(0, 864000)
            field.setDecimals(3)
            field.setKeyboardTracking(False)
            field.setAccessibleName(title)
            form.addRow(title+' 秒', field)
        row = QHBoxLayout()
        self.action('当前时间作入点', lambda: self.start.setValue(editor.position), row)
        self.action('当前时间作出点', lambda: self.end.setValue(editor.position), row)
        form.addRow(row)
        self.text = QPlainTextEdit()
        self.text.setMaximumHeight(90)
        self.text.setAccessibleName('字幕文本')
        form.addRow(self.text)
        row = QHBoxLayout()
        self.action('应用字幕', self.apply, row)
        self.action('删除字幕', self.remove, row)
        form.addRow(row)
        self.burn = QCheckBox('将字幕压入视频')
        self.burn.toggled.connect(self.apply_style)
        form.addRow(self.burn)
        self.fields = {}
        self.font = QFontComboBox()
        form.addRow('字体', self.font)
        for key, label, low, high in [('size', '字号(1080p)', 8, 200), ('outline', '描边(1080p)', 0, 12), ('margin', '边距 %', 0, 40)]:
            field = QDoubleSpinBox()
            field.setRange(low, high)
            field.setDecimals(1)
            self.fields[key] = field
            form.addRow(label, field)
        for key, label in [('color', '文字颜色'), ('outline_color', '描边颜色')]:
            field = QLineEdit()
            self.fields[key] = field
            form.addRow(label, field)
        self.position = QComboBox()
        for title, value in [('下方居中', 'bottom'), ('画面居中', 'middle'), ('上方居中', 'top')]:
            self.position.addItem(title, value)
        form.addRow('字幕位置', self.position)
        for key, label in [('bold', '加粗'), ('italic', '斜体'), ('background', '半透明背景')]:
            field = QCheckBox(label)
            self.fields[key] = field
            form.addRow(field)
        self.action('应用样式', self.apply_style, form)
        scroll.setWidget(content)

    def action(self, title, callback, layout):
        button = QPushButton(title)
        button.setObjectName('IconButton')
        button.clicked.connect(callback)
        if isinstance(layout, QFormLayout):
            layout.addRow(button)
        else:
            layout.addWidget(button)
        return button

    def refresh(self):
        data = self.editor.document.data
        self.rows.clear()
        cues = placed_cues(data)
        for cue, start, end in cues:
            item = QTreeWidgetItem([f'{start:.3f} → {end:.3f}', cue['text']])
            item.setData(0, Qt.UserRole, cue['id'])
            item.setToolTip(1, cue['text'])
            self.rows.addTopLevelItem(item)
            if cue['id'] == self.editor.selected_id:
                self.rows.setCurrentItem(item)
        hidden = len(data.get('subtitles', []))-len(cues)
        self.hint.setText(f'{len(cues)}条字幕，{hidden}条在裁剪范围外。双击字幕定位；时间轴可拖动打轴。')
        style = {**default_style(), **data.get('subtitle_style', {})}
        from PySide6.QtGui import QFont
        from ddm import theme
        self.font.setCurrentFont(QFont(style['font'] or theme.danmaku_font_family()))
        for key, field in self.fields.items():
            if isinstance(field, QLineEdit):
                field.setText(style[key])
            elif isinstance(field, QCheckBox):
                field.setChecked(style[key])
            else:
                field.setValue(style[key])
        self.position.setCurrentIndex(self.position.findData(style['position']))
        self.burn.blockSignals(True)
        self.burn.setChecked(data.get('burn_subtitles', True))
        self.burn.blockSignals(False)
        self.select(self.editor.selected_id)

    def select(self, identity):
        row = next((row for row in placed_cues(self.editor.document.data) if row[0]['id'] == identity), None)
        self.start.setValue(row[1] if row else self.editor.position)
        self.end.setValue(row[2] if row else self.editor.position+2)
        self.text.setPlainText(row[0]['text'] if row else '')

    def pick(self, item, seek):
        identity = item.data(0, Qt.UserRole)
        self.editor.select_item(identity)
        if seek:
            self.editor.seek(self.start.value())

    def add(self):
        from .project import placed_clips
        end = next((stop for _, start, stop in placed_clips(self.editor.document.data) if start <= self.editor.position < stop), self.editor.position)
        identity = self.editor.change(lambda data: set_cue(data, '', self.editor.position, min(end, self.editor.position+2), '字幕'))
        if identity:
            self.editor.select_item(identity)

    def apply(self):
        identity = self.editor.selected_id
        if not any(cue['id'] == identity for cue in self.editor.document.data.get('subtitles', [])):
            self.editor.status.setText('请先选择一条字幕')
            return
        self.editor.change(lambda data: set_cue(data, identity, self.start.value(), self.end.value(), self.text.toPlainText()))
        self.editor.select_item(identity)

    def remove(self):
        identity = self.editor.selected_id
        self.editor.change(lambda data: data.update(subtitles=[row for row in data.get('subtitles', []) if row['id'] != identity]))

    def apply_style(self, *_):
        style = dict(font=self.font.currentFont().family(), position=self.position.currentData())
        for key, field in self.fields.items():
            style[key] = field.text() if isinstance(field, QLineEdit) else field.isChecked() if isinstance(field, QCheckBox) else field.value()
        self.editor.change(lambda data: data.update(subtitle_style=style, burn_subtitles=self.burn.isChecked()))

    def import_srt(self):
        path, _ = QFileDialog.getOpenFileName(self, '导入工程时间轴字幕', '', '字幕 (*.srt)')
        if not path:
            return
        def load():
            content = Path(path).read_bytes()
            try:
                text = content.decode('utf-8-sig')
            except UnicodeDecodeError:
                text = content.decode('gb18030')
            cues = parse_srt(text)
            def change(data):
                rows = anchor_cues(data, cues)
                if not rows:
                    raise ValueError('SRT中没有位于主视频范围内的字幕')
                data.setdefault('subtitles', []).extend(rows)
            self.editor.change(change)
        self.editor.guard(load)

    def export_srt(self):
        path, _ = QFileDialog.getSaveFileName(self, '导出字幕', self.editor.document.data['title']+'.srt', '字幕 (*.srt)')
        if path:
            def save():
                target = Path(path if path.lower().endswith('.srt') else path+'.srt')
                with target.open('x', encoding='utf-8-sig') as handle:
                    handle.write(srt_text(self.editor.document.data))
                self.editor.status.setText('字幕已导出：'+str(target))
            self.editor.guard(save)
