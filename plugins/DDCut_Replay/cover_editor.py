"""Embedded dual-ratio cover editor. Drafts own state; tasks receive snapshots."""
from copy import deepcopy
from pathlib import Path
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFontComboBox,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
    QPushButton, QScrollArea, QSizePolicy, QSlider, QSplitter, QVBoxLayout, QWidget)
from .cover_canvas import CoverCanvas
from .cover_media import capture_frame
from .cover_model import CoverDocument, SIZES, change_element, identity, new_cover, new_element, write_cover
from .cover_render import export_covers, render_cover
from .project import duration


class CoverEditor(QWidget):
    backRequested = Signal()

    def __init__(self, workspace):
        super().__init__()
        self.workspace, self.hub = workspace, workspace.hub
        self.document, self.project_document, self.source = None, None, None
        self.source_key, self.selection, self.ratio = '', '', 'wide'
        self.frame_job = self.export_job = None
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.button('返回', self.leave, row)
        title = QLabel('封面制作')
        title.setObjectName('SettingsTitle')
        row.addWidget(title, 1)
        self.button('新建', lambda: self.open_source(self.source, self.project_document, force_new=True), row)
        self.button('打开草稿', self.open_draft, row)
        self.button('另存草稿', self.save_as, row)
        self.format = QComboBox()
        self.format.addItems(['PNG', 'JPEG'])
        row.addWidget(self.format)
        self.export_button = self.button('导出双封面', self.export, row)
        self.export_button.setObjectName('PrimaryButton')
        box.addLayout(row)
        row = QHBoxLayout()
        self.button('导入背景', self.choose_background, row)
        row.addWidget(QLabel('取帧时间 秒'))
        self.time = QDoubleSpinBox()
        self.time.setDecimals(3)
        self.time.setKeyboardTracking(False)
        row.addWidget(self.time)
        self.capture_button = self.button('从视频取帧', self.capture, row)
        self.scrubber = QSlider(Qt.Horizontal)
        self.scrubber.setRange(0, 10000)
        self.scrubber.valueChanged.connect(lambda value: self.time.setValue(self.time.maximum()*value/10000))
        self.scrubber.sliderReleased.connect(self.capture)
        self.time.valueChanged.connect(self.sync_time)
        row.addWidget(self.scrubber, 1)
        box.addLayout(row)
        row = QHBoxLayout()
        self.ratios = QComboBox()
        self.ratios.addItem('16:9 · 1920×1080', 'wide')
        self.ratios.addItem('4:3 · 1440×1080', 'home')
        self.ratios.currentIndexChanged.connect(self.switch_ratio)
        row.addWidget(self.ratios)
        self.sync = QCheckBox('同步文字和贴纸')
        self.sync.toggled.connect(lambda value: self.change(lambda data: data.update(sync=value)))
        row.addWidget(self.sync)
        self.undo_button = self.button('撤销', lambda: self.history(False), row)
        self.redo_button = self.button('重做', lambda: self.history(True), row)
        row.addStretch()
        row.addWidget(QLabel('背景缩放 %'))
        self.zoom = QDoubleSpinBox()
        self.zoom.setRange(100, 400)
        self.zoom.setKeyboardTracking(False)
        self.zoom.editingFinished.connect(lambda: self.change(lambda data: data['views'][self.ratio]['frame'].update(zoom=self.zoom.value()/100)))
        row.addWidget(self.zoom)
        self.button('重置裁切', lambda: self.change(lambda data: data['views'][self.ratio].update(frame=dict(x=.5, y=.5, zoom=1))), row)
        box.addLayout(row)
        splitter = QSplitter(Qt.Horizontal)
        box.addWidget(splitter, 1)
        left = QWidget()
        left.setMinimumWidth(130)
        left.setMaximumWidth(210)
        tools = QVBoxLayout(left)
        tools.setContentsMargins(0, 0, 4, 0)
        self.button('添加文字', self.add_text, tools)
        self.button('导入贴纸', self.choose_sticker, tools)
        tools.addWidget(QLabel('文字样式'))
        self.presets = QComboBox()
        from .graphic_style import TEXT_PRESETS
        for title, values in TEXT_PRESETS:
            self.presets.addItem(title, values)
        tools.addWidget(self.presets)
        self.button('应用所选样式', self.apply_preset, tools)
        tools.addWidget(QLabel('图层 · 上方显示在前'))
        self.layers = QListWidget()
        self.layers.setAccessibleName('封面图层')
        self.layers.itemClicked.connect(lambda item: self.select(item.data(Qt.UserRole)))
        tools.addWidget(self.layers, 1)
        row = QHBoxLayout()
        self.button('上移', lambda: self.reorder(1), row)
        self.button('下移', lambda: self.reorder(-1), row)
        tools.addLayout(row)
        row = QHBoxLayout()
        self.button('复制', self.duplicate, row)
        self.button('删除', self.remove, row)
        tools.addLayout(row)
        self.other = QLabel()
        self.other.setAlignment(Qt.AlignCenter)
        tools.addWidget(self.other)
        splitter.addWidget(left)
        self.canvas = CoverCanvas()
        self.canvas.selected.connect(self.select)
        self.canvas.editRequested.connect(self.apply_gesture)
        self.canvas.deleteRequested.connect(self.remove)
        splitter.addWidget(self.canvas)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(200)
        scroll.setMaximumWidth(260)
        properties = QWidget()
        form = QFormLayout(properties)
        self.property_form = form
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.text = QPlainTextEdit()
        self.text.setMaximumHeight(90)
        form.addRow('多行文字', self.text)
        self.font = QFontComboBox()
        form.addRow('字体', self.font)
        self.fields = {}
        for key, title, low, high in [('x', 'X %', -100, 200), ('y', 'Y %', -100, 200),
            ('w', '宽度 %', 2, 200), ('h', '高度 %', 2, 200), ('rotation', '旋转 °', -180, 180),
            ('opacity', '不透明度 %', 0, 100), ('font_size', '字号', 8, 240), ('outline', '描边', 0, 30)]:
            field = QDoubleSpinBox()
            field.setRange(low, high)
            field.setDecimals(1)
            field.setKeyboardTracking(False)
            field.setMinimumWidth(0)
            self.fields[key] = field
            form.addRow(title, field)
        for key, title in [('color', '文字颜色'), ('stroke', '描边颜色'), ('background', '文字底色')]:
            field = QLineEdit()
            self.fields[key] = field
            form.addRow(title, field)
        for key, title in [('bold', '加粗'), ('italic', '斜体'), ('shadow', '投影'), ('flip', '水平翻转贴纸')]:
            field = QCheckBox(title)
            self.fields[key] = field
            form.addRow(field)
        self.align = QComboBox()
        for title, value in [('居中', 'center'), ('左对齐', 'left'), ('右对齐', 'right')]:
            self.align.addItem(title, value)
        form.addRow('文字对齐', self.align)
        apply = self.button('应用属性', self.apply_fields, form)
        apply.setObjectName('PrimaryButton')
        self.button('元素居中', lambda: self.edit_element(dict(x=(1-self.current()['w'])/2, y=(1-self.current()['h'])/2)) if self.current() else None, form)
        for field in [self.text, self.font, self.align, *self.fields.values()]:
            field.setMinimumWidth(0)
            field.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred if field is self.text else QSizePolicy.Fixed)
        scroll.setWidget(properties)
        splitter.addWidget(scroll)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([170, 620, 230])
        self.status = QLabel('拖动背景调整裁切；选中元素后拖动、等比缩放或旋转。草稿自动保存。')
        self.status.setWordWrap(True)
        self.status.setObjectName('SettingsHint')
        box.addWidget(self.status)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.tick)
        for key, callback in [('Ctrl+Z', lambda: self.history(False)), ('Ctrl+Shift+Z', lambda: self.history(True))]:
            shortcut = QShortcut(QKeySequence(key), self.canvas)
            shortcut.activated.connect(callback)

    def button(self, title, callback, layout):
        button = QPushButton(title)
        button.setObjectName('IconButton')
        button.clicked.connect(callback)
        layout.addRow(button) if isinstance(layout, QFormLayout) else layout.addWidget(button)
        return button

    def guard(self, function):
        try:
            return function()
        except Exception as error:
            self.status.setText(str(error))

    def open_source(self, source=None, project_document=None, *, force_new=False):
        self.shutdown()
        self.source, self.project_document = deepcopy(source), project_document
        self.source_key = str(project_document.path) if project_document else (source or {}).get('path', 'blank')
        paths = self.hub.preferences.setdefault('cover_drafts', {})
        path = project_document.data.get('cover_draft') if project_document else paths.get(self.source_key)
        if path and not force_new:
            self.document = CoverDocument(path)
        else:
            self.document = CoverDocument(self.hub.root/'covers'/(identity()+'.ddcover.json'), new_cover())
            write_cover(self.document.path, self.document.data)
            self.remember()
        self.selection = ''
        total = duration(self.source['project']) if self.source and 'project' in self.source else (self.source or {}).get('duration', 0)
        self.time.setRange(0, max(0, total-.001))
        self.time.setValue(0)
        self.capture_button.setEnabled(bool(total))
        self.scrubber.setEnabled(bool(total))
        self.refresh()
        self.timer.start()

    def remember(self):
        if self.project_document:
            self.project_document.commit(lambda data: data.update(cover_draft=str(self.document.path)))
        else:
            self.hub.preferences.setdefault('cover_drafts', {})[self.source_key] = str(self.document.path)
            self.hub.save()

    def change(self, operation):
        if self.document:
            def apply():
                result = self.document.commit(operation)
                self.refresh()
                return result
            return self.guard(apply)

    def refresh(self):
        data = self.document.data
        self.canvas.data, self.canvas.ratio, self.canvas.selection = data, self.ratio, self.selection
        self.canvas.drag = None
        self.canvas.update()
        self.sync.blockSignals(True)
        self.sync.setChecked(data['sync'])
        self.sync.blockSignals(False)
        self.zoom.setValue(data['views'][self.ratio]['frame']['zoom']*100)
        self.undo_button.setEnabled(bool(self.document.undo_stack))
        self.redo_button.setEnabled(bool(self.document.redo_stack))
        self.layers.clear()
        for row in reversed(data['views'][self.ratio]['elements']):
            item = QListWidgetItem(row['text'].splitlines()[0] if row['kind'] == 'text' and row['text'] else '贴纸' if row['kind'] == 'image' else '空文字')
            item.setData(Qt.UserRole, row['id'])
            self.layers.addItem(item)
            if row['id'] == self.selection:
                self.layers.setCurrentItem(item)
        other = 'home' if self.ratio == 'wide' else 'wide'
        try:
            self.other.setPixmap(QPixmap.fromImage(render_cover(data, other, cache=self.canvas.cache)).scaled(160, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        except (OSError, ValueError) as error:
            self.other.setText(str(error))
        self.select(self.selection)

    def current(self):
        return next((row for row in self.document.data['views'][self.ratio]['elements'] if row['id'] == self.selection), None) if self.document else None

    def select(self, identity):
        self.selection = identity
        self.canvas.selection = identity
        self.canvas.update()
        item = self.current()
        text_selected = item is not None and item['kind'] == 'text'
        for field in (self.text, self.font, self.align):
            field.setEnabled(text_selected)
            self.property_form.setRowVisible(field, text_selected)
        for key, field in self.fields.items():
            if key in ('font_size', 'outline', 'color', 'stroke', 'background', 'bold', 'italic', 'shadow', 'flip'):
                self.property_form.setRowVisible(field, item is not None and (item['kind'] == 'image' if key == 'flip' else text_selected))
            field.setEnabled(item is not None and (key not in ('font_size', 'outline', 'color', 'stroke', 'background', 'bold', 'italic', 'shadow', 'flip')
                                                   or (key == 'flip' and item['kind'] == 'image') or (key != 'flip' and text_selected)))
            if item:
                value = item[key]
                if isinstance(field, QLineEdit):
                    field.setText(value)
                elif isinstance(field, QCheckBox):
                    field.setChecked(value)
                else:
                    field.setValue(value*(100 if key in ('x', 'y', 'w', 'h', 'opacity') else 1))
        self.text.setPlainText(item['text'] if item else '')
        if item:
            from ddm import theme
            self.font.setCurrentFont(QFont(item['font'] or theme.danmaku_font_family()))
            self.align.setCurrentIndex(self.align.findData(item['align']))

    def switch_ratio(self):
        self.ratio = self.ratios.currentData()
        if self.document:
            self.refresh()

    def edit_element(self, values):
        if self.current():
            self.change(lambda data: change_element(data, self.ratio, self.selection, values))

    def apply_gesture(self, kind, values):
        if kind == 'frame':
            self.change(lambda data: data['views'][self.ratio].update(frame=values))
        else:
            item = self.current()
            if item:
                self.edit_element({key: value for key, value in values.items() if item.get(key) != value})

    def apply_fields(self):
        if not self.current():
            return
        values = dict(text=self.text.toPlainText(), font=self.font.currentFont().family(), align=self.align.currentData())
        for key, field in self.fields.items():
            values[key] = field.text() if isinstance(field, QLineEdit) else field.isChecked() if isinstance(field, QCheckBox) else field.value()/(100 if key in ('x', 'y', 'w', 'h', 'opacity') else 1)
        self.edit_element(values)

    def add(self, item):
        def operation(data):
            for ratio in (SIZES if data['sync'] else (self.ratio,)):
                data['views'][ratio]['elements'].append(deepcopy(item))
        self.selection = item['id']
        self.change(operation)

    def add_text(self):
        self.add(new_element('text'))

    def apply_preset(self):
        if self.current() is None or self.current()['kind'] != 'text':
            self.add_text()
        color, stroke, background, shadow = self.presets.currentData()
        self.edit_element(dict(color=color, stroke=stroke, background=background, shadow=shadow))

    def choose_background(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择背景图片', '', '图片 (*.png *.jpg *.jpeg *.webp *.bmp)')
        if path:
            self.set_background(path)

    def set_background(self, path):
        from PySide6.QtGui import QImage
        image = QImage(str(path))
        if image.isNull():
            self.status.setText('无法读取背景图片')
            return
        if self.frame_job:
            self.hub.cancel(self.frame_job[0])
            self.frame_job = None
        self.canvas.cache.clear()
        self.canvas.cache[str(Path(path).resolve())] = image
        self.change(lambda data: data.update(background=str(Path(path).resolve()), views={key: {**view, 'frame': dict(x=.5, y=.5, zoom=1)} for key, view in data['views'].items()}))
        self.status.setText('背景已更新，草稿已保存。')

    def choose_sticker(self):
        path, _ = QFileDialog.getOpenFileName(self, '导入外部贴纸', '', '图片 (*.png *.jpg *.jpeg *.webp *.bmp)')
        if path:
            self.add_sticker(path)

    def add_sticker(self, path):
        from PySide6.QtGui import QImage
        picture = QImage(str(path))
        if picture.isNull():
            self.status.setText('无法读取贴纸图片')
            return
        item = new_element('image', str(Path(path).resolve()))
        width, height = picture.width(), picture.height()
        scale = min(480/width, 270/height)
        item.update(w=max(.02, width*scale/1920), h=max(.02, height*scale/1080))
        self.add(item)

    def remove(self):
        if self.current():
            self.change(lambda data: change_element(data, self.ratio, self.selection, remove=True))

    def duplicate(self):
        if self.current():
            item = dict(self.current(), id=identity())
            item['x'], item['y'] = min(2, item['x']+.02), min(2, item['y']+.02)
            self.add(item)

    def reorder(self, direction):
        def operation(data):
            for ratio in (SIZES if data['sync'] else (self.ratio,)):
                rows = data['views'][ratio]['elements']
                index = next((i for i, row in enumerate(rows) if row['id'] == self.selection), -1)
                if index >= 0 and 0 <= index+direction < len(rows):
                    rows.insert(index+direction, rows.pop(index))
        self.change(operation)

    def history(self, redo):
        if self.document:
            self.guard(lambda: self.document.history(redo))
            self.refresh()

    def sync_time(self, value):
        self.scrubber.blockSignals(True)
        self.scrubber.setValue(round(value/max(.001, self.time.maximum())*10000))
        self.scrubber.blockSignals(False)

    def capture(self):
        if not self.source:
            return
        if self.frame_job:
            self.hub.cancel(self.frame_job[0])
        target = self.hub.root/'cover_frames'/(identity()+'.png')
        job = self.hub.submit('封面取帧', 'cover_frame', capture_frame, self.workspace.service.options()['ffmpeg'],
            deepcopy(self.source), self.time.value(), target, self.hub.root/'work')
        self.frame_job = job, self.document
        self.status.setText('正在读取所选视频帧…')

    def tick(self):
        if self.frame_job and self.frame_job[0].future.done():
            job, document = self.frame_job
            self.frame_job = None
            if job.state == 'done' and document is self.document:
                self.set_background(job.result['path'])
                self.status.setText(f'已读取{job.result["time"]:.3f}秒的视频帧；草稿已保存。')
            elif job.state == 'failed':
                self.status.setText(job.message)
        if self.export_job and self.export_job.future.done():
            job, self.export_job = self.export_job, None
            self.status.setText('双比例封面已导出：'+'；'.join(job.result['images'].values()) if job.state == 'done' else job.message)

    def export(self):
        extension = 'png' if self.format.currentIndex() == 0 else 'jpg'
        path, _ = QFileDialog.getSaveFileName(self, '导出双比例封面（自动添加比例后缀）', '封面.'+extension, '图片 (*.'+extension+')')
        if path:
            chosen = Path(path)
            base = chosen.with_suffix('') if chosen.suffix.lower() in ('.png', '.jpg', '.jpeg') else chosen
            targets = {key: base.parent/(base.name+('-16x9' if key == 'wide' else '-4x3')+'.'+extension) for key in SIZES}
            self.export_job = self.hub.submit('导出双比例封面', 'cover_export', export_covers, deepcopy(self.document.data), targets, extension)
            self.status.setText('正在导出双比例封面…')

    def open_draft(self):
        path, _ = QFileDialog.getOpenFileName(self, '打开封面草稿', '', '封面草稿 (*.ddcover.json *.json)')
        if path:
            def load():
                document = CoverDocument(path)
                self.shutdown()
                self.document = document
                self.selection = ''
                self.remember()
                self.canvas.cache.clear()
                self.refresh()
                self.timer.start()
            self.guard(load)

    def save_as(self):
        path, _ = QFileDialog.getSaveFileName(self, '另存封面草稿', '封面.ddcover.json', '封面草稿 (*.ddcover.json)')
        if path:
            def save():
                path_value = path if path.endswith('.ddcover.json') else path+'.ddcover.json'
                write_cover(path_value, self.document.data)
                self.document.path = Path(path_value)
                self.remember()
                self.status.setText('草稿已保存：'+path_value)
            self.guard(save)

    def shutdown(self):
        self.timer.stop()
        self.canvas.drag = None
        if self.frame_job:
            self.hub.cancel(self.frame_job[0])
            self.frame_job = None

    def leave(self):
        self.shutdown()
        self.backRequested.emit()
