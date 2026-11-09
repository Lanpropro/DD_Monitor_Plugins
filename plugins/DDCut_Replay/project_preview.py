"""Audio-clock project preview; raw video and graphics never require preview encoding."""
from copy import deepcopy
import ctypes
from itertools import groupby
import time
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, Qt, QTimer
from PySide6.QtGui import QImage, QPainter

from .chat import load_events
from .chat_scene import ChatScene
from .project import duration, placed_clips, placed_layers, validate
from .project_audio import AudioClock, audio_signature
from .project_media import ProjectGraphics
from .project_video import VideoFrames
from .subtitle_render import SubtitleGraphics


class PreviewCore:
    """Preserve native window/OSD input while exposing the project's audio clock."""
    def __init__(self, runtime, display):
        object.__setattr__(self, 'runtime', runtime)
        object.__setattr__(self, 'display', display)

    def __getattr__(self, key):
        runtime = self.runtime
        if key == 'time_pos':
            return runtime.position if runtime.ready else None
        if key == 'pause':
            return not runtime.want_play
        if key == 'mute':
            return runtime.muted
        if key == 'eof_reached':
            return runtime.ended
        if self.display is not None:
            return getattr(self.display, key)
        if key == 'osd_dimensions':
            return None
        raise AttributeError(key)

    def __setattr__(self, key, value):
        if key == 'pause':
            self.runtime.play(not value)
        elif key == 'mute':
            self.runtime.set_mute(value)
        else:
            setattr(self.display, key, value)

    def seek(self, stamp, reference='absolute+exact'):
        self.runtime.seek(stamp if reference.startswith('absolute') else self.runtime.position+stamp)

    def command(self, *args, **kwargs):
        if self.display is not None:
            return self.display.command(*args, **kwargs)
        raise RuntimeError('当前预览没有原生显示窗口')

    def observe_property(self, *args):
        if self.display is not None:
            return self.display.observe_property(*args)

    def register_key_binding(self, *args):
        if self.display is not None:
            return self.display.register_key_binding(*args)

    def overlay_add(self, *args):
        if self.display is not None:
            return self.display.overlay_add(*args)

    def overlay_remove(self, *args):
        if self.display is not None:
            return self.display.overlay_remove(*args)

    def terminate(self):
        self.runtime.close()
        if self.display is not None:
            self.display.terminate()
            object.__setattr__(self, 'display', None)
            self.runtime.display = None


