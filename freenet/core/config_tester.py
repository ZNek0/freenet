# -*- coding: utf-8 -*-
"""SMART SETUP: проверка профилей zapret перед запуском.

Два уровня проверки:
  1) Статический аудит bat-профилей (существование bin/list, синтаксис,
     перенос строки, опасные параметры) — быстрый, безопасен на любой ОС.
  2) Сетевой тест профиля (только Windows + админ): временный запуск winws
     с параметрами из bat, затем замер доступности целевых хостов и RTT.
     После теста процесс гарантированно останавливается.

Используется для «Автоматического подбора»: выбираем профиль с наилучшей
комбинацией (успешность соединений, минимальный RTT, стабильность).
"""
import concurrent.futures as cf
import os
import re
import socket
import ssl
import threading
import time
import urllib.parse
import urllib.request

from . import zapret
from .process_mgr import manager, IS_WIN

TARGETS = [
    # (название, url для HTTPS-проверки)
    ("YouTube", "https://www.youtube.com/generate_204"),
    ("Google API", "https://clients1.google.com/generate_204"),
    ("Discord CDN", "https://media-discordapp.com/"),
    ("Discord API", "https://discord.com/api/latency"),
]

GAME_TARGETS = {
    "CS2": [("Steam", "https://steamcommunity.com/"), ("Steam CDN", "https://cdn.steamstatic.com/")],
    "Другая игра": [("Steam", "https://steamcommunity.com/")],
    "Универсальный": TARGETS[:2] + [("Steam", "https://steamcommunity.com/")],
}


# ---------------- статический аудит ----------------
def audit_profile(name: str) -> dict:
    """Проверяет целостность bat-профиля без запуска."""
    root = zapret.root_dir()
    path = os.path.join(root, name + ".bat") if root else ""
    res = {"profile": name, "ok": False, "issues": []}
    if not path or not os.path.exists(path):
        res["issues"].append("Файл профиля не найден")
        return res
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as e:
        res["issues"].append(f"Не читается: {e}")
        return res

    if "winws.exe" not in text:
        res["issues"].append("В профиле нет запуска winws.exe")
        return res

    # проверка существования всех %LISTS%...txt и %BIN%...bin файлов
    missing = []
    for ref in set(re.findall(r'"%(?:LISTS|BIN)%([^"]+)"', text)):
        sub = "lists" if "%LISTS%" in text and ref.endswith(".txt") else "bin"
        if not os.path.exists(os.path.join(root, sub, ref)):
            # ACTIVE_* и *-user.* генерируются service.bat при первом старте — не ошибка
            if ref.startswith("ACTIVE_") or "-user." in ref:
                continue
            missing.append(ref)
    if missing:
        res["issues"].append("Отсутствуют файлы: " + ", ".join(sorted(missing)[:5]))

    # базовая валидность переносов строк внутри команды winws (^)
    m = re.search(r"start .*?winws\.exe(.*?)(?:\r\n|\n|\r)", text)
    if m and "--new" not in text:
        res["issues"].append("Нет ни одного блока --new (подозрительный профиль)")

    res["ok"] = not res["issues"]
    return res


def audit_all() -> list:
    return [audit_profile(p) for p in zapret.list_profiles()]


# ---------------- сетевые замеры ----------------
def _check_target(url: str, timeout: float = 6.0):
    """Возвращает (ok, rtt_ms). Проверяет TLS+HTTP 2xx/3xx."""
    host = urllib.parse.urlsplit(url).hostname
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            code = r.getcode()
        rtt = (time.perf_counter() - t0) * 1000
        return code in (200, 204, 301, 302, 304), rtt
    except Exception:
        # запасной вариант: чистый TLS handshake к 443
        try:
            ctx = ssl.create_default_context()
            with socket.create_connection((host, 443), timeout=timeout) as s:
                with ctx.wrap_socket(s, server_hostname=host):
                    pass
            rtt = (time.perf_counter() - t0) * 1000
            return True, rtt
        except Exception:
            return False, None


