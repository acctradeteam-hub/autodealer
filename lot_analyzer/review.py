"""Разбор торгов: наш прогноз до торгов ↔ итоги торгов.

Прогноз запоминается сам (data/forecasts.json): при каждом пересчёте окна — по каждой машине, у которой торги
сегодня или впереди, — KBB (настоящий или предварительный), средняя цена покупки, потолок, вердикт, ваша ставка
и замечания аукциона. После дня торгов прогноз больше не меняется («заморожен»): сверяем с тем, что знали ДО торгов.

Когда в «Загрузках» появляются итоги этих торгов, страница «Разбор торгов» (/review) показывает по каждой площадке
и дню: насколько точной была средняя цена покупки, какие отобранные машины («МОЖНО») можно было купить и за сколько,
чем кончились ваши ставки, какие машины мы пропустили, хотя они ушли дёшево, и где прогноз ошибся сильнее всего.
"""

from __future__ import annotations

import datetime as dt
import json
import statistics
from html import escape as esc
from pathlib import Path

from .paths import DATA_DIR
from . import schedule

FORECASTS_PATH = DATA_DIR / "forecasts.json"
FIELDS = ("auction", "location", "lot_number", "vin", "year", "make", "model", "trim", "odometer_miles", "group",
          "kbb_private_party_usd", "kbb_estimate_usd", "market_estimate_usd", "sale_estimate_usd", "calc_max_bid_usd",
          "calc_profit_market_usd", "calc_verdict", "my_proxy_usd", "auction_notes", "odometer_brand", "lot_url")


def _num(value) -> float | None:
    try:
        return float(str(value).replace("$", "").replace(",", "")) if str(value or "").strip() else None
    except ValueError:
        return None


def load() -> dict[str, dict]:
    try:
        return json.loads(FORECASTS_PATH.read_text(encoding="utf-8")) if FORECASTS_PATH.exists() else {}
    except (OSError, ValueError):
        return {}


def place_of(row: dict) -> str:
    """«Oceanside, CA» → «CarMax Oceanside»; Manheim — название площадки, как в итогах."""
    location = str(row.get("location") or "").split(",")[0].strip()
    if row.get("auction") == "CarMax":
        return f"CarMax {location}".strip()
    return location or str(row.get("auction") or "")


def remember(rows: list[dict], now: dt.datetime | None = None) -> bool:
    """Запоминает прогноз машин, чьи торги сегодня или впереди. True — файл изменился."""
    now = now or dt.datetime.now()
    today = now.date().isoformat()
    store = load()
    changed = False
    for row in rows:
        vin = row.get("vin", "")
        if not vin or row.get("lot_status") in ("Sold", "Ended"):
            continue
        saved = dt.datetime.fromtimestamp(float(row.get("_saved") or now.timestamp()))
        end, _ = schedule.ends_at(row.get("sale_date", ""), saved)
        if not end:
            continue
        day = end.date().isoformat()
        if day < today:                               # торги прошли — прогноз заморожен
            continue
        rec = {k: str(row.get(k) or "") for k in FIELDS}
        rec.update(sale_day=day, place=place_of(row), verdict=str(row.get("calc_verdict") or "").split(":")[0].split(";")[0].strip(),
                   kbb_kind="KBB" if row.get("kbb_private_party_usd") and not row.get("kbb_from_window_field") else
                   ("≈" if row.get("kbb_estimate_usd") else ""), updated=now.isoformat(timespec="minutes"))
        key = f"{vin}|{day}"
        old = store.get(key)
        if not old or any(old.get(k) != rec.get(k) for k in rec if k != "updated"):
            store[key] = rec
            changed = True
    if changed:
        # Не копим бесконечно: прогнозы старше 120 дней — убираем.
        cutoff = (now.date() - dt.timedelta(days=120)).isoformat()
        store = {k: v for k, v in store.items() if v.get("sale_day", "") >= cutoff}
        FORECASTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        FORECASTS_PATH.write_text(json.dumps(store, ensure_ascii=False, indent=0), encoding="utf-8")
    return changed


def _result_for(fc: dict, by_vin: dict[str, list[dict]]) -> dict | None:
    """Итог этой машины на этих торгах: тот же VIN, дата итога — от дня до торгов до +2 дней."""
    day = dt.date.fromisoformat(fc["sale_day"])
    best = None
    for rec in by_vin.get(fc["vin"], []):
        try:
            when = dt.date.fromisoformat(rec.get("date", "")[:10])
        except ValueError:
            continue
        if -1 <= (when - day).days <= 2 and rec.get("outcome") != "нет итога":
            best = rec
    return best


