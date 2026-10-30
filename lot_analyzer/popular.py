"""Популярные модели — идут в окне первыми (настройка popular_models в config/costs.json).

Правило: марка, регулярное выражение для «модель + комплектация» (без учёта регистра), необязательно
годы списком (years), «с такого-то года» (year_from) и «по такой-то год» (year_to).
"""

from __future__ import annotations

import re

DEFAULT = [
    {"make": "Toyota", "model": r"^corolla(?!\s*cross)"},
    {"make": "Honda", "model": r"^civic(?!.*(type\s*r|hybrid))"},
    {"make": "Honda", "model": r"^civic\b.*\bhybrid"},
    {"make": "Toyota", "model": r"^camry"},
    {"make": "Honda", "model": r"^accord"},
    {"make": "Mazda", "model": r"^(mazda\s*)?3\b", "year_from": 2012},
    {"make": "Honda", "model": r"^cr-?v"},
    {"make": "Toyota", "model": r"^rav\s*4"},
    {"make": "Mazda", "model": r"^cx-?5\b"},
    {"make": "Mazda", "model": r"^(mazda\s*)?6\b"},
    {"make": "Toyota", "model": r"^prius(?!\s*[cv]\b)"},
    {"make": "Toyota", "model": r"^highlander"},
    {"make": "Toyota", "model": r"^avalon\b"},
    {"make": "Honda", "model": r"^clarity\b"},
    {"make": "Honda", "model": r"^insight\b"},
    {"make": "Chevrolet", "model": r"^volt\b"},
    {"make": "Lexus", "model": r"."},                     # все Lexus
    # Acura — техника Honda; без V6-АКПП 2000-х (TL 2004–2010, MDX 2003–2006) и V6 / SH-AWD у TLX
    {"make": "Acura", "model": r"^(tsx|ilx|rsx)\b"},
    {"make": "Acura", "model": r"^tlx\b(?!.*\b(v6|sh-?awd|type\s*s)\b)"},
    {"make": "Acura", "model": r"^rdx\b", "year_to": 2018},
    {"make": "Acura", "model": r"^mdx\b", "year_from": 2007, "year_to": 2020},
    {"make": "Scion", "model": r"."},                     # все Scion — это Toyota
    {"make": "Tesla", "model": r"^model\s*3\b(?!.*(long range|performance|awd|dual motor))", "years": [2022]},
]


def rules(costs: dict) -> list[dict]:
    return costs.get("popular_models") or DEFAULT


def is_popular(row: dict, costs: dict) -> bool:
    make = str(row.get("make", "")).strip().lower()
    text = f"{row.get('model', '')} {row.get('trim', '')}".strip()
    year = str(row.get("year", "")).strip()
    for rule in rules(costs):
        if make != rule["make"].lower():
            continue
        if rule.get("years") and year not in {str(y) for y in rule["years"]}:
            continue
        if rule.get("year_from") and not (year.isdigit() and int(year) >= int(rule["year_from"])):
            continue
        if rule.get("year_to") and not (year.isdigit() and int(year) <= int(rule["year_to"])):
            continue
        if re.search(rule["model"], text, re.I):
            return True
    return False


# Пикапы: своя вкладка в окне (свой покупатель и свой рынок).
PICKUP = re.compile(r"^(f-?\s?(150|250|350|450)|silverado|sierra|tacoma|tundra|colorado|canyon|frontier|titan|ranger|ridgeline|"
                    r"gladiator|maverick|santa cruz|cybertruck|r1t|avalanche|ram\s?(1500|2500|3500)|1500|2500|3500|"
                    r"dakota|tacoma|hilux|f-150 lightning|sierra ev|silverado ev)\b", re.I)


def is_pickup(row: dict) -> bool:
    model = str(row.get("model", "")).strip()
    make = str(row.get("make", "")).strip().lower()
    if re.match(r"^(1500|2500|3500)\b", model) and make not in ("ram", "dodge", "chevrolet", "gmc"):
        return False
    return bool(PICKUP.search(model) or re.search(r"\b(pickup|crew ?cab|crewmax|supercrew|double cab|quad cab|regular cab|access cab|king cab)\b",
                                                  f"{model} {row.get('trim', '')}", re.I))


# Для своей статистики «за сколько продаются»: популярные модели и то, что вы отслеживаете отдельно
# (настройка stats_models в config/costs.json; по умолчанию — Tesla Model 3 2022–2023, все комплектации).
STATS_DEFAULT = [{"make": "Tesla", "model": r"^model\s*3\b", "years": [2022, 2023]}]


def is_stats_target(row: dict, costs: dict) -> bool:
    if is_popular(row, costs):
        return True
    extra = {**costs, "popular_models": costs.get("stats_models") or STATS_DEFAULT}
    return is_popular(row, extra)
