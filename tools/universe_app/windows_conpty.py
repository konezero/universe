"""Windows ConPTY backend for one interactive CLI process (pywinpty)."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path
from collections.abc import Mapping
from typing import Any


class WindowsConPTY:
    def __init__(
        self,
        executable: str,
        cwd: str,
        cols: int,
        rows: int,
        argv: "list[str] | str | None" = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        import winpty

        self._closed = False
        self._lock = threading.Lock()
        self._output = bytearray()
        self._output_event = threading.Event()
        c = max(80, int(cols))
        r = max(24, int(rows))
        self._pty = winpty.PTY(c, r)
        _STRIP_KEYS = (
            "CLAUDE_CODE_SESSION_ID",
            "CLAUDE_CODE_CHILD_SESSION",
            "CLAUDE_SESSION_ID",
            "CLAUDE_CONVERSATION_ID",
            "CODEX_THREAD_ID",
            "CODEX_SESSION_ID",
            "GROK_SESSION_ID",
            "XAI_SESSION_ID",
            "GROK_CONVERSATION_ID",
        )
        env = os.environ.copy()
        for _k in _STRIP_KEYS:
            env.pop(_k, None)
        for key, value in dict(environment or {}).items():
            env[str(key)] = str(value)
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"
        env_block = "\0".join(f"{k}={v}" for k, v in env.items()) + "\0"
        exe = str(Path(executable))
        kwargs: dict[str, Any] = {"cwd": str(Path(cwd)), "env": env_block}
        # A raw string is preserved verbatim: list2cmdline would escape its
        # quotes into \" and cmd would read the whole thing as a program name.
        line = _managed_cmdline(argv)
        if line.strip():
            kwargs["cmdline"] = " " + line
        # Also temporarily remove from os.environ in case the PTY backend
        # ignores the explicit env block and the child inherits the process env.
        _saved: dict[str, str] = {}
        for _k in _STRIP_KEYS:
            if _k in os.environ:
                _saved[_k] = os.environ.pop(_k)
        try:
            self._pty.spawn(exe, **kwargs)
        finally:
            os.environ.update(_saved)
        self._reader = threading.Thread(
            target=self._read_loop, name="conpty-read", daemon=True
        )
        self._reader.start()

    @property
    def pid(self) -> int | None:
        pty = getattr(self, "_pty", None)
        pid = getattr(pty, "pid", None)
        return int(pid) if isinstance(pid, int) and pid > 0 else None

    def is_alive(self) -> bool:
        if self._closed:
            return False
        pty = self._pty
        if pty is None:
            return False
        try:
            return bool(pty.isalive())
        except Exception:
            return False

    def write(self, data: bytes) -> None:
        if not data or self._closed:
            return
        try:
            self._pty.write(data.decode("utf-8", errors="replace"))
        except Exception:
            pass

    def read(self, timeout: float = 0.2) -> bytes:
        if self._output_event.wait(timeout):
            with self._lock:
                chunk = bytes(self._output)
                self._output.clear()
                self._output_event.clear()
            return chunk
        return b""

    def resize(self, cols: int, rows: int) -> None:
        if self._closed:
            return
        try:
            self._pty.set_size(max(80, int(cols)), max(24, int(rows)))
        except Exception:
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        pty = self._pty
        self._pty = None  # type: ignore[assignment]
        if pty is not None:
            try:
                if pty.isalive():
                    import signal

                    try:
                        os.kill(pty.pid, signal.SIGTERM)
                    except (OSError, ProcessLookupError):
                        pass
            except Exception:
                pass
        with self._lock:
            self._output_event.set()

    def _read_loop(self) -> None:
        while not self._closed:
            pty = self._pty
            if pty is None:
                break
            try:
                data = pty.read(blocking=False)
            except Exception:
                if self._closed:
                    break
                time.sleep(0.05)
                continue
            if data:
                encoded = data.encode("utf-8", errors="replace")
                with self._lock:
                    self._output.extend(encoded)
                    self._output_event.set()
            else:
                time.sleep(0.05)
        with self._lock:
            self._output_event.set()


def _managed_cmdline(argv: "list[str] | str | None") -> str:
    """Return the argument line for a spawn, preserving a raw string exactly."""

    if isinstance(argv, str):
        return argv
    return subprocess.list2cmdline(list(argv or []))


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