def compare(forecasts: dict[str, dict], history: dict[str, dict]) -> list[dict]:
    """По каждой площадке и дню торгов, где есть и прогноз, и итоги: сводка и списки машин."""
    by_vin: dict[str, list[dict]] = {}
    for rec in history.values():
        if rec.get("vin"):
            by_vin.setdefault(rec["vin"], []).append(rec)
    groups: dict[tuple, list[tuple[dict, dict | None]]] = {}
    for fc in forecasts.values():
        groups.setdefault((fc["place"], fc["sale_day"]), []).append((fc, _result_for(fc, by_vin)))
    out = []
    for (place, day), pairs in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0]), reverse=True):
        matched = [(f, r) for f, r in pairs if r]
        if not matched:
            continue
        sold = [(f, r) for f, r in matched if r["outcome"] == "Sold" and _num(r.get("price"))]
        errs, by_kind = [], {"KBB": [], "≈": []}
        for f, r in sold:
            market, price = _num(f.get("market_estimate_usd")), _num(r["price"])
            if market and price:
                e = (price - market) / market
                errs.append(e)
                if f.get("kbb_kind") in by_kind:
                    by_kind[f["kbb_kind"]].append(e)
        def actual_profit(f: dict, price: float) -> float | None:
            """Прибыль, если бы купили за фактическую цену: прибыль при средней цене + (средняя цена − фактическая)."""
            pm, market = _num(f.get("calc_profit_market_usd")), _num(f.get("market_estimate_usd"))
            if pm is not None and market:
                return pm + market - price
            return None
        could, lost, missed, bids = [], [], [], []
        for f, r in sold:
            price = _num(r["price"])
            ceiling = _num(f.get("calc_max_bid_usd"))
            profit = actual_profit(f, price)
            if f.get("verdict", "").startswith(("МОЖНО", "ОСМОТР", "РИСК")) and ceiling:
                (could if price <= ceiling else lost).append((f, r, profit, price - ceiling))
            elif profit is not None and profit > 500:
                missed.append((f, r, profit))
            bid = _num(f.get("my_proxy_usd"))
            if bid:
                bids.append((f, r, bid >= price, price - bid))
        out.append({
            "place": place, "day": day, "forecast": len(pairs), "with_result": len(matched), "sold": len(sold),
            "no_sale": sum(1 for _, r in matched if r["outcome"] != "Sold"),
            "err_median": statistics.median([abs(e) for e in errs]) if errs else None,
            "bias": statistics.median(errs) if errs else None,
            "within15": sum(1 for e in errs if abs(e) <= 0.15) / len(errs) if errs else None, "n_err": len(errs),
            "by_kind": {k: (statistics.median([abs(e) for e in v]), len(v)) for k, v in by_kind.items() if v},
            "could": sorted(could, key=lambda x: -(x[2] or 0)), "lost": sorted(lost, key=lambda x: x[3]),
            "missed": sorted(missed, key=lambda x: -x[2])[:15], "bids": bids,
            "worst": sorted(((f, r, (_num(r["price"]) - _num(f["market_estimate_usd"])) / _num(f["market_estimate_usd"]))
                             for f, r in sold if _num(f.get("market_estimate_usd"))), key=lambda x: -abs(x[2]))[:10],
        })
    return out


def _money(v) -> str:
    n = _num(v)
    return f"${n:,.0f}" if n is not None else "—"


def _car(f: dict) -> str:
    link = f'<a href="{esc(f["lot_url"])}" target="_blank" rel="noopener">↗</a>' if f.get("lot_url") else ""
    tmu = " <b class=bad>TMU</b>" if f.get("odometer_brand") == "TMU" else ""
    return (f'{esc(f.get("lot_number", ""))} · {esc(f.get("year", ""))} {esc(f.get("make", ""))} {esc(f.get("model", ""))} '
            f'{esc(f.get("trim", ""))} · {_num(f.get("odometer_miles")) or 0:,.0f} mi{tmu} {link}')


