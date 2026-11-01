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


def parse(text: str) -> dict:
    """Главное из текста отчёта Carfax."""
    t = re.sub(r"[ \t ]+", " ", text or "")
    low = t.lower()
    out: dict = {}
    m = re.search(r"carfax\s+(\d+)-owner", t, re.I) or re.search(r"\b(\d{1,2})\s+previous owners?\b", t, re.I) \
        or re.search(r"owners?\s*[:\-]?\s*(\d{1,2})\b", t, re.I)
    owners = [int(n) for n in re.findall(r"\bowner\s+(\d{1,2})\b", t, re.I)]
    out["owners"] = int(m.group(1)) if m else (max(owners) if owners else None)
    if re.search(r"no accidents? or damage reported|no accident or damage", low):
        out["accidents"] = 0
    else:
        m = re.search(r"(\d{1,2})\s+(?:accidents?|damage reports?|accident\s*/\s*damage reports?)\s+reported", t, re.I)
        events = _count(r"(?:damage\s+)?severity\s*[:\-]?\s*(?:minor|moderate|severe|functional|disabling)", t)
        out["accidents"] = int(m.group(1)) if m else (events or (1 if re.search(r"\b(?:accident|damage) reported\b", low) else None))
    out["severity"] = sorted({SEVERITY[s.lower()] for s in re.findall(r"(?:damage\s+)?severity\s*[:\-]?\s*(minor|moderate|severe|functional|disabling)", t, re.I)},
                             key=["лёгкое", "среднее", "сильное"].index)
    out["impact"] = sorted({IMPACT[s.lower()] for s in re.findall(r"(?:point|area) of impact\s*[:\-]?\s*(front|rear|left|right|side|undercarriage|roof|rollover)", t, re.I)})
    out["airbags"] = bool(re.search(r"air ?bags? (?:were |was )?deployed", low)) and not re.search(r"no air ?bags? deploy", low)
    out["structural"] = bool(re.search(r"structural damage|frame damage", low)) and not re.search(r"no structural damage|no frame damage", low)
    states = []
    for found in re.findall(r"(?:owned in|registered in|last owned in|location)\s*[:\-]?\s*(" + "|".join(STATES) + r")\b", t):
        if found not in states:
            states.append(found)
    if not states:
        states = [s for s in STATES if re.search(r"\b" + s + r"\b", t)][:6]
    out["states"] = states
    out["salt"] = [s for s in states if s in SALT]
    out["flood_state"] = bool(re.search(r"hurricane|flood", low)) and not re.search(r"no (?:flood|hurricane)", low)
    clean = re.sub(r"\bno (?:indication of (?:an? )?)?[a-z ]{0,40}?(?:problem|rollback|issues?|brand)s?\b[^.\n]*", " ", low)   # «No indication of an odometer rollback»
    out["title"] = sorted({m.lower() for m in TITLE_BAD.findall(clean)} - {"flood"} | ({"flood"} if re.search(r"flood (?:title|damage)", clean) else set()))
    out["usage"] = [name for name, rx in USAGE if re.search(rx + r"\s+(?:vehicle|use)", t, re.I)]
    m = re.search(r"(\d{1,3})\s+service (?:history )?records?", t, re.I)
    out["service"] = int(m.group(1)) if m else None
    miles = [int(x.replace(",", "")) for x in re.findall(r"(?:last reported odometer|odometer reading)\s*[:\-]?\s*([\d,]{3,9})", t, re.I)]
    out["last_miles"] = max(miles) if miles else None
    out["rollback"] = bool(re.search(r"odometer (?:rollback|problem|inconsistenc)|not actual mileage", clean))
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
    if s.get("service") is not None:
        parts.append(f"сервис {s['service']}")
    return " · ".join(parts)


def flags(s: dict) -> list[str]:
    """Что настораживает: идёт в вердикт / пометку."""
    out = []
    if s.get("title"):
        out.append("титул: " + ", ".join(s["title"]))
    if "сильное" in (s.get("severity") or []) or s.get("airbags") or s.get("structural"):
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
