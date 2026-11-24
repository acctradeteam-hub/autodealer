"""Перепродажи: машины, купленные на торгах CarMax, снова в продаже на Manheim — кто их купил на CarMax, за сколько,
где и за сколько выставил, через сколько дней.

Откуда данные:
  * покупки на CarMax — из истории итогов (data/auction_results.csv): VIN, дата, площадка, лот, цена, покупатель
    (имя покупателя есть в файлах записи торгов Velocicast: auction_all_… / auction_summary_…);
  * Manheim — из CSV-выгрузки поиска (кнопка экспорта в результатах поиска Manheim): VIN, площадка, живые торги или OVE,
    продавец, ставка и Buy Now в OVE, MMR, оценка состояния. Выгрузку кладите в «Загрузки» (или «Загрузки/Lot Analyzer») —
    программа читает каждый новый файл один раз;
  * продажи на Manheim — из той же истории итогов (CSV дорожки, PDF Post-Sale Results), если они есть.
Совпадения по VIN копятся в data/resale.json: выгрузки в «Загрузках» со временем пропадают, а картина остаётся.
"""

from __future__ import annotations

import collections
import csv
import datetime as dt
import io
import json
import re
import statistics
import time
from pathlib import Path

from . import manheim_csv
from .paths import DATA_DIR

PATH = DATA_DIR / "resale.json"
FILE_DAYS = 60            # выгрузки Manheim не старше 60 дней
KEEP_DAYS = 180           # совпадения хранятся полгода


def _num(value) -> float | None:
    try:
        n = float(str(value or "").replace("$", "").replace(",", "").strip())
    except ValueError:
        return None
    return n if n > 0 else None


def _name(value: str) -> str:
    """«WILLYS AUTO LLC» / «Willys Auto, L.L.C.» → «WILLYS AUTO»: для сравнения покупателя на CarMax и продавца на Manheim."""
    words = re.sub(r"[^A-Z0-9 ]", " ", str(value or "").upper().replace("L.L.C", "LLC")).split()
    while words and words[-1] in ("LLC", "INC", "CORP", "CO", "LTD", "CORPORATION", "COMPANY"):
        words.pop()
    return " ".join(words)


def load() -> dict:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else {}
    except (OSError, ValueError):
        data = {}
    data.setdefault("files", {})       # имя выгрузки → «размер:время» (каждую читаем один раз)
    data.setdefault("cars", {})        # VIN → совпадение
    data.setdefault("buys_n", 0)       # сколько покупок CarMax было при последнем чтении выгрузок
    data.setdefault("listed", 0)       # машин во всех прочитанных выгрузках
    return data


