"""Цены объявлений (Facebook Marketplace через Muse, CarGurus через Claude в Chrome) — к машинам и в цену продажи.

Как устроено (редко и коротко — чтобы не тратить лимит агента):
— программа сама собирает ГРУППЫ из машин-кандидатов: марка и модель, три модельных года, пробег (шаг 40 тыс.);
  в задание идут только группы, по которым свежих цен нет (refresh_days), не больше max_groups за раз;
— окно: «📋 Задание …» — готовый текст (по-английски, ответ — CSV); вы вставляете его агенту (Muse, Claude в Chrome);
— ответ (CSV) — «📥 Ответ …» в окне или файлом (fb_market*.csv, cargurus*.csv) в «Загрузки/Lot Analyzer» — программа
  читает сама; объявления хранятся в data/fb_market.json и data/cargurus.json;
— к каждой машине — медиана цен похожих объявлений (тот же год ±1, пробег ±25 тыс., при нехватке — ±40 тыс.);
  цена продажи: KBB + weight × (цена по объявлениям − KBB) — bid.py. Настройки — costs.json: fb_market, cargurus.
Агент только смотрит объявления: продавцам не пишет, ничего не покупает и не продаёт (это есть в задании).
Здесь — общий механизм и Facebook; CarGurus — cargurus.py.
"""

from __future__ import annotations

import csv
import io
import json
import re
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .paths import DATA_DIR

PATH = DATA_DIR / "fb_market.json"
HEADER = ["group", "year", "make", "model", "trim", "miles", "price", "city", "days_listed", "seller", "url"]
DEFAULTS = {"zip": "92620", "radius_miles": 75, "per_group": 12, "max_groups": 8, "refresh_days": 14, "asked_wait_days": 3,
            "max_age_days": 30, "min_listings": 3, "asking_to_sale": 0.93, "weight": 0.5, "use_in_sale": True}
MILES_STEP = 40000


@dataclass
class Source:
    """Источник объявлений: где хранить, как спросить агента, что взять из ответа, что положить в строку машины."""
    name: str                                  # для пояснений: «Facebook», «CarGurus»
    agent: str                                 # кто ищет: «Muse», «Claude в Chrome»
    path: Callable[[], Path]                   # файл базы (функция — чтобы тесты могли подменить путь)
    header: list[str]
    defaults: dict
    cfg_key: str                               # раздел costs.json
    files: str                                 # начало имени файла ответа в «Загрузках»
    prefix: str                                # поля строки: fb_median_usd, cg_median_usd …
    intro: Callable[[dict], str]               # начало задания (что и где искать)
    extra: Callable[[dict], dict] = field(default=lambda rec: {})   # доп. поля объявления из строки CSV


def _fb_intro(cfg: dict) -> str:
    return (f"Search Facebook Marketplace near ZIP {cfg['zip']} (within {cfg['radius_miles']} miles). For each line below, find up to "
            f"{cfg['per_group']} current listings (posted in the last {cfg['max_age_days']} days) of that car: same make and model, model "
            "year within the range, mileage within the range (±10,000 is OK). Skip salvage / rebuilt titles, parts cars, \"take over "
            "payments\" ads and listings without a price or mileage.\n"
            "Only look at listings. Do not message sellers, do not make offers, do not buy or sell anything, do not share any personal info.\n\n")


FB = Source(name="Facebook", agent="Muse", path=lambda: PATH, header=HEADER, defaults=DEFAULTS, cfg_key="fb_market", files="fb_market",
            prefix="fb", intro=_fb_intro)


def config(costs: dict, src: Source = FB) -> dict:
    return {**src.defaults, **(costs.get(src.cfg_key) or {})}


def load(src: Source = FB) -> dict:
    path = src.path()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        data = {}
    for key, empty in (("listings", []), ("answered", {}), ("asked", {}), ("task", {}), ("files", {})):
        data.setdefault(key, empty)
    return data


