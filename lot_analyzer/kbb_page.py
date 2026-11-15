"""Страница kbb.com, которую вы открыли и сохранили сами (закладкой или Cmd+S).

В странице KBB есть встроенный JSON __NEXT_DATA__, а в нём блок valuations — все
значения для выбранной комплектации, ZIP и пробега: Private Party и Trade-In по
состояниям (Fair / Good / Very Good / Excellent), Auction, Typical Listing Price, FPP.
Программа ничего не скачивает с kbb.com — только читает сохранённый вами файл.

Какой машине лота подходит страница: тот же год, марка и модель, и пробег на KBB
близок к пробегу лота (config: kbb_page.max_miles_gap). Если на KBB пробег не указан
(0 — KBB считает «типичный пробег»), значение не подставляется: впишите пробег на KBB.
"""

from __future__ import annotations

import json
import re

NEXT_DATA_RE = re.compile(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
CONDITIONS = {"fair": "Fair", "good": "Good", "verygood": "Very Good", "excellent": "Excellent"}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def is_kbb(html: str) -> bool:
    """Страница kbb.com: по адресу, записанному при сохранении, или по встроенным данным KBB."""
    if "kbb.com/" in _saved_url(html):
        return True
    return "__NEXT_DATA__" in html and ("kbb.com" in html[:5000].lower() or "Kelley Blue Book" in html[:20000])


def parse(html: str) -> dict | None:
    """Значения KBB со страницы: {"year", "make", "model", "trim", "zip", "miles", "private_party": {…}, …}."""
    record = _parse_next_data(html) or parse_text(html)
    if record:
        # Закладка-автопилот записывает VIN лота, для которого запрошен KBB: так цена встаёт точно в свою строку.
        vin = re.search(r"lot-vin: ([A-HJ-NPR-Z0-9]{17})", html[:3000])
        record["vin"] = vin.group(1) if vin else ""
    return record


def _saved_url(html: str) -> str:
    match = re.search(r"<!-- saved-by: lot_analyzer bookmarklet;[^>]*?url: (\S+) -->", html[:3000]) or \
        re.search(r"<!-- saved from url=\(\d+\)(\S+) -->", html[:3000])
    return match.group(1) if match else ""


def parse_text(html: str) -> dict | None:
    """Итоговая страница оценки KBB («Your car's value»): цена — в видимом тексте и на шкале.

    Адрес страницы (его записывает закладка) говорит, что выбрано: pricetype=private-party
    («Sell it yourself») или trade-in («Trade or sell to dealer»), пробег, ZIP, состояние.
    Главную цифру KBB рисует на шкале (SVG) — закладка сохраняет её текст в span.svg-text.
    """
    from urllib.parse import parse_qs, urlparse
    from bs4 import BeautifulSoup

    url = _saved_url(html)
    parts = urlparse(url)
    path = [p for p in parts.path.split("/") if p]
    if "kbb.com" not in parts.netloc or len(path) < 4 or not path[2].isdigit():
        return None
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    start = text.find("Your car's value")
    if start < 0:
        start = text.find("Your car’s value")
    if start < 0:
        return None                                        # не итоговая страница (опции, цвет, выбор предложения)
    block = text[start:start + 1500]
    price_range = re.search(r"\$\s?([\d,]+)\s*[-–]\s*\$\s?([\d,]+)", block)
    gauge = " ".join(el.get_text(" ", strip=True) for el in soup.select(".svg-text"))
    # Только подписанная цифра шкалы; на странице есть и другие графики с «$» (обслуживание по годам).
    value = re.search(r"(?:Private Party|Trade-In|Trade In)[^$]{0,40}(?:Value)?[^$]{0,20}\$\s?([\d,]+)(?!\s*[-–])", gauge)
    miles = query.get("mileage") or (re.search(r"currently ([\d,]+)", block) or [None, ""])[1]
    zipcode = query.get("zipcode") or (re.search(r"ZIP Code:?[^0-9]{0,40}(\d{5})", block) or [None, ""])[1]
    condition = slug(query.get("condition") or (re.search(r"Condition\s+(Excellent|Very Good|Good|Fair)", block) or [None, "good"])[1]).replace("-", "")
    price_type = (query.get("pricetype") or "").lower()
    record = {"year": path[2], "make": path[0], "model": path[1], "trim": path[3] if len(path) > 3 else "",
              "zip": zipcode, "miles": int(re.sub(r"\D", "", miles) or 0), "private_party": {}, "trade_in": {},
              "auction": {}, "retail": None, "fpp": None, "range": None, "note": ""}
    if price_range:
        record["range"] = (int(price_range.group(1).replace(",", "")), int(price_range.group(2).replace(",", "")))
    amount = int(value.group(1).replace(",", "")) if value else None
    if amount is None and record["range"]:
        amount = round(sum(record["range"]) / 2)
        record["note"] = f"середина диапазона KBB ${record['range'][0]:,}–${record['range'][1]:,}"
    if amount is None:
        return None
    if "private" in price_type:
        record["private_party"][condition] = amount
    else:
        record["trade_in"][condition] = amount
        record["note"] = "открыта вкладка «Trade or sell to dealer» (Trade-In) — нажмите «Sell it yourself» и сохраните снова"
    return record


def _parse_next_data(html: str) -> dict | None:
    match = NEXT_DATA_RE.search(html)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
    except ValueError:
        return None
    root = ((data.get("props") or {}).get("apolloState") or {}).get("ROOT_QUERY") or {}
    found = None
    for key, value in root.items():
        if key.startswith("valuations(") and isinstance(value, dict) and value.get("prices"):
            try:
                params = json.loads(key[len("valuations("):-1])
            except ValueError:
                params = {}
            found = (params, value["prices"])
            if params.get("mileage"):                       # если вариантов несколько — с пробегом главнее
                break
    if not found:
        return None
    params, prices = found
    query = data.get("query") or {}
    record = {
        "year": str(params.get("year") or query.get("year") or ""),
        "make": params.get("make") or query.get("make") or "",
        "model": params.get("model") or query.get("model") or "",
        "trim": params.get("trim") or query.get("trim") or "",
        "zip": str(params.get("zipcode") or ""),
        "miles": int(params.get("mileage") or 0),
        "private_party": {}, "trade_in": {}, "auction": {}, "retail": None, "fpp": None,
    }
    by_type = {"Private Party": "private_party", "Trade-In": "trade_in", "Auction": "auction"}
    for price in prices:
        value = price.get("configuredValue")
        if value is None:
            continue
        kind = by_type.get(price.get("priceType"))
        condition = slug(price.get("condition") or "").replace("-", "")
        if kind and condition:
            record[kind][condition] = int(value)
        elif price.get("priceType") == "Retail":
            record["retail"] = int(value)
        elif price.get("priceType") == "FPP":
            record["fpp"] = int(value)
    return record if record["private_party"] else None


_BMW_CODE = re.compile(r"^(m?)([1-8])(\d{2})\s*(li|xi|ci|is|i|d|e)?\b", re.I)
_BMW_PLUG = re.compile(r"plug[- ]?in(?:\s+hybrid)?|hybrid", re.I)


def bmw_series(model: str, trim: str = "") -> tuple[list[str], str] | None:
    """BMW: на аукционах модель — номер («535» + трим «I», «M340» + «I», «330» + «Plug In Hybrid»), а на KBB модель —
    серия («5 Series»), номер — комплектация («535i xDrive Sedan 4D»). → (модель в адресе KBB — по очереди,
    трим для выбора комплектации) или None (X3, M5, Z4, i3 … — у KBB так же, как на аукционе)."""
    text = " ".join(f"{model or ''} {trim or ''}".split())
    found = _BMW_CODE.match(text)
    if not found:
        return None
    rest = text[found.end():]
    plug = bool(_BMW_PLUG.search(rest))
    rest = " ".join(_BMW_PLUG.sub(" ", rest).split())
    head = f"{found.group(1)}{found.group(2)}{found.group(3)}".lower()
    suffix = (found.group(4) or ("e" if plug else "i")).lower()     # «535 Base» — это 535i; plug-in — 330e, 530e
    code = head + suffix
    if suffix == "xi":
        code += f" {head}i xDrive"                    # до 2009 у KBB «328xi», позже — «328i xDrive»
    series = f"{found.group(2)}-series"
    if re.search(r"gran turismo|\bgt\b", rest, re.I):
        slugs = [f"{series}-gran-turismo", series]
    elif re.search(r"gran coupe", rest, re.I):
        slugs = [f"{series}-gran-coupe", series]
    else:
        slugs = [series]
    return slugs, f"{code} {rest}".strip()


# Mercedes: класс → модель на KBB (по ссылкам со страниц kbb.com). Варианты — по очереди; год решает, какой первым.
_MB_CLASS = {"a": ["a-class"], "b": ["b-class"], "c": ["c-class"], "cla": ["cla", "cla-class"], "cle": ["cle"], "clk": ["clk-class"],
             "cls": ["cls-class", "cls"], "e": ["e-class"], "g": ["g-class"], "gl": ["gl-class"], "gla": ["gla", "gla-class"],
             "glb": ["glb"], "glc": ["glc"], "gle": ["gle"], "glk": ["glk-class"], "gls": ["gls"], "m": ["m-class"], "ml": ["m-class"],
             "r": ["r-class"], "s": ["s-class"], "sl": ["sl-class", "sl"], "slc": ["slc", "slc-class"], "slk": ["slk-class"],
             "eqb": ["mercedes-eq-eqb"], "eqe": ["mercedes-eq-eqe"], "eqs": ["mercedes-eq-eqs"]}
_MB_COUPE = {"glc": "glc-coupe", "gle": "gle-coupe"}
_MB_SUV = {"eqe": "mercedes-eq-eqe-suv", "eqs": "mercedes-eq-eqs-suv"}
_MB_AMG_PAGE = {"a", "c", "cla", "cls", "e", "g", "gla", "glb", "glc", "gle", "gls", "s", "sl", "slc", "eqe", "eqs"}
_MB_CODE = re.compile(r"(?<![a-z0-9])(cla|cle|clk|cls|gla|glb|glc|gle|glk|gls|slc|slk|eqb|eqe|eqs|gl|ml|sl|a|b|c|e|g|m|r|s)"
                      r"\s*-?\s*(\d{2,3})(?!\d)(e|d|h)?(\+)?(?![a-z])", re.I)


def mercedes_class(model: str, trim: str = "", year: str = "") -> tuple[list[str], str] | None:
    """Mercedes: на аукционах модель — «C300» + «Base», «GLE53» + «AMG Coupe Base», у Manheim «B C300W C 300»; на KBB
    модель — класс («c-class», «gle», «mercedes-amg-gle-coupe»), а «C 300» — комплектация. → (модели KBB по очереди,
    трим для выбора комплектации) или None (Sprinter, Metris, B-Class, AMG GT … — как раньше)."""
    text = " ".join(f"{model or ''} {trim or ''}".split())
    if re.search(r"maybach|sprinter|metris", text, re.I):
        return None
    if re.match(r"amg\s+gt\b", text, re.I):
        return ["mercedes-amg-gt"], text[4:]
    found = _MB_CODE.search(text)
    if not found:
        return None
    fam, num = found.group(1).lower(), found.group(2)
    rest = " ".join(text[found.end():].split())     # у Manheim слова до номера («B C300W») — служебные, не нужны
    rest = " ".join(re.sub(r"plug[- ]?in(?:\s+hybrid)?", " ", rest, flags=re.I).split())
    try:
        y = int(str(year)[:4])
    except ValueError:
        y = 0
    amg = len(num) == 2 or bool(re.search(r"\bamg\b", text, re.I))
    base = list(_MB_CLASS[fam])
    if fam == "cla" and 0 < y <= 2015:
        base.reverse()                                   # CLA 2014–2015 у KBB — «cla-class»
    if fam == "cls" and y >= 2019:
        base.reverse()
    if fam in _MB_COUPE and re.search(r"\bcoupe\b", rest, re.I):
        base = [_MB_COUPE[fam]] + base
    if fam in _MB_SUV and re.search(r"\bsuv\b", rest, re.I):
        base = [_MB_SUV[fam]] + base
    slugs = base
    if amg and fam in _MB_AMG_PAGE:
        pages = [f"mercedes-amg-{b}" for b in base if not b.startswith("mercedes-")]
        if fam in ("eqe", "eqs"):
            pages = [f"mercedes-amg-{fam}"]
        slugs = (pages + base) if y >= 2016 or not y else (base + pages)   # раньше 2016 AMG — в самом классе
    label = {"ml": "ML", "m": "ML"}.get(fam, fam.upper())
    code = f"{label} {num}{(found.group(3) or '').lower()}{found.group(4) or ''}"
    if amg and not re.search(r"\bamg\b", rest, re.I):
        code += " AMG"
    return list(dict.fromkeys(slugs)), f"{code} {rest}".strip()


def kbb_model(make: str, model: str, trim: str = "", year: str = "") -> tuple[list[str], str] | None:
    """Марки, у которых на KBB модель — серия / класс, а номер — комплектация: BMW («535» → 5-series), Mercedes («C300» → c-class)."""
    make_s = slug(make)
    if make_s == "bmw":
        return bmw_series(model, trim)
    if make_s == "mercedes-benz" or make_s == "mercedes":
        return mercedes_class(model, trim, year)
    return None


def matches(record: dict, row: dict[str, str], max_gap: int) -> bool:
    """Подходит ли страница KBB машине лота: тот же VIN, либо год, марка, модель и пробег рядом."""
    if record.get("vin"):
        return record["vin"] == row.get("vin")
    if str(row.get("year", "")) != record["year"] or slug(row.get("make", "")) != record["make"]:
        return False
    page_model = record["model"]
    lot_models = [slug(row.get("model", ""))]
    mapped = kbb_model(record["make"], row.get("model", ""), row.get("trim", ""), str(row.get("year", "")))
    if mapped:
        lot_models += mapped[0]                       # «535» на аукционе — «5-series» на KBB, «C300» — «c-class»
    if not any(lot_model == page_model or lot_model.replace("-", "") == page_model.replace("-", "")
               or page_model.startswith(lot_model + "-") or lot_model.startswith(page_model + "-") for lot_model in lot_models):
        return False
    miles = re.sub(r"\D", "", str(row.get("odometer_miles", "")))
    if not record["miles"] or not miles:
        return False
    return abs(int(miles) - record["miles"]) <= max_gap


def describe(record: dict) -> str:
    text = f"{record['trim']}, {record['miles']:,} миль, ZIP {record['zip']}"
    return text + (f" ({record['note']})" if record.get("note") else "")
