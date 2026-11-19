# -*- coding: utf-8 -*-
"""Загрузка и распаковка компонентов с проверкой источника."""
import fnmatch
import os
import re
import shutil
import threading
import zipfile

import requests

from .settings import data_dir

API = "https://api.github.com/repos/{repo}/releases/latest"
UA = {"User-Agent": "FREENET-desktop"}


def latest_release(repo: str) -> dict:
    """Возвращает {tag, assets:[{name,url,size}], notes} для последнего релиза."""
    r = requests.get(API.format(repo=repo), headers=UA, timeout=20)
    r.raise_for_status()
    d = r.json()
    return {
        "tag": d.get("tag_name", ""),
        "published_at": d.get("published_at", ""),
        "assets": [
            {"name": a["name"], "url": a["browser_download_url"], "size": a.get("size", 0)}
            for a in d.get("assets", [])
        ],
    }


def pick_asset(assets, patterns):
    """Подбирает ассет по списку glob-паттернов (первый совпавший)."""
    for pat in patterns:
        for a in assets:
            if fnmatch.fnmatch(a["name"].lower(), pat):
                return a
    return None


def download(asset: dict, dest_dir: str, progress=None, cancel=None) -> str:
    """Скачивает asset в dest_dir с прогрессом. Возвращает путь к файлу."""
    url = asset["url"]
    # Защита: разрешаем только github.com / objects.githubusercontent.com
    host = re.match(r"https?://([^/]+)/", url)
    if not host or not (host.group(1).endswith("github.com") or
                        host.group(1).endswith("githubusercontent.com")):
        raise ValueError(f"Недоверенный источник загрузки: {url}")

    name = os.path.basename(url.split("?")[0])
    path = os.path.join(dest_dir, name)
    total = int(asset.get("size") or 0)
    done = 0
    with requests.get(url, stream=True, headers=UA, timeout=60) as r:
        r.raise_for_status()
        with open(path + ".part", "wb") as f:
            for chunk in r.iter_content(chunk_size=256 * 1024):
                if cancel is not None and cancel.is_set():
                    f.close()
                    os.remove(path + ".part")
                    raise InterruptedError("Загрузка отменена")
                f.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress(done, total)
    if os.path.exists(path):
        os.remove(path)
    os.replace(path + ".part", path)
    return path


def extract_zip(path: str, dest_dir: str) -> str:
    """Безопасная распаковка zip (защита от zip-slip). Возвращает корень содержимого."""
    staging = os.path.join(dest_dir, "_unzip_tmp")
    if os.path.isdir(staging):
        shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging, exist_ok=True)
    root = os.path.abspath(staging)
    with zipfile.ZipFile(path) as z:
        for member in z.namelist():
            target = os.path.abspath(os.path.join(root, member))
            if not target.startswith(root + os.sep) and target != root:
                raise ValueError(f"Небезопасный путь в архиве: {member}")
        z.extractall(root)
    # определяем единственный корневой каталог внутри staging
    entries = [e for e in os.listdir(staging)]
    final_root = staging
    if len(entries) == 1 and os.path.isdir(os.path.join(staging, entries[0])):
        final_root = os.path.join(staging, entries[0])
    return final_root