def _save(data: dict, src: Source = FB) -> None:
    path = src.path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _num(value) -> int:
    digits = re.sub(r"[^\d]", "", str(value or "").split(".")[0])
    return int(digits) if digits else 0


def _model(text: str) -> str:
    """«Odyssey EX-L» → «odyssey», «CR-V» → «cr-v», «RAV4 Hybrid» → «rav4»: первое слово модели, без регистра."""
    words = re.sub(r"[^a-z0-9\- ]", " ", str(text or "").lower()).split()
    return words[0] if words else ""


def group_of(make: str, model: str, year, miles) -> dict | None:
    """Группа машины: марка, модель, три модельных года (2013–2015), пробег шагом 40 тыс. (80–120 тыс.)."""
    y, m = _num(year), _num(miles)
    if not (make and _model(model) and 1990 < y < 2100):
        return None
    y0 = y - (y % 3)
    lo = (m // MILES_STEP) * MILES_STEP if m else 0
    key = f"{make.lower()}|{_model(model)}|{y0}-{y0 + 2}|{lo}-{lo + MILES_STEP}"
    return {"key": key, "make": make, "model": str(model).split()[0] if model else "", "years": [y0, y0 + 2],
            "miles": [lo, lo + MILES_STEP] if m else None}


def candidates(rows: list[dict]) -> list[dict]:
    """Машины, по которым нужны цены объявлений (чтобы не тратить лимит агента — только те, что всерьёз рассматриваем):
    торги впереди, есть настоящий KBB, или ваша ставка, или машина в списке на осмотр; не «Отбой», не ПРОПУСТИТЬ / НЕВЫГОДНО,
    без брендированного титула и TMU."""
    def serious(r: dict) -> bool:
        return bool(_num(r.get("kbb_private_party_usd")) or _num(r.get("my_proxy_usd")) or r.get("inspect_manual"))
    return [r for r in rows if serious(r) and not r.get("passed") and not r.get("rejected") and not r.get("branded_hidden")
            and not r.get("tmu") and not str(r.get("calc_verdict") or "").startswith(("ПРОПУСТИТЬ", "НЕВЫГОДНО"))
            and r.get("year") and r.get("make") and r.get("model")]


def task(rows: list[dict], costs: dict, now: float | None = None, src: Source = FB, vins: list[str] | None = None) -> dict:
    """Задание агенту: группы без свежих цен (больше машин — раньше), не больше max_groups. → {text, groups, due, total}.
    vins — конкретные машины («🛒 Рынок» в окне): по каждой своя строка (год ±1, пробег ±20 тыс.), без правила «раз в 14 дней»."""
    cfg = config(costs, src)
    now = time.time() if now is None else now
    data = load(src)
    if vins:
        return _task_for(rows, cfg, data, src, [v.upper() for v in vins])
    from .inspection import _day as sale_day
    groups: dict[str, dict] = {}
    for r in candidates(rows):
        g = group_of(r.get("make", ""), r.get("model", ""), r.get("year"), r.get("odometer_miles"))
        if g:
            got = groups.setdefault(g["key"], {**g, "cars": 0, "soon": "99"})
            got["cars"] += 1
            got["soon"] = min(got["soon"], sale_day(r.get("sale_date", "")) or "99")
    fresh = now - float(cfg["refresh_days"]) * 86400
    waiting = now - float(cfg["asked_wait_days"]) * 86400
    due = [g for k, g in groups.items() if float(data["answered"].get(k) or 0) < fresh and float(data["asked"].get(k) or 0) < waiting]
    due.sort(key=lambda g: (g["soon"], -g["cars"], g["key"]))      # сначала — машины ближайших торгов
    pick = due[:int(cfg["max_groups"])]
    lines = []
    ids = {}
    for i, g in enumerate(pick, 1):
        gid = f"G{i}"
        ids[gid] = g["key"]
        miles = f", {g['miles'][0]:,}-{g['miles'][1]:,} miles" if g["miles"] else ""
        lines.append(f"{gid}: {g['make']} {g['model']} {g['years'][0]}-{g['years'][1]}{miles}")
    text = ""
    if pick:
        text = (src.intro(cfg) + "Reply ONLY with CSV, no other text. Header exactly:\n" + ",".join(src.header) + "\n"
                "group = G1, G2 …; " + ("seller = private or dealer; days_listed = number of days since posted (empty if not shown); "
                                         if "days_listed" in src.header else "")
                + "prices and miles = numbers only.\n\n" + "\n".join(lines))
    return {"text": text, "groups": pick, "ids": ids, "due": len(due), "total": len(groups),
            "last_answer": max([float(v) for v in data["answered"].values()] or [0]), "listings": len(data["listings"]),
            "agent": src.agent, "name": src.name}


def _task_for(rows: list[dict], cfg: dict, data: dict, src: Source, vins: list[str]) -> dict:
    """Задание по выбранным машинам: по строке на машину — марка, модель, годы ±1, пробег ±20 тыс., комплектация."""
    cars = [r for r in rows if str(r.get("vin", "")).upper() in vins and r.get("year") and r.get("make") and r.get("model")]
    lines, ids, picked = [], {}, []
    for i, r in enumerate(cars[:10], 1):
        g = group_of(r.get("make", ""), r.get("model", ""), r.get("year"), r.get("odometer_miles"))
        if not g:
            continue
        gid = f"C{i}"                                   # C1, C2 … — не путать с G1… общего задания
        ids[gid] = g["key"]
        y, miles = _num(r.get("year")), _num(r.get("odometer_miles"))
        span = f", {max(0, miles - 20000) // 1000 * 1000:,}-{(miles + 20999) // 1000 * 1000:,} miles" if miles else ""   # ±20 тыс., до тысяч
        trim = f" (like {r['trim']})" if r.get("trim") else ""
        lines.append(f"{gid}: {r['make']} {str(r['model']).split()[0]} {y - 1}-{y + 1}{span}{trim}")
        picked.append({**g, "cars": 1, "car": f"{y} {r['make']} {r['model']}", "vin": r.get("vin", "")})
    text = ""
    if lines:
        text = (src.intro(cfg) + "Reply ONLY with CSV, no other text. Header exactly:\n" + ",".join(src.header) + "\n"
                "group = C1, C2 … (the line the listing is for); " + ("seller = private or dealer; days_listed = number of days since posted (empty if not shown); "
                                                                       if "days_listed" in src.header else "")
                + "prices and miles = numbers only.\n\n" + "\n".join(lines))
    return {"text": text, "groups": picked, "ids": ids, "due": len(picked), "total": len(picked),
            "last_answer": max([float(v) for v in data["answered"].values()] or [0]), "listings": len(data["listings"]),
            "agent": src.agent, "name": src.name, "missing": [v for v in vins if v not in {str(r.get('vin', '')).upper() for r in cars}]}


def mark_asked(ids: dict, now: float | None = None, src: Source = FB) -> None:
    """Задание скопировали агенту — эти группы не просим снова несколько дней; номера G1… — чтобы понять ответ."""
    now = time.time() if now is None else now
    data = load(src)
    for key in ids.values():
        data["asked"][key] = now
    old = (data.get("task") or {}).get("ids") or {}
    data["task"] = {"ids": {**old, **ids}, "at": now}    # общее (G1…) и по выбранным машинам (C1…) — оба ответа поймём
    _save(data, src)


# Заголовки, как их может назвать агент (файл от Claude, таблица Muse) → наши колонки.
SYNONYMS = {"mileage": "miles", "odometer": "miles", "mi": "miles", "list_price": "price", "asking_price": "price", "price_usd": "price",
            "instant_market_value": "imv", "cargurus_imv": "imv", "imv_usd": "imv", "market_value": "imv", "deal_rating": "deal",
            "days_on_cargurus": "days_on_market", "days_on_site": "days_on_market", "dom": "days_on_market", "days_listed": "days_listed",
            "location": "city", "link": "url", "listing_url": "url", "seller_type": "seller", "distance_mi": "distance",
            "distance_miles": "distance", "group_id": "group", "model_year": "year", "car": "vehicle", "title": "vehicle",
            "listing": "vehicle", "listing_title": "vehicle", "year_make_model": "vehicle", "name": "vehicle", "vehicle_name": "vehicle"}


def _head(cell: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", str(cell or "").strip().lower()).strip("_")
    return SYNONYMS.get(key, key)


def _split(line: str, delim: str) -> list[str]:
    if delim == "|":                                  # таблица Markdown: | Year | Make | … |
        return [c.strip() for c in line.strip().strip("|").split("|")]
    return next(csv.reader([line], delimiter=delim), [])


def _is_head(heads: list[str]) -> bool:
    cells = set(heads)
    return "price" in cells and (({"year", "make"} <= cells or {"year", "model"} <= cells) or "vehicle" in cells)


def parse_answer(text: str, src: Source = FB) -> list[dict]:
    """Ответ агента → объявления. Понимает CSV (можно с ```csv … ``` и пояснениями вокруг), табуляцию, «;» и таблицу Markdown;
    заголовки — как получилось (Mileage, Deal Rating, Instant Market Value …); машина может быть одной колонкой
    («Vehicle: 2014 Toyota Sienna LE»)."""
    body = str(text or "").lstrip("\ufeff")
    fenced = re.search(r"```(?:csv|tsv)?\s*\n(.*?)```", body, re.S | re.I)
    body = fenced.group(1) if fenced else body
    lines = body.splitlines()
    head, delim, start = None, ",", 0
    for i, ln in enumerate(lines):
        for d in (",", "\t", ";", "|"):
            if d in ln and _is_head([_head(c) for c in _split(ln, d)]):
                head, delim, start = [_head(c) for c in _split(ln, d)], d, i + 1
                break
        if head:
            break
    if not head:
        return []
    out = []
    for ln in lines[start:]:
        if not ln.strip() or re.fullmatch(r"[\s|:\-+]+", ln):    # пустые строки и черта под заголовком Markdown
            continue
        cells = _split(ln, delim)
        if delim == ",":
            cells = _fix_thousands(cells, len(head))
        rec = {k: (cells[i].strip() if i < len(cells) else "") for i, k in enumerate(head)}
        if rec.get("vehicle") and not (rec.get("year") and rec.get("model")):
            m = re.search(r"\b((?:19|20)\d{2})\s+([A-Za-z-]+)\s+([A-Za-z0-9-]+)\s*(.*)", rec["vehicle"])
            if m:
                rec.setdefault("year", m.group(1))
                rec["year"] = rec["year"] or m.group(1)
                rec["make"] = rec.get("make") or m.group(2)
                rec["model"] = rec.get("model") or m.group(3)
                rec["trim"] = rec.get("trim") or m.group(4)
        price, miles, year = _num(rec.get("price")), _num(rec.get("miles")), _num(rec.get("year"))
        if not (1000 <= price <= 300000 and 1990 < year < 2100 and rec.get("model")):
            continue
        out.append({"group": rec.get("group", "").upper(), "year": year, "make": rec.get("make", ""), "model": rec.get("model", ""),
                    "trim": rec.get("trim", ""), "miles": miles or None, "price": price, "city": rec.get("city", ""),
                    "days": _num(rec.get("days_listed") or rec.get("days_on_market")) or None,
                    "seller": "dealer" if "dealer" in rec.get("seller", "").lower() else "private",
                    "url": rec.get("url", "")[:300], **src.extra(rec)})
    return out


def _fix_thousands(cells: list[str], width: int) -> list[str]:
    """«$11,500» без кавычек разваливает строку CSV на лишнюю колонку — склеиваем «$11» + «500», пока колонок не станет как в заголовке."""
    cells = list(cells)
    i = 0
    while len(cells) > width and i < len(cells) - 1:
        if re.fullmatch(r"\s*\$?\d{1,3}", cells[i]) and re.fullmatch(r"\d{3}(?:\.\d+)?\s*", cells[i + 1]):
            cells[i:i + 2] = [cells[i].strip() + cells[i + 1].strip()]
            continue
        i += 1
    return cells


def add_answer(text: str, now: float | None = None, src: Source = FB) -> dict:
    """Ответ агента → в базу объявлений. Группы задания (G1…) отмечаются как полученные — снова их не спросим refresh_days."""
    now = time.time() if now is None else now
    found = parse_answer(text, src)
    if not found:
        return {"ok": False, "error": f"в ответе нет CSV с заголовком «group,year,make,model,…» — попросите {src.agent} ответить только CSV"}
    data = load(src)
    ids = (data.get("task") or {}).get("ids") or {}
    asked = set(ids.values())
    seen = {(x.get("url") or "", x["price"], x["year"], x.get("miles")) for x in data["listings"]}
    added = 0
    for x in found:
        x["at"] = now
        x["group_key"] = ids.get(x["group"], "")
        if not x["group_key"]:                     # нет колонки group — группа по самой машине (марка, модель, годы, пробег)
            g = group_of(x.get("make", ""), x.get("model", ""), x.get("year"), x.get("miles"))
            if g and g["key"] in asked:
                x["group_key"] = g["key"]
        sig = (x.get("url") or "", x["price"], x["year"], x.get("miles"))
        if sig in seen:
            continue
        seen.add(sig)
        data["listings"].append(x)
        added += 1
    for key in {x["group_key"] for x in found if x["group_key"]}:
        data["answered"][key] = now
    keep = now - 120 * 86400                       # объявления старше 4 месяцев — не нужны
    data["listings"] = [x for x in data["listings"] if float(x.get("at") or 0) > keep]
    _save(data, src)
    return {"ok": True, "added": added, "found": len(found), "dup": len(found) - added, "total": len(data["listings"]),
            "groups": len({x["group_key"] or x["group"] for x in found})}


def import_files(folders: list[Path], src: Source = FB) -> int:
    """Файлы ответа в «Загрузках» (fb_market*.csv / cargurus*.csv, .txt) — прочитать один раз."""
    data = load(src)
    done = 0
    for folder in folders:
        if not Path(folder).is_dir():
            continue
        for path in Path(folder).iterdir():           # имя — любое со словом источника: «CarGurus_results (1).csv», «fb_market.csv»
            if src.files not in path.name.lower().replace(" ", "").replace("-", "_") or path.suffix.lower() not in (".csv", ".txt") or not path.is_file():
                continue
            stamp = f"{path.stat().st_mtime:.0f}"
            if data["files"].get(path.name) == stamp:
                continue
            try:
                add_answer(path.read_text(encoding="utf-8", errors="replace"), src=src)
            except OSError:
                continue
            data = load(src)
            data["files"][path.name] = stamp
            _save(data, src)
            done += 1
    return done


def _index(listings: list[dict], cfg: dict, now: float) -> dict[tuple[str, str], list[dict]]:
    """Свежие объявления по (марка, слово модели): «Toyota Sienna LE» — под ("toyota", "sienna"), ("toyota", "le") …"""
    fresh = now - float(cfg["max_age_days"]) * 86400
    out: dict[tuple[str, str], list[dict]] = {}
    for x in listings:
        if float(x.get("at") or 0) < fresh:
            continue
        x_make = (str(x.get("make", "")).lower().split() or [""])[0]
        for word in set(re.findall(r"[a-z0-9\-]+", f"{x.get('make', '')} {x.get('model', '')}".lower())):
            out.setdefault((x_make, word), []).append(x)
    return out


def comps(row: dict, listings: list[dict], cfg: dict, now: float, index: dict | None = None) -> list[dict]:
    """Похожие объявления: та же марка и модель, год ±1, пробег ±25 тыс. (мало — ±40 тыс.), не старше max_age_days."""
    make, model, year, miles = (str(row.get("make", "")).lower().split() or [""])[0], _model(row.get("model", "")), _num(row.get("year")), _num(row.get("odometer_miles"))
    index = _index(listings, cfg, now) if index is None else index
    pool = index.get((make, model), []) + index.get(("", model), [])         # у объявления без марки — только по модели
    base = [x for x in pool if abs(int(x["year"]) - year) <= 1]
    for spread in (25000, 40000):
        near = [x for x in base if not miles or not x.get("miles") or abs(int(x["miles"]) - miles) <= spread]
        if len(near) >= int(cfg["min_listings"]):
            break
    private = [x for x in near if x.get("seller") != "dealer"]
    return private if len(private) >= int(cfg["min_listings"]) else near


def attach(rows: list[dict], costs: dict, now: float | None = None, src: Source = FB) -> None:
    """К машинам: медиана цен похожих объявлений (fb_median_usd, fb_n, fb_note / cg_…) — окно и расчёт цены продажи."""
    cfg = config(costs, src)
    now = time.time() if now is None else now
    listings = load(src)["listings"]
    index = _index(listings, cfg, now)
    p = src.prefix
    for row in rows:
        row[f"{p}_median_usd"] = row[f"{p}_n"] = row[f"{p}_note"] = row[f"{p}_links"] = ""
        if src is not FB:
            row[f"{p}_imv_usd"] = row[f"{p}_days"] = ""
        if not listings or not row.get("model"):
            continue
        found = comps(row, listings, cfg, now, index)
        if len(found) < int(cfg["min_listings"]):
            continue
        med = statistics.median(x["price"] for x in found)
        newest = max(float(x.get("at") or 0) for x in found)
        row[f"{p}_median_usd"] = f"{med:.0f}"
        row[f"{p}_n"] = str(len(found))
        sellers = "частные" if all(x.get("seller") != "dealer" for x in found) else "дилеры" if all(x.get("seller") == "dealer" for x in found) else "частные и дилеры"
        note = f"{src.name}: медиана {len(found)} объявлений ${med:,.0f} ({sellers}, год ±1, похожий пробег"
        imv = [x["imv"] for x in found if x.get("imv")]
        days = [x["days"] for x in found if x.get("days")]
        if src is not FB:
            if len(imv) >= int(cfg["min_listings"]):
                row[f"{p}_imv_usd"] = f"{statistics.median(imv):.0f}"
                note += f"; IMV ${statistics.median(imv):,.0f}"
            if days:
                row[f"{p}_days"] = f"{statistics.median(days):.0f}"
                note += f"; в продаже ~{statistics.median(days):.0f} дн."
        row[f"{p}_note"] = note + f"; {src.agent} {time.strftime('%m/%d', time.localtime(newest))})"
        row[f"{p}_links"] = json.dumps(links(row, found), ensure_ascii=False)


def links(row: dict, found: list[dict], limit: int = 8) -> list[dict]:
    """Объявления-конкуренты для окна (номера 1, 2, 3 … — каждый открывает своё объявление): ближе по пробегу и году — первыми."""
    miles, year = _num(row.get("odometer_miles")), _num(row.get("year"))
    near = sorted(found, key=lambda x: (abs(int(x.get("miles") or miles) - miles), abs(int(x["year"]) - year), x["price"]))
    out = []
    for x in near[:limit]:
        parts = [f"{x['year']} {x.get('make', '')} {x.get('model', '')} {x.get('trim', '')}".replace("  ", " ").strip(),
                 f"{int(x['miles']):,} mi" if x.get("miles") else "", f"${x['price']:,}",
                 f"IMV ${int(x['imv']):,}" if x.get("imv") else "", f"{x['deal']} deal" if x.get("deal") else "",
                 f"{x['days']} дн." if x.get("days") else "", x.get("city", ""), "дилер" if x.get("seller") == "dealer" else "частник"]
        url = str(x.get("url") or "")
        out.append({"u": url if url.startswith(("https://", "http://")) else "", "t": " · ".join(p for p in parts if p)})
    return out
