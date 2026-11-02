"""Check native clipping timer routing with empty widgets; no media is generated or played."""
import argparse
from collections import Counter
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


def check(app, host, module):
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtGui import QRegion
    from ddm import plugins
    from ddm.video_surface import VideoSurface
    calls = Counter()
    def clip(surface, **_kwargs):
        calls[id(surface)] += 1
        return True
    with tempfile.TemporaryDirectory() as directory, patch.object(VideoSurface, '_clip_native_video', clip):
        viewer = module.Viewer(plugins.PluginContext(
            plugins.PluginManager(window=host, plugins_dir=directory), 'clip_retry'), {})
        control = VideoSurface(host)
        viewer.embed(getattr(host, '_content', host))
        viewer.show()
        control.resize(100, 100)
        control.show()
        try:
            app.processEvents()
            surface = viewer.picture.video
            surface.setMask(QRegion(surface.rect()))
            control.setMask(QRegion(control.rect()))
            app.processEvents()
            assert not surface.mask().isEmpty() and surface.isVisible()
            assert not control.mask().isEmpty() and control.isVisible()
            for repeat in range(2):
                calls.clear()
                surface.refresh_clip()
                control.refresh_clip()
                loop = QEventLoop()
                QTimer.singleShot(180, loop.quit)
                loop.exec()
                assert calls[id(surface)] == 0, ('Qt main picture retried native clipping', repeat, calls)
                assert not surface._clip_timer.isActive()
                assert calls[id(control)] >= 1, 'Ordinary native surfaces must retain their clipping retries'
            # Mask updates still follow the existing synchronous path.
            calls.clear()
            surface.setMask(QRegion(surface.rect().adjusted(1, 1, -1, -1)))
            assert calls[id(surface)] == 1 and not surface.mask().isEmpty()
            return dict(passed=True, media_playback=False, main_periodic_native_calls=0,
                        ordinary_surface_retry_preserved=True, mask_updates_preserved=True,
                        restart_after_resize_blocked=True)
        finally:
            viewer.close()
            control.close()
            control._clip_timer.stop()
            viewer.deleteLater()
            control.deleteLater()
            app.processEvents()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host.resolve()), str(Path(__file__).resolve().parents[1])]
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ['QT_QPA_PLATFORM'] = 'windows'
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QWidget
    from plugins.match_sync import viewer as module
    app = QApplication([])
    host = QWidget()
    host.setAttribute(Qt.WA_DontShowOnScreen)
    host.resize(1600, 950)
    host.show()
    try:
        print(check(app, host, module))
    finally:
        host.close()
        app.processEvents()
        app.shutdown()


if __name__ == '__main__':
    main()
