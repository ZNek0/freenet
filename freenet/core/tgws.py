# -*- coding: utf-8 -*-
"""Компонент tg-ws-proxy (Flowseal/tg-ws-proxy): установка, конфиг, запуск.

Запускаем TgWsProxy_windows*.exe с флагами из README репозитория:
  -listen :socks_port   SOCKS5-порт для Telegram
  -domain / -ip         адреса, трафик на которые оборачивается в WebSocket
  -wss                  использовать wss:// (443) вместо ws://
Порт по умолчанию берём 10808, если он занят — ищем свободный.
"""
import os
import shutil
import socket
import subprocess
import threading

from . import downloader
from .settings import data_dir, settings
from .process_mgr import manager, IS_WIN, CREATE_NO_WINDOW

REPO = "Flowseal/tg-ws-proxy"
COMP_DIR = os.path.join(data_dir(), "components", "tgws")
BIN_PATH = os.path.join(COMP_DIR, "TgWsProxy.exe")
VERSION_FILE = os.path.join(COMP_DIR, ".freenet_version")

DEFAULT_DOMAINS = [
    "telegram.org", "core.telegram.org", "api.telegram.org",
    "web.telegram.org", "t.me", "www.t.me",
    "149.154.167.51", "149.154.175.53", "52.162.51.51",
]


def installed_version() -> str:
    try:
        with open(VERSION_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _win_arch_asset(assets):
    """Выбор exe под текущую разрядность Windows."""
    prefer64 = downloader.pick_asset(assets, ["*windows*64*.exe", "*windows.exe"])
    if prefer64:
        return prefer64
    return downloader.pick_asset(assets, ["*windows*.exe"])


def install(progress=None, cancel=None) -> dict:
    rel = downloader.latest_release(REPO)
    ver = rel["tag"]
    if installed_version() == ver and os.path.exists(BIN_PATH):
        return {"version": ver}
    asset = _win_arch_asset(rel["assets"])
    if not asset:
        raise RuntimeError("В релизе нет Windows-исполняемого файла")
    os.makedirs(COMP_DIR, exist_ok=True)
    path = downloader.download(asset, COMP_DIR, progress=progress, cancel=cancel)
    if os.path.exists(BIN_PATH):
        try:
            os.remove(BIN_PATH)
        except OSError:  # процесс ещё жив
            pass
    shutil.move(path, BIN_PATH)
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        f.write(ver)
    return {"version": ver}


def free_port(preferred=10808) -> int:
    for p in (preferred, 10809, 12733, 8080, 8118, 1070):
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get_config() -> dict:
    cfg = settings.get("tgConfig") or {}
    return {
        "port": int(cfg.get("port") or free_port()),
        "domains": cfg.get("domains") or DEFAULT_DOMAINS,
        "wss": bool(cfg.get("wss", True)),
    }


def save_config(cfg: dict):
    settings.update({"tgConfig": cfg})


def start():
    """Запускает прокси; возвращает актуальный конфиг."""
    if not os.path.exists(BIN_PATH):
        raise RuntimeError("tg-ws-proxy не установлен")
    cfg = get_config()
    args = [BIN_PATH, "-listen", f"127.0.0.1:{cfg['port']}"]
    if cfg["wss"]:
        args.append("-wss")
    for d in cfg["domains"]:
        args += ["-ip", d] if _is_ip(d) else ["-domain", d]
    manager.tgws.start(args)
    return cfg


def _is_ip(s: str) -> bool:
    parts = s.split(".")
    return len(parts) == 4 and all(p.isdigit() and int(p) < 256 for p in parts)


def stop():
    manager.tgws.stop()


def is_running() -> bool:
    return manager.tgws.running()


def proxy_link() -> str:
    """Ссылка для добавления прокси в Telegram (MTProto-совместимый формат)."""
    cfg = get_config()
    secret = os.environ.get("FREENET_TG_SECRET", "")
    if not secret:
        import secrets as _s
        secret = _s.token_hex(16)
        settings.update({"tgSecret": secret})
    else:
        secret = settings.get("tgSecret")
    import base64
    tag = base64.urlsafe_b64encode(bytes.fromhex(secret)).decode().rstrip("=")
    return f"tg://proxy?server=127.0.0.1&port={cfg['port']}&secret={tag}"
