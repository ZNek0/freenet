# -*- coding: utf-8 -*-
"""Хранилище настроек FREENET (JSON в %APPDATA%/FREENET)."""
import json
import os
import sys
import threading

DEFAULTS = {
    "selectedConfig": "auto",
    "gameMode": False,
    "gameProfile": "Универсальный",
    "settings": {
        "startup": False,          # автозапуск Windows
        "tray": True,              # сворачивать в трей
        "autoModules": False,      # запускать модули автоматически
        "autoUpdates": True,       # проверять обновления
    },
    "zapretEnabled": False,
    "tgEnabled": False,
    "lastUpdateCheck": None,
}


def ui_dir() -> str:
    """Каталог со статикой (учитывает PyInstaller --add-data и dev-режим)."""
    if getattr(sys, "frozen", False):
        return os.path.join(getattr(sys, "_MEIPASS", ""), "ui")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui")


def data_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "FREENET")
    os.makedirs(d, exist_ok=True)
    return d


class Settings:
    def __init__(self):
        self._lock = threading.Lock()
        self._path = os.path.join(data_dir(), "settings.json")
        self._data = dict(DEFAULTS)
        self.load()

    def load(self):
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                user = json.load(f)
            self._merge(self._data, user)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass

    def _merge(self, dst, src):
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(dst.get(k), dict):
                self._merge(dst[k], v)
            else:
                dst[k] = v

    def get(self, key, default=None):
        with self._lock:
            return self._data.get(key, default)

    def update(self, patch: dict):
        with self._lock:
            self._merge(self._data, patch)
            self._save_locked()

    def all(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._data))

    def _save_locked(self):
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._path)

    def save(self):
        with self._lock:
            self._save_locked()


settings = Settings()
