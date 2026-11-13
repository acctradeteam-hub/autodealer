"""Окно программы с телефона: Mac ↔ телефон через Tailscale (закрытая сеть только ваших устройств).

— Программа, как и раньше, слушает 127.0.0.1 (окно на Mac, расширение). Когда в окне включён «доступ с телефона»
  и на Mac работает Tailscale, она слушает ещё и свой адрес Tailscale (100.x.y.z): в интернет не открывается ничего,
  зайти может только ваше устройство из вашей сети Tailscale. PIN — по желанию: задан — телефон спросит его один раз.
— С телефона нельзя: менять настройки (PIN, отправку Claude, вход Carfax) и адреса, которые принадлежат расширению.
— Кнопки, которые на Mac выполняет расширение Chrome (собрать аукцион, KBB, проверка фото, Carfax, запись торгов),
  с телефона кладутся в очередь; расширение на Mac забирает её раз в 30 секунд и выполняет у себя.
— Mac не засыпает, пока работает программа (caffeinate): иначе ни телефон, ни расписание (KBB в четверг,
  запись торгов в понедельник) не работают. Экран при этом гаснет как обычно.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from http.cookies import SimpleCookie

from .paths import DATA_DIR

SETTINGS_PATH = DATA_DIR / "remote.json"
COOKIE = "la_auth"
TOKEN_DAYS = 180
TAILNET = ipaddress.ip_network("100.64.0.0/10")
# Кнопки окна, которые с телефона уходят расширению на Mac (те же, что окно на Mac шлёт расширению само).
COMMANDS = {"auto-run", "kbbweek-run", "photos-start", "carfax-order", "join-run", "join-enable", "open-bg"}
OPEN_BG_PREFIXES = ("https://www.kbb.com/", "https://www.carmaxauctions.com/")
# POST, которые окно на телефоне может делать: правки машин и ставок. Настройки и адреса расширения — только на Mac.
REMOTE_POST = {"/api/bid", "/api/bids_ceiling", "/api/inspect", "/api/kbb", "/api/links", "/api/owners_scope", "/api/reject",
               "/api/voice_push", "/api/window_note", "/api/remote_cmd", "/api/login"}

_lock = threading.Lock()
_queue: list[dict] = []
_fails: list[float] = []
EXT_STATUS: dict = {}                 # что расширение на Mac сообщило в последний раз (автосбор, запись торгов, KBB на неделю)
STATE = {"ip": "", "bound": "", "error": "", "awake": ""}


def load() -> dict:
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8")) if SETTINGS_PATH.exists() else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        SETTINGS_PATH.chmod(0o600)
    except OSError:
        pass


def _hash(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 200_000).hex()


def set_enabled(on: bool) -> None:
    """Доступ с телефона: включить (без PIN — пускает любое ваше устройство из сети Tailscale) или выключить совсем."""
    data = load()
    data["open"] = bool(on)
    if not on:
        data.pop("pin_hash", None)
        data.pop("salt", None)
        data["tokens"] = {}
    _save(data)


def set_pin(pin: str) -> str:
    """PIN — по желанию: 6–12 цифр (телефон спросит его один раз). Пусто — без PIN. Новый PIN выходит из всех телефонов."""
    pin = str(pin or "").strip()
    data = load()
    if not pin:
        data.pop("pin_hash", None)
        data.pop("salt", None)
        data["tokens"] = {}
        _save(data)
        return ""
    if not re.fullmatch(r"\d{6,12}", pin):
        return "PIN — от 6 до 12 цифр"
    salt = secrets.token_hex(16)
    data.update(salt=salt, pin_hash=_hash(pin, salt), tokens={})
    _save(data)
    return ""


def enabled() -> bool:
    data = load()
    return bool(data.get("pin_hash") or data.get("open"))


def pin_required() -> bool:
    return bool(load().get("pin_hash"))


def login(pin: str) -> str | None:
    """Верный PIN — новый пропуск (кука на телефоне), иначе None. Подбор PIN: не больше 8 ошибок за 15 минут."""
    now = time.time()
    with _lock:
        _fails[:] = [t for t in _fails if now - t < 900]
        if len(_fails) >= 8:
            return None
    data = load()
    if not data.get("pin_hash") or not hmac.compare_digest(_hash(str(pin or ""), data["salt"]), data["pin_hash"]):
        with _lock:
            _fails.append(now)
        time.sleep(1)
        return None
    token = secrets.token_urlsafe(32)
    tokens = {k: v for k, v in (data.get("tokens") or {}).items() if v > now}
    tokens[hashlib.sha256(token.encode()).hexdigest()] = now + TOKEN_DAYS * 86400
    data["tokens"] = tokens
    _save(data)
    return token


def authorized(cookie_header: str | None) -> bool:
    data = load()
    if data.get("open") and not data.get("pin_hash"):
        return True                                   # без PIN: пускает любое ваше устройство из сети Tailscale
    if not cookie_header:
        return False
    try:
        jar = SimpleCookie(cookie_header)
    except Exception:  # noqa: BLE001 — кривая кука = не вошли
        return False
    morsel = jar.get(COOKIE)
    if not morsel or not morsel.value:
        return False
    expires = (load().get("tokens") or {}).get(hashlib.sha256(morsel.value.encode()).hexdigest())
    return bool(expires and expires > time.time())


def cookie_header(token: str) -> str:
    return f"{COOKIE}={token}; Path=/; Max-Age={TOKEN_DAYS * 86400}; HttpOnly; SameSite=Strict"


def tailscale_ip() -> str:
    """Адрес этого компьютера в сети Tailscale (100.64.0.0/10) или "" — Tailscale не запущен."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("100.100.100.100", 53))          # сервис Tailscale: путь к нему идёт через свой адрес Tailscale
            ip = s.getsockname()[0]
    except OSError:
        return ""
    try:
        return ip if ipaddress.ip_address(ip) in TAILNET else ""
    except ValueError:
        return ""


