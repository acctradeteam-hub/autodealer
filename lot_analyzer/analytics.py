"""Своя аналитика: какой у машины KBB и за сколько такие уходят на каждой площадке.

Копится само, из того, что окно и так видит:
  * data/kbb_history.csv — каждый настоящий KBB (автопилот, вписанный в окне, страница KBB, заметка) вместе с
    MMR, пробегом и замечаниями аукциона на тот день;
  * data/auction_results.csv — итоги торгов (results.py); окно дописывает к ним KBB, MMR и замечания аукциона,
    известные до торгов (по VIN).

Из этого считается:
  * KBB ÷ MMR по модели (± 3 года) — предварительная цена продажи машины, у которой KBB ещё не смотрели;
  * цена продажи ÷ KBB и ÷ MMR по каждой площадке, отдельно без тяжёлых дефектов и с ними, — «средняя цена
    покупки на аукционе», когда своих продаж набирается достаточно.
"""

from __future__ import annotations

import csv
import datetime as dt
import re
import statistics
from pathlib import Path

from .market import HEAVY
from .paths import DATA_DIR

KBB_LOG_PATH = DATA_DIR / "kbb_history.csv"
KBB_FIELDS = ("vin", "date", "year", "make", "model", "trim", "miles", "kbb", "mmr", "auction", "location", "remarks", "source")

MIN_MODEL = 3          # KBB ÷ MMR по модели — от стольких машин
MIN_MAKE = 5
MIN_ALL = 10
MIN_SALES = 20         # своя «средняя цена покупки» площадки — от стольких продаж


def _num(text) -> float | None:
    text = re.sub(r"[$,\s]", "", str(text or ""))
    try:
        return float(text) if text else None
    except ValueError:
        return None


def model_base(model: str) -> str:
    """«Civic LX» / «CIVIC» → «civic»; «CR-V Hybrid» → «cr-v»; «Model 3» → «model 3»."""
    words = str(model or "").lower().replace("hybrid", "").split()
    if not words:
        return ""
    return " ".join(words[:2]) if words[0] == "model" else words[0]


# ---------------------------------------------------------------- журнал KBB

def load_kbb_log(path: Path | None = None) -> dict[str, dict]:
    path = path or KBB_LOG_PATH
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        return {r["vin"]: {k: v or "" for k, v in r.items()} for r in csv.DictReader(handle) if r.get("vin")}


def save_kbb_log(log: dict[str, dict], path: Path | None = None) -> None:
    path = path or KBB_LOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=KBB_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for rec in sorted(log.values(), key=lambda r: (r["date"], r["vin"])):
            writer.writerow(rec)


def record_kbb(rows: list[dict], log: dict[str, dict]) -> bool:
    """Дописывает в журнал настоящие KBB из строк окна. True — журнал изменился."""
    changed = False
    today = dt.date.today().isoformat()
    for row in rows:
        vin = row.get("vin", "")
        kbb = _num(row.get("kbb_private_party_usd"))
        if not vin or not kbb or row.get("kbb_from_window_field"):
            continue
        old = log.get(vin)
        mmr = _num(row.get("mmr_adjusted_usd"))
        rec = {"vin": vin, "date": row.get("kbb_date") or (old or {}).get("date") or today, "year": row.get("year", ""),
               "make": row.get("make", ""), "model": row.get("model", ""), "trim": row.get("trim", ""),
               "miles": re.sub(r"\D", "", str(row.get("odometer_miles", ""))), "kbb": f"{kbb:.0f}",
               "mmr": f"{mmr:.0f}" if mmr else (old or {}).get("mmr", ""), "auction": row.get("auction", ""),
               "location": row.get("location", ""), "remarks": row.get("auction_notes") or row.get("defects", "") or "",
               "source": (row.get("kbb_entered") or "заметка / список")[:80]}
        if old is None or any(old.get(k, "") != rec[k] for k in ("kbb", "mmr", "miles", "remarks")):
            log[vin] = rec
            changed = True
    return changed


# ---------------------------------------------------------------- KBB по MMR: предварительная цена продажи

