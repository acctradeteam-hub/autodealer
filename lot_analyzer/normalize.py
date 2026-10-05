"""Нормализация значений, вытащенных со страницы лота.

Всё, что можно посчитать формулой (контрольная цифра VIN, год из VIN,
перевод километров в мили), считается кодом, а не «на глаз».
"""

from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------- VIN

# Буквы I, O, Q в VIN не используются, поэтому в них частая ошибка распознавания.
VIN_RE = re.compile(r"\b([A-HJ-NPR-Z0-9]{17})\b")

_VIN_TRANSLIT = {
    **{str(d): d for d in range(10)},
    "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
    "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9,
    "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9,
}
_VIN_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)

# 30-летний цикл кодов модельного года (позиция 10 VIN).
_YEAR_CODES = "ABCDEFGHJKLMNPRSTVWXY123456789"


def clean_vin(value: str | None) -> str:
    """Приводит VIN к верхнему регистру без разделителей. Пустая строка, если не похоже на VIN."""
    if not value:
        return ""
    candidate = re.sub(r"[^A-Za-z0-9]", "", str(value)).upper()
    if len(candidate) != 17:
        match = VIN_RE.search(candidate)
        if not match:
            return ""
        candidate = match.group(1)
    return candidate if VIN_RE.fullmatch(candidate) else ""


def vin_check_digit_ok(vin: str) -> bool | None:
    """Проверяет контрольную цифру VIN по стандарту ISO 3779 (позиция 9).

    None — если VIN не 17 знаков и проверять нечего.
    """
    vin = clean_vin(vin)
    if len(vin) != 17:
        return None
    try:
        total = sum(_VIN_TRANSLIT[ch] * w for ch, w in zip(vin, _VIN_WEIGHTS))
    except KeyError:
        return False
    remainder = total % 11
    expected = "X" if remainder == 10 else str(remainder)
    return vin[8] == expected


def vin_model_year(vin: str) -> int | None:
    """Модельный год из 10-й позиции VIN.

    Код года неоднозначен (30-летний цикл), поэтому эпоха определяется по 7-й
    позиции: буква — 2010 год и позже, цифра — до 2010 года.
    """
    vin = clean_vin(vin)
    if len(vin) != 17:
        return None
    code = vin[9]
    if code not in _YEAR_CODES:
        return None
    offset = _YEAR_CODES.index(code)
    base = 2010 if vin[6].isalpha() else 1980
    return base + offset


# ---------------------------------------------------------------- числа и деньги

_MONEY_RE = re.compile(r"-?\d[\d\s,.']*")


def parse_money(value: str | None) -> float | None:
    """Достаёт денежную сумму: '$12,345.00', 'USD 12 345', '12.345' -> число."""
    if value is None:
        return None
    text = str(value)
    if not text.strip():
        return None
    match = _MONEY_RE.search(text.replace(" ", " "))
    if not match:
        return None
    raw = match.group(0).strip()
    # Разделители тысяч убираем, десятичную точку оставляем.
    raw = raw.replace(" ", "").replace("'", "")
    if "," in raw and "." in raw:
        raw = raw.replace(",", "") if raw.rfind(".") > raw.rfind(",") else raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        # «1,234» — тысячи; «1,23» — десятичная запятая.
        raw = raw.replace(",", "") if re.search(r",\d{3}(?!\d)", raw) else raw.replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def parse_int(value: str | None) -> int | None:
    """Целое число из текста: '123,456 mi' -> 123456."""
    number = parse_money(value)
    return None if number is None else int(round(number))


def parse_year(value: str | None) -> int | None:
    """Год выпуска: ищем 4 цифры в разумном диапазоне."""
    if value is None:
        return None
    for match in re.finditer(r"\b(19[5-9]\d|20[0-4]\d)\b", str(value)):
        return int(match.group(1))
    return None


MILES_PER_KM = 0.621371


