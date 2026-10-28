"""Итоги торгов: за сколько машина продалась — в окно и в свою историю для анализа.

Файлы из «Загрузок», которые окно подхватывает само:
  * CSV итогов дорожки Simulcast (Run #, Year/Make/Model, VIN, CR, Odometer, MMR Avg, Outcome, Sale Price) —
    площадка по коду в имени файла (manheim_scaa_lane03_… → SCAA);
  * PDF «Manheim – Post-Sale Results – Vehicle Listing» (год, марка, модель, пробег, цена; код и дата в шапке).

Всё прочитанное копится в data/auction_results.csv (не пропадает через сутки, как файлы в «Загрузках»).
Машина лота находится по VIN, а если его нет (PDF) — по году, марке и точному пробегу.
По накопленной истории считается «цена продажи ÷ MMR» отдельно для каждой площадки:
окно использует это как «рынок» этой площадки, когда продаж набирается достаточно.
"""

from __future__ import annotations

import csv
import datetime as dt
import re
import statistics
from pathlib import Path
from .paths import DATA_DIR

HISTORY_PATH = DATA_DIR / "auction_results.csv"
# remarks — замечания самого аукциона (Major engine defect, Prior rental …), kbb — KBB Private Party, известный до торгов.
FIELDS = ("key", "date", "auction", "code", "lot", "vin", "year", "make", "model", "miles", "cr", "mmr", "outcome", "price", "source", "remarks", "kbb")
# Коды площадок Manheim → название, как оно стоит в списках («CA - Manheim California»).
CODES = {"CADE": "Manheim California", "SCAA": "Manheim Southern California", "RAA": "Manheim Riverside",
         "SDAA": "Manheim San Diego", "SFAA": "Manheim San Francisco Bay", "NVAA": "Manheim Nevada",
         "FRES": "Manheim Fresno", "PHXA": "Manheim Phoenix"}
BANDS = (5000, 10000, 20000, 1e9)
LANE_HEADER = ("Run #", "Year/Make/Model", "VIN", "Outcome", "Sale Price")


def _num(text) -> float | None:
    text = re.sub(r"[$,\s]", "", str(text or ""))
    try:
        return float(text) if text else None
    except ValueError:
        return None


def is_lane_csv(path: Path) -> bool:
    if path.suffix.lower() != ".csv":
        return False
    try:
        head = path.open(encoding="utf-8-sig", errors="replace").readline()
    except OSError:
        return False
    return all(h in head for h in LANE_HEADER)


def is_postsale_pdf(path: Path) -> bool:
    return path.suffix.lower() == ".pdf" and re.search(r"post.?sale", path.name, re.I) is not None


def _key(rec: dict) -> str:
    who = rec["vin"] or f"{rec['year']}|{rec['make']}|{rec['miles']}"
    return f"{rec['code'] or rec['auction']}|{rec['date']}|{who}"


def read_lane_csv(path: Path) -> list[dict]:
    code = (re.search(r"manheim[_-]([a-z]{3,4})[_-]", path.name, re.I) or [None, ""])[1].upper()
    date = dt.date.fromtimestamp(path.stat().st_mtime).isoformat()
    out = []
    for r in csv.DictReader(path.open(encoding="utf-8-sig")):
        ymm = (r.get("Year/Make/Model") or "").split()
        outcome = (r.get("Outcome") or "").strip() or "нет итога"
        rec = {"date": date, "auction": CODES.get(code, code), "code": code, "lot": r.get("Run #", ""),
               "vin": (r.get("VIN") or "").strip().upper(), "year": ymm[0] if ymm else "", "make": ymm[1] if len(ymm) > 1 else "",
               "model": " ".join(ymm[2:]), "miles": str(int(_num(r.get("Odometer")) or 0)), "cr": r.get("CR", ""),
               "mmr": str(int(_num(r.get("MMR Avg")) or 0) or ""), "outcome": outcome,
               "price": str(int(_num(r.get("Sale Price")) or 0) or ""), "source": path.name, "remarks": "", "kbb": ""}
        rec["key"] = _key(rec)
        out.append(rec)
    return out


# Выгрузка Manheim «все линии»: Lane, Lane Name, Lot, Year/Make/Model, VIN, …, Adj MMR, Result («Sold $7,000» / «IF Sale $2,500» / «No Sale»).
LANES_HEADER = ("Lane Name", "Lot", "Year/Make/Model", "VIN", "Adj MMR", "Result")