class OwnKbb:
    """Оценщик для bid.apply_to_rows: KBB ≈ MMR × (ваше KBB ÷ MMR у той же модели ± 3 года → марки → всех)."""

    def __init__(self, log: dict[str, dict]):
        self.pairs = []
        for rec in log.values():
            kbb, mmr = _num(rec.get("kbb")), _num(rec.get("mmr"))
            if kbb and mmr and 0.5 < kbb / mmr < 4:
                self.pairs.append((rec["make"].lower(), model_base(rec["model"]), int(rec["year"]) if str(rec["year"]).isdigit() else 0, kbb / mmr, rec["vin"]))
        self.mmr_by_vin: dict[str, float] = {}

    def copy(self) -> "OwnKbb":
        return self

    def add_rows(self, rows: list[dict]) -> None:
        for row in rows:
            mmr = _num(row.get("mmr_adjusted_usd")) or _num(row.get("wholesale_usd"))
            if row.get("vin") and mmr:
                self.mmr_by_vin[row["vin"]] = mmr

    def ratio(self, make: str, model: str, year: int | None, vin: str = "") -> tuple[float, int, str] | None:
        make, base = make.lower(), model_base(model)
        pairs = [p for p in self.pairs if p[4] != vin]
        for label, minimum, pick in (
                (f"{model} ±3 года", MIN_MODEL, lambda p: p[0] == make and p[1] == base and (not year or abs(p[2] - year) <= 3)),
                (f"марка {make.title()}", MIN_MAKE, lambda p: p[0] == make),
                ("все ваши машины", MIN_ALL, lambda p: True)):
            found = [p[3] for p in pairs if pick(p)]
            if len(found) >= minimum:
                return statistics.median(found), len(found), label
        return None

    def estimate(self, make: str, model: str, year: int | None, miles: float | None, vin: str = ""):
        from .kbb import Estimate

        mmr = self.mmr_by_vin.get(vin)
        found = self.ratio(make, model, year, vin) if mmr else None
        if not found:
            return None
        share, n, label = found
        return Estimate(value=mmr * share, source=f"по вашей базе: KBB ≈ MMR × {share:.2f} ({label}, {n} машин)", n=n, spread=0.0)


# ---------------------------------------------------------------- площадки: за сколько уходят

def place_of(rec: dict) -> str:
    """Площадка итога: «CarMax Chino» → «Chino»; Manheim — название площадки."""
    return rec["auction"].replace("CarMax ", "", 1) if rec["auction"].startswith("CarMax ") else rec["auction"]


def joined_sales(history: dict[str, dict], log: dict[str, dict]) -> list[dict]:
    """Продажи с KBB / MMR и замечаниями (из итога или из журнала KBB по VIN)."""
    out = []
    for rec in history.values():
        price = _num(rec.get("price"))
        if rec.get("outcome") != "Sold" or not price or price < 500:
            continue
        seen = log.get(rec.get("vin", "")) or {}
        kbb = _num(rec.get("kbb")) or _num(seen.get("kbb"))
        mmr = _num(rec.get("mmr")) or _num(seen.get("mmr"))
        remarks = rec.get("remarks") or seen.get("remarks") or ""
        out.append({**rec, "place": place_of(rec), "price_f": price, "kbb_f": kbb, "mmr_f": mmr, "remarks": remarks,
                    "heavy": bool(HEAVY.search(remarks))})
    return out


def _summary(values: list[float]) -> dict:
    values = sorted(values)
    n = len(values)
    return {"n": n, "median": round(statistics.median(values), 3),
            "curve": [[round(values[min(n - 1, int(q * n))], 3), q] for q in (0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95)]}


def place_stats(sales: list[dict]) -> dict[str, dict]:
    """По площадке: цена ÷ KBB и ÷ MMR, отдельно без тяжёлых дефектов (clean) и с ними (heavy)."""
    out: dict[str, dict] = {}
    for sale in sales:
        for base in ("kbb", "mmr"):
            if sale[f"{base}_f"]:
                kind = "heavy" if sale["heavy"] else "clean"
                out.setdefault(sale["place"], {}).setdefault(f"{base}_{kind}", []).append(sale["price_f"] / sale[f"{base}_f"])
    return {place: {key: _summary(vals) for key, vals in groups.items()} for place, groups in out.items()}


