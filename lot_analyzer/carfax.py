"""Carfax: отчёт, открытый на vin-reports-pro.com (или carfax.com), — коротко к машине в окне, в списке на осмотр и на телефоне.

Расширение присылает текст отчёта и его адрес (POST /api/carfax); здесь — разбор по обычным фразам Carfax
(«CARFAX 1-Owner», «Accident reported», «Damage Severity: Moderate», «Point of Impact: Front», «Owned in: Ohio»,
«Personal vehicle», «Salvage title» …). data/carfax.json: VIN → {url, at, summary}.
"""

from __future__ import annotations

import json
import re
import time

from .paths import DATA_DIR

PATH = DATA_DIR / "carfax.json"

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
    return out


def line(s: dict) -> str:
    """Одна строка для окна / списка / телефона."""
    parts = []
    if s.get("owners"):
        parts.append(f"{s['owners']} влад.")
    if s.get("accidents") == 0:
        parts.append("без ДТП")
    elif s.get("accidents"):
        hit = ", ".join(x for x in (", ".join(s.get("severity") or []), ", ".join(s.get("impact") or [])) if x)
        parts.append(f"{s['accidents']} ДТП" + (f" ({hit})" if hit else ""))
    if s.get("airbags"):
        parts.append("подушки сработали")
    if s.get("structural"):
        parts.append("повреждение рамы")
    if s.get("states"):
        parts.append("штаты: " + ", ".join(s["states"][:4]))
    if s.get("usage") and s["usage"] != ["личная"]:
        parts.append(", ".join(s["usage"]))
    if s.get("title"):
        parts.append("титул: " + ", ".join(s["title"]))
    if s.get("rollback"):
        parts.append("пробег под вопросом")
    if s.get("value"):
        parts.append(f"Carfax ${s['value']:,}")
    if s.get("service") is not None:
        parts.append(f"сервис {s['service']}")
    return " · ".join(parts)


def flags(s: dict) -> list[str]:
    """Что настораживает: идёт в вердикт / пометку."""
    out = []
    if s.get("title"):
        out.append("титул: " + ", ".join(s["title"]))
    if "сильное" in (s.get("severity") or []) or s.get("airbags") or s.get("structural") or s.get("total_loss"):
        out.append("сильное ДТП")
    if s.get("salt"):
        out.append("соль на дорогах: " + ", ".join(s["salt"]))
    if s.get("rollback"):
        out.append("пробег")
    if s.get("owners") and s["owners"] >= 4:
        out.append(f"{s['owners']} владельцев")
    if set(s.get("usage") or []) & {"аренда", "такси", "полиция"}:
        out.append(", ".join(sorted(set(s["usage"]) & {"аренда", "такси", "полиция"})))
    return out


def save(vin: str, url: str, text: str) -> dict | None:
    vin = str(vin or "").strip().upper()
    if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin) or len(text or "") < 500:
        return None
    summary = parse(text)
    data = load()
    data[vin] = {"url": str(url or "")[:1000], "at": time.time(), "summary": summary, "line": line(summary), "flags": flags(summary)}
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
    return data[vin]


def attach(rows: list[dict]) -> None:
    data = load()
    for row in rows:
        rec = data.get(str(row.get("vin", "")).upper())
        if rec:
            row["carfax"] = rec.get("line") or "отчёт прочитан"
            row["carfax_url"] = rec.get("url", "")
            row["carfax_flags"] = "; ".join(rec.get("flags") or [])