def is_manheim_lanes_csv(path: Path) -> bool:
    if path.suffix.lower() != ".csv":
        return False
    try:
        with path.open(encoding="utf-8-sig", errors="replace") as handle:
            head = handle.readline()
    except OSError:
        return False
    return all(h in head for h in LANES_HEADER)


def read_manheim_lanes_csv(path: Path) -> list[dict]:
    """Итоги Manheim по всем линиям. Площадка — из имени файла («Manheim_California_…»), дата — из имени или дата файла.
    «IF Sale $X» — не продана: последняя ставка X не дотянула до резерва продавца (outcome «If Sale», price — эта ставка)."""
    stem = path.stem.replace("_", " ")
    place = next((name for name in sorted(CODES.values(), key=len, reverse=True) if name.lower() in stem.lower()), "")
    code = next((c for c, name in CODES.items() if name == place), "")
    found = re.search(r"(20\d{2})-(\d{2})-(\d{2})|(\d{1,2})[^\d]?(\d{1,2})[^\d]?(20\d{2})(?!\d)", path.stem)
    if found and found.group(1):
        date = f"{found.group(1)}-{found.group(2)}-{found.group(3)}"
    elif found:
        date = f"{found.group(6)}-{int(found.group(4)):02d}-{int(found.group(5)):02d}"
    else:
        date = dt.date.fromtimestamp(path.stat().st_mtime).isoformat()
    out = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for r in csv.DictReader(handle):
            ymm = (r.get("Year/Make/Model") or "").split()
            result = (r.get("Result") or "").strip()
            kind = re.match(r"(sold|if sale|no sale)", result, re.I)
            outcome = {"sold": "Sold", "if sale": "If Sale", "no sale": "No Sale"}.get(kind.group(1).lower(), result) if kind else "нет итога"
            miles = _num(r.get("Odometer"))
            rec = {"date": date, "auction": place or "Manheim", "code": code, "lot": (r.get("Lot") or "").strip(),
                   "vin": (r.get("VIN") or "").strip().upper(), "year": ymm[0] if ymm else "", "make": ymm[1].title() if len(ymm) > 1 else "",
                   "model": " ".join(ymm[2:]).title(), "miles": str(int(miles)) if miles and miles > 10 else "", "cr": (r.get("CR") or "").strip(),
                   "mmr": str(int(_num(r.get("Adj MMR")) or 0) or ""), "outcome": outcome,
                   "price": str(int(_num(result.split("$")[-1]) or 0) or "") if "$" in result else "", "source": path.name,
                   "remarks": (r.get("Lane Name") or "").strip(), "kbb": ""}
            rec["key"] = _key(rec)
            out.append(rec)
    return out


def read_postsale_pdf(path: Path) -> list[dict]:
    from pypdf import PdfReader

    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    head = re.search(r"\b([A-Z]{3,4})\s+(\d{2})/(\d{2})/(\d{4})\s+Sales Results", text)
    code = head.group(1) if head else ""
    date = f"{head.group(4)}-{head.group(2)}-{head.group(3)}" if head else ""
    name = (re.search(r"/ / (Manheim [A-Za-z .]+)\n", text) or [None, CODES.get(code, code)])[1].strip()
    out = []
    for line in text.splitlines():
        m = re.match(r"(\d{4}) ([A-Z][A-Z-]+) (.+?) ([\d,]+) \$([\d,]+)\s*$", line.strip())
        if m:
            rec = {"date": date, "auction": name, "code": code, "lot": "", "vin": "", "year": m[1], "make": m[2],
                   "model": m[3], "miles": m[4].replace(",", ""), "cr": "", "mmr": "", "outcome": "Sold",
                   "price": m[5].replace(",", ""), "source": path.name, "remarks": "", "kbb": ""}
            rec["key"] = _key(rec)
            out.append(rec)
    return out


