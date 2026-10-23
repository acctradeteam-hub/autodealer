"""Обучение предварительного KBB: насколько прикидка отличалась от настоящего KBB с kbb.com.

Пока у машины нет настоящего KBB, программа запоминает её прикидку (последнюю, что вы видели в окне).
Когда приходит настоящий KBB («уточнить» / KBB 15+5+5+5), пара «прикидка → KBB» записывается в
data/kbb_learning.json: у машины в окне видно «прикидка была ≈ $X (−8%)», а по накопленным парам
следующие прикидки поправляются (своя поправка у модели, если по ней уточнено от 4 машин, иначе общая).
"""

from __future__ import annotations

import json
import statistics
import time

from .paths import DATA_DIR

PATH = DATA_DIR / "kbb_learning.json"
MIN_ALL, MIN_MODEL = 5, 4            # сколько уточнённых машин нужно для общей поправки и для поправки модели
LIMITS = (0.8, 1.2)                  # поправка не больше ±20%


def load() -> dict:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else {}
    except (OSError, ValueError):
        data = {}
    return {"prelim": data.get("prelim") or {}, "learned": data.get("learned") or {}}


def save(data: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")


def _key(make: str, model: str) -> str:
    from .kbb import model_key

    return "|".join(model_key(make, model))


def factors(learned: dict) -> dict[str, tuple[float, int]]:
    """Поправка «настоящий KBB ÷ прикидка без поправки»: общая («*») и по моделям."""
    groups: dict[str, list[float]] = {}
    for rec in learned.values():
        try:
            ratio = float(rec["kbb"]) / float(rec["raw"])
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
        if 0.3 < ratio < 3:
            groups.setdefault("*", []).append(ratio)
            groups.setdefault(rec.get("key", ""), []).append(ratio)
    out = {}
    for key, values in groups.items():
        if len(values) >= (MIN_ALL if key == "*" else MIN_MODEL):
            out[key] = (min(max(statistics.median(values), LIMITS[0]), LIMITS[1]), len(values))
    return out


def correction(table: dict[str, tuple[float, int]], make: str, model: str) -> tuple[float, str]:
    """Множитель для прикидки этой машины и подпись, откуда он."""
    own = table.get(_key(make, model))
    if own:
        return own[0], f"поправка по вашим уточнениям этой модели × {own[0]:.2f} ({own[1]} машин)"
    common = table.get("*")
    if common:
        return common[0], f"поправка по вашим уточнениям × {common[0]:.2f} ({common[1]} машин)"
    return 1.0, ""


def update(rows: list[dict], data: dict) -> bool:
    """Запоминает прикидки машин без KBB; у машин, которым пришёл настоящий KBB, — пару «прикидка → KBB»."""
    prelim, learned = data["prelim"], data["learned"]
    today = time.strftime("%Y-%m-%d")
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - 45 * 86400))
    stale = [vin for vin, rec in prelim.items() if rec.get("date", "") < since]    # старые прикидки (машина ушла) — не копим
    for vin in stale:
        del prelim[vin]
    changed = bool(stale)
    for row in rows:
        vin = row.get("vin", "")
        if not vin:
            continue
        real = row.get("kbb_private_party_usd") and not row.get("kbb_from_window_field")
        if real and vin in prelim and vin not in learned:
            was = prelim.pop(vin)
            learned[vin] = {**was, "kbb": float(row["kbb_private_party_usd"]), "kbb_date": today}
            changed = True
        elif not real and row.get("kbb_estimate_usd"):
            est, raw = float(row["kbb_estimate_usd"]), float(row.get("kbb_estimate_raw_usd") or row["kbb_estimate_usd"])
            old = prelim.get(vin) or {}
            if old.get("est") != est or old.get("raw") != raw:
                prelim[vin] = {"est": est, "raw": raw, "date": today, "key": _key(row.get("make", ""), f"{row.get('model', '')} {row.get('trim', '')}"),
                               "car": " ".join(str(row.get(k, "")) for k in ("year", "make", "model")).strip(), "miles": row.get("odometer_miles", "")}
                changed = True
        rec = learned.get(vin)
        if real and rec and rec.get("est"):
            row["kbb_prelim_usd"] = f"{float(rec['est']):.0f}"
            row["kbb_prelim_pct"] = f"{(float(rec['est']) - float(rec['kbb'])) / float(rec['kbb']) * 100:+.0f}"
    return changed


def summary(learned: dict) -> dict:
    """Для окна: сколько машин уточнено, насколько ошибалась прикидка (медиана) и в какую сторону."""
    errs = []
    for rec in learned.values():
        try:
            errs.append((float(rec["est"]) - float(rec["kbb"])) / float(rec["kbb"]))
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
    if not errs:
        return {"n": 0}
    common = factors(learned).get("*")
    return {"n": len(errs), "bias": round(statistics.median(errs) * 100), "abs": round(statistics.median(abs(e) for e in errs) * 100),
            "factor": round(common[0], 2) if common else None}
