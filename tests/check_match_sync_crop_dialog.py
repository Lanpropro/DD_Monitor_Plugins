"""Modeless crop acceptance, cancellation and source lifetime; offline regression."""
import argparse
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch


def check(app, host, module):
    from PySide6.QtCore import QCoreApplication, QEvent, QObject, QRectF, Qt, QTimer
    from PySide6.QtGui import QImage
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QWidget
    from shiboken6 import isValid
    from ddm import plugins

    class BlockingEvents(QObject):
        def __init__(self):
            super().__init__()
            self.events = []

        def eventFilter(self, watched, event):
            if event.type() in (QEvent.WindowBlocked, QEvent.WindowUnblocked):
                self.events.append(event.type().name)
            return False

    host.settings, host.rooms = {}, []
    host.setAttribute(Qt.WA_DontShowOnScreen)
    host.show()
    observed = BlockingEvents()
    app.installEventFilter(observed)
    with tempfile.TemporaryDirectory() as directory:
        manager = plugins.PluginManager(window=host, plugins_dir=directory)
        viewer = module.Viewer(plugins.PluginContext(manager, 'crop_dialog_check'), {})
        viewer.setAttribute(Qt.WA_DontShowOnScreen)
        viewer.show()
        viewer.add_room('1')
        viewer.add_room('2')
        row = viewer.rows['1']
        image = QImage(320, 180, QImage.Format_RGB32)
        image.fill(Qt.red)
        viewer.canvas.set_frame((1., image))
        history = SimpleNamespace(previous=None)
        reads = []
        ready = [None]
        decoder = SimpleNamespace(history=history, stop=lambda: None,
                                  pictures=SimpleNamespace(get=lambda *a, **k: reads.append(a) or ready[0]))
        row.decoder = decoder
        app.processEvents()
        observed.events.clear()

        def open_crop(floating=False):
            with patch.object(QDialog, 'exec', side_effect=AssertionError('Crop must return without exec')):
                viewer.choose_crop(row, floating=floating)
            dialog = viewer.crop_dialog
            assert dialog is not None and dialog.isVisible()
            assert dialog.parentWidget() is viewer and dialog.windowModality() == Qt.NonModal
            assert not dialog.isModal() and QApplication.activeModalWidget() is None
            return dialog

        def release(dialog):
            assert viewer.crop_dialog is None
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            assert not isValid(dialog), 'Finished pickers must be released, not retained as hidden children'

        try:
            with patch.object(viewer, 'show_comparison') as show, patch.object(viewer, 'changed') as changed:
                dialog = open_crop()
                viewer.choose_crop(viewer.rows['2'], floating=True)
                assert viewer.crop_dialog is dialog, 'Repeated requests reuse the active picker'
                progressed = []
                QTimer.singleShot(0, lambda: progressed.append(True))
                app.processEvents()
                assert progressed, 'Application events continue while the picker is open'
                dialog.findChild(module.Canvas).crop = QRectF(.1, .2, .3, .4)
                dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()
                assert row.crop == (.1, .2, .3, .4)
                show.assert_called_once_with(row.room_id)
                changed.assert_called_once()
                release(dialog)
                for finish in ('escape', 'close'):
                    dialog = open_crop()
                    dialog.findChild(module.Canvas).crop = QRectF(0, 0, 1, 1)
                    if finish == 'escape':
                        QTest.keyClick(dialog, Qt.Key_Escape)
                    else:
                        dialog.close()
                    assert row.crop == (.1, .2, .3, .4)
                    release(dialog)

            with patch.object(viewer, 'show_floating_crop') as show:
                dialog = open_crop(floating=True)
                dialog.findChild(module.Canvas).crop = QRectF(.2, .3, .4, .5)
                dialog.accept()
                show.assert_called_once_with(row, (.2, .3, .4, .5), (1., image))
                release(dialog)

            for change in ('decoder', 'history'):
                dialog = open_crop()
                previous_crop = row.crop
                dialog.findChild(module.Canvas).crop = QRectF(0, 0, 1, 1)
                if change == 'decoder':
                    row.decoder = SimpleNamespace(history=history)
                else:
                    decoder.history = SimpleNamespace(previous=None)
                dialog.accept()
                assert row.crop == previous_crop, 'An old source must never apply a crop after replacement'
                release(dialog)
                row.decoder, decoder.history = decoder, history

            dialog = open_crop()
            viewer._stop_row(row)
            release(dialog)
            row.decoder = decoder
            dialog = open_crop()
            viewer.running = True
            history.previous = SimpleNamespace(frame_at=lambda stamp: (1., image))
            with patch.object(viewer, 'live_clock', return_value=1.), patch.object(viewer, 'shifts', return_value={'1': 0}):
                viewer._reset(row, decoder)
            release(dialog)
            viewer.running = False

            dialog = open_crop()
            viewer.select_main('2')
            release(dialog)
            viewer.select_main('1')
            viewer.canvas.set_frame((1., image))

            # A secondary source may need an exact picture asynchronously.
            viewer.select_main('2')
            history.frame_at = lambda stamp: (1., image)
            decoder.picture_at = lambda *a, **k: None
            with patch.object(viewer, 'live_clock', return_value=1.), patch.object(viewer, 'shifts', return_value={'1': 0}):
                dialog = open_crop()
            buttons = dialog.findChild(QDialogButtonBox)
            assert not buttons.button(QDialogButtonBox.Ok).isEnabled()
            prepare = dialog.findChild(QTimer)
            assert prepare.isActive()
            ready[0] = (1., image)
            prepare.timeout.emit()
            assert reads and buttons.button(QDialogButtonBox.Ok).isEnabled() and not prepare.isActive()
            dialog.reject()
            release(dialog)
            ready[0] = None
            with patch.object(viewer, 'live_clock', return_value=1.), patch.object(viewer, 'shifts', return_value={'1': 0}):
                dialog = open_crop()
            prepare = dialog.findChild(QTimer)
            read_count = len(reads)
            decoder.history = SimpleNamespace(previous=None)
            prepare.timeout.emit()
            assert len(reads) == read_count and not prepare.isActive(), 'Never poll a replacement history with an old target'
            release(dialog)
            decoder.history = history

            viewer.select_main('1')
            viewer.canvas.set_frame((1., image))
            dialog = open_crop()
            viewer.remove_room('1')
            release(dialog)
            row = viewer.rows['2']
            row.decoder = decoder
            viewer.canvas.set_frame((1., image))
            dialog = open_crop()
            viewer.stop()
            release(dialog)
            row.decoder = decoder
            viewer.canvas.set_frame((1., image))
            dialog = open_crop()
            viewer.reject()
            release(dialog)
            viewer.show()
            dialog = open_crop()
            viewer.close()
            release(dialog)
            app.processEvents()
            assert not observed.events, observed.events
            return dict(passed=True, modeless=True, nested_exec=False, blocked_events=0,
                        confirm_cancel=True, duplicate_reused=True, stale_source_rejected=True,
                        stop_remove_reset_main_close=True, frozen_prepare_cleanup=True,
                        real_messages_sent=0, performance_claim=False)
        finally:
            for current in viewer.rows.values():
                current.decoder = None
            viewer.close()
            viewer.deleteLater()
            app.removeEventFilter(observed)
            app.processEvents()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host), str(Path(__file__).resolve().parents[1])]
    from PySide6.QtWidgets import QApplication, QWidget
    from plugins.match_sync import viewer as module
    app = QApplication([])
    print(check(app, QWidget(), module))


if __name__ == '__main__':
    main()
