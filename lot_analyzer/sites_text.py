"""Разбор карточек ADESA и CarMax Auctions по видимому тексту.

У обеих площадок классы в разметке генерируются сборщиком (sc-fZNomH,
css-1plakzg) и меняются при каждом обновлении сайта, а встроенного JSON с
лотом нет. Зато подписи на экране стабильные: «VIN:», «Opening bid»,
«Wholesale:», «Damage assessment», «Tread depth». Поэтому берём только блок
карточки (без «похожих машин» и списка результатов поиска), превращаем его в
строки текста и читаем значения рядом с подписями.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag

from .normalize import parse_money, split_model, squeeze
from .notes import strip_own

MONEY_RE = re.compile(r"^\$\s?[\d,]+(\.\d\d)?$")
DATE_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}\b")
TIRE_RE = re.compile(r"^(\d{1,2})\s*/\s*32")
BULLET_RE = re.compile(r"\s*•\s*")
HEADER_RE = re.compile(r"^((?:19|20)\d{2})\s+(\S+)\s+(.+)$")


@dataclass
class TextDetail:
    auction: str
    fragment_html: str
    lines: list[str]
    fields: dict[str, str] = field(default_factory=dict)
    announcements: list[str] = field(default_factory=list)
    damages: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)      # разделы отчёта с «Issue Present»
    history: list[str] = field(default_factory=list)
    tires: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _lines(tag: Tag) -> list[str]:
    for junk in tag(["script", "style", "svg", "noscript"]):
        junk.decompose()
    return [squeeze(x) for x in tag.get_text("\n").split("\n") if squeeze(x)]


def _after(lines: list[str], label: str, start: int = 0, skip: tuple[str, ...] = (":", "- $", "$", "-")) -> str:
    """Первая содержательная строка после строки-подписи."""
    for i in range(start, len(lines)):
        if lines[i] == label:
            for value in lines[i + 1:i + 5]:
                if value not in skip:
                    return value
    return ""


def _index(lines: list[str], label: str, start: int = 0) -> int:
    for i in range(start, len(lines)):
        if lines[i] == label:
            return i
    return -1


def _fragment(title: str, tag: Tag) -> str:
    return f"<html><head><title>{title}</title></head><body>{tag}</body></html>"


# ---------------------------------------------------------------- ADESA


def find_adesa(html: str) -> TextDetail | None:
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    if "adesa" not in title.lower() or soup.body is None:
        return None
    # «Похожие машины» — карусель с чужими ставками и пробегом: выбрасываем.
    for carousel in soup.select('[data-testid="ds-carousel-wrapper"]'):
        carousel.decompose()
    body = soup.body
    lines = _lines(body)
    cut = _index(lines, "Similar Vehicles You Might Like")
    if cut >= 0:
        lines = lines[:cut]
    if "VIN:" not in lines:
        return None
    detail = TextDetail("ADESA", _fragment(title, body), lines)
    f = detail.fields

    # Заголовок «2013 Honda Civic» и следующей строкой комплектация «LX».
    for i, line in enumerate(lines[:80]):
        match = HEADER_RE.match(line)
        if match and not line.endswith("mi"):
            f["year"], f["make"], f["model"] = match.groups()
            f["trim"] = lines[i + 1] if i + 1 < len(lines) and len(lines[i + 1]) <= 20 else ""
            if i > 0 and re.fullmatch(r"\d\.\d", lines[i - 1]):
                f["grade"] = lines[i - 1]
            elif i > 1 and re.fullmatch(r"\d\.\d", lines[i - 2]):
                f["grade"] = lines[i - 2]
            break
    for i, line in enumerate(lines):
        if line == "mi" and i > 0 and re.fullmatch(r"[\d,]+", lines[i - 1]):
            f["miles"] = lines[i - 1].replace(",", "")
            break
    f["vin"] = _after(lines, "VIN:")
    f["seller"] = _after(lines, "Seller:")
    vin_at = _index(lines, f["vin"]) if f["vin"] else -1
    if vin_at >= 0:
        after_vin = lines[vin_at + 1:vin_at + 6]
        f["location"] = after_vin[0] if after_vin else ""
        f["lane"] = next((x for x in after_vin if x.upper().startswith(("LOT", "LANE"))), "")
        f["sale_date"] = next((x for x in after_vin if DATE_RE.match(x)), "")
    for i, line in enumerate(lines):
        if line in ("Opening bid", "Current bid", "High bid", "Current Bid", "Starting bid") and i > 0 and MONEY_RE.match(lines[i - 1]):
            f["bid"], f["bid_kind"] = lines[i - 1], line
            break
    f["wholesale"] = _after(lines, "Wholesale:")
    f["retail"] = _after(lines, "Retail:")
    fee_at = _index(lines, "Buy fee")
    if fee_at >= 0:
        f["buy_fee"] = next((x for x in lines[fee_at + 1:fee_at + 4] if re.fullmatch(r"[\d,]+", x)), "")
    personal = next((x for x in lines if x.startswith("Personalized max bid")), "")
    f["adesa_max_bid"] = personal.split("$")[-1] if "$" in personal else ""
    remaining = _index(lines, "Time Remaining")
    if remaining >= 0:
        f["time_remaining"] = "".join(x for x in lines[remaining + 1:remaining + 6] if re.fullmatch(r"\d{1,2}|:", x))

    ann = _index(lines, "Announcements")
    if ann >= 0:
        detail.announcements = [x for x in lines[ann + 1:ann + 4] if x != ":" and x not in ("Mechanical", "Exterior", "Interior")][:1]

    # Таблица «All Damages»: тройки «деталь — повреждение — степень» до строки «to».
    head = _index(lines, "Severity")
    if head >= 0 and lines[head - 1] == "Type":
        i = head + 1
        while i + 1 < len(lines) and lines[i] not in ("to", "No Damages Reported"):
            part, kind = lines[i], lines[i + 1]
            severity = lines[i + 2] if i + 2 < len(lines) and lines[i + 2] != "to" else ""
            detail.damages.append(f"{part}: {kind} {severity}".strip())
            i += 3

    detail.issues = [lines[i - 1] for i, x in enumerate(lines) if x == "Issue Present" and i > 0]
    for i, line in enumerate(lines):
        if line in ("Left Front", "Right Front", "Left Rear", "Right Rear") and i + 1 < len(lines):
            depth = TIRE_RE.match(lines[i + 1])
            if depth:
                detail.tires.append(int(depth.group(1)))
    f["keys"] = next((x for x in lines if re.search(r"\bKeys? Present\b", x) and re.match(r"^\d", x)), "")

    hist = _index(lines, "View Summary Report")
    if hist >= 0:
        for x in lines[hist + 1:hist + 10]:
            if x.startswith("AutoCheck") or x == "Configuration":
                break
            detail.history.append(x)
    return detail


# ---------------------------------------------------------------- CarMax Auctions


def find_carmax(html: str) -> TextDetail | None:
    if "carmaxauctions" not in html[:600_000].lower() and "CarMax Auctions" not in html[:5000]:
        return None
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    # Карточка открыта поверх результатов поиска — во всплывающем окне.
    modal = None
    for candidate in soup.select('[role="presentation"]'):
        if candidate.find(string=re.compile(r"^\s*Vehicle info\s*$")):
            modal = candidate
            break
    if modal is None:
        return None
    lines = _lines(modal)
    detail = TextDetail("CarMax", _fragment(title, modal), lines)
    f = detail.fields
    f["vin"] = _after(lines, "VIN")
    vin_at = [i for i, x in enumerate(lines) if x == f["vin"]]
    for i in vin_at:
        if i + 1 < len(lines) and HEADER_RE.match(lines[i + 1]):
            year, make, rest = HEADER_RE.match(lines[i + 1]).groups()
            model, _, trim = rest.partition(" ")
            f.update(year=year, make=make, model=model, trim=trim)
            # «A/21 • Chino, CA • VIN»
            head = [x for x in lines[max(0, i - 5):i] if x != "•"]
            if len(head) >= 2:
                f["lane_run"], f["location"] = head[-2], head[-1]
            break
    f["miles"] = _after(lines, "Miles").replace(",", "")
    f["sale_date"] = _after(lines, "Date")
    f["sale_time"] = _after(lines, "Time")
    f["run"] = _after(lines, "Run Number")
    f["lane"] = _after(lines, "Lane")

    ann = _index(lines, "Announcements")
    if ann >= 0:
        for x in lines[ann + 1:ann + 12]:
            if x.startswith(("Damage Detected", "View Full Assessment", "Add a note", "Miles")):
                break
            for item in x.split(","):
                if squeeze(item) and squeeze(item) != "No announcement(s)":
                    detail.announcements.append(squeeze(item))

    # Вкладки фото «Ext. (8)», «Int. (4)», «Tires (4)»… — все нули: фото нет.
    counts = [int(m.group(1)) for x in lines for m in [re.match(r"^(?:Ext\.|Int\.|Undercarriage|Tires|OBDII)\s*\((\d+)\)$", x)] if m]
    if counts:
        f["photo_total"] = str(sum(counts))
    dmg = _index(lines, "Damage assessment")
    if dmg >= 0:
        i = dmg + 1
        while i + 1 < len(lines) and lines[i] not in ("Tire information", "Features"):
            detail.damages.append(lines[i] + ": " + BULLET_RE.sub(", ", lines[i + 1]))
            i += 2
    tread = _index(lines, "Tread depth")
    if tread >= 0:
        for x in lines[tread + 1:tread + 12]:
            depth = TIRE_RE.match(x)
            if depth:
                detail.tires.append(int(depth.group(1)))
    return detail


# ---------------------------------------------------------------- строка таблицы


def apply_detail(row: dict[str, str], detail: TextDetail) -> list[str]:
    """Заполняет строку из карточки ADESA / CarMax. Возвращает заметки для «Проверить»."""
    f = detail.fields
    notes = list(detail.notes)
    row["auction"] = detail.auction
    for key, name in (("vin", "vin"), ("year", "year"), ("make", "make"), ("model", "model"), ("trim", "trim"), ("location", "location")):
        if f.get(name):
            row[key] = f[name]
    if f.get("miles", "").isdigit():
        row["odometer_miles"] = f["miles"]
    row["lot_number"] = f.get("lane_run") or f.get("lane", "") if detail.auction == "CarMax" else f.get("lane", "")
    if detail.auction == "CarMax" and f.get("lane") and f.get("run"):
        row["lot_number"] = f"{f['lane']}/{f['run']}"
    row["sale_date"] = squeeze(f"{f.get('sale_date', '')} {f.get('sale_time', '')}")
    bid = parse_money(f.get("bid"))
    row["current_bid_usd"] = f"{bid:.0f}" if bid else ""
    wholesale, retail = parse_money(f.get("wholesale")), parse_money(f.get("retail"))
    row["wholesale_usd"] = f"{wholesale:.0f}" if wholesale else ""
    row["auction_retail_usd"] = f"{retail:.0f}" if retail else ""
    row["condition_grade"] = f.get("grade", "")
    row["mmr_adjusted_usd"] = row.get("mmr_adjusted_usd", "") if detail.auction != "ADESA" else ""
    row["acv_estimate_usd"] = ""

    defects = detail.announcements + detail.damages + [f"{x}: issue present" for x in detail.issues]
    keys = re.match(r"(\d+)", f.get("keys", ""))
    if keys:
        row["keys_present"] = f"да ({keys.group(1)})"
        if keys.group(1) == "1":
            defects.append("1 key")
    if detail.tires:
        defects.append("tires: " + ", ".join(f"{t}/32" for t in detail.tires))
    row["defects"] = " | ".join(defects)[:1500]
    row["condition_report"] = " | ".join(
        ([f"grade {f['grade']}"] if f.get("grade") else []) + detail.announcements + detail.damages + [f"{x}: issue present" for x in detail.issues]
    )[:1500]
    if not row["condition_report"] and detail.auction == "CarMax":
        row["condition_report"] = "повреждений не отмечено (CarMax)"
    row["history_page"] = " | ".join(detail.history + [a for a in detail.announcements if re.search(r"title|history|rental|fleet|lease|theft|227|miles", a, re.I)])[:800]
    title_flags = [x for x in detail.history + detail.announcements if re.search(r"title|227", x, re.I)]
    row["title_type"] = "; ".join(title_flags)[:80] or f"без замечаний по титулу ({detail.auction})"
    row["damage_primary"] = row["damage_secondary"] = ""
    row["run_and_drive"] = "нет" if any(re.search(r"no[n]?[\s-]*runner|inop", a, re.I) for a in detail.announcements) else ("да" if detail.auction == "ADESA" and "Drivable" in detail.lines else row.get("run_and_drive", ""))

    extra = []
    if f.get("seller"):
        extra.append(f"продавец: {f['seller']}")
    if f.get("lane") and detail.auction == "ADESA":
        extra.append(f["lane"])
    if f.get("bid_kind"):
        extra.append(f"{f['bid_kind'].lower()}: {f['bid']}")
    if f.get("buy_fee"):
        extra.append(f"сбор ADESA в карточке: ${f['buy_fee']}")
    if f.get("adesa_max_bid"):
        extra.append(f"«Personalized max bid» ADESA: ${f['adesa_max_bid']}")
    if f.get("time_remaining"):
        extra.append(f"до конца: {f['time_remaining']}")
    if detail.tires:
        extra.append("протектор: " + ", ".join(f"{t}/32" for t in detail.tires))
    row["lot_description"] = "; ".join(extra)
    if f.get("photo_total") is not None and f.get("photo_total") != "":
        row["photo_count"] = f["photo_total"]
        row["no_photos"] = "да" if f["photo_total"] == "0" else ""

    if detail.auction == "ADESA" and f.get("bid_kind") == "Opening bid":
        notes.append("ставок ещё нет — показана стартовая цена")
    if detail.auction == "CarMax":
        notes.append("ставку CarMax видно только в Simulcast во время торгов")
    return notes


# ---------------------------------------------------------------- CarMax: watch list / список лотов

# Заметки покупателя в карточке: «KBB 14,380$», «KBB $6,125», «KBB PP $19,940 (92620, 9/18)»,
# «MMR $13,850», «Est Retail $20,000», «sold 8400».
_NOTE_KBB = re.compile(r"\bKBB(?:\s*PP)?\s*\$?\s*([\d][\d,]{2,})\s*\$?", re.I)
_NOTE_MMR = re.compile(r"\bMMR\s*\$?\s*([\d][\d,]{2,})", re.I)
_NOTE_RETAIL = re.compile(r"\b(?:est\.?\s*)?retail\s*\$?\s*([\d][\d,]{2,})", re.I)
# Своя цена продажи на Facebook Marketplace: «FB 9500», «FB $9,500», «sell 9500».
_NOTE_SALE = re.compile(r"\b(?:FB|sell|sale)\s*\$?\s*([\d][\d,]{2,})", re.I)
# Своя прокси-ставка: «MP 5600», «MP $5,600», «my proxy 5600».
_NOTE_PROXY = re.compile(r"\b(?:MP|my\s*proxy|proxy)\s*\$?\s*([\d][\d,]{2,})", re.I)
# «No pictures», «no pics», «без фото» в заметке или объявлении — лот без фотографий.
NO_PHOTOS_RE = re.compile(r"\bno\s*(pictures?|pics|photos?)\b|\bбез\s*фото", re.I)
_NOTE_SOLD = re.compile(r"\bsold\s*(?:for\s*)?\$?\s*([\d][\d,]{2,})", re.I)


def _last(pattern: re.Pattern[str], text: str) -> str:
    """Последнее значение в заметке: дописанное позже — свежее."""
    found = pattern.findall(text)
    return found[-1].replace(",", "") if found else ""


def _carmax_page_url(html: str) -> str:
    """Адрес сохранённой страницы CarMax (из пометки закладки или «saved from url» Chrome)."""
    m = re.search(r"; url: (https://[^\s]*carmaxauctions\.com[^\s]*) -->", html[:3000]) or \
        re.search(r"saved from url=\(\d+\)(https://[^\s]*carmaxauctions\.com[^\s]*) -->", html[:3000])
    return m.group(1) if m else ""


def carmax_lot_url(stock: str, page_url: str = "") -> str:
    """Страница лота CarMax: все фото, отчёт и кнопка ставки «Bid early»."""
    return f"https://www.carmaxauctions.com/vehicledetail/{stock}" if stock else ""


def carmax_photo_url(stock: str) -> str:
    """Главное фото CarMax по номеру машины (так их отдаёт сам сайт)."""
    return f"https://img2.carmax.com/assets/{stock}/image/DSIDE.jpg?width=400&height=300" if stock else ""


def _carmax_photo(card, page_url: str) -> str:
    """Главное фото карточки: адрес из <img> (у страниц «Сохранить как» Chrome — локальный файл, такой не берём)."""
    from urllib.parse import urljoin
    for img in card.find_all("img"):
        src = img.get("src") or img.get("data-src") or (img.get("srcset") or "").split(" ")[0]
        if not src or src.startswith(("data:", "./", "file:")) or "_files/" in src:
            continue
        url = urljoin(page_url or "https://www.carmaxauctions.com/", src)
        if url.startswith("http"):
            return url
    return ""


# Карточка CarMax без фотографий: вместо фото надпись «Check back for images».
_CARMAX_NO_IMAGES = re.compile(r"check back for images|no images available|images coming soon", re.I)


def _carmax_card_photo(card, page_url: str) -> str:
    """Фото карточки; пусто — у машины фото нет (CarMax пишет «Check back for images»)."""
    if card.find(string=_CARMAX_NO_IMAGES) and not card.find("img"):
        return ""
    return _carmax_photo(card, page_url) or carmax_photo_url(card["id"] if card.has_attr("id") else "")


def find_carmax_cards(html: str) -> list[dict[str, str]]:
    """Карточки машин со страницы-списка CarMax (watch list, результаты с VIN).

    Карточка — блок с кнопкой «Copy VIN». Блоки «Nearby cars / Sedans you might
    like» VIN не показывают и сюда не попадают.
    """
    if "carmax" not in html[:600_000].lower():
        return []
    soup = BeautifulSoup(html, "lxml")
    page_url = _carmax_page_url(html)
    cards: list[dict[str, str]] = []
    seen: set[str] = set()
    for button in soup.select('[data-testid="copy-vin-button"]'):
        vin_box = button.parent
        vin = squeeze(vin_box.get_text(" ", strip=True))
        if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin) or vin in seen:
            continue
        card = vin_box
        while card is not None and not (card.name == "div" and re.fullmatch(r"\d{5,}", card.get("id", ""))):
            card = card.parent
        if card is None or card.find_parent(attrs={"role": "presentation"}) is not None:
            continue
        seen.add(vin)
        info: dict[str, str] = {"vin": vin, "stock": card["id"], "lot_url": carmax_lot_url(card["id"], page_url), "photo": _carmax_card_photo(card, page_url)}
        caption = card.find("p", class_=re.compile("caption"))
        head = squeeze(caption.get_text(" ", strip=True)) if caption else ""
        lane_run, _, location = head.partition("•")
        info["lane_run"], info["location"] = squeeze(lane_run), squeeze(location)
        title_btn = caption.find_next("button") if caption else None
        info["title"] = squeeze(title_btn.get_text(" ", strip=True)) if title_btn else ""
        texts = [squeeze(p.get_text(" ", strip=True)) for p in card.find_all("p")]
        info["miles"] = next((t.replace(",", "").removesuffix(" mi") for t in texts if re.fullmatch(r"[\d,]+ mi", t)), "")
        info["drive"] = next((t for t in texts if "•" in t and "Drive" in t), "")
        # Объявления CarMax — подписи (caption) после VIN, кроме пометок «Updated …».
        announcements = []
        # Только внутри карточки: find_all_next шёл бы до конца страницы у каждой машины (минуты на больших списках).
        after_vin = False
        for span in card.descendants:
            if span is vin_box:
                after_vin = True
                continue
            if not after_vin or getattr(span, "name", None) != "span" or not any("caption" in c for c in span.get("class") or []):
                continue
            text = squeeze(span.get_text(" ", strip=True))
            if text and not text.startswith("Updated") and text != "No announcement(s)":
                announcements.append(text)
        info["announcements"] = " | ".join(dict.fromkeys(announcements))
        note = card.find("textarea")
        info["notes"] = squeeze(strip_own(note.get_text())) if note else ""   # без строк расчёта программы («LA …»)
        status = card.find(string=re.compile(r"^\s*(Ended|Live|Upcoming|Sold)\s*$"))
        info["status"] = squeeze(status) if status else ""
        started = card.find(string=re.compile(r"Started on|Starts on|Starts"))
        info["start"] = squeeze(str(started)).replace("Started on ", "").replace("Starts on ", "") if started else ""
        bid = card.find(string=re.compile(r"^\s*\$[\d,]+\s*$"))
        info["your_bid"] = squeeze(bid) if bid and card.find(string=re.compile("Your bid")) else ""
        cards.append(info)
    return cards or _carmax_tiles(soup, page_url) or _carmax_by_vin(soup, page_url)


def _carmax_tiles(soup, page_url: str = "") -> list[dict[str, str]]:
    """Вид «плитка» (поиск CarMax): VIN не показан — берём дорожку, машину, пробег и объявления."""
    cards: list[dict[str, str]] = []
    for card in soup.find_all("div", id=re.compile(r"^\d{5,}$")):
        if card.find_parent(attrs={"role": "presentation"}) is not None or card.find_parent("div", id=re.compile(r"^\d{5,}$")):
            continue
        texts = [squeeze(p.get_text(" ", strip=True)) for p in card.find_all("p")]
        head = next((t for t in texts if re.match(r"[A-Z]+/\d+\s*•", t)), "")
        title = next((t for t in texts if re.match(r"(19|20)\d\d [A-Z]", t)), "")
        if not head or not title:
            continue
        lane_run, _, location = head.partition("•")
        info = {"vin": "", "stock": card["id"], "lot_url": carmax_lot_url(card["id"], page_url), "photo": _carmax_card_photo(card, page_url), "lane_run": squeeze(lane_run), "location": squeeze(location), "title": title}
        spec = next((t for t in texts if re.match(r"[\d,]+ mi\b", t)), "")
        info["miles"] = spec.split(" mi")[0].replace(",", "") if spec else ""
        info["drive"] = " • ".join(spec.split(" • ")[1:]) if spec else ""
        after = texts[texts.index(spec) + 1:] if spec else []
        info["announcements"] = next((t for t in after if not t.startswith("$") and t != "No announcement(s)"), "")
        info["notes"], info["status"] = "", ""
        started = card.find(string=re.compile(r"Started on|Starts on|Starts"))
        info["start"] = squeeze(str(started)).replace("Started on ", "").replace("Starts on ", "") if started else ""
        bid = next((t for t in after if re.fullmatch(r"\$[\d,]+", t)), "")
        info["your_bid"] = bid if card.find(string=re.compile("Your bid")) else ""
        cards.append(info)
    return cards


def row_from_carmax_card(row: dict[str, str], card: dict[str, str]) -> list[str]:
    """Заполняет строку из карточки watch-листа CarMax. Возвращает заметки для «Проверить»."""
    notes: list[str] = []
    row["auction"] = "CarMax"
    row["vin"] = card["vin"]
    row["lot_number"] = card.get("lane_run", "")
    row["location"] = card.get("location", "")
    row["lot_url"] = card.get("lot_url", "")
    row["photo_main_url"] = card.get("photo", "")
    match = HEADER_RE.match(card.get("title", ""))
    if match:
        year, make, rest = match.groups()
        model, trim = split_model(rest)
        row.update(year=year, make=make, model=model, trim=trim)
    if card.get("miles", "").isdigit():
        row["odometer_miles"] = card["miles"]
    row["sale_date"] = card.get("start", "")
    announcements = card.get("announcements", "")
    row["defects"] = announcements
    row["auction_notes"] = announcements or "no announcements"   # замечания самого аукциона — в Notes на сайте
    row["condition_report"] = announcements
    row["history_page"] = announcements
    row["title_type"] = "; ".join(x for x in announcements.split(", ") if re.search(r"title|227", x, re.I))[:80] or "без замечаний по титулу (CarMax)"
    row["run_and_drive"] = "нет" if re.search(r"no[n]?[\s-]*runner", announcements, re.I) else ""

    user_notes = card.get("notes", "")
    row["site_notes"] = user_notes                    # ваша заметка в Notes на сайте — видна в окне целиком
    # TMU (True Mileage Unknown): CarMax пишет «Not actual miles»; бывает и в вашей заметке («TMU»).
    if re.search(r"\btmu\b|true mileage unknown|not actual mile|mileage unknown", f"{announcements} {user_notes}", re.I):
        row["odometer_brand"] = "TMU"
    if user_notes:
        row["carfax_autocheck"] = user_notes          # заметки покупателя: история, KBB, решения
        for key, pattern in (
            ("kbb_private_party_usd", _NOTE_KBB),
            ("mmr_adjusted_usd", _NOTE_MMR),
            ("my_proxy_usd", _NOTE_PROXY),
            ("retail_estimate_usd", _NOTE_SALE),
            ("auction_retail_usd", _NOTE_RETAIL),
        ):
            value = _last(pattern, user_notes)
            if value:
                row[key] = value
    extra = [x for x in (card.get("drive", ""), f"статус: {card['status']}" if card.get("status") else "") if x]
    if card.get("your_bid"):
        extra.append(f"ваша ставка: {card['your_bid']}")
    sold = _NOTE_SOLD.search(user_notes)
    if sold:
        extra.append(f"продана за ${sold.group(1)}")
    if user_notes:
        extra.append(f"заметки: {user_notes}")
    row["lot_description"] = "; ".join(extra)
    if NO_PHOTOS_RE.search(f"{user_notes} {announcements}") or not card.get("photo"):
        row["no_photos"] = "да"
        row["photo_count"] = "0"
    if card.get("status") == "Ended":
        notes.append("торги по лоту уже закончились")
    if not card["vin"]:
        notes.append("VIN нет: страница сохранена в виде «плитка» — VIN видны в виде «Detailed table»")
    return notes


def carmax_detail(html: str) -> dict[str, str] | None:
    """Страница лота CarMax (carmaxauctions.com/vehicledetail/<номер>) → те же поля, что у карточки списка."""
    head = html[:3000]
    found = re.search(r"carmaxauctions\.com/vehicledetail/(\d+)", head)
    if not found:
        return None
    stock = found.group(1)
    soup = BeautifulSoup(html, "lxml")
    title = squeeze(soup.title.get_text(" ", strip=True)) if soup.title else ""
    notes = soup.find("textarea")
    for junk in soup.select('[role="dialog"], [role="presentation"], header, footer, nav, script, style'):
        junk.decompose()
    text = " | ".join(t for t in (squeeze(x) for x in soup.body.stripped_strings) if t) if soup.body else ""
    pick = lambda pattern: (re.search(pattern, text) or [None, ""])[1].strip()
    vin = pick(r"\bVIN \| ([A-HJ-NPR-Z0-9]{17})\b") or pick(r"\b([A-HJ-NPR-Z0-9]{17})\b")
    announcements = pick(r"Announcements \| (.*?) \| Add a note")
    announcements = "" if announcements.startswith("No announcement") else ", ".join(x.strip() for x in announcements.split(" | ") if x.strip())
    lane, run = pick(r"Lane \| ([A-Z]{1,2})\b"), pick(r"Run Number \| (\d+)")
    drive = " • ".join(x for x in (pick(r"Drive Type \| ([^|]+)"), pick(r"Transmission \| ([^|]+)"), pick(r"Engine \| ([^|]+)")) if x)
    date, time_ = pick(r"\bDate \| (\d{1,2}/\d{1,2}/\d{4})"), pick(r"\bTime \| ([^|]+)")
    photo = next((img.get("src") for img in soup.find_all("img") if "img2.carmax.com/assets/" in (img.get("src") or "")), "") or carmax_photo_url(stock)
    return {"vin": vin, "stock": stock, "lot_url": carmax_lot_url(stock), "photo": photo,
            "lane_run": f"{lane}/{run}" if lane and run else "", "location": pick(r"Auction information \| ([^|]+)"),
            "title": title.split("|")[0].strip(), "miles": pick(r"\bMiles \| ([\d,]+)").replace(",", ""), "drive": drive,
            "announcements": announcements, "notes": squeeze(strip_own(notes.get_text())) if notes else "",
            "status": "", "start": f"{date} {time_}".strip(), "your_bid": "",
            "color": pick(r"Ext/Int Color \| ([^|/]+)")}


_VIN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")
_REMARK = re.compile(r"defect|damage|prior|title|227|rental|lease|fleet|airbag|frame|salvage|odometer|flood|runner|arbx|recall|smoke|noise", re.I)


def _carmax_by_vin(soup, page_url: str = "") -> list[dict[str, str]]:
    """Страница CarMax другого вида (список аукциона, таблица): карточка — самый большой блок вокруг VIN,
    в котором этот VIN один. Берём, что видно: машину, пробег, дорожку/номер, площадку, объявления."""
    cards: list[dict[str, str]] = []
    seen: set[str] = set()
    for text in soup.find_all(string=_VIN):
        vin = next((v for v in _VIN.findall(str(text)) if re.search(r"\d", v) and re.search(r"[A-Z]", v)), "")
        el = text.parent
        if not vin or vin in seen or el is None or el.find_parent(["script", "style", "textarea"]) or el.find_parent(attrs={"role": ["presentation", "dialog"]}):
            continue
        while el.parent is not None and el.parent.name not in ("body", "[document]"):
            inner = el.parent.get_text(" ", strip=True)
            if len(inner) > 5000 or any(v != vin for v in _VIN.findall(inner) if re.search(r"\d", v) and re.search(r"[A-Z]", v)):
                break
            el = el.parent
        seen.add(vin)
        texts = [squeeze(t) for t in el.stripped_strings if squeeze(t)]
        title = next((t for t in texts if re.match(r"(19|20)\d\d [A-Z][A-Za-z-]+ \S", t)), "")
        spec = next((t for t in texts if re.match(r"[\d,]+\s*mi\b", t)), "")
        head = next((t for t in texts if re.match(r"[A-Z]{1,2}/\d+\s*•", t)), "")
        lane_run, _, location = head.partition("•")
        if not lane_run:
            lane = next((m.group(1) for t in texts for m in [re.match(r"Lane\s*[:|]?\s*([A-Z]{1,2})$", t)] if m), "")
            run = next((m.group(1) for t in texts for m in [re.match(r"Run(?: Number| #)?\s*[:|]?\s*(\d+)$", t)] if m), "")
            lane_run = f"{lane}/{run}" if lane and run else ""
        link = el.find("a", href=re.compile(r"/vehicledetail/(\d+)"))
        stock = (re.search(r"/vehicledetail/(\d+)", link["href"]).group(1) if link else
                 next((x.get("data-vehicle-id") for x in el.find_all(attrs={"data-vehicle-id": True})), "") or "")
        cards.append({"vin": vin, "stock": stock, "lot_url": carmax_lot_url(stock), "photo": "" if (el.find(string=_CARMAX_NO_IMAGES) and not el.find("img")) else (_carmax_photo(el, page_url) or carmax_photo_url(stock)),
                      "lane_run": squeeze(lane_run), "location": squeeze(location), "title": title,
                      "miles": re.sub(r"\D", "", spec.split("mi")[0]) if spec else "", "drive": " • ".join(spec.split(" • ")[1:]) if spec else "",
                      "announcements": ", ".join(dict.fromkeys(t for t in texts if _REMARK.search(t) and len(t) < 120 and t != title)),
                      "notes": "", "status": "", "start": "", "your_bid": ""})
    return cards
