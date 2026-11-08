"""Windows compatibility for Qt WinRT factories across recycled native players."""
import sys

_pinned = False


def retain_qt_ui_library():
    global _pinned
    if _pinned or sys.platform != 'win32':
        return
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel.GetModuleHandleW.restype = wintypes.HMODULE
    if not kernel.GetModuleHandleW('Windows.UI.dll'):
        return
    kernel.GetModuleHandleExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, ctypes.POINTER(wintypes.HMODULE)]
    kernel.GetModuleHandleExW.restype = wintypes.BOOL
    handle = wintypes.HMODULE()
    # Qt retains WinRT factory vtables after native video teardown can unload this DLL.
    # PIN keeps this already-loaded Windows library valid until process termination.
    if not kernel.GetModuleHandleExW(1, 'Windows.UI.dll', ctypes.byref(handle)):
        raise ctypes.WinError(ctypes.get_last_error())
    _pinned = True
