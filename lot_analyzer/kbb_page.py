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
    return "__NEXT_DATA__" in html and ("kbb.com" in html[:5000].lower() or "Kelley Blue Book" in html[:20000])


def parse(html: str) -> dict | None:
    """Значения KBB со страницы: {"year", "make", "model", "trim", "zip", "miles", "private_party": {…}, …}."""
    record = _parse_next_data(html)
    return record if record else parse_text(html)


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
    value = re.search(r"(?:Value|Price)[^$]{0,60}\$\s?([\d,]+)", gauge) or re.search(r"\$\s?([\d,]+)(?!\s*[-–])", gauge)
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
        record["note"] = "середина диапазона KBB — центральной цифры в файле нет"
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


def matches(record: dict, row: dict[str, str], max_gap: int) -> bool:
    """Подходит ли страница KBB машине лота: год, марка, модель и пробег рядом."""
    if str(row.get("year", "")) != record["year"] or slug(row.get("make", "")) != record["make"]:
        return False
    lot_model, kbb_model = slug(row.get("model", "")), record["model"]
    if not (lot_model == kbb_model or lot_model.replace("-", "") == kbb_model.replace("-", "")
            or kbb_model.startswith(lot_model + "-") or lot_model.startswith(kbb_model + "-")):
        return False
    miles = re.sub(r"\D", "", str(row.get("odometer_miles", "")))
    if not record["miles"] or not miles:
        return False
    return abs(int(miles) - record["miles"]) <= max_gap


def describe(record: dict) -> str:
    text = f"{record['trim']}, {record['miles']:,} миль, ZIP {record['zip']}"
    return text + (f" ({record['note']})" if record.get("note") else "")
