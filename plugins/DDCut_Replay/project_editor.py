"""Embedded project editor: external file bin, preview, inspector and timeline."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal, QSize
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QScrollArea,
    QSizePolicy, QSpinBox, QSplitter, QTabWidget, QVBoxLayout, QWidget)
from .project import (add_layer, anchor_at, append_clip, duplicate_item, duration,
                      placed_clips, placed_layers, remove_item, split_clip, write_project)
from .project_media import inspect_file, render_project, waveform
from .project_timeline import ProjectTimeline
from .project_stage import ProjectStage
from .subtitle_panel import SubtitlePanel
from .subtitles import placed_cues, set_cue
from .encoding_widgets import EncodingOptions
from .graphic_style_widgets import TextStyleOptions
from .timeline import timecode
from .workspace import Preview, button


class ProjectEditor(QWidget):
    backRequested = Signal()

    def __init__(self, workspace):
        super().__init__()
        self.workspace, self.hub = workspace, workspace.hub
        self.document = None
        self.selected_id = ''
        self.position, self.playing = 0, False
        self.imports, self.waves = [], []
        self.preview_job = None
        self.pending_preview = None
        self.loaded_key = None
        self.preview_dirty = True
        self.resume = None
        self.preview_begin, self.preview_end = 0, 0
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        button('工程列表', self.leave, row)
        self.title = QLineEdit()
        self.title.setAccessibleName('剪辑工程标题')
        self.title.editingFinished.connect(self.rename)
        row.addWidget(self.title, 1)
        self.save_button = button('保存工程', self.save, row)
        button('另存为', self.save_as, row)
        self.export_button = button('导出视频', self.export, row)
        self.export_button.setObjectName('PrimaryButton')
        box.addLayout(row)
        self.vertical = QSplitter(Qt.Vertical)
        box.addWidget(self.vertical, 1)
        top = QSplitter(Qt.Horizontal)
        self.vertical.addWidget(top)
        left = QWidget()
        left.setMinimumWidth(150)
        left.setMaximumWidth(280)
        files = QVBoxLayout(left)
        files.setContentsMargins(4, 4, 4, 4)
        label = QLabel('工程文件')
        label.setObjectName('SettingsTitle')
        files.addWidget(label)
        self.import_button = button('导入外部文件', self.choose_files, files)
        self.bin = QListWidget()
        self.bin.setObjectName('FollowList')
        self.bin.setIconSize(QSize(72, 42))
        self.bin.setAccessibleName('工程引用的外部文件')
        self.bin.itemDoubleClicked.connect(lambda *_: self.append_selected())
        files.addWidget(self.bin, 1)
        row = QHBoxLayout()
        button('添加主轨', self.append_selected, row)
        button('波形', self.analyze_waveform, row)
        files.addLayout(row)
        button('添加为画中画', lambda: self.add_media_layer('video'), files)
        button('添加为音频', lambda: self.add_media_layer('audio'), files)
        button('添加为贴纸', lambda: self.add_media_layer('image'), files)
        button('添加文字', self.add_text, files)
        button('重新定位文件', self.relocate, files)
        top.addWidget(left)
        center = QWidget()
        center.setMinimumWidth(240)
        stage = QVBoxLayout(center)
        stage.setContentsMargins(0, 0, 0, 0)
        self.preview = Preview(workspace.owner.preview_server.source_url, interactive=True)
        self.stage = ProjectStage(self)
        self.preview.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        stage.addWidget(self.preview, 1)
        self.preview_hint = QLabel('添加主视频后，按当前位置生成工程预览。')
        self.preview_hint.setWordWrap(True)
        self.preview_hint.setObjectName('SettingsHint')
        stage.addWidget(self.preview_hint)
        row = QHBoxLayout()
        self.play_button = button('播放', self.toggle_play, row)
        self.muted = QCheckBox('静音')
        self.muted.setChecked(True)
        self.muted.toggled.connect(self.set_mute)
        row.addWidget(self.muted)
        self.clock = QLabel('00:00 / 00:00')
        row.addWidget(self.clock, 1)
        button('更新预览', lambda: self.request_preview(force=True), row)
        stage.addLayout(row)
        top.addWidget(center)
        right = QWidget()
        right.setMinimumWidth(210)
        right.setMaximumWidth(310)
        inspector = QVBoxLayout(right)
        inspector.setContentsMargins(4, 4, 4, 4)
        self.selected_label = QLabel('选择片段或轨道内容')
        self.selected_label.setWordWrap(True)
        self.selected_label.setObjectName('SettingsTitle')
        inspector.addWidget(self.selected_label)
        scroll = QScrollArea()
        self.inspector_scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inspector.addWidget(scroll, 1)
        properties = QWidget()
        props = QVBoxLayout(properties)
        self.form = QFormLayout()
        self.form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        props.addLayout(self.form)
        self.fields = {}
        for key, label, lower, upper, decimals in [
            ('at', '工程起点', 0, 864000, 3), ('in', '来源入点', 0, 864000, 3),
            ('out', '来源出点', 0, 864000, 3), ('duration', '时长', .04, 864000, 3),
            ('speed', '速度', .25, 4, 2), ('volume', '音量 %', 0, 200, 0),
            ('x', 'X %', -100, 200, 1), ('y', 'Y %', -100, 200, 1),
            ('w', '宽度 %', 2, 200, 1), ('h', '高度 %', 2, 200, 1),
            ('opacity', '不透明度 %', 0, 100, 0), ('font_size', '字号(1080p)', 8, 200, 0),
            ('rotation', '旋转 °', -180, 180, 1),
            ('fade_in', '淡入 秒', 0, 3600, 2), ('fade_out', '淡出 秒', 0, 3600, 2),
            ('track', '轨道层级', 1, 999, 0)]:
            field = QDoubleSpinBox()
            field.setDecimals(decimals)
            field.setRange(lower, upper)
            field.setKeyboardTracking(False)
            field.setMinimumWidth(0)
            field.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            field.setAccessibleName(label)
            self.fields[key] = field
            self.form.addRow(label, field)
        self.text = QPlainTextEdit()
        self.text.setMinimumHeight(55)
        self.text.setMaximumHeight(90)
        self.text.setAccessibleName('叠加文字内容')
        self.form.addRow('文字', self.text)
        self.color = QLineEdit('#ffffff')
        self.form.addRow('颜色', self.color)
        self.text_style = TextStyleOptions()
        self.text_style.presetRequested.connect(self.apply_text_preset)
        self.form.addRow(self.text_style)
        self.image_flip = QCheckBox('水平翻转贴纸')
        self.form.addRow(self.image_flip)
        button('应用属性', self.apply_fields, props)
        self.visual_tools = QWidget()
        tools = QVBoxLayout(self.visual_tools)
        tools.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        button('居中', lambda: self.place_selected('center'), row)
        button('铺满画面', lambda: self.place_selected('fill'), row)
        tools.addLayout(row)
        row = QHBoxLayout()
        button('下移一层', lambda: self.move_visual(-1), row)
        button('上移一层', lambda: self.move_visual(1), row)
        tools.addLayout(row)
        button('取消旋转', lambda: self.place_selected('reset'), tools)
        props.addWidget(self.visual_tools)
        self.note = QLineEdit()
        self.note.setPlaceholderText('工程备注')
        self.note.editingFinished.connect(self.rename)
        props.addWidget(self.note)
        self.canvas = QComboBox()
        self.canvas.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        for title, size in [('横屏 1280×720', (1280, 720)), ('横屏 1920×1080', (1920, 1080)),
                            ('竖屏 720×1280', (720, 1280)), ('方形 1080×1080', (1080, 1080))]:
            self.canvas.addItem(title, size)
        self.canvas.currentIndexChanged.connect(self.canvas_changed)
        props.addWidget(QLabel('画布'))
        props.addWidget(self.canvas)
        self.frame_rate = QSpinBox()
        self.frame_rate.setRange(1, 60)
        self.frame_rate.setValue(30)
        self.frame_rate.editingFinished.connect(self.canvas_changed)
        props.addWidget(QLabel('工程帧率'))
        props.addWidget(self.frame_rate)
        self.chat = QCheckBox('压制主轨来源弹幕')
        self.chat.toggled.connect(self.canvas_changed)
        props.addWidget(self.chat)
        self.encoding = EncodingOptions()
        self.encoding.changed.connect(lambda: self.change(lambda data: data.update(encoding=self.encoding.value())))
        props.addWidget(QLabel('导出设置'))
        props.addWidget(self.encoding)
        props.addStretch()
        scroll.setWidget(properties)
        self.right_tabs = QTabWidget()
        self.right_tabs.setMaximumWidth(350)
        self.right_tabs.addTab(right, '属性')
        self.subtitle_panel = SubtitlePanel(self)
        self.right_tabs.addTab(self.subtitle_panel, '字幕')
        top.addWidget(self.right_tabs)
        top.setSizes([190, 650, 240])
        top.setStretchFactor(1, 1)
        bottom = QWidget()
        timeline_box = QVBoxLayout(bottom)
        timeline_box.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.undo_button = button('撤销', lambda: self.history(False), row)
        self.redo_button = button('重做', lambda: self.history(True), row)
        self.split_button = button('分割 S', self.split, row)
        button('复制', self.duplicate, row)
        button('删除', self.remove, row)
        button('← 前移', lambda: self.move_clip(-1), row)
        button('后移 →', lambda: self.move_clip(1), row)
        self.snap = QCheckBox('吸附')
        self.snap.setChecked(True)
        row.addWidget(self.snap)
        row.addStretch()
        button('−', lambda: self.zoom(.8), row)
        button('+', lambda: self.zoom(1.25), row)
        timeline_box.addLayout(row)
        self.timeline = ProjectTimeline()
        self.snap.toggled.connect(lambda value: setattr(self.timeline, 'snap', value))
        self.timeline.selected.connect(self.select_item)
        self.timeline.seekRequested.connect(self.seek)
        self.timeline.editRequested.connect(self.timeline_edit)
        self.timeline.filesDropped.connect(lambda paths: self.import_files(paths, append=True))
        self.timeline_scroll = QScrollArea()
        self.timeline_scroll.setWidgetResizable(True)
        self.timeline_scroll.setWidget(self.timeline)
        timeline_box.addWidget(self.timeline_scroll, 1)
        self.vertical.addWidget(bottom)
        self.vertical.setSizes([470, 220])
        self.vertical.setStretchFactor(0, 1)
        self.status = QLabel('外部文件仅被引用；删除片段或工程不会删除来源文件。')
        self.status.setWordWrap(True)
        self.status.setObjectName('SettingsHint')
        box.addWidget(self.status)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(600)
        self.debounce.timeout.connect(self.launch_preview)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        for key, callback in [('Space', self.toggle_play), ('S', self.split), ('Delete', self.remove),
                              ('Ctrl+D', self.duplicate),
                              ('Ctrl+Z', lambda: self.history(False)), ('Ctrl+Shift+Z', lambda: self.history(True))]:
            shortcut = QShortcut(QKeySequence(key), self.timeline)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)
        shortcut = QShortcut(QKeySequence('Ctrl+S'), self)
        shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        shortcut.activated.connect(self.save)

    def guard(self, function):
        try:
            return function()
        except Exception as error:
            self.status.setText(str(error))
            return None

    def open_document(self, document):
        self.shutdown_preview()
        self.document = document
        self.position, self.selected_id, self.loaded_key = 0, '', None
        self.refresh()
        self.request_preview()

    def refresh(self):
        if not self.document:
            return
        data = self.document.data
        self.title.setText(data['title'])
        self.note.setText(data.get('note', ''))
        path = self.bin.currentItem().data(Qt.UserRole) if self.bin.currentItem() else None
        self.bin.clear()
        for key, info in data['media'].items():
            missing = not Path(key).is_file()
            label = {'video': '视频', 'audio': '音频', 'image': '图片'}[info['type']]
            item = QListWidgetItem(f'{label} · {info["title"]}' + (' [缺失]' if missing else ''))
            item.setData(Qt.UserRole, key)
            item.setToolTip(key)
            if info.get('cover_path') and Path(info['cover_path']).is_file():
                item.setIcon(QIcon(info['cover_path']))
            self.bin.addItem(item)
            if key == path:
                self.bin.setCurrentItem(item)
        self.timeline.set_project(data, self.selected_id)
        self.undo_button.setEnabled(bool(self.document.undo_stack))
        self.redo_button.setEnabled(bool(self.document.redo_stack))
        self.canvas.blockSignals(True)
        index = self.canvas.findData((data['render']['width'], data['render']['height']))
        if index >= 0:
            self.canvas.setCurrentIndex(index)
        else:
            self.canvas.addItem(f'自定义 {data["render"]["width"]}×{data["render"]["height"]}',
                                (data['render']['width'], data['render']['height']))
            self.canvas.setCurrentIndex(self.canvas.count()-1)
        self.canvas.blockSignals(False)
        self.frame_rate.setValue(data['render']['fps'])
        self.chat.blockSignals(True)
        self.chat.setChecked(data.get('danmaku', False))
        self.chat.blockSignals(False)
        self.encoding.set_value(data.get('encoding'))
        self.subtitle_panel.refresh()
        self.select_item(self.selected_id)
        self.clock.setText(timecode(self.position) + ' / ' + timecode(duration(data)))

    def change(self, operation):
        if not self.document:
            return
        def apply():
            revision = self.document.revision
            result = self.document.commit(operation)
            if self.document.revision == revision:
                return result
            self.refresh()
            self.status.setText('工程已保存：' + str(self.document.path))
            self.request_preview()
            return result
        return self.guard(apply)

    def selected(self):
        if not self.document:
            return None
        for item, start, end in placed_clips(self.document.data):
            if item['id'] == self.selected_id:
                return item, True, start, end
        for item, start, end in placed_layers(self.document.data):
            if item['id'] == self.selected_id:
                return item, False, start, end
        return None

    def select_item(self, item_id):
        self.selected_id = item_id
        self.timeline.selection = item_id
        self.timeline.update()
        block = next((row for row in self.timeline.blocks if row[0]['id'] == item_id), None)
        if block:
            self.timeline_scroll.ensureVisible(round(block[3].center().x()), round(block[3].center().y()), 40, 20)
        selected = self.selected()
        self.selected_label.setText('选择片段或轨道内容' if not selected else '主视频片段' if selected[1] else {'text': '文字', 'image': '贴纸', 'video': '画中画', 'audio': '音频'}[selected[0]['kind']])
        for key, field in self.fields.items():
            visible = selected is not None
            if selected:
                item, main, start, end = selected
                kind = 'main' if main else item['kind']
                visible = key in {'at', 'duration'} or key in {'in', 'out', 'speed', 'volume'} and kind in {'main', 'audio', 'video'} \
                    or key in {'x', 'y', 'w', 'h', 'opacity', 'rotation'} and kind in {'image', 'text', 'video'} \
                    or key == 'font_size' and kind == 'text' or key in {'fade_in', 'fade_out'} and kind == 'audio' or key == 'track' and not main
                values = {'at': start, 'duration': end-start, 'in': item['start'] if main else item.get('in', 0),
                          'out': item['end'] if main else item.get('in', 0) + item['duration'] * item.get('speed', 1)}
                field.setValue(values.get(key, item.get(key, 0) * (100 if key in ('volume', 'x', 'y', 'w', 'h', 'opacity') else 1)))
                field.setReadOnly(main and key in ('at', 'duration') or not main and key == 'out')
            field.setVisible(visible)
            self.form.labelForField(field).setVisible(visible)
        text_visible = selected is not None and not selected[1] and selected[0]['kind'] == 'text'
        for field in (self.text, self.color):
            field.setVisible(text_visible)
            self.form.labelForField(field).setVisible(text_visible)
        if text_visible:
            self.text.setPlainText(selected[0]['text'])
            self.color.setText(selected[0]['color'])
            self.text_style.set_values(selected[0].get('text_style', {}))
        self.text_style.setVisible(text_visible)
        image_visible = selected is not None and not selected[1] and selected[0]['kind'] == 'image'
        self.image_flip.setVisible(image_visible)
        if image_visible:
            self.image_flip.setChecked(selected[0].get('flip', False))
        if any(cue['id'] == item_id for cue in self.document.data.get('subtitles', [])):
            self.right_tabs.setCurrentWidget(self.subtitle_panel)
            self.subtitle_panel.select(item_id)
        elif selected is not None:
            self.right_tabs.setCurrentIndex(0)
        self.visual_tools.setVisible(selected is not None and not selected[1] and selected[0]['kind'] != 'audio')
        self.stage.update()

    def apply_fields(self):
        selected = self.selected()
        if not selected:
            return
        identity, main = selected[0]['id'], selected[1]
        values = {key: field.value() for key, field in self.fields.items()}
        text, color = self.text.toPlainText(), self.color.text()
        style, flip = self.text_style.values(), self.image_flip.isChecked()
        def operation(data):
            item = next(row for row in data['clips' if main else 'layers'] if row['id'] == identity)
            if main:
                item.update(start=values['in'], end=values['out'], speed=values['speed'], volume=values['volume']/100)
            else:
                anchor, source_at = anchor_at(data, values['at'])
                item.update(anchor=anchor, source_at=source_at, duration=values['duration'], track=round(values['track']))
                for key in ('x', 'y', 'w', 'h', 'opacity', 'volume'):
                    item[key] = values[key]/100
                item.update(speed=values['speed'], **{'in': values['in']}, fade_in=values['fade_in'], fade_out=values['fade_out'])
                item.update(text=text, color=color, font_size=values['font_size'])
                item['rotation'] = values['rotation']
                if item['kind'] == 'text':
                    item['text_style'] = style
                elif item['kind'] == 'image':
                    item['flip'] = flip
        self.change(operation)

    def apply_text_preset(self):
        selected = self.selected()
        if not selected or selected[1] or selected[0]['kind'] != 'text':
            return
        color, stroke, background, shadow = self.text_style.presets.currentData()
        self.color.setText(color)
        self.text_style.set_values({**self.text_style.values(), 'stroke': stroke,
            'background': background, 'shadow': shadow, 'outline': 6 if stroke else 0, 'bold': True})
        self.apply_fields()

    def place_item(self, identity, values):
        def operation(data):
            item = next(row for row in data['layers'] if row['id'] == identity)
            item.update(values)
        self.change(operation)

    def place_selected(self, action):
        selected = self.selected()
        if not selected or selected[1] or selected[0]['kind'] == 'audio':
            return
        item = selected[0]
        if action == 'reset':
            self.place_item(item['id'], {'rotation': 0})
            return
        w, h = item['w'], item['h']
        if action == 'fill':
            render = self.document.data['render']
            info = self.document.data['media'].get(item.get('source'), {})
            ratio = info.get('video_width', render['width'])/max(1, info.get('video_height', render['height']))
            w, h = max(1, ratio*render['height']/render['width']), max(1, render['width']/ratio/render['height'])
            factor = min(1, 2/max(w, h))
            w, h = w*factor, h*factor
        self.place_item(item['id'], dict(x=(1-w)/2, y=(1-h)/2, w=w, h=h))

    def move_visual(self, direction):
        selected = self.selected()
        if not selected or selected[1] or selected[0]['kind'] == 'audio':
            return
        track = selected[0]['track']
        def operation(data):
            tracks = sorted({row['track'] for row in data['layers'] if row['kind'] != 'audio'})
            index = tracks.index(track)+direction
            if 0 <= index < len(tracks):
                other = tracks[index]
                for row in data['layers']:
                    if row['kind'] != 'audio':
                        row['track'] = other if row['track'] == track else track if row['track'] == other else row['track']
        self.change(operation)

    def choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, '引用外部文件', '', '媒体 (*.mp4 *.mkv *.mov *.ts *.flv *.webm *.m3u8 *.mp3 *.wav *.flac *.m4a *.aac *.ogg *.opus *.png *.jpg *.jpeg *.webp *.gif)')
        self.import_files(paths)

    def import_files(self, paths, append=False):
        if not self.document:
            return
        for path in paths:
            job = self.hub.submit('读取工程文件：' + Path(path).name, 'project_import', inspect_file,
                                  self.workspace.service.options()['ffmpeg'], path, self.hub.root / 'work', thumbnails=self.hub.root/'project_thumbnails')
            self.imports.append((job, self.document, append, None))
        self.status.setText('正在读取外部文件信息…')

    def selected_file(self):
        item = self.bin.currentItem()
        return item.data(Qt.UserRole) if item else None

    def append_selected(self):
        path = self.selected_file()
        if path:
            identity = self.change(lambda data: append_clip(data, path))
            if identity:
                self.select_item(identity)

    def add_media_layer(self, kind):
        path = self.selected_file()
        if not path:
            return
        info = self.document.data['media'][path]
        valid = info['type'] == {'video': 'video', 'image': 'image', 'audio': 'audio'}[kind] or kind == 'audio' and info.get('has_audio')
        if not valid:
            self.status.setText('请选择相应的视频、图片或包含声音的文件。')
            return
        identity = self.change(lambda data: add_layer(data, kind, self.position, path))
        if identity:
            self.select_item(identity)

    def add_text(self):
        identity = self.change(lambda data: add_layer(data, 'text', self.position))
        if identity:
            self.select_item(identity)

    def analyze_waveform(self):
        path = self.selected_file()
        if path:
            job = self.hub.submit('分析音频波形：' + Path(path).name, 'project_waveform', waveform,
                                  self.workspace.service.options()['ffmpeg'], deepcopy(self.document.data['media'][path]), self.hub.root / 'work')
            self.waves.append((job, self.document, path))

    def relocate(self):
        original = self.selected_file()
        if not original:
            return
        path, _ = QFileDialog.getOpenFileName(self, '重新定位来源文件')
        if path:
            job = self.hub.submit('重新定位文件', 'project_import', inspect_file,
                                  self.workspace.service.options()['ffmpeg'], path, self.hub.root / 'work', thumbnails=self.hub.root/'project_thumbnails')
            self.imports.append((job, self.document, False, original))

    def split(self):
        cue = next((row for row in placed_cues(self.document.data) if row[0]['id'] == self.selected_id), None) if self.document else None
        if cue and cue[1]+.04 < self.position < cue[2]-.04:
            def operation(data):
                from .project import identity
                original = next(row for row in data['subtitles'] if row['id'] == cue[0]['id'])
                clip = next(row for row in data['clips'] if row['id'] == original['anchor'])
                cut = max(original['start'], clip['start'])+(self.position-cue[1])*clip['speed']
                data['subtitles'].append(dict(original, id=identity(), start=cut))
                original['end'] = cut
            self.change(operation)
            return
        if self.selected():
            item, main, start, end = self.selected()
            if main:
                identity = self.change(lambda data: split_clip(data, item['id'], self.position))
                if identity:
                    self.select_item(identity)
            elif start + .04 < self.position < end - .04:
                def operation(data):
                    layer = next(row for row in data['layers'] if row['id'] == item['id'])
                    from .project import identity
                    copy = dict(layer, id=identity(), duration=end-self.position)
                    copy['anchor'], copy['source_at'] = anchor_at(data, self.position)
                    copy['in'] += (self.position-start)*copy.get('speed', 1)
                    layer['duration'] = self.position-start
                    for content in (layer, copy):
                        for key in ('fade_in', 'fade_out'):
                            content[key] = min(content[key], content['duration'])
                    data['layers'].append(copy)
                self.change(operation)

    def remove(self):
        self.change(lambda data: remove_item(data, self.selected_id))

    def duplicate(self):
        identity = self.change(lambda data: duplicate_item(data, self.selected_id))
        if identity:
            self.select_item(identity)

    def move_clip(self, direction):
        def operation(data):
            index = next((i for i, row in enumerate(data['clips']) if row['id'] == self.selected_id), -1)
            if index >= 0 and 0 <= index+direction < len(data['clips']):
                data['clips'].insert(index+direction, data['clips'].pop(index))
        self.change(operation)

    def timeline_edit(self, edit):
        cue = next((row for row in self.document.data.get('subtitles', []) if row['id'] == edit['id']), None)
        if cue:
            start, end = edit['start'], edit['end']
            if edit['edge'] == 'move':
                end += edit['preview']-start
                start = edit['preview']
            elif edit['edge'] == 'left':
                start = edit['preview']
            else:
                end = edit['preview']
            self.change(lambda data: set_cue(data, cue['id'], start, end, cue['text']))
            return
        def operation(data):
            item = next(row for row in data['clips' if edit['main'] else 'layers'] if row['id'] == edit['id'])
            stamp = edit['preview']
            delta = stamp - (edit['end'] if edit['edge'] == 'right' else edit['start'])
            if edit['main']:
                if edit['edge'] == 'move':
                    index = next((i for i, (_, start, end) in enumerate(placed_clips(data)) if stamp < (start+end)/2), len(data['clips'])-1)
                    data['clips'].remove(item)
                    data['clips'].insert(index, item)
                else:
                    item['start' if edit['edge'] == 'left' else 'end'] += delta * item['speed']
            elif edit['edge'] == 'right':
                item['duration'] += delta
            else:
                item['anchor'], item['source_at'] = anchor_at(data, stamp)
                if edit['edge'] == 'left':
                    item['duration'] -= delta
                    if item['kind'] in ('video', 'audio'):
                        item['in'] += delta*item['speed']
        self.change(operation)

    def history(self, redo):
        if self.document:
            self.guard(self.document.redo if redo else self.document.undo)
            self.refresh()
            self.request_preview()

    def zoom(self, factor):
        self.timeline.zoom = max(.25, min(64, self.timeline.zoom*factor))
        self.timeline.reflow()

    def rename(self):
        if self.document:
            title, note = self.title.text().strip() or '未命名工程', self.note.text()
            self.change(lambda data: data.update(title=title, note=note))

    def canvas_changed(self, *_):
        if self.document:
            size, fps, chat = self.canvas.currentData(), self.frame_rate.value(), self.chat.isChecked()
            self.change(lambda data: data.update(render={'width': size[0], 'height': size[1], 'fps': fps}, danmaku=chat))

    def save(self):
        if self.document:
            def save():
                self.document.save()
                self.status.setText('工程已保存：' + str(self.document.path))
            self.guard(save)

    def save_as(self):
        if not self.document:
            return
        path, _ = QFileDialog.getSaveFileName(self, '另存剪辑工程', self.document.data['title']+'.ddcut.json', '剪辑工程 (*.ddcut.json)')
        if path:
            if not path.lower().endswith('.ddcut.json'):
                path += '.ddcut.json'
            def save():
                write_project(path, self.document.data)
                self.document.path = Path(path)
                paths = self.hub.preferences.setdefault('project_paths', [])
                resolved = str(Path(path).resolve())
                if resolved not in paths:
                    paths.append(resolved)
                    self.hub.save()
            self.guard(save)

    def export(self):
        if not self.document:
            return
        path, _ = QFileDialog.getSaveFileName(self, '导出剪辑工程', self.document.data['title']+'.mp4', '视频 (*.mp4)')
        if path:
            if Path(path).suffix.lower() != '.mp4':
                path += '.mp4'
            self.guard(lambda: self.hub.submit('导出工程：'+self.document.data['title'], 'export', render_project,
                self.workspace.service.options()['ffmpeg'], deepcopy(self.document.data), path, self.hub.root/'work',
                project_path=str(self.document.path)))
            self.workspace.toggle_tasks(True)

    def request_preview(self, force=False):
        if not self.document or not self.isVisible():
            return
        self.playing = False
        if self.preview.core:
            self.preview.core.pause = True
        data = self.document.data
        if not data['clips']:
            self.preview.stop()
            self.preview_hint.setText('从左侧外部文件添加主视频片段。')
            return
        self.position = min(self.position, max(0, duration(data)-.02))
        begin = max(0, int(self.position//8)*8)
        self.preview_dirty = True
        key = hashlib.sha256((json.dumps(data, sort_keys=True, ensure_ascii=False)+f'/{begin}').encode()).hexdigest()
        self.pending_preview = (key, begin, self.position, False, deepcopy(data))
        self.preview_hint.setText('工程已修改，正在更新当前位置的预览…')
        self.debounce.start(0 if force else 600)

    def launch_preview(self):
        if not self.pending_preview or not self.isVisible():
            return
        if self.preview_job and not self.preview_job.future.done():
            self.hub.cancel(self.preview_job)
            self.debounce.start(200)
            return
        key, begin, position, playing, data = self.pending_preview
        self.pending_preview = None
        target = self.hub.root/'project_previews'/(key+'.mp4')
        if target.is_file():
            self.load_preview(target, key, begin, position, playing)
            return
        self.preview_request = key, begin, position, playing
        self.preview_job = self.hub.submit('工程预览：'+data['title'], 'project_preview', render_project,
            self.workspace.service.options()['ffmpeg'], data, target, self.hub.root/'work',
            begin=begin, length=8, preview=True, project_path=str(self.document.path))

    def load_preview(self, path, key, begin, position, playing):
        self.stage.clear()
        self.preview.load(str(path))
        self.stage.attach()
        self.preview.chat.enabled = False
        self.loaded_key, self.preview_begin = key, begin
        self.preview_dirty = False
        self.preview_end = min(duration(self.document.data), begin+8)
        self.resume = position-begin, playing
        self.preview_hint.setText('工程预览已更新；跨预览段时会等待下一段合成。')

    def seek(self, position):
        self.position = max(0, min(duration(self.document.data), position))
        self.timeline.position = self.position
        self.timeline.update()
        self.guard(self.stage.update)
        if self.preview.core and self.preview_begin <= position < self.preview_end and not self.preview_dirty:
            self.preview.seek(position-self.preview_begin)
        else:
            self.request_preview(force=True)

    def toggle_play(self):
        if self.preview_job and not self.pending_preview and self.preview_dirty:
            key, begin, position, playing = self.preview_request
            self.preview_request = key, begin, position, not playing
            return
        if self.pending_preview or not self.preview.core or self.preview_dirty:
            self.request_preview(force=True)
            if self.pending_preview:
                key, begin, pos, _, data = self.pending_preview
                self.pending_preview = key, begin, pos, True, data
            return
        self.playing = not self.playing
        self.preview.core.pause = not self.playing

    def set_mute(self, value):
        if self.preview.core:
            self.preview.core.mute = value

    def tick(self):
        while self.imports and self.imports[0][0].future.done():
            job, document, append, original = self.imports.pop(0)
            if job.state != 'done':
                self.status.setText(job.message)
                continue
            info = job.result
            def operation(data):
                if original:
                    data['media'].pop(original)
                    for row in data['clips'] + data['layers']:
                        if row.get('source') == original:
                            row['source'] = info['path']
                data['media'][info['path']] = info
                if append and info['type'] == 'video':
                    append_clip(data, info['path'])
            self.guard(lambda: document.commit(operation))
            if self.document is document:
                self.refresh()
                self.request_preview()
        for item in list(self.waves):
            job, document, path = item
            if job.future.done():
                self.waves.remove(item)
                if job.state == 'done' and path in document.data['media']:
                    document.data['media'][path]['peaks'] = job.result
                    self.guard(document.save)
                    if document is self.document:
                        self.timeline.reflow()
                elif job.state != 'done':
                    self.status.setText(job.message)
        if self.preview_job and self.preview_job.future.done():
            job, self.preview_job = self.preview_job, None
            if not self.pending_preview and self.isVisible():
                if job.state == 'done':
                    self.guard(lambda: self.load_preview(job.output, *self.preview_request))
                elif job.state == 'failed':
                    self.preview_hint.setText('预览失败：'+job.message)
        if not self.isVisible() or not self.document:
            return
        if self.preview.core:
            if self.resume is not None and self.preview.core.time_pos is not None:
                position, playing = self.resume
                self.resume = None
                self.preview.seek(position)
                self.preview.core.mute = self.muted.isChecked()
                self.preview.core.pause = not playing
                self.playing = playing
            if self.playing:
                self.position = self.preview_begin+self.preview.position()
                if self.preview.core.eof_reached or self.position >= self.preview_end-.02:
                    if self.preview_end < duration(self.document.data)-.02:
                        self.position = self.preview_end
                        self.request_preview(force=True)
                        if self.pending_preview:
                            key, begin, pos, _, data = self.pending_preview
                            self.pending_preview = key, begin, pos, True, data
                    else:
                        self.playing = False
        self.timeline.position = self.position
        self.timeline.update()
        self.clock.setText(timecode(self.position)+' / '+timecode(duration(self.document.data)))
        self.play_button.setText('暂停' if self.playing else '播放')
        self.guard(self.stage.update)

    def shutdown_preview(self):
        self.debounce.stop()
        self.pending_preview = None
        self.stage.clear()
        self.preview.stop()
        self.resume = None
        self.playing = False
        if self.preview_job:
            self.hub.cancel(self.preview_job)
            self.preview_job = None

    def leave(self):
        self.shutdown_preview()
        self.backRequested.emit()

    def discard_document(self, path):
        if self.document and self.document.path == Path(path):
            self.shutdown_preview()
            document = self.document
            for row in self.imports + self.waves:
                if row[1] is document:
                    self.hub.cancel(row[0])
            self.imports = [row for row in self.imports if row[1] is not document]
            self.waves = [row for row in self.waves if row[1] is not document]
            self.document = None
