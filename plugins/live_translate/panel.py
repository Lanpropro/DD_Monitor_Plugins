"""Session settings and a small, bounded floating subtitle window."""
import json
import weakref

from PySide6.QtCore import Qt, Signal, QSignalBlocker
from PySide6.QtWidgets import (QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
                              QFormLayout, QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit,
                              QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
                              QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

from ddm.ui import (DisclosureButton, HintLabel, SelectControl,
                    SegmentedControl, SettingsSurface, StepperControl, TitleLabel, ToggleSwitch)

from .caption_style import CaptionStyle
from .options import Options
from .panel_controls import RoundedAction as ActionButton, StableSelect
from .model_picker import ModelPicker
from .prompt_policy import LOCAL_PROMPT, API_PROMPT
from .source_picker import SourcePicker
from .glossary_editor import GlossaryEditor
from ddm import theme
from .panel_visuals import (CaptionPreview, ControlSymbol, LanguageRoute, PanelSurface, TranslationGlyph,
                            apply_panel_theme, panel_icon)


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


class Panel(QWidget):
    startRequested = Signal()
    stopRequested = Signal()
    captionStyleChanged = Signal(object)
    apiKeyChanged = Signal(str)
    hideRequested = Signal()
    setupRequested = Signal()
    settingsChanged = Signal()

    def __init__(self, settings, parent=None, caption=None, api_key='', caption_preview_factory=None,
                 room_presentation=None, glossary_rooms=None):
        super().__init__(parent)
        self.setWindowTitle('实时翻译设置')
        self.setObjectName('TranslatePanel')
        self.resize(440, 560)
        self.setMinimumSize(0, 0)
        self._caption = CaptionStyle.from_mapping(caption)
        self.room_presentation = room_presentation
        self._room_previews = weakref.WeakKeyDictionary()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 12)
        layout.setSizeConstraint(QLayout.SetNoConstraint)
        layout.setSpacing(10)
        header = QHBoxLayout()
        header.setSpacing(12)
        header.addWidget(TranslationGlyph(self))
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(TitleLabel('实时翻译'))
        header.addLayout(titles)
        header.addStretch()
        self.badge = QLabel('待开始')
        self.badge.setObjectName('TranslateBadge')
        header.addWidget(self.badge)
        self.hide_button = ActionButton('×', self)
        self.hide_button.setFixedSize(32, 32)
        self.hide_button.setAccessibleName('收起实时翻译')
        self.hide_button.setAutoDefault(False)
        self.hide_button.setToolTip('收起设置，传译继续运行')
        self.hide_button.clicked.connect(self.reject)
        header.addWidget(self.hide_button)
        layout.addLayout(header)
        self.navigation = SegmentedControl(
            [('overview', '传译'), ('captions', '字幕'), ('models', '模型')], 'overview', self)
        self.navigation.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(self.navigation)
        self.pages = QStackedWidget(self)
        self.page_scrolls, self.page_boxes, self.page_roots = {}, {}, {}
        for key in ('overview', 'captions', 'models'):
            scroll = QScrollArea(self)
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
            content = QWidget()
            content.setObjectName('TranslatePage')
            scroll.setWidget(content)
            sections = QVBoxLayout(content)
            sections.setContentsMargins(0, 0, 6, 0)
            sections.setSpacing(12)
            page = QWidget(self)
            page.setMinimumSize(0, 0)
            page_box = QVBoxLayout(page)
            page_box.setContentsMargins(0, 0, 0, 0)
            page_box.setSpacing(8)
            scroll.setMinimumSize(0, 0)
            scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
            page_box.addWidget(scroll, 1)
            self.pages.addWidget(page)
            self.page_roots[key] = page
            self.page_scrolls[key], self.page_boxes[key] = scroll, sections
        self.scroll = self.page_scrolls['overview']
        self.navigation.valueChanged.connect(self._page_changed)
        layout.addWidget(self.pages, 1)

        _card, form = self._section('传译直播间')
        overview_form = form
        self.sources = SourcePicker(self)
        form.addRow(self.sources)
        self.source_language = StableSelect(self)
        self.source_language.addItems(['日语', '英语', '韩语', '中文'])
        self.source_language.setCurrentText(settings.get('source_language', '日语'))
        self.source_language.setAccessibleName('原声语言')
        self.target_language = QLineEdit(settings.get('target_language', '简体中文'), self)
        self.target_language.setAccessibleName('字幕语言')
        self.language_route = LanguageRoute(self.source_language, self.target_language)
        form.addRow(self.language_route)
        self.translation_mode = StableSelect(self)
        for title, value in [('本地翻译', 'local'), ('API 翻译', 'api'), ('仅显示原文', 'original')]:
            self.translation_mode.addItem(title, value)
        mode = settings.get('translation_mode', 'api' if settings.get('endpoint') else 'local')
        self.translation_mode.setCurrentIndex(max(0, self.translation_mode.findData(mode)))
        self.translation_mode.setAccessibleName('翻译方式')
        self.translation_mode.setMinimumWidth(0)
        self.translation_mode.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        mode_row = QHBoxLayout()
        mode_row.addWidget(self.translation_mode, 1)
        mode_row.addWidget(ControlSymbol('music', '音乐检测随传译自动启用'))
        form.addRow(mode_row)

        _card, setup_form = self._section('模型准备')
        self.setup_button = ActionButton('准备默认模型…', self)
        self.setup_button.setToolTip('选择保存位置，确认后下载识别、音乐检测与本地翻译模型；也可使用已有模型')
        self.setup_button.clicked.connect(self.setupRequested)
        setup_form.addRow(self.setup_button)
        _card, form = self._section('本地识别')
        self.engine = StableSelect(self)
        self.engine.addItem('Qwen3-ASR 0.6B INT8', 'qwen3_asr')
        self._row(form, '识别模型', self.engine)
        self.recognition_details, details = self._details(form, '模型与运行环境')
        self.python = QLineEdit(settings.get('python', ''), self)
        self.python.setPlaceholderText('选择准备好的识别运行环境')
        self.model_dir = QLineEdit(settings.get('model_dir', ''), self)
        self._path_row(details, '运行库目录', self.python, directory=True)
        self._path_row(details, '识别目录', self.model_dir, directory=True)
        self.music_model_dir = QLineEdit(settings.get('music_model_dir', ''), self)
        self._path_row(details, '音乐目录', self.music_model_dir, directory=True)
        self.threads = QSpinBox(self)
        self.threads.setRange(1, 8)
        self.threads.setValue(int(settings.get('threads', 2)))
        self._row(details, '模型线程', self.threads)
        self.recognition_details.setToolTip('已有兼容模型可直接选择目录；开始传译时才加载。')
        self.model_dir.textChanged.connect(self._recognition_model_changed)
        self._recognition_model_changed()

        self.local_group, local_form = self._section('本地翻译模型')
        self.local_model = QLineEdit(settings.get('local_model', ''), self)
        self.translation_model_choice = ModelPicker(self.local_model, '翻译模型', parent=self)
        self._row(local_form, '翻译模型', self.translation_model_choice)
        self.translation_details, local_paths = self._details(local_form, '模型文件与运行程序')
        self.translation_details.setToolTip('离线运行，无需 API Key。开始时加载，停止时释放。')
        self._path_row(local_paths, '模型文件', self.local_model, file_filter='GGUF (*.gguf)')
        self.local_server = QLineEdit(settings.get('local_server', ''), self)
        self._path_row(local_paths, '运行程序', self.local_server, file_filter='程序 (*.exe);;All files (*)')
        self.api_group, api_form = self._section('API 翻译服务')
        self.endpoint = QLineEdit(settings.get('endpoint', ''), self)
        self.endpoint.setPlaceholderText('例如 http://127.0.0.1:11434/v1')
        self._row(api_form, '接口地址', self.endpoint)
        self.model = QLineEdit(settings.get('model', ''), self)
        self.model.setPlaceholderText('输入服务提供的模型名称')
        self._row(api_form, '翻译模型', self.model)
        self.key = QLineEdit(api_key, self)
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText('加密保存，清空可删除')
        self._row(api_form, 'API Key', self.key)
        self.home_recognition = ModelPicker(self.model_dir, '识别模型', directory=True, parent=self)
        self.home_translation = ModelPicker(self.local_model, '翻译模型', parent=self)
        self.home_api_model = QLineEdit(self.model.text(), self)
        self.home_api_model.setPlaceholderText('输入服务提供的模型名称')
        self.home_api_model.textChanged.connect(self.model.setText)
        self.model.textChanged.connect(self.home_api_model.setText)
        self._row(overview_form, '识别', self.home_recognition)
        self._row(overview_form, '翻译', self.home_translation)
        self._row(overview_form, 'API 模型', self.home_api_model)
        self.home_api_label = overview_form.labelForField(self.home_api_model)
        self.home_translation_label = overview_form.labelForField(self.home_translation)
        self.glossary_details, glossary_form = self._details(overview_form, '直播间术语')
        self.glossary = GlossaryEditor(glossary_rooms, self)
        self.glossary.set_language(self.target_language.text())
        glossary_form.addRow(self.glossary)
        self.target_language.textChanged.connect(self.glossary.set_language)
        self.key.editingFinished.connect(lambda: self.apiKeyChanged.emit(self.key.text().strip()))
        for editor in (self.endpoint, self.model, self.home_api_model):
            editor.editingFinished.connect(self.settingsChanged)
        _card, prompt_form = self._section('翻译提示词')
        self.prompt_details, prompts = self._details(prompt_form, '自定义提示词')
        self.prompt_resets = []
        for name, title, default, limit in (('local_prompt', '本地翻译', LOCAL_PROMPT, 400),
                                            ('api_prompt', 'API 翻译', API_PROMPT, 2000)):
            editor = QPlainTextEdit(settings.get(name) or default, self)
            editor.setFixedHeight(96)
            editor.setAccessibleName(title + '提示词')
            editor.setToolTip(f'支持 {{source_language}} 和 {{target_language}}；最多 {limit} 字，停止后修改。字幕语言和正文检查仍生效。')
            setattr(self, name, editor)
            prompts.addRow(title, editor)
            reset = ActionButton('恢复默认', self)
            reset.clicked.connect(lambda checked=False, control=editor, value=default: control.setPlainText(value))
            self.prompt_resets.append(reset)
            prompts.addRow(reset)
        _card, performance = self._section('翻译等待')
        self.timeout = QDoubleSpinBox(self)
        self.timeout.setRange(0.2, 3)
        self.timeout.setSingleStep(0.1)
        self.timeout.setSuffix(' 秒')
        self.timeout.setValue(float(settings.get('timeout', 1.5)))
        self._row(performance, '响应等待上限', self.timeout)
        self.timeout.parentWidget().setToolTip('超过等待上限时保留原文，避免翻译请求堆积。')
        self.translation_mode.currentIndexChanged.connect(self._mode_changed)
        self._mode_changed()

        self.style_preview = (caption_preview_factory(self) if callable(caption_preview_factory)
                              else CaptionPreview(self))
        self.style_preview.setFixedHeight(170)
        self.page_roots['captions'].layout().insertWidget(0, self.style_preview)
        _card, caption_form = self._section('字幕显示')
        self.caption_enabled = ToggleSwitch('显示内嵌字幕', self)
        self.caption_enabled.setChecked(self._caption.enabled)
        self._row(caption_form, '显示内嵌字幕', self.caption_enabled)
        self.caption_mode = StableSelect(self)
        for title, value in [('原文 + 译文', 'both'), ('仅原文', 'original'), ('仅译文', 'translation')]:
            self.caption_mode.addItem(title, value)
        self.caption_mode.setCurrentIndex(max(0, self.caption_mode.findData(self._caption.mode)))
        self._row(caption_form, '显示组合', self.caption_mode)
        self.caption_details, style_form = self._details(caption_form, '字幕样式')
        for name, title, low, high, suffix, value in [
                ('caption_lines', '总行数', 1, 4, '', self._caption.lines),
                ('caption_font_size', '字体大小', 10, 64, ' px', self._caption.font_size),
                ('caption_opacity', '背景不透明度', 0, 100, ' %', self._caption.opacity),
                ('caption_width', '字幕框宽度', 20, 100, ' %', self._caption.width),
                ('caption_height', '字幕框高度', 8, 45, ' %', self._caption.height),
                ('caption_x', '横向位置', 0, 100, ' %', self._caption.x),
                ('caption_y', '纵向位置', 0, 100, ' %', self._caption.y)]:
            spin = QSpinBox(self)
            spin.setRange(low, high)
            spin.setSuffix(suffix)
            spin.setValue(value)
            setattr(self, name, spin)
            self._row(style_form, title, spin)
        self.caption_locked = ToggleSwitch('锁定字幕位置', self)
        self.caption_locked.setChecked(self._caption.locked)
        self._row(caption_form, '锁定位置', self.caption_locked)
        self.caption_details.setChecked(True)
        self.caption_enabled.setToolTip('隐藏字幕不会停止识别；字幕样式修改后立即生效。')
        for widget in (self.caption_enabled, self.caption_mode, self.caption_lines,
                       self.caption_opacity, self.caption_font_size, self.caption_width, self.caption_height,
                       self.caption_x, self.caption_y, self.caption_locked):
            if isinstance(widget, QSpinBox):
                widget.valueChanged.connect(self._caption_changed)
            elif isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self._caption_changed)
            else:
                widget.toggled.connect(self._caption_changed)
        if hasattr(self.style_preview, 'styleChanged'):
            self.style_preview.styleChanged.connect(self._preview_style_changed)
            self.style_preview.set_style(self._caption.mapping())
        self.preview = CaptionPreview(self)
        self.page_boxes['overview'].addWidget(self.preview)
        _card, preview_form = self._section('资源与诊断')
        self.diagnostic_details, diagnostics = self._details(preview_form, '资源与诊断')
        self.resources = HintLabel('暂无资源记录')
        diagnostics.addRow(self.resources)
        self.processing = HintLabel('暂无处理记录')
        self.processing.setToolTip('仅统计完成分段后的处理时间，不代表端到端字幕延迟。')
        diagnostics.addRow(self.processing)
        self.export = ActionButton('导出本次资源记录', self)
        self.export.setAutoDefault(False)
        diagnostics.addRow(self.export)
        for sections in self.page_boxes.values():
            sections.addStretch()

        self.footer = QWidget(self)
        footer = QVBoxLayout(self.footer)
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(10)
        self.status = HintLabel('选择直播间后开始')
        self.status.setTextFormat(Qt.PlainText)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        footer.addWidget(self.status)
        buttons = QHBoxLayout()
        self.start = ActionButton('开始', self)
        self.start.setObjectName('PrimaryButton')
        self.start.setAccessibleName('开始传译')
        self.start.setToolTip('开始传译')
        self.stop = ActionButton('', self)
        self.stop.setFixedSize(36, 36)
        self.stop.setAccessibleName('停止传译')
        self.stop.setToolTip('停止传译')
        for button in (self.start, self.stop):
            button.setAutoDefault(False)
        self.start.setMinimumWidth(112)
        self.start.clicked.connect(self.startRequested)
        self.stop.clicked.connect(self.stopRequested)
        buttons.addStretch()
        buttons.addWidget(self.stop)
        buttons.addWidget(self.start)
        footer.addLayout(buttons)
        layout.addWidget(self.footer)
        self.set_running(False)
        theme.signals().changed.connect(self._refresh_theme)
        self._refresh_theme()

    def _refresh_theme(self):
        apply_panel_theme(self)
        self.stop.setIcon(panel_icon('stop'))
        for button in self.findChildren(ActionButton):
            if button.property('pathPicker'):
                button.setIcon(panel_icon('folder'))

    def _page_changed(self, key):
        self.pages.setCurrentWidget(self.page_roots[key])
        self.scroll = self.page_scrolls[key]

    def resizeEvent(self, event):
        if hasattr(self, 'style_preview'):
            self.style_preview.setFixedHeight(max(90, min(170, self.height() // 3)))
        super().resizeEvent(event)

    def reject(self):
        self.apiKeyChanged.emit(self.key.text().strip())
        self.hideRequested.emit()
        if self.isWindow():
            self.hide()

    def _section(self, title):
        card = PanelSurface()
        card.setObjectName('SettingsSection')
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        box = QVBoxLayout(card)
        box.setContentsMargins(12, 12, 12, 12)
        box.setSpacing(12)
        box.setAlignment(Qt.AlignTop)
        if title != '传译直播间':
            heading = QLabel(title)
            heading.setObjectName('SettingsSectionTitle')
            box.addWidget(heading)
        form = self._form()
        box.addLayout(form)
        key = ('overview' if title == '传译直播间' else
               'captions' if title == '字幕显示' else 'models')
        self.page_boxes[key].addWidget(card)
        return card, form

    @staticmethod
    def _form():
        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(12)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setFormAlignment(Qt.AlignTop)
        return form

    def _row(self, form, title, editor):
        label = QLabel(title)
        label.setBuddy(editor)
        editor.setAccessibleName(title)
        if isinstance(editor, (QSpinBox, QDoubleSpinBox)):
            control = StepperControl(editor)
        else:
            control = editor
            if isinstance(editor, (QComboBox, QLineEdit)):
                editor.setMinimumWidth(0)
                editor.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        form.addRow(label, control)

    def _details(self, form, title):
        button = DisclosureButton()
        button.setText(title)
        button.setAccessibleName(title)
        form.addRow(button)
        body = QWidget()
        details = self._form()
        body.setLayout(details)
        details.setContentsMargins(0, 4, 0, 0)
        body.hide()
        def toggle(visible):
            # Resize the scroll content before repainting: otherwise the form
            # briefly packs its new rows into the previous card height.
            scroll = body.parentWidget()
            while scroll is not None and not isinstance(scroll, QScrollArea):
                scroll = scroll.parentWidget()
            self.setUpdatesEnabled(False)
            try:
                body.setVisible(visible)
                if scroll is not None:
                    content = scroll.widget()
                    content.layout().activate()
                    content.resize(scroll.viewport().width(), max(scroll.viewport().height(), content.minimumSizeHint().height()))
                    content.layout().activate()
            finally:
                self.setUpdatesEnabled(True)
        button.toggled.connect(toggle)
        form.addRow(body)
        return button, details

    def _path_row(self, form, label, editor, directory=False, file_filter='Python (python.exe);;All files (*)'):
        row = QHBoxLayout()
        row.addWidget(editor, 1)
        editor.setMinimumWidth(0)
        editor.setAccessibleName(label)
        editor.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        choose = ActionButton('', self)
        choose.setFixedSize(32, 32)
        choose.setProperty('pathPicker', True)
        choose.setToolTip('选择' + label)
        editor.setToolTip(editor.text())
        editor.textChanged.connect(editor.setToolTip)
        choose.setAutoDefault(False)
        choose.setAccessibleName('选择' + label)
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
        for widget in (self.home_translation, self.home_translation_label):
            widget.setVisible(mode == 'local')
        for widget in (self.home_api_model, self.home_api_label):
            widget.setVisible(mode == 'api')

    def _recognition_model_changed(self, _text=None):
        try:
            Options(model_dir=self.model_dir.text().strip()).files()
            state = '模型已就绪'
        except ValueError:
            state = '模型未就绪'
        self.engine.setItemText(0, f'Qwen3-ASR 0.6B INT8（{state}）')

    def refresh_sources(self, sources, selected=None):
        self.sources.refresh(sources, selected, self.room_presentation)
        self.glossary.refresh(sources)

    def options(self):
        return Options(engine=self.engine.currentData(), model_dir=self.model_dir.text().strip(),
                       python=self.python.text().strip(), endpoint=self.endpoint.text().strip(),
                       model=self.model.text().strip(), source_language=self.source_language.currentText(),
                       translation_mode=self.translation_mode.currentData(),
                       local_model=self.local_model.text().strip(), local_server=self.local_server.text().strip(),
                       target_language=self.target_language.text().strip() or '简体中文',
                       threads=self.threads.value(), timeout=self.timeout.value(),
                       music_detection=True,
                       music_model_dir=self.music_model_dir.text().strip(),
                       local_prompt='' if self.local_prompt.toPlainText().strip() == LOCAL_PROMPT else self.local_prompt.toPlainText().strip(),
                       api_prompt='' if self.api_prompt.toPlainText().strip() == API_PROMPT else self.api_prompt.toPlainText().strip())

    def caption_options(self):
        return CaptionStyle(
            enabled=self.caption_enabled.isChecked(),
            mode=self.caption_mode.currentData(),
            lines=self.caption_lines.value(),
            opacity=self.caption_opacity.value(),
            width=self.caption_width.value(),
            height=self.caption_height.value(),
            font_size=self.caption_font_size.value(),
            x=self.caption_x.value(),
            y=self.caption_y.value(),
            locked=self.caption_locked.isChecked(),
        )

    def _caption_changed(self, _value=None):
        self._caption = self.caption_options()
        if hasattr(self.style_preview, 'set_style'):
            self.style_preview.set_style(self._caption.mapping())
        self.captionStyleChanged.emit(self._caption)

    def _preview_style_changed(self, value):
        style = CaptionStyle.from_mapping(dict(self.caption_options().mapping(), **value))
        controls = (self.caption_width, self.caption_height, self.caption_x, self.caption_y,
                    self.caption_font_size, self.caption_opacity, self.caption_lines, self.caption_locked,
                    self.caption_mode)
        blockers = [QSignalBlocker(control) for control in controls]
        for control, setting in ((self.caption_width, style.width), (self.caption_height, style.height),
                                 (self.caption_x, style.x), (self.caption_y, style.y),
                                 (self.caption_font_size, style.font_size), (self.caption_opacity, style.opacity),
                                 (self.caption_lines, style.lines)):
            control.setValue(setting)
        self.caption_locked.setChecked(style.locked)
        self.caption_mode.setCurrentIndex(self.caption_mode.findData(style.mode))
        del blockers
        self._caption_changed()

    def set_running(self, running):
        self.badge.setText('传译中' if running else '待开始')
        self.start.setEnabled(not running)
        self.stop.setEnabled(running)
        self.sources.setEnabled(not running)
        self.translation_mode.setEnabled(not running)
        self.source_language.setEnabled(not running)
        self.target_language.setEnabled(not running)
        self.glossary.setEnabled(not running)
        self.music_model_dir.setEnabled(not running)
        for control in (self.home_recognition, self.home_translation, self.home_api_model,
                        self.engine, self.model_dir, self.python, self.local_model, self.local_server, self.model,
                        self.translation_model_choice, self.local_prompt, self.api_prompt, self.setup_button, *self.prompt_resets):
            control.setEnabled(not running)

    def apply_prepared_models(self, values):
        for name in ('python', 'model_dir', 'music_model_dir', 'local_model', 'local_server'):
            getattr(self, name).setText(values[name])
        self.settingsChanged.emit()

    def closeEvent(self, event):
        self.apiKeyChanged.emit(self.key.text().strip())
        self.hideRequested.emit()
        super().closeEvent(event)

    def update_caption(self, original, translated):
        original, translated = self.caption_options().text(original, translated)
        self.preview.set_caption(original, translated)

    def update_room_caption(self, tile, name, original, translated):
        preview = self._room_previews.get(tile)
        if preview is None:
            if self.preview not in self._room_previews.values():
                preview = self.preview
            else:
                preview = CaptionPreview(self)
                sections = self.page_boxes['overview']
                sections.insertWidget(sections.count() - 1, preview)
            self._room_previews[tile] = preview
        preview.heading.setText(name)
        preview.set_caption(original, translated)
        preview.show()

    def remove_room_caption(self, tile):
        preview = self._room_previews.pop(tile, None)
        if preview is self.preview:
            preview.hide()
        elif preview is not None:
            preview.deleteLater()
        if not self._room_previews:
            self.preview.heading.setText('字幕')
            self.preview.set_caption('', '')
            self.preview.show()

    def update_metrics(self, value):
        if value['event'] == 'resources':
            if value.get('active_sources'):
                self.badge.setText(f'传译中 · {value["active_sources"]}')
            memory = value['child_rss_bytes']
            label = f'{memory / 1024**2:.1f} MiB' if memory is not None else '不可用'
            self.resources.setText(
                f'FFmpeg/本地翻译子进程 RSS：{label}；识别在宿主线程内运行；'
                f'待发音频 {value["pending_pcm_bytes"]} B；过载丢弃 {value["dropped_audio_seconds"]:.2f} 秒。')
        elif value['event'] == 'caption':
            self.processing.setText(f'识别 {value["asr_ms"]:.0f} ms · 翻译 {value["translation_ms"]:.0f} ms')

    def export_metrics(self, values):
        filename, _ = QFileDialog.getSaveFileName(self, '导出资源记录', 'live-translate-metrics.jsonl', 'JSONL (*.jsonl)')
        if filename:
            try:
                with open(filename, 'w', encoding='utf-8') as output:
                    for value in values:
                        output.write(json.dumps(value, ensure_ascii=False) + '\n')
            except OSError:
                self.status.setText('无法写入资源记录，请选择可写目录')
