"""Журнал ваших сделок: реальная прибыль, маржа, сроки — и сверка настроек расчёта.

    python3 -m lot_analyzer.deals add --vehicle "2015 Honda Civic" --auction CarMax --bid 4500 --paid 4945 \\
        --repair 28 --prep 0 --dealer 300 --sale 8300 --days-to-list 3 --days-listed 2 --no-photos
    python3 -m lot_analyzer.deals            # сводка и подсказки для config/costs.json

Сделки хранятся в data/deals.csv (только у вас, в git не попадает).
"""

from __future__ import annotations

import argparse
import csv
import os
import statistics
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .bid import auction_fee, load_costs

DEALS_PATH = Path(os.environ.get("LOT_ANALYZER_DEALS", "data/deals.csv"))


@dataclass
class Deal:
    vehicle: str = ""
    vin: str = ""
    auction: str = ""
    bid: float = 0.0          # ставка (hammer)
    paid: float = 0.0         # заплачено аукциону вместе со сборами
    transport: float = 0.0
    repair: float = 0.0
    prep: float = 0.0         # мойка, детейлинг
    dealer: float = 0.0
    other: float = 0.0
    sale: float = 0.0
    kbb: float = 0.0          # KBB PP (92620, Good) на момент покупки, если смотрели
    days_to_list: int = 0     # от покупки до публикации объявления
    days_listed: int = 0      # от публикации до продажи
    no_photos: str = ""       # «да» — лот был без фото
    announced_defect: str = ""   # что было в объявлении: «Major Engine Defect» …
    defect_confirmed: str = ""   # «да» / «нет» — подтвердился ли на деле
    notes: str = ""

    @property
    def invested(self) -> float:
        return self.paid + self.transport + self.repair + self.prep + self.dealer + self.other

    @property
    def profit(self) -> float:
        return self.sale - self.invested

    @property
    def days(self) -> int:
        return self.days_to_list + self.days_listed


NUMERIC = {f.name for f in fields(Deal) if f.type in ("float", "int")}


def load(path: Path = DEALS_PATH) -> list[Deal]:
    if not path.exists():
        return []
    deals = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            values = {}
            for f in fields(Deal):
                raw = row.get(f.name, "") or ""
                values[f.name] = (int(float(raw)) if f.type == "int" else float(raw)) if f.name in NUMERIC and raw else (raw if f.name not in NUMERIC else 0)
            deals.append(Deal(**values))
    return deals


def save(deals: list[Deal], path: Path = DEALS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[f.name for f in fields(Deal)])
        writer.writeheader()
        for deal in deals:
            writer.writerow(asdict(deal))


def report(deals: list[Deal], costs: dict) -> list[str]:
    lines = []
    for d in deals:
        fee_table = auction_fee(d.bid, d.auction, costs) if d.auction and d.bid else None
        fee_real = d.paid - d.bid if d.paid and d.bid else None
        line = (f"{d.vehicle}: закуп ${d.bid:,.0f} (со сборами ${d.paid:,.0f}), расходы ${d.invested - d.paid:,.0f}, продажа ${d.sale:,.0f} → "
                f"прибыль ${d.profit:,.0f} = {d.profit / d.sale:.0%} от продажи, {d.profit / d.invested:.0%} на вложенное; "
                f"{d.days} дн. ({d.days_to_list} до объявления + {d.days_listed} в продаже)")
        if d.kbb:
            line += f"; продажа = {d.sale / d.kbb:.0%} KBB, закуп = {d.bid / d.kbb:.0%} KBB"
        if fee_table is not None and fee_real is not None:
            line += f"; сбор {d.auction}: реально ${fee_real:,.0f}, по нашей сетке ${fee_table:,.0f}"
        if d.no_photos:
            line += "; лот без фото"
        if d.announced_defect:
            line += f"; объявлено «{d.announced_defect}» — {'подтвердилось' if d.defect_confirmed == 'да' else 'не подтвердилось' if d.defect_confirmed == 'нет' else 'не проверено'}"
        lines.append(line)
    if deals:
        lines.append("")
        lines.append(f"Сделок: {len(deals)}. Медиана: прибыль ${statistics.median(d.profit for d in deals):,.0f}, "
                     f"{statistics.median(d.profit / d.sale for d in deals):.0%} от продажи; "
                     f"до объявления {statistics.median(d.days_to_list for d in deals):g} дн., в продаже {statistics.median(d.days_listed for d in deals):g} дн.")
        announced = [d for d in deals if d.announced_defect and d.defect_confirmed in ("да", "нет")]
        if announced:
            false = sum(1 for d in announced if d.defect_confirmed == "нет")
            lines.append(f"Объявленные «Major … Defect»: не подтвердились {false} из {len(announced)}")
        lines.append(f"Подсказки для config/costs.json: подготовка по факту ≈ {statistics.median(d.prep for d in deals):,.0f} "
                     f"(в detailing_usd — с запасом: при большом обороте мыть самому не получится), "
                     f"ремонт по факту ≈ {statistics.median(d.repair for d in deals):,.0f} (recon_default_usd — резерв на скрытое), "
                     f"plan.prep_days ≈ {statistics.median(d.days_to_list for d in deals):g}, дни продажи ≈ {statistics.median(d.days_listed for d in deals):g}"
                     + (" — мало сделок, это ещё не статистика" if len(deals) < 5 else ""))
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lot_analyzer.deals", description="Журнал сделок и сверка настроек.")
    sub = parser.add_subparsers(dest="cmd")
    add = sub.add_parser("add", help="добавить сделку")
    for f in fields(Deal):
        if f.name == "no_photos":
            add.add_argument("--no-photos", action="store_true")
        else:
            add.add_argument("--" + f.name.replace("_", "-"), type=(int if f.type == "int" else float) if f.name in NUMERIC else str,
                             default=(0 if f.name in NUMERIC else ""))
    args = parser.parse_args(argv)
    deals = load()
    if args.cmd == "add":
        values = {f.name: getattr(args, f.name) for f in fields(Deal) if f.name != "no_photos"}
        values["no_photos"] = "да" if args.no_photos else ""
        deals.append(Deal(**values))
        save(deals)
        print(f"Добавлено в {DEALS_PATH}")
    for line in report(deals, load_costs()) or ["Сделок пока нет: python3 -m lot_analyzer.deals add …"]:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