def is_carmax_results(path: Path) -> bool:
    """Итоги торгов CarMax: выгрузка Velocicast (JSON / CSV), «all lanes» CSV, PDF своего списка с итогами."""
    suffix = path.suffix.lower()
    name = path.name.lower()
    if suffix == ".pdf":
        return "carmax" in name and ("result" in name or "list" in name)
    if suffix not in (".json", ".csv"):
        return False
    try:
        head = path.open(encoding="utf-8-sig", errors="replace").read(2000)
    except OSError:
        return False
    if suffix == ".json":
        return '"vehicles"' in head and ("velocicast" in head.lower() or "final_status" in head or "carmax" in head.lower())
    return (head.startswith("auction_location,") or "final_amount" in head[:600] or "velocicast" in head.lower()
            or _is_carmax_table(head))


_RUNLIST_CACHE: dict = {}


def is_runlist_pdf(path: Path) -> bool:
    """PDF-список CarMax к торгам (печать watch list): машины без единого итога. Это список машин, а не итоги."""
    if path.suffix.lower() != ".pdf" or not is_carmax_results(path):
        return False
    try:
        key = (str(path), path.stat().st_mtime)
    except OSError:
        return False
    if key not in _RUNLIST_CACHE:
        try:
            recs = read_carmax(path)
        except Exception:  # битый PDF
            recs = []
        _RUNLIST_CACHE[key] = bool(recs) and all(r["outcome"] == "нет итога" for r in recs)
    return _RUNLIST_CACHE[key]


def is_results_file(path: Path) -> bool:
    return (is_lane_csv(path) or is_manheim_lanes_csv(path) or is_carmax_site_csv(path) or is_velocicast_page(path) or is_velocicast_export(path) or is_carmax_ru_table(path) or is_carmax_sale_results(path)
            or is_postsale_pdf(path) or is_carmax_results(path))


CARMAX_TABLE_HEAD = "Location,Lane,Lot #,Year/Make/Model,VIN"
# Колонки, по которым узнаём таблицу итогов CarMax, в любом порядке (CarMax добавляет новые, например «Date»).
CARMAX_TABLE_COLUMNS = {"Location", "Lane", "Lot #", "Year/Make/Model", "VIN", "Status", "Sale Price (USD)"}


# Ещё одна выгрузка CarMax: «Auction_Site, Lane, Lot, Year, Make, Model, Mileage, Status, Price, VIN».
CARMAX_SITE_COLUMNS = {"Auction_Site", "Lane", "Lot", "Year", "Make", "Model", "Status", "Price", "VIN"}


def _header(head: str) -> set[str]:
    first = head.lstrip("\ufeff").splitlines()[0] if head.strip() else ""
    return {c.strip().strip('"') for c in next(csv.reader([first]), [])}


def _date_from_name(path: Path) -> str:
    found = re.search(r"(\d{1,2})[^\d]?(\d{1,2})[^\d]?(20\d{2})(?!\d)", path.stem)
    if found:
        try:
            return dt.date(int(found.group(3)), int(found.group(1)), int(found.group(2))).isoformat()
        except ValueError:
            pass
    return dt.date.fromtimestamp(path.stat().st_mtime).isoformat()


def read_carmax_site_table(path: Path) -> list[dict]:
    from .normalize import split_model

    date = _date_from_name(path)
    out = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for r in csv.DictReader(handle):
            place = (r.get("Auction_Site") or "").strip().replace("Murietta", "Murrieta")
            status = (r.get("Status") or "").strip()
            won = bool(re.search(r"you win|won", status, re.I))
            outcome = "Sold" if won or status.lower().startswith("sold") else {"no sale": "No Sale"}.get(status.lower(), status or "нет итога")
            model, trim = split_model((r.get("Model") or "").replace("(no trim)", "").strip())
            rec = {"date": date, "auction": f"CarMax {place}".strip(), "code": "", "lot": f"{r.get('Lane', '')}/{r.get('Lot', '')}".strip("/"),
                   "vin": (r.get("VIN") or "").strip().upper(), "year": (r.get("Year") or "").strip(), "make": (r.get("Make") or "").strip(),
                   "model": f"{model} {trim}".strip(), "miles": re.sub(r"\D", "", r.get("Mileage") or ""), "cr": "", "mmr": "",
                   "outcome": outcome, "price": str(int(_num(r.get("Price")) or 0) or ""), "source": path.name,
                   "remarks": "купили вы" if won else "", "kbb": ""}
            rec["key"] = _key(rec)
            out.append(rec)
    return out


