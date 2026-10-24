"""Tell the Windows shell about this playback window, without hiding the taskbar."""
import ctypes
import sys
from uuid import UUID


def mark_fullscreen(hwnd, fullscreen):
    if sys.platform != 'win32':
        return False
    from ctypes import wintypes
    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx.argtypes = (ctypes.c_void_p, wintypes.DWORD)
    ole32.CoCreateInstance.argtypes = (ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                                     ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
    initialized = ole32.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
    if initialized < 0 and initialized != -2147417850:  # RPC_E_CHANGED_MODE: use Qt's existing apartment.
        return False
    instance = ctypes.c_void_p()
    try:
        clsid = ctypes.create_string_buffer(UUID('56FDF344-FD6D-11D0-958A-006097C9A090').bytes_le)
        iid = ctypes.create_string_buffer(UUID('602D4995-B13A-429B-A66E-1935E44F4317').bytes_le)
        if ole32.CoCreateInstance(clsid, None, 1, iid, ctypes.byref(instance)) < 0:
            return False
        table = ctypes.cast(instance, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        initialize = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(table[3])  # ITaskbarList.HrInit
        mark = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.HWND, wintypes.BOOL)(table[8])
        release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(table[2])
        try:
            return initialize(instance) >= 0 and mark(instance, hwnd, bool(fullscreen)) >= 0
        finally:
            release(instance)
    finally:
        if initialized >= 0:
            ole32.CoUninitialize()