def net_check(targets=None, repeats: int = 2) -> dict:
    """Параллельная проверка целей; returns {reachable, total, avg_rtt, per_host}."""
    targets = targets or TARGETS
    results = {}
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        futs = {(name, i): ex.submit(_check_target, url)
                for name, url in targets for i in range(repeats)}
        for (name, _i), fut in futs.items():
            ok, rtt = fut.result()
            results.setdefault(name, []).append((ok, rtt))
    per_host = {}
    rtts = []
    reachable = 0
    for name, vals in results.items():
        oks = [o for o, _ in vals if o]
        good = len(oks) >= (len(vals) + 1) // 2
        if good:
            reachable += 1
        hs = [r for o, r in vals if o and r is not None]
        avg = sum(hs) / len(hs) if hs else None
        if avg is not None:
            rtts.append(avg)
        per_host[name] = {"ok": good, "rtt_ms": round(avg) if avg else None}
    return {
        "reachable": reachable,
        "total": len(targets),
        "avg_rtt": round(sum(rtts) / len(rtts)) if rtts else None,
        "per_host": per_host,
    }


# ---------------- тест профиля с временным запуском ----------------
class ProfileTester:
    """Тестирует профили по очереди. Отменяемый через cancel_event."""

    def __init__(self):
        self.cancel_event = threading.Event()
        self.progress = {"stage": "", "current": 0, "total": 0, "results": []}
        self._thread = None

    def start(self, profiles, game_mode=False, on_done=None):
        if self._thread and self._thread.is_alive():
            raise RuntimeError("Тест уже выполняется")
        self.cancel_event.clear()
        self.progress = {"stage": "Подготовка", "current": 0,
                         "total": len(profiles), "results": []}
        self._on_done = on_done
        self._thread = threading.Thread(target=self._run,
                                        args=(profiles, game_mode), daemon=True)
        self._thread.start()

    def _run(self, profiles, game_mode):
        targets = GAME_TARGETS.get("Универсальный") if game_mode else TARGETS
        base = net_check(targets)          # фон ДО zapret
        results = []
        for i, prof in enumerate(profiles):
            if self.cancel_event.is_set():
                break
            self.progress.update(stage=f"Статическая проверка: {prof}",
                                 current=i, total=len(profiles))
            aud = audit_profile(prof)
            entry = {"profile": prof, "audit": aud, "score": None,
                     "net": None, "verdict": "fail"}
            if not aud["ok"]:
                entry["verdict"] = "broken"
                results.append(entry)
                self.progress["results"] = list(results)
                continue

            self.progress.update(stage=f"Сетевой тест: {prof}")
            verdict, score, net = "fail", None, None
            if IS_WIN:
                # реальный прогон: winws с параметрами профиля (нужен админ)
                try:
                    zapret.start_profile(prof)
                    time.sleep(4.0)  # даём WinDivert поднять фильтры
                    net = net_check(targets)
                    up = net["reachable"] >= max(1, len(targets) // 2)
                    # повторная проверка для оценки стабильности
                    net2 = net_check(targets)
                    stable = net2["reachable"] >= max(1, len(targets) // 2)
                    if up and stable:
                        verdict = "pass"
                        rt = net2["avg_rtt"] or 9999
                        score = round(net2["reachable"] * 1000 - rt)
                    else:
                        verdict = "unstable" if up else "fail"
                except Exception as e:
                    entry["error"] = str(e)
                finally:
                    zapret.stop() if hasattr(zapret, "stop") else None
                    manager.zapret.stop()
                    time.sleep(1.5)
            else:
                # вне Windows: оценка только по аудиту + общий сетевой статус
                entry["note"] = "Сетевой прогон winws доступен только в Windows"
                net = base
                verdict = "candidate"
            entry["net"], entry["verdict"], entry["score"] = net, verdict, score
            results.append(entry)
            self.progress["results"] = list(results)

        best = None
        passed = [r for r in results if r["verdict"] == "pass" and r["score"]]
        if passed:
            best = max(passed, key=lambda r: r["score"])["profile"]
        else:
            # кандидаты (аудит пройден): предпочитаем проверенные сообществом профили
            prio = ["general", "general (ALT)", "general (FAKE TLS AUTO)"]
            cands = [r["profile"] for r in results if r["verdict"] == "candidate"]
            best = next((p for p in prio if p in cands), cands[0] if cands else None)
        self.progress["stage"] = "Готово"
        self.progress["best"] = best
        self.progress["baseline"] = base
        self.progress["results"] = list(results)
        if self._on_done:
            self._on_done(self.progress)

    def status(self) -> dict:
        return dict(self.progress)


tester = ProfileTester()
