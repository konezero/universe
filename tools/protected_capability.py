"""Machine-protected storage for local graceful-stop capabilities.

Runtime stop capabilities are credentials, not process identity. They may be
held briefly in memory, but a durable Supervisor registry needs an encrypted
at-rest representation so a service restart can still request graceful
shutdown without persisting a bearer token in plaintext.
"""

from __future__ import annotations

import base64
import ctypes
import os
from typing import Any


class ProtectedCapabilityError(RuntimeError):
    """Raised when the Host cannot protect or recover a capability."""


_DPAPI_PREFIX = "dpapi:v1:"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_uint32),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _input_blob(raw: bytes) -> tuple[_DataBlob, Any]:
    if not raw:
        raise ProtectedCapabilityError("capability must not be empty")
    buffer = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    return _DataBlob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


def _protect_windows(raw: bytes) -> bytes:
    if os.name != "nt":
        raise ProtectedCapabilityError("DPAPI requires a Windows Host")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    source, _keepalive = _input_blob(raw)
    protected = _DataBlob()
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.c_wchar_p,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = ctypes.c_int
    if not crypt32.CryptProtectData(
        ctypes.byref(source),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(protected),
    ):
        raise ProtectedCapabilityError(
            f"CryptProtectData failed: {ctypes.get_last_error()}"
        )
    try:
        return ctypes.string_at(protected.pbData, protected.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(protected.pbData)


def _unprotect_windows(raw: bytes) -> bytes:
    if os.name != "nt":
        raise ProtectedCapabilityError("DPAPI requires a Windows Host")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    source, _keepalive = _input_blob(raw)
    clear = _DataBlob()
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(ctypes.c_wchar_p),
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = ctypes.c_int
    description = ctypes.c_wchar_p()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source),
        ctypes.byref(description),
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(clear),
    ):
        raise ProtectedCapabilityError(
            f"CryptUnprotectData failed: {ctypes.get_last_error()}"
        )
    try:
        return ctypes.string_at(clear.pbData, clear.cbData)
    finally:
        if description:
            ctypes.windll.kernel32.LocalFree(description)
        ctypes.windll.kernel32.LocalFree(clear.pbData)


def protect_capability(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProtectedCapabilityError("capability must be non-empty text")
    protected = _protect_windows(value.encode("utf-8"))
    return _DPAPI_PREFIX + base64.urlsafe_b64encode(protected).decode("ascii")


def unprotect_capability(value: str) -> str:
    if not isinstance(value, str) or not value.startswith(_DPAPI_PREFIX):
        raise ProtectedCapabilityError("protected capability format is invalid")
    encoded = value.removeprefix(_DPAPI_PREFIX)
    try:
        protected = base64.urlsafe_b64decode(encoded.encode("ascii"))
        clear = _unprotect_windows(protected)
        return clear.decode("utf-8")
    except (ValueError, UnicodeDecodeError, TypeError) as error:
        raise ProtectedCapabilityError("protected capability cannot be decoded") from error


def is_protected_capability(value: object) -> bool:
    return isinstance(value, str) and value.startswith(_DPAPI_PREFIX)
