"""«✍ Заметки фоном»: KBB, ставка и замечания в Notes на CarMax — без открытой страницы CarMax.

Раньше заметки писались, только пока вы сидите на странице CarMax (вид «Detailed»), и приходилось ждать. Теперь
расширение (notes_bg.js) само открывает в фоновой вкладке карточку лота (carmaxauctions.com/vehicledetail/<номер>),
carmax_notes.js вписывает заметку и жмёт Save, вкладка переходит к следующей машине и в конце закрывается.

Программа ведёт учёт (data/notes_sync.json): какой текст заметки у машины уже записан на CarMax. Текст поменялся
(пришёл KBB, вписали ставку, надиктовали голосом) — машина снова в очереди. Записанное с открытой страницы CarMax
тоже отмечается — второй раз не открываем. Дата у строки KBB в учёт не входит: она не меняется на сайте.

В очередь — только CarMax, торги впереди (архив — нет), есть карточка лота, не «отбой» и не убрана из избранного,
и в заметке есть что-то ваше: настоящий KBB, ставка из окна или голосовая заметка. И только «ваши» машины: в избранном
CarMax (звёздочка), со ставкой из окна, «На осмотр», «Беру» с телефона, с голосовой заметкой. Запись в Notes добавляет
машину в избранное CarMax — на 26.11 из 161 машины с KBB в избранном было 17; всем машинам с KBB — all_cars: true.
Ставку на CarMax расширение не ставит и «Place bid» не нажимает — только поле Notes и его кнопку Save.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

from . import notes
from .paths import DATA_DIR

PATH = DATA_DIR / "notes_sync.json"     # VIN → {h: отпечаток записанного текста, at; или wait_h, n, try_at, why — не вышло}
DEFAULTS = {"enabled": True, "all_cars": False, "every_min": 30, "from_hour": 7, "to_hour": 22, "per_run": 60, "gap_s": 2, "tries": 3, "retry_hours": 2}


def config(costs: dict) -> dict:
    return {**DEFAULTS, **(costs.get("notes_bg") or {})}


def load() -> dict[str, dict]:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else {}
    except (OSError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def _save(state: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_name(PATH.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=0), encoding="utf-8")
    tmp.replace(PATH)


def fingerprint(note: str) -> str:
    """Отпечаток текста заметки — без даты у строки KBB (тот же KBB на сайте остаётся со своей датой)."""
    lines = [re.sub(r"^(KBB [\d,]+\$).*$", r"\1", line.strip()) for line in str(note or "").splitlines() if line.strip()]
    return hashlib.sha1("\n".join(lines).encode("utf-8")).hexdigest()[:16]


def stock_of(row: dict) -> str:
    m = re.search(r"/vehicledetail/(\d+)", str(row.get("lot_url") or ""))
    return m.group(1) if m else ""


def _ours(note: str) -> bool:
    """В заметке есть ваше: настоящий KBB, ставка из окна (или её снятие), голосовая заметка."""
    return any(line.startswith(("KBB ", "MP ", "Голос: ")) for line in note.splitlines())


def mine(row: dict) -> bool:
    """Машина, с которой вы работаете: избранное CarMax, ставка из окна, «На осмотр», «Беру», голос."""
    return bool(row.get("favorite") == "1" or row.get("my_bid_from_window") or row.get("mp_removed") or row.get("inspect_manual")
                or row.get("phone_take") == "Беру" or str(row.get("voice_notes") or "").strip())


def candidates(rows: list[dict], all_cars: bool = False) -> list[tuple[dict, str]]:
    """(машина, текст заметки) — кому заметку на CarMax вообще пишем фоном."""
    out, seen = [], set()
    for row in rows:
        vin = str(row.get("vin") or "").upper()
        if (not vin or vin in seen or row.get("auction") != "CarMax" or row.get("passed") or row.get("archived")
                or row.get("rejected") or row.get("favorite_removed") or not stock_of(row) or not (all_cars or mine(row))):
            continue
        note = notes.lot_note(row, clear_mp=True)
        if note and _ours(note):
            seen.add(vin)
            out.append((row, note))
    return out


def queue(rows: list[dict], costs: dict, now: float | None = None) -> dict:
    """Очередь для расширения: машины, чей текст заметки ещё не записан на CarMax. Сначала — со ставкой и
    с ближайшими торгами. Не вышло tries раз — эту версию текста больше не пробуем (новый текст — снова)."""
    now = now or time.time()
    cfg = config(costs)
    state = load()
    todo, written, failed = [], 0, 0
    for row, note in candidates(rows, bool(cfg["all_cars"])):
        vin, h = row["vin"].upper(), fingerprint(note)
        rec = state.get(vin) or {}
        if rec.get("h") == h:
            written += 1
            continue
        if rec.get("wait_h") == h:
            if int(rec.get("n") or 0) >= int(cfg["tries"]):
                failed += 1
                continue
            if now - float(rec.get("try_at") or 0) < float(cfg["retry_hours"]) * 3600:
                continue
        todo.append({"vin": vin, "stock": stock_of(row), "note": note, "known": str(row.get("site_notes") or "")[:2000],
                     "car": " ".join(str(row.get(k) or "") for k in ("year", "make", "model")).strip(),
                     "bid": 1 if row.get("my_bid_from_window") else 0, "sale": str(row.get("sale_date") or "")})
    from .inspection import _day
    todo.sort(key=lambda c: (-c["bid"], _day(c["sale"]) or "99"))
    return {"cars": todo[:int(cfg["per_run"])], "pending": len(todo), "written": written, "failed": failed,
            "cfg": {k: cfg[k] for k in ("enabled", "every_min", "from_hour", "to_hour", "gap_s")}}


def done(cars: list[dict], now: float | None = None) -> int:
    """Расширение отчиталось: заметка записана (или на сайте уже такая) — запоминаем текст; не вышло — попытка."""
    now = now or time.time()
    state = load()
    n = 0
    for car in cars or []:
        vin = str(car.get("vin") or "").upper()
        note = str(car.get("note") or "")
        if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin) or not note:
            continue
        h = fingerprint(note)
        if car.get("ok"):
            state[vin] = {"h": h, "at": now}
        else:
            old = state.get(vin) or {}
            tries = int(old.get("n") or 0) + 1 if old.get("wait_h") == h else 1
            state[vin] = {**({"h": old["h"], "at": old.get("at")} if old.get("h") else {}),
                          "wait_h": h, "n": tries, "try_at": now, "why": str(car.get("why") or "")[:200]}
        n += 1
    # старые записи (машины ушли) — не копим: 60 дней
    for vin in [v for v, r in state.items() if now - float(r.get("at") or r.get("try_at") or 0) > 60 * 86400]:
        del state[vin]
    if n:
        _save(state)
    return n


def summary(rows: list[dict], costs: dict) -> dict:
    q = queue(rows, costs)
    return {k: q[k] for k in ("pending", "written", "failed")} | {"enabled": q["cfg"]["enabled"]}
