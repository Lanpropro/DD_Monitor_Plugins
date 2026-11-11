"""Windows DPAPI storage for the optional translation API key."""
import base64
import ctypes
from ctypes import wintypes
import os


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def available():
    if os.name != "nt":
        return False
    try:
        return ctypes.windll.crypt32 is not None
    except (AttributeError, OSError):
        return False


def protect(value):
    value = str(value or "")
    if not value or not available():
        return ""
    data = value.encode("utf-8")
    source = ctypes.create_string_buffer(data, len(data))
    source_blob = _DataBlob(
        len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    target_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    crypt32.CryptProtectData.argtypes = (
        ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob),
        ctypes.c_void_p, ctypes.POINTER(_DataBlob), wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    )
    crypt32.CryptProtectData.restype = wintypes.BOOL
    try:
        if not crypt32.CryptProtectData(
                ctypes.byref(source_blob), "DD Monitor CE live translate",
                None, None, None, 0x1, ctypes.byref(target_blob)):
            return ""
        encrypted = ctypes.string_at(target_blob.pbData, target_blob.cbData)
        return base64.b64encode(encrypted).decode("ascii")
    except (OSError, ValueError, ctypes.ArgumentError):
        return ""
    finally:
        _free(target_blob)


def unprotect(token):
    token = str(token or "")
    if not token or not available():
        return ""
    try:
        encrypted = base64.b64decode(token, validate=True)
    except (ValueError, TypeError):
        return ""
    if not encrypted:
        return ""
    source = ctypes.create_string_buffer(encrypted, len(encrypted))
    source_blob = _DataBlob(
        len(encrypted), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    target_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    crypt32.CryptUnprotectData.argtypes = (
        ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.POINTER(_DataBlob),
        wintypes.DWORD, ctypes.POINTER(_DataBlob),
    )
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    try:
        if not crypt32.CryptUnprotectData(
                ctypes.byref(source_blob), None, None, None, None, 0x1,
                ctypes.byref(target_blob)):
            return ""
        plain = ctypes.string_at(target_blob.pbData, target_blob.cbData)
        return plain.decode("utf-8")
    except (OSError, UnicodeDecodeError, ValueError, ctypes.ArgumentError):
        return ""
    finally:
        _free(target_blob)


def _free(blob):
    if not blob.pbData:
        return
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
        kernel32.LocalFree.restype = ctypes.c_void_p
        kernel32.LocalFree(ctypes.cast(blob.pbData, ctypes.c_void_p))
    except (AttributeError, OSError):
        pass
