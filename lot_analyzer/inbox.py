"""Отправка сохранённых файлов в ваш ЗАКРЫТЫЙ репозиторий GitHub — оттуда их читает Claude.

Включается в окне программы (кнопка «Отправка Claude»): репозиторий (например acctradeteam-hub/autodealer-inbox)
и токен GitHub с правом «Contents: Read and write» только на этот репозиторий. Настройки — в data/inbox.json
(на вашем компьютере, в git не попадают).

Что отправляется: те же файлы, которые окно читает из «Загрузок» (страницы закладки, KBB, выгрузки и итоги торгов),
в папку inbox/<дата>/ репозитория. Каждый файл — один раз (повторно — только если он изменился).
Перед отправкой проверяется, что репозиторий закрытый: в открытый файлы не отправляются никогда.
"""

from __future__ import annotations

import base64
import json
import ssl
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

SETTINGS_PATH = Path("data/inbox.json")
SENT_PATH = Path("data/inbox_sent.json")
API = "https://api.github.com"
MAX_BYTES = 40 * 1024 * 1024

_state = {"status": "выключена", "sent": 0, "error": "", "checked": 0.0, "private": None}
_lock = threading.Lock()


def _context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8")) if SETTINGS_PATH.exists() else {}
    except (OSError, ValueError):
        return {}


def save_settings(repo: str, token: str | None, enabled: bool) -> None:
    """token=None — оставить прежний (в окне токен не показывается)."""
    old = load_settings()
    data = {"repo": repo.strip().strip("/"), "token": old.get("token", "") if token is None else token.strip(), "enabled": bool(enabled)}
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        SETTINGS_PATH.chmod(0o600)
    except OSError:
        pass
    with _lock:
        _state.update(checked=0.0, private=None, error="")


def _request(method: str, url: str, token: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "lot-analyzer"})
    try:
        with urllib.request.urlopen(req, timeout=60, context=_context()) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read() or b"{}")
        except ValueError:
            return error.code, {}


def _sent() -> dict[str, str]:
    try:
        return json.loads(SENT_PATH.read_text(encoding="utf-8")) if SENT_PATH.exists() else {}
    except (OSError, ValueError):
        return {}


def _mark(sent: dict[str, str]) -> None:
    SENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SENT_PATH.write_text(json.dumps(sent, ensure_ascii=False, indent=1), encoding="utf-8")


def _is_private(repo: str, token: str) -> bool | None:
    """True — закрытый, False — открытый (не отправляем), None — нет доступа / нет связи."""
    with _lock:
        if _state["private"] is not None and time.time() - _state["checked"] < 600:
            return _state["private"]
    code, info = _request("GET", f"{API}/repos/{repo}", token)
    private = bool(info.get("private")) if code == 200 else None
    with _lock:
        _state.update(checked=time.time(), private=private)
        if code != 200:
            _state["error"] = f"нет доступа к {repo} (GitHub ответил {code}) — проверьте имя репозитория и токен"
    return private


def send_new(files: list[Path]) -> int:
    """Отправляет новые и изменившиеся файлы. Возвращает, сколько отправлено сейчас."""
    cfg = load_settings()
    if not (cfg.get("enabled") and cfg.get("repo") and cfg.get("token")):
        with _lock:
            _state["status"] = "выключена"
        return 0
    private = _is_private(cfg["repo"], cfg["token"])
    if private is not True:
        with _lock:
            _state["status"] = "остановлена"
            if private is False:
                _state["error"] = f"репозиторий {cfg['repo']} ОТКРЫТЫЙ — в него файлы не отправляются. Сделайте его закрытым (Private)."
        return 0
    sent = _sent()
    done = 0
    for path in files:
        try:
            stat = path.stat()
        except OSError:
            continue
        stamp = f"{stat.st_size}:{int(stat.st_mtime)}"
        if sent.get(path.name) == stamp or stat.st_size > MAX_BYTES:
            continue
        day = time.strftime("%Y-%m-%d", time.localtime(stat.st_mtime))
        url = f"{API}/repos/{cfg['repo']}/contents/inbox/{day}/{urllib.request.quote(path.name)}"
        code, info = _request("GET", url, cfg["token"])
        body = {"message": f"inbox: {path.name}", "content": base64.b64encode(path.read_bytes()).decode()}
        if code == 200 and info.get("sha"):
            body["sha"] = info["sha"]
        code, info = _request("PUT", url, cfg["token"], body)
        if code in (200, 201):
            sent[path.name] = stamp
            _mark(sent)
            done += 1
        else:
            with _lock:
                _state["error"] = f"{path.name}: GitHub ответил {code} {info.get('message', '')}".strip()
            break
    with _lock:
        _state["status"] = "включена"
        _state["sent"] = len(sent)
        if done:
            _state["error"] = ""
    return done


def state() -> dict:
    cfg = load_settings()
    with _lock:
        return {**_state, "repo": cfg.get("repo", ""), "enabled": bool(cfg.get("enabled")), "has_token": bool(cfg.get("token"))}


def start(find_files, every: int = 30) -> None:
    """Фоновая отправка: раз в `every` секунд — новые файлы из «Загрузок»."""
    def loop():
        while True:
            try:
                send_new(find_files())
            except Exception as error:  # сеть, диск — окно не роняем
                with _lock:
                    _state["error"] = f"отправка: {error}"
            time.sleep(every)

    threading.Thread(target=loop, daemon=True).start()
