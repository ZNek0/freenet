# -*- coding: utf-8 -*-
"""Компонент zapret (Flowseal/zapret-discord-youtube): установка, конфиги, запуск."""
import os
import shutil
import subprocess
import sys

from . import downloader
from .settings import data_dir
from .process_mgr import manager, IS_WIN, CREATE_NO_WINDOW

REPO = "Flowseal/zapret-discord-youtube"
COMP_DIR = os.path.join(data_dir(), "components", "zapret")
VERSION_FILE = os.path.join(COMP_DIR, ".freenet_version")


def installed_version() -> str:
    try:
        with open(VERSION_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def root_dir() -> str:
    """Каталог с bat-файлами (поиск папки zapret-*)."""
    if not os.path.isdir(COMP_DIR):
        return ""
    for name in sorted(os.listdir(COMP_DIR)):
        p = os.path.join(COMP_DIR, name)
        if os.path.isdir(p) and any(f.lower().endswith(".bat") for f in os.listdir(p)):
            return p
    # вариант: bat лежат прямо в COMP_DIR
    if any(f.lower().endswith(".bat") for f in os.listdir(COMP_DIR)):
        return COMP_DIR
    return ""


def list_profiles() -> list:
    """Все доступные конфигурации (bat-профили) в виде имён без расширения."""
    root = root_dir()
    if not root:
        return []
    out = []
    for f in sorted(os.listdir(root)):
        low = f.lower()
        if not low.endswith(".bat"):
            continue
        # служебные скрипты не предлагаем пользователю
        if low.startswith(("service", "uninstall", "delete", "cmd_")) or \
           "service" in low or "update" in low:
            continue
        out.append(os.path.splitext(f)[0])
    return out


def install(progress=None, cancel=None) -> dict:
    """Скачивает и устанавливает последний релиз. Возвращает {version, profiles}."""
    rel = downloader.latest_release(REPO)
    ver = rel["tag"]
    if installed_version() == ver and root_dir():
        return {"version": ver, "profiles": list_profiles()}

    asset = downloader.pick_asset(rel["assets"], ["*.zip"])
    if not asset:
        raise RuntimeError("В релизе нет zip-архива")

    os.makedirs(COMP_DIR, exist_ok=True)
    tmp = os.path.join(COMP_DIR, "_download")
    os.makedirs(tmp, exist_ok=True)
    path = downloader.download(asset, tmp, progress=progress, cancel=cancel)
    src_root = downloader.extract_zip(path, COMP_DIR)

    dst = os.path.join(COMP_DIR, f"zapret-{ver}")
    if os.path.isdir(dst):
        shutil.rmtree(dst, ignore_errors=True)
    shutil.move(src_root, dst)
    os.remove(path)
    shutil.rmtree(tmp, ignore_errors=True)
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        f.write(ver)
    return {"version": ver, "profiles": list_profiles()}


def check_prerequisites() -> dict:
    """Проверка окружения перед запуском профиля (drweb/cert/WinDivert/антивирусы)."""
    root = root_dir()
    problems = []
    if not root:
        problems.append("Компонент zapret не установлен — нажмите «Обновить» или включите автоподбор.")
        return {"ok": False, "problems": problems}
    bin_dir = os.path.join(root, "bin")
    if not os.path.isdir(bin_dir):
        problems.append("Не найдена папка bin — возможно, антивирус удалил файлы. Добавьте каталог в исключения.")
    else:
        for need in ("winws.exe", "WinDivert64.sys"):
            if not os.path.exists(os.path.join(bin_dir, need)):
                problems.append(f"Отсутствует {need} (частая причина — блокировка антивирусом).")
    bat = os.path.join(root, "general.bat")
    if not os.path.exists(bat):
        problems.append("Не найден базовый профиль general.bat.")
    return {"ok": not problems, "problems": problems}


def _bat_for_profile(profile: str) -> str:
    root = root_dir()
    cand = os.path.join(root, profile + ".bat")
    if os.path.exists(cand):
        return cand
    raise FileNotFoundError(f"Профиль не найден: {profile}")


def start_profile(profile: str):
    """Запускает bat-профиль как администратор (UAC), затем следит за winws.exe."""
    if IS_WIN:
        bat = _bat_for_profile(profile)
        # эскалация через UAC; сам bat запускается с повышенными правами
        ps = (f'Start-Process -FilePath "{bat}" -Verb RunAs -WindowStyle Minimized')
        subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", ps],
            creationflags=CREATE_NO_WINDOW)
        manager.zapret.started_at = None  # внешний процесс, uptime не считаем
    else:
        raise RuntimeError("Управление zapret доступно только в Windows (нужен WinDivert).")


def is_running() -> bool:
    """Признак работы: живой winws.exe (запущенный вне нашего контроля)."""
    if manager.zapret.running():
        return True
    if not IS_WIN:
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq winws.exe", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=5,
                             creationflags=CREATE_NO_WINDOW).stdout
        return "winws.exe" in out.lower()
    except Exception:
        return False
