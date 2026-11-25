"""Опт на Manheim: MMR для машин CarMax и проверка идеи «купить на CarMax → сдать на Manheim (California / Southern California)».

MMR машины CarMax:
  * точный — вписанный в окне (поле MMR у машины) — он главнее;
  * прикидка — по похожим машинам из ваших выгрузок поиска Manheim (у каждой там есть MMR): та же марка и модель, тот же
    год (комплектация — если совпадает), иначе год ±1 (6% в год), пробег — лог-линейная поправка по группе. Наблюдения
    копятся в data/mmr_obs.json (каждая выгрузка читается один раз) и берутся за obs_days дней; чем больше выгрузок —
    тем точнее. Проверка 25.11 на одной выгрузке: у 77 машин с CarMax, которые потом нашлись на Manheim, ошибка в среднем
    15% (с совпавшей комплектацией — 10%). Для решения по конкретной машине — впишите точный MMR.
Перепродажа на Manheim:
  выручка = MMR × «цена ÷ MMR» площадки (ваши итоги Manheim: California ≈ 1,00, Southern California ≈ 1,03, по ценам);
  расходы = сбор продавца Manheim + отчёт о состоянии (CR) + доставка на Manheim + подготовка + резерв (% выручки)
            + ремонт по замечаниям CarMax и доставка с площадки CarMax (как в основном расчёте) + сбор покупателя CarMax;
  потолок для перепродажи — цена на CarMax, при которой остаётся min_profit. Настройки — costs.json: manheim_flip
  (сборы продавца Manheim — предположение, сверьте со своей сеткой сборов).
"""

from __future__ import annotations

import collections
import csv
import datetime as dt
import io
import json
import math
import re
import statistics
import time
from pathlib import Path

from . import manheim_csv, resale
from .bid import auction_fee, estimate_recon
from .normalize import parse_money
from .paths import DATA_DIR

OBS_PATH = DATA_DIR / "mmr_obs.json"
DEFAULTS = {"target": "Manheim California", "sale_to_mmr": None, "obs_days": 90, "min_comps": 3,
            "sell_fee_tiers": [[1000, 150], [5000, 225], [10000, 275], [15000, 325], [25000, 400], [1e9, 450]],
            "cr_fee_usd": 75, "transport_to_manheim_usd": 175, "prep_usd": 150, "reserve_pct": 0.03, "min_profit_usd": 500,
            "use_recon": True}


def config(costs: dict) -> dict:
    return {**DEFAULTS, **(costs.get("manheim_flip") or {})}


def _model(text: str) -> str:
    w = str(text or "").lower().split()
    return re.sub(r"[^a-z0-9]", "", w[0]) if w else ""


def _make(text: str) -> str:
    w = str(text or "").lower().replace("mercedes benz", "mercedes-benz").split()
    return w[0] if w else ""


# ---------------------------------------------------------------- наблюдения MMR (выгрузки Manheim)

def load_obs() -> dict:
    try:
        data = json.loads(OBS_PATH.read_text(encoding="utf-8")) if OBS_PATH.exists() else {}
    except (OSError, ValueError):
        data = {}
    data.setdefault("files", {})
    data.setdefault("cars", {})
    return data


