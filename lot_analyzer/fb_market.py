"""Facebook Marketplace через Muse (агент Meta): цены объявлений — к машинам и в цену продажи.

Как устроено (редко и коротко — чтобы не тратить лимит Muse):
— программа сама собирает ГРУППЫ из машин-кандидатов: марка и модель, три модельных года, пробег (шаг 40 тыс.);
  в задание идут только группы, по которым свежих цен нет (refresh_days), не больше max_groups за раз;
— окно: «📋 Задание для Muse» — готовый текст (по-английски, ответ — CSV); вы вставляете его в Muse;
— ответ Muse (CSV) — «📥 Ответ Muse» в окне или файл fb_market*.csv / .txt в «Загрузки/Lot Analyzer» — программа
  читает сама; объявления хранятся в data/fb_market.json;
— к каждой машине — медиана цен похожих объявлений (тот же год ±1, пробег ±25 тыс., при нехватке — ±40 тыс.) и
  поправка цены продажи: KBB + weight × (медиана × asking_to_sale − KBB). Настройки — costs.json: fb_market.
Muse только смотрит объявления: продавцам не пишет, ничего не покупает и не продаёт (это есть в задании).
"""

from __future__ import annotations

import csv
import io
import json
import re
import statistics
import time
from pathlib import Path

from .paths import DATA_DIR

PATH = DATA_DIR / "fb_market.json"
HEADER = ["group", "year", "make", "model", "trim", "miles", "price", "city", "days_listed", "seller", "url"]
DEFAULTS = {"zip": "92620", "radius_miles": 75, "per_group": 12, "max_groups": 8, "refresh_days": 14, "asked_wait_days": 3,
            "max_age_days": 30, "min_listings": 4, "asking_to_sale": 0.93, "weight": 0.5, "use_in_sale": True}
MILES_STEP = 40000


def config(costs: dict) -> dict:
    return {**DEFAULTS, **(costs.get("fb_market") or {})}


def load() -> dict:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else {}
    except (OSError, ValueError):
        data = {}
    for key, empty in (("listings", []), ("answered", {}), ("asked", {}), ("task", {}), ("files", {})):
        data.setdefault(key, empty)
    return data