def _save(data: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_name(PATH.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PATH)


def carmax_buys(history: dict[str, dict]) -> dict[str, dict]:
    """VIN → последняя продажа на CarMax (дата, площадка, лот, цена, покупатель, пробег)."""
    out: dict[str, dict] = {}
    for rec in history.values():
        vin = str(rec.get("vin") or "").upper()
        if (len(vin) == 17 and str(rec.get("auction", "")).lower().startswith("carmax") and rec.get("outcome") == "Sold"
                and _num(rec.get("price")) and rec.get("date")):
            if vin not in out or rec["date"] > out[vin]["date"]:
                out[vin] = {"date": rec["date"][:10], "auction": rec["auction"].replace("CarMax ", ""), "lot": rec.get("lot", ""),
                            "price": _num(rec["price"]), "buyer": rec.get("buyer", ""), "miles": rec.get("miles", ""),
                            "car": " ".join(x for x in (rec.get("year"), rec.get("make"), rec.get("model")) if x)}
    return out


def read_export(path: Path) -> list[dict]:
    """Строки выгрузки Manheim как есть (живые торги и OVE отдельно): то, что нужно для сверки."""
    text = manheim_csv._decode(path.read_bytes()).replace("\r\n", "\n").replace("\r", "\n")
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        vin = (r.get("Vin") or "").strip().upper()
        if len(vin) != 17:
            continue
        ove = r.get("Inventory") == manheim_csv.TIMED
        lane, run = (r.get("Lane") or "").strip(), (r.get("Run") or "").strip()
        out.append({"vin": vin, "house": (r.get("Auction House") or "").strip(), "location": (r.get("Pickup Location") or "").strip(),
                    "kind": "OVE" if ove else "торги", "lane": "" if ove else f"{lane}-{run}".strip("-"),
                    "when": manheim_csv._utc_to_local(r.get("Ends At" if ove else "Starts At") or "")[:10],
                    "seller": (r.get("Seller Name") or "").strip(), "mmr": _num(r.get("MMR")), "bid": _num(r.get("Bid Amount")),
                    "buy_now": _num(r.get("Buy Now Price")), "grade": (r.get("Condition Report Grade") or "").strip(),
                    "miles": (r.get("Odometer Value") or "").strip().split(".")[0],
                    "car": " ".join(x for x in ((r.get("Year") or "").strip(), (r.get("Make") or "").strip(), (r.get("Model") or "").strip(),
                                                (r.get("Trim") or "").strip()) if x)})
    return out


def exports(folders: list[Path], now: float) -> list[Path]:
    found = []
    for folder in folders:
        folder = Path(folder)
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            try:
                fresh = path.is_file() and path.suffix.lower() == ".csv" and path.stat().st_mtime > now - FILE_DAYS * 86400
            except OSError:
                continue
            if fresh and manheim_csv.is_export(path):
                found.append(path)
    return sorted(found, key=lambda p: p.stat().st_mtime)


def stamp(folders: list[Path], now: float | None = None) -> str:
    """Отпечаток выгрузок Manheim и истории итогов: окно пересчитывает сводку, как только что-то из них изменилось."""
    now = time.time() if now is None else now
    from . import results
    parts = [f"{p.name}:{p.stat().st_size}:{p.stat().st_mtime:.0f}" for p in exports(folders, now)]
    hist = results.HISTORY_PATH
    parts.append(f"h:{hist.stat().st_mtime:.0f}" if hist.exists() else "h:-")
    return str(hash("|".join(parts)))


def _ask(car: dict) -> tuple[float | None, str]:
    """Цена на Manheim для наценки: продана (итог) → Buy Now в OVE → ставка в OVE → MMR."""
    sold = [s for s in car.get("sold", []) if s.get("price")]
    if sold:
        return sold[-1]["price"], "продана"
    seen = car.get("seen", [])
    for field, label in (("buy_now", "Buy Now"), ("bid", "ставка OVE"), ("mmr", "MMR")):
        vals = [s[field] for s in seen if s.get(field)]
        if vals:
            return vals[-1], label
    return None, ""


def refresh(folders: list[Path], history: dict[str, dict], now: float | None = None) -> dict:
    """Новые выгрузки Manheim → совпадения с покупками на CarMax (data/resale.json) → сводка для окна."""
    now = time.time() if now is None else now
    data = load()
    buys = carmax_buys(history)
    if len(buys) != data["buys_n"]:          # пришли новые итоги CarMax — старые выгрузки сверяем заново
        data["files"], data["buys_n"] = {}, len(buys)
    changed = False
    for path in exports(folders, now):
        st = path.stat()
        stamp = f"{st.st_size}:{st.st_mtime:.0f}"
        if data["files"].get(path.name) == stamp:
            continue
        data["files"][path.name] = stamp
        changed = True
        seen_day = dt.date.fromtimestamp(st.st_mtime).isoformat()
        try:
            listed = read_export(path)
        except (OSError, csv.Error):
            continue
        data["listed"] = max(int(data.get("listed") or 0), len({r["vin"] for r in listed}))
        for rec in listed:
            buy = buys.get(rec["vin"])
            if not buy or (rec["when"] or seen_day) < buy["date"]:
                continue                     # торги на Manheim раньше, чем купили на CarMax, — не перепродажа
            car = data["cars"].setdefault(rec["vin"], {"vin": rec["vin"], "seen": [], "sold": []})
            key = (rec["house"], rec["kind"], rec["lane"], rec["when"])
            old = next((s for s in car["seen"] if (s["house"], s["kind"], s["lane"], s["when"]) == key), None)
            entry = {k: rec[k] for k in ("house", "location", "kind", "lane", "when", "seller", "mmr", "bid", "buy_now", "grade", "miles")}
            entry["file"], entry["seen_day"] = path.name, seen_day
            if old:
                entry["first_seen"] = old.get("first_seen", old.get("seen_day", seen_day))
                old.update(entry)
            else:
                entry["first_seen"] = seen_day
                car["seen"].append(entry)
            car["manheim_car"] = rec["car"]
    # Покупка на CarMax (покупатель мог появиться позже) и продажа на Manheim из истории итогов.
    by_vin = collections.defaultdict(list)
    for rec in history.values():
        if str(rec.get("auction", "")).lower().startswith("manheim") and rec.get("vin"):
            by_vin[rec["vin"].upper()].append(rec)
    for vin, car in list(data["cars"].items()):
        buy = buys.get(vin)
        if buy:
            car["carmax"] = buy
        sold = [{"date": r["date"][:10], "auction": r["auction"], "outcome": r.get("outcome", ""), "price": _num(r.get("price"))}
                for r in sorted(by_vin.get(vin, []), key=lambda r: r["date"]) if car.get("carmax") and r["date"][:10] >= car["carmax"]["date"]]
        if sold != car.get("sold"):
            car["sold"], changed = sold, True
        last = max([s.get("seen_day", "") for s in car["seen"]] + [s["date"] for s in car["sold"]] or [""])
        if last and last < dt.date.fromtimestamp(now - KEEP_DAYS * 86400).isoformat():
            del data["cars"][vin]
            changed = True
    if changed:
        _save(data)
    return summary(data, buys)


def rows(data: dict) -> list[dict]:
    """Совпадения для страницы и окна: самое свежее сверху."""
    out = []
    for car in data["cars"].values():
        buy = car.get("carmax") or {}
        if not buy:
            continue
        seen = sorted(car["seen"], key=lambda s: (s.get("first_seen", ""), s.get("when", "")))
        first = min([s.get("first_seen") or s.get("seen_day", "") for s in seen] + [s["date"] for s in car["sold"]] or [""])
        ask, ask_kind = _ask(car)
        try:
            days = (dt.date.fromisoformat(first) - dt.date.fromisoformat(buy["date"])).days if first else None
        except ValueError:
            days = None
        sellers = sorted({s["seller"] for s in seen if s.get("seller")})
        out.append({"vin": car["vin"], "car": buy.get("car") or car.get("manheim_car", ""), "carmax": buy, "seen": seen, "sold": car["sold"],
                    "first": first, "days": days, "ask": ask, "ask_kind": ask_kind,
                    "markup": round(ask - buy["price"]) if ask and buy.get("price") else None,
                    "sellers": sellers, "self": bool(buy.get("buyer")) and _name(buy["buyer"]) in {_name(s) for s in sellers}})
    return sorted(out, key=lambda r: (r["first"], r["carmax"].get("date", "")), reverse=True)


def summary(data: dict, buys: dict[str, dict]) -> dict:
    """Окну: сколько нашлось, кто покупал на CarMax и перепродаёт, наценка, через сколько дней."""
    found = rows(data)
    bought = collections.Counter(_name(b["buyer"]) for b in buys.values() if b.get("buyer"))
    groups = collections.defaultdict(list)
    for r in found:
        groups[r["carmax"].get("buyer") or "(покупатель не записан)"].append(r)
    by_buyer = []
    for buyer, rs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        marks = [r["markup"] for r in rs if r["markup"] is not None]
        pct = [r["markup"] / r["carmax"]["price"] for r in rs if r["markup"] is not None and r["carmax"].get("price")]
        days = [r["days"] for r in rs if r["days"] is not None]
        sellers = collections.Counter(s for r in rs for s in r["sellers"])
        by_buyer.append({"buyer": buyer, "found": len(rs), "bought": bought.get(_name(buyer), 0),
                         "markup": round(statistics.median(marks)) if marks else None, "markup_pct": round(statistics.median(pct), 3) if pct else None,
                         "days": statistics.median(days) if days else None, "self": sum(1 for r in rs if r["self"]),
                         "sellers": sellers.most_common(3)})
    marks = [r["markup"] for r in found if r["markup"] is not None]
    return {"cars": len(found), "files": len(data["files"]), "listed": data.get("listed", 0), "buys": len(buys),
            "markup": round(statistics.median(marks)) if marks else None, "by_buyer": by_buyer[:12]}


def page(data: dict, buys: dict[str, dict]) -> str:
    """Страница /resale: сводка по покупателям CarMax и все найденные машины."""
    import html
    sm = summary(data, buys)
    found = rows(data)
    e = html.escape

    def money(v):
        return f"${v:,.0f}" if v else ""

    def ru(day):
        try:
            return dt.date.fromisoformat(day).strftime("%d.%m")
        except (TypeError, ValueError):
            return day or ""
    head = ("<!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Перепродажи CarMax → Manheim</title><style>body{font:14px/1.45 -apple-system,'Segoe UI',Arial,sans-serif;margin:16px;color:#1d2b45;"
            "background:#fff}h1{font-size:20px;margin:0 0 6px}h2{font-size:16px;margin:18px 0 6px}.muted{color:#667}table{border-collapse:collapse;"
            "width:100%;margin:6px 0 14px}th,td{border-bottom:1px solid #e3e8f0;padding:5px 7px;text-align:left;vertical-align:top}th{background:#1f3864;"
            "color:#fff;font-weight:600;position:sticky;top:0}td.n{text-align:right;white-space:nowrap}.pos{color:#1f6f43;font-weight:600}"
            ".neg{color:#b42318;font-weight:600}.wrap{overflow-x:auto}.pill{display:inline-block;padding:0 6px;border-radius:8px;background:#e2efda;"
            "font-size:12px}</style></head><body>")
    intro = ("<h1>🔁 Перепродажи: машины с торгов CarMax — на Manheim</h1>"
             f"<p class='muted'>Сверено выгрузок Manheim: {sm['files']} (машин в них до {sm['listed']:,}); покупок CarMax в истории: {sm['buys']:,}. "
             "Как пополнить: в Manheim найдите машины (ближайшие площадки и OVE), нажмите экспорт в CSV и положите файл в «Загрузки» "
             "(или «Загрузки/Lot Analyzer») — программа сама сверит VIN с покупками на CarMax. Наценка — цена на Manheim (продана → Buy Now → "
             "ставка OVE → MMR) минус цена покупки на CarMax.</p>")
    if not found:
        return head + intro + "<p><b>Совпадений пока нет.</b> Нужна выгрузка Manheim, сделанная после торгов CarMax, по которым есть итоги с покупателями.</p></body></html>"
    t1 = ["<h2>Кто покупает на CarMax и выставляет на Manheim</h2><div class='wrap'><table><tr><th>Покупатель на CarMax</th>"
          "<th>Куплено на CarMax (в истории)</th><th>Нашлось на Manheim</th><th>Продаёт сам</th><th>Через, дней (медиана)</th>"
          "<th>Наценка (медиана)</th><th>Продавец на Manheim</th></tr>"]
    for b in sm["by_buyer"]:
        days = "" if b["days"] is None else str(int(b["days"]))
        pct = "" if b["markup_pct"] is None else f" ({b['markup_pct']:+.0%})"
        sellers = ", ".join(f"{n} ({c})" for n, c in b["sellers"])
        t1.append(f"<tr><td>{e(b['buyer'])}</td><td class='n'>{b['bought'] or ''}</td><td class='n'>{b['found']}</td><td class='n'>{b['self'] or ''}</td>"
                  f"<td class='n'>{days}</td><td class='n'>{money(b['markup'])}{pct}</td><td>{e(sellers)}</td></tr>")
    t1.append("</table></div>")
    t2 = ["<h2>Машины</h2><div class='wrap'><table><tr><th>Машина</th><th>CarMax: когда, где</th><th>Цена CarMax</th><th>Купил</th>"
          "<th>Manheim: где</th><th>Продавец</th><th>Buy Now / ставка / MMR</th><th>Продана</th><th>Наценка</th><th>Дней</th></tr>"]
    for r in found:
        b = r["carmax"]
        where = "<br>".join(e(f"{s['house'] or s['location']} · {s['kind']}{' ' + s['lane'] if s['lane'] else ''} · {ru(s['when'])}"
                              + (f" · CR {s['grade']}" if s.get("grade") else "")) for s in r["seen"]) or "—"
        prices = "<br>".join(" · ".join(x for x in (f"Buy Now {money(s['buy_now'])}" if s.get("buy_now") else "",
                                                    f"ставка {money(s['bid'])}" if s.get("bid") else "",
                                                    f"MMR {money(s['mmr'])}" if s.get("mmr") else "") if x) for s in r["seen"])
        sold = "<br>".join(e(f"{ru(s['date'])} {s['auction']}: " + (money(s["price"]) if s.get("price") else s.get("outcome", ""))) for s in r["sold"])
        mk = r["markup"]
        mark = (f"<span class='{'pos' if mk >= 0 else 'neg'}'>{'+' if mk >= 0 else '−'}{money(abs(mk))}</span><br><span class='muted'>{e(r['ask_kind'])}</span>"
                if mk is not None else "")
        t2.append(f"<tr><td>{e(r['car'])}<br><span class='muted'>{e(r['vin'])} · {e(str(b.get('miles') or ''))} mi</span></td>"
                  f"<td>{ru(b.get('date'))} {e(b.get('auction', ''))} {e(b.get('lot', ''))}</td><td class='n'>{money(b.get('price'))}</td>"
                  f"<td>{e(b.get('buyer') or '—')}{' <span class=pill>сам на Manheim</span>' if r['self'] else ''}</td><td>{where}</td>"
                  f"<td>{e(', '.join(r['sellers']))}</td><td>{prices}</td><td>{sold}</td><td class='n'>{mark}</td>"
                  f"<td class='n'>{'' if r['days'] is None else r['days']}</td></tr>")
    t2.append("</table></div>")
    return head + intro + "".join(t1) + "".join(t2) + "</body></html>"
