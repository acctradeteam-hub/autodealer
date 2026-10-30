"""Голосовые заметки с аукциона: телефон ↔ программа через ваш ЗАКРЫТЫЙ репозиторий (тот же, что «Отправка Claude»).

Кнопка окна «📱 На телефон» кладёт в репозиторий voice/list.json — машины из «Списка на осмотр»
(лот, машина, пробег, VIN, KBB, наш потолок, средняя цена, ставка, заметки). Приложение «Голосовые заметки»
на телефоне (страница claude.ai) читает этот список через ваш подключённый GitHub, вы надиктовываете заметку
к машине — приложение пишет voice/notes.json. Программа раз в пару минут забирает его в data/voice_notes.json:
заметка видна в окне («🎤 …»), в списке на печать и уходит строкой «Голос: …» в Notes на CarMax.
"""

from __future__ import annotations

import base64
import json
import threading
import time
import urllib.request

from . import inbox
from .paths import DATA_DIR

LOCAL_PATH = DATA_DIR / "voice_notes.json"
WINDOW_PATH = DATA_DIR / "window_notes.json"     # надиктовано / написано в окне программы (с телефона приходит в LOCAL_PATH)
LIST_PATH, NOTES_PATH = "voice/list.json", "voice/notes.json"
PULL_EVERY = 120
_state = {"pulled": 0.0, "pushed": "", "error": "", "busy": False}
_lock = threading.Lock()


def _repo() -> tuple[str, str] | None:
    cfg = inbox.load_settings()
    if not (cfg.get("repo") and cfg.get("token")):
        return None
    if inbox._is_private(cfg["repo"], cfg["token"]) is not True:      # в открытый репозиторий — никогда
        return None
    return cfg["repo"], cfg["token"]


def _url(repo: str, path: str) -> str:
    return f"{inbox.API}/repos/{repo}/contents/{urllib.request.quote(path)}"


def car_of(row: dict) -> dict:
    from .notes import lot_note

    notes = [x for x in lot_note(row).split("\n") if x.strip() and not x.startswith("Голос: ")]
    own = [x.strip() for x in str(row.get("site_notes") or "").split("\n") if x.strip()]
    return {"vin": row.get("vin", ""), "lot": row.get("lot_number", ""), "place": row.get("place", ""), "sale": row.get("sale_date", ""),
            "car": " ".join(str(row.get(k, "")) for k in ("year", "make", "model", "trim")).strip(), "miles": row.get("odometer_miles", ""),
            "kbb": row.get("kbb_private_party_usd") or "", "kbb_est": row.get("kbb_estimate_usd") or "",
            "mine": row.get("calc_max_bid_usd", ""), "mine_defect": row.get("calc_max_bid_if_defect_usd", ""),
            "market": row.get("market_estimate_usd", ""), "mp": row.get("my_proxy_usd", ""), "no_pics": row.get("no_photos") == "да",
            "notes": notes, "own": own, "url": row.get("lot_url", "")}


def push_list(rows: list[dict]) -> dict:
    """Машины списка на осмотр (впереди торгов) — в voice/list.json репозитория."""
    from .inspection import inspection_rows

    target = _repo()
    if not target:
        return {"ok": False, "error": "Включите «Отправка Claude» (закрытый репозиторий и токен) — через него список уходит на телефон."}
    repo, token = target
    cars = [car_of(r) for r in inspection_rows([r for r in rows if not r.get("passed")]) if r.get("vin")]
    body = json.dumps({"made": time.strftime("%Y-%m-%d %H:%M"), "cars": cars}, ensure_ascii=False).encode("utf-8")
    code, info = inbox._request("GET", _url(repo, LIST_PATH), token)
    put = {"message": f"voice: список на телефон ({len(cars)} машин)", "content": base64.b64encode(body).decode()}
    if code == 200 and info.get("sha"):
        put["sha"] = info["sha"]
    code, info = inbox._request("PUT", _url(repo, LIST_PATH), token, put)
    if code not in (200, 201):
        return {"ok": False, "error": f"GitHub ответил {code} {info.get('message', '')}".strip()}
    with _lock:
        _state["pushed"] = time.strftime("%H:%M")
    return {"ok": True, "cars": len(cars)}


def load() -> dict[str, dict]:
    try:
        return json.loads(LOCAL_PATH.read_text(encoding="utf-8")) if LOCAL_PATH.exists() else {}
    except (OSError, ValueError):
        return {}


def pull() -> bool:
    """voice/notes.json из репозитория → data/voice_notes.json. True — что-то изменилось."""
    target = _repo()
    if not target:
        return False
    repo, token = target
    code, info = inbox._request("GET", _url(repo, NOTES_PATH), token)
    with _lock:
        _state["pulled"] = time.time()
    if code == 404:
        return False
    if code != 200 or not info.get("content"):
        with _lock:
            _state["error"] = f"заметки с телефона: GitHub ответил {code}"
        return False
    try:
        data = json.loads(base64.b64decode(info["content"]).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return False
    notes = {str(v).upper(): n for v, n in (data.get("notes") or {}).items() if isinstance(n, dict)}
    if notes == load():
        return False
    LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_PATH.write_text(json.dumps(notes, ensure_ascii=False, indent=0), encoding="utf-8")
    return True


def pull_soon() -> None:
    """Не чаще PULL_EVERY секунд, в фоне — окно не ждёт GitHub."""
    with _lock:
        if _state["busy"] or time.time() - _state["pulled"] < PULL_EVERY:
            return
        _state["busy"] = True

    def run() -> None:
        try:
            pull()
        except Exception as error:  # нет сети — попробуем позже
            with _lock:
                _state["error"] = str(error)
        finally:
            with _lock:
                _state["busy"] = False
    threading.Thread(target=run, daemon=True).start()


def text_of(note: dict) -> str:
    """Одна строка: пометки («дефекта нет», «беру») и надиктованный текст."""
    tags = [str(t) for t in note.get("tags") or [] if t]
    return " · ".join(x for x in (", ".join(tags), str(note.get("text") or "").strip()) if x).replace("\n", " ")[:400]


def load_window() -> dict[str, dict]:
    try:
        return json.loads(WINDOW_PATH.read_text(encoding="utf-8")) if WINDOW_PATH.exists() else {}
    except (OSError, ValueError):
        return {}


def save_window_note(vin: str, text: str) -> dict[str, dict]:
    """Заметка окна к машине (VIN): пустой текст — убрать."""
    vin = str(vin or "").strip().upper()
    notes = load_window()
    text = " ".join(str(text or "").split())[:1000]
    if not vin:
        return notes
    if text:
        notes[vin] = {"text": text, "at": int(time.time() * 1000)}
    else:
        notes.pop(vin, None)
    WINDOW_PATH.parent.mkdir(parents=True, exist_ok=True)
    WINDOW_PATH.write_text(json.dumps(notes, ensure_ascii=False, indent=0), encoding="utf-8")
    return notes


def attach(rows: list[dict]) -> None:
    """С телефона и из окна — одной строкой «🎤 …»: в окне, в списке на печать и в Notes на CarMax."""
    notes, window = load(), load_window()
    for row in rows:
        vin = row.get("vin", "")
        note, own = notes.get(vin), window.get(vin)
        parts = [x for x in ((text_of(note) if note else ""), (own or {}).get("text", "")) if x]
        if parts:
            row["voice_notes"] = " · ".join(parts)[:600]
        if own:
            row["window_note"] = own.get("text", "")
