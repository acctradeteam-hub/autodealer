"""Результаты торгов Manheim → калибровка «рынка» (сколько реально платят от MMR).

    python3 -m lot_analyzer.manheim_results ФАЙЛЫ… [--lists СПИСКИ…] [--update-config]

Понимает:
  * CSV итогов дорожки Simulcast: Run #, Year/Make/Model, VIN, CR, Odometer, MMR Avg, Outcome, Sale Price;
  * PDF «Post-Sale Results – Vehicle Listing» (год, марка, модель, пробег, цена) — в нём нет MMR,
    поэтому MMR берётся из списков до торгов (--lists: CSV-выгрузка Manheim или сохранённая страница поиска)
    по году, марке и точному пробегу.

Считает «цена продажи ÷ MMR» по проданным машинам: медиану по ценовым диапазонам MMR и кривую
шанса выиграть. С --update-config записывает их в config/costs.json → market_by_auction.Manheim.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
from pathlib import Path

from .bid import DEFAULT_COSTS_PATH

BANDS = (5000, 10000, 20000, 1e9)


def _num(text) -> float | None:
    text = re.sub(r"[$,\s]", "", str(text or ""))
    try:
        return float(text) if text else None
    except ValueError:
        return None


def read_lane_csv(path: Path) -> list[dict]:
    """Итоги дорожки Simulcast: продано (Sold) с ценой и MMR."""
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    out = []
    for r in rows:
        price, mmr = _num(r.get("Sale Price")), _num(r.get("MMR Avg"))
        if (r.get("Outcome") or "").strip() == "Sold" and price and mmr:
            out.append({"vin": r.get("VIN", ""), "price": price, "mmr": mmr, "source": path.name})
    return out


def read_postsale_pdf(path: Path) -> list[dict]:
    """PDF Post-Sale Results: год, марка, описание, пробег, цена (MMR в нём нет)."""
    from pypdf import PdfReader

    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    out = []
    for line in text.splitlines():
        m = re.match(r"(\d{4}) ([A-Z][A-Z-]+) (.+?) ([\d,]+) \$([\d,]+)\s*$", line.strip())
        if m:
            out.append({"year": m[1], "make": m[2], "desc": m[3], "miles": int(m[4].replace(",", "")),
                        "price": float(m[5].replace(",", "")), "source": path.name})
    return out


def mmr_index(lists: list[Path]) -> dict:
    """(год, МАРКА, пробег) → MMR из списков до торгов."""
    from .manheim_csv import is_export, read_export
    from .pages import read_page
    from .parsers import parse_page

    index = {}
    for path in lists:
        rows = read_export(path) if is_export(path) else parse_page(read_page(path), source_name=path.name)
        for r in rows:
            miles, mmr = _num(r.get("odometer_miles")), _num(r.get("mmr_adjusted_usd"))
            if miles and mmr:
                index[(str(r.get("year")), str(r.get("make", "")).upper(), int(miles))] = mmr
    return index


def summarize(sales: list[dict]) -> dict:
    ratios = sorted(s["price"] / s["mmr"] for s in sales)
    if len(ratios) < 10:
        raise SystemExit(f"мало продаж с MMR для калибровки: {len(ratios)}")
    n = len(ratios)
    bands, low = [], 0
    for upper in BANDS:
        part = sorted(s["price"] / s["mmr"] for s in sales if low <= s["mmr"] < upper)
        if len(part) >= 5:
            bands.append([upper, round(statistics.median(part), 2), len(part)])
        low = upper
    curve = [[round(ratios[min(n - 1, int(q * n))], 3), q] for q in (0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95)]
    return {"n": n, "median": round(statistics.median(ratios), 3), "bands": bands, "curve": curve}


def update_config(summary: dict, costs_path: Path, sources: str) -> None:
    text = costs_path.read_text(encoding="utf-8")
    line = next(l for l in text.splitlines() if l.strip().startswith('"market_by_auction"'))
    data = json.loads("{" + line.strip().rstrip(",") + "}")["market_by_auction"]
    manheim = data.setdefault("Manheim", {"prefer": "mmr"})
    manheim.update({
        "mmr_clean": round(summary["median"], 2),
        "mmr_bands": [[b[0], b[1]] for b in summary["bands"]],
        "mmr_clean_curve": summary["curve"],
        "based_on": f"цена продажи ÷ Adj MMR, {summary['n']} продаж ({sources}); " + ", ".join(
            f"MMR до {int(b[0]) if b[0] < 1e9 else '∞'}: {b[1]} (n={b[2]})" for b in summary["bands"]),
    })
    costs_path.write_text(text.replace(line, '  "market_by_auction": ' + json.dumps(data, ensure_ascii=False) + ","), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lot_analyzer.manheim_results", description=__doc__.split("\n")[0])
    parser.add_argument("files", nargs="+", help="CSV итогов дорожки Simulcast и/или PDF Post-Sale Results")
    parser.add_argument("--lists", nargs="*", default=[], help="списки до торгов (CSV-выгрузка / страница поиска) — MMR для PDF")
    parser.add_argument("--costs", default=str(DEFAULT_COSTS_PATH))
    parser.add_argument("--update-config", action="store_true", help="записать калибровку в config/costs.json")
    args = parser.parse_args(argv)

    sales, unmatched = [], 0
    index = mmr_index([Path(p) for p in args.lists]) if args.lists else {}
    for name in args.files:
        path = Path(name)
        if path.suffix.lower() == ".pdf":
            for s in read_postsale_pdf(path):
                mmr = index.get((s["year"], s["make"], s["miles"]))
                if mmr:
                    sales.append({**s, "mmr": mmr})
                else:
                    unmatched += 1
        else:
            sales += read_lane_csv(path)
    summary = summarize(sales)
    print(f"Продаж с MMR: {summary['n']} (без MMR в списках: {unmatched}); цена ÷ MMR — медиана {summary['median']}")
    for upper, share, n in summary["bands"]:
        print(f"  MMR до {int(upper) if upper < 1e9 else '∞'}: {share} (n={n})")
    if args.update_config:
        update_config(summary, Path(args.costs), ", ".join(Path(f).name for f in args.files)[:200])
        print(f"Записано в {args.costs} → market_by_auction.Manheim")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
