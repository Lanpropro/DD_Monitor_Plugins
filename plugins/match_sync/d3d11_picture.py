"""D3D11 main-picture presentation from the existing audio clock and frame cache."""
from collections import deque
from dataclasses import dataclass
import os
from pathlib import Path
import sys
from sysconfig import get_platform
from threading import Event, Thread
from time import perf_counter, sleep, time

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QImage, QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication

from .audio import observed_media_time
from .d3d11_transport import FrameTransport, window_api
from .engine import RATE


@dataclass(frozen=True)
class PlaybackTarget:
    decoder: object
    generation: int
    stamp: float
    sampled_at: float
    rate: float
    audio: object = None
    observation: tuple | None = None
    correction: float = 0.
    settings_move: Event | None = None

    def sample(self, now):
        elapsed = max(0., now - self.sampled_at)
        advance = min(elapsed, .080) * self.rate
        if elapsed > .080 and self.observation is not None:
            # Outside a native settings drag, stay within the GUI's original audio queue.
            current = self.audio.clock_observation
            observed, played, written, anchor, _ = self.observation
            if (current is not None and current[0] > self.sampled_at and current[3] == anchor
                    and self.audio.correction == self.correction and current[1] >= played):
                at_target = min(written, played + max(0., self.sampled_at - observed) * RATE)
                if self.settings_move is not None and self.settings_move.is_set() and current[4]:
                    played_time = observed_media_time(current, min(current[1], current[2]))
                else:
                    played_time = observed_media_time(self.observation, min(written, current[1]))
                confirmed = played_time - observed_media_time(self.observation, at_target)
                advance = max(advance, min(confirmed, elapsed * self.rate))
        return self.stamp + advance


@dataclass(frozen=True)
class PresentedFrame:
    target: PlaybackTarget
    frame: tuple
    hwnd: int
    process_id: int
    submitted: int
    presented: int
    displayed_stamp: float = 0.


class PictureWorker:
    def __init__(self, directory, parent, *, transport=FrameTransport):
        self.directory = directory
        self.parent = parent
        self.transport = transport
        self.target = None
        self.latest = None
        self.error = ''
        self.precise_wait = False
        self.stop = Event()
        self.publish_gaps = deque(maxlen=12)
        self.thread = Thread(target=self._run, name='match-d3d11', daemon=True)
        self.thread.start()

    def _run(self):
        output = None
        last = None
        published_at = last_poll = None
        poll_interval_max = wait_max = lookup_max = target_age_max = 0.
        duplicates = missing = 0
        try:
            while True:
                wait_started = perf_counter()
                if self.precise_wait and self.target is not None:
                    sleep(.004)
                    if self.stop.is_set():
                        break
                elif self.stop.wait(.004 if self.target is not None else .016):
                    break
                target = self.target
                if target is None:
                    last = None
                    published_at = last_poll = None
                    poll_interval_max = wait_max = lookup_max = target_age_max = 0.
                    duplicates = missing = 0
                    continue
                poll = perf_counter()
                wait_max = max(wait_max, poll - wait_started)
                if last_poll is not None:
                    poll_interval_max = max(poll_interval_max, poll - last_poll)
                target_age_max = max(target_age_max, poll - target.sampled_at)
                last_poll = poll
                frame = target.decoder.picture_at(target.sample(poll))
                lookup_max = max(lookup_max, perf_counter() - poll)
                if frame is None or target.stamp - frame[0] > .15:
                    missing += 1
                    continue
                current = self.target
                if (current is None or current.decoder is not target.decoder
                        or current.generation != target.generation
                        or abs(current.sample(perf_counter()) - target.sample(perf_counter())) > .1):
                    continue
                stamp, image = frame
                identity = target.decoder, target.generation, stamp
                if identity == last or (self.latest is not None and self.latest.target is target
                                        and stamp < self.latest.frame[0]):
                    duplicates += 1
                    continue
                if not isinstance(image, QImage):
                    image = QImage.fromData(image)
                if image.isNull():
                    continue
                if image.format() != QImage.Format_ARGB32:
                    image = image.convertToFormat(QImage.Format_ARGB32)
                geometry = image.width(), image.height(), image.bytesPerLine()
                if output is None or output.geometry != geometry:
                    self.latest = None
                    published_at = None
                    self.publish_gaps.clear()
                    if output is not None:
                        output.close()
                    output = self.transport(self.directory, self.parent, image)
                if self.stop.is_set():
                    break
                before_publish = perf_counter()
                output.publish(stamp, image)
                now = perf_counter()
                if published_at is not None and now - published_at > .050 and last[:2] == identity[:2]:
                    self.publish_gaps.append((now, dict(sequence=output.sequence, ended_at=time(),
                        gap_ms=round((now - published_at) * 1000, 3),
                        poll_interval_max_ms=round(poll_interval_max * 1000, 3),
                        wait_max_ms=round(wait_max * 1000, 3),
                        lookup_max_ms=round(lookup_max * 1000, 3),
                        target_age_max_ms=round(target_age_max * 1000, 3),
                        publish_ms=round((now - before_publish) * 1000, 3),
                        duplicates=duplicates, missing=missing)))
                published_at = last_poll = now
                poll_interval_max = wait_max = lookup_max = target_age_max = 0.
                duplicates = missing = 0
                self.latest = PresentedFrame(target, (stamp, image), output.hwnd, output.process.pid,
                                             output.sequence, output.displayed(), output.displayed_stamp())
                last = identity
        except (OSError, RuntimeError, ValueError, TypeError, BufferError) as error:
            self.error = str(error)
        finally:
            if output is not None:
                output.close()


