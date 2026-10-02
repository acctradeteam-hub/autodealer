"""Текст для заметки (Notes) на карточке лота на сайте аукциона.

Пишется только машинам с настоящим KBB Private Party. Расширение «Lot Analyzer KBB» берёт
эти тексты у окна программы (/api/notes) и вписывает их в Notes на carmaxauctions.com.

Формат — две строки:
    KBB 19,530$ 10/2/26                            ← KBB Private Party и дата, на которую он посчитан
    CarMax: Major transmission defect, Prior rental ← замечания самого аукциона (announcements)
Замечания аукциона нужны потом, при разборе итогов торгов: дешёвая продажа с «Major engine defect» —
не показатель цены. Строку «KBB …$» окно читает обратно; строку аукциона при чтении заметки пропускает
(она и так есть в карточке). Ваш текст заметки остаётся как был.
"""

from __future__ import annotations

import datetime as dt

# Строки, которые пишет программа (и прежний формат «LA …»): при пересчёте заменяются, при чтении пропускаются.
OWN_PREFIXES = ("LA ", "CarMax: ", "Manheim: ", "ACV: ", "ADESA: ")


def _date(iso: str = "") -> str:
    """«2026-10-02» → «10/2/26» (как в ваших заметках: «9/18»), пусто — сегодня."""
    try:
        day = dt.date.fromisoformat(iso[:10]) if iso else dt.date.today()
    except ValueError:
        day = dt.date.today()
    return f"{day.month}/{day.day}/{day.strftime('%y')}"


def lot_note(row: dict) -> str:
    """KBB с датой и замечания аукциона. Пусто, если настоящего KBB нет."""
    kbb = str(row.get("kbb_private_party_usd") or "").replace(",", "")
    if not kbb.replace(".", "", 1).isdigit() or float(kbb) <= 0:
        return ""
    lines = [f"KBB {float(kbb):,.0f}$ {_date(row.get('kbb_date', ''))}"]
    remarks = str(row.get("auction_notes") or "").strip()
    if remarks:
        lines.append(f"{row.get('auction') or 'Auction'}: {remarks}")
    return "\n".join(lines)


def strip_own(text: str) -> str:
    """Заметка без строк замечаний аукциона, вписанных программой, — то, что писали вы, и строка KBB."""
    return "\n".join(line for line in str(text or "").splitlines() if not line.startswith(OWN_PREFIXES)).strip()


def notes_for(rows: list[dict]) -> dict[str, str]:
    """VIN → текст заметки, для машин с настоящим KBB."""
    out = {}
    for row in rows:
        vin = row.get("vin", "")
        note = lot_note(row)
        if vin and note and vin not in out:
            out[vin] = note
    return out
