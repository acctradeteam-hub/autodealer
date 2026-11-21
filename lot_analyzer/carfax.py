"""Carfax: отчёт, открытый на vin-reports-pro.com (или carfax.com), — коротко к машине в окне, в списке на осмотр и на телефоне.

Пути в программу:
— кнопка «📄 Carfax» в окне (на Mac или с телефона): расширение открывает отчёт на Carfax Online (carfaxonline.com/vhr/<VIN>,
  Carfax for Dealers) с вашим входом в Chrome и присылает его текст (POST /api/carfax);
— отчёт, открытый на сайте вручную, — так же; PDF отчёта (раньше — vin-reports-pro) — POST /api/carfax_pdf.
Разбор — по обычным фразам Carfax («CARFAX 1-Owner», «Accident reported», «Damage Severity: Moderate», «Point of
Impact: Front», «Owned in: Ohio», «Personal vehicle», «Salvage title» …). data/carfax.json: VIN → {url, at, summary}.
Итоги — по-английски, как в самом Carfax: «1 owner · 1 moderate accident (front) · 19 service records».
Полный текст отчёта хранится в программе (data/carfax/text/<VIN>.txt.gz) — страница /carfax/<VIN> в окне и на телефоне,
на сайт Carfax заходить заново не нужно. Сервисные записи — регулярность ТО и календарь по марке (config/maintenance.json):
что по регламенту пора (ремень ГРМ, свечи, жидкости), а записи о работе нет.
Найденное идёт и в расчёт (history_text): ДТП, 4+ владельца, аренда — скидка к цене продажи (history_discounts),
рама, подушки, брендированный титул, скрутка — стоп-факторы, как у замечаний аукциона.
"""

from __future__ import annotations

import datetime as dt
import gzip
import html
import json
import re
import statistics
import time
from pathlib import Path

from .paths import DATA_DIR

PATH = DATA_DIR / "carfax.json"
PDF_DIR = DATA_DIR / "carfax"
TEXT_DIR = PDF_DIR / "text"                # полный текст отчётов: <VIN>.txt.gz — смотреть в программе, без сайта
MAINT_PATH = Path(__file__).resolve().parent.parent / "config" / "maintenance.json"
VIN_RE = re.compile(r"[A-HJ-NPR-Z0-9]{17}")

STATES = ("Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware", "Florida", "Georgia",
          "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts",
          "Michigan", "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey",
          "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island",
          "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington", "West Virginia",
          "Wisconsin", "Wyoming", "District of Columbia")
# Соль на дорогах зимой — ржавчина днища и рамы.
SALT = {"Connecticut", "Delaware", "Illinois", "Indiana", "Iowa", "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
        "Missouri", "Nebraska", "New Hampshire", "New Jersey", "New York", "North Dakota", "Ohio", "Pennsylvania", "Rhode Island",
        "South Dakota", "Vermont", "West Virginia", "Wisconsin", "District of Columbia"}
TITLE_BAD = re.compile(r"\b(salvage|rebuilt|reconstructed|junk|flood|fire damage|hail damage|lemon|manufacturer buyback|"
                       r"not actual mileage|odometer rollback|exceeds mechanical limits|total loss)\b", re.I)
USAGE = (("аренда", r"\brental\b"), ("флит", r"\bfleet\b"), ("лизинг", r"\blease\b"), ("такси", r"\btaxi\b"),
         ("коммерческая", r"\bcommercial\b"), ("полиция", r"\bpolice\b"), ("личная", r"\bpersonal\b"))
SEVERITY = {"minor": "лёгкое", "moderate": "среднее", "severe": "сильное", "functional": "сильное", "disabling": "сильное"}
IMPACT = {"front": "спереди", "rear": "сзади", "left": "слева", "right": "справа", "side": "сбоку", "undercarriage": "днище",
          "roof": "крыша", "rollover": "опрокидывание"}
# Итоги — по-английски (как в Carfax); разбор хранит русские слова — переводим при показе.
EN = {"лёгкое": "minor", "среднее": "moderate", "сильное": "severe",
      "спереди": "front", "сзади": "rear", "слева": "left side", "справа": "right side", "сбоку": "side", "днище": "undercarriage",
      "крыша": "roof", "опрокидывание": "rollover",
      "аренда": "rental", "флит": "fleet", "лизинг": "lease", "такси": "taxi", "коммерческая": "commercial", "полиция": "police",
      "личная": "personal", "проблема с титулом (см. отчёт)": "title brand (see report)",
      "пробег — бренд в титуле (см. отчёт)": "odometer brand (see report)"}
