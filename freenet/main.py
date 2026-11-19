# -*- coding: utf-8 -*-
"""FREENET Desktop — обёртка над zapret-discord-youtube и tg-ws-proxy.

Точка входа. Запускает локальный API-сервер и окно (pywebview, если есть;
иначе — браузер по умолчанию).
"""
import os
import socket
import sys
import threading
import webbrowser

# добавляем корень пакета в sys.path (важно для PyInstaller)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.server import app, run_server, TOKEN  # noqa: E402
from core.settings import settings              # noqa: E402


def free_port() -> int:
    for p in (47908, 47909, 0):
        try:
            with socket.socket() as s:
                if p == 0:
                    s.bind(("127.0.0.1", 0))
                    return s.getsockname()[1]
                s.bind(("127.0.0.1", p))
                return p
        except OSError:
            continue
    raise SystemExit("Не удалось найти свободный порт")


def main():
    port = free_port()
    url = f"http://127.0.0.1:{port}/?t={TOKEN}"

    t = threading.Thread(target=run_server, args=(port,), daemon=True)
    t.start()

    # ждём готовности сервера
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                break
        except OSError:
            threading.Event().wait(0.1)

    minimized = "--minimized" in sys.argv

    try:
        import webview  # pywebview — нативное окно без лишних зависимостей
        webview.create_window(
            "FREENET", url, width=920, height=760,
            min_size=(560, 480), text_select=True)
        webview.start(private_mode=False, storage_path=os.path.expanduser("~/.freenet"))
    except ImportError:
        # фолбэк: обычный браузер
        print("pywebview не установлен — открываю браузер:", url)
        webbrowser.open(url)
        if not minimized:
            # держим процесс живым, пока пользователь не закроет его
            try:
                while True:
                    threading.Event().wait(3600)
            except KeyboardInterrupt:
                pass

    from core.process_mgr import manager
    if not settings.get("settings", {}).get("autoModules"):
        manager.stop_all()


if __name__ == "__main__":
    main()