def is_velocicast_page(path: Path) -> bool:
    if path.suffix.lower() not in (".html", ".htm"):
        return False
    try:
        with path.open("rb") as handle:
            head = handle.read(900).decode("utf-8", errors="replace")
    except OSError:
        return False
    return "velocicast" in head.lower() and "saved-by: lot_analyzer" in head


def read_velocicast_page(path: Path) -> list[dict]:
    """Страница CarMax Velocicast (идут или прошли торги), сохранённая закладкой: по дорожкам «Lane A - CarMax Murrieta»
    — номер, машина, VIN, пробег, статус (Sold / No Sale / Early Bid) и цена. Дата — когда сохранили страницу."""
    from bs4 import BeautifulSoup
    from .normalize import split_model

    html = path.read_text(encoding="utf-8", errors="replace")
    saved = re.search(r"saved-at: (\d{4}-\d{2}-\d{2})T(\d{2})", html[:900])
    date = saved.group(1) if saved else dt.date.fromtimestamp(path.stat().st_mtime).isoformat()
    soup = BeautifulSoup(html, "lxml")
    out = []
    for panel in soup.select(".lane-panel-container"):
        name = re.search(r"Lane (\w+) - (CarMax [A-Za-z .]+?)(?= #| \||\s{2}|$)", panel.get_text(" ", strip=True))
        lane, place = (name.group(1), name.group(2).strip()) if name else ("", "CarMax")
        for tr in panel.select("tbody.vc-tbdy-vehlist tr"):
            title = squeeze_text(tr.select_one(".item-info-row div").get_text(" ", strip=True)) if tr.select_one(".item-info-row div") else ""
            head = re.match(r"#(\d+)\s*-\s*(\d{4})\s+(\S+)\s+(.*)", title)
            vin_el = tr.select_one(".item-vin")
            vin = re.search(r"[A-HJ-NPR-Z0-9]{17}", vin_el.parent.get_text(" ", strip=True) if vin_el else "")
            status = squeeze_text(tr.select_one(".badge-row").get_text(" ", strip=True)) if tr.select_one(".badge-row") else ""
            price = _num((tr.select_one(".price-row") or tr).get_text(" ", strip=True).split("$")[-1]) if "$" in tr.get_text() else None
            miles = re.search(r"([\d,]+)\s*M\b", tr.select_one(".mileage-row").get_text(" ", strip=True)) if tr.select_one(".mileage-row") else None
            if not head:
                continue
            model, trim = split_model(head.group(4).replace("(no trim)", "").strip())
            won = bool(re.search(r"you win|won", status, re.I))
            outcome = "Sold" if won else {"sold": "Sold", "no sale": "No Sale", "early bid": "Early Bid"}.get(status.lower(), status or "нет итога")
            rec = {"date": date, "auction": place, "code": "", "lot": f"{lane}/{head.group(1)}".strip("/"),
                   "vin": vin.group(0) if vin else "", "year": head.group(2), "make": head.group(3), "model": f"{model} {trim}".strip(),
                   "miles": miles.group(1).replace(",", "") if miles else "", "cr": "", "mmr": "", "outcome": outcome,
                   "price": str(int(price)) if price and outcome in ("Sold", "Early Bid") else "", "source": path.name,
                   "remarks": "купили вы" if won else "", "kbb": ""}
            rec["key"] = _key(rec)
            out.append(rec)
    return out


def squeeze_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _is_carmax_table(head: str) -> bool:
    first = head.lstrip("\ufeff").splitlines()[0] if head.strip() else ""
    return CARMAX_TABLE_COLUMNS <= {c.strip().strip('"') for c in next(csv.reader([first]), [])}


