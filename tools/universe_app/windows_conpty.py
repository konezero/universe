"""Windows ConPTY backend for one interactive CLI process."""

from __future__ import annotations

import ctypes
import os
import subprocess
import threading
from ctypes import wintypes
from pathlib import Path
from typing import Any

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

HPCON = ctypes.c_void_p
PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
HANDLE_FLAG_INHERIT = 0x00000001
STARTF_USESTDHANDLES = 0x00000100
STILL_ACTIVE = 259


class COORD(ctypes.Structure):
    _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wintypes.BOOL),
    ]


kernel32.CreatePipe.argtypes = [
    ctypes.POINTER(wintypes.HANDLE),
    ctypes.POINTER(wintypes.HANDLE),
    ctypes.POINTER(SECURITY_ATTRIBUTES),
    wintypes.DWORD,
]
kernel32.CreatePipe.restype = wintypes.BOOL
kernel32.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
kernel32.SetHandleInformation.restype = wintypes.BOOL
kernel32.CreatePseudoConsole.argtypes = [
    COORD,
    wintypes.HANDLE,
    wintypes.HANDLE,
    wintypes.DWORD,
    ctypes.POINTER(HPCON),
]
kernel32.CreatePseudoConsole.restype = ctypes.c_long
kernel32.ResizePseudoConsole.argtypes = [HPCON, COORD]
kernel32.ResizePseudoConsole.restype = ctypes.c_long
kernel32.ClosePseudoConsole.argtypes = [HPCON]
kernel32.ClosePseudoConsole.restype = None
kernel32.InitializeProcThreadAttributeList.argtypes = [
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
kernel32.UpdateProcThreadAttribute.argtypes = [
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
kernel32.DeleteProcThreadAttributeList.restype = None
kernel32.CreateProcessW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.LPWSTR,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.BOOL,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.LPCWSTR,
    ctypes.POINTER(STARTUPINFOEXW),
    ctypes.POINTER(PROCESS_INFORMATION),
]
kernel32.CreateProcessW.restype = wintypes.BOOL
kernel32.ReadFile.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.c_void_p,
]
kernel32.ReadFile.restype = wintypes.BOOL
kernel32.WriteFile.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.c_void_p,
]
kernel32.WriteFile.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.TerminateProcess.restype = wintypes.BOOL
kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel32.GetExitCodeProcess.restype = wintypes.BOOL


