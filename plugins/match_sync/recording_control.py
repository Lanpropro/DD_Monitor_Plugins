"""Recording control. Session state remains on the Viewer facade."""
from __future__ import annotations

import shutil
import time
from .recording import MultiTrackRecorder
from pathlib import Path
from PySide6.QtCore import Qt

def _stop_recording(viewer):
    if viewer.multi_recorder is not None:
        viewer.multi_recorder.stop()
        viewer.picture.recording_button.setEnabled(False)
    recorder = getattr(viewer.context.window, "recorder", None)
    if recorder is not None and viewer.picture in recorder.sessions:
        recorder.stop(viewer.picture)


def _record_picture(viewer):
    if viewer.multi_recorder is not None:
        viewer._stop_recording()
        return
    settings = getattr(viewer.context.window, 'settings', {})
    if not settings.get('recording_enabled', True):
        viewer.notice.setText('录制功能已在「设置 → 录制」关闭')
        return
    row = viewer.rows.get(viewer.alignment.reference)
    history = row.decoder.history if row is not None and row.decoder is not None else None
    if not viewer.running or history is None or not getattr(history, 'codec', None) or history.origin is None:
        viewer.notice.setText('请等待主画面缓存就绪后再录制')
        return
    tracks = tuple((number, '、'.join(current.label() for current in viewer.rows.values() if number in current.record_tracks),
                    tuple(key for key, current in viewer.rows.items() if number in current.record_tracks))
                   for number in range(1, 7) if any(number in current.record_tracks for current in viewer.rows.values()))
    if not tracks:
        viewer.notice.setText('请先在二路控制台设置中选择至少一条录制音轨')
        return
    directory = str(settings.get('recording_dir') or '').strip()
    if not directory:
        viewer.notice.setText('请先在「设置 → 录制」选择保存目录')
        return
    try:
        folder = Path(directory).expanduser().resolve()
        folder.mkdir(parents=True, exist_ok=True)
        minimum = max(256, int(settings.get('recording_min_free_mb', 2048))) * 1024 * 1024
        if shutil.disk_usage(folder).free < minimum:
            viewer.notice.setText('录制未开始：保存目录所在磁盘剩余空间不足')
            return
        path = folder / f'match_sync_{time.strftime("%Y%m%d_%H%M%S")}_{time.time_ns() % 1000000000:09d}.{viewer.record_format.currentData()}'
        worker = MultiTrackRecorder(path, history, tracks, viewer._recording_snapshot(), minimum_free=minimum)
    except (OSError, ValueError) as error:
        viewer.notice.setText(f'无法开始分音轨录制：{error}')
        return
    viewer.multi_recorder = worker
    viewer.record_started = time.monotonic()
    worker.started.connect(viewer._recording_started, Qt.QueuedConnection)
    worker.completed.connect(viewer._recording_finished, Qt.QueuedConnection)
    viewer.picture.set_recording_state('record')
    viewer.picture.recording_button.setToolTip('停止录制主画面与所选音轨')
    if viewer.match_settings is not None:
        viewer.match_settings.refresh_recording_routes()
    viewer.notice.setText(f'开始分音轨录制：{path}')
    worker.start()


def _recording_snapshot(viewer):
    clock, shifts = viewer.replay.presentation(viewer.live_clock(), viewer.shifts())
    main = shifts.get(viewer.alignment.reference, 0)
    sources = {key: (row.decoder.history if row.decoder is not None and
                    (key == viewer.alignment.reference or row.sync_state == '已同步' or key in viewer.independent_playback)
                    else None, shift - main)
               for key, shift in shifts.items() if (row := viewer.rows.get(key)) is not None}
    return clock + main, sources


def _recording_started(viewer, path):
    if viewer.multi_recorder is viewer.sender():
        viewer.context.notify(f'二路开始录制：{path}')


def _recording_finished(viewer, path, error):
    worker = viewer.sender()
    if viewer.multi_recorder is worker:
        viewer.multi_recorder = None
        viewer.record_started = None
        viewer.record_elapsed = ''
        viewer.picture.set_recording_state('')
        viewer.picture.set_recording_elapsed('')
        viewer.picture.recording_button.setEnabled(True)
        viewer.picture.recording_button.setToolTip('录制主画面与所选音轨；在二路控制台设置中分配音轨')
        message = f'二路录制停止：{error} · 文件：{path}' if error else f'二路录制已保存：{path}'
        viewer.notice.setText(message)
        viewer.context.notify(message, kind='warning' if error else 'info')
        if viewer.match_settings is not None:
            viewer.match_settings.refresh_recording_routes()
    worker.deleteLater()