def read_carmax_table(path: Path) -> list[dict]:
    """CSV итогов CarMax «Location, Lane, Lot #, Year/Make/Model, VIN, Color, Mileage, Status, Sale Price (USD)».
    Дата — из колонки «Date» (новые выгрузки), иначе из имени файла (…_10052026.csv), иначе дата файла."""
    from .normalize import split_model

    # Дата из имени: «…_10052026», «… 10:05:2026» (так Mac хранит «10/05/2026»), «…_10-05-2026».
    found = re.search(r"(\d{1,2})[^\d]?(\d{1,2})[^\d]?(20\d{2})(?!\d)", path.stem)
    date = (f"{found.group(3)}-{int(found.group(1)):02d}-{int(found.group(2)):02d}" if found
            else dt.date.fromtimestamp(path.stat().st_mtime).isoformat())
    out = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for r in csv.DictReader(handle):
            ymm = (r.get("Year/Make/Model") or "").replace("(no trim)", "").split()
            if len(ymm) > 2 and f"{ymm[1]} {ymm[2]}".lower() in ("land rover", "alfa romeo", "aston martin"):   # марка из двух слов
                ymm[1:3] = [f"{ymm[1]} {ymm[2]}"]
            make = ymm[1] if len(ymm) > 1 else ""
            model, trim = split_model(" ".join(ymm[2:]))
            place = (r.get("Location") or "").replace(" Auction Center", "").strip()
            status = (r.get("Status") or "").strip()
            outcome = {"sold": "Sold", "no sale": "No Sale"}.get(status.lower(), status or "нет итога")
            day = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(20\d{2})", (r.get("Date") or "").strip())   # своя колонка «Date», если есть
            rec = {"date": f"{day.group(3)}-{int(day.group(1)):02d}-{int(day.group(2)):02d}" if day else date, "auction": place, "code": "", "lot": f"{r.get('Lane', '')}/{r.get('Lot #', '')}".strip("/"),
                   "vin": (r.get("VIN") or "").strip().upper(), "year": ymm[0] if ymm else "", "make": make,
                   "model": f"{model} {trim}".strip(), "miles": re.sub(r"\D", "", r.get("Mileage") or ""), "cr": "", "mmr": "",
                   "outcome": outcome, "price": str(int(_num(r.get("Sale Price (USD)")) or 0) or ""), "source": path.name,
                   "remarks": "", "kbb": ""}
            rec["key"] = _key(rec)
            out.append(rec)
    return out


def read_carmax(path: Path) -> list[dict]:
    from .market import read_results

    if path.suffix.lower() == ".csv" and _is_carmax_table(path.open(encoding="utf-8-sig", errors="replace").read(2000)):
        return read_carmax_table(path)
    out = []
    for r in read_results(path):
        make, _, model = r.vehicle.partition(" ")
        outcome = {"sold": "Sold", "won": "Sold", "no sale": "No Sale"}.get(r.status.lower().split(" (")[0], r.status or "нет итога")
        rec = {"date": r.date, "auction": f"CarMax {r.location}".strip(), "code": "", "lot": f"{r.lane}/{r.run}".strip("/"),
               "vin": r.vin, "year": r.year, "make": make, "model": model, "miles": re.sub(r"\D", "", r.miles), "cr": "",
               "mmr": str(int(_num(r.mmr) or 0) or ""), "outcome": outcome, "price": str(int(_num(r.price) or 0) or ""),
               "source": path.name, "remarks": r.announcements, "kbb": str(int(_num(r.kbb) or 0) or "")}
        rec["key"] = _key(rec)
        out.append(rec)
    return out


VELOCICAST_EXPORT_COLUMNS = {"site", "run", "finalStatus", "finalAmount", "vin", "year", "make", "model"}


def is_velocicast_export(path: Path) -> bool:
    """Выгрузка итогов CarMax из Velocicast («velocicast-auction-detailed-….csv», «…-results-….csv»):
    site, laneId, run, finalStatus (SOLD / NOSALE), finalAmount, VIN, покупатель, число участников, ваши заметка и представка."""
    if path.suffix.lower() != ".csv":
        return False
    try:
        with path.open(encoding="utf-8-sig", errors="replace") as handle:
            return VELOCICAST_EXPORT_COLUMNS <= _header(handle.read(4000))
    except OSError:
        return False


def _export_date(path: Path, rows: list[dict]) -> str:
    """День торгов: по времени ставок (UTC → время Калифорнии), иначе из имени («2026-10-19-…», «…_19102026», «…_10192026»)."""
    from zoneinfo import ZoneInfo

    for r in rows:
        stamp = (r.get("bidFirstTs") or r.get("liveFirstTs") or r.get("bidLastTs") or "").strip()
        if re.match(r"20\d\d-\d\d-\d\dT", stamp):
            try:
                return dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(ZoneInfo("America/Los_Angeles")).date().isoformat()
            except ValueError:
                continue
    found = re.search(r"(20\d\d)-(\d\d)-(\d\d)", path.stem)
    if found:
        return f"{found.group(1)}-{found.group(2)}-{found.group(3)}"
    found = re.search(r"(\d{2})(\d{2})(20\d{2})(?!\d)", path.stem)
    if found:
        a, b, year = int(found.group(1)), int(found.group(2)), int(found.group(3))
        for month, day in ((a, b), (b, a)):          # 10192026 (месяц-день) или 19102026 (день-месяц)
            try:
                return dt.date(year, month, day).isoformat()
            except ValueError:
                continue
    return dt.date.fromtimestamp(path.stat().st_mtime).isoformat()


