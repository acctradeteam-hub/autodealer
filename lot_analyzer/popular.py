"""Популярные модели — идут в окне первыми (настройка popular_models в config/costs.json).

Правило: марка, регулярное выражение для «модель + комплектация» (без учёта регистра), необязательно год.
"""

from __future__ import annotations

import re

DEFAULT = [
    {"make": "Toyota", "model": r"^corolla(?!\s*cross)"},
    {"make": "Honda", "model": r"^civic(?!.*type\s*r)"},
    {"make": "Toyota", "model": r"^camry"},
    {"make": "Honda", "model": r"^accord"},
    {"make": "Mazda", "model": r"^(mazda\s*)?3\b"},
    {"make": "Honda", "model": r"^cr-?v"},
    {"make": "Toyota", "model": r"^rav\s*4"},
    {"make": "Mazda", "model": r"^cx-?5\b"},
    {"make": "Toyota", "model": r"^prius(?!\s*[cv]\b)"},
    {"make": "Lexus", "model": r"^ct\s*\d*h?\b"},
    {"make": "Lexus", "model": r"^rx\s*\d*h?\b"},
    {"make": "Lexus", "model": r"^is\s*\d*\b"},
    {"make": "Lexus", "model": r"^es\s*\d*h?\b"},
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
        if re.search(rule["model"], text, re.I):
            return True
    return False
