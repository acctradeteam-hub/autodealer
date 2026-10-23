"""«Wait Pictures»: машины, у которых при сохранении списка не было фото, — ждём, пока CarMax их добавит.

data/photo_wait.json: VIN → {"since": день, когда увидели без фото, "arrived": день, когда фото появились}.
Пока фото нет — у машины пометка «📷 Wait Pictures»; когда на свежей странице фото появились —
«📷 Фото появились 10/23» (7 дней), и такие машины видны отдельной кнопкой, чтобы пересмотреть.
"""

from __future__ import annotations

import json
import time

from .paths import DATA_DIR

PATH = DATA_DIR / "photo_wait.json"
SHOW_ARRIVED_DAYS = 7
KEEP_DAYS = 30


def load() -> dict:
    try:
        return json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else {}
    except (OSError, ValueError):
        return {}


def save(data: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")


def _day(seconds: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(seconds))


def update(rows: list[dict], data: dict, now: float | None = None) -> bool:
    """По самым свежим строкам машин: нет фото — ждём; фото появились — отмечаем день. Строки получают
    photo_wait (с какого дня ждём) и photo_arrived (когда появились)."""
    now = now or time.time()
    today, show_since, keep_since = _day(now), _day(now - SHOW_ARRIVED_DAYS * 86400), _day(now - KEEP_DAYS * 86400)
    changed = False
    for vin in [v for v, rec in data.items() if (rec.get("arrived") or rec.get("since", "")) < keep_since]:
        del data[vin]
        changed = True
    for row in rows:
        vin = row.get("vin", "")
        if not vin:
            continue
        rec = data.get(vin)
        if row.get("no_photos") == "да":
            if not rec or rec.get("arrived"):            # снова без фото — ждём заново
                data[vin] = rec = {"since": today, "arrived": ""}
                changed = True
            row["photo_wait"] = rec["since"]
        elif rec:
            if not rec.get("arrived"):
                rec["arrived"] = today
                changed = True
            if rec["arrived"] >= show_since:
                row["photo_arrived"] = rec["arrived"]
    return changed
