"""Страницы-списки ADESA и ACV: результаты поиска, сохранённые поиски, watch list.

Классы в разметке у обеих площадок случайные, поэтому карточку машины ищем по
смыслу: у каждой карточки ровно один пробег («103,283 mi», «75,570 miles»).
Карточка — самый крупный блок вокруг пробега, в котором другого пробега нет.
Дальше поля читаются из строк текста карточки по подписям на экране.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from .normalize import parse_money, squeeze, vin_check_digit_ok

ODOMETER_RE = re.compile(r"^\s*([\d,]{2,9})\s*(?:mi|miles)\s*$", re.I)
HEADER_RE = re.compile(r"^((?:19|20)\d{2})\s+(\S+)\s+(.+)$")
MONEY_RE = re.compile(r"^\$\s?[\d,]+$")
GRADE_RE = re.compile(r"^\d\.\d$")
# Блоки «похожие машины» и рекомендации — не из поиска.
SKIP_CONTAINERS = '[data-testid="ds-carousel-wrapper"], [role="presentation"]'


def find_cards(soup: BeautifulSoup) -> list[Tag]:
    cards: list[Tag] = []
    seen: set[int] = set()
    for text in soup.find_all(string=ODOMETER_RE):
        node = text.parent
        if node is None or node.find_parent(attrs={"data-testid": "ds-carousel-wrapper"}) is not None:
            continue
        best = None
        while node is not None and node.name not in ("body", "html", "[document]"):
            count = len(node.find_all(string=ODOMETER_RE))
            if count == 1:
                best = node
            elif count > 1:
                break
            node = node.parent
        if best is not None and id(best) not in seen:
            seen.add(id(best))
            cards.append(best)
    return cards


def _lines(card: Tag) -> list[str]:
    return [squeeze(x) for x in card.get_text("\n").split("\n") if squeeze(x)]


def _after(lines: list[str], label: str) -> str:
    for i, line in enumerate(lines):
        if line == label and i + 1 < len(lines):
            return lines[i + 1]
    return ""


def _title(lines: list[str]) -> tuple[str, str, str, int]:
    for i, line in enumerate(lines):
        match = HEADER_RE.match(line)
        if match:
            year, make, model = match.groups()
            return year, make, model, i
    return "", "", "", -1


# ---------------------------------------------------------------- ADESA


def adesa_card(card: Tag) -> dict[str, str]:
    lines = _lines(card)
    year, make, model, at = _title(lines)
    info = {"auction": "ADESA", "year": year, "make": make, "model": model}
    if at >= 0 and at + 1 < len(lines):
        info["trim"] = lines[at + 1] if len(lines[at + 1]) <= 25 else ""
    grade = next((x for x in lines[:max(at, 0) + 1] if GRADE_RE.match(x)), "")
    info["grade"] = grade
    for i, line in enumerate(lines):
        odo = ODOMETER_RE.match(line)
        if odo:
            info["miles"] = odo.group(1).replace(",", "")
            place = lines[i + 1] if i + 1 < len(lines) else ""
            if i + 2 < len(lines) and lines[i + 2].startswith(","):
                place += lines[i + 2]
            info["location"] = place
            break
    info["site"] = next((x for x in lines if x.startswith("ADESA ")), "")
    info["run"] = next((x for x in lines if re.match(r"^Run \S+$", x)), "")
    seller = next((x for x in lines if x.startswith("Seller:")), "")
    info["seller"] = seller.replace("Seller:", "").strip()
    info["retail"] = _after(lines, "Retail")
    for i, line in enumerate(lines):
        if MONEY_RE.match(line) and i + 1 < len(lines) and lines[i + 1] in ("Opening bid", "Current bid", "High bid", "Current Bid"):
            info["bid"], info["bid_kind"] = line, lines[i + 1]
            break
    else:
        for i, line in enumerate(lines):
            if MONEY_RE.match(line) and i + 1 < len(lines) and lines[i + 1] == "Retail":
                info["bid"], info["bid_kind"] = line, "ставка"
                break
    # VIN в карточке разбит на две строки: «19XFB2F53DE» + «271909».
    for i in range(len(lines) - 1):
        joined = lines[i] + lines[i + 1]
        if re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", joined) and vin_check_digit_ok(joined):
            info["vin"] = joined
            break
    timing = next((i for i, x in enumerate(lines) if x.startswith(("Starts", "Ends"))), -1)
    if timing >= 0:
        text = lines[timing]
        if text in ("Ends", "Starts") and timing + 1 < len(lines):
            text += " " + lines[timing + 1]
        info["when"] = text
    elif lines and re.match(r"^\d{1,2} : \d{2}", lines[0]):
        info["when"] = "осталось " + lines[0].replace(" : ", ":")
    link = card.find("a", href=re.compile(r"/details/"))
    info["url"] = "https://marketplace.adesa.com" + link["href"] if link else ""
    info["lot"] = info["url"].rstrip("/").split("/")[-1][:12] if info["url"] else info.get("run", "")
    info["photo"] = _photo(card)
    return info


# ---------------------------------------------------------------- ACV

# Синий значок стоит почти у каждого лота ACV — его смысл по списку не определить, не трактуем.
ACV_LIGHTS = {"green": "зелёный", "yellow": "жёлтый", "red": "красный"}


def _photo(card: Tag) -> str:
    """Главное фото карточки (адрес в интернете; локальные копии «Сохранить как» не берём)."""
    for img in card.find_all("img"):
        src = img.get("src") or (img.get("srcset") or "").split(" ")[0]
        if src.startswith("https://") and not src.endswith(".svg"):
            return src
    return ""


def acv_card(card: Tag) -> dict[str, str]:
    lines = _lines(card)
    year, make, model, at = _title(lines)
    info = {"auction": "ACV", "year": year, "make": make, "model": model}
    subtitle = next((x for x in lines[at + 1:at + 8] if "•" in x), "") if at >= 0 else ""
    info["trim"] = subtitle.split("•")[0].strip() if subtitle else ""
    info["drive"] = subtitle
    odo = next((ODOMETER_RE.match(x) for x in lines if ODOMETER_RE.match(x)), None)
    info["miles"] = odo.group(1).replace(",", "") if odo else ""
    info["lights"] = [x for x in lines if x in ACV_LIGHTS]
    info["reserve"] = next((x for x in lines if x in ("No Reserve", "Low Reserve", "Reserve Met")), "")
    info["when"] = _after(lines, "schedule")
    info["bid"] = _after(lines, "Current Bid")
    info["buy_now"] = next((x.replace("Buy Now ", "") for x in lines if x.startswith("Buy Now $")), "")
    info["location"] = _after(lines, "location_on")
    bids = next((lines[i - 1] for i, x in enumerate(lines) if x in ("Bids", "Bid") and i > 0 and lines[i - 1].isdigit()), "")
    info["bids"] = bids
    link = card.find("a", href=re.compile(r"/auction/\d+"))
    lot = re.search(r"/auction/(\d+)", link["href"]) if link else None
    info["lot"] = lot.group(1) if lot else ""
    info["url"] = f"https://app.acvauctions.com/auction/{info['lot']}" if info["lot"] else ""
    info["seller"] = next((x for x in lines if ":" in x and "Reseller" in x), "")
    info["photo"] = _photo(card)
    return info


# ---------------------------------------------------------------- страница


def find_list(html: str) -> list[dict[str, str]]:
    """Карточки со страницы-списка ADESA или ACV (или [], если это не такой список)."""
    head = html[:600_000].lower()
    if "acvauctions" in head:
        extract = acv_card
    elif "adesa" in head:
        extract = adesa_card
    else:
        return []
    soup = BeautifulSoup(html, "lxml")
    for junk in soup.select('[data-testid="ds-carousel-wrapper"]'):
        junk.decompose()
    detail = soup.select_one("#auction-detail")
    if detail is not None:
        detail.decompose()          # открытая карточка ACV разбирается отдельно
    cards = [extract(card) for card in find_cards(soup)]
    # Без ссылки на лот — боковая панель watch list (проданные, «Make Offer»), не результаты поиска.
    cards = [c for c in cards if c.get("year") and c.get("miles") and c.get("lot")]
    return cards if len(cards) >= 2 else []


def row_from_card(row: dict[str, str], info: dict[str, str]) -> list[str]:
    """Заполняет строку таблицы из карточки списка. Возвращает заметки для «Проверить»."""
    notes: list[str] = []
    row["auction"] = info["auction"]
    for key in ("year", "make", "model", "trim", "location"):
        row[key] = info.get(key, "")
    row["vin"] = info.get("vin", "")
    row["lot_number"] = info.get("lot", "")
    row["odometer_miles"] = info.get("miles", "")
    row["sale_date"] = info.get("when", "")
    row["lot_url"] = info.get("url", "")
    row["photo_main_url"] = info.get("photo", "")
    bid = parse_money(info.get("bid"))
    row["current_bid_usd"] = f"{bid:.0f}" if bid else ""
    retail = parse_money(info.get("retail"))
    row["auction_retail_usd"] = f"{retail:.0f}" if retail else ""
    row["condition_grade"] = info.get("grade", "")

    history: list[str] = []
    lights = info.get("lights") or []
    if "red" in lights:
        history.append("as-is (red light)")
    if "yellow" in lights:
        history.append("yellow light: see announcements")
    row["history_page"] = " | ".join(history)
    row["condition_report"] = row["history_page"]
    row["title_type"] = ""

    extra = [x for x in (
        info.get("drive", ""),
        f"площадка: {info['site']}" if info.get("site") else "",
        info.get("run", ""),
        f"продавец: {info['seller']}" if info.get("seller") else "",
        f"{info['bid_kind'].lower()}: {info['bid']}" if info.get("bid_kind") and info.get("bid") else "",
        f"Buy Now {info['buy_now']}" if info.get("buy_now") else "",
        info.get("reserve", ""),
        f"ставок: {info['bids']}" if info.get("bids") else "",
        "огни ACV: " + ", ".join(ACV_LIGHTS[x] for x in lights) if lights else "",
    ) if x]
    row["lot_description"] = "; ".join(extra)
    notes.append("из списка: повреждения и история — в карточке лота")
    return notes
