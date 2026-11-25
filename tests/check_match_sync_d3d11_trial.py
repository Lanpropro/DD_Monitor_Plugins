"""Verify the trial with an isolated Windows window and offline frame source."""
import argparse
import ctypes
from ctypes import wintypes
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch


def visible_widget_color(widget, point):
    user = ctypes.WinDLL('user32')
    user.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
    user.GetDC.argtypes = (wintypes.HWND,)
    user.GetDC.restype = wintypes.HDC
    user.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
    gdi = ctypes.WinDLL('gdi32')
    gdi.GetPixel.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int)
    gdi.GetPixel.restype = wintypes.DWORD
    ratio = widget.devicePixelRatioF()
    window = widget.window()
    at = widget.mapTo(window, point)
    center = wintypes.POINT(round(at.x() * ratio), round(at.y() * ratio))
    assert user.ClientToScreen(int(window.winId()), ctypes.byref(center))
    screen = user.GetDC(None)
    try:
        color = gdi.GetPixel(screen, center.x, center.y)
    finally:
        user.ReleaseDC(None, screen)
    return color


def assert_blue(canvas, message, point=None):
    from PySide6.QtCore import QPoint
    point = point or QPoint(round(canvas.width() * .55), round(canvas.height() * .64))
    color = visible_widget_color(canvas, point)
    assert (color & 0xff) < 32 and ((color >> 8) & 0xff) < 32 and ((color >> 16) & 0xff) > 220, (
        message, hex(color), canvas.size(), canvas.isVisible())


