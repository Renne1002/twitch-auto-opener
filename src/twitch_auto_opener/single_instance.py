from __future__ import annotations

import ctypes
from ctypes import wintypes


class SingleInstanceError(RuntimeError):
    """Raised when the app is already running."""


ERROR_ALREADY_EXISTS = 183


class SingleInstance:
    def __init__(self, mutex_name: str) -> None:
        self._mutex_name = mutex_name
        self._handle: int | None = None

    def acquire(self) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        kernel32.CreateMutexW.restype = wintypes.HANDLE

        handle = kernel32.CreateMutexW(None, False, self._mutex_name)
        if not handle:
            raise RuntimeError("CreateMutexW failed")

        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            raise SingleInstanceError("another instance is already running")

        self._handle = int(handle)

    def release(self) -> None:
        if self._handle is None:
            return

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle(wintypes.HANDLE(self._handle))
        self._handle = None