def parse_odometer(value: str | None) -> tuple[int | None, str, bool]:
    """Пробег -> (мили, отметка о достоверности, был_ли_перевод_из_км).

    Отметку берём из текста: ACTUAL / EXEMPT / NOT ACTUAL / TMU.
    """
    if value is None:
        return None, "", False
    text = str(value)
    lowered = text.lower()

    brand = ""
    if re.search(r"\bnot[\s-]*actual\b|\bnaa\b", lowered):
        brand = "Not Actual"
    elif re.search(r"\btmu\b|true mileage unknown|\bmileage unknown\b", lowered):
        brand = "TMU"
    elif re.search(r"\bexempt\b", lowered):
        brand = "Exempt"
    elif re.search(r"\bactual\b", lowered):
        brand = "Actual"

    number = parse_int(text)
    converted = False
    if number is not None and re.search(r"\bkm\b|kilometer|километ", lowered):
        number = int(round(number * MILES_PER_KM))
        converted = True
    return number, brand, converted


# ---------------------------------------------------------------- да/нет, даты, текст

_YES = re.compile(r"^\s*(yes|y|true|present|available|да|есть|1)\s*$", re.I)
_NO = re.compile(r"^\s*(no|n|false|absent|none|нет|0)\s*$", re.I)


def parse_yes_no(value: str | None) -> str:
    """'YES'/'NO'/'Present' -> 'да'/'нет'. Непонятное возвращаем как есть."""
    if value is None:
        return ""
    text = squeeze(str(value))
    if not text:
        return ""
    if _YES.match(text):
        return "да"
    if _NO.match(text):
        return "нет"
    return text


def parse_date(value: str | None) -> str:
    """Дата продажи -> ISO YYYY-MM-DD. Если разобрать не удалось — исходный текст."""
    if value is None:
        return ""
    text = squeeze(str(value))
    if not text:
        return ""
    iso = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if iso:
        return iso.group(0)
    try:
        import warnings

        from dateutil import parser as date_parser  # поставляется вместе с pandas

        with warnings.catch_warnings():
            # Аукционы пишут время в PST/EST; зона нам не нужна, нужна дата.
            warnings.simplefilter("ignore")
            # fuzzy разбирает «Tue Nov 12, 2026 9:00 AM PST»; dayfirst=False — формат США.
            parsed = date_parser.parse(text, fuzzy=True, dayfirst=False)
        return parsed.date().isoformat()
    except Exception:
        return text


def squeeze(value: str | None) -> str:
    """Схлопывает пробелы и неразрывные пробелы, убирает служебные символы."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = text.replace("​", "").replace("\r", " ")
    return re.sub(r"\s+", " ", text).strip()


def clean_cell(value: str | None, limit: int = 1000) -> str:
    """Готовит текст для клетки TSV: без табов и переводов строк, с ограничением длины."""
    text = squeeze(value)
    text = text.replace("\t", " ")
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


_PLACEHOLDERS = {
    "", "-", "--", "—", "n/a", "na", "n\\a", "none", "null", "undefined",
    "not available", "not applicable", "unknown", "tbd", "&nbsp;",
}


def is_placeholder(value: str | None) -> bool:
    """True для значений-заглушек, которые на странице означают «данных нет»."""
    return squeeze(value).lower().strip(" .:") in _PLACEHOLDERS


# Модели из двух-трёх слов: «Tesla Model X 75D» — модель «Model X», а не «Model» + трим «X 75D».
_MULTIWORD_MODEL = re.compile(
    r"^(model [3sxy]|grand (cherokee|caravan|marquis|vitara)|range rover( (sport|evoque|velar))?|santa (fe|cruz)|town & country|"
    r"monte carlo|crown victoria|land cruiser|mustang mach-e|bolt euv|prius (prime|plug-in( hybrid)?|plug in hybrid|c|v)|"
    r"rav4 (prime|hybrid)|cr-v hybrid|camry hybrid|accord hybrid|niro ev|kona electric|ioniq (5|6|electric)|id\.4|e-tron( gt)?|"
    r"f-150 lightning|silverado ev|sierra ev)\b", re.I)


def split_model(rest: str) -> tuple[str, str]:
    """«Model X 75D» → («Model X», «75D»); «Civic LX» → («Civic», «LX»)."""
    rest = squeeze(rest)
    found = _MULTIWORD_MODEL.match(rest)
    if found:
        return rest[:found.end()], rest[found.end():].strip()
    model, _, trim = rest.partition(" ")
    return model, trim