def market_overrides(sales: list[dict]) -> dict[str, dict]:
    """Для bid.market_config: доли «цена ÷ KBB» площадок, где своих продаж ≥ MIN_SALES."""
    out = {}
    for place, groups in place_stats(sales).items():
        own = {}
        for kind in ("clean", "heavy"):
            g = groups.get(f"kbb_{kind}")
            if g and g["n"] >= (MIN_SALES if kind == "clean" else MIN_SALES // 2):
                own[f"kbb_{kind}"] = g["median"]
                own[f"kbb_{kind}_curve"] = g["curve"]
                own[f"n_kbb_{kind}"] = g["n"]
        if own:
            out[place] = own
    return out


def hybrid_factor(sales: list[dict], costs: dict, minimum: int = 8) -> dict:
    """Насколько гибриды с большим пробегом уходят дешевле обычных машин с таким же пробегом:
    медиана «цена ÷ KBB» гибридов ÷ медиана обычных (без тяжёлых дефектов). Пусто — своих гибридов мало."""
    from .bid import is_hybrid, is_electric

    high = float((costs.get("hybrid") or {}).get("high_miles", 130000))
    hybrids, others = [], []
    for sale in sales:
        if not sale["kbb_f"] or sale["heavy"] or _num(sale.get("miles")) is None or _num(sale.get("miles")) < high:
            continue
        row = {"make": sale.get("make", ""), "model": sale.get("model", "")}
        if is_electric(row):
            continue
        (hybrids if is_hybrid(row) else others).append(sale["price_f"] / sale["kbb_f"])
    if len(hybrids) < minimum or len(others) < minimum:
        return {}
    factor = statistics.median(hybrids) / statistics.median(others)
    return {"market_factor": round(min(1.0, factor), 2), "market_factor_n": len(hybrids)}


def model_stats(sales: list[dict], log: dict[str, dict]) -> list[dict]:
    """По моделям: сколько машин в базе, KBB ÷ MMR, цена продажи ÷ KBB (без тяжёлых дефектов)."""
    groups: dict[tuple, dict] = {}
    for rec in log.values():
        key = (rec["make"].title(), model_base(rec["model"]).upper() if len(model_base(rec["model"])) <= 4 else model_base(rec["model"]).title())
        g = groups.setdefault(key, {"kbb": 0, "kbb_mmr": [], "sold_kbb": []})
        g["kbb"] += 1
        kbb, mmr = _num(rec["kbb"]), _num(rec["mmr"])
        if kbb and mmr:
            g["kbb_mmr"].append(kbb / mmr)
    for sale in sales:
        key = (sale["make"].title(), model_base(sale["model"]).upper() if len(model_base(sale["model"])) <= 4 else model_base(sale["model"]).title())
        g = groups.setdefault(key, {"kbb": 0, "kbb_mmr": [], "sold_kbb": []})
        if sale["kbb_f"] and not sale["heavy"]:
            g["sold_kbb"].append(sale["price_f"] / sale["kbb_f"])
    out = []
    for (make, model), g in groups.items():
        out.append({"make": make, "model": model, "kbb": g["kbb"], "kbb_mmr_n": len(g["kbb_mmr"]),
                    "kbb_mmr": round(statistics.median(g["kbb_mmr"]), 2) if g["kbb_mmr"] else None,
                    "sold_kbb_n": len(g["sold_kbb"]), "sold_kbb": round(statistics.median(g["sold_kbb"]), 2) if g["sold_kbb"] else None})
    return sorted(out, key=lambda m: -(m["kbb"] + m["sold_kbb_n"]))


# ---------------------------------------------------------------- страница «Наша аналитика»

def render(history: dict[str, dict], log: dict[str, dict]) -> str:
    from html import escape as esc

    sales = joined_sales(history, log)
    places = place_stats(sales)
    used = market_overrides(sales)
    from .results import stats as mmr_stats
    mmr_used = mmr_stats(history)
    dates = sorted({r["date"] for r in history.values() if r.get("date")})
    pct = lambda g: f"{g['median']:.2f} <span class=m>({g['n']})</span>" if g else "—"
    place_rows = []
    for place in sorted(places, key=lambda p: -sum(g["n"] for g in places[p].values())):
        g = places[place]
        n = sum(1 for s_ in sales if s_["place"] == place)
        status = ("в расчёте: от KBB" if place in used else "в расчёте: от MMR" if mmr_used.get(place, {}).get("enough") else
                  f"копим: нужно ≥ {MIN_SALES} продаж с KBB или MMR")
        place_rows.append(f"<tr><td>{esc(place)}</td><td class=n>{n}</td><td class=n>{pct(g.get('kbb_clean'))}</td><td class=n>{pct(g.get('kbb_heavy'))}</td>"
                          f"<td class=n>{pct(g.get('mmr_clean'))}</td><td class=n>{pct(g.get('mmr_heavy'))}</td><td>{status}</td></tr>")
    model_rows = [f"<tr><td>{esc(m['make'])} {esc(m['model'])}</td><td class=n>{m['kbb']}</td>"
                  f"<td class=n>{m['kbb_mmr'] if m['kbb_mmr'] else '—'} <span class=m>({m['kbb_mmr_n']})</span></td>"
                  f"<td class=n>{m['sold_kbb'] if m['sold_kbb'] else '—'} <span class=m>({m['sold_kbb_n']})</span></td></tr>"
                  for m in model_stats(sales, log)[:80]]
    with_kbb = sum(1 for s_ in sales if s_["kbb_f"])
    with_remarks = sum(1 for s_ in sales if s_["remarks"])
    return f"""<!doctype html><html lang=ru><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Наша аналитика</title><style>
:root{{--bg:#f6f6f3;--fg:#1c1c1a;--m:#62625c;--card:#fff;--line:#e2e2dc}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151514;--fg:#ececea;--m:#a2a29c;--card:#1f1f1d;--line:#34342f}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}}main{{max-width:1100px;margin:0 auto;padding:20px 16px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:12px 0;overflow-x:auto}}
table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left}}.n{{text-align:right;white-space:nowrap}}.m{{color:var(--m);font-size:12px}}
</style></head><body><main><h1>Наша аналитика</h1>
<div class=m><a href="/">← в окно</a> · база копится сама из файлов в «Загрузках»</div>
<div class=card><b>В базе:</b> итогов торгов {len(history)} ({len(sales)} продаж; с KBB — {with_kbb}, с замечаниями аукциона — {with_remarks})
{f"за {dates[0]} — {dates[-1]}" if dates else ""} · машин с настоящим KBB: {len(log)}
<div class=m>Числа — медиана «цена продажи ÷ KBB» или «÷ MMR», в скобках — сколько машин. Без тяжёлых дефектов / с тяжёлыми
(мотор, коробка, рама, salvage, не на ходу — по замечаниям аукциона).</div></div>
<div class=card><h3>Площадки: за сколько уходят машины</h3><table><tr><th>Площадка</th><th class=n>Продаж</th><th class=n>÷ KBB, чистые</th><th class=n>÷ KBB, тяжёлые</th>
<th class=n>÷ MMR, чистые</th><th class=n>÷ MMR, тяжёлые</th><th>В расчёте?</th></tr>{"".join(place_rows) or "<tr><td colspan=7 class=m>пока пусто</td></tr>"}</table></div>
<div class=card><h3>Модели</h3><div class=m>KBB ÷ MMR — по нему считается предварительная цена продажи машин без KBB (от {MIN_MODEL} машин модели ± 3 года, иначе марка — от {MIN_MAKE}, иначе все — от {MIN_ALL}). Продано ÷ KBB — за сколько эта модель уходит на торгах без тяжёлых дефектов.</div>
<table><tr><th>Модель</th><th class=n>KBB в базе</th><th class=n>KBB ÷ MMR</th><th class=n>Продано ÷ KBB</th></tr>{"".join(model_rows) or "<tr><td colspan=4 class=m>пока пусто</td></tr>"}</table></div>
<div class=card><h3>Как пополнять</h3><ol>
<li>До торгов: сохраняйте списки закладкой и получайте KBB — каждый KBB, MMR и замечания аукциона записываются в базу.</li>
<li>После торгов: положите в «Загрузки» итоги — Manheim: CSV дорожки Simulcast или PDF Post-Sale Results; CarMax: выгрузка результатов (JSON / CSV Velocicast). Окно само соединит их по VIN с тем, что было известно до торгов.</li>
<li>Когда у площадки набирается ≥ {MIN_SALES} продаж с KBB, её «средняя цена покупки» считается по вашим данным, а не по общей формуле.</li></ol></div>
</main></body></html>"""
