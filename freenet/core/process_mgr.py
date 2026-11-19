# -*- coding: utf-8 -*-
"""Управление процессами zapret (winws.exe / .bat) и tg-ws-proxy."""
import os
import subprocess
import sys
import threading
import time

IS_WIN = sys.platform.startswith("win")

CREATE_NO_WINDOW = 0x08000000 if IS_WIN else 0


def _pid_alive(pid: int) -> bool:
    if not pid:
        return False
    if IS_WIN:
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class ManagedProcess:
    """Один управляемый процесс с логом и корректной остановкой по дереву."""

    def __init__(self, key: str):
        self.key = key
        self.proc = None
        self.started_at = None
        self.log_lines = []
        self._lock = threading.Lock()
        self._reader = None

    # ---------- запуск ----------
    def start(self, cmd, cwd=None, env_extra=None):
        with self._lock:
            already = (self.proc is not None and self.proc.poll() is None)
            if already:
                return
            env = os.environ.copy()
            if env_extra:
                env.update(env_extra)
            kwargs = dict(cwd=cwd, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if IS_WIN:
                kwargs["creationflags"] = CREATE_NO_WINDOW
            if isinstance(cmd, str):
                self.proc = subprocess.Popen(cmd, shell=True, **kwargs)
            else:
                self.proc = subprocess.Popen(list(cmd), **kwargs)
            self.started_at = time.time()
            self.log_lines.clear()
            self._reader = threading.Thread(target=self._pump_log, daemon=True)
            self._reader.start()

    def _pump_log(self):
        try:
            for raw in iter(self.proc.stdout.readline, b""):
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                with self._lock:
                    self.log_lines.append(line)
                    if len(self.log_lines) > 400:
                        del self.log_lines[:100]
        except Exception:
            pass

    # ---------- остановка ----------
    def stop(self, timeout=8):
        with self._lock:
            p = self.proc
            self.proc = None
        if not p:
            return
        pid = p.pid
        try:
            if IS_WIN:
                # убиваем всё дерево (bat -> winws.exe)
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=timeout,
                               creationflags=CREATE_NO_WINDOW)
            else:
                p.terminate()
                try:
                    p.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    p.kill()
        except Exception:
            pass

    def running(self) -> bool:
        with self._lock:
            if self.proc is None:
                return False
            if self.proc.poll() is not None:
                return False
            return _pid_alive(self.proc.pid) or not IS_WIN

    def uptime(self) -> float:
        if not self.running() or not self.started_at:
            return 0.0
        return time.time() - self.started_at

    def tail(self, n=60):
        with self._lock:
            return list(self.log_lines[-n:])


class ProcessManager:
    def __init__(self):
        self.zapret = ManagedProcess("zapret")
        self.tgws = ManagedProcess("tgws")

    def get(self, key):
        return getattr(self, key)

    def status(self) -> dict:
        return {
            "zapret": {"running": self.zapret.running(),
                       "uptime": round(self.zapret.uptime())},
            "tg": {"running": self.tgws.running(),
                   "uptime": round(self.tgws.uptime())},
        }

    def stop_all(self):
        self.zapret.stop()
        self.tgws.stop()


manager = ProcessManager()
