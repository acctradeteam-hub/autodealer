"""Текст для заметки (Notes) на карточке лота на сайте аукциона.

Пишется только машинам с настоящим KBB Private Party. Расширение «Lot Analyzer KBB» берёт
эти тексты у окна программы (/api/notes) и вписывает их в Notes на carmaxauctions.com.

Формат: строка «KBB 14,950$» (как пишет навык kbb-pp-92620 — окно читает её обратно)
и строки расчёта с меткой «LA » — при пересчёте они заменяются, ваш текст заметки остаётся.
Строки «LA » окно при чтении заметки пропускает, так что числа из них не принимаются за ваши.
"""

from __future__ import annotations

import json
import re

PREFIX = "LA "


def _usd(value) -> str:
    value = float(value)
    return ("−" if value < 0 else "") + f"${abs(value):,.0f}"


def _short(label: str) -> str:
    """«Продажа: KBB PP $14,950 − $500» → «Продажа»; «Сборы аукциона CarMax при цене $7,800» → «Сборы аукциона CarMax»."""
    label = re.split(r":| \(| при цене", label, maxsplit=1)[0]
    return label.strip()


def lot_note(row: dict) -> str:
    """Блок для Notes: KBB, вердикт и статьи прибыли. Пусто, если настоящего KBB нет."""
    kbb = str(row.get("kbb_private_party_usd") or "").replace(",", "")
    if not kbb.replace(".", "", 1).isdigit() or float(kbb) <= 0:
        return ""
    lines = [f"KBB {float(kbb):,.0f}$"]
    head = [str(row.get("calc_verdict") or "").split(";")[0].strip()[:90]]
    if row.get("market_estimate_usd"):
        head.append(f"рынок ≈{_usd(row['market_estimate_usd'])}")
    if row.get("calc_profit_market_usd") not in (None, ""):
        head.append(f"прибыль по рынку {_usd(row['calc_profit_market_usd'])}")
    lines.append(PREFIX + " · ".join(x for x in head if x))
    try:
        items = json.loads(row.get("calc_profit_items") or "[]")
    except ValueError:
        items = []
    if items:
        parts = [f"{_short(label)} {'+' if amount > 0 else '−'}{_usd(abs(amount))}" for label, amount in items]
        lines.append(PREFIX + "; ".join(parts))
    return "\n".join(lines)


def strip_own(text: str) -> str:
    """Заметка без строк расчёта программы — то, что писали вы."""
    return "\n".join(line for line in str(text or "").splitlines() if not line.startswith(PREFIX)).strip()


def notes_for(rows: list[dict]) -> dict[str, str]:
    """VIN → текст заметки, для машин с настоящим KBB."""
    out = {}
    for row in rows:
        vin = row.get("vin", "")
        note = lot_note(row)
        if vin and note and vin not in out:
            out[vin] = note
    return out
