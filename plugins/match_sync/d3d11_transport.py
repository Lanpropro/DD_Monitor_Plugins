"""Transfer BGRA frames to the local development presenter."""
from contextlib import ExitStack
import ctypes
from ctypes import wintypes
import mmap
import struct
import subprocess
import time
import uuid


HEADER = struct.Struct('<iiIIIII36x')
FRAME = struct.Struct('<Qq')
PRESENTED = 24


def window_api():
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.GetParent.argtypes = (wintypes.HWND,)
    user.GetParent.restype = wintypes.HWND
    user.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, wintypes.UINT)
    user.ShowWindowAsync.argtypes = (wintypes.HWND, ctypes.c_int)
    user.IsWindowVisible.argtypes = (wintypes.HWND,)
    user.IsWindowVisible.restype = wintypes.BOOL
    return user


class FrameTransport:
    def __init__(self, directory, parent, image):
        self.resources = ExitStack()
        self.sequence = 0
        self.slot = 2
        self.geometry = image.width(), image.height(), image.bytesPerLine()
        width, height, stride = self.geometry
        self.slot_bytes = FRAME.size + stride * height
        try:
            name = f'Local\\ddm-match-{uuid.uuid4().hex}'
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.CreateEventW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
            kernel.CreateEventW.restype = wintypes.HANDLE
            kernel.SetEvent.argtypes = kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel.PeekNamedPipe.argtypes = (wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                          ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p)
            self.wake = kernel.SetEvent
            event = kernel.CreateEventW(None, False, False, name + '-frame')
            if not event:
                raise ctypes.WinError(ctypes.get_last_error())
            self.event = event
            self.resources.callback(kernel.CloseHandle, event)
            self.map = self.resources.enter_context(
                mmap.mmap(-1, HEADER.size + 3 * self.slot_bytes, tagname=name))
            HEADER.pack_into(self.map, 0, 1, 0, width, height, stride, self.slot_bytes, 0)
            self.address = ctypes.addressof(ctypes.c_char.from_buffer(self.map))
            self.swap = ctypes.CDLL(str(directory / 'mailbox.dll')).swap_slot
            self.swap.argtypes = (ctypes.c_void_p, wintypes.LONG)
            self.swap.restype = wintypes.LONG
            self.process = self.resources.enter_context(subprocess.Popen(
                [str(directory / 'presenter.exe'), name, name + '-frame', '--parent', str(parent)],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW))
            self.resources.callback(self._stop_child)
            self.hwnd = self._ready(kernel)
            user = window_api()
            owner = wintypes.DWORD()
            user.GetWindowThreadProcessId(self.hwnd, ctypes.byref(owner))
            if owner.value != self.process.pid or user.GetParent(self.hwnd) != parent:
                raise RuntimeError('D3D11 child window ownership mismatch')
        except (OSError, ValueError, RuntimeError):
            self.resources.close()
            raise

    def _ready(self, kernel):
        import msvcrt
        handle = msvcrt.get_osfhandle(self.process.stdout.fileno())
        deadline = time.monotonic() + 4
        while self.process.poll() is None and time.monotonic() < deadline:
            pending = wintypes.DWORD()
            if kernel.PeekNamedPipe(handle, None, 0, None, ctypes.byref(pending), None) and pending.value:
                reply = self.process.stdout.readline().split()
                if len(reply) == 2 and reply[0] == b'ready':
                    return int(reply[1])
                break
            time.sleep(.01)
        raise RuntimeError('D3D11 presenter failed to initialize')

    def publish(self, timestamp, image):
        if self.process.poll() is not None:
            raise RuntimeError('D3D11 presenter exited')
        self.sequence += 1
        offset = HEADER.size + self.slot * self.slot_bytes
        FRAME.pack_into(self.map, offset, self.sequence, round(timestamp * 1_000_000))
        self.map[offset + FRAME.size:offset + self.slot_bytes] = image.constBits()
        self.slot = self.swap(self.address, self.slot | 4) & 3
        if not self.wake(self.event):
            raise ctypes.WinError(ctypes.get_last_error())

    def displayed(self):
        return struct.unpack_from('<I', self.map, PRESENTED)[0]

    def _stop_child(self):
        if self.process.poll() is None:
            self.swap(self.address + ctypes.sizeof(wintypes.LONG), 1)
            self.wake(self.event)
            try:
                self.process.wait(timeout=.5)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def close(self):
        self.resources.close()
