# -*- coding: utf-8 -*-
"""Локальный HTTP-сервер (Flask) — мост между UI и backend-логикой.

Все запросы идут на 127.0.0.1, токен защищает от сторонних обращений.
"""
import secrets
import threading
import time
import traceback

from flask import Flask, jsonify, request, send_from_directory

from core import downloader, zapret, tgws
from core.config_tester import tester, audit_all, net_check
from core.process_mgr import manager
from core.settings import settings, ui_dir

TOKEN = secrets.token_hex(16)
app = Flask(__name__, static_folder=ui_dir(), static_url_path="")

dl_state = {"active": False, "component": "", "done": 0, "total": 0,
            "stage": "", "error": ""}


def _auth_ok() -> bool:
    return request.headers.get("X-Freenet-Token") == TOKEN or \
        request.args.get("t") == TOKEN


@app.before_request
def guard():
    if request.path in ("/", "/index.html") or request.path.startswith("/api/"):
        if request.path.startswith("/api/") and not _auth_ok():
            return jsonify({"error": "forbidden"}), 403


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ------------------------------ API ------------------------------
@app.route("/api/state")
def api_state():
    st = manager.status()
    st["zapret"]["running"] = zapret.is_running()
    st["tg"]["running"] = tgws.is_running()
    st["versions"] = {
        "zapret": zapret.installed_version(),
        "tg": tgws.installed_version(),
    }
    st["settings"] = settings.all()
    st["profiles"] = zapret.list_profiles()
    st["download"] = dict(dl_state)
    st["test"] = tester.status()
    return jsonify(st)


@app.route("/api/toggle", methods=["POST"])
def api_toggle():
    name = request.json.get("component")
    on = bool(request.json.get("on"))
    try:
        if name == "zapret":
            prof = settings.get("selectedConfig", "auto")
            if on:
                if prof == "auto":
                    best = tester.status().get("best") or "general"
                    prof = best
                pre = zapret.check_prerequisites()
                if not pre["ok"]:
                    # пробуем доставить компонент автоматически
                    zapret.install()
                    pre = zapret.check_prerequisites()
                    if not pre["ok"]:
                        return jsonify({"error": "; ".join(pre["problems"])}), 400
                zapret.start_profile(prof)
            else:
                manager.zapret.stop()
        elif name == "tg":
            if on:
                if not tgws.installed_version():
                    _install_component("tg")
                tgws.start()
            else:
                tgws.stop()
        settings.update({"zapretEnabled": name == "zapret" and on}
                        if name == "zapret" else
                        {"tgEnabled": on})
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/start_selected", methods=["POST"])
def api_start_selected():
    results = {}
    for comp in ("zapret", "tg"):
        if settings.get(comp + "Enabled"):
            r = _safe_toggle(comp, True)
            results[comp] = r
    return jsonify(results)


def _safe_toggle(comp, on):
    try:
        with app.test_request_context(json={"component": comp, "on": on}):
            resp = api_toggle()
            return resp[0].get_json() if isinstance(resp, tuple) else resp.get_json()
    except Exception as e:
        return {"error": str(e)}


@app.route("/api/stop_all", methods=["POST"])
def api_stop_all():
    manager.stop_all()
    settings.update({"zapretEnabled": False, "tgEnabled": False})
    return jsonify({"ok": True})


@app.route("/api/settings", methods=["POST"])
def api_settings():
    patch = request.json or {}
    settings.update(patch)
    # автозапуск Windows через реестр
    s = patch.get("settings", {})
    if "startup" in s and manager_is_win():
        _apply_startup(bool(s["startup"]))
    return jsonify({"ok": True, "saved": settings.all()})


def manager_is_win():
    from core.process_mgr import IS_WIN
    return IS_WIN


def _apply_startup(enable: bool):
    import sys
    import winreg
    exe = getattr(sys, "executable", sys.argv[0])
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                         r"Software\Microsoft\Windows\CurrentVersion\Run",
                         0, winreg.KEY_SET_VALUE)
    if enable:
        winreg.SetValueEx(key, "FREENET", 0, winreg.REG_SZ, f'"{exe}" --minimized')
    else:
        try:
            winreg.DeleteValue(key, "FREENET")
        except FileNotFoundError:
            pass
    winreg.CloseKey(key)


@app.route("/api/install", methods=["POST"])
def api_install():
    comp = request.json.get("component", "all")
    if dl_state["active"]:
        return jsonify({"error": "Загрузка уже выполняется"}), 409
    t = threading.Thread(target=_install_component, args=(comp,), daemon=True)
    t.start()
    return jsonify({"started": True})


def _install_component(comp):
    global dl_state
    dl_state = {"active": True, "component": comp, "done": 0, "total": 0,
                "stage": "Проверка релизов…", "error": ""}

    def prog(done, total):
        dl_state.update(done=done, total=total)

    try:
        if comp in ("zapret", "all"):
            dl_state["stage"] = "Zapret: загрузка"
            zapret.install(progress=prog)
        if comp in ("tg", "all"):
            dl_state["stage"] = "TG-WG: загрузка"
            dl_state.update(done=0, total=0)
            tgws.install(progress=prog)
        dl_state["stage"] = "Готово"
    except Exception as e:
        dl_state["error"] = str(e)
        dl_state["stage"] = "Ошибка"
    finally:
        dl_state["active"] = False


@app.route("/api/updates/check")
def api_updates_check():
    out = {}
    try:
        zr = downloader.latest_release(zapret.REPO)
        out["zapret"] = {"latest": zr["tag"],
                         "installed": zapret.installed_version(),
                         "need_update": zr["tag"] != zapret.installed_version()}
    except Exception as e:
        out["zapret"] = {"error": str(e)}
    try:
        tr = downloader.latest_release(tgws.REPO)
        out["tg"] = {"latest": tr["tag"],
                     "installed": tgws.installed_version(),
                     "need_update": tr["tag"] != tgws.installed_version()}
    except Exception as e:
        out["tg"] = {"error": str(e)}
    settings.update({"lastUpdateCheck": time.strftime("%Y-%m-%d %H:%M")})
    return jsonify(out)


@app.route("/api/configs/audit")
def api_configs_audit():
    return jsonify({"profiles": audit_all()})


@app.route("/api/configs/test", methods=["POST"])
def api_configs_test():
    game_mode = bool((request.json or {}).get("gameMode"))
    profiles = zapret.list_profiles()
    if not profiles:
        return jsonify({"error": "Компонент не установлен"}), 400
    try:
        tester.start(profiles, game_mode=game_mode)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 409
    return jsonify({"started": True, "total": len(profiles)})


@app.route("/api/configs/test/cancel", methods=["POST"])
def api_configs_cancel():
    tester.cancel_event.set()
    return jsonify({"ok": True})


@app.route("/api/netcheck")
def api_netcheck():
    return jsonify(net_check())


@app.route("/api/log/<comp>")
def api_log(comp):
    p = manager.get(comp if comp != "tg" else "tgws")
    return jsonify({"lines": p.tail(120)})


@app.route("/api/proxy_link")
def api_proxy_link():
    try:
        return jsonify({"link": tgws.proxy_link()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def run_server(port: int):
    app.run(host="127.0.0.1", port=port, threaded=True, debug=False)
