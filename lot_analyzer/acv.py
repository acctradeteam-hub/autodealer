"""Разбор карточки лота ACV Auctions (app.acvauctions.com).

Сохранённая страница ACV — это весь Marketplace: слева список других лотов с
их ставками, фильтры поиска со словами «Salvage Title», «Flood Damage» и т.п.,
а сама карточка лота лежит в блоке #auction-detail. Если читать страницу
целиком, в строку попадут чужая ставка и ложные стоп-факторы, поэтому общий
разбор получает только этот блок, а объявления инспектора ACV снимаются
отсюда по отдельности: «заголовок — подробности».
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag

from .normalize import parse_money, squeeze

DETAIL_SELECTOR = "#auction-detail"


@dataclass
class AcvDetail:
    fragment_html: str                     # только карточка лота — для общего разбора
    fields: dict[str, str] = field(default_factory=dict)       # «Vehicle Details»: подпись -> значение
    announcements: list[tuple[str, str, bool]] = field(default_factory=list)  # (заголовок, подробности, жёлтое)
    current_bid: float | None = None
    transport_quote: float | None = None
    transport_note: str = ""
    seller: str = ""
    obd_codes: list[str] = field(default_factory=list)
    tire_depths: list[int] = field(default_factory=list)       # в 1/32"

    def conditions_text(self) -> str:
        """Все объявления одной строкой: «Заголовок: подробности | …»."""
        parts = []
        for title, details, _ in self.announcements:
            parts.append(f"{title}: {details}" if details else title)
        return " | ".join(parts)


def find_detail(html: str) -> AcvDetail | None:
    """Карточка лота ACV или None, если это не страница лота ACV."""
    soup = BeautifulSoup(html, "lxml")
    block = soup.select_one(DETAIL_SELECTOR)
    if block is None:
        return None

    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    detail = AcvDetail(fragment_html=f"<html><head><title>{title}</title></head><body>{block}</body></html>")

    for row in block.select(".auction-vehicle-details tr"):
        cells = [squeeze(c.get_text(" ", strip=True)) for c in row.find_all(["td", "th"])]
        if len(cells) >= 2 and cells[0]:
            detail.fields[cells[0]] = cells[1]

    notes = block.select_one(".auction-notes")
    if notes is not None:
        # Объявления инспектора — div.announcement, отметки Carfax — div.carfax-alerts.
        for box in notes.select("div.announcement, div.carfax-alerts"):
            if box.find_parent("div", class_="announcement") is not None:
                continue  # вложенная таблица внутри объявления
            heading = box.select_one("span.bold")
            if heading is None:
                continue
            head_text = squeeze(heading.get_text(" ", strip=True))
            name = re.sub(r"^CARFAX\S*\s*Reports?$", "Carfax", head_text.rstrip(":").strip(), flags=re.I)
            full = squeeze(box.get_text(" ", strip=True))
            details = squeeze(full[len(head_text):]) if full.startswith(head_text) else full
            cell = box.find_parent("td")
            yellow = bool(cell is not None and "yellow-announcement" in (cell.get("class") or []))
            detail.announcements.append((name, details, yellow))

    bid_box = block.select_one(".bid-amount-wrapper")
    if bid_box is not None:
        detail.current_bid = parse_money(bid_box.get_text(" ", strip=True))

    transport = block.select_one(".auction-purchase-options")
    if isinstance(transport, Tag):
        text = squeeze(transport.get_text(" ", strip=True))
        price = re.search(r"\$\s?[\d,]+", text)
        if price:
            detail.transport_quote = parse_money(price.group(0))
            days = re.search(r"(\d+)\s+business days?", text)
            detail.transport_note = f"доставка ACV ${detail.transport_quote:,.0f}" + (f", {days.group(1)} раб. дня" if days else "")

    metrics = block.select_one(".auction-metrics")
    if metrics is not None:
        text = squeeze(metrics.get_text(" | ", strip=True))
        seller = re.search(r"Seller \| ([^|]+)", text)
        detail.seller = squeeze(seller.group(1)) if seller else ""

    conditions = detail.conditions_text()
    detail.obd_codes = sorted(set(re.findall(r"\b[PBCU][0-3][0-9A-F]{3}\b", conditions)))
    detail.tire_depths = [int(x) for x in re.findall(r"Tire:\s*(\d{1,2})\s*/\s*32", conditions)]
    return detail


def apply_detail(row: dict[str, str], detail: AcvDetail) -> list[str]:
    """Перекрывает поля строки данными карточки ACV. Возвращает заметки для «Проверить»."""
    notes: list[str] = []
    f = detail.fields
    row["auction"] = "ACV"
    if f.get("Auction ID"):
        row["lot_number"] = f["Auction ID"].lstrip("#")
    for key, label in (("make", "Make"), ("model", "Model"), ("trim", "Trim"), ("location", "City")):
        if f.get(label):
            row[key] = f[label]
    if f.get("Year", "").isdigit():
        row["year"] = f["Year"]
    if f.get("Odometer"):
        miles = parse_money(f["Odometer"])
        if miles is not None:
            row["odometer_miles"] = str(int(miles))
    # Оценки ACV в карточке нет; общий разбор находит «ACV» в чужих числах (адрес площадки).
    row["acv_estimate_usd"] = ""
    if detail.current_bid is not None:
        row["current_bid_usd"] = f"{detail.current_bid:.0f}"
    if detail.transport_quote is not None:
        row["transport_quote_usd"] = f"{detail.transport_quote:.0f}"

    titles = [t.lower() for t, _, _ in detail.announcements]
    conditions = detail.conditions_text()
    row["condition_report"] = conditions[:1500]
    # Дефекты — объявления без служебных (показания толщиномера, мониторы готовности).
    skip_titles = ("paint meter readings", "incomplete readiness monitors")
    row["defects"] = " | ".join(
        (f"{t}: {d}" if d else t) for t, d, _ in detail.announcements if t.lower() not in skip_titles
    )[:1500]

    history = [f"{t}: {d}" if d else t for t, d, _ in detail.announcements if "carfax" in t.lower() or "title" in t.lower() or "theft" in t.lower() or "structural" in t.lower()]
    row["history_page"] = " | ".join(history)[:800]

    title_flags = [t for t, _, _ in detail.announcements if "title" in t.lower()]
    row["title_type"] = "; ".join(title_flags)[:80] if title_flags else "без замечаний по титулу (ACV)"

    row["run_and_drive"] = "нет" if any("inop" in t or "does not move" in t for t in titles) else "да"
    keys = next((d for t, d, _ in detail.announcements if t.lower() == "keys present"), "")
    count = re.search(r"(\d+)\s+keys?", keys)
    row["keys_present"] = f"да ({count.group(1)})" if count else ("да" if keys else row.get("keys_present", ""))

    description = [f"продавец: {detail.seller}"] if detail.seller else []
    if detail.obd_codes:
        description.append("коды OBD: " + ", ".join(detail.obd_codes))
    if detail.tire_depths:
        description.append("протектор: " + ", ".join(f"{d}/32" for d in detail.tire_depths))
    if detail.transport_note:
        description.append(detail.transport_note)
    row["lot_description"] = "; ".join(description)

    if any(yellow for _, _, yellow in detail.announcements):
        notes.append("есть жёлтое объявление ACV: " + ", ".join(t for t, _, y in detail.announcements if y))
    notes.append("торги идут онлайн — время окончания смотрите в карточке")
    return notes
