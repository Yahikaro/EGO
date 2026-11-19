"""Native user-scope DPAPI only; no machine scope or cross-platform substitute."""
import ctypes
from ctypes import wintypes
import os

from server.ego_core.vault.crypto_restore.storage import StorageFailure


def _call(data, decrypt):
    if os.name != 'nt':
        raise StorageFailure('WINDOWS_DPAPI_REQUIRED')
    if not isinstance(data, bytes) or not data:
        raise StorageFailure('INVALID_DPAPI_INPUT')

    class Blob(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]

    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    incoming, outgoing = Blob(len(data), buffer), Blob()
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    # The second parameter is NULL for both (no description input or output).
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    try:
        ok = function(ctypes.byref(incoming), None, None, None, None,
                      1, ctypes.byref(outgoing))  # UI_FORBIDDEN only, never LOCAL_MACHINE
        if not ok:
            raise StorageFailure('DPAPI_OPERATION_FAILED')
        return ctypes.string_at(outgoing.pbData, outgoing.cbData)
    finally:
        ctypes.memset(buffer, 0, len(data))
        if outgoing.pbData:
            ctypes.memset(outgoing.pbData, 0, outgoing.cbData)
            kernel.LocalFree(ctypes.cast(outgoing.pbData, ctypes.c_void_p))


def protect(data):
    return _call(data, False)


def unprotect(data):
    return _call(data, True)
