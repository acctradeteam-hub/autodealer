"""Текст для заметки (Notes) на карточке лота на сайте аукциона.

Пишется только машинам с настоящим KBB Private Party. Расширение «Lot Analyzer KBB» берёт
эти тексты у окна программы (/api/notes) и вписывает их в Notes на carmaxauctions.com.

Формат:
    KBB 19,530$ 10/2/26                            ← KBB Private Party и дата, на которую он посчитан
    MP 15000                                       ← ваша ставка, вписанная в окне («Наша ставка»)
    Был: 9/28/26 CarMax Murrieta — продана $1,600  ← прошлые торги этой машины (по VIN, из вашей базы итогов)
    CarMax: Major transmission defect, Prior rental ← замечания самого аукциона (announcements)
Замечания аукциона нужны потом, при разборе итогов торгов: дешёвая продажа с «Major engine defect» —
не показатель цены. Строку «KBB …$» окно читает обратно; строку аукциона при чтении заметки пропускает
(она и так есть в карточке). Ваш текст заметки остаётся как был.
"""

from __future__ import annotations

import datetime as dt
import re

# Строки, которые пишет программа (и прежний формат «LA …»): при пересчёте заменяются, при чтении пропускаются.
OWN_PREFIXES = ("LA ", "CarMax: ", "Manheim: ", "ACV: ", "ADESA: ", "Был: ")


def _date(iso: str = "") -> str:
    """«2026-10-02» → «10/2/26» (как в ваших заметках: «9/18»), пусто — сегодня."""
    try:
        day = dt.date.fromisoformat(iso[:10]) if iso else dt.date.today()
    except ValueError:
        day = dt.date.today()
    return f"{day.month}/{day.day}/{day.strftime('%y')}"


def past_sales(row: dict, limit: int = 3) -> list[str]:
    """Прошлые торги этой машины (по VIN, из вашей базы итогов): «Был: 9/28/26 CarMax Murrieta — продана $1,600».
    Только с итогом (продана / IF / не продана), последние `limit`."""
    out = []
    for item in str(row.get("seen_before") or "").split("; "):
        item = item.strip()
        if not item or re.search(r"итога нет|нет итога", item):
            continue
        out.append("Был: " + re.sub(r"^был на аукционе\s+", "", item))
    return out[-limit:]


def lot_note(row: dict) -> str:
    """KBB с датой, ваша ставка из окна («MP 5600»), прошлые торги машины и замечания аукциона.
    Пусто, если нет ни KBB, ни ставки, ни прошлых торгов."""
    kbb = str(row.get("kbb_private_party_usd") or "").replace(",", "")
    has_kbb = kbb.replace(".", "", 1).isdigit() and float(kbb) > 0 and not row.get("kbb_from_window_field")
    bid = str(row.get("my_proxy_usd") or "").replace(",", "") if row.get("my_bid_from_window") else ""
    has_bid = bid.replace(".", "", 1).isdigit() and float(bid) > 0
    history = past_sales(row)
    if not has_kbb and not has_bid and not history:
        return ""
    lines = [f"KBB {float(kbb):,.0f}$ {_date(row.get('kbb_date', ''))}"] if has_kbb else []
    if has_bid:
        lines.append(f"MP {float(bid):.0f}")
    lines += history
    remarks = str(row.get("auction_notes") or "").strip()
    if re.fullmatch(r"(no announcements?(\(s\))?|none|-)\.?", remarks, re.I):      # «нет замечаний» — не замечание
        remarks = ""
    if remarks:
        lines.append(f"{row.get('auction') or 'Auction'}: {remarks}")
    return "\n".join(lines)


def strip_own(text: str) -> str:
    """Заметка без строк замечаний аукциона, вписанных программой, — то, что писали вы, и строка KBB."""
    return "\n".join(line for line in str(text or "").splitlines() if not line.startswith(OWN_PREFIXES)).strip()


def notes_for(rows: list[dict]) -> dict[str, str]:
    """VIN → текст заметки, для машин с настоящим KBB или со ставкой, вписанной в окне."""
    out = {}
    for row in rows:
        vin = row.get("vin", "")
        note = lot_note(row)
        if vin and note and vin not in out:
            out[vin] = note
    return out