def read_velocicast_export(path: Path) -> list[dict]:
    from .normalize import split_model

    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    date = _export_date(path, rows)
    out = []
    for r in rows:
        status = (r.get("finalStatus") or "").strip().upper()
        outcome = {"SOLD": "Sold", "NOSALE": "No Sale", "NO SALE": "No Sale", "IFSALE": "IF Sale"}.get(status, status.title() or "нет итога")
        model, trim = split_model(f"{r.get('model', '')} {r.get('trim', '')}".replace("(no trim)", "").strip())
        place = (r.get("site") or "").replace(" Auction Center", "").strip()
        remarks = [x.strip().capitalize() for x in (r.get("announcement") or "").split("|") if x.strip()]
        if outcome != "Sold" and _num(r.get("highMoney")):
            remarks.append(f"высшая ставка ${int(_num(r.get('highMoney'))):,}")
        if r.get("myPrebid"):
            remarks.append(f"ваша представка ${int(_num(r['myPrebid']) or 0):,}")
        kbb = re.search(r"\bKBB\s*(?:PP\s*)?\$?\s*([\d,]{3,})", r.get("myNote") or "", re.I)
        rec = {"date": date, "auction": place, "code": "", "lot": (r.get("run") or "").strip(),
               "vin": (r.get("vin") or "").strip().upper(), "year": (r.get("year") or "").strip(), "make": (r.get("make") or "").strip(),
               "model": f"{model} {trim}".strip(), "miles": re.sub(r"\D", "", r.get("miles") or ""), "cr": "", "mmr": "",
               "outcome": outcome, "price": str(int(_num(r.get("finalAmount")) or 0) or "") if outcome == "Sold" else "",
               "source": path.name, "remarks": ", ".join(remarks), "kbb": kbb.group(1).replace(",", "") if kbb else ""}
        rec["key"] = _key(rec)
        out.append(rec)
    return out


RU_TABLE_COLUMNS = {"Аукцион", "Лейн", "Лот", "Год", "Марка", "Модель", "VIN", "Статус"}


def is_carmax_ru_table(path: Path) -> bool:
    """Таблица итогов CarMax с русскими колонками («Аукцион, Лейн, Лот, Год, Марка, Модель, … VIN, … Статус,
    Продана/Не продана, Цена продажи, Цена число, Покупатель»): Carmax_results_10202026.csv."""
    if path.suffix.lower() != ".csv":
        return False
    try:
        with path.open(encoding="utf-8-sig", errors="replace") as handle:
            return RU_TABLE_COLUMNS <= _header(handle.read(2000))
    except OSError:
        return False


def read_carmax_ru_table(path: Path) -> list[dict]:
    from .normalize import split_model

    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    date = _export_date(path, [])
    out = []
    for r in rows:
        status = (r.get("Статус") or "").strip().lower()
        ru = (r.get("Продана/Не продана") or "").strip().lower()
        outcome = ("Sold" if status == "sold" or ru == "продана" else "No Sale" if status == "no sale" or ru == "не продана"
                   else "нет итога")                       # «Pending» / «Не торговалась» — машину не выставляли
        model, trim = split_model((r.get("Модель") or "").replace("(no trim)", "").strip())
        price = _num(r.get("Цена число")) or _num(r.get("Цена продажи"))
        rec = {"date": date, "auction": (r.get("Аукцион") or "").replace(" Auction Center", "").strip(), "code": "",
               "lot": f"{r.get('Лейн', '')}/{r.get('Лот', '')}".strip("/"), "vin": (r.get("VIN") or "").strip().upper(),
               "year": (r.get("Год") or "").strip(), "make": (r.get("Марка") or "").strip(), "model": f"{model} {trim}".strip(),
               "miles": re.sub(r"\D", "", r.get("Пробег число") or r.get("Пробег (миль)") or ""), "cr": "", "mmr": "",
               "outcome": outcome, "price": str(int(price)) if price and outcome == "Sold" else "", "source": path.name,
               "remarks": "", "kbb": ""}
        rec["key"] = _key(rec)
        out.append(rec)
    return out


