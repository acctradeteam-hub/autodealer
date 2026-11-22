"""Ликвидность лота: как быстро такую машину берут и сколько за неё дают дилеры.

— Ваши итоги торгов (CarMax и Manheim, data/auction_results.csv) за days дней: сколько машин этой модели выставлено
  и продано. Много продаж — модель ходовая, дилеры её берут.
— MMR (Manheim Market Report, оптовая цена у дилеров) ÷ KBB Private Party: чем ближе опт к рознице, тем охотнее её
  покупают дилеры (высокий спрос); MMR сильно ниже KBB — спрос слабый или машина «тяжёлая».
MMR берётся со страниц Manheim, из заметок («MMR $12,500») или из поля MMR в окне (data/mmr_manual.json).
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time

from .paths import DATA_DIR

MMR_PATH = DATA_DIR / "mmr_manual.json"     # MMR, вписанный в окне: VIN → {usd, at}
DAYS = 60


def _num(value) -> float:
    digits = re.sub(r"[^\d.]", "", str(value or ""))
    try:
        return float(digits) if digits else 0.0
    except ValueError:
        return 0.0


def _key(make: str, model: str) -> str:
    words = re.sub(r"[^a-z0-9\- ]", " ", str(model or "").lower()).split()
    return f"{str(make or '').strip().lower()}|{words[0] if words else ''}"


def load_mmr() -> dict[str, dict]:
    try:
        return json.loads(MMR_PATH.read_text(encoding="utf-8")) if MMR_PATH.exists() else {}
    except (OSError, ValueError):
        return {}


def save_mmr(vin: str, usd) -> bool:
    """MMR из окна (с mmr.manheim.com или приложения Manheim): число — сохранить, пусто — убрать."""
    vin = str(vin or "").strip().upper()
    if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin):
        return False
    data = load_mmr()
    value = _num(usd)
    if value >= 300:
        data[vin] = {"usd": round(value), "at": time.time()}
    else:
        data.pop(vin, None)
    MMR_PATH.parent.mkdir(parents=True, exist_ok=True)
    MMR_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return True


def attach_mmr(rows: list[dict]) -> None:
    """MMR из окна — к машинам, у которых своего MMR нет (страницы Manheim дают его сами). До расчёта."""
    data = load_mmr()
    for row in rows:
        rec = data.get(str(row.get("vin", "")).upper())
        row["mmr_manual"] = ""
        if rec and not _num(row.get("mmr_adjusted_usd")):
            row["mmr_adjusted_usd"] = str(rec["usd"])
            row["mmr_manual"] = "1"


def model_stats(history: dict[str, dict], today: dt.date | None = None, days: int = DAYS) -> dict[str, dict]:
    """По модели за days дней: выставлено, продано (ваши итоги торгов)."""
    today = today or dt.date.today()
    since = (today - dt.timedelta(days=days)).isoformat()
    out: dict[str, dict] = {}
    for rec in history.values():
        if str(rec.get("date") or "") < since or rec.get("outcome") in ("Not Run", "нет итога", ""):
            continue
        st = out.setdefault(_key(rec.get("make", ""), rec.get("model", "")), {"offered": 0, "sold": 0})
        st["offered"] += 1
        st["sold"] += rec.get("outcome") == "Sold"
    return out


def attach(rows: list[dict], history: dict[str, dict], today: dt.date | None = None) -> None:
    """Ликвидность — к машинам: «высокая / средняя / низкая» и из чего сложилась (после расчёта: нужен KBB или прикидка)."""
    stats = model_stats(history, today)
    for row in rows:
        row["liq"] = row["liq_note"] = ""
        st = stats.get(_key(row.get("make", ""), row.get("model", "")))
        mmr = _num(row.get("mmr_adjusted_usd"))
        kbb = _num(row.get("kbb_private_party_usd"))          # только настоящий KBB: прикидка для этого слишком грубая
        ratio = mmr / kbb if mmr and kbb else None
        days, rivals = _num(row.get("cg_days")), _num(row.get("cg_n"))
        if not st and ratio is None and not days:
            continue
        score, notes = 0, []
        if st:
            rate = st["sold"] / st["offered"] if st["offered"] else 0
            score += 2 if st["sold"] >= 12 and rate >= 0.9 else 1 if st["sold"] >= 5 and rate >= 0.8 else -1 if st["sold"] < 3 or rate < 0.7 else 0
            notes.append(f"на ваших торгах за {DAYS} дней: продано {st['sold']} из {st['offered']} ({rate:.0%})")
        if ratio is not None:
            score += 1 if ratio >= 0.72 else -1 if ratio <= 0.55 else 0
            notes.append(f"MMR ${mmr:,.0f} ÷ KBB ${kbb:,.0f} = {ratio:.2f}" + (" (MMR вписан вручную)" if row.get("mmr_manual") else ""))
        if days:
            score += 1 if days <= 30 else -1 if days >= 60 else 0
            notes.append(f"CarGurus: {rivals:.0f} похожих в 50 милях, в продаже ~{days:.0f} дн.")
        row["liq"] = "высокая" if score >= 2 else "низкая" if score < 0 else "средняя"
        row["liq_note"] = "; ".join(notes)