class ProjectPreview(QObject):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor, self.widget = editor, editor.preview
        self.project, self.display, self.clock = None, None, None
        self.sources, self.images, self.scenes = {}, {}, {}
        self.audio_key = None
        self.ready, self.preparing, self.closed = False, True, False
        self.want_play, self.muted, self.ended = False, editor.muted.isChecked(), False
        self.stamp, self.pending_seek, self.seek_changed = 0, None, 0
        self.last_frame, self.buffer, self.frame_signature = None, None, None
        self.error, self.frame_ready = '', False
        self.generation = 0
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self.tick)

    @property
    def position(self):
        if self.ended and self.project:
            return duration(self.project)
        if self.pending_seek is not None:
            return self.pending_seek
        return self.clock.position if self.clock else self.stamp

    def set_project(self, project, *, force=False):
        validate(project)
        for item in project['clips']+project['layers']:
            if item.get('source') and not Path(project['media'][item['source']]['path']).is_file():
                raise FileNotFoundError('来源文件缺失，请重新定位：'+item['source'])
        force = force or bool(self.error)
        previous_size = self.project['render'] if self.project else None
        self.stamp = min(self.position, max(0, duration(project)-.001))
        self.project = deepcopy(project)
        self.error, self.ended = '', False
        self.generation += 1
        self.scenes.clear()
        if force:
            self.images.clear()
            for source in self.sources.values():
                source.close()
            self.sources.clear()
        if self.display is None or previous_size != project['render']:
            self.create_display()
        key = audio_signature(project)
        if force or self.clock is None or key != self.audio_key:
            self.audio_key = key
            self.pending_seek, self.seek_changed = self.stamp, 0
            self.preparing, self.ready = True, False
            if self.clock:
                self.clock.core.pause = True
        self.timer.start()
        self.tick()

    def create_display(self):
        render = self.project['render']
        folder = self.editor.hub.root/'project_frames'
        folder.mkdir(parents=True, exist_ok=True)
        path = folder/f'canvas-{render["width"]}x{render["height"]}.png'
        if not path.is_file():
            width = 320
            blank = QImage(width, max(2, round(width*render['height']/render['width'])), QImage.Format_RGB32)
            blank.fill(Qt.black)
            if not blank.save(str(path)):
                raise OSError('无法创建预览画布')
        # Detach the proxy before recycling only the display; the audio clock stays alive.
        if isinstance(self.widget.core, PreviewCore):
            self.widget.core = self.display
        self.editor.stage.clear()
        self.widget.load(str(path))
        self.display = self.widget.core
        if self.display is not None:
            self.display.pause = True
            self.display.mute = True
            self.display.image_display_duration = 'inf'
        self.widget.core = PreviewCore(self, self.display)
        self.widget.chat.enabled = False
        self.editor.stage.attach()

    def seek(self, stamp):
        self.stamp = max(0, min(duration(self.project), stamp))
        self.pending_seek, self.seek_changed = self.stamp, time.monotonic()
        self.preparing, self.ready, self.ended = True, False, False
        if self.clock:
            self.clock.core.pause = True
        self.tick()

    def play(self, value):
        self.want_play = bool(value)
        if value and self.ended:
            self.seek(0)
        if self.clock and not value:
            self.clock.core.pause = True
            self.stamp = self.clock.position
        self.tick()

    def set_mute(self, value):
        self.muted = bool(value)
        if self.clock:
            self.clock.core.mute = self.muted

    def source_frame(self, key, info, stamp, speed, width, height, *, contain=False, playing=False):
        source = self.sources.get(key)
        if source is not None and source.path != info['path']:
            source.close()
            source = None
        if source is None:
            source = VideoFrames(info['path'], self.widget.source_url, contain=contain)
            source.path = info['path']
            self.sources[key] = source
        return source.frame(stamp, playing, speed, width, height)

    def draw(self, draft=None):
        if self.closed or self.project is None or self.error:
            return
        render = self.project['render']
        frame = self.editor.stage.frame()
        width = max(2, min(960, round(frame.width()) or 640))
        height = max(2, round(width*render['height']/render['width']))
        stamp = min(self.position, max(0, duration(self.project)-.00001))
        playing = self.want_play and not self.preparing
        clips = placed_clips(self.project)
        main = next((row for row in clips if row[1] <= stamp < row[2]), None)
        active, video_images, ready = set(), {}, True
        main_image = None
        if main:
            clip, start, _ = main
            key = ('main', clip['id'])
            active.add(key)
            main_image, valid = self.source_frame(key, self.project['media'][clip['source']],
                clip['start']+(stamp-start)*clip['speed'], clip['speed'], width, height, contain=True, playing=playing)
            ready &= valid
            index = clips.index(main)+1
            if index < len(clips):
                following = clips[index][0]
                key = ('main', following['id'])
                active.add(key)
                self.source_frame(key, self.project['media'][following['source']], following['start'], following['speed'], width, height, contain=True)
        visuals = []
        for item, start, stop in sorted(placed_layers(self.project), key=lambda row: row[0]['track']):
            if item['kind'] == 'audio' or not start <= stamp < stop:
                continue
            if draft and draft['id'] == item['id']:
                item = draft
            visuals.append((item, start, stop))
            if item['kind'] == 'video':
                key = ('overlay', item['id'])
                active.add(key)
                image, valid = self.source_frame(key, self.project['media'][item['source']],
                    item['in']+(stamp-start)*item['speed'], item['speed'],
                    max(2, round(item['w']*width)), max(2, round(item['h']*height)), playing=playing)
                video_images[item['id']] = image
                ready &= valid
        for key in set(self.sources)-active:
            self.sources.pop(key).close()
        self.frame_ready = bool(ready)
        dimensions = self.display.osd_dimensions if self.display is not None else None
        display_ready = bool(dimensions and dimensions.get('w') and dimensions.get('h'))
        signature = (self.generation, stamp, width, height, frame.getRect(), display_ready, str(draft), id(main_image),
                     tuple((key, id(image)) for key, image in video_images.items()))
        if signature == self.frame_signature:
            return
        image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.black)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        try:
            if main_image is not None:
                painter.drawImage(0, 0, main_image)
            if main and self.project.get('danmaku'):
                clip, start, _ = main
                info = self.project['media'][clip['source']]
                if info.get('danmaku_path') and not info.get('chat_burned'):
                    key = (clip['id'], width, height)
                    if key not in self.scenes:
                        self.scenes[key] = ChatScene(load_events(info), width, height, info.get('chat_size',36), info.get('chat_opacity',80))
                    painter.drawImage(0, 0, self.scenes[key].frame_at(clip['start']+(stamp-start)*clip['speed']-info.get('danmaku_offset',0)))
            for graphics, rows in groupby(visuals, key=lambda row: row[0]['kind'] != 'video'):
                rows = list(rows)
                if graphics:
                    layer = ProjectGraphics(self.project, width, height, rows)
                    layer.images = self.images
                    painter.drawImage(0, 0, layer.frame_at(stamp))
                else:
                    for item, _, _ in rows:
                        video = video_images[item['id']]
                        if video is None:
                            continue
                        painter.save()
                        painter.setOpacity(item['opacity'])
                        w, h = item['w']*width, item['h']*height
                        painter.translate((item['x']+item['w']/2)*width, (item['y']+item['h']/2)*height)
                        painter.rotate(item.get('rotation',0))
                        painter.drawImage(QRectF(-w/2,-h/2,w,h),video)
                        painter.restore()
            if self.project.get('burn_subtitles',True):
                painter.drawImage(0,0,SubtitleGraphics(self.project,width,height).frame_at(stamp))
        finally:
            painter.end()
        self.last_frame, self.frame_signature = image, signature
        if dimensions and dimensions.get('w') and dimensions.get('h'):
            buffer = ctypes.create_string_buffer(image.constBits().tobytes())
            self.display.command('overlay-add',61,round(frame.left()),round(frame.top()),
                '&'+str(ctypes.addressof(buffer)),0,'bgra',width,height,image.bytesPerLine(),round(frame.width()),round(frame.height()))
            self.buffer = buffer

    def tick(self):
        if self.closed or self.project is None or self.error:
            return
        try:
            if self.pending_seek is not None and (self.want_play or time.monotonic()-self.seek_changed >= .08):
                if self.clock:
                    self.clock.close()
                self.clock = AudioClock(self.editor.workspace.service.options()['ffmpeg'], self.project,
                    min(self.pending_seek,max(0,duration(self.project)-.001)), self.editor.hub.root/'work',self.muted)
                self.pending_seek = None
            if self.clock:
                error = self.clock.error()
                if error:
                    raise RuntimeError(error)
                if self.clock.core.eof_reached and self.want_play:
                    self.stamp, self.want_play, self.ended = duration(self.project), False, True
                    self.clock.core.pause = True
            self.draw(self.editor.stage.drag['draft'] if self.editor.stage.drag else None)
            displayed = self.display is None or self.display.current_vo == 'null' or self.buffer is not None
            if self.preparing and self.clock and self.clock.ready and self.pending_seek is None and self.frame_ready and displayed:
                self.preparing, self.ready = False, True
            if self.clock:
                self.clock.core.pause = not (self.want_play and self.ready)
        except Exception as error:
            self.fail(error)

    def fail(self, error):
        self.error, self.ready, self.want_play = str(error), False, False
        if self.clock:
            self.clock.core.pause = True

    def refresh_frame(self, draft=None):
        try:
            self.draw(draft)
        except Exception as error:
            self.fail(error)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.timer.stop()
        if self.display is not None:
            self.display.overlay_remove(61)
        for source in self.sources.values():
            source.close()
        self.sources.clear()
        if self.clock:
            self.stamp = self.clock.position
            self.clock.close()
            self.clock = None
        self.buffer = None
        self.images.clear()
        self.scenes.clear()
        self.last_frame, self.project = None, None
        self.ready, self.want_play = False, False
