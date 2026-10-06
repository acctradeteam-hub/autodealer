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


def is_results_file(path: Path) -> bool:
    return is_lane_csv(path) or is_postsale_pdf(path) or is_carmax_results(path)


CARMAX_TABLE_HEAD = "Location,Lane,Lot #,Year/Make/Model,VIN"
# Колонки, по которым узнаём таблицу итогов CarMax, в любом порядке (CarMax добавляет новые, например «Date»).
CARMAX_TABLE_COLUMNS = {"Location", "Lane", "Lot #", "Year/Make/Model", "VIN", "Status", "Sale Price (USD)"}


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


def read_file(path: Path) -> list[dict]:
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
