"""Прошёл ли аукцион лота: чтобы в окне были только машины, которые ещё можно купить.

Правило: аукцион с днём торгов (CarMax, Manheim) — прошёл после 12:00 в этот день; онлайн-лот с точным
временем окончания (ACV, ADESA, Manheim Timed) — прошёл, когда это время наступило. Время из списка
бывает в виде обратного отсчёта («16:48:07», «осталось …», «3 days») — тогда от времени сохранения файла.
ACV «Make Offer» — торги прошли, но машину ещё можно купить: такие не скрываются.
"""

from __future__ import annotations

import datetime as dt
import re

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
NOON = dt.time(12, 0)


def _year_for(month: int, day: int, saved: dt.datetime) -> int:
    """Год для «Oct 5»: тот, при котором дата ближе всего ко времени сохранения файла."""
    best = saved.year
    for year in (saved.year - 1, saved.year, saved.year + 1):
        try:
            if abs((dt.datetime(year, month, day) - saved).days) < abs((dt.datetime(best, month, day) - saved).days):
                best = year
        except ValueError:
            pass
    return best


def _clock(text: str) -> dt.time | None:
    found = re.search(r"(\d{1,2}):(\d{2})\s*([ap])\.?\s*m\.?", text, re.I) or re.search(r"\b(\d{1,2}):(\d{2})\b(?!:)", text)
    if not found:
        return None
    hour, minute = int(found.group(1)), int(found.group(2))
    if found.lastindex == 3:
        hour = hour % 12 + (12 if found.group(3).lower() == "p" else 0)
    return dt.time(min(hour, 23), min(minute, 59))


def ends_at(text: str, saved: dt.datetime) -> tuple[dt.datetime | None, bool]:
    """Когда кончаются торги лота: (момент, точное_время). Для дня торгов без точного конца — полдень этого дня."""
    text = (text or "").strip()
    low = text.lower()
    if not text or low.startswith("make offer"):
        return None, False
    if low in ("ended", "sold"):
        return saved, True
    # обратный отсчёт: «16:48:07», «осталось 05:12:33», «3 days», «1 day»
    countdown = re.fullmatch(r"(?:осталось\s*)?(\d{1,3}):(\d{2}):(\d{2})", low) or re.fullmatch(r"(?:осталось\s*)?(\d{1,2}):(\d{2})", low)
    if countdown:
        parts = [int(x) for x in countdown.groups()] + [0]
        return saved + dt.timedelta(hours=parts[0], minutes=parts[1], seconds=parts[2]), True
    days = re.fullmatch(r"(\d+)\s*days?", low)
    if days:
        return saved + dt.timedelta(days=int(days.group(1))), True
    # ISO: «2026-09-30», «до 2026-09-30 13:00», «2026-10-01T16:00:00Z»
    iso = re.search(r"(20\d\d)-(\d\d)-(\d\d)(?:[ T](\d\d):(\d\d))?", text)
    if iso:
        day = dt.date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        if iso.group(4) and low.startswith("до"):
            return dt.datetime.combine(day, dt.time(int(iso.group(4)), int(iso.group(5)))), True
        return dt.datetime.combine(day, NOON), False
    # «10/5/2026 9:00 AM», «Ends Tue 10/06 12:00 p.m.», «10/6 10:00am»
    md = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(20\d\d))?\b", text)
    if md:
        month, day = int(md.group(1)), int(md.group(2))
        year = int(md.group(3)) if md.group(3) else _year_for(month, day, saved)
        try:
            date = dt.date(year, month, day)
        except ValueError:
            return None, False
        clock = _clock(text[md.end():])
        if low.startswith("ends") and clock:
            return dt.datetime.combine(date, clock), True
        if clock and "starts" not in low and not re.search(r"\b(am|pm)\s*pt\b", low):
            return dt.datetime.combine(date, clock), True
        return dt.datetime.combine(date, NOON), False
    # «Oct 5, 9:00am PT», «Started on Sep 29, 8:00am PT»
    mon = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})\b", low)
    if mon:
        month, day = MONTHS[mon.group(1)], int(mon.group(2))
        try:
            date = dt.date(_year_for(month, day, saved), month, day)
        except ValueError:
            return None, False
        return dt.datetime.combine(date, NOON), False
    return None, False


def passed(row: dict, saved: dt.datetime, now: dt.datetime | None = None) -> str:
    """Причина, если аукцион лота прошёл («торги 10/5 прошли»), иначе пусто."""
    now = now or dt.datetime.now()
    if row.get("lot_status") == "Make Offer":
        return ""
    if row.get("lot_status") in ("Sold", "Ended"):
        return "торги закончились"
    end, exact = ends_at(row.get("sale_date", ""), saved)
    if end and now >= end:
        return f"торги {end.month}/{end.day} прошли" + ("" if exact else " (после 12:00 дня торгов)")
    return ""
