"""Результаты торгов: сколько на самом деле платят относительно KBB и MMR.

Источники (все — то, что покупатель сохраняет у себя сам):
  * «My List» CarMax (PDF/CSV-выгрузка): Run, Lane, машина, MyMax, HighBid, Lost/Won, заметка с KBB;
  * печатный run list CarMax с заметками (PDF): A/1 2015 Honda Odyssey … + KBB PP / MMR / Est Retail / Sold / MP;
  * CSV результатов всех дорожек («DETAILED RESULTS - ALL LANES»): VIN, пробег, статус, цена продажи.

Записи копятся в data/market_history.csv (без дублей), а сводка — «цена продажи ÷ KBB»
по машинам без тяжёлых дефектов — становится ориентиром «сколько возьмёт рынок».

    python3 -m lot_analyzer.market results/*.pdf results/*.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import statistics
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .normalize import clean_vin, parse_money, squeeze

HISTORY_PATH = Path("data/market_history.csv")

# Тяжёлые дефекты и проблемы титула: такие машины уходят дешевле, в «чистую» сводку не берём.
HEAVY = re.compile(
    r"major (engine|transmission|transfer case) defect|structural|frame|salvage|rebuilt|total loss|"
    r"not actual|flood|water intrusion|no runner|non[\s-]*runner|no start|title absent|227|as[\s-]?is|inop",
    re.I,
)


@dataclass
class Result:
    date: str = ""
    auction: str = ""          # CarMax
    location: str = ""         # Murrieta, Oceanside
    lane: str = ""
    run: str = ""
    year: str = ""
    vehicle: str = ""          # «Honda Civic LX»
    vin: str = ""
    miles: str = ""
    announcements: str = ""
    notes: str = ""
    kbb: str = ""
    mmr: str = ""
    est_retail: str = ""
    my_max: str = ""
    price: str = ""            # цена продажи / выигравшая ставка
    status: str = ""           # Sold / Lost / Won / No Sale / Not Run
    source: str = ""

    def key(self) -> str:
        if self.vin:
            return f"vin:{self.vin}:{self.date}"
        return f"run:{self.location}:{self.lane}/{self.run}:{self.date}".lower()


# ---------------------------------------------------------------- заметки

_KBB = re.compile(r"\bKBB(?:\s*PP)?(?:\s*Good)?:?\s*\$?\s*([\d][\d,]{2,})(?:\s*-\s*\$?([\d][\d,]{2,}))?\s*\$?", re.I)
_MMR = re.compile(r"\bMMR\s*\$?\s*([\d][\d,]{2,})", re.I)
_RETAIL = re.compile(r"\bEst\.?\s*Retail\s*\$?\s*([\d][\d,]{2,})", re.I)
_SOLD = re.compile(r"\bsold\s*\$?\s*([\d][\d,]{2,})", re.I)
_MP = re.compile(r"\bMP\s*\$?\s*([\d][\d,]{2,})", re.I)


def _num(text: str) -> str:
    return text.replace(",", "") if text else ""


def note_values(text: str) -> dict[str, str]:
    """KBB (диапазон «$9,990-12,340» → середина), MMR, Est Retail, Sold, MP из заметки. Берётся последнее."""
    out: dict[str, str] = {}
    kbbs = _KBB.findall(text)
    if kbbs:
        low, high = kbbs[-1]
        low_v = float(_num(low))
        high_v = float(_num(high)) if high else low_v
        if high and high_v < low_v:              # «$9,990-12,340» — вторая часть без тысяч? нет, но на всякий
            high_v = low_v
        out["kbb"] = f"{(low_v + high_v) / 2:.0f}"
    for key, pattern in (("mmr", _MMR), ("est_retail", _RETAIL), ("price", _SOLD), ("my_max", _MP)):
        found = pattern.findall(text)
        if found:
            out[key] = _num(found[-1])
    return out


# ---------------------------------------------------------------- «My List» CarMax

_MYLIST_HEAD = re.compile(r"^CarMax (\w+) (Early Bid|watch & note|note|watch)\s*(\d+) ([A-Z]) (.+)$")
_STATUS = re.compile(r"\s(Lost|Won|Win|Not Run)\b(.*)$")


def parse_my_list(text: str, source: str = "") -> list[Result]:
    results = []
    for raw in text.splitlines():
        line = squeeze(raw)
        head = _MYLIST_HEAD.match(line)
        if not head:
            continue
        location, _kind, run, lane, rest = head.groups()
        status = _STATUS.search(" " + rest)
        if not status:
            continue
        desc_and_bids = (" " + rest)[:status.start()].strip()
        note = squeeze(status.group(2))
        tokens = desc_and_bids.split(" ")
        numbers: list[str] = []
        # С конца: одно-два числа (MyMax, HighBid); число может быть склеено с описанием: «Touring7250».
        while tokens and len(numbers) < 2:
            tail = re.match(r"^(.*?)(\d{3,6})$", tokens[-1])
            if not tail:
                break
            prefix, digits = tail.groups()
            numbers.insert(0, digits)
            if prefix:
                tokens[-1] = prefix
                break
            tokens.pop()
        description = " ".join(tokens)
        year = re.match(r"^((?:19|20)\d{2})\s+(.*)$", description)
        result = Result(
            auction="CarMax", location=location, lane=lane, run=run,
            year=year.group(1) if year else "", vehicle=(year.group(2) if year else description).replace("(no trim)", "").strip(),
            status=status.group(1).replace("Win", "Won"), notes=note, source=source,
        )
        if len(numbers) == 2:
            result.my_max, result.price = numbers
        elif len(numbers) == 1 and result.status != "Not Run":
            result.price = numbers[0]
        values = note_values(note)
        result.kbb = values.get("kbb", "")
        date = re.search(r"(\d{2})/(\d{2})/(\d{4})", note)
        result.date = f"{date.group(3)}-{date.group(1)}-{date.group(2)}" if date else ""
        results.append(result)
    return results


# ---------------------------------------------------------------- печатный run list CarMax с заметками

_RUN_LINE = re.compile(r"^([A-Z])/(\d+)$")
_RUN_HEAD = re.compile(r"^([A-Z])/(\d+)\s+((?:19|20)\d{2})\s+(.+?)\s+([\d,]{2,9})\s+(?:N/A|\S+ • .+?)\s+([A-HJ-NPR-Z0-9]{17})\s+(\S+)\s+(\S+)$")
_SKIP_LINE = ("CarMax makes every effort", "information about each vehicle", "Run", "Year Make Model Trim", "Mileage",
              "Drive • Transmission • Engine", "VIN", "Color", "Location", "www.carmaxauctions.com")


def parse_run_list(text: str, source: str = "") -> list[Result]:
    """Печатный run list CarMax с заметками. pypdf отдаёт поля либо строкой, либо каждое на своей строке."""
    printed = re.search(r"(\d{2})/(\d{2})/(\d{4}) \d{1,2}:\d{2}", text)
    date = f"{printed.group(3)}-{printed.group(1)}-{printed.group(2)}" if printed else ""
    lines = [squeeze(x) for x in text.splitlines()]
    lines = [x for x in lines if x and not x.startswith(_SKIP_LINE[:2]) and x not in _SKIP_LINE[2:]
             and not x.startswith("CarMax Auctions Page") and not re.match(r"^\d{2}/\d{2}/\d{4} \d", x)]
    results: list[Result] = []
    i = 0
    while i < len(lines):
        head = _RUN_HEAD.match(lines[i])
        if head:
            lane, run, year, vehicle, miles, vin, _color, location = head.groups()
            i += 1
        elif _RUN_LINE.match(lines[i]) and i + 6 < len(lines) and re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", lines[i + 4]):
            lane, run = _RUN_LINE.match(lines[i]).groups()
            title = re.match(r"^((?:19|20)\d{2})\s+(.+)$", lines[i + 1])
            year, vehicle = (title.groups() if title else ("", lines[i + 1]))
            miles, vin, location = lines[i + 2], lines[i + 4], lines[i + 6]
            i += 7
        else:
            i += 1
            continue
        body = []
        while i < len(lines) and not _RUN_HEAD.match(lines[i]) and not (
            _RUN_LINE.match(lines[i]) and i + 4 < len(lines) and re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", lines[i + 4])
        ):
            body.append(lines[i])
            i += 1
        rec = Result(date=date, auction="CarMax", location=location, lane=lane, run=run, year=year,
                     vehicle=vehicle, vin=vin, miles=miles.replace(",", ""), source=source)
        rec.announcements = body[0] if body else ""
        rec.notes = " | ".join(body[1:])
        values = note_values(" ".join(body[1:]))
        rec.kbb, rec.mmr, rec.est_retail = values.get("kbb", ""), values.get("mmr", ""), values.get("est_retail", "")
        rec.price, rec.my_max = values.get("price", ""), values.get("my_max", "")
        rec.status = "Sold" if rec.price else ""
        results.append(rec)
    return results


# ---------------------------------------------------------------- CSV всех дорожек

def parse_all_lanes_csv(path: Path) -> list[Result]:
    text = path.read_text(encoding="utf-8-sig")
    generated = re.search(r"Generated: \w{3} (\w{3}) (\d{1,2}) (\d{4})", text)
    months = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
    date = f"{generated.group(3)}-{months[generated.group(1)]:02d}-{int(generated.group(2)):02d}" if generated else ""
    start = text.find('"Auction","Lane","Run #"')
    if start < 0:
        return []
    results = []
    for rec in csv.DictReader(text[start:].splitlines()):
        if not rec.get("VIN"):
            continue
        vehicle = squeeze(rec.get("Year / Vehicle", ""))
        year = re.match(r"^((?:19|20)\d{2})\s+(.*)$", vehicle)
        price = parse_money(rec.get("Sale Price"))
        results.append(Result(
            date=date, auction="CarMax", location=squeeze(rec.get("Auction", "")).replace("CarMax ", ""),
            lane=rec.get("Lane", ""), run=rec.get("Run #", ""), year=year.group(1) if year else "",
            vehicle=year.group(2) if year else vehicle, vin=clean_vin(rec.get("VIN", "")),
            miles=(rec.get("Mileage") or "").replace(",", ""), status=rec.get("Status", ""),
            price=f"{price:.0f}" if price else "", source=path.name,
        ))
    return results


# ---------------------------------------------------------------- выгрузка Velocicast (JSON / CSV)

def _velocicast_record(v: dict, source: str) -> Result | None:
    vin = clean_vin(str(v.get("vin") or ""))
    if not vin:
        return None
    status = {"SOLD": "Sold", "NOSALE": "No Sale"}.get(str(v.get("final_status") or "").upper(), "")
    if not status and str(v.get("has_run", "1")) == "0":
        status = "Not Run"
    price = parse_money(str(v.get("final_amount") or "")) if status == "Sold" else None
    announcements = ", ".join(x.strip().capitalize() for x in re.split(r"[\n,]+", str(v.get("announcement") or "")) if x.strip())
    location = squeeze(str(v.get("auction_location") or "")).replace("CarMax ", "").replace(" Auction Center", "")
    return Result(
        date=str(v.get("event_start_utc") or "")[:10], auction="CarMax", location=location,
        lane=str(v.get("lane") or ""), run=str(v.get("item_num") or ""), year=str(v.get("year") or ""),
        vehicle=squeeze(f"{v.get('make', '')} {v.get('model', '')} {v.get('trim') or ''}"), vin=vin,
        miles=str(v.get("miles") or ""), announcements=announcements, status=status,
        price=f"{price:.0f}" if price else "", source=source,
        notes=f"floor ${v['floor_amount']}" if v.get("floor_amount") else "",
    )


def parse_velocicast(path: Path) -> list[Result]:
    """Выгрузка результатов CarMax / Velocicast: JSON {"vehicles": […]} или CSV с теми же колонками."""
    if path.suffix.lower() == ".json":
        import json

        items = json.loads(path.read_text(encoding="utf-8")).get("vehicles") or []
    else:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            items = list(csv.DictReader(handle))
    return [rec for rec in (_velocicast_record(v, path.name) for v in items) if rec]


# ---------------------------------------------------------------- таблица результатов с досчитанными оценками

def parse_price_table_csv(path: Path) -> list[Result]:
    """CSV «Аукцион / Лайн, Лот #, … VIN, … Статус, Цена покупки, $, KBB…, MMR…».

    Берём только факты торгов (VIN, дорожка, лот, пробег, статус, цена). Колонки KBB и
    MMR в таком файле могут быть досчитаны, а не взяты с KBB / Manheim, — их не читаем.
    """
    results = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for rec in csv.DictReader(handle):
            vin = clean_vin(rec.get("VIN", ""))
            price = parse_money(rec.get("Цена покупки, $"))
            if not vin:
                continue
            place = squeeze(rec.get("Аукцион / Лайн", ""))
            location, _, lane = place.partition(" - Lane ")
            status = squeeze(rec.get("Статус", "")).replace(" (You Win!)", "")
            results.append(Result(
                auction="CarMax", location=location, lane=lane, run=squeeze(rec.get("Лот #", "")),
                year=squeeze(rec.get("Год", "")), vehicle=squeeze(f"{rec.get('Марка', '')} {rec.get('Модель / Комплектация', '')}"),
                vin=vin, miles=squeeze(rec.get("Пробег, мили", "")), status=status,
                price=f"{price:.0f}" if price and status.startswith("Sold") else "", source=path.name,
            ))
    return results


# ---------------------------------------------------------------- чтение файлов, история

def pdf_text(path: Path) -> str:
    from pypdf import PdfReader  # только для PDF; pip install pypdf

    return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


def read_results(path: Path) -> list[Result]:
    if path.suffix.lower() == ".pdf":
        text = pdf_text(path)
        return parse_my_list(text, path.name) or parse_run_list(text, path.name)
    if path.suffix.lower() == ".json":
        return parse_velocicast(path)
    if path.suffix.lower() == ".csv":
        text = path.read_text(encoding="utf-8-sig")
        if text.startswith("auction_location,") or "final_amount" in text[:600]:
            return parse_velocicast(path)
        if "DETAILED RESULTS" in text or "velocicast" in text[:2000]:
            return parse_all_lanes_csv(path)
        if "Цена покупки" in text[:1000] and "VIN" in text[:1000]:
            return parse_price_table_csv(path)
    return []


FIELDS = [f.name for f in fields(Result)]


def merge(records: list[Result], history: list[Result]) -> list[Result]:
    """Объединяет записи: по VIN+дате или по месту/дорожке/номеру+дате. Пустые поля дополняются."""
    by_key: dict[str, Result] = {}
    by_run: dict[str, list[str]] = {}
    by_vin: dict[str, str] = {}

    def same_car(a: Result, b: Result) -> bool:
        make = lambda r: (r.vehicle.split() or [""])[0].lower()
        return a.year == b.year and make(a) == make(b)

    # Сначала записи с VIN — чтобы записям без VIN («My List») было с чем сопоставиться.
    for rec in history + sorted(records, key=lambda r: not r.vin):
        key = rec.key()
        run_key = f"{rec.location}:{rec.lane}/{rec.run}".lower()
        if key not in by_key and not rec.vin and run_key in by_run:
            # «My List» без VIN — к записи той же дорожки/номера, только если год и марка совпадают:
            # номера дорожек повторяются от торгов к торгам.
            match = next((k for k in by_run[run_key] if same_car(by_key[k], rec)), None)
            if match:
                key = match
        elif key not in by_key and rec.vin and rec.vin in by_vin:
            other = by_key[by_vin[rec.vin]]
            if not rec.date or not other.date:
                key = by_vin[rec.vin]         # та же машина, у одной из записей нет даты
        if key in by_key:
            old = by_key[key]
            for name in FIELDS:
                if not getattr(old, name) and getattr(rec, name):
                    setattr(old, name, getattr(rec, name))
        else:
            by_key[key] = rec
        if rec.run and key not in by_run.setdefault(run_key, []):
            by_run[run_key].append(key)
        if rec.vin:
            by_vin.setdefault(rec.vin, key)
    return list(by_key.values())


def load_history(path: Path = HISTORY_PATH) -> list[Result]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [Result(**{k: (v or "") for k, v in row.items() if k in FIELDS}) for row in csv.DictReader(handle)]


def save_history(records: list[Result], path: Path = HISTORY_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for rec in sorted(records, key=lambda r: (r.date, r.location, r.lane, r.run.zfill(4))):
            writer.writerow(asdict(rec))


# ---------------------------------------------------------------- сводка

@dataclass
class Summary:
    n: int
    median: float
    low: float      # 25-й процентиль
    high: float     # 75-й процентиль


def ratio_summary(records: list[Result], base: str = "kbb", clean_only: bool = True) -> Summary | None:
    ratios = []
    for rec in records:
        price, value = parse_money(rec.price), parse_money(getattr(rec, base))
        if not price or not value or price < 300 or rec.status in ("No Sale", "Not Run"):
            continue
        if clean_only and HEAVY.search(f"{rec.announcements} {rec.notes}"):
            continue
        ratios.append(price / value)
    if len(ratios) < 3:
        return None
    ratios.sort()
    n = len(ratios)
    return Summary(n, statistics.median(ratios), ratios[n // 4], ratios[(3 * n) // 4])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lot_analyzer.market", description="Результаты торгов → история и сводка «цена ÷ KBB».")
    parser.add_argument("files", nargs="+", help="PDF «My List» / run list с заметками, CSV всех дорожек")
    parser.add_argument("--history", default=str(HISTORY_PATH))
    parser.add_argument("--update-config", nargs="?", const="config/costs.json", default="",
                        help="записать медианы в раздел market файла настроек (по умолчанию config/costs.json)")
    args = parser.parse_args(argv)
    new: list[Result] = []
    for name in args.files:
        found = read_results(Path(name))
        print(f"  {Path(name).name}: записей {len(found)}")
        new.extend(found)
    history_path = Path(args.history)
    merged = merge(new, load_history(history_path))
    save_history(merged, history_path)
    print(f"История: {history_path} — записей {len(merged)}")
    shares: dict[str, float] = {}
    counts: dict[str, int] = {}
    heavy = [r for r in merged if HEAVY.search(f"{r.announcements} {r.notes}")]
    for base, title in (("kbb", "KBB Private Party"), ("mmr", "MMR")):
        for kind, subset, clean_only, label in (("clean", merged, True, "без тяжёлых дефектов"), ("heavy", heavy, False, "с тяжёлыми дефектами")):
            summary = ratio_summary(subset, base, clean_only=clean_only)
            if summary:
                shares[f"{base}_{kind}"], counts[f"{base}_{kind}"] = round(summary.median, 2), summary.n
                print(f"Цена продажи ÷ {title}, {label} (n={summary.n}): медиана {summary.median:.2f}, "
                      f"половина машин — от {summary.low:.2f} до {summary.high:.2f}")
    if args.update_config:
        update_config(Path(args.update_config), shares, counts)
    return 0


def update_config(path: Path, shares: dict[str, float], counts: dict[str, int]) -> None:
    """Переписывает строку «"market": {…}» в config/costs.json, остальной файл не трогает."""
    import json

    text = path.read_text(encoding="utf-8")
    current = json.loads(text).get("market", {})
    current.update(shares)
    current["based_on"] = "история результатов: " + ", ".join(f"{k} n={v}" for k, v in counts.items())
    line = '  "market": ' + json.dumps(current, ensure_ascii=False) + ","
    new_text, found = re.subn(r'^  "market": \{.*\},?$', line, text, flags=re.M)
    if not found:
        raise SystemExit(f"в {path} нет строки \"market\" — добавьте её вручную")
    json.loads(new_text)
    path.write_text(new_text, encoding="utf-8")
    print(f"Обновлено: {path} → market = {current}")


if __name__ == "__main__":
    raise SystemExit(main())