def assert_comment(overlay):
    from PySide6.QtCore import QPoint

    comment, = overlay.comments
    x, y = round(comment.x), comment.lane * overlay.lane_height()
    glyph = comment.image.deviceIndependentSize()
    points = [QPoint(column, row)
              for column in range(max(0, x + 2), min(overlay.width(), x + round(glyph.width()) - 2))
              for row in range(y + 2, min(overlay.height(), y + round(glyph.height()) - 2))]
    assert points, 'The synthetic white comment must have content'
    colors = [visible_widget_color(overlay, point) for point in points[::max(1, len(points) // 6)]]
    for point, color in zip(points[::max(1, len(points) // 6)], colors):
        if color != 0xffffffff and (color & 0xff) >= 125 and ((color >> 8) & 0xff) >= 125:
            return point
    raise AssertionError(('The synthetic white comment must remain visible', list(map(hex, colors))))


def wait_for_pixels(check, *args):
    from PySide6.QtTest import QTest

    deadline = time.monotonic() + 1.5
    while True:
        try:
            return check(*args)
        except AssertionError:
            if time.monotonic() >= deadline:
                raise
            QTest.qWait(40)


def check_visible_overlay(viewer):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPixmap
    from PySide6.QtTest import QTest
    from ddm.video_danmaku import _Comment

    overlay = viewer.video_danmaku
    wait_for_pixels(assert_blue, viewer.canvas, 'The picture must be visible before enabling the overlay')
    overlay.settings['video_danmaku_opacity'] = 80
    glyph = QPixmap(24, 24)
    glyph.fill(Qt.white)
    layer = overlay._quick_layer
    speed = 1. if layer is not None else 0.
    comment = _Comment(glyph, 200., 1, speed, 'fixture', QColor(Qt.white), [])
    overlay.set_active(True)
    overlay.set_enabled(True)
    overlay.comments.append(comment)
    if layer is not None:
        y = comment.lane * overlay.lane_height()
        comment.render_id = layer.add(overlay._quick_group, glyph.toImage(), comment.x, y, 24, 24, speed)
        overlay._set_quick_active()
    overlay.update()
    viewer.picture.raise_overlays()
    QTest.qWait(150)
    assert layer.window.isExposed() if layer is not None else overlay._alpha_uploaded
    assert_blue(viewer.canvas, 'Rolling danmaku must preserve the picture beneath it')
    point = wait_for_pixels(assert_comment, overlay)
    assert overlay.comment_at(point) is comment
    overlay._set_hover(comment)
    assert comment.held
    overlay._set_hover(None)
    assert not comment.held

    comment.x += 75
    if layer is not None:
        layer.move(comment.render_id, comment.x, y, speed)
    overlay.update()
    wait_for_pixels(assert_blue, overlay, 'Moving the comment must clear its old pixels', point)
    point = wait_for_pixels(assert_comment, overlay)
    overlay.clear_comments()
    wait_for_pixels(assert_blue, overlay, 'Clearing the overlay must clear the current pixels', point)
    overlay.set_enabled(False)
    assert not overlay.isVisible()
    overlay.set_enabled(True)
    assert overlay.isVisible() and not overlay.comments
    overlay.set_enabled(False)


def check_monitor(viewer, decoder, host_path, python):
    from PySide6.QtTest import QTest

    script = Path(__file__).with_name('check_match_monitor.py')
    process = subprocess.Popen([str(python), str(script), '--host', str(host_path), '--pid',
                                str(os.getpid()), '--check-controls'], creationflags=subprocess.CREATE_NO_WINDOW)
    start = time.monotonic()
    try:
        while process.poll() is None and time.monotonic() - start < 25:
            viewer.d3d11_picture.render(decoder, time.monotonic() - start, 1.)
            QTest.qWait(10)
        assert process.poll() == 0, 'MCP control client failed or timed out'
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def check_device_clock_target():
    from math import isclose
    from plugins.match_sync.d3d11_picture import PlaybackTarget
    from plugins.match_sync.engine import RATE

    half = RATE // 10
    # Queue 100 ms at normal speed followed by 100 ms at half speed.
    segments = ((0, half, 0., 1.), (half, 2 * half, .1, .5))
    initial = (4., 0, 2 * half, 100., segments)
    audio = SimpleNamespace(clock_observation=initial, correction=.125)
    target = PlaybackTarget(None, 1, 120., 4.02, 1., audio, initial, .125)
    assert isclose(target.sample(4.05), 120.03)
    assert isclose(target.sample(5.), 120.08)
    audio.clock_observation = (4.18, int(.16 * RATE), RATE, 100., ())
    assert isclose(target.sample(4.18), 120.11)
    audio.clock_observation = (4.6, RATE // 2, RATE, 100., ())
    assert isclose(target.sample(4.6), 120.13), 'Never advance beyond the originally queued audio'
    audio.correction += 1
    assert isclose(target.sample(4.6), 120.08)
    audio.correction = .125
    audio.clock_observation = (4.6, RATE // 2, RATE, 101., ())
    assert isclose(target.sample(4.6), 120.08)
    audio.clock_observation = None
    assert isclose(target.sample(4.6), 120.08)


def check(app, host, native_dir, visible_alpha, quick=False, monitor_python=None, host_path=None):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    from PySide6.QtTest import QTest
    from ddm import plugins
    from plugins.match_sync.panels import MatchSettingsDialog
    from plugins.match_sync.viewer import Viewer

    check_device_clock_target()
    host.settings, host.rooms = {}, []
    image = QImage(320, 180, QImage.Format_ARGB32)
    image.fill(Qt.blue)
    start = time.perf_counter()

    class Decoder:
        def __init__(self):
            self.history = SimpleNamespace(frame_at=self.picture_at)
            self.images = []

        def picture_at(self, stamp):
            self.images.append(stamp)
            return int(stamp * 60) / 60, image

    decoder = Decoder()
    flags = dict(DDM_MATCH_MONITOR='1' if monitor_python else '0')
    if native_dir is not None:
        flags.update(DDM_MATCH_D3D11_DIR=str(native_dir), DDM_MATCH_QUICK_DANMAKU='1' if quick else '0')
    with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, flags):
        if native_dir is None:
            os.environ.pop('DDM_MATCH_D3D11_DIR', None)
            os.environ.pop('DDM_MATCH_QUICK_DANMAKU', None)
        records = []
        if not quick:
            from ddm.danmaku_diagnostics import MotionTrace
            trace = MotionTrace(Path(directory) / 'motion.jsonl', emit=lambda stage, **row: records.append((stage, row)))
            trace.install_raster()
        manager = plugins.PluginManager(window=host, plugins_dir=directory)
        viewer = Viewer(plugins.PluginContext(manager, 'match_sync'), {})
        assert hasattr(viewer, 'monitor_bridge') == bool(monitor_python)
        viewer.setAttribute(Qt.WA_ShowWithoutActivating)
        viewer.setWindowFlag(Qt.WindowDoesNotAcceptFocus, True)
        if visible_alpha:
            viewer.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        if visible_alpha:
            viewer.move(65, 80)
        else:
            viewer.move(-14000, -14000)
        viewer.resize(1200, 830)
        viewer.show()
        app.processEvents()
        gpu = viewer.d3d11_picture
        assert gpu is not None and not gpu.requested
        overlay = viewer.video_danmaku
        quick_layer = overlay._quick_layer
        if quick:
            assert quick_layer is not None and overlay._quick_requested
            assert not overlay.native_video and not getattr(overlay, 'native_alpha', False)
        else:
            assert overlay.native_video and not overlay._quick_requested and overlay.native_alpha
        if hasattr(overlay, '_motion_interval'):
            assert overlay._motion_interval() == (50 if quick else 16)
        viewer.match_settings = MatchSettingsDialog(viewer)
        renderer = viewer.match_settings.picture_renderer
        raster_index, d3d11_index = renderer.findData(False), renderer.findData(True)
        assert '减轻卡顿' in renderer.itemText(d3d11_index)
        assert '长间隔' in renderer.toolTip()
        if native_dir is None:
            assert gpu.directory.name == 'd3d11'
            assert gpu.directory.parent.name == 'native'
        assert raster_index != d3d11_index
        assert renderer.isEnabled() and renderer.currentIndex() == raster_index
        renderer.setCurrentIndex(d3d11_index)
        assert viewer.context.setting('main_picture_renderer') == 'd3d11'
        reopened = Viewer(plugins.PluginContext(manager, 'match_sync'), {})
        try:
            assert reopened.d3d11_picture.requested
            assert MatchSettingsDialog(reopened).picture_renderer.currentData() is True
        finally:
            reopened.close()
            reopened.deleteLater()
            app.processEvents()
        gpu.set_requested(False)
        assert viewer.context.setting('main_picture_renderer') == 'd3d11', 'A diagnostic change must not replace the preference'
        renderer.setCurrentIndex(d3d11_index)
        renderer.setCurrentIndex(raster_index)
        assert viewer.context.setting('main_picture_renderer') == 'raster'
        reopened = Viewer(plugins.PluginContext(manager, 'match_sync'), {})
        try:
            assert not reopened.d3d11_picture.requested
        finally:
            reopened.close()
            reopened.deleteLater()
            app.processEvents()
        if monitor_python:
            viewer.add_room('1')
        alignment = viewer.alignment
        alignment.reference = '1'
        alignment.automatic = False
        alignment.manual_locked = True
        alignment.lags = {'1': 0., 'second': .125, 'third': -.25}
        delays = {'1': 0., 'second': .57, 'third': 1.31}
        shifts = alignment.shifts(delays)
        viewer.stable_reference_shift = shifts['1']
        generation = viewer.generation
        try:
            if monitor_python:
                check_monitor(viewer, decoder, host_path, monitor_python)
                assert not gpu.requested and not viewer.canvas.native_presenting
                assert viewer.picture.danmaku_button.isChecked()
                # Opening and closing comparison each invalidate its presentation generation.
                assert viewer.generation == generation + 2 and alignment.shifts(delays) == shifts
                generation = viewer.generation
            gpu.render(decoder, 1., 1.)
            assert viewer.canvas.frame_key == 1. and not viewer.canvas.native_presenting
            if visible_alpha:
                QTest.qWait(200)
                check_visible_overlay(viewer)
                old_size = viewer.video_danmaku.size()
                viewer.resize(1250, 860)
                gpu.render(decoder, 1.5, 1.)
                QTest.qWait(100)
                assert viewer.video_danmaku.size() != old_size
                check_visible_overlay(viewer)
            renderer.setCurrentIndex(d3d11_index)
            assert gpu.requested
            deadline = time.monotonic() + 7
            while time.monotonic() < deadline:
                gpu.render(decoder, 10 + time.perf_counter() - start, 1.)
                if gpu.diagnostics().get('presented', 0) >= 3:
                    break
                QTest.qWait(10)
            assert gpu.requested, 'D3D11 trial fell back before receiving frames'
            assert viewer.canvas.native_presenting and gpu.diagnostics()['backend'] == 'd3d11'
            assert gpu.diagnostics()['presented'] >= 3
            child = gpu.hwnd
            user = ctypes.WinDLL('user32')
            user.IsWindowVisible.argtypes = (wintypes.HWND,)
            user.IsWindowVisible.restype = wintypes.BOOL
            assert user.IsWindowVisible(child)
            gpu.user.ShowWindowAsync(child, 0)
            deadline = time.monotonic() + 1
            while user.IsWindowVisible(child) and time.monotonic() < deadline:
                QTest.qWait(10)
            assert not user.IsWindowVisible(child)
            previous_presented = gpu.diagnostics()['presented']
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                gpu.render(decoder, 10 + time.perf_counter() - start, 1.)
                if (gpu.hwnd == child and user.IsWindowVisible(child) and viewer.canvas.native_presenting
                        and gpu.diagnostics()['presented'] > previous_presented):
                    break
                QTest.qWait(10)
            else:
                raise AssertionError('A hidden video child must reappear even when its geometry did not change')
            # Hold the GUI clock beyond the worker's extrapolation window, then resume.
            time.sleep(.32)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                gpu.render(decoder, 10 + time.perf_counter() - start, 1.)
                gaps = gpu.diagnostics().get('publish_gaps', [])
                if any(g['duplicates'] and g['target_age_max_ms'] >= 80 and g['gap_ms'] > 50
                       and g['wait_max_ms'] > 0 for g in gaps):
                    break
                QTest.qWait(10)
            else:
                raise AssertionError('A paused target must be observable in the actual worker')
            from plugins.match_sync.engine import RATE
            now = time.perf_counter()
            stamp = 10 + now - start
            viewer.audio.clock_observation = (now, 0, RATE // 5, 200., ())
            try:
                gpu.render(decoder, stamp, 1.)
                time.sleep(.14)
                frozen = gpu.worker.latest.frame[0]
                assert frozen <= stamp + .081
                # Only the audio device advances. No GUI target is sent to the worker.
                viewer.audio.clock_observation = (time.perf_counter(), int(.16 * RATE), RATE, 200., ())
                deadline = time.perf_counter() + 1
                while gpu.worker.latest.frame[0] < stamp + .12 and time.perf_counter() < deadline:
                    time.sleep(.01)
                assert stamp + .12 <= gpu.worker.latest.frame[0] <= stamp + .201
            finally:
                viewer.audio.clock_observation = None
            assert overlay._quick_layer is quick_layer
            assert gpu.user.GetParent(gpu.hwnd) == int(viewer.picture.video.winId())
            assert viewer.canvas.frame is not None, 'Retain the snapshot for comparison and crop UI'
            assert alignment.shifts(delays) == shifts and viewer.generation == generation
            if visible_alpha:
                gpu.render(decoder, 10 + time.perf_counter() - start, 1.)
                QTest.qWait(100)
                check_visible_overlay(viewer)
            if visible_alpha and not quick:
                viewer.video_danmaku._layered_paint_trace.flush()
                uploads = [row for stage, row in records if stage == 'layered-cadence' and row['uploads']]
                assert uploads and all(not row['fallbacks'] for row in uploads)
                assert len({size for row in uploads for size in row['sizes']}) >= 2
                stages = {'queue_ms', 'image_prepare_ms', 'draw_ms', 'dib_prepare_ms', 'upload_ms', 'refresh_gap_ms'}
                if getattr(viewer.video_danmaku, '_layered_draw_into_bitmap', False):
                    assert all(row['direct_bitmap_draws'] == row['uploads'] for row in uploads)
                    assert all('copy_ms' not in row for row in uploads)
                else:
                    stages.add('copy_ms')
                assert any(stages <= row.keys() for row in uploads)
            hwnd = gpu.hwnd
            viewer.replay.delay = 1.
            assert gpu.render(decoder, 2., 1.)[0] == 2.
            assert not viewer.canvas.native_presenting and gpu.diagnostics()['backend'] == 'raster'
            assert renderer.currentIndex() == d3d11_index, 'Temporary replay fallback must preserve the selection'
            viewer.replay.live()
            QTest.qWait(25)
            user = ctypes.WinDLL('user32')
            user.IsWindowVisible.argtypes = (wintypes.HWND,)
            assert not user.IsWindowVisible(hwnd), 'Hide the child while the raster fallback is in use'
            worker = gpu.worker
            renderer.setCurrentIndex(raster_index)
            assert not gpu.requested and gpu.render(decoder, 3., 1.)[0] == 3.
            worker.thread.join(3)
            assert not worker.thread.is_alive(), 'Close the frame mailbox and its child process'
            gpu.shortcut.activated.emit()
            assert gpu.requested and renderer.currentIndex() == d3d11_index
            assert viewer.context.setting('main_picture_renderer') == 'd3d11'
            gpu.render(decoder, 3., 1.)
            worker = gpu.worker
            worker.error = 'fixture presenter failure'
            assert gpu.render(decoder, 4., 1.)[0] == 4.
            assert not gpu.requested and renderer.currentIndex() == raster_index
            assert renderer.isEnabled(), 'A presenter failure must allow retrying D3D11'
            assert viewer.context.setting('main_picture_renderer') == 'd3d11'
            worker.thread.join(3)
            assert not worker.thread.is_alive()
            renderer.setCurrentIndex(d3d11_index)
            if quick:
                viewer.quick_danmaku.fallback()
                assert overlay._quick_layer is None and not overlay._quick_requested
            else:
                viewer.video_danmaku.nativeAlphaFailed.emit()
            assert not gpu.requested and renderer.currentIndex() == raster_index
            assert not renderer.isEnabled() and not gpu.shortcut.isEnabled()
            assert alignment.manual_locked and alignment.reference == '1'
            assert alignment.shifts(delays) == shifts and viewer.stable_reference_shift == shifts['1']
            assert viewer.generation == generation
            return dict(gpu_presented=True, embedded_child=True, raster_replay=True, worker_closed=True,
                        isolated_source=True, transparent_on_both_paths=visible_alpha,
                        shared_quick_overlay=quick,
                        settings_hot_switch=True, shortcut_synced=True, fallback_synced=True,
                        alignment_preserved=True)
        finally:
            gpu.stop()
            viewer.close()
            viewer.deleteLater()
            app.processEvents()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    parser.add_argument('--native-dir', type=Path, help='Override the bundled binaries for development')
    parser.add_argument('--check-visible-alpha', action='store_true')
    parser.add_argument('--quick-overlay', action='store_true')
    parser.add_argument('--monitor-python', type=Path, help='MCP SDK interpreter for a real stdio control client')
    args = parser.parse_args()
    if args.native_dir is None:
        args.quick_overlay = True
    if args.monitor_python and not args.quick_overlay:
        parser.error('--monitor-python requires --quick-overlay')
    sys.path[:0] = [str(args.host.resolve()), str(Path(__file__).resolve().parents[1])]
    os.environ['DDM_NO_SAVE'] = '1'
    from PySide6.QtWidgets import QApplication, QWidget
    app = QApplication([])
    print(check(app, QWidget(), args.native_dir.resolve() if args.native_dir else None, args.check_visible_alpha, args.quick_overlay,
                args.monitor_python, args.host.resolve()))


if __name__ == '__main__':
    main()
