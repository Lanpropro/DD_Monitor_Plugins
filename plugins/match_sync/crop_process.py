"""One crop request, one child UI process and one immutable shared image."""
import json
import os
import sys
import time
import uuid

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QRectF, QSharedMemory, QTimer, Signal
from PySide6.QtWidgets import QApplication, QDialog
from PySide6.QtGui import QImage
from ddm import plugins as api
from .crop_ui import CropPicker
from .crop_protocol import PREFIX, valid_crop


def create_picker(viewer, snapshot):
    if not hasattr(api, 'worker_command'):
        return CropPicker(viewer, snapshot)
    return CropProcess(viewer, snapshot)


class CropProcess(QObject):
    finished = Signal(int)
    failed = Signal(str)
    ready = Signal()

    def __init__(self, owner, snapshot):
        # Retain the supervisor until its own process exits, even if a Viewer
        # is deleted immediately after stop. The Viewer owns the active request.
        super().__init__(QApplication.instance())
        self.snapshot = snapshot
        self.crop = QRectF(*snapshot['crop'])
        self.frame = None
        self.worker_pid = None
        self._worker_handle = None
        self.shared = None
        self.image_meta = None
        self.buffer = b''
        self.child_ready = self.done = self.started = False
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.readyReadStandardError.connect(self._stderr)
        self.process.finished.connect(self._exited)
        self.process.errorOccurred.connect(self._error)
        self.deadline = QTimer(self)
        self.deadline.setSingleShot(True)
        self.deadline.timeout.connect(self._timeout)
        self.kill_timer = QTimer(self)
        self.kill_timer.setSingleShot(True)
        self.kill_timer.timeout.connect(self._kill_worker)
        owner.destroyed.connect(self.reject)
        QApplication.instance().aboutToQuit.connect(self._kill_worker)

    def show(self):
        if self.started or self.done:
            return
        self.started = True
        try:
            program, arguments = api.worker_command(__file__, 'crop_worker')
        except (OSError, ValueError):
            self._fail('worker_entry_missing')
            return
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert('DDM_NO_SAVE', '1')
        environment.insert('PYTHONUTF8', '1')
        environment.insert('PYTHONUNBUFFERED', '1')
        self.process.setProcessEnvironment(environment)
        self.process.setProgram(program)
        self.process.setArguments(arguments)
        self.process.setWorkingDirectory(os.getcwd())
        self.process.start()
        self.deadline.start(10000)

    def set_frame(self, frame):
        self.frame = frame
        if frame is None or self.done:
            return
        image = frame[1] if isinstance(frame[1], QImage) else QImage.fromData(frame[1])
        shared = QSharedMemory('ddm-crop-' + uuid.uuid4().hex, self)
        size = image.sizeInBytes()
        if not shared.create(size):
            self._fail('snapshot_allocation_failed')
            return
        # Publish only after the immutable copy; the worker acknowledges its
        # own QImage copy before this process detaches the shared segment.
        shared.data()[:size] = image.constBits()
        self.shared = shared
        self.image_meta = dict(key=shared.key(), width=image.width(), height=image.height(),
                               stride=image.bytesPerLine(), size=size, format=image.format().value)
        if self.child_ready:
            self._send(dict(kind='frame', image=self.image_meta))

    def _send(self, message):
        self.process.write((json.dumps(message, ensure_ascii=False)+'\n').encode('utf-8'))

    def _trace(self, phase, **fields):
        if os.environ.get('DDM_MATCH_DIAGNOSTICS', '1') != '0':
            print('[框选进程] '+json.dumps(dict(time=time.time(), phase=phase,
                  pid=self.worker_pid, **fields)), file=sys.stderr, flush=True)

    def _read(self):
        self.buffer += bytes(self.process.readAllStandardOutput())
        while b'\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\n', 1)
            if not line.startswith(PREFIX.encode()):
                continue
            try:
                message = json.loads(line[len(PREFIX):])
            except (ValueError, UnicodeDecodeError):
                self._fail('invalid_worker_response')
                return
            kind = message.get('kind')
            if kind == 'spawned':
                self.worker_pid = message['pid']
                if sys.platform == 'win32' and self.process.state() != QProcess.NotRunning:
                    import ctypes
                    kernel = ctypes.windll.kernel32
                    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
                    kernel.OpenProcess.restype = ctypes.c_void_p
                    self._worker_handle = kernel.OpenProcess(0x0001 | 0x00100000, False, self.worker_pid)
            elif kind == 'ready' and not self.done:
                self.worker_pid = message['pid']
                self.child_ready = True
                self._send(dict(kind='open', snapshot=self.snapshot, image=self.image_meta))
                self._trace('ready')
            elif kind == 'loaded':
                self._release_image()
                self._trace('image-loaded')
            elif kind == 'shown' and not self.done:
                self.deadline.stop()
                self._trace('shown')
                self.ready.emit()
            elif kind == 'gesture':
                self._trace(message['phase'], child_time=message['time'])
            elif kind == 'result' and not self.done:
                crop = message.get('crop')
                if message.get('accepted') and valid_crop(crop):
                    self.crop = QRectF(*crop)
                    self._complete(QDialog.Accepted)
                else:
                    self._complete(QDialog.Rejected)
                self.process.closeWriteChannel()
                self.kill_timer.start(1000)
            elif kind == 'error':
                self._fail('worker_failed')

    def _release_image(self):
        if self.shared is not None:
            self.shared.detach()
            self.shared.deleteLater()
            self.shared = None

    def _complete(self, result):
        if self.done:
            return
        self.done = True
        self.deadline.stop()
        self._release_image()
        self.finished.emit(result)

    def reject(self, *_args):
        self._complete(QDialog.Rejected)
        if self.process.state() != QProcess.NotRunning:
            self._send(dict(kind='quit'))
            self.process.closeWriteChannel()
            self.kill_timer.start(1000)
        else:
            self.deleteLater()

    def raise_(self):
        self.activateWindow()

    def activateWindow(self):
        if self.child_ready and not self.done:
            if sys.platform == 'win32' and self.worker_pid:
                import ctypes
                ctypes.windll.user32.AllowSetForegroundWindow(self.worker_pid)
            self._send(dict(kind='focus'))

    def _timeout(self):
        self._fail('worker_start_timeout')

    def _kill_worker(self):
        # Windows venv launchers have a different PID. Hold the real child's
        # handle from its initial handshake so cancellation cannot orphan it.
        if self._worker_handle:
            import ctypes
            ctypes.windll.kernel32.TerminateProcess(ctypes.c_void_p(self._worker_handle), 1)
        self.process.kill()

    def _fail(self, reason):
        if not self.done:
            self.failed.emit(reason)
        self.reject()

    def _error(self, error):
        if error == QProcess.FailedToStart:
            self._fail('worker_start_failed')

    def _stderr(self):
        # Worker errors contain no stream addresses, credentials or frame data.
        text = bytes(self.process.readAllStandardError()).decode('utf-8', errors='replace')
        if text:
            print(text, file=sys.stderr, end='')

    def _exited(self, *_args):
        self._read()
        if not self.done:
            self.failed.emit('worker_exited')
            self._complete(QDialog.Rejected)
        self.kill_timer.stop()
        self._release_image()
        if self._worker_handle:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(self._worker_handle))
            self._worker_handle = None
        self._trace('exited')
        self.deleteLater()