# Записи сервиса Carfax → вид работы (календарь ТО — config/maintenance.json).
SERVICE_TYPES = (
    ("oil", r"oil (?:and filter )?chang|oil and filter|\blube\b"),
    ("rotation", r"tires? rotated|tire rotation"),
    ("brake_fluid", r"brake fluid"),
    ("transmission", r"transmission (?:fluid|service|flush|serviced)|\batf\b|cvt fluid"),
    ("coolant", r"coolant|antifreeze|radiator (?:flush|fluid|serviced)|cooling system (?:flush|serviced)"),
    ("spark_plugs", r"spark plugs?"),
    ("timing_belt", r"timing belt"),
    ("water_pump", r"water pump"),
    ("air_filter", r"(?<!cabin )air filter"),
    ("cabin_filter", r"cabin (?:air )?filter"),
    ("battery", r"battery (?:replaced|serviced|installed|tested)|new battery"),
    ("brakes", r"brake (?:pads?|rotors?|shoes?)|brakes? (?:replaced|serviced)"),
    ("alignment", r"alignment"),
    ("tires", r"tires? replaced|new tires?|tires? (?:mounted|installed)"),
    ("maintenance", r"recommended maintenance|scheduled maintenance|maintenance (?:inspection|performed|completed)|\d{2},?000 mile service"),
    ("recall", r"recall"),
    ("emissions", r"emissions? (?:inspection|test)"),
)
SERVICE_NAMES = {"oil": "Oil & filter", "rotation": "Tire rotation", "brake_fluid": "Brake fluid", "transmission": "Transmission fluid",
                 "coolant": "Coolant", "spark_plugs": "Spark plugs", "timing_belt": "Timing belt", "water_pump": "Water pump",
                 "air_filter": "Air filter", "cabin_filter": "Cabin filter", "battery": "Battery", "brakes": "Brakes",
                 "alignment": "Alignment", "tires": "Tires", "maintenance": "Maintenance", "recall": "Recall work", "emissions": "Emissions test"}
_DATE_LINE = re.compile(r"(?m)^[ \t]*(\d{1,2})/(\d{1,2})/(\d{4})\b")
# Заголовки разделов отчёта — запись сервиса до них не тянется.
_SECTION = re.compile(r"(?im)^[ \t]*(?:recent service highlights|additional history|title history|ownership history|detailed history|"
                      r"future reliability|accident / damage history|owner \d+\s*$|full glossary)")


def load() -> dict[str, dict]:
    try:
        return json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else {}
    except (OSError, ValueError):
        return {}


def _count(word_re: str, text: str) -> int:
    return len(re.findall(word_re, text, re.I))


def _section(t: str, head: str, stop: str) -> str:
    """Кусок отчёта от заголовка до следующего (без учёта регистра); нет заголовка — пусто."""
    m = re.search(head, t, re.I | re.M)
    if not m:
        return ""
    rest = t[m.end():]
    n = re.search(stop, rest, re.I | re.M)
    return rest[:n.start()] if n else rest[:3000]


