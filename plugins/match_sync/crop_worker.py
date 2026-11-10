"""Crop-only QApplication. Receives one snapshot, never opens a live stream."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time


def run():
    from .crop_protocol import PREFIX

    def send(message):
        print(PREFIX+json.dumps(message, ensure_ascii=False), flush=True)

    send(dict(kind='spawned', pid=os.getpid()))
    from PySide6.QtCore import QObject, QSharedMemory, QTimer, Signal
    from PySide6.QtGui import QFont, QImage
    from PySide6.QtWidgets import QApplication, QDialog
    from ddm import config, theme
    from .crop_ui import CropPicker

    def load_image(metadata):
        shared = QSharedMemory(metadata['key'])
        if not shared.attach(QSharedMemory.ReadOnly):
            raise RuntimeError('Cannot attach crop snapshot')
        try:
            size = metadata['stride']*metadata['height']
            if size != metadata['size'] or size > shared.size():
                raise ValueError('Invalid crop snapshot dimensions')
            image = QImage(shared.constData(), metadata['width'], metadata['height'],
                           metadata['stride'], QImage.Format(metadata['format'])).copy()
            if image.isNull():
                raise ValueError('Invalid crop snapshot image')
        finally:
            shared.detach()
        send(dict(kind='loaded'))
        return (0, image)

    class Worker(QObject):
        request = Signal(dict)

        def __init__(self):
            super().__init__()
            self.dialog = None
            self.request.connect(self.handle)

        def handle(self, message):
            try:
                kind = message['kind']
                if kind == 'quit':
                    app.quit()
                elif kind == 'open' and self.dialog is None:
                    snapshot = message['snapshot']
                    theme.apply(snapshot['theme'])
                    app.setStyleSheet(snapshot['stylesheet'])
                    if snapshot.get('font'):
                        font = QFont()
                        font.fromString(snapshot['font'])
                        app.setFont(font)
                    dialog = CropPicker(None, snapshot, native_caption=True)
                    self.dialog = dialog
                    dialog.caption_started.connect(lambda: send(dict(kind='gesture', phase='drag-start', time=time.time())))
                    dialog.caption_finished.connect(lambda: send(dict(kind='gesture', phase='drag-end', time=time.time())))
                    dialog.finished.connect(self.finished)
                    if message.get('image'):
                        dialog.set_frame(load_image(message['image']))
                    dialog.move(*snapshot['position'])
                    dialog.show()
                    dialog.raise_()
                    dialog.activateWindow()
                    send(dict(kind='shown', pid=os.getpid()))
                elif kind == 'frame' and self.dialog is not None:
                    self.dialog.set_frame(load_image(message['image']))
                elif kind == 'focus' and self.dialog is not None:
                    self.dialog.showNormal()
                    self.dialog.raise_()
                    self.dialog.activateWindow()
            except (ValueError, KeyError, RuntimeError, TypeError):
                send(dict(kind='error'))
                # The host cancels the request and closes stdin on an error.

        def finished(self, result):
            dialog, self.dialog = self.dialog, None
            rect = dialog.crop
            send(dict(kind='result', accepted=result == QDialog.Accepted,
                      crop=[rect.x(), rect.y(), rect.width(), rect.height()]))
            # The host closes stdin after receiving the result. Let its EOF
            # stop the reader before shutting down QApplication/Python.

    with tempfile.TemporaryDirectory(prefix='ddm_crop_') as temporary:
        config.REPO = config.DATA_ROOT = temporary
        config.CONFIG_PATH = str(Path(temporary)/'config.json')
        os.environ['DDM_NO_SAVE'] = '1'
        app = QApplication([])
        app.setQuitOnLastWindowClosed(False)
        from ddm.localization import install_chinese_translations
        install_chinese_translations(app)
        worker = Worker()

        def read_requests():
            for line in sys.stdin:
                try:
                    worker.request.emit(json.loads(line))
                except ValueError:
                    worker.request.emit(dict(kind='quit'))
                    return
            worker.request.emit(dict(kind='quit'))

        threading.Thread(target=read_requests, name='crop-input', daemon=True).start()
        QTimer.singleShot(0, lambda: send(dict(kind='ready', pid=os.getpid())))
        result = app.exec()
        app.shutdown()
        return result