def install(viewer):
    overlay = viewer.video_danmaku
    if (sys.platform != 'win32' or QApplication.platformName() != 'windows'
            or not viewer.split_presentation or overlay is None
            or (viewer.quick_danmaku is None and not hasattr(overlay, 'nativeAlphaFailed'))):
        return None
    override = os.environ.get('DDM_MATCH_D3D11_DIR')
    if override:
        directory = Path(override)
    elif get_platform() == 'win-amd64':
        directory = Path(__file__).resolve().parent / 'native' / 'd3d11'
    else:
        return None
    if all((directory / name).is_file() for name in ('presenter.exe', 'mailbox.dll')):
        return D3D11Picture(viewer, directory)
    return None


class D3D11Picture(QObject):
    stateChanged = Signal()

    def __init__(self, viewer, directory):
        super().__init__(viewer)
        self.viewer, self.directory = viewer, directory
        self.settings_move = Event()
        self.canvas = viewer.canvas
        self.worker = None
        self.requested = viewer.context.setting('main_picture_renderer', 'd3d11') == 'd3d11'
        self.available = True
        self.hwnd = None
        self.area = None
        self.user = window_api()
        # Keep the overlay configuration identical in both arms of this diagnostic session.
        if viewer.quick_danmaku is None:
            viewer.video_danmaku.set_native_video(True, allow_quick=False, per_pixel=True)
        if hasattr(viewer.video_danmaku, 'nativeAlphaFailed'):
            viewer.video_danmaku.nativeAlphaFailed.connect(self.overlay_failed)
        self.shortcut = QShortcut(QKeySequence('Ctrl+Alt+G'), viewer)
        self.shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self.shortcut.activated.connect(self.toggle)

    def overlay_failed(self):
        if not self.available:
            return
        self.available = False
        self.shortcut.setEnabled(False)
        self.set_requested(False, failure='弹幕透明层不可用，已切回光栅主画面')

    def toggle(self):
        self.select(not self.requested)

    def select(self, requested):
        if not self.available:
            return
        self.viewer.context.set_setting('main_picture_renderer', 'd3d11' if requested else 'raster')
        self.set_requested(requested)

    def set_requested(self, requested, *, failure=''):
        if not failure and (requested == self.requested or not self.available):
            return
        self.requested = requested
        if not self.requested:
            self.stop()
        self.stateChanged.emit()
        self.viewer.context.notify(failure or ('比赛二路已请求 D3D11 主画面' if requested else '比赛二路已切回光栅主画面'))

    def suspend(self):
        if self.worker is not None:
            self.worker.target = None
            self.worker.latest = None
        if self.hwnd is not None:
            self.user.ShowWindowAsync(self.hwnd, 0)
        self.hwnd = self.area = None
        if self.canvas.native_presenting:
            self.canvas.native_presenting = False
            self.canvas.update()

    def stop(self):
        self.suspend()
        if self.worker is not None:
            self.worker.stop.set()
            self.worker = None

    def render(self, decoder, stamp, rate):
        viewer = self.viewer
        if (not self.requested or viewer.fullscreen_dialog is not None or viewer.replay.active
                or self.canvas.zoom_crop or viewer.replay_bar.preview.isVisible()):
            self.suspend()
            return self.canvas.show_at(decoder, stamp)
        if self.worker is None:
            self.worker = PictureWorker(self.directory, int(viewer.picture.video.winId()))
        worker = self.worker
        if worker.error:
            self.set_requested(False, failure='D3D11 主画面启动或呈现失败，已切回光栅')
            return self.canvas.show_at(decoder, stamp)
        generation = viewer.generation
        audio = viewer.audio
        observation = None if viewer.sync_waiting or viewer.offset_repositioning else audio.clock_observation
        worker.target = PlaybackTarget(decoder, generation, stamp, perf_counter(), rate,
                                       audio, observation, audio.correction, self.settings_move)
        latest = worker.latest
        if (latest is None or latest.target.decoder is not decoder or latest.target.generation != generation
                or not stamp - .15 <= latest.frame[0] <= stamp + .05):
            # During startup retain the existing raster display. A stale
            # native frame is hidden when the source or playback epoch changes.
            if self.hwnd is not None:
                self.user.ShowWindowAsync(self.hwnd, 0)
                self.hwnd = self.area = None
                self.canvas.native_presenting = False
                self.canvas.update()
            return self.canvas.show_at(decoder, stamp) if latest is None else None
        pos = self.canvas.pos()
        rect = self.canvas._image_target(latest.frame[1].rect()).toAlignedRect().translated(pos)
        ratio = self.canvas.devicePixelRatioF()
        area = tuple(round(value * ratio) for value in rect.getRect())
        moved = self.hwnd != latest.hwnd or self.area != area
        visible = bool(self.user.IsWindowVisible(latest.hwnd))
        # A dialog can hide the native child without changing its handle or geometry.
        if moved or (not visible and self.user.IsWindowVisible(worker.parent)):
            if self.hwnd is not None and self.hwnd != latest.hwnd:
                self.user.ShowWindowAsync(self.hwnd, 0)
            # Queue geometry on the presenter's thread, without waiting for Present().
            if not self.user.SetWindowPos(latest.hwnd, None, *area, 0x4014 | 0x0040):
                self.set_requested(False, failure='无法显示 D3D11 视频窗口，已切回光栅')
                return self.canvas.show_at(decoder, stamp)
            self.hwnd, self.area = latest.hwnd, area
            if moved:
                viewer.picture.raise_overlays()
                viewer.crop_stack_timer.start(0)
            visible = bool(self.user.IsWindowVisible(latest.hwnd))
        if self.canvas.native_presenting != visible:
            self.canvas.native_presenting = visible
            self.canvas.update()
        self.canvas.requested_frame_key = latest.frame[0]
        self.canvas.set_frame(latest.frame)
        return latest.frame

    def diagnostics(self):
        latest = self.worker.latest if self.worker is not None else None
        if latest is None or self.hwnd != latest.hwnd or not self.canvas.native_presenting:
            return {'backend': 'raster'}
        cutoff = perf_counter() - 2
        return {'backend': 'd3d11', 'pid': latest.process_id, 'submitted': latest.submitted,
                'presented': latest.presented, 'publish_gaps': [row for at, row in tuple(self.worker.publish_gaps)
                                                               if at >= cutoff]}