def _save(data: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_name(PATH.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PATH)


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
    """Машины, по которым нужны цены Facebook (чтобы не тратить лимит Muse — только те, что всерьёз рассматриваем):
    торги впереди, есть настоящий KBB, или ваша ставка, или машина в списке на осмотр; не «Отбой», не ПРОПУСТИТЬ / НЕВЫГОДНО,
    без брендированного титула и TMU."""
    def serious(r: dict) -> bool:
        return bool(_num(r.get("kbb_private_party_usd")) or _num(r.get("my_proxy_usd")) or r.get("inspect_manual"))
    return [r for r in rows if serious(r) and not r.get("passed") and not r.get("rejected") and not r.get("branded_hidden")
            and not r.get("tmu") and not str(r.get("calc_verdict") or "").startswith(("ПРОПУСТИТЬ", "НЕВЫГОДНО"))
            and r.get("year") and r.get("make") and r.get("model")]


def task(rows: list[dict], costs: dict, now: float | None = None) -> dict:
    """Задание для Muse: группы без свежих цен (больше машин — раньше), не больше max_groups. → {text, groups, due, total}."""
    cfg = config(costs)
    now = time.time() if now is None else now
    data = load()
    groups: dict[str, dict] = {}
    for r in candidates(rows):
        g = group_of(r.get("make", ""), r.get("model", ""), r.get("year"), r.get("odometer_miles"))
        if g:
            groups.setdefault(g["key"], {**g, "cars": 0})["cars"] += 1
    fresh = now - float(cfg["refresh_days"]) * 86400
    waiting = now - float(cfg["asked_wait_days"]) * 86400
    due = [g for k, g in groups.items() if float(data["answered"].get(k) or 0) < fresh and float(data["asked"].get(k) or 0) < waiting]
    due.sort(key=lambda g: (-g["cars"], g["key"]))
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
        text = (f"Search Facebook Marketplace near ZIP {cfg['zip']} (within {cfg['radius_miles']} miles). For each line below, find up to "
                f"{cfg['per_group']} current listings (posted in the last {cfg['max_age_days']} days) of that car: same make and model, model "
                "year within the range, mileage within the range (±10,000 is OK). Skip salvage / rebuilt titles, parts cars, \"take over "
                "payments\" ads and listings without a price or mileage.\n"
                "Only look at listings. Do not message sellers, do not make offers, do not buy or sell anything, do not share any personal info.\n\n"
                "Reply ONLY with CSV, no other text. Header exactly:\n" + ",".join(HEADER) + "\n"
                "group = G1, G2 …; seller = private or dealer; days_listed = number of days since posted (empty if not shown); "
                "price and miles = numbers only.\n\n" + "\n".join(lines))
    return {"text": text, "groups": pick, "ids": ids, "due": len(due), "total": len(groups),
            "last_answer": max([float(v) for v in data["answered"].values()] or [0]), "listings": len(data["listings"])}


def mark_asked(ids: dict, now: float | None = None) -> None:
    """Задание скопировали в Muse — эти группы не просим снова несколько дней; номера G1… — чтобы понять ответ."""
    now = time.time() if now is None else now
    data = load()
    for key in ids.values():
        data["asked"][key] = now
    data["task"] = {"ids": ids, "at": now}
    _save(data)


def parse_answer(text: str) -> list[dict]:
    """CSV из ответа Muse (можно с ```csv … ``` и пояснениями вокруг) → объявления."""
    body = str(text or "")
    fenced = re.search(r"```(?:csv)?\s*\n(.*?)```", body, re.S | re.I)
    body = fenced.group(1) if fenced else body
    lines = body.splitlines()
    start = next((i for i, ln in enumerate(lines) if re.match(r"\s*group\s*,\s*year\s*,", ln, re.I)), None)
    if start is None:
        return []
    out = []
    reader = csv.reader(io.StringIO("\n".join(lines[start:])))
    head = [h.strip().lower() for h in next(reader, [])]
    for cells in reader:
        cells = _fix_thousands(cells, len(head))
        rec = {k: (cells[i].strip() if i < len(cells) else "") for i, k in enumerate(head)}
        price, miles, year = _num(rec.get("price")), _num(rec.get("miles")), _num(rec.get("year"))
        if not (1000 <= price <= 300000 and 1990 < year < 2100 and rec.get("model")):
            continue
        out.append({"group": rec.get("group", "").upper(), "year": year, "make": rec.get("make", ""), "model": rec.get("model", ""),
                    "trim": rec.get("trim", ""), "miles": miles or None, "price": price, "city": rec.get("city", ""),
                    "days": _num(rec.get("days_listed")) or None, "seller": "dealer" if "dealer" in rec.get("seller", "").lower() else "private",
                    "url": rec.get("url", "")[:300]})
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


def add_answer(text: str, now: float | None = None) -> dict:
    """Ответ Muse → в базу объявлений. Группы задания (G1…) отмечаются как полученные — снова их не спросим refresh_days."""
    now = time.time() if now is None else now
    found = parse_answer(text)
    if not found:
        return {"ok": False, "error": "в ответе нет CSV с заголовком «group,year,make,model,…» — попросите Muse ответить только CSV"}
    data = load()
    ids = (data.get("task") or {}).get("ids") or {}
    seen = {(x.get("url") or "", x["price"], x["year"], x.get("miles")) for x in data["listings"]}
    added = 0
    for x in found:
        x["at"] = now
        x["group_key"] = ids.get(x["group"], "")
        sig = (x.get("url") or "", x["price"], x["year"], x.get("miles"))
        if sig in seen:
            continue
        seen.add(sig)
        data["listings"].append(x)
        added += 1
    for gid in {x["group"] for x in found}:
        if ids.get(gid):
            data["answered"][ids[gid]] = now
    keep = now - 120 * 86400                       # объявления старше 4 месяцев — не нужны
    data["listings"] = [x for x in data["listings"] if float(x.get("at") or 0) > keep]
    _save(data)
    return {"ok": True, "added": added, "found": len(found), "groups": len({x["group"] for x in found})}


def import_files(folders: list[Path]) -> int:
    """Файлы ответа Muse в «Загрузках» (fb_market*.csv / .txt) — прочитать один раз."""
    data = load()
    done = 0
    for folder in folders:
        if not Path(folder).is_dir():
            continue
        for path in Path(folder).glob("fb_market*"):
            if path.suffix.lower() not in (".csv", ".txt") or not path.is_file():
                continue
            stamp = f"{path.stat().st_mtime:.0f}"
            if data["files"].get(path.name) == stamp:
                continue
            try:
                add_answer(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            data = load()
            data["files"][path.name] = stamp
            _save(data)
            done += 1
    return done


def comps(row: dict, listings: list[dict], cfg: dict, now: float) -> list[dict]:
    """Похожие объявления: та же марка и модель, год ±1, пробег ±25 тыс. (мало — ±40 тыс.), не старше max_age_days."""
    make, model, year, miles = str(row.get("make", "")).lower(), _model(row.get("model", "")), _num(row.get("year")), _num(row.get("odometer_miles"))
    fresh = now - float(cfg["max_age_days"]) * 86400
    base = [x for x in listings if float(x.get("at") or 0) >= fresh and str(x.get("make", "")).lower() == make
            and _model(x.get("model")) == model and abs(int(x["year"]) - year) <= 1]
    for spread in (25000, 40000):
        near = [x for x in base if not miles or not x.get("miles") or abs(int(x["miles"]) - miles) <= spread]
        if len(near) >= int(cfg["min_listings"]):
            break
    private = [x for x in near if x.get("seller") != "dealer"]
    return private if len(private) >= int(cfg["min_listings"]) else near


def attach(rows: list[dict], costs: dict, now: float | None = None) -> None:
    """К машинам: медиана цен похожих объявлений Facebook (fb_median_usd, fb_n) — окно и расчёт цены продажи."""
    cfg = config(costs)
    now = time.time() if now is None else now
    listings = load()["listings"]
    for row in rows:
        row["fb_median_usd"] = row["fb_n"] = row["fb_note"] = ""
        if not listings or not row.get("model"):
            continue
        found = comps(row, listings, cfg, now)
        if len(found) < int(cfg["min_listings"]):
            continue
        med = statistics.median(x["price"] for x in found)
        newest = max(float(x.get("at") or 0) for x in found)
        row["fb_median_usd"] = f"{med:.0f}"
        row["fb_n"] = str(len(found))
        row["fb_note"] = (f"Facebook: медиана {len(found)} объявлений ${med:,.0f} ({'частные' if all(x.get('seller') != 'dealer' for x in found) else 'частные и дилеры'}, "
                          f"год ±1, похожий пробег; Muse {time.strftime('%m/%d', time.localtime(newest))})")