def parse(text: str) -> dict:
    """Главное из текста отчёта Carfax (как его показывает vin-reports-pro.com / carfax.com)."""
    t = re.sub(r"[ \t\u00a0]+", " ", text or "")
    cut = re.search(r"\bfull glossary\b", t, re.I)           # в конце — справочник со списками штатов и терминов
    t = t[:cut.start()] if cut else t
    low = t.lower()
    out: dict = {}
    m = re.search(r"carfax\s+(\d+)-owner", t, re.I) or re.search(r"\b(\d{1,2})\s+previous owners?\b", t, re.I)
    owners = [int(n) for n in re.findall(r"\bowner\s+(\d{1,2})\b", t, re.I)]
    out["owners"] = int(m.group(1)) if m else (max(owners) if owners else None)
    # ДТП: «No accidents or damage reported» — нет; иначе — число из сводки или события «Accident reported» / «Damage reported».
    if re.search(r"no accidents? or damage reported", low):
        out["accidents"] = 0
    else:
        m = re.search(r"(\d{1,2})\s+(?:accidents?|damage reports?|accident\s*/\s*damage reports?)\s+reported", t, re.I)
        # PDF Carfax: события пронумерованы «Event 1», «Event 2» — это и есть число ДТП / повреждений
        numbered = [int(n) for n in re.findall(r"\bevent\s+(\d{1,2})\b", t, re.I)]
        events = len(re.findall(r"^\s*(?:accident|damage) reported:", t, re.I | re.M))
        sev = len(re.findall(r"severity\s*[:\-]?\s*(?:minor|moderate|severe|functional|disabling)", t, re.I))
        out["accidents"] = (int(m.group(1)) if m else max(numbered) if numbered else
                            (max(events, sev) or (1 if re.search(r"\b(?:accident|damage) reported\b", low) else None)))
    out["severity"] = sorted({SEVERITY[s.lower()] for s in re.findall(r"severity\s*[:\-]?\s*(minor|moderate|severe|functional|disabling)", t, re.I)
                              + re.findall(r"(?:accident|damage) reported:\s*(minor|moderate|severe)", t, re.I)},
                             key=["лёгкое", "среднее", "сильное"].index)
    out["impact"] = sorted({IMPACT[s.lower()] for s in re.findall(
        r"(?:(?:point|area) of impact\s*[:\-]?\s*|damage to (?:the )?)(front|rear|left|right|side|undercarriage|roof|rollover)", t, re.I)})
    # Блоки «Additional History»: «No … reported» — нет; заголовок есть, а «No …» нет — есть.
    # В PDF без колонок статус («No Issues Reported») уезжает в таблицу ниже, а в блоке остаётся общая фраза —
    # тогда «есть» только по прямым словам об этом событии.
    generic = r"carfax recommends that you have this vehicle inspected by a collision repair specialist\.?"
    def flagged(head: str, clean: str, bad: str = r"(?!x)x") -> bool:
        part = _section(t, r"^\s*" + head + r"\s*$", r"^\s*(?:total loss|structural damage|airbag deployment|odometer check|accident / damage|manufacturer recall|basic warranty|title history)\s*$")
        if not part or re.search(clean, part, re.I):
            return False
        return bool(re.sub(generic, "", part, flags=re.I).strip()) or bool(re.search(bad, t, re.I))
    out["total_loss"] = flagged("total loss", r"no total loss|no issues")
    out["structural"] = flagged("structural damage", r"no structural damage|no issues", r"(?<!no )structural damage reported|frame damage|problem reported")
    out["airbags"] = flagged("airbag deployment", r"no airbag|no issues") or (bool(re.search(r"\bair ?bags? (?:were |was )?deployed", low))
                                                                    and not re.search(r"no air ?bag", low))
    out["rollback"] = flagged("odometer check", r"no indication|no issues")
    # Титул: «Damage Brands … No Problem», «Odometer Brands … No Problem»; иначе — что за титул, по событиям истории.
    title = _section(t, r"^\s*title history\s*$", r"^\s*ownership history\s*$")
    out["title"] = []
    found = sorted({w.lower() for w in re.findall(r"\b(salvage|junk|rebuilt|reconstructed|fire|flood|hail|lemon|not actual mileage|exceeds mechanical limits)\b[^\n|]{0,25}(?:title|brand|issued|reported)", t, re.I)})
    for group, nxt in (("damage brands", r"odometer brands"), ("odometer brands", r"guaranteed\s*-|none of these")):
        part = _section(title, group, nxt)
        # в тексте PDF без колонок «Guaranteed / No Problem» стоят до подписей групп — тогда смотрим на весь блок титула
        if part and not re.search(r"no problem", part, re.I) and (found or not re.search(r"no problem", title, re.I)):
            out["title"] += found or [("проблема с титулом" if group == "damage brands" else "пробег — бренд в титуле") + " (см. отчёт)"]
    if out["total_loss"]:
        out["title"].append("total loss")
    out["title"] = list(dict.fromkeys(out["title"]))
    # Штаты: «Owned in the following states/provinces», «Last Owned in …», «Owned in: …».
    states = []
    for part in re.findall(r"owned in the following states(?:/provinces)?\s*(.*?)\s*(?:estimated|last reported|$)", t, re.I | re.S):
        states += [s for s in STATES if re.search(r"\b" + s + r"\b", part)]
    states += re.findall(r"(?:last owned in|owned in:?)\s+(" + "|".join(STATES) + r")\b", t, re.I)
    out["states"] = list(dict.fromkeys(s.title() if s.islower() else s for s in states))
    out["salt"] = [s for s in out["states"] if s in SALT]
    out["usage"] = [name for name, rx in USAGE if re.search(rx + r"\s+(?:vehicle|use)\b", t, re.I)
                    or re.search(r"type of owner\s*" + rx, t, re.I)]
    m = re.search(r"(\d{1,3})\s+service (?:history )?records?", t, re.I)
    out["service"] = int(m.group(1)) if m else None
    miles = [int(x.replace(",", "")) for x in re.findall(r"(?:last reported odometer reading|odometer reading)\s*[:\-]?\s*(\d[\d,]{2,8})", t, re.I)]
    for run in re.findall(r"last reported odometer reading((?:\s+\d[\d,]{2,8}\b)+)", t, re.I):   # по владельцам: «94,438  152,023  160,563»
        miles += [int(x.replace(",", "")) for x in re.findall(r"\d[\d,]{2,8}", run)]
    miles += [int(x.replace(",", "")) for x in re.findall(r"(\d[\d,]{2,8}) mi\s*\|\s*vin", t, re.I)]   # шапка: «160,563 mi | VIN: …»
    out["last_miles"] = max(miles) if miles else None
    # Retail: «CARFAX Retail Value $12,180» или в PDF — сумма перед подписью («$12,180 … CARFAX Retail Value»)
    # или обе суммы выше, а подписи подряд («$13,750 … $6,960 … CARFAX Retail Value CARFAX Wholesale Value») — розница больше опта
    label = re.search(r"carfax retail value(?=\s*carfax wholesale value)", t, re.I)
    pair = [int(x.replace(",", "")) for x in re.findall(r"\$(\d[\d,]*)", t[max(0, label.start() - 300):label.start()])][-2:] if label else []
    m = re.search(r"\$([\d,]+)[^$]{0,120}?carfax retail value", t, re.I) or re.search(r"carfax (?:retail )?value\s*\$([\d,]+)", t, re.I)
    out["value"] = max(pair) if len(pair) == 2 else int(m.group(1).replace(",", "")) if m else None
    # Шапка: «2015 Honda Accord EX-L», «Sedan 4 DR | 2.4L I4 F DOHC 16V | Gasoline» — машина и двигатель (ремень ГРМ или цепь).
    m = re.search(r"\b(\d\.\d)\s*L\s+([IVHLW])-?(\d{1,2})\b", t, re.I)
    out["engine"] = f"{m.group(1)}L {m.group(2).upper().replace('L', 'I')}{m.group(3)}" if m else ("Electric" if re.search(r"\|\s*electric\b", t, re.I) else "")
    m = re.search(r"(?m)^[ \t]*((?:19|20)\d{2}\s+[A-Z][A-Za-z-]+(?:\s+[A-Za-z0-9-]+){1,4})[ \t]*$", t[:1500])
    out["car"] = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    return out


