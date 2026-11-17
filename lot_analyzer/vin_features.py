"""«🔑 Нужен ключ» (Oceanside) и камера заднего вида — по VIN и по отметкам с осмотра.

На Oceanside CarMax забирает ключи у машин с кнопкой Start/Stop (бесключевой запуск): на осмотре ключ нужно попросить.
Узнаём, у какой машины кнопка:
1. Отметка на осмотре в приложении на телефоне: «Кнопка Start/Stop» / «Ключ в замке» — для этой машины, и запоминается
   для такой же машины (год, марка, модель, комплектация): следующие такие на Oceanside получат пометку сами.
2. Бесплатный расшифровщик VIN NHTSA (vPIC, vpic.nhtsa.dot.gov): поле «Keyless Ignition: Standard». Он знает не все
   машины: пусто — значит «неизвестно», а не «нет».
Камера заднего вида: отметка на осмотре («Камера есть» / «Камера нет»); подсказка — по VIN (vPIC) или по году
(с мая 2018 камера обязательна на всех новых машинах в США).
VIN уходят в NHTSA (это номера машин с торгов, не ваши данные); ответы хранятся в data/vin_features.json.
"""

from __future__ import annotations

import json
import re
import ssl
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .paths import DATA_DIR

CACHE_PATH = DATA_DIR / "vin_features.json"       # VIN → {"keyless": "Standard" | "", "camera": …, "at": …}
START_PATH = DATA_DIR / "start_types.json"         # «год марка модель комплектация» → {VIN: "push" | "key"} — с осмотра
VPIC_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVINValuesBatch/"
KEY_PLACES = ("Oceanside",)                         # где CarMax забирает ключи у машин с кнопкой Start/Stop
BATCH = 50                                          # VIN за один запрос (предел vPIC)
PUSH_TAG, KEY_TAG = "Кнопка Start/Stop", "Ключ в замке"
CAMERA_YES, CAMERA_NO = "Камера есть", "Камера нет"
_lock = threading.Lock()
_mem: dict[str, tuple[float, dict]] = {}


def _ctx() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _read(path: Path) -> dict:
    """JSON-файл с кэшем по времени изменения (читается при каждой строке окна — должно быть быстро)."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    hit = _mem.get(str(path))
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    _mem[str(path)] = (mtime, data if isinstance(data, dict) else {})
    return _mem[str(path)][1]


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def decode(vins: list[str], fetch=None, timeout: float = 40) -> int:
    """Расшифровать VIN в NHTSA (по 50 за запрос) и запомнить кнопку Start/Stop и камеру. → сколько VIN записано."""
    wanted = [v.upper() for v in dict.fromkeys(vins) if re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", str(v).upper())]
    if not wanted:
        return 0

    def http(batch: list[str]) -> list[dict]:
        body = urllib.parse.urlencode({"DATA": ";".join(batch), "format": "json"}).encode()
        req = urllib.request.Request(VPIC_URL, data=body, headers={"User-Agent": "lot-analyzer"})
        with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as resp:
            return json.loads(resp.read() or b"{}").get("Results") or []
    get = fetch or http
    done = 0
    for i in range(0, len(wanted), BATCH):
        try:
            results = get(wanted[i:i + BATCH])
        except Exception:  # нет сети / NHTSA не ответил — в следующий раз
            break
        with _lock:
            cache = dict(_read(CACHE_PATH))
            for d in results:
                vin = str(d.get("VIN") or "").upper()
                if not vin:
                    continue
                cache[vin] = {"keyless": str(d.get("KeylessIgnition") or ""), "camera": str(d.get("RearVisibilitySystem") or d.get("BackupCamera") or ""),
                              "at": time.time()}
                done += 1
            _write(CACHE_PATH, cache)
    return done


def model_key(row: dict) -> str:
    return " ".join(" ".join(str(row.get(k, "")) for k in ("year", "make", "model", "trim")).lower().split())


def learn(row: dict, tags: list[str]) -> None:
    """Отметка с осмотра «Кнопка Start/Stop» / «Ключ в замке» — запомнить для такой же машины."""
    kind = "push" if PUSH_TAG in tags else "key" if KEY_TAG in tags else ""
    vin, key = str(row.get("vin") or "").upper(), model_key(row)
    if not vin or not key.strip():
        return
    with _lock:
        data = _read(START_PATH)
        current = (data.get(key) or {}).get(vin, "")
        if current == kind:
            return
        data = json.loads(json.dumps(data))
        votes = data.setdefault(key, {})
        if kind:
            votes[vin] = kind
        else:
            votes.pop(vin, None)
        _write(START_PATH, data)


def start_type(row: dict) -> tuple[str, str]:
    """Запуск: ("push" | "key" | "", откуда знаем)."""
    mark = row.get("phone_start", "")
    if mark:
        return ("push" if mark == "кнопка" else "key"), "отмечено на осмотре"
    votes = list((_read(START_PATH).get(model_key(row)) or {}).values())
    if votes:
        push = votes.count("push")
        return ("push" if push * 2 >= len(votes) else "key"), "по осмотру такой же машины"
    if (_read(CACHE_PATH).get(str(row.get("vin") or "").upper()) or {}).get("keyless", "").lower() == "standard":
        return "push", "по VIN (NHTSA)"
    return "", ""


def at_key_place(row: dict) -> bool:
    text = f"{row.get('place', '')} {row.get('location', '')}".lower()
    return any(p.lower() in text for p in KEY_PLACES)


def key_note(row: dict) -> str:
    """«🔑 Нужен ключ …» — только для Oceanside и только у машин с кнопкой Start/Stop."""
    if not at_key_place(row):
        return ""
    kind, source = start_type(row)
    return f"🔑 Нужен ключ: кнопка Start/Stop — попросите ключ у CarMax ({source})" if kind == "push" else ""


def camera(row: dict) -> str:
    """Камера заднего вида: отметка с осмотра, иначе подсказка (по VIN или по году)."""
    mark = row.get("phone_camera", "")
    if mark:
        return mark
    if (_read(CACHE_PATH).get(str(row.get("vin") or "").upper()) or {}).get("camera", "").lower() == "standard":
        return "есть (по VIN)"
    try:
        if int(str(row.get("year") or "0")[:4]) >= 2019:
            return "есть (с 2018 обязательна)"
    except ValueError:
        pass
    return ""


def warm(get_rows, every: int = 900, first: int = 60) -> threading.Thread:
    """В фоне: VIN машин, которые вы будете смотреть (торги впереди), — в NHTSA, раз в 15 минут, только новые."""
    def loop() -> None:
        time.sleep(first)
        while True:
            try:
                cache = _read(CACHE_PATH)
                vins = [str(r.get("vin") or "").upper() for r in get_rows()]
                todo = [v for v in dict.fromkeys(vins) if v and v not in cache][:500]
                if todo:
                    decode(todo)
            except Exception:  # нет сети, файл занят — в следующий раз
                pass
            time.sleep(every)
    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return thread