class WindowsConPTY:
    def __init__(
        self,
        executable: str,
        cwd: str,
        cols: int,
        rows: int,
        argv: list[str] | None = None,
    ) -> None:
        self._closed = False
        self._lock = threading.Lock()
        self._output = bytearray()
        self._output_event = threading.Event()
        self._hpc = HPCON()
        self._stdin_write = wintypes.HANDLE()
        self._stdout_read = wintypes.HANDLE()
        self._process = PROCESS_INFORMATION()
        self._attr_buf = None
        stdin_read = wintypes.HANDLE()
        stdout_write = wintypes.HANDLE()
        sa = SECURITY_ATTRIBUTES()
        sa.nLength = ctypes.sizeof(SECURITY_ATTRIBUTES)
        sa.bInheritHandle = True
        if not kernel32.CreatePipe(ctypes.byref(stdin_read), ctypes.byref(self._stdin_write), ctypes.byref(sa), 0):
            raise OSError("CreatePipe stdin failed")
        if not kernel32.CreatePipe(ctypes.byref(self._stdout_read), ctypes.byref(stdout_write), ctypes.byref(sa), 0):
            raise OSError("CreatePipe stdout failed")
        kernel32.SetHandleInformation(self._stdin_write, HANDLE_FLAG_INHERIT, 0)
        kernel32.SetHandleInformation(self._stdout_read, HANDLE_FLAG_INHERIT, 0)
        size = COORD(int(cols), int(rows))
        status = kernel32.CreatePseudoConsole(
            size, stdin_read, stdout_write, 0, ctypes.byref(self._hpc)
        )
        kernel32.CloseHandle(stdin_read)
        kernel32.CloseHandle(stdout_write)
        if status != 0:
            raise OSError(f"CreatePseudoConsole failed: {status}")
        self._start_process(executable, cwd, list(argv or []))
        self._reader = threading.Thread(target=self._read_loop, name="conpty-read", daemon=True)
        self._reader.start()

    def _start_process(self, executable: str, cwd: str, argv: list[str]) -> None:
        size = ctypes.c_size_t(0)
        kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        self._attr_buf = ctypes.create_string_buffer(size.value)
        if not kernel32.InitializeProcThreadAttributeList(self._attr_buf, 1, 0, ctypes.byref(size)):
            raise OSError("InitializeProcThreadAttributeList failed")
        if not kernel32.UpdateProcThreadAttribute(
            self._attr_buf,
            0,
            ctypes.c_void_p(PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE),
            ctypes.byref(self._hpc),
            ctypes.sizeof(self._hpc),
            None,
            None,
        ):
            raise OSError("UpdateProcThreadAttribute failed")
        si = STARTUPINFOEXW()
        si.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
        si.lpAttributeList = ctypes.cast(self._attr_buf, ctypes.c_void_p)
        command = subprocess.list2cmdline([executable, *argv])
        command_buf = ctypes.create_unicode_buffer(command)
        if not kernel32.CreateProcessW(
            str(Path(executable)),
            command_buf,
            None,
            None,
            False,
            EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT,
            None,
            str(Path(cwd)),
            ctypes.byref(si),
            ctypes.byref(self._process),
        ):
            raise OSError(f"CreateProcessW failed: {ctypes.get_last_error()}")

    def write(self, data: bytes) -> None:
        if not data or self._closed:
            return
        written = wintypes.DWORD(0)
        payload = bytes(data)
        kernel32.WriteFile(self._stdin_write, payload, len(payload), ctypes.byref(written), None)

    def read(self, timeout: float = 0.2) -> bytes:
        if self._output_event.wait(timeout):
            with self._lock:
                chunk = bytes(self._output)
                self._output.clear()
                self._output_event.clear()
            return chunk
        return b""

    def resize(self, cols: int, rows: int) -> None:
        if self._closed or not self._hpc:
            return
        kernel32.ResizePseudoConsole(self._hpc, COORD(int(cols), int(rows)))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._process.hProcess:
            exit_code = wintypes.DWORD(0)
            if kernel32.GetExitCodeProcess(self._process.hProcess, ctypes.byref(exit_code)):
                if exit_code.value == STILL_ACTIVE:
                    kernel32.TerminateProcess(self._process.hProcess, 1)
            kernel32.CloseHandle(self._process.hProcess)
            self._process.hProcess = None
        if self._process.hThread:
            kernel32.CloseHandle(self._process.hThread)
            self._process.hThread = None
        if self._hpc:
            kernel32.ClosePseudoConsole(self._hpc)
            self._hpc = None
        if self._stdin_write:
            kernel32.CloseHandle(self._stdin_write)
            self._stdin_write = None
        if self._stdout_read:
            kernel32.CloseHandle(self._stdout_read)
            self._stdout_read = None
        if self._attr_buf is not None:
            kernel32.DeleteProcThreadAttributeList(self._attr_buf)
            self._attr_buf = None

    def _read_loop(self) -> None:
        buffer = ctypes.create_string_buffer(4096)
        read = wintypes.DWORD(0)
        while not self._closed and self._stdout_read:
            ok = kernel32.ReadFile(self._stdout_read, buffer, 4096, ctypes.byref(read), None)
            if not ok or read.value == 0:
                break
            with self._lock:
                self._output.extend(buffer.raw[: read.value])
                self._output_event.set()
        with self._lock:
            self._output_event.set()


def spawn_fallback_process(executable: str, cwd: str, cols: int, rows: int) -> Any:
    del cols, rows
    return subprocess.Popen(
        [executable],
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