def events(text: str) -> list[dict]:
    """Записи истории по датам: сервис (что делали), ДТП, регистрация, продажа … → [{date, miles, kind, types}].
    Строка записи: «06/04/2016  5,269  Honda Marin  Vehicle serviced - Oil and filter changed - Tires rotated»."""
    t = re.sub(r"[\u00a0]", " ", text or "")
    cut = re.search(r"\bfull glossary\b", t, re.I)
    t = t[:cut.start()] if cut else t
    marks = list(_DATE_LINE.finditer(t))
    out, seen = [], set()
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(t)
        block = t[m.end():min(end, m.end() + 900)]
        stop = _SECTION.search(block)
        block = block[:stop.start()] if stop else block
        try:
            day = dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            continue
        mm = re.match(r"[ \t]*\n?[ \t]*(\d{1,3}(?:,\d{3})+|\d{1,6})(?![\d/.])", block)
        miles = int(mm.group(1).replace(",", "")) if mm else None
        low = block.lower()
        head = low[:160]
        if re.search(r"(?:accident|damage) reported", head):
            kind, types = "accident", []
        else:
            types = [k for k, rx in SERVICE_TYPES if re.search(rx, low)]
            if types == ["emissions"] and "serviced" not in low:      # смог-тест — не сервис
                kind, types = "emissions", []
            else:
                kind = None
            kind = kind or ("service" if types or "serviced" in low else "registration" if re.search(r"registration|registered", head)
                    else "title" if "title" in head else "sale" if re.search(r"listed|offered for sale|\bsold\b|auction", head)
                    else "odometer" if "odometer" in head else "other")
        key = (day, miles, kind, tuple(types))
        if key in seen:
            continue
        seen.add(key)
        out.append({"date": day.isoformat(), "miles": miles, "kind": kind, "types": types})
    return sorted(out, key=lambda e: e["date"])


def service_stats(evts: list[dict], today: dt.date | None = None) -> dict:
    """Регулярность ТО по записям сервиса: сколько, первая / последняя, раз в год, самый большой перерыв, масло."""
    today = today or dt.date.today()
    svc = [e for e in evts if e["kind"] == "service"]
    if not svc:
        return {"records": 0, "verdict": "none"}
    days = [dt.date.fromisoformat(e["date"]) for e in svc]
    gaps = [(b - a).days / 30.44 for a, b in zip(days, days[1:])]
    span = max((days[-1] - days[0]).days / 365.25, 0.5)
    oil = [e for e in svc if "oil" in e["types"] and e["miles"]]
    oil_miles = [b["miles"] - a["miles"] for a, b in zip(oil, oil[1:]) if b["miles"] > a["miles"]]
    since = (today - days[-1]).days / 30.44
    out = {"records": len(svc), "first": svc[0]["date"], "last": svc[-1]["date"], "last_miles": svc[-1]["miles"],
           "per_year": round(len(svc) / span, 1) if len(svc) > 1 else None, "max_gap_months": round(max(gaps)) if gaps else None,
           "months_since": round(since), "oil_changes": len([e for e in svc if "oil" in e["types"]]),
           "oil_every_miles": int(statistics.median(oil_miles)) if oil_miles else None}
    if len(svc) <= 2:
        out["verdict"] = "sparse"
    elif (gaps and max(gaps) > 24) or (out["per_year"] or 0) < 0.7:
        out["verdict"] = "gaps"
    else:
        out["verdict"] = "regular"
    return out


def last_done(evts: list[dict]) -> dict[str, list]:
    """Последний раз по каждому виду работ: {type: [дата, пробег]}."""
    done: dict[str, list] = {}
    for e in evts:
        if e["kind"] == "service":
            for k in e["types"]:
                done[k] = [e["date"], e["miles"]]
    return done