RESULTS_SALE_COLUMNS = {"Sale", "Lane", "Run", "VIN", "Status", "FinalBid"}


def is_carmax_sale_results(path: Path) -> bool:
    """Итоги CarMax / Velocicast «Sale, Lane, EventID, SaleStatus, … Run, VIN, … Status, FinalBid, WinnerBadge …»
    (carmax-velocicast-results-10262026.csv)."""
    if path.suffix.lower() != ".csv":
        return False
    try:
        with path.open(encoding="utf-8-sig", errors="replace") as handle:
            return RESULTS_SALE_COLUMNS <= _header(handle.read(4000))
    except OSError:
        return False


def read_carmax_sale_results(path: Path) -> list[dict]:
    from .normalize import split_model

    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    date = _export_date(path, [])
    out = []
    for r in rows:
        status = (r.get("Status") or "").strip().upper()
        outcome = {"SOLD": "Sold", "NOSALE": "No Sale", "NO SALE": "No Sale", "IFSALE": "IF Sale"}.get(status, "нет итога")   # NOTRUN, пусто
        model, trim = split_model(f"{r.get('Model', '')} {r.get('Trim', '')}".replace("(no trim)", "").strip())
        price = _num(r.get("FinalBid"))
        remarks = [x.strip().capitalize() for x in re.split(r"[;|]", r.get("Announcements") or "") if x.strip()]
        if outcome != "Sold" and _num(r.get("HighBid")):
            remarks.append(f"высшая ставка ${int(_num(r.get('HighBid'))):,}")
        if _num(r.get("MyBid")):
            remarks.append(f"ваша ставка ${int(_num(r.get('MyBid'))):,}")
        day = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(20\d{2})", (r.get("Date") or "").strip())      # своя колонка «Date» (MM/DD/YYYY)
        rec = {"date": f"{day.group(3)}-{int(day.group(1)):02d}-{int(day.group(2)):02d}" if day else date, "auction": (r.get("Sale") or "").replace(" Auction Center", "").strip(), "code": "",
               "lot": f"{(r.get('Lane') or '').strip()}/{(r.get('Run') or '').strip()}".strip("/"),
               "vin": (r.get("VIN") or "").strip().upper(), "year": (r.get("Year") or "").strip(), "make": (r.get("Make") or "").strip(),
               "model": f"{model} {trim}".strip(), "miles": re.sub(r"\D", "", r.get("Miles") or ""), "cr": "", "mmr": "",
               "outcome": outcome, "price": str(int(price)) if price and outcome == "Sold" else "", "source": path.name,
               "remarks": ", ".join(remarks), "kbb": ""}
        rec["key"] = _key(rec)
        out.append(rec)
    return out


def is_carmax_site_csv(path: Path) -> bool:
    if path.suffix.lower() != ".csv":
        return False
    try:
        with path.open(encoding="utf-8-sig", errors="replace") as handle:
            return CARMAX_SITE_COLUMNS <= _header(handle.read(2000))
    except OSError:
        return False


def read_file(path: Path) -> list[dict]:
    if is_velocicast_export(path):
        return read_velocicast_export(path)
    if is_carmax_ru_table(path):
        return read_carmax_ru_table(path)
    if is_carmax_sale_results(path):
        return read_carmax_sale_results(path)
    if is_carmax_site_csv(path):
        return read_carmax_site_table(path)
    if is_velocicast_page(path):
        return read_velocicast_page(path)
    if is_manheim_lanes_csv(path):
        return read_manheim_lanes_csv(path)
    if is_lane_csv(path):
        return read_lane_csv(path)
    if is_postsale_pdf(path):
        return read_postsale_pdf(path)
    if is_carmax_results(path):
        return read_carmax(path)
    return []


# ---------------------------------------------------------------- история

def load_history(path: Path | None = None) -> dict[str, dict]:
    path = path or HISTORY_PATH
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        return {r["key"]: {k: v or "" for k, v in r.items()} for r in csv.DictReader(handle)}


