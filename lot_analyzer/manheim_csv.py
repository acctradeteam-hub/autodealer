"""CSV-выгрузка списка Manheim (кнопка экспорта на странице результатов поиска).

Страница поиска Manheim показывает по 100 машин, а выгрузка — весь список
(Manheim California 30.09: 2 111 строк против 100 на первой странице). В CSV есть
VIN, машина, пробег, MMR, оценка состояния (CR grade), продавец, дорожка/номер,
ставка и Buy Now у OVE, комментарии продавца. Нет AutoCheck, объявлений
(announcements), фото и статуса титула — они есть на сохранённых закладкой
страницах. Поэтому строка из CSV — предварительная: если та же машина есть
на сохранённой странице, в окне берётся строка со страницы.

Одна машина может стоять дважды: в живых торгах (Simulcast, дорожка/номер)
и в OVE (Timed Sale, Buy Now). Остаётся одна строка — живые торги, а цены OVE
дописываются в описание.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
from pathlib import Path

from .schema import empty_row

REQUIRED = ("Vin", "Year", "Make", "Model", "MMR", "Condition Report Grade")
TIMED = "Timed Sale"


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def is_export(path: Path) -> bool:
    """Похоже ли на выгрузку Manheim — по заголовку, не по имени файла."""
    if path.suffix.lower() != ".csv":
        return False
    try:
        with path.open("rb") as handle:
            head = _decode(handle.read(2000))
    except OSError:
        return False
    first = re.split(r"\r\n|\r|\n", head, maxsplit=1)[0]
    return all(f'{name}' in first for name in REQUIRED) and "Auction House" in first


def _money(value: str) -> str:
    value = (value or "").replace("$", "").replace(",", "").strip()
    try:
        return f"{float(value):.0f}" if value and float(value) > 0 else ""
    except ValueError:
        return ""


def _comments(text: str) -> str:
    text = re.sub(r"br\s*/", " ", text or "")          # «<br />» без угловых скобок
    return re.sub(r"\s+", " ", text).strip()


def _utc_to_local(value: str) -> str:
    """«2026-09-30T16:00:00Z» → «2026-09-30 09:00» по времени Калифорнии (UTC−7 летом)."""
    try:
        stamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return (stamp - dt.timedelta(hours=7)).strftime("%Y-%m-%d %H:%M")


def read_export(path: Path) -> list[dict[str, str]]:
    text = _decode(path.read_bytes()).replace("\r\n", "\n").replace("\r", "\n")
    records = list(csv.DictReader(io.StringIO(text)))
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    by_vin: dict[str, list[dict[str, str]]] = {}
    for record in records:
        vin = (record.get("Vin") or "").strip().upper()
        if vin:
            by_vin.setdefault(vin, []).append(record)

    rows = []
    for vin, group in by_vin.items():
        live = [r for r in group if r.get("Inventory") != TIMED]
        main = live[0] if live else group[0]
        row = empty_row()
        row["source_file"], row["parsed_at"] = path.name, stamp
        row["auction"] = "Manheim"
        row["vin"] = vin
        row["year"] = (main.get("Year") or "").strip()
        row["make"] = (main.get("Make") or "").strip()
        row["model"] = (main.get("Model") or "").strip()
        row["trim"] = (main.get("Trim") or "").strip()
        miles = _money(main.get("Odometer Value", ""))
        if miles and (main.get("Odometer Units") or "mi").lower().startswith("km"):
            miles = f"{float(miles) / 1.609:.0f}"
        row["odometer_miles"] = miles
        row["location"] = main.get("Pickup Location") or main.get("Auction House") or ""
        row["mmr_adjusted_usd"] = row["wholesale_usd"] = _money(main.get("MMR", ""))
        grade = (main.get("Condition Report Grade") or "").strip()
        row["condition_grade"] = grade

        extra = []
        if main.get("Inventory") == TIMED:
            row["lot_number"] = "OVE"
            row["sale_date"] = f"до {_utc_to_local(main.get('Ends At', ''))}"
        else:
            lane, run = (main.get("Lane") or "").strip(), (main.get("Run") or "").strip()
            row["lot_number"] = f"{lane}-{run}" if lane or run else ""
            row["sale_date"] = _utc_to_local(main.get("Starts At", ""))[:10]
            if lane or run:
                extra.append(f"дорожка {lane or '?'}, номер {run or '?'}")
        if main.get("Event Sale Name"):
            extra.append(main["Event Sale Name"].strip())
        for record in group:
            if record.get("Inventory") != TIMED:
                continue
            bid, buy_now = _money(record.get("Bid Amount", "")), _money(record.get("Buy Now Price", ""))
            if record is main:
                row["current_bid_usd"] = bid
            parts = [f"ставка ${float(bid):,.0f}" if bid else "", f"Buy Now ${float(buy_now):,.0f}" if buy_now else ""]
            parts = [p for p in parts if p]
            if parts:
                extra.append("OVE: " + ", ".join(parts) + f" (до {_utc_to_local(record.get('Ends At', ''))})")
        if main.get("Seller Name"):
            extra.append(f"продавец: {main['Seller Name'].strip()}")
        details = " ".join(x for x in (main.get("Drivetrain"), main.get("Engine Type"), main.get("Transmission Type")) if x)
        if details:
            extra.append(details)
        row["lot_description"] = "; ".join(extra)

        comments = _comments(main.get("Seller Comments", ""))
        report = ([f"grade {grade}"] if grade else []) + ([comments[:400]] if comments else [])
        row["condition_report"] = " | ".join(report)
        if re.search(r"\bas[\s-]*is\b|no arbitration|non[\s-]*runner|does not run|no start|tmu|title\s+(?:absent|attached)", comments, re.I):
            row["defects"] = comments[:300]

        notes = ["из списка: CSV-выгрузка Manheim — AutoCheck, объявления и фото в карточке лота"]
        if not row["mmr_adjusted_usd"]:
            notes.append("нет MMR")
        if not grade:
            notes.append("нет CR grade")
        row["needs_review"] = "; ".join(notes)
        rows.append(row)
    return rows