def load_maintenance(path: Path | None = None) -> dict:
    try:
        return json.loads((path or MAINT_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def has_belt(make: str, engine: str, year, cfg: dict) -> bool | None:
    """Ремень ГРМ у этого двигателя? None — двигатель не известен."""
    if not engine:
        return None
    if engine == "Electric":
        return False
    y = int(year) if str(year or "").isdigit() else 0
    for rule in cfg.get("belt_engines") or []:
        if not re.fullmatch(rule["make"], make or "", re.I) or not re.search(rule["engine"], engine, re.I):
            continue
        if (rule.get("year_from") and y and y < int(rule["year_from"])) or (rule.get("year_to") and y and y > int(rule["year_to"])):
            continue
        return True
    return False


def _months(a: dt.date, b: dt.date) -> float:
    return (b - a).days / 30.44


def plan(make: str, year, miles_now, engine: str, done: dict, cfg: dict | None = None, today: dt.date | None = None) -> list[dict]:
    """Календарь ТО этой машины: по каждой работе — когда делали (по Carfax), когда пора, статус.
    status: ok — рано; soon — скоро; due — пора, а записи нет; overdue — делали, но интервал прошёл; skip — не нужно."""
    cfg = load_maintenance() if cfg is None else cfg
    today = today or dt.date.today()
    if engine == "Electric":
        return []
    sched = next((s for s in cfg.get("schedules") or [] if s.get("make") == "*" or re.fullmatch(s["make"], make or "", re.I)), None)
    if not sched:
        return []
    y = int(year) if str(year or "").isdigit() else 0
    age = _months(dt.date(y - 1, 10, 1), today) if y else None      # модельный год начинается осенью предыдущего
    now = int(str(miles_now or "0").replace(",", "") or 0) or None
    belt = has_belt(make, engine, year, cfg)
    out = []
    for item in sched.get("items") or []:
        kind = item["type"]
        row = {"type": kind, "name": SERVICE_NAMES.get(kind, kind), "miles": item.get("miles"), "months": item.get("months"),
               "first_miles": item.get("first_miles"), "first_months": item.get("first_months"), "note": item.get("note", "")}
        if kind == "timing_belt" and not belt:
            row["status"] = "skip" if belt is False else "unknown"
            out.append(row)
            continue
        last = done.get(kind)
        if last:
            row["last"] = last
            since_m = (now - last[1]) if now and last[1] else None
            since_t = _months(dt.date.fromisoformat(last[0]), today)
            lim_m, lim_t = item.get("miles"), item.get("months")
            row["next_miles"] = last[1] + lim_m if lim_m and last[1] else None
            row["every_miles"], row["every_months"] = lim_m, lim_t
        else:
            since_m, since_t = now, age
            lim_m, lim_t = item.get("first_miles") or item.get("miles"), item.get("first_months") or item.get("months")
            row["next_miles"] = lim_m
            row["every_miles"], row["every_months"] = lim_m, lim_t
        over = (lim_m and since_m is not None and since_m >= lim_m) or (lim_t and since_t is not None and since_t >= lim_t)
        near = (lim_m and since_m is not None and since_m >= lim_m * 0.9) or (lim_t and since_t is not None and since_t >= lim_t - 6)
        row["status"] = ("overdue" if last else "due") if over else "soon" if near else "ok"
        out.append(row)
    return out


BIG = ("timing_belt", "spark_plugs", "transmission", "coolant")     # дорогие / важные работы — в пометку у машины


def plan_warnings(items: list[dict]) -> list[str]:
    """Коротко, что пора (по-английски, как итоги Carfax): «Timing belt due — no record (every 105k mi / 7 yrs)»."""
    out = []
    for r in sorted(items, key=lambda r: BIG.index(r["type"]) if r["type"] in BIG else 99):   # ремень ГРМ — первым
        if r["type"] not in BIG or r["status"] not in ("due", "overdue"):
            continue
        every = " / ".join(x for x in (f"{r['every_miles'] // 1000}k mi" if r.get("every_miles") else "",
                                       _yrs(r['every_months']) if r.get("every_months") else "") if x)
        if r["status"] == "due":
            out.append(f"{r['name']} due — no record" + (f" (every {every})" if every else ""))
        else:
            out.append(f"{r['name']} overdue — last {r['last'][0][5:7]}/{r['last'][0][:4]}" + (f" at {r['last'][1]:,} mi" if r['last'][1] else ""))
    return out


def stats_line(st: dict) -> str:
    """«Service: 19 records, regular · last 03/2026 at 74,245 mi · oil every ~6,800 mi»."""
    if not st or not st.get("records"):
        return "Service: no records in Carfax"
    word = {"regular": "regular", "gaps": "with gaps", "sparse": "few records"}.get(st.get("verdict"), "")
    last = st.get("last", "")
    parts = [f"Service: {st['records']} record{'s' if st['records'] != 1 else ''}" + (f", {word}" if word else "")]
    if last:
        parts.append(f"last {last[5:7]}/{last[:4]}" + (f" at {st['last_miles']:,} mi" if st.get("last_miles") else ""))
    if st.get("max_gap_months") and st["max_gap_months"] > 18:
        parts.append(f"longest gap {st['max_gap_months']} mo")
    if st.get("oil_every_miles"):
        parts.append(f"oil every ~{st['oil_every_miles']:,} mi")
    return " · ".join(parts)


def _en(words) -> list[str]:
    return [EN.get(w, w) for w in words or []]


def _yrs(months) -> str:
    n = int(months or 0) // 12
    return f"{n} yr" + ("s" if n != 1 else "")


def line(s: dict, service: bool = True) -> str:
    """Одна строка для окна / списка / телефона — по-английски: «1 owner · 1 moderate accident (front) · 19 service records».
    service=False — без числа записей сервиса (оно — в строке «Service: …» нашего разбора)."""
    parts = []
    if s.get("owners"):
        parts.append(f"{s['owners']} owner" + ("s" if s["owners"] != 1 else ""))
    if s.get("accidents") == 0:
        parts.append("No accidents")
    elif s.get("accidents"):
        n, sev, where = s["accidents"], _en(s.get("severity")), ", ".join(_en(s.get("impact")))
        if n == 1:
            text = f"1 {sev[0]} accident" if len(sev) == 1 else "1 accident" + (f" ({', '.join(sev)})" if sev else "")
        else:
            text = f"{n} accidents" + (f" ({', '.join(sev)})" if sev else "")
        parts.append(text + (f", {where}" if where and "(" in text else f" ({where})" if where else ""))
    if s.get("airbags"):
        parts.append("airbags deployed")
    if s.get("structural"):
        parts.append("structural damage")
    if s.get("total_loss") and "total loss" not in (s.get("title") or []):
        parts.append("total loss")
    if s.get("states"):
        parts.append("owned in " + ", ".join(s["states"][:4]))
    if s.get("usage") and s["usage"] != ["личная"]:
        parts.append(", ".join(u for u in _en(s["usage"]) if u != "personal"))
    if s.get("title"):
        parts.append("title: " + ", ".join(_en(s["title"])))
    if s.get("rollback"):
        parts.append("odometer issue")
    # Цену Carfax (Retail / Wholesale) не показываем: на неё не ориентируемся — только сбивает (разбор её помнит в summary).
    if service and s.get("service") is not None:
        parts.append(f"{s['service']} service record" + ("s" if s["service"] != 1 else ""))
    return " · ".join(parts)


def flags(s: dict) -> list[str]:
    """Что настораживает (по-английски, как итоги): идёт в пометку у машины."""
    out = []
    if s.get("title"):
        out.append("title: " + ", ".join(_en(s["title"])))
    if "сильное" in (s.get("severity") or []) or s.get("airbags") or s.get("structural") or s.get("total_loss"):
        out.append("severe accident")
    if s.get("salt"):
        out.append("salt-belt states: " + ", ".join(s["salt"]))
    if s.get("rollback"):
        out.append("odometer")
    if s.get("owners") and s["owners"] >= 4:
        out.append(f"{s['owners']} owners")
    if set(s.get("usage") or []) & {"аренда", "такси", "полиция"}:
        out.append(", ".join(_en(sorted(set(s["usage"]) & {"аренда", "такси", "полиция"}))))
    return out


# Куда смотреть на осмотре по месту удара из Carfax.
LOOK = {
    "спереди": "бампер, капот, фары, решётка, радиатор, кондиционер, лонжероны, зазоры капота и крыльев",
    "сзади": "бампер, багажник, фонари, пол багажника (сварка), задние лонжероны",
    "слева": "левые двери, крылья, порог, стойки, колёса и подвеска",
    "справа": "правые двери, крылья, порог, стойки, колёса и подвеска",
    "сбоку": "двери, крылья, пороги, стойки, колёса и подвеска с той стороны",
    "днище": "днище, рычаги, подвеска, выхлоп, подтёки",
    "крыша": "крыша, стойки, лобовое стекло, потолок",
    "опрокидывание": "крыша, все стойки, стёкла, потолок, геометрия кузова",
}


def inspect_points(s: dict) -> list[str]:
    """Повреждения по Carfax — коротко «что и куда смотреть» для списка на осмотр, телефона и окна."""
    out = []
    sev = ", ".join(s.get("severity") or [])
    if s.get("accidents"):
        areas = [a for a in (s.get("impact") or []) if a in LOOK]
        out += [f"Удар {a}{f' ({sev})' if sev else ''}: {LOOK[a]}; перекрас, шпаклёвка" for a in areas]
        if not areas:
            out.append(f"ДТП: {s['accidents']}{f' ({sev})' if sev else ''}, место удара не указано: весь кузов — зазоры, перекрас, сварка")
        if "сильное" in (s.get("severity") or []):
            out.append("Сильный удар: геометрия — руль ровно, не уводит, шины изношены ровно")
    if s.get("airbags"):
        out.append("Подушки сработали: лампа SRS, руль, торпедо, ремни")
    if s.get("structural"):
        out.append("Рама / кузов: лонжероны, следы стапеля и сварки, зазоры дверей и капота")
    if s.get("title"):
        title = ", ".join(s["title"])
        out.append(f"Титул: {title}" + ("; салон — запах сырости, ржавчина под сиденьями, электрика" if "flood" in title.lower() else ""))
    if s.get("salt"):
        out.append(f"Соль на дорогах ({', '.join(s['salt'][:3])}): ржавчина — днище, рама, тормозные трубки")
    if s.get("rollback"):
        out.append("Пробег под вопросом: износ педалей, руля, сиденья")
    return out


def history_text(s: dict) -> str:
    """Находки Carfax словами, которые понимает расчёт (bid.assess_history): только то, что есть, — без «No …»."""
    parts = []
    if s.get("owners"):
        parts.append(f"{s['owners']} owners")
    if s.get("accidents"):
        sev = s.get("severity") or []
        word = "severe" if "сильное" in sev else "moderate" if "среднее" in sev else "minor" if "лёгкое" in sev else ""
        parts.append(f"{s['accidents']} accidents" + (f" {word}" if word else ""))
    if s.get("structural"):
        parts.append("structural damage")
    if s.get("airbags"):
        parts.append("airbags deployed")
    if s.get("total_loss"):
        parts.append("total loss")
    for t in s.get("title") or []:
        if t == "total loss":
            continue
        parts.append("odometer problem" if t.startswith("пробег") or t in ("not actual mileage", "exceeds mechanical limits")
                     else ("salvage/rebuilt brand" if t.startswith("проблема") else f"{t} title"))
    if s.get("rollback"):
        parts.append("odometer rollback")
    parts += [{"аренда": "rental", "флит": "fleet", "такси": "taxi", "полиция": "police"}[u] for u in s.get("usage") or []
              if u in ("аренда", "флит", "такси", "полиция")]
    return "Carfax: " + "; ".join(parts) if parts else ""


def save(vin: str, url: str, text: str, source: str = "text", pdf: str = "") -> dict | None:
    vin = str(vin or "").strip().upper()
    if not VIN_RE.fullmatch(vin) or len(text or "") < 500:
        return None
    data = load()
    old = data.get(vin) or {}
    # Отчёт из PDF (кнопка «📄 Carfax») точнее текста страницы: текст того же отчёта, пришедший следом, его не затирает.
    if old.get("source") == "pdf" and source != "pdf" and time.time() - float(old.get("at") or 0) < 7 * 86400:
        return old
    summary = parse(text)
    evts = events(text)
    data[vin] = {"url": str(url or "")[:1000], "at": time.time(), "summary": summary, "line": line(summary), "flags": flags(summary),
                 "source": source, "pdf": pdf or (old.get("pdf", "") if source != "pdf" else ""),
                 "stats": service_stats(evts), "done": last_done(evts)}
    TEXT_DIR.mkdir(parents=True, exist_ok=True)             # полный отчёт — в программе: смотреть без сайта Carfax
    (TEXT_DIR / f"{vin}.txt.gz").write_bytes(gzip.compress(text.encode("utf-8")))
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
    return data[vin]


def saved_text(vin: str) -> str:
    """Сохранённый полный текст отчёта ('' — нет: отчёт пришёл до версии 2026-11-21)."""
    path = TEXT_DIR / f"{str(vin).upper()}.txt.gz"
    try:
        return gzip.decompress(path.read_bytes()).decode("utf-8", errors="replace") if path.is_file() else ""
    except (OSError, EOFError):
        return ""


def pdf_text(data: bytes) -> str:
    """Текст PDF-отчёта (pypdf — из requirements.txt)."""
    import io
    try:
        from pypdf import PdfReader
    except ImportError as err:
        raise RuntimeError("для PDF нужен pypdf: python3 -m pip install pypdf") from err
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def save_pdf(vin: str, url: str, data: bytes) -> dict | None:
    """PDF Carfax (кнопка «📄 Carfax»): файл — в data/carfax/<VIN>.pdf, разбор — как у текста отчёта."""
    if not data.startswith(b"%PDF") or len(data) < 2000:
        return None
    text = pdf_text(data)
    vin = str(vin or "").strip().upper()
    if not VIN_RE.fullmatch(vin):                      # VIN из самого отчёта: чаще всего встречающийся
        found = [v for v in VIN_RE.findall(text.upper()) if re.search(r"\d", v) and re.search(r"[A-Z]", v)]
        vin = max(set(found), key=found.count) if found else ""
    if not VIN_RE.fullmatch(vin) or len(text) < 500:
        return None
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    path = PDF_DIR / f"{vin}.pdf"
    path.write_bytes(data)
    return save(vin, url, text, source="pdf", pdf=str(path))


def attach(rows: list[dict], engine_of=None) -> None:
    """Carfax — к машинам: итоги (по-английски), что настораживает, что смотреть на осмотре, сервис и календарь ТО.
    engine_of(row) — двигатель по VIN (NHTSA), если в отчёте его нет."""
    data = load()
    cfg = load_maintenance()
    saved = {p.name[:17] for p in TEXT_DIR.glob("*.txt.gz")} if TEXT_DIR.is_dir() else set()
    for row in rows:
        vin = str(row.get("vin", "")).upper()
        rec = data.get(vin)
        if rec:
            s = rec.get("summary") or {}
            row["carfax"] = (line(s, service=not rec.get("stats")) if s else rec.get("line")) or "report saved"   # строка — заново: без цены и у старых
            row["carfax_url"] = rec.get("url", "")
            row["carfax_flags"] = "; ".join(flags(s) if s else rec.get("flags") or [])
            row["carfax_pdf"] = f"/carfax/{vin}.pdf" if rec.get("pdf") and Path(rec["pdf"]).exists() else ""
            row["carfax_saved"] = "1" if vin in saved else ""          # полный отчёт — в программе (/carfax/<VIN>)
            points = inspect_points(s)
            if rec.get("stats"):
                engine = s.get("engine") or (engine_of(row) if engine_of else "")
                items = plan(row.get("make", ""), row.get("year", ""), row.get("odometer_miles") or s.get("last_miles"), engine, rec.get("done") or {}, cfg)
                warn = plan_warnings(items)
                if (rec["stats"].get("records") or 0) < 3:                # записей мало — «нет записи» ничего не значит; кроме ремня ГРМ
                    warn = [w for w in warn if w.startswith(SERVICE_NAMES["timing_belt"])]
                row["carfax_service"] = " · ".join([stats_line(rec["stats"])] + warn)
                row["carfax_service_warn"] = "1" if warn else ""
                belt = next((r for r in items if r["type"] == "timing_belt" and r["status"] in ("due", "overdue")), None)
                if belt:
                    points.append("Ремень ГРМ: по регламенту пора, " + ("записи о замене в Carfax нет" if belt["status"] == "due" else
                                  f"последняя замена {belt['last'][0][:7]}") + " — спросить чек / наклейку под капотом; заложить замену")
                    if cfg.get("belt_to_recon", True) and "timing belt due" not in row.get("carfax_autocheck", ""):   # в ремонт: recon_keywords
                        row["carfax_autocheck"] = " | ".join(x for x in (row.get("carfax_autocheck", ""), "timing belt due") if x)
            row["carfax_check"] = "\n".join(points)    # что смотреть на осмотре
            found = history_text(s)
            if found and found not in row.get("carfax_autocheck", ""):          # в расчёт: скидки и стоп-факторы истории
                row["carfax_autocheck"] = " | ".join(x for x in (row.get("carfax_autocheck", ""), found) if x)


STATUS_TEXT = {"ok": ("ok", "rok"), "soon": ("soon", "rsoon"), "due": ("due — no record", "rbad"), "overdue": ("overdue", "rbad"),
               "skip": ("timing chain — no belt", "rmuted"), "unknown": ("engine unknown — check belt / chain", "rsoon")}


def page(vin: str, miles_now: str = "", car: str = "", engine: str = "") -> str:
    """Сохранённый отчёт Carfax в программе: итоги, сервис и календарь ТО, что смотреть, полный текст. '' — отчёта нет."""
    vin = str(vin or "").upper()
    rec = load().get(vin)
    text = saved_text(vin)
    if not rec and not text:
        return ""
    s = parse(text) if text else (rec or {}).get("summary") or {}
    evts = events(text) if text else []
    st = service_stats(evts) if text else (rec or {}).get("stats") or {}
    done = last_done(evts) if text else (rec or {}).get("done") or {}
    car = car or s.get("car", "")
    make = (car.split() + ["", ""])[1] if car else ""
    year = (car.split() + [""])[0] if car else ""
    eng = s.get("engine") or engine
    miles = miles_now or s.get("last_miles") or ""
    items = plan(make, year, miles, eng, done) if (text or rec.get("stats")) else []
    esc = html.escape
    saved_at = time.strftime("%m/%d/%Y", time.localtime(float((rec or {}).get("at") or 0))) if rec else ""
    flags_ = flags(s)
    points = inspect_points(s)
    rows = []
    for r in items:
        word, cls = STATUS_TEXT.get(r["status"], (r["status"], ""))
        every = " / ".join(x for x in (f"{r['every_miles']:,} mi" if r.get("every_miles") else (f"{r['miles']:,} mi" if r.get("miles") else ""),
                                       _yrs(r.get('every_months') or r.get('months')) if (r.get("every_months") or r.get("months")) else "") if x)
        first = f" (first {r['first_miles']:,} mi)" if r.get("first_miles") and r.get("last") else ""
        last = f"{r['last'][0][5:7]}/{r['last'][0][:4]}" + (f" · {r['last'][1]:,} mi" if r['last'][1] else "") if r.get("last") else "—"
        rows.append(f"<tr><td>{esc(r['name'])}</td><td>{esc(every + first)}</td><td>{esc(last)}</td>"
                    f"<td class='{cls}'>{esc(word)}</td><td class='note'>{esc(r.get('note') or '')}</td></tr>")
    svc = [e for e in evts if e["kind"] == "service"]
    hist = "".join(f"<tr><td>{e['date'][5:7]}/{e['date'][8:10]}/{e['date'][:4]}</td><td class='num'>{(format(e['miles'], ',') if e['miles'] else '—')}</td>"
                   f"<td>{esc(', '.join(SERVICE_NAMES.get(k, k) for k in e['types']) or 'Vehicle serviced')}</td></tr>" for e in reversed(svc))
    belt = has_belt(make, eng, year, load_maintenance())
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Carfax {esc(vin)}</title><style>
body{{font:15px/1.45 -apple-system,Segoe UI,Arial,sans-serif;margin:0;padding:16px;background:#f6f7f9;color:#1d2330}}
.wrap{{max-width:980px;margin:0 auto}}h1{{font-size:21px;margin:0 0 2px}}h2{{font-size:16px;margin:18px 0 6px}}
.card{{background:#fff;border:1px solid #dde1e8;border-radius:10px;padding:12px 14px;margin:10px 0}}.muted{{color:#667085;font-size:13px}}
.sum{{font-size:17px;font-weight:600}}.flag{{color:#b42318;font-weight:600}}table{{border-collapse:collapse;width:100%}}
td,th{{border-bottom:1px solid #eceef2;padding:5px 6px;text-align:left;vertical-align:top}}th{{font-size:13px;color:#667085;font-weight:600}}
td.num{{text-align:right;white-space:nowrap}}td.note{{font-size:13px;color:#667085}}.rok{{color:#067647}}.rsoon{{color:#b54708}}.rbad{{color:#b42318;font-weight:600}}
.rmuted{{color:#98a2b3}}pre{{white-space:pre-wrap;word-break:break-word;font:13px/1.4 Menlo,Consolas,monospace;margin:0}}
details summary{{cursor:pointer;font-weight:600}}a{{color:#2f5597}}.tbl{{overflow-x:auto}}
</style></head><body><div class="wrap">
<h1>CARFAX — {esc(car or vin)}</h1>
<div class="muted">VIN {esc(vin)}{' · ' + esc(eng) if eng else ''}{' · отчёт сохранён ' + esc(saved_at) if saved_at else ''}
{(' · <a href="' + esc(rec.get('url', '')) + '" target="_blank" rel="noopener">открыть на сайте Carfax ↗</a>') if rec and rec.get('url') else ''}</div>
<div class="card"><div class="sum">{esc(line(s, service=not evts))}</div>
{''.join(f'<div class="flag">⚠ {esc(f)}</div>' for f in flags_)}
<div style="margin-top:6px">{esc(stats_line(st))}</div>
{f'<div class="muted">Ремень ГРМ: {"да — менять по регламенту" if belt else "нет, цепь" if belt is False else "двигатель не известен"}</div>' if make else ''}</div>
{('<div class="card"><h2>Maintenance — по регламенту для ' + esc(make or 'этой марки') + (f' при {int(str(miles).replace(",", "")):,} mi' if str(miles).replace(",", "").isdigit() else '') + '</h2><div class="tbl"><table><tr><th>Работа</th><th>Интервал</th><th>Последний раз (Carfax)</th><th>Статус</th><th></th></tr>' + ''.join(rows) + '</table></div><div class="muted">Нет записи — не значит, что не делали: у независимых мастерских и при замене самим записей в Carfax нет. Спросите чеки / наклейку под капотом.</div></div>') if rows else ''}
{('<div class="card"><h2>Что смотреть на осмотре</h2>' + ''.join(f'<div>• {esc(p)}</div>' for p in points) + '</div>') if points else ''}
{('<div class="card"><h2>Service history — ' + str(len(svc)) + '</h2><div class="tbl"><table><tr><th>Дата</th><th>Пробег</th><th>Что делали</th></tr>' + hist + '</table></div></div>') if svc else ''}
{('<div class="card"><details><summary>Полный текст отчёта</summary><pre>' + esc(text) + '</pre></details></div>') if text else '<div class="card muted">Полного текста нет: отчёт пришёл до версии 2026-11-21 — откройте его на сайте, и он сохранится здесь.</div>'}
</div></body></html>"""