def save_history(records: dict[str, dict], path: Path | None = None) -> None:
    path = path or HISTORY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        for rec in sorted(records.values(), key=lambda r: (r["date"], r["auction"], r["lot"], r["key"])):
            writer.writerow(rec)


def merge(history: dict[str, dict], new: list[dict]) -> bool:
    """Добавляет новые итоги; MMR, найденный позже по списку до торгов, не затирается пустым."""
    changed = False
    for rec in new:
        old = history.get(rec["key"])
        if old is None:
            history[rec["key"]] = rec
            changed = True
        else:
            for field in ("mmr", "kbb", "remarks"):
                if rec.get(field) and not old.get(field):
                    old[field] = rec[field]
                    changed = True
    return changed


# ---------------------------------------------------------------- лоты ↔ итоги

def index(history: dict[str, dict]) -> tuple[dict, dict]:
    by_vin, by_ymm = {}, {}
    for rec in history.values():
        if rec.get("vin"):
            by_vin.setdefault(rec["vin"], []).append(rec)
        by_ymm.setdefault((rec["year"], rec["make"].upper(), rec["miles"]), []).append(rec)
    return by_vin, by_ymm


def find(row: dict, by_vin: dict, by_ymm: dict) -> dict | None:
    """Свежий итог для машины лота: по VIN, иначе по году, марке и точному пробегу."""
    found = by_vin.get(row.get("vin", "")) or by_ymm.get(
        (str(row.get("year", "")), str(row.get("make", "")).upper(), re.sub(r"\D", "", str(row.get("odometer_miles", "")))))
    if not found:
        return None
    return sorted(found, key=lambda r: (r["date"], r["outcome"] == "Sold"))[-1]


def short(rec: dict) -> str:
    """«был 9/29/26 CarMax Chino — продана $10,250»."""
    try:
        y, m, d = rec["date"][:10].split("-")
        when = f"{int(m)}/{int(d)}/{y[2:]}"
    except ValueError:
        when = rec.get("date", "")
    price = _num(rec.get("price"))
    outcome = rec.get("outcome", "")
    what = (f"продана ${price:,.0f}" if outcome == "Sold" and price else f"IF ${price:,.0f}, не продана" if outcome.lower().startswith("if") and price
            else "не продана" if outcome == "No Sale" else outcome.lower() or "итога нет")
    return f"был на аукционе {when} {rec.get('auction', '')} — {what}"


def describe(rec: dict, mmr: float | None = None) -> str:
    mmr = mmr or _num(rec.get("mmr"))
    price = _num(rec.get("price"))
    when = f"{rec['auction']} {rec['date']}".strip()
    if rec["outcome"] == "Sold" and price:
        return f"Продано ${price:,.0f}" + (f" (× MMR {price / mmr:.2f})" if mmr else "") + f" · {when}"
    if rec["outcome"].lower().startswith("if") and price:
        return f"IF ${price:,.0f} — продавец не согласился" + (f" (× MMR {price / mmr:.2f})" if mmr else "") + f" · {when}"
    if rec["outcome"] == "No Sale":
        return f"Не продана · {when}"
    return f"{rec['outcome']} · {when}"


# ---------------------------------------------------------------- статистика по площадкам

def stats(history: dict[str, dict], min_sales: int = 20) -> dict[str, dict]:
    """По каждой площадке: продажи с MMR, медиана «цена ÷ MMR» и по ценовым диапазонам."""
    groups: dict[str, list[tuple[float, float]]] = {}
    for rec in history.values():
        price, mmr = _num(rec.get("price")), _num(rec.get("mmr"))
        if rec["outcome"] == "Sold" and price and mmr:
            groups.setdefault(rec["auction"], []).append((price, mmr))
    out = {}
    for auction, sales in groups.items():
        ratios = sorted(p / m for p, m in sales)
        bands, low = [], 0.0
        for upper in BANDS:
            part = [p / m for p, m in sales if low <= m < upper]
            if len(part) >= 5:
                bands.append([upper, round(statistics.median(part), 2)])
            low = upper
        n = len(ratios)
        out[auction] = {"n": n, "median": round(statistics.median(ratios), 3), "bands": bands, "enough": n >= min_sales,
                        "curve": [[round(ratios[min(n - 1, int(q * n))], 3), q] for q in (0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95)]}
    return out
