"""Определение аукциона и сборка строки таблицы из разобранной страницы.

Синонимы подписей общие для всех аукционов, а per-site словарь добавляет
названия, специфичные для конкретной площадки. Поэтому новый аукцион обычно
подключается добавлением нескольких синонимов, а не написанием нового парсера.
"""

from __future__ import annotations

import datetime as dt
import re

from . import acv, manheim, sites_text
from .extract import LotDocument
from .normalize import (
    clean_cell,
    clean_vin,
    is_placeholder,
    parse_date,
    parse_int,
    parse_money,
    parse_odometer,
    parse_year,
    squeeze,
    vin_check_digit_ok,
    vin_model_year,
)
from .schema import empty_row

# ---------------------------------------------------------------- аукционы

# Признаки площадки: домен в ссылках/тексте страницы -> название аукциона.
AUCTION_SIGNATURES: tuple[tuple[str, str], ...] = (
    ("copart.com", "Copart"),
    ("iaai.com", "IAAI"),
    ("iaa-auctions.com", "IAAI"),
    ("manheim.com", "Manheim"),
    ("acvauctions.com", "ACV"),
    ("openlane.com", "OPENLANE"),
    ("carmaxauctions.com", "CarMax"),
    ("carmax.com", "CarMax"),
)


def detect_auction(doc: LotDocument) -> str:
    """Определяет аукцион по домену в разметке, затем по названию в тексте."""
    haystack = doc.raw_html[:400_000].lower()
    for needle, name in AUCTION_SIGNATURES:
        if needle in haystack:
            return name
    text = doc.text.lower()
    for name in ("copart", "iaai", "manheim", "openlane", "carmax"):
        if name in text:
            return name.upper() if name in {"iaai"} else name.capitalize()
    if "acv auctions" in text:
        return "ACV"
    return ""


# ---------------------------------------------------------------- синонимы полей

# Общий словарь: подпись поля на странице (в любом регистре и написании).
FIELD_SYNONYMS: dict[str, tuple[str, ...]] = {
    "lot_number": (
        "lot number", "lot #", "lot no", "lotnumber", "lotnum", "lot", "stock number",
        "stock #", "item number", "auction item", "vehicle id", "lotdetaillotnumber",
    ),
    "vin": ("vin", "vin number", "vehicle identification number", "vinnumber", "fullvin", "serialnumber"),
    "year": ("year", "model year", "vehicleyear", "modelyear", "productiondate"),
    "make": ("make", "manufacturer", "brand", "vehiclemake", "makename"),
    "model": ("model", "model name", "vehiclemodel", "modelname", "modeldetail"),
    "trim": ("trim", "series", "model detail", "body style", "trimlevel", "vehicletrim", "modelgroup"),
    "odometer_miles": (
        "odometer", "mileage", "miles", "odometer reading", "odo", "odometerreading",
        "lotdetailodometerexact", "vehicleodometer", "mileageodometer",
    ),
    "title_type": (
        "title type", "title", "title code", "document type", "doc type", "titletype",
        "titledescription", "saledocument", "titlebrand", "titlestatus", "docdesc",
    ),
    "title_state": ("title state", "title st", "state", "titlestate", "sellingstate", "docstate"),
    "damage_primary": (
        "primary damage", "damage", "primarydamage", "damagedescription", "lossdescription",
        "primary damage description", "damagetype",
    ),
    "damage_secondary": ("secondary damage", "secondarydamage", "seconddamage", "otherdamage"),
    "keys_present": ("keys", "key", "keyspresent", "haskeys", "keyavailable"),
    "run_and_drive": (
        "run and drive", "runs and drives", "run & drive", "runanddrive", "startcode",
        "engine starts", "drivable", "runsdrives", "operable",
    ),
    "odometer_brand": (
        "odometer brand", "odometerbrand", "mileage brand", "odometer condition",
        "odometerdisclosure", "odometer status", "mileagebrand",
    ),
    "location": (
        "location", "sale location", "yard", "branch", "facility", "saleyard",
        "locationname", "auctionlocation", "city",
    ),
    "sale_date": (
        "sale date", "auction date", "sale time", "saledate", "auctiondate",
        "saledatetime", "lotsaledate", "runsaledate",
    ),
    "current_bid_usd": (
        "current bid", "high bid", "currentbid", "bid amount", "currentbidamount",
        "buynow", "buy it now", "highbid", "price", "offer",
    ),
    "acv_estimate_usd": (
        "estimated retail value", "actual cash value", "acv", "estretailvalue",
        "estimatedretailvalue", "estimated value", "retailvalue", "erv",
    ),
    "lot_description": (
        "description", "vehicle description", "lotdescription", "announcements",
        "seller announcements", "comments", "notes", "ogdescription",
    ),
    "photo_count": ("image count", "photo count", "imagecount", "numberofimages", "photos"),
    "lot_url": ("canonical", "ogurl", "url", "loturl", "vdpurl"),
    # Ниже — поля, которые обычно заполняются вручную, но если сохранённая
    # страница их содержит (например Adjusted MMR у Manheim), берём со страницы.
    "mmr_adjusted_usd": (
        "adjusted mmr", "adjustedmmr", "mmr adjusted", "manheim market report",
        "manheimmarketreport", "mmrvalue", "mmr",
    ),
    "condition_report": (
        "condition grade", "conditiongrade", "condition report", "conditionreport",
        "autograde", "cr grade", "crgrade", "gradescore",
    ),
    "cargurus_retail_usd": ("cargurus price", "cargurusprice", "instant market value", "imv"),
    "cargurus_deal_rating": ("deal rating", "dealrating", "cargurus rating"),
}