def refresh_obs(folders: list[Path], now: float | None = None, days: int = 90) -> dict:
    """Новые выгрузки Manheim → наблюдения MMR (VIN → год, марка, модель, комплектация, пробег, MMR, CR, день)."""
    now = time.time() if now is None else now
    data = load_obs()
    changed = False
    for path in resale.exports(folders, now):
        st = path.stat()
        stamp = f"{st.st_size}:{st.st_mtime:.0f}"
        if data["files"].get(path.name) == stamp:
            continue
        data["files"][path.name] = stamp
        changed = True
        day = dt.date.fromtimestamp(st.st_mtime).isoformat()
        try:
            text = manheim_csv._decode(path.read_bytes()).replace("\r\n", "\n").replace("\r", "\n")
            records = list(csv.DictReader(io.StringIO(text)))
        except (OSError, csv.Error):
            continue
        for r in records:
            vin = (r.get("Vin") or "").strip().upper()
            mmr, miles = parse_money(r.get("MMR")), parse_money(r.get("Odometer Value"))
            year = str(r.get("Year") or "").strip()
            if len(vin) != 17 or not mmr or not miles or not year.isdigit():
                continue
            if (r.get("Odometer Units") or "mi").lower().startswith("km"):
                miles = miles / 1.609
            data["cars"][vin] = {"y": int(year), "make": _make(r.get("Make")), "model": _model(r.get("Model")),
                                 "trim": str(r.get("Trim") or "").strip().lower(), "miles": round(miles), "mmr": round(mmr),
                                 "cr": str(r.get("Condition Report Grade") or "").strip(), "day": day}
    cutoff = dt.date.fromtimestamp(now - days * 86400).isoformat()
    old = [v for v, o in data["cars"].items() if o.get("day", "") < cutoff]
    for v in old:
        del data["cars"][v]
    if changed or old:
        OBS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = OBS_PATH.with_name(OBS_PATH.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(OBS_PATH)
    return data


def index(obs: dict) -> dict[tuple[str, str], list[dict]]:
    out: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for vin, o in obs.get("cars", {}).items():
        out[(o["make"], o["model"])].append({**o, "vin": vin})
    return out


def estimate(year, make: str, model: str, trim: str, miles, idx: dict, exclude: str = "", min_comps: int = 3) -> tuple[float | None, int, str]:
    """MMR ≈ по похожим машинам: (значение, сколько похожих, как подобраны)."""
    try:
        y, mi = int(str(year)[:4]), float(parse_money(miles) or 0)
    except ValueError:
        return None, 0, ""
    if mi < 1000:                                         # пробег не указан («1») — не оцениваем
        return None, 0, ""
    group = [o for o in idx.get((_make(make), _model(model)), []) if abs(o["miles"] - mi) <= 60000]   # похожие по пробегу
    trim = str(trim or "").strip().lower()
    levels = (("тот же год и комплектация", lambda o: o["y"] == y and trim and o["trim"] == trim),
              ("тот же год", lambda o: o["y"] == y),
              ("год ±1", lambda o: abs(o["y"] - y) <= 1))
    for label, keep in levels:
        comp = [o for o in group if o["vin"] != exclude and keep(o)]
        if len(comp) < min_comps:
            continue
        xs = [o["miles"] / 1000 for o in comp]
        ys = [math.log(o["mmr"] * (1.06 ** (y - o["y"]))) for o in comp]
        slope = -0.004                                    # −0,4% на 1000 миль, если по группе не видно
        if len(comp) >= 6 and max(xs) - min(xs) > 30:
            mx, my = statistics.mean(xs), statistics.mean(ys)
            sxx = sum((x - mx) ** 2 for x in xs)
            if sxx:
                slope = max(-0.01, min(0.0, sum((x - mx) * (v - my) for x, v in zip(xs, ys)) / sxx))
        adj = [v + slope * (mi / 1000 - x) for x, v in zip(xs, ys)]
        weights = [1 / (1 + abs(mi / 1000 - x) / 20) for x in xs]
        return math.exp(sum(a * w for a, w in zip(adj, weights)) / sum(weights)), len(comp), label
    return None, 0, ""


# ---------------------------------------------------------------- перепродажа

def sale_ratio(mmr: float, stats: dict, cfg: dict) -> tuple[float, str]:
    """«Цена ÷ MMR» на выбранной площадке Manheim по вашим итогам (по ценовым диапазонам); нет итогов — 1,00."""
    if cfg.get("sale_to_mmr"):
        return float(cfg["sale_to_mmr"]), "costs.json"
    s = stats.get(cfg["target"]) or {}
    for upper, ratio in s.get("bands") or []:
        if mmr < float(upper):
            return float(ratio), f"ваши итоги {cfg['target']}"
    if s.get("median"):
        return float(s["median"]), f"ваши итоги {cfg['target']}"
    return 1.0, "нет итогов — 1,00"


def _tier(value: float, tiers: list) -> float:
    for upper, fee in tiers:
        if value <= float(upper):
            return float(fee)
    return float(tiers[-1][1]) if tiers else 0.0


def _items(row: dict) -> dict[str, float]:
    """Ремонт и доставка с площадки — из основного расчёта (статьи прибыли)."""
    out = {"recon": 0.0, "transport": 0.0}
    try:
        items = json.loads(row.get("calc_profit_items") or "[]")
    except ValueError:
        items = []
    for label, amount in items:
        if str(label).startswith("Ремонт"):
            out["recon"] = -float(amount)
        elif str(label).startswith("Доставка"):
            out["transport"] = -float(amount)
    return out


def flip(mmr: float, auction: str, costs: dict, stats: dict, recon: float = 0.0, transport: float = 0.0,
         price: float | None = None) -> dict:
    """Перепродажа на Manheim: выручка, расходы, потолок цены на CarMax, прибыль при цене price."""
    cfg = config(costs)
    ratio, ratio_src = sale_ratio(mmr, stats, cfg)
    proceeds = mmr * ratio
    sell = _tier(proceeds, cfg["sell_fee_tiers"])
    fixed = {"Сбор продавца Manheim": sell, "Отчёт о состоянии (CR)": float(cfg["cr_fee_usd"]),
             "Доставка на Manheim": float(cfg["transport_to_manheim_usd"]), "Подготовка": float(cfg["prep_usd"]),
             f"Резерв {float(cfg['reserve_pct']):.0%}": proceeds * float(cfg["reserve_pct"])}
    if cfg.get("use_recon") and recon:
        fixed["Ремонт по замечаниям CarMax"] = recon
    if transport:
        fixed["Доставка с площадки CarMax"] = transport
    total = sum(fixed.values())
    target = float(cfg["min_profit_usd"])
    ceiling = proceeds - total - target
    for _ in range(5):                                    # сбор покупателя CarMax зависит от цены
        ceiling = proceeds - total - target - auction_fee(max(ceiling, 0), auction, costs)
    out = {"mmr": round(mmr), "ratio": ratio, "ratio_src": ratio_src, "proceeds": round(proceeds), "costs": {k: round(v) for k, v in fixed.items()},
           "ceiling": max(0, int(ceiling // 25 * 25)), "target": target}
    if price:
        fee = auction_fee(price, auction, costs)
        out["profit"] = round(proceeds - total - price - fee)
        out["buy_fee"] = round(fee)
    return out


def attach(rows: list[dict], costs: dict, folders: list[Path], history: dict[str, dict] | None = None) -> None:
    """Машинам CarMax: MMR (точный или прикидка), потолок перепродажи на Manheim, прибыль при средней цене покупки."""
    from . import results
    cfg = config(costs)
    obs = refresh_obs(folders, days=int(cfg["obs_days"]))
    idx = index(obs)
    stats = results.stats(history if history is not None else results.load_history())
    for row in rows:
        for k in ("mmr_est_usd", "mmr_est_n", "mmr_est_note", "flip_mmr_usd", "flip_ceiling_usd", "flip_profit_usd", "flip_note"):
            row[k] = ""
        if not str(row.get("auction", "")).lower().startswith("carmax"):
            continue
        est, n, how = estimate(row.get("year"), row.get("make", ""), row.get("model", ""), row.get("trim", ""), row.get("odometer_miles"),
                               idx, min_comps=int(cfg["min_comps"]))
        if est:
            row["mmr_est_usd"], row["mmr_est_n"] = f"{est:.0f}", str(n)
            row["mmr_est_note"] = f"≈ MMR по {n} похожим машинам из ваших выгрузок Manheim ({how}); точность ±15%"
        exact = parse_money(row.get("mmr_adjusted_usd")) if row.get("mmr_manual") else None
        mmr = exact or est
        if not mmr:
            continue
        extra = _items(row)
        if cfg.get("use_recon") and not extra["recon"]:      # в расчёте ремонта нет — по замечаниям CarMax (costs.json: recon_keywords)
            text = " ".join(str(row.get(k) or "") for k in ("auction_notes", "defects", "condition_report"))
            if text.strip():
                extra["recon"] = estimate_recon(text, costs)[0]
        market = parse_money(row.get("market_estimate_usd"))
        f = flip(mmr, row.get("auction") or "CarMax", costs, stats, extra["recon"], extra["transport"], market)
        row["flip_mmr_usd"] = f"{mmr:.0f}"
        row["flip_ceiling_usd"] = str(f["ceiling"])
        row["flip_profit_usd"] = str(f["profit"]) if market else ""
        src = "MMR вписан вами" if exact else f"≈ MMR по {n} похожим ({how}, ±15%)"
        row["flip_note"] = (f"Перепродажа на {cfg['target']}: {src} ${mmr:,.0f} × {f['ratio']:.2f} ({f['ratio_src']}) = ${f['proceeds']:,} выручки; "
                            + "; ".join(f"{k} ${v:,}" for k, v in f["costs"].items())
                            + f"; сбор покупателя CarMax — по цене. Потолок (прибыль ${f['target']:,.0f}): ${f['ceiling']:,}"
                            + (f"; при средней цене покупки ${market:,.0f} прибыль ≈ ${f['profit']:,}" if market else ""))


# ---------------------------------------------------------------- проверка идеи на прошлых торгах

def backtest(history: dict[str, dict], costs: dict, obs: dict, now: float | None = None, days: int = 30) -> dict:
    """Продажи CarMax за days дней: если бы купили по цене продажи и сдали на Manheim — сколько в плюсе.
    Ремонт по замечаниям здесь не учитывается (в итогах их нет) — это верхняя граница."""
    from . import results
    now = time.time() if now is None else now
    cfg = config(costs)
    idx = index(obs)
    stats = results.stats(history)
    since = dt.date.fromtimestamp(now - days * 86400).isoformat()
    rows = []
    for rec in history.values():
        if not str(rec.get("auction", "")).lower().startswith("carmax") or rec.get("outcome") != "Sold" or rec.get("date", "") < since:
            continue
        price = parse_money(rec.get("price"))
        model_trim = str(rec.get("model") or "")
        est, n, how = estimate(rec.get("year"), rec.get("make", ""), model_trim, "", rec.get("miles"), idx, exclude=str(rec.get("vin", "")).upper(),
                               min_comps=int(cfg["min_comps"]))
        if not price or not est:
            continue
        f = flip(est, "CarMax", costs, stats, price=price)
        rows.append({"date": rec["date"][:10], "auction": rec["auction"].replace("CarMax ", ""), "car": f"{rec.get('year')} {rec.get('make')} {model_trim}",
                     "miles": rec.get("miles"), "price": price, "mmr": round(est), "n": n, "share": round(price / est, 2), "profit": f["profit"],
                     "buyer": rec.get("buyer", ""), "vin": rec.get("vin", "")})
    junk = [r for r in rows if r["share"] < 0.5]          # дешевле половины MMR — почти наверняка тяжёлый дефект: не считаем
    rows = [r for r in rows if r["share"] >= 0.5]
    ok = [r for r in rows if r["profit"] >= cfg["min_profit_usd"]]
    by_place = collections.defaultdict(list)
    for r in rows:
        by_place[r["auction"]].append(r)
    by_model = collections.defaultdict(list)
    for r in rows:
        by_model[" ".join(r["car"].split()[1:3])].append(r)

    def med(xs):
        return round(statistics.median(xs), 2) if xs else None
    return {"n": len(rows), "junk": len(junk), "ok": len(ok), "ok_pct": round(len(ok) / len(rows), 3) if rows else 0, "share": med([r["share"] for r in rows]),
            "profit_ok": round(sum(r["profit"] for r in ok)), "min_profit": cfg["min_profit_usd"],
            "places": sorted([(p, len(v), sum(1 for r in v if r["profit"] >= cfg["min_profit_usd"]), med([r["share"] for r in v])) for p, v in by_place.items()],
                             key=lambda x: -x[2]),
            "models": sorted([(m, len(v), sum(1 for r in v if r["profit"] >= cfg["min_profit_usd"]), med([r["share"] for r in v])) for m, v in by_model.items()
                              if len(v) >= 5], key=lambda x: -(x[2] / x[1]))[:20],
            "best": sorted(ok, key=lambda r: -r["profit"])[:40]}


def real_flips(costs: dict, history: dict[str, dict]) -> list[dict]:
    """Машины, которые на самом деле перепродали с CarMax на Manheim (сверка выгрузок, resale.py): прибыль по MMR / цене продажи."""
    from . import results
    stats = results.stats(history)
    out = []
    for r in resale.rows(resale.load()):
        b = r["carmax"]
        mmr = next((s["mmr"] for s in r["seen"] if s.get("mmr")), None)
        sold = next((s["price"] for s in reversed(r["sold"]) if s.get("price")), None)
        if not (mmr or sold) or not b.get("price"):
            continue
        f = flip(sold or mmr, "CarMax", {**costs, "manheim_flip": {**config(costs), "sale_to_mmr": 1.0 if sold else None}}, stats, price=b["price"])
        out.append({"car": r["car"], "buyer": b.get("buyer", ""), "price": b["price"], "mmr": mmr, "sold": sold, "profit": f["profit"],
                    "days": r["days"], "auction": b.get("auction", "")})
    return out


def page(history: dict[str, dict], costs: dict, now: float | None = None) -> str:
    import html
    e = html.escape
    cfg = config(costs)
    obs = load_obs()
    bt = backtest(history, costs, obs, now)
    real = real_flips(costs, history)

    def money(v):
        return f"${v:,.0f}" if v is not None else "—"
    head = ("<!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Опт на Manheim</title><style>body{font:14px/1.45 -apple-system,'Segoe UI',Arial,sans-serif;margin:16px;color:#1d2b45;background:#fff}"
            "h1{font-size:20px;margin:0 0 6px}h2{font-size:16px;margin:18px 0 6px}.muted{color:#667}table{border-collapse:collapse;width:100%;margin:6px 0 14px}"
            "th,td{border-bottom:1px solid #e3e8f0;padding:5px 7px;text-align:left;vertical-align:top}th{background:#1f3864;color:#fff;font-weight:600}"
            "td.n{text-align:right;white-space:nowrap}.wrap{overflow-x:auto}.pos{color:#1f6f43;font-weight:600}.neg{color:#b42318;font-weight:600}"
            ".box{border:1px solid #c9d6ea;background:#f3f6fb;border-radius:8px;padding:10px 12px;margin:8px 0}</style></head><body>")
    costs_line = (f"сбор продавца Manheim {', '.join(f'до ${u:,.0f} — ${f:,.0f}' for u, f in cfg['sell_fee_tiers'][:-1])}, дороже — ${cfg['sell_fee_tiers'][-1][1]:,.0f}; "
                  f"CR ${cfg['cr_fee_usd']}; доставка на Manheim ${cfg['transport_to_manheim_usd']}; подготовка ${cfg['prep_usd']}; "
                  f"резерв {float(cfg['reserve_pct']):.0%}; сбор покупателя CarMax — по его сетке; желаемая прибыль ${cfg['min_profit_usd']:,}")
    intro = ("<h1>🔁 Опт на Manheim: есть ли смысл покупать на CarMax и сдавать на Manheim</h1>"
             f"<p class='muted'>MMR для машин CarMax — прикидка по {len(obs.get('cars', {})):,} машинам из ваших выгрузок Manheim "
             f"(выгрузок: {len(obs.get('files', {}))}); точный MMR — впишите у машины в окне. Расходы (costs.json: manheim_flip, проверьте по своей сетке): "
             f"{e(costs_line)}.</p>")
    box = (f"<div class='box'><b>Продажи CarMax за 30 дней с прикидкой MMR: {bt['n']:,}.</b> Если бы купили по цене продажи и сдали на "
           f"{e(cfg['target'])}, прибыль от ${bt['min_profit']:,} осталась бы у <b>{bt['ok']:,} ({bt['ok_pct']:.0%})</b>, всего ≈ {money(bt['profit_ok'])}. "
           f"Цена CarMax ÷ MMR — в среднем {bt['share'] or '—'}. Ремонт по замечаниям CarMax здесь не учтён — это верхняя граница; "
           f"ещё {bt['junk']:,} машин ушли дешевле половины MMR — почти наверняка тяжёлый дефект, их не считаем.</div>")
    t_real = ["<h2>Настоящие перепродажи (сверка выгрузок Manheim)</h2>"]
    if real:
        profits = [r["profit"] for r in real]
        t_real.append(f"<p>Машин: {len(real)}; если бы перепродавали вы — прибыль после расходов в среднем {money(statistics.median(profits))}, "
                      f"в плюсе от ${cfg['min_profit_usd']:,} — {sum(1 for p in profits if p >= cfg['min_profit_usd'])}. По MMR, где нет цены продажи.</p>"
                      "<div class='wrap'><table><tr><th>Машина</th><th>Купил на CarMax</th><th>Цена CarMax</th><th>MMR на Manheim</th><th>Продана</th>"
                      "<th>Прибыль после расходов</th><th>Дней</th></tr>")
        for r in sorted(real, key=lambda r: -r["profit"])[:60]:
            t_real.append(f"<tr><td>{e(r['car'])}</td><td>{e(r['buyer'] or '—')}</td><td class='n'>{money(r['price'])}</td><td class='n'>{money(r['mmr'])}</td>"
                          f"<td class='n'>{money(r['sold']) if r['sold'] else ''}</td><td class='n {'pos' if r['profit'] >= 0 else 'neg'}'>{money(r['profit'])}</td>"
                          f"<td class='n'>{'' if r['days'] is None else r['days']}</td></tr>")
        t_real.append("</table></div>")
    else:
        t_real.append("<p class='muted'>Пока нет — нужны выгрузки Manheim (страница «🔁 Перепродажи»).</p>")
    t_place = ["<h2>По площадкам CarMax</h2><div class='wrap'><table><tr><th>Площадка</th><th>Продано (с MMR)</th><th>В плюсе</th><th>Цена ÷ MMR</th></tr>"]
    t_place += [f"<tr><td>{e(p)}</td><td class='n'>{n}</td><td class='n'>{k} ({k / n:.0%})</td><td class='n'>{s}</td></tr>" for p, n, k, s in bt["places"]]
    t_place.append("</table></div>")
    t_model = ["<h2>По моделям (от 5 продаж)</h2><div class='wrap'><table><tr><th>Модель</th><th>Продано</th><th>В плюсе</th><th>Цена ÷ MMR</th></tr>"]
    t_model += [f"<tr><td>{e(m)}</td><td class='n'>{n}</td><td class='n'>{k} ({k / n:.0%})</td><td class='n'>{s}</td></tr>" for m, n, k, s in bt["models"]]
    t_model.append("</table></div>")
    t_best = ["<h2>Лучшие по прикидке (купить по этой цене и сдать на Manheim)</h2><div class='wrap'><table><tr><th>Машина</th><th>CarMax</th>"
              "<th>Цена</th><th>≈ MMR</th><th>Цена ÷ MMR</th><th>Прибыль</th><th>Купил</th></tr>"]
    t_best += [f"<tr><td>{e(r['car'])}<br><span class='muted'>{e(r['vin'])} · {e(str(r['miles']))} mi</span></td><td>{e(r['date'][5:])} {e(r['auction'])}</td>"
               f"<td class='n'>{money(r['price'])}</td><td class='n'>{money(r['mmr'])} <span class='muted'>({r['n']})</span></td><td class='n'>{r['share']}</td>"
               f"<td class='n pos'>{money(r['profit'])}</td><td>{e(r['buyer'] or '—')}</td></tr>" for r in bt["best"]]
    t_best.append("</table></div>")
    return head + intro + box + "".join(t_real) + "".join(t_place) + "".join(t_model) + "".join(t_best) + "</body></html>"
