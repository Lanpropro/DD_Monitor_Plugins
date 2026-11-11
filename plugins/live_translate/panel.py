"""Session settings and a small, bounded floating subtitle window."""
import json
import weakref

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
                              QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                              QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout)

from .caption_style import CaptionStyle
from .options import Options


class Captions(QDialog):
    stopRequested = Signal()

    def __init__(self, parent, style=None):
        super().__init__(parent, Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setWindowTitle('实时翻译字幕')
        self.resize(640, 155)
        self._style = CaptionStyle.from_mapping(style)
        layout = QVBoxLayout(self)
        self.text = QPlainTextEdit(self)
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(6)
        self.set_style(self._style)
        layout.addWidget(self.text)
        stop = QPushButton('停止传译', self)
        stop.clicked.connect(self.stopRequested)
        layout.addWidget(stop, alignment=Qt.AlignRight)

    def set_style(self, style):
        self._style = CaptionStyle.from_mapping(style)
        font = self.text.font()
        font.setPixelSize(self._style.font_size)
        self.text.setFont(font)

    def update_caption(self, original, translated):
        original, translated = self._style.text(original, translated)
        self.text.setPlainText(original + ('\n' + translated if translated else ''))

    def closeEvent(self, event):
        self.stopRequested.emit()
        super().closeEvent(event)


class Panel(QDialog):
    startRequested = Signal()
    stopRequested = Signal()
    captionStyleChanged = Signal(object)
    apiKeyChanged = Signal(str)

    def __init__(self, settings, parent=None, caption=None, api_key=''):
        super().__init__(parent, Qt.Tool)
        self.setWindowTitle('实时翻译设置')
        self.resize(560, 760)
        self._caption = CaptionStyle.from_mapping(caption)
        layout = QVBoxLayout(self)
        title = QLabel('实时翻译 · 开发预览', self)
        font = title.font()
        font.setPointSize(18)
        title.setFont(font)
        layout.addWidget(title)
        note = QLabel('为所选直播间开启字幕。收起设置后，字幕继续显示。', self)
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.sources = QComboBox(self)
        form.addRow('直播间', self.sources)
        self.engine = QComboBox(self)
        self.engine.addItem('ReazonSpeech INT8（日语／英语，小模型）', 'reazon')
        self.engine.addItem('Qwen3-ASR 0.6B INT8（多语言，对比候选）', 'qwen3_asr')
        self.engine.setCurrentIndex(max(0, self.engine.findData(settings.get('engine', 'reazon'))))
        form.addRow('本地识别', self.engine)
        self.model_dir = QLineEdit(settings.get('model_dir', ''), self)
        self._python = settings.get('python', '')
        self._path_row(form, '模型目录', self.model_dir, directory=True)
        self.source_language = QComboBox(self)
        self.source_language.addItems(['日语', '英语', '韩语', '中文'])
        self.source_language.setCurrentText(settings.get('source_language', '日语'))
        form.addRow('原声语言', self.source_language)
        self.target_language = QLineEdit(settings.get('target_language', '简体中文'), self)
        form.addRow('字幕语言', self.target_language)
        self.translation_mode = QComboBox(self)
        self.translation_mode.addItem('本地翻译（离线，无需 API Key）', 'local')
        self.translation_mode.addItem('API 翻译（填写自己的接口）', 'api')
        self.translation_mode.addItem('仅显示识别原文', 'original')
        mode = settings.get('translation_mode', 'api' if settings.get('endpoint') else 'local')
        self.translation_mode.setCurrentIndex(max(0, self.translation_mode.findData(mode)))
        form.addRow('翻译方式', self.translation_mode)
        self.threads = QSpinBox(self)
        self.threads.setRange(1, 8)
        self.threads.setValue(int(settings.get('threads', 2)))
        form.addRow('每个本地模型线程', self.threads)
        self.timeout = QDoubleSpinBox(self)
        self.timeout.setRange(0.2, 3)
        self.timeout.setSingleStep(0.1)
        self.timeout.setSuffix(' 秒')
        self.timeout.setValue(float(settings.get('timeout', 1.5)))
        form.addRow('翻译响应等待', self.timeout)
        layout.addLayout(form)
        caption_group = QGroupBox('字幕覆盖层', self)
        caption_form = QFormLayout(caption_group)
        self.caption_enabled = QCheckBox('显示内嵌字幕', caption_group)
        self.caption_enabled.setChecked(self._caption.enabled)
        caption_form.addRow(self.caption_enabled)
        self.caption_mode = QComboBox(caption_group)
        self.caption_mode.addItem('原文 + 译文', 'both')
        self.caption_mode.addItem('仅原文', 'original')
        self.caption_mode.addItem('仅译文', 'translation')
        self.caption_mode.setCurrentIndex(max(0, self.caption_mode.findData(self._caption.mode)))
        caption_form.addRow('显示组合', self.caption_mode)
        self.caption_lines = QSpinBox(caption_group)
        self.caption_lines.setRange(1, 4)
        self.caption_lines.setValue(self._caption.lines)
        caption_form.addRow('总行数', self.caption_lines)
        self.caption_opacity = QSpinBox(caption_group)
        self.caption_opacity.setRange(0, 100)
        self.caption_opacity.setSuffix(' %')
        self.caption_opacity.setValue(self._caption.opacity)
        caption_form.addRow('背景不透明度', self.caption_opacity)
        self.caption_font_size = QSpinBox(caption_group)
        self.caption_font_size.setRange(10, 64)
        self.caption_font_size.setSuffix(' px')
        self.caption_font_size.setValue(self._caption.font_size)
        caption_form.addRow('字体大小', self.caption_font_size)
        self.caption_width = QSpinBox(caption_group)
        self.caption_width.setRange(20, 100)
        self.caption_width.setSuffix(' %')
        self.caption_width.setValue(self._caption.width)
        caption_form.addRow('字幕框宽度', self.caption_width)
        self.caption_height = QSpinBox(caption_group)
        self.caption_height.setRange(8, 45)
        self.caption_height.setSuffix(' %')
        self.caption_height.setValue(self._caption.height)
        caption_form.addRow('字幕框高度', self.caption_height)
        caption_hint = QLabel('关闭内嵌字幕不会停止识别；旧宿主会自动使用独立字幕窗。', caption_group)
        caption_hint.setWordWrap(True)
        caption_form.addRow(caption_hint)
        layout.addWidget(caption_group)
        for widget in (self.caption_enabled, self.caption_mode, self.caption_lines,
                       self.caption_opacity, self.caption_font_size,
                       self.caption_width, self.caption_height):
            if isinstance(widget, QSpinBox):
                widget.valueChanged.connect(self._caption_changed)
            elif isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self._caption_changed)
            else:
                widget.toggled.connect(self._caption_changed)
        self.local_group = QGroupBox('本地翻译模型', self)
        local_form = QFormLayout(self.local_group)
        self.local_model = QLineEdit(settings.get('local_model', ''), self)
        self._path_row(local_form, '模型文件', self.local_model, file_filter='GGUF (*.gguf)')
        self.local_server = QLineEdit(settings.get('local_server', ''), self)
        self._path_row(local_form, '运行程序', self.local_server, file_filter='程序 (*.exe);;All files (*)')
        hint = QLabel('开始时加载本地模型，停止时释放；音频和文字均在本机处理。', self)
        hint.setWordWrap(True)
        local_form.addRow(hint)
        layout.addWidget(self.local_group)
        self.api_group = QGroupBox('API 翻译服务', self)
        api_form = QFormLayout(self.api_group)
        self.endpoint = QLineEdit(settings.get('endpoint', ''), self)
        self.endpoint.setPlaceholderText('例如 http://127.0.0.1:11434/v1，也可填写在线接口')
        api_form.addRow('翻译接口地址', self.endpoint)
        self.model = QLineEdit(settings.get('model', 'qwen3:0.6b'), self)
        api_form.addRow('翻译模型', self.model)
        self.key = QLineEdit(self)
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setText(api_key)
        self.key.setPlaceholderText('加密保存；清空可删除')
        api_form.addRow('API Key', self.key)
        self.key.editingFinished.connect(
            lambda: self.apiKeyChanged.emit(self.key.text().strip()))
        layout.addWidget(self.api_group)
        self.translation_mode.currentIndexChanged.connect(self._mode_changed)
        self._mode_changed()
        buttons = QHBoxLayout()
        self.start = QPushButton('开始传译', self)
        self.stop = QPushButton('停止', self)
        self.export = QPushButton('导出本次资源记录', self)
        self.start.clicked.connect(self.startRequested)
        self.stop.clicked.connect(self.stopRequested)
        buttons.addWidget(self.start)
        buttons.addWidget(self.stop)
        buttons.addWidget(self.export)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.status = QLabel('识别依赖在宿主进程内加载；关闭字幕窗或点击停止会释放模型与识别线程。', self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.resources = QLabel('识别线程与宿主共享内存；本地翻译/FFmpeg 子进程另计。', self)
        self.resources.setWordWrap(True)
        layout.addWidget(self.resources)
        self.preview = QPlainTextEdit(self)
        self.preview.setReadOnly(True)
        self.preview.setMaximumBlockCount(6)
        self.preview.setMaximumHeight(80)
        layout.addWidget(self.preview)
        layout.addStretch()
        self.set_running(False)

    def _path_row(self, form, label, editor, directory=False, file_filter='Python (python.exe);;All files (*)'):
        row = QHBoxLayout()
        row.addWidget(editor)
        choose = QPushButton('选择', self)
        def browse():
            path = (QFileDialog.getExistingDirectory(self, label, editor.text()) if directory else
                    QFileDialog.getOpenFileName(self, label, editor.text(), file_filter)[0])
            if path:
                editor.setText(path)
        choose.clicked.connect(browse)
        row.addWidget(choose)
        form.addRow(label, row)

    def _mode_changed(self):
        mode = self.translation_mode.currentData()
        self.local_group.setVisible(mode == 'local')
        self.api_group.setVisible(mode == 'api')

    def refresh_sources(self, sources, selected=None):
        old = self.sources.currentData()
        selected = selected or (old() if old else None)
        self.sources.clear()
        for tile, source in sources.items():
            self.sources.addItem(f'{source.uname or source.room_id} · {source.platform}', weakref.ref(tile))
            if tile is selected:
                self.sources.setCurrentIndex(self.sources.count() - 1)

    def options(self):
        return Options(engine=self.engine.currentData(), model_dir=self.model_dir.text().strip(),
                       python=self._python, endpoint=self.endpoint.text().strip(),
                       model=self.model.text().strip(), source_language=self.source_language.currentText(),
                       translation_mode=self.translation_mode.currentData(),
                       local_model=self.local_model.text().strip(), local_server=self.local_server.text().strip(),
                       target_language=self.target_language.text().strip() or '简体中文',
                       threads=self.threads.value(), timeout=self.timeout.value())

    def caption_options(self):
        return CaptionStyle(
            enabled=self.caption_enabled.isChecked(),
            mode=self.caption_mode.currentData(),
            lines=self.caption_lines.value(),
            opacity=self.caption_opacity.value(),
            width=self.caption_width.value(),
            height=self.caption_height.value(),
            font_size=self.caption_font_size.value(),
        )

    def _caption_changed(self, _value=None):
        self._caption = self.caption_options()
        self.captionStyleChanged.emit(self._caption)

    def set_running(self, running):
        self.start.setEnabled(not running)
        self.stop.setEnabled(running)
        self.translation_mode.setEnabled(not running)

    def closeEvent(self, event):
        self.apiKeyChanged.emit(self.key.text().strip())
        super().closeEvent(event)

    def update_caption(self, original, translated):
        original, translated = self.caption_options().text(original, translated)
        self.preview.setPlainText(original + ('\n' + translated if translated else ''))

    def update_metrics(self, value):
        if value['event'] == 'resources':
            memory = value['child_rss_bytes']
            label = f'{memory / 1024**2:.1f} MiB' if memory is not None else '不可用'
            self.resources.setText(
                f'FFmpeg/本地翻译子进程 RSS：{label}；识别在宿主线程内运行；'
                f'待发音频 {value["pending_pcm_bytes"]} B；过载丢弃 {value["dropped_audio_seconds"]:.2f} 秒。')
        elif value['event'] == 'caption':
            self.status.setText(f'本段识别 {value["asr_ms"]:.0f} ms，翻译 {value["translation_ms"]:.0f} ms；'
                                '仅统计分段后的处理耗时，不代表端到端字幕延迟。')

    def export_metrics(self, values):
        filename, _ = QFileDialog.getSaveFileName(self, '导出资源记录', 'live-translate-metrics.jsonl', 'JSONL (*.jsonl)')
        if filename:
            try:
                with open(filename, 'w', encoding='utf-8') as output:
                    for value in values:
                        output.write(json.dumps(value, ensure_ascii=False) + '\n')
            except OSError:
                self.status.setText('无法写入资源记录，请选择可写目录')