# Уточнения под конкретные площадки: добавляются в начало общего списка.
SITE_SYNONYMS: dict[str, dict[str, tuple[str, ...]]] = {
    "Copart": {
        "lot_number": ("lotdetaillotnumber", "lot number"),
        "odometer_miles": ("lotdetailodometerexact", "odometerreading", "odometer"),
        "acv_estimate_usd": ("estretailvalue", "estimated retail value"),
        "title_type": ("titledescription", "lotdetailtitledesc", "title code"),
        "run_and_drive": ("lotdetailhighlights", "highlights", "run and drive"),
    },
    "IAAI": {
        "lot_number": ("stock number", "stock #", "itemnumber"),
        "acv_estimate_usd": ("actual cash value", "acv"),
        "run_and_drive": ("run and drive verified", "startcode", "vehicle starts"),
        "damage_primary": ("primary damage", "loss"),
    },
    "Manheim": {
        "lot_number": ("vehicleid", "workorder", "lane and run", "laneandrun"),
        "current_bid_usd": ("currentbid", "highbid", "buynowprice"),
        "mmr_adjusted_usd": ("adjustedmmr", "mmr", "manheimmarketreport"),
        "condition_report": ("conditiongrade", "cr grade", "autograde", "conditionreport"),
    },
    "ACV": {
        "lot_number": ("vehicleid", "auctionid", "listingid"),
        "condition_report": ("conditionreport", "conditiongrade", "acvgrade"),
    },
    "OPENLANE": {
        "lot_number": ("vehicleid", "listingid", "stocknumber"),
        "condition_report": ("conditiongrade", "conditionreport"),
    },
    "CarMax": {
        "lot_number": ("stocknumber", "stock #", "vehicleid"),
    },
}

# Отметки в тексте страницы, которые считаем дефектами/особенностями лота.
DEFECT_MARKERS: tuple[tuple[str, str], ...] = (
    (r"\bairbag[s]?\s*(deployed|deployment)\b", "подушки сработали"),
    (r"\bflood\b|\bwater damage\b", "залив/вода"),
    (r"\bfire\s*damage\b|\bburn\b", "пожар"),
    (r"\bhail\b", "град"),
    (r"\bvandalism\b", "вандализм"),
    (r"\brollover\b", "переворот"),
    (r"\bmissing\s+(parts|engine|transmission|wheels)\b", "отсутствуют узлы"),
    (r"\bfrontend\b|\bfront end\b", "передняя часть"),
    (r"\brear end\b", "задняя часть"),
    (r"\bside\b.{0,12}\bdamage\b", "боковое повреждение"),
    (r"\bundercarriage\b", "днище"),
    (r"\bengine\s+damage\b", "двигатель"),
    (r"\bmechanical\b", "механика"),
    (r"\bframe\s*damage\b", "повреждение рамы"),
    (r"\bbiohazard\b", "биологическое загрязнение"),
    (r"\bnon[- ]?repairable\b|\bcert of destruction\b|\bparts only\b", "неремонтопригоден"),
    (r"\bsalvage\b", "salvage-титул"),
    (r"\brebuil[dt]\b", "rebuilt-титул"),
    (r"\blemon\b|\bmanufacturer buyback\b", "lemon/выкуп производителем"),
    (r"\bodometer\s+(discrepancy|rollback)\b|\btmu\b", "вопросы к пробегу"),
    (r"\btheft\s*recovery\b", "после угона"),
    # Типичные объявления дилерских аукционов (Manheim / ACV / OPENLANE / CarMax)
    (r"check engine light|\bcel\b|\bmil on\b", "горит check engine"),
    (r"title\s+(absent|delay|late|missing)|\bno title\b", "проблема с титулом"),
    (r"\btransmission\b.{0,20}\b(slip|issue|problem|fault)", "коробка передач"),
    (r"\ba[/\s]?c\b.{0,20}(inoperative|not work|issue)", "не работает кондиционер"),
    (r"\bstructural\s+damage\b|\bframe\s+repair\b", "силовая структура"),
    (r"\bas[- ]is\b|\bno arbitration\b", "продажа as-is"),
)