def render(history: dict[str, dict], forecasts: dict[str, dict] | None = None) -> str:
    forecasts = load() if forecasts is None else forecasts
    blocks = compare(forecasts, history)
    pct = lambda x: f"{x * 100:.0f}%" if x is not None else "—"
    sections = []
    for b in blocks:
        kinds = " · ".join(f"{'настоящий KBB' if k == 'KBB' else 'предварительный KBB'}: ошибка {v[0] * 100:.0f}% ({v[1]})" for k, v in b["by_kind"].items())
        rows_could = "".join(f"<tr><td>{_car(f)}</td><td class=n>{_money(f['calc_max_bid_usd'])}</td><td class=n>{_money(r['price'])}</td>"
                             f"<td class=n><b>{_money(p)}</b></td><td>{esc(f.get('verdict', ''))}</td></tr>" for f, r, p, _ in b["could"]) \
            or "<tr><td colspan=5 class=m>нет</td></tr>"
        rows_lost = "".join(f"<tr><td>{_car(f)}</td><td class=n>{_money(f['calc_max_bid_usd'])}</td><td class=n>{_money(r['price'])}</td>"
                            f"<td class=n>+{_money(d)}</td></tr>" for f, r, _, d in b["lost"]) or "<tr><td colspan=4 class=m>нет</td></tr>"
        rows_bids = "".join(f"<tr><td>{_car(f)}</td><td class=n>{_money(f['my_proxy_usd'])}</td><td class=n>{_money(r['price'])}</td>"
                            f"<td>{'хватило бы' if ok else f'не хватило {_money(d)}'}</td></tr>" for f, r, ok, d in b["bids"]) \
            or "<tr><td colspan=4 class=m>ставок в окне не было</td></tr>"
        rows_missed = "".join(f"<tr><td>{_car(f)}</td><td>{esc(f.get('verdict', ''))}{' (KBB ≈)' if f.get('kbb_kind') == '≈' else ''}</td>"
                              f"<td class=n>{_money(r['price'])}</td><td class=n><b>{_money(p)}</b></td><td class=m>{esc(f.get('auction_notes', '')[:90])}</td></tr>"
                              for f, r, p in b["missed"]) or "<tr><td colspan=5 class=m>нет</td></tr>"
        rows_worst = "".join(f"<tr><td>{_car(f)}</td><td class=n>{_money(f['market_estimate_usd'])}</td><td class=n>{_money(r['price'])}</td>"
                             f"<td class=n>{e * 100:+.0f}%</td><td class=m>{esc(f.get('auction_notes', '')[:90])}</td></tr>" for f, r, e in b["worst"])
        sections.append(f"""<div class=card><h2>{esc(b['place'])} · торги {esc(b['day'])}</h2>
<p>В прогнозе {b['forecast']} машин, итог нашёлся у {b['with_result']}: продано {b['sold']}, не продано {b['no_sale']}.</p>
<p><b>Средняя цена покупки (наш прогноз) ↔ фактическая цена:</b> обычная ошибка {pct(b['err_median'])}, в пределах ±15% — {pct(b['within15'])}
({b['n_err']} машин); {'машины уходили дороже прогноза' if (b['bias'] or 0) > 0.03 else 'машины уходили дешевле прогноза' if (b['bias'] or 0) < -0.03 else 'без заметного перекоса'}
(смещение {pct(b['bias'])}). <span class=m>{esc(kinds)}</span></p>
<h3>Отобранные (МОЖНО / ОСМОТР / РИСК), ушли не дороже нашего потолка — можно было купить</h3>
<table><tr><th>Машина</th><th class=n>Потолок</th><th class=n>Продана</th><th class=n>Прибыль при такой цене*</th><th>Вердикт</th></tr>{rows_could}</table>
<h3>Отобранные, ушли дороже потолка</h3>
<table><tr><th>Машина</th><th class=n>Потолок</th><th class=n>Продана</th><th class=n>Дороже потолка на</th></tr>{rows_lost}</table>
<h3>Ваши ставки (MP)</h3>
<table><tr><th>Машина</th><th class=n>Ставка</th><th class=n>Продана</th><th>Итог</th></tr>{rows_bids}</table>
<h3>Пропущенные: не отобраны, но ушли дёшево (прибыль при такой цене > $500)*</h3>
<table><tr><th>Машина</th><th>Наш вердикт</th><th class=n>Продана</th><th class=n>Прибыль*</th><th>Замечания</th></tr>{rows_missed}</table>
<h3>Где прогноз ошибся сильнее всего</h3>
<table><tr><th>Машина</th><th class=n>Прогноз</th><th class=n>Продана</th><th class=n>Разница</th><th>Замечания</th></tr>{rows_worst}</table>
</div>""")
    body = "".join(sections) or ("<div class=card>Пока нечего сверять. Прогноз запоминается сам, пока в окне есть машины к торгам; "
                                 "после торгов скачайте итоги (файл итогов CarMax / Manheim) — он попадёт в «Загрузки», и разбор появится здесь.</div>")
    return f"""<!doctype html><html lang=ru><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Разбор торгов</title><style>
:root{{--bg:#f6f6f3;--fg:#1c1c1a;--m:#62625c;--card:#fff;--line:#e2e2dc;--bad:#b3261e}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151514;--fg:#ececea;--m:#a2a29c;--card:#1f1f1d;--line:#34342f;--bad:#f2b8b5}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}}main{{max-width:1100px;margin:0 auto;padding:20px 16px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:12px 0;overflow-x:auto}}
table{{border-collapse:collapse;width:100%;font-size:13px;margin-bottom:8px}}td,th{{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
.n{{text-align:right;white-space:nowrap}}.m{{color:var(--m);font-size:12px}}.bad{{color:var(--bad)}}h2{{margin:4px 0 8px;font-size:19px}}h3{{font-size:15px;margin:14px 0 4px}}
</style></head><body><main><h1>Разбор торгов</h1>
<p class=m>Наш прогноз до торгов (запомнен программой) против итогов торгов из ваших файлов. * Прибыль при такой цене — примерно:
прибыль при покупке по средней цене + (средняя цена − фактическая цена), с теми же расходами.</p>{body}</main></body></html>"""
