from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys

from .workflow import ResearchDomainError


CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def copy_text(text: str) -> None:
    """Copy text through Win32 without ever reading or monitoring the clipboard."""
    if sys.platform != "win32":
        raise ResearchDomainError("CLIPBOARD_UNAVAILABLE", "Windows clipboard is unavailable")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    payload = (text + "\0").encode("utf-16-le")
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(payload))
    if not handle:
        raise ResearchDomainError("CLIPBOARD_UNAVAILABLE", "Windows clipboard is unavailable")
    transferred = False
    try:
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            raise ResearchDomainError("CLIPBOARD_UNAVAILABLE", "Windows clipboard is unavailable")
        try:
            ctypes.memmove(pointer, payload, len(payload))
        finally:
            kernel32.GlobalUnlock(handle)
        if not user32.OpenClipboard(None):
            raise ResearchDomainError("CLIPBOARD_UNAVAILABLE", "Windows clipboard is busy", retryable=True)
        try:
            if not user32.EmptyClipboard() or not user32.SetClipboardData(CF_UNICODETEXT, handle):
                raise ResearchDomainError("CLIPBOARD_UNAVAILABLE", "Windows clipboard write failed", retryable=True)
            transferred = True
        finally:
            user32.CloseClipboard()
    finally:
        if not transferred:
            kernel32.GlobalFree(handle)