def _synonyms(field: str, auction: str) -> tuple[str, ...]:
    site = SITE_SYNONYMS.get(auction, {}).get(field, ())
    return tuple(site) + FIELD_SYNONYMS.get(field, ())


def _text_field(doc: LotDocument, field: str, auction: str, limit: int = 200) -> str:
    value, _ = doc.find(_synonyms(field, auction))
    return clean_cell(value, limit)


# ---------------------------------------------------------------- сборка строки


def parse_lot(html: str, source_name: str = "", auction_hint: str = "") -> dict[str, str]:
    """Разбирает HTML одного лота в строку итоговой таблицы.

    Возвращает словарь «ключ колонки -> строковое значение». Ненайденные поля
    остаются пустыми, а их список попадает в колонку «Проверить».
    """
    # Площадки с отдельным разбором: на сохранённой странице кроме лота есть
    # списки других машин и фильтры поиска, поэтому общий разбор получает
    # только карточку лота, а точные поля затем берутся из неё же.
    site_name, site_detail, site_apply = _site_detail(html)
    if site_detail is not None:
        html = site_detail.fragment_html
        auction_hint = auction_hint or site_name

    doc = LotDocument(html, source_name)
    auction = auction_hint or detect_auction(doc)

    row = empty_row()
    row["auction"] = auction
    row["source_file"] = source_name
    row["parsed_at"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    notes: list[str] = []

    # --- VIN: сначала подписанное поле, иначе поиск по тексту страницы ---
    vin_raw, _ = doc.find(_synonyms("vin", auction))
    vin = clean_vin(vin_raw)
    if not vin:
        for candidate in re.findall(r"\b[A-HJ-NPR-Z0-9]{17}\b", doc.text):
            if vin_check_digit_ok(candidate):
                vin = candidate
                notes.append("VIN взят из текста страницы, а не из поля")
                break
    row["vin"] = vin

    vin_ok = vin_check_digit_ok(vin) if vin else None
    if vin_ok is None:
        row["vin_valid"] = ""
    else:
        row["vin_valid"] = "да" if vin_ok else "нет"
        if not vin_ok:
            notes.append("контрольная цифра VIN не сходится")

    # --- лот, марка, модель, комплектация ---
    row["lot_number"] = _text_field(doc, "lot_number", auction, 40)
    row["make"] = _text_field(doc, "make", auction, 40)
    row["model"] = _text_field(doc, "model", auction, 60)
    row["trim"] = _text_field(doc, "trim", auction, 60)

    # --- год: поле, затем заголовок страницы, и сверка с VIN ---
    year = parse_year(_text_field(doc, "year", auction, 40))
    title_text = squeeze(doc.soup.title.get_text() if doc.soup.title else "")
    if year is None:
        year = parse_year(title_text)
    vin_year = vin_model_year(vin) if vin else None
    if year is None and vin_year is not None:
        year = vin_year
        notes.append("год восстановлен из VIN")
    elif year is not None and vin_year is not None and abs(year - vin_year) > 1:
        notes.append(f"год на странице ({year}) не совпадает с годом из VIN ({vin_year})")
    row["year"] = str(year) if year else ""

    # --- разбор заголовка «2019 TOYOTA CAMRY SE», если поля пустые ---
    if year and (not row["make"] or not row["model"]):
        heading = _heading_after_year(title_text, year) or _heading_after_year(doc.text[:400], year)
        if heading:
            words = heading.split()
            if not row["make"] and words:
                row["make"] = words[0]
                notes.append("марка взята из заголовка")
            if not row["model"] and len(words) > 1:
                row["model"] = words[1]
                notes.append("модель взята из заголовка")

    # --- пробег ---
    odo_raw, _ = doc.find(_synonyms("odometer_miles", auction))
    if not odo_raw:
        odo_raw = doc.search_text(r"(?:odometer|mileage)\D{0,12}([\d,\. ]{3,12}\s*(?:mi|miles|km)?)")
    miles, brand, converted = parse_odometer(odo_raw)
    if not brand:
        # У части аукционов отметка лежит в отдельном поле (odometerBrand).
        _, _brand_from_field, _ = parse_odometer(_text_field(doc, "odometer_brand", auction, 40))
        brand = _brand_from_field
    row["odometer_miles"] = str(miles) if miles is not None else ""
    row["odometer_brand"] = brand
    if converted:
        notes.append("пробег переведён из км в мили")
    if miles is not None and miles > 400_000:
        notes.append(f"подозрительно большой пробег: {miles}")

    # --- титул, повреждения, ключи, «на ходу» ---
    row["title_type"] = _text_field(doc, "title_type", auction, 80)
    state = _text_field(doc, "title_state", auction, 40)
    row["title_state"] = state if len(state) <= 30 else ""
    row["damage_primary"] = _text_field(doc, "damage_primary", auction, 80)
    if not row["title_state"]:
        # Аукционы часто пишут штат в самом типе титула: «TX NON-REPAIRABLE».
        prefix = re.match(r"([A-Z]{2})\s", row["title_type"])
        if prefix and prefix.group(1) in US_STATES:
            row["title_state"] = prefix.group(1)
    row["damage_secondary"] = _text_field(doc, "damage_secondary", auction, 80)
    keys_raw, _ = doc.find(_synonyms("keys_present", auction), allow_weak=True)
    row["keys_present"] = _normalize_keys(clean_cell(keys_raw, 40))
    row["run_and_drive"], run_drive_note = _run_and_drive(doc, auction)
    if run_drive_note:
        notes.append(run_drive_note)

    # --- локация, дата, деньги ---
    row["location"] = _text_field(doc, "location", auction, 60)
    row["sale_date"] = parse_date(_text_field(doc, "sale_date", auction, 60))
    row["current_bid_usd"] = _money_cell(doc, "current_bid_usd", auction)
    row["acv_estimate_usd"] = _money_cell(doc, "acv_estimate_usd", auction)
    # Обычно эти поля заполняются вручную, но если страница их содержит — берём.
    row["mmr_adjusted_usd"] = _money_cell(doc, "mmr_adjusted_usd", auction)
    row["cargurus_retail_usd"] = _money_cell(doc, "cargurus_retail_usd", auction)
    row["cargurus_deal_rating"] = _text_field(doc, "cargurus_deal_rating", auction, 30)
    row["condition_report"] = _text_field(doc, "condition_report", auction, 120)

    # --- описание и история со страницы ---
    row["lot_description"] = _text_field(doc, "lot_description", auction, 900)
    row["history_page"] = _history_summary(row)
    row["defects"] = _defect_summary(row, doc)

    # --- фото ---
    photos = doc.image_urls()
    row["photo_count"] = str(len(photos)) if photos else ""
    row["photo_urls"] = clean_cell(" ".join(photos), 1500)

    # --- ссылка на лот ---
    canonical = doc.soup.find("link", attrs={"rel": re.compile("canonical", re.I)})
    url = squeeze(canonical.get("href") if canonical else "") or _text_field(doc, "lot_url", auction, 300)
    row["lot_url"] = url if url.startswith("http") else ""

    # --- площадка с отдельным разбором: её поля точнее найденного общим разбором ---
    if site_detail is not None:
        notes = site_apply(row, site_detail) + _vin_notes(row)

    # --- что осталось проверить глазами ---
    missing = _missing_summary(row)
    if site_name == "ACV":
        # У онлайн-торгов ACV нет даты продажи — только таймер в карточке.
        missing = [m.replace("дата продажи, ", "").replace(", дата продажи", "") for m in missing if m != "не найдено: дата продажи"]
    row["needs_review"] = clean_cell("; ".join(notes + missing), 600)
    return row


def parse_page(html: str, source_name: str = "", auction_hint: str = "") -> list[dict[str, str]]:
    """Разбирает сохранённую страницу: одна строка на лот.

    Страница-список (watch list CarMax) даёт строку на каждую машину; если
    поверх списка открыта карточка одной из них, её подробности (повреждения,
    протектор) добавляются в строку этой машины. Иначе — одна строка, как parse_lot.
    """
    cards = sites_text.find_carmax_cards(html)
    if not cards:
        return [parse_lot(html, source_name=source_name, auction_hint=auction_hint)]

    opened = parse_lot(html, source_name=source_name, auction_hint=auction_hint)
    opened_vin = opened["vin"] if opened.get("auction") == "CarMax" else ""
    rows = []
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    for card in cards:
        if card["vin"] == opened_vin:
            row = dict(opened)
            card_row = empty_row()
            notes = sites_text.row_from_carmax_card(card_row, card)
            # Из списка — заметки покупателя, статус и объявления (они свежее).
            for key in ("carfax_autocheck", "my_proxy_usd", "retail_estimate_usd", "kbb_private_party_usd", "mmr_adjusted_usd", "auction_retail_usd", "lot_description"):
                if card_row.get(key):
                    row[key] = card_row[key]
            if card_row["defects"] and card_row["defects"] not in row["defects"]:
                row["defects"] = f"{card_row['defects']} | {row['defects']}"
                row["condition_report"] = f"{card_row['defects']} | {row['condition_report']}"
                row["history_page"] = card_row["history_page"]
            row["needs_review"] = clean_cell("; ".join([row["needs_review"]] + notes if row["needs_review"] else notes), 600)
        else:
            row = empty_row()
            row["source_file"], row["parsed_at"] = source_name, stamp
            notes = sites_text.row_from_carmax_card(row, card) + _vin_notes(row)
            notes.append("из списка: повреждения и протектор — в карточке лота")
            row["needs_review"] = clean_cell("; ".join(notes), 600)
        rows.append(row)
    return rows


# ---------------------------------------------------------------- частные помощники


def _site_detail(html: str):
    """(аукцион, карточка, функция заполнения строки) либо ("", None, None)."""
    head = html[:600_000].lower()
    if "acvauctions" in head:
        detail = acv.find_detail(html)
        if detail is not None:
            return "ACV", detail, acv.apply_detail
    if "manheim.com" in head or "coxautoinc.com" in head:
        detail = manheim.find_detail(html)
        if detail is not None:
            return "Manheim", detail, manheim.apply_detail
    if "adesa" in head:
        detail = sites_text.find_adesa(html)
        if detail is not None:
            return "ADESA", detail, sites_text.apply_detail
    if "carmax" in head:
        detail = sites_text.find_carmax(html)
        if detail is not None:
            return "CarMax", detail, sites_text.apply_detail
    return "", None, None


def _vin_notes(row: dict[str, str]) -> list[str]:
    """Проверки VIN и года — по уже заполненной строке."""
    notes = []
    vin = clean_vin(row.get("vin", ""))
    row["vin"] = vin
    ok = vin_check_digit_ok(vin) if vin else None
    row["vin_valid"] = "" if ok is None else ("да" if ok else "нет")
    if ok is False:
        notes.append("контрольная цифра VIN не сходится")
    year = parse_year(row.get("year", ""))
    vin_year = vin_model_year(vin) if vin else None
    if year and vin_year and abs(year - vin_year) > 1:
        notes.append(f"год на странице ({year}) не совпадает с годом из VIN ({vin_year})")
    return notes



def _heading_after_year(text: str, year: int) -> str:
    """Из «2019 TOYOTA CAMRY SE» достаёт «TOYOTA CAMRY SE»."""
    match = re.search(rf"\b{year}\b[\s-]+([A-Za-z][A-Za-z0-9\-/ ]{{2,40}})", text)
    return squeeze(match.group(1)) if match else ""


def _money_cell(doc: LotDocument, field: str, auction: str) -> str:
    """Денежное поле -> целое число без символа валюты (для расчётов в таблице)."""
    raw, _ = doc.find(_synonyms(field, auction))
    amount = parse_money(raw)
    if amount is None or amount <= 0:
        return ""
    return str(int(round(amount)))


def _normalize_keys(value: str) -> str:
    """'YES'/'Present'/'1' -> 'да'; 'NO'/'None' -> 'нет'.

    Слово «None» в поле ключей значит «ключей нет», а не «данных нет», поэтому
    содержательные ответы разбираются до проверки на заглушку.
    """
    if not value:
        return ""
    lowered = value.lower()
    if re.search(r"\byes\b|present|available|есть", lowered):
        return "да"
    if re.search(r"\bno\b|none|absent|unavailable|нет", lowered):
        return "нет"
    if is_placeholder(value):
        return ""
    count = parse_int(value)
    if count is not None:
        return "да" if count > 0 else "нет"
    return clean_cell(value, 20)


def _run_and_drive(doc: LotDocument, auction: str) -> tuple[str, str]:
    """Отметка «на ходу» -> (значение, замечание).

    Сначала читаем значение поля и только потом, если поля нет, ищем отметку в
    тексте страницы. Порядок важен: подпись «Run and Drive Verified» содержит
    искомые слова, и поиск по тексту дал бы «да» там, где в значении стоит «No».
    """
    raw, _ = doc.find(_synonyms("run_and_drive", auction), allow_weak=True)
    value = clean_cell(raw, 120)
    if value:
        lowered = value.lower()
        if re.search(r"\b(no|none|not)\b|non[- ]?runner|does not", lowered):
            return "нет", ""
        if re.search(r"\byes\b|verified|run\s*(and|&)?\s*drive|runs\b|start[s]?\b|drivable", lowered):
            return "да", ""
        if re.search(r"engine start[s]? program|stationary", lowered):
            return "запускается", ""
        return clean_cell(value, 30), ""

    text = doc.text[:40000].lower()
    if re.search(r"does not (run|start)|non[- ]?runner|stationary", text):
        return "нет", "отметка «на ходу» взята из текста страницы"
    if re.search(r"run\s*(and|&)?\s*drive|runs\s*(and|&)?\s*drives", text):
        return "да", "отметка «на ходу» взята из текста страницы, а не из поля — проверьте"
    return "", ""


def _history_summary(row: dict[str, str]) -> str:
    """Собирает историю из того, что есть на самой странице лота."""
    parts: list[str] = []
    if row["title_type"]:
        title = row["title_type"]
        if row["title_state"]:
            title = f"{title} ({row['title_state']})"
        parts.append(f"титул: {title}")
    if row["odometer_brand"]:
        parts.append(f"пробег: {row['odometer_brand']}")
    if row["damage_primary"]:
        parts.append(f"повреждение: {row['damage_primary']}")
    if row["damage_secondary"]:
        parts.append(f"вторичное: {row['damage_secondary']}")
    if row["sale_date"]:
        parts.append(f"продажа: {row['sale_date']}")
    return clean_cell("; ".join(parts), 400)


def _defect_summary(row: dict[str, str], doc: LotDocument) -> str:
    """Сводка дефектов: поля повреждений + отметки-маркеры из текста страницы."""
    found: list[str] = []
    for field in ("damage_primary", "damage_secondary"):
        value = row[field]
        if value and value.lower() not in {f.lower() for f in found}:
            found.append(value)
    haystack = f"{row['lot_description']} {doc.text[:40000]}".lower()
    for pattern, label in DEFECT_MARKERS:
        if re.search(pattern, haystack) and label not in found:
            found.append(label)
    return clean_cell("; ".join(found), 400)


# Поля, без которых строку нельзя считать готовой к решению о ставке.
CRITICAL_FIELDS: tuple[tuple[str, str], ...] = (
    ("vin", "VIN"),
    ("lot_number", "номер лота"),
    ("year", "год"),
    ("make", "марка"),
    ("model", "модель"),
    ("odometer_miles", "пробег"),
    ("title_type", "тип титула"),
    ("location", "локация"),
    ("sale_date", "дата продажи"),
)

# Страховые аукционы всегда публикуют повреждения и наличие ключей.
# Дилерские (Manheim / ACV / OPENLANE / CarMax) вместо этого дают Condition Report,
# поэтому требовать от них поле «повреждение» — значит плодить ложные замечания.
SALVAGE_AUCTIONS = {"Copart", "IAAI"}
SALVAGE_CRITICAL: tuple[tuple[str, str], ...] = (
    ("damage_primary", "повреждение"),
    ("keys_present", "ключи"),
)
DEALER_CRITICAL: tuple[tuple[str, str], ...] = (
    ("condition_report", "condition report"),
)

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL",
    "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE",
    "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD",
    "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
}


def _missing_summary(row: dict[str, str]) -> list[str]:
    checks = list(CRITICAL_FIELDS)
    auction = row.get("auction", "")
    checks += list(SALVAGE_CRITICAL if auction in SALVAGE_AUCTIONS else DEALER_CRITICAL)
    missing = [label for key, label in checks if not row.get(key)]
    return [f"не найдено: {', '.join(missing)}"] if missing else []