def add_command(cmd: dict) -> str:
    """Команда с телефона для расширения на Mac. Возвращает ошибку или ""."""
    kind = str(cmd.get("type") or "")
    if kind not in COMMANDS:
        return "такой команды нет"
    clean = {"type": kind, "at": time.time(), "id": secrets.token_hex(6)}
    if kind == "auto-run":
        clean["which"] = cmd.get("which") if cmd.get("which") in ("mon", "tue") else ""
    elif kind == "join-enable":
        clean["on"] = bool(cmd.get("on"))
    elif kind == "carfax-order":
        vin = str(cmd.get("vin") or "").upper()
        if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin):
            return "нет VIN"
        clean["vin"] = vin
    elif kind == "open-bg":
        url = str(cmd.get("url") or "")
        if not url.startswith(OPEN_BG_PREFIXES) or len(url) > 60000:
            return "этот адрес открывается на телефоне"
        clean["url"], clean["cars"] = url, str(cmd.get("cars") or "")[:60000]
    with _lock:
        _queue.append(clean)
        del _queue[:-50]
    return ""


def take_commands(status: dict | None = None) -> list[dict]:
    """Расширение на Mac (раз в 30 с): отдаёт своё состояние, забирает команды (не старше 15 минут)."""
    now = time.time()
    if isinstance(status, dict):
        EXT_STATUS.clear()
        EXT_STATUS.update({k: status.get(k) for k in ("version", "auto", "join", "kbbweek")})
        EXT_STATUS["at"] = now
    with _lock:
        out = [c for c in _queue if now - c["at"] < 900]
        _queue.clear()
    return out


def status() -> dict:
    data = load()
    fresh = bool(EXT_STATUS.get("at") and time.time() - EXT_STATUS["at"] < 90)
    return {"enabled": bool(data.get("pin_hash") or data.get("open")), "pin": bool(data.get("pin_hash")),
            "phones": len([1 for v in (data.get("tokens") or {}).values() if v > time.time()]),
            "ip": STATE["ip"], "bound": STATE["bound"], "error": STATE["error"], "awake": STATE["awake"],
            "ext": {**EXT_STATUS, "fresh": fresh}, "queued": len(_queue)}


def keep_awake() -> None:
    """Mac не засыпает, пока работает программа (caffeinate -i -s, до выхода программы). Экран гаснет как обычно.
    Крышку MacBook закрыли без внешнего монитора — macOS всё равно уснёт."""
    if sys.platform != "darwin" or os.environ.get("LOT_ANALYZER_NO_CAFFEINATE"):
        return
    try:
        subprocess.Popen(["caffeinate", "-i", "-s", "-w", str(os.getpid())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        STATE["awake"] = "Mac не засыпает, пока работает программа"
    except OSError as err:
        STATE["awake"] = f"не вышло запретить сон: {err}"


def start(make_server, every: float = 30.0) -> None:
    """Фон: когда задан PIN и есть адрес Tailscale — слушать его (адрес сменился — перейти на новый)."""
    def loop() -> None:
        server = None
        while True:
            ip = tailscale_ip() if enabled() else ""
            STATE["ip"] = ip
            if server is not None and (not ip or server.server_address[0] != ip):
                server.shutdown()
                server.server_close()
                server, STATE["bound"] = None, ""
            if ip and server is None:
                try:
                    server = make_server(ip)
                    threading.Thread(target=server.serve_forever, daemon=True).start()
                    STATE["bound"], STATE["error"] = f"http://{ip}:{server.server_address[1]}", ""
                except OSError as err:
                    STATE["error"] = f"не удалось открыть адрес {ip}: {err}"
            elif enabled() and not ip:
                STATE["error"] = "на Mac не запущен Tailscale"
            elif not enabled():
                STATE["error"] = ""
            time.sleep(every)
    threading.Thread(target=loop, daemon=True).start()
