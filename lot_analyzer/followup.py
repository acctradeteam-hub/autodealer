"""Повторный Carfax: что стало с машинами, которые купили на CarMax дилеры, за которыми следим (Willys Auto, JS Auto Sales …).

Через after_days (21 день) после их покупки на CarMax программа сама заказывает Carfax ещё раз — той же очередью «Carfax сам»
(расширение, Carfax Online, по одной вкладке), когда ваши машины уже обработаны. Из нового отчёта берутся записи ПОСЛЕ
продажи на CarMax: аукцион (какой), объявление о продаже, регистрация / новый владелец (штат), экспорт, сервис у дилера.
Окно напоминает: сколько ждут проверки, сколько готово и что нового; страница /followup — всё подробно и список дилеров.
Состояние — data/followup.json; дилеры по умолчанию — costs.json: followup.buyers.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time

from . import carfax
from .paths import DATA_DIR
from .resale import _name, carmax_buys

PATH = DATA_DIR / "followup.json"
DEFAULTS = {"buyers": ["WILLYS AUTO", "JS AUTO SALES", "ARICAL AUTOS", "AUTO SALES EXIM"], "after_days": 21, "since_days": 45,
            "per_day": 20, "tries": 2, "retry_hours": 2, "gap_s": 20}


def load() -> dict:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else {}
    except (OSError, ValueError):
        data = {}
    data.setdefault("cars", {})
    data.setdefault("viewed_at", 0)
    return data


def _save(data: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_name(PATH.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PATH)


def config(costs: dict, data: dict | None = None) -> dict:
    cfg = {**DEFAULTS, **(costs.get("followup") or {})}
    if data is not None and isinstance(data.get("buyers"), list):      # список, изменённый на странице /followup
        cfg["buyers"] = data["buyers"]
    return cfg


def watched(buyer: str, buyers: list[str]) -> bool:
    name = _name(buyer)
    return bool(name) and any(name == _name(b) or name.startswith(_name(b) + " ") for b in buyers if _name(b))


def _today(now: float) -> dt.date:
    return dt.date.fromtimestamp(now)


# ---------------------------------------------------------------- что стало после CarMax

_DATE = re.compile(r"(?m)^[ \t]*(\d{1,2})/(\d{1,2})/(\d{4})\b")
CATS = (("экспорт", r"\bexport"), ("изъята", r"repossess"), ("аукцион", r"\bauction\b"),
        ("в продаже", r"offered for sale|listed for sale|listed as (?:a )?dealer vehicle|for sale\b"),
        ("новый владелец", r"new owner|title issued|registration|registered|title or registration"),
        ("сервис", r"serviced|inspection|emissions|smog|detailed|reconditioned"))
_AUCTION = re.compile(r"(manheim[ \w'.-]{0,30}|adesa[ \w'.-]{0,20}|openlane|acv auctions?|america'?s auto auction[ \w-]{0,20}|"
                      r"insurance auto auctions?|copart|carmax auctions?)", re.I)


def after_events(text: str, since: str) -> list[dict]:
    """Записи отчёта с датой после продажи на CarMax (саму продажу CarMax в эти дни — не считаем)."""
    try:
        start = dt.date.fromisoformat(since)
    except ValueError:
        return []
    t = re.sub(r"[ ]", " ", text or "")
    cut = re.search(r"\bfull glossary\b", t, re.I)
    t = t[:cut.start()] if cut else t
    marks = list(_DATE.finditer(t))
    out, seen = [], set()
    for i, m in enumerate(marks):
        try:
            day = dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            continue
        if day < start:
            continue
        end = marks[i + 1].start() if i + 1 < len(marks) else len(t)
        block = re.sub(r"\s+", " ", t[m.end():min(end, m.end() + 400)]).strip()
        low = block.lower()
        if "carmax" in low and (day - start).days <= 4:              # сама продажа на торгах CarMax
            continue
        cats = [c for c, rx in CATS if re.search(rx, low)]
        if not cats:
            continue
        state = ""
        abbr = re.search(r",\s*([A-Z]{2})\b", block)
        full = re.search(r"\b(" + "|".join(carfax.STATES) + r")\b", block)
        if abbr and abbr.group(1) in carfax.ABBR:
            state = abbr.group(1)
        elif full:
            state = carfax.STATE_CODE.get(full.group(1).lower(), "")
        if re.search(r"\bmexico\b", low):
            state = "Mexico"
        where = _AUCTION.search(block)
        key = (day, tuple(cats))
        if key in seen:
            continue
        seen.add(key)
        out.append({"date": day.isoformat(), "cats": cats, "state": state, "where": where.group(1).strip() if where and "аукцион" in cats else "",
                    "text": block[:220]})
    return out


def verdict(evts: list[dict]) -> str:
    """«02.12 аукцион Manheim Riverside → 10.12 новый владелец (CA)»; пусто — новых записей нет."""
    parts, used = [], set()
    for e in evts:
        main = next((c for c in ("экспорт", "изъята", "аукцион", "в продаже", "новый владелец", "сервис") if c in e["cats"]), "")
        if not main or (main, e.get("where") or e.get("state")) in used:
            continue
        used.add((main, e.get("where") or e.get("state")))
        day = dt.date.fromisoformat(e["date"]).strftime("%d.%m")
        extra = f" {e['where']}" if e.get("where") else f" ({e['state']})" if e.get("state") and main in ("новый владелец", "экспорт", "в продаже") else ""
        parts.append(f"{day} {main}{extra}")
    return " → ".join(parts[:5])


# ---------------------------------------------------------------- очередь

def refresh(history: dict[str, dict], costs: dict, now: float | None = None) -> dict:
    """Новые покупки дилеров из списка → в ожидание; пришедшие отчёты → «что стало после CarMax». → сводка окну."""
    now = time.time() if now is None else now
    data = load()
    cfg = config(costs, data)
    since = (_today(now) - dt.timedelta(days=int(cfg["since_days"]))).isoformat()
    changed = False
    for vin, buy in carmax_buys(history).items():
        if buy["date"] < since or not watched(buy.get("buyer", ""), cfg["buyers"]) or vin in data["cars"]:
            continue
        data["cars"][vin] = {"vin": vin, "buyer": buy["buyer"], "date": buy["date"], "auction": buy["auction"], "lot": buy.get("lot", ""),
                             "price": buy.get("price"), "car": buy.get("car", ""), "miles": buy.get("miles", ""), "n": 0, "asked_at": 0,
                             "done_at": 0, "events": [], "verdict": ""}
        changed = True
    reports = carfax.load()
    for vin, car in data["cars"].items():
        if car.get("done_at") or not car.get("asked_at"):
            continue
        rec = reports.get(vin) or {}
        if float(rec.get("at") or 0) >= float(car["asked_at"]) - 60:        # отчёт пришёл после повторного заказа
            evts = after_events(carfax.saved_text(vin), car["date"])
            car.update(events=evts, verdict=verdict(evts) or "новых записей после CarMax нет", done_at=now)
            changed = True
        elif int(car.get("n") or 0) >= int(cfg["tries"]) and now - float(car["asked_at"]) > 3600:
            car.update(verdict="Carfax не открылся (две попытки)", done_at=now)
            changed = True
    if changed:
        _save(data)
    return summary(data, cfg, now)


def due(car: dict, cfg: dict, now: float) -> bool:
    try:
        when = dt.date.fromisoformat(car["date"]) + dt.timedelta(days=int(cfg["after_days"]))
    except (KeyError, ValueError):
        return False
    return when <= _today(now)


def next_vin(costs: dict, now: float | None = None) -> dict | None:
    """Очередь «Carfax сам» пуста — следующая машина для повторного Carfax (по одной, не чаще gap_s, не больше per_day в сутки)."""
    now = time.time() if now is None else now
    data = load()
    cfg = config(costs, data)
    asked = [float(c.get("asked_at") or 0) for c in data["cars"].values()]
    if asked and now - max(asked) < float(cfg["gap_s"]):
        return None
    if sum(1 for a in asked if now - a < 86400) >= int(cfg["per_day"]):
        return None
    retry = float(cfg["retry_hours"]) * 3600
    todo = [c for c in data["cars"].values() if not c.get("done_at") and due(c, cfg, now) and int(c.get("n") or 0) < int(cfg["tries"])
            and watched(c.get("buyer", ""), cfg["buyers"])
            and (not c.get("asked_at") or now - float(c["asked_at"]) > retry)]
    if not todo:
        return None
    todo.sort(key=lambda c: (int(c.get("n") or 0), c["date"]))
    car = todo[0]
    car["n"] = int(car.get("n") or 0) + 1
    car["asked_at"] = now
    _save(data)
    return car


def again(vin: str, now: float | None = None) -> bool:
    """«🔁 Ещё раз» на странице: машина снова в очереди (отчёт мог ещё не обновиться)."""
    data = load()
    car = data["cars"].get(str(vin).upper())
    if not car:
        return False
    car.update(n=0, asked_at=0, done_at=0, verdict="", events=[])
    _save(data)
    return True


def set_buyers(add: str = "", remove: str = "", costs: dict | None = None) -> list[str]:
    data = load()
    buyers = list(config(costs or {}, data)["buyers"])
    if add and _name(add) and _name(add) not in {_name(b) for b in buyers}:
        buyers.append(add.strip().upper())
    if remove:
        buyers = [b for b in buyers if _name(b) != _name(remove)]
    data["buyers"] = buyers
    _save(data)
    return buyers


def mark_viewed(now: float | None = None) -> None:
    data = load()
    data["viewed_at"] = time.time() if now is None else now
    _save(data)


def summary(data: dict, cfg: dict, now: float) -> dict:
    cars = list(data["cars"].values())
    done = [c for c in cars if c.get("done_at")]
    waiting = [c for c in cars if not c.get("done_at")]
    ready = [c for c in waiting if due(c, cfg, now)]
    later = sorted(c["date"] for c in waiting if not due(c, cfg, now))
    first = ""
    if later:
        first = (dt.date.fromisoformat(later[0]) + dt.timedelta(days=int(cfg["after_days"]))).isoformat()
    fresh = [c for c in done if float(c["done_at"]) > float(data.get("viewed_at") or 0)]
    return {"buyers": cfg["buyers"], "cars": len(cars), "done": len(done), "due": len(ready), "later": len(later), "next": first,
            "new": len(fresh), "after_days": cfg["after_days"],
            "latest": [{"car": c["car"], "buyer": c["buyer"], "verdict": c["verdict"]} for c in sorted(fresh, key=lambda c: -float(c["done_at"]))[:3]]}


def page(data: dict, costs: dict, now: float | None = None) -> str:
    import html
    now = time.time() if now is None else now
    cfg = config(costs, data)
    e = html.escape

    def ru(day):
        try:
            return dt.date.fromisoformat(day).strftime("%d.%m")
        except (TypeError, ValueError):
            return ""
    cars = list(data["cars"].values())
    done = sorted([c for c in cars if c.get("done_at")], key=lambda c: -float(c["done_at"]))
    wait = sorted([c for c in cars if not c.get("done_at")], key=lambda c: c["date"])
    head = ("<!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Повторный Carfax</title><style>body{font:14px/1.45 -apple-system,'Segoe UI',Arial,sans-serif;margin:16px;color:#1d2b45;background:#fff}"
            "h1{font-size:20px;margin:0 0 6px}h2{font-size:16px;margin:18px 0 6px}.muted{color:#667}table{border-collapse:collapse;width:100%;margin:6px 0 14px}"
            "th,td{border-bottom:1px solid #e3e8f0;padding:5px 7px;text-align:left;vertical-align:top}th{background:#1f3864;color:#fff;font-weight:600}"
            "td.n{text-align:right;white-space:nowrap}.wrap{overflow-x:auto}.tag{display:inline-block;margin:2px 4px 2px 0;padding:2px 8px;border-radius:10px;"
            "background:#eef3fb;border:1px solid #c9d6ea}button{cursor:pointer}.new{background:#e2efda}</style></head><body>")
    tags = "".join(f"<span class='tag'>{e(b)} <button type='button' data-remove='{e(b)}' onclick='fu({{remove:this.dataset.remove}})' "
                   "title='Убрать из списка'>✕</button></span>" for b in cfg["buyers"])
    intro = ("<h1>🔎 Повторный Carfax: что стало с машинами дилеров после CarMax</h1>"
             f"<p class='muted'>Через {cfg['after_days']} дн. после их покупки на CarMax программа сама заказывает Carfax ещё раз — той же очередью "
             "«Carfax сам», когда ваши машины уже обработаны (не больше " + str(cfg["per_day"]) + " в сутки). Из отчёта берутся записи после продажи "
             "на CarMax: аукцион, объявление о продаже, новый владелец / регистрация (штат), экспорт, сервис у дилера.</p>"
             f"<p><b>Следим за дилерами:</b> {tags} <input id='add' placeholder='Название, как на CarMax' size='26'> "
             "<button type='button' onclick=\"fu({add:document.getElementById('add').value})\">Добавить</button></p>"
             "<script>async function fu(b){await fetch('/api/followup',{method:'POST',headers:{'Content-Type':'application/json','X-LA':'1'},body:JSON.stringify(b)});"
             "location.reload()}</script>")
    viewed = float(data.get("viewed_at") or 0)
    t1 = ["<h2>Проверено</h2>" + ("<p class='muted'>Пока ничего — первые проверки начнутся через "
                                  f"{cfg['after_days']} дн. после покупки.</p>" if not done else
                                  "<div class='wrap'><table><tr><th>Машина</th><th>CarMax</th><th>Купил</th><th>Что стало после CarMax</th>"
                                  "<th>Записи Carfax</th><th></th></tr>")]
    for c in done:
        evts = "<br>".join(e(f"{ru(x['date'])}: {x['text'][:140]}") for x in c.get("events", [])[:6])
        t1.append(f"<tr class='{'new' if float(c['done_at']) > viewed else ''}'><td>{e(c['car'])}<br><span class='muted'>{e(c['vin'])}</span></td>"
                  f"<td>{ru(c['date'])} {e(c['auction'])} {e(c.get('lot', ''))}<br>${float(c.get('price') or 0):,.0f}</td><td>{e(c['buyer'])}</td>"
                  f"<td><b>{e(c['verdict'])}</b></td><td class='muted'>{evts}</td>"
                  f"<td><a href='/carfax/{e(c['vin'])}' target='_blank'>отчёт ↗</a><br><button type='button' data-vin='{e(c['vin'])}' onclick='fu({{again:this.dataset.vin}})'>"
                  "🔁 ещё раз</button></td></tr>")
    if done:
        t1.append("</table></div>")
    t2 = [f"<h2>Ждут проверки ({len(wait)})</h2><div class='wrap'><table><tr><th>Машина</th><th>CarMax</th><th>Купил</th><th>Проверка</th></tr>"]
    for c in wait:
        when = dt.date.fromisoformat(c["date"]) + dt.timedelta(days=int(cfg["after_days"]))
        state = ("запрошен, ждём отчёт" if c.get("asked_at") else "в очереди «Carfax сам»") if when <= _today(now) else f"с {when.strftime('%d.%m')}"
        t2.append(f"<tr><td>{e(c['car'])}<br><span class='muted'>{e(c['vin'])}</span></td><td>{ru(c['date'])} {e(c['auction'])} "
                  f"${float(c.get('price') or 0):,.0f}</td><td>{e(c['buyer'])}</td><td>{state}</td></tr>")
    t2.append("</table></div>")
    return head + intro + "".join(t1) + "".join(t2) + "</body></html>"
