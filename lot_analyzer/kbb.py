"""Прикидка KBB Private Party (ZIP 92620, Good), пока официальную цену не посмотрели на kbb.com.

KBB PP — официальная цена Kelley Blue Book, программа её не заменяет: это ориентир
до проверки на kbb.com (или навыком «KBB PP 92620», который пишет её в заметку).

Три источника по убыванию точности:
  1. «точно» — KBB уже записан у этой машины (заметка, таблица);
  2. «по KBB похожих» — ваши же KBB у той же марки/модели ±2 года из истории
     (data/market_history.csv), пересчитанные на год и пробег этой машины;
  3. «по рынку» — цены продажи той же марки/модели ±2 года на торгах, пересчитанные
     на год и пробег и делённые на долю «цена ÷ KBB» (market.kbb_clean).

Пересчёт на год и пробег: × (1 + age_per_year) за каждый год разницы и
× (пробег / пробег похожей) ^ miles_elasticity. Коэффициенты — из регрессии
цен CarMax 29.09.2026 (−10% за год, эластичность по пробегу −0.4), меняются в
config/costs.json → kbb_estimate. Это ориентир, а не KBB: точность проверяется
командой `python3 -m lot_analyzer.kbb` (оценка каждой известной машины по остальным).
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from pathlib import Path

from .market import HEAVY, HISTORY_PATH, Result, load_history
from .normalize import parse_money


@dataclass
class Comp:
    make: str
    model: str
    year: int
    miles: float
    value: float          # KBB или цена продажи
    vin: str = ""


@dataclass
class Estimate:
    value: float
    source: str           # «точно», «по KBB N похожих», «по рынку: N продаж»
    n: int
    spread: float         # разброс пересчитанных значений (межквартильный, доля)


def model_key(make: str, model: str) -> tuple[str, str]:
    first = (model or "").lower().replace("-", "").split()
    return (make or "").lower().strip(), first[0] if first else ""


def _split_vehicle(vehicle: str) -> tuple[str, str]:
    words = (vehicle or "").split()
    return (words[0] if words else ""), " ".join(words[1:])


class KbbEstimator:
    def __init__(self, records: list[Result], costs: dict) -> None:
        cfg = costs.get("kbb_estimate") or {}
        self.age = float(cfg.get("age_per_year", -0.10))
        self.elasticity = float(cfg.get("miles_elasticity", -0.40))
        self.max_years = int(cfg.get("max_year_gap", 2))
        self.min_comps = int(cfg.get("min_comps", 2))
        self.share = float((costs.get("market") or {}).get("kbb_clean") or 0.78)
        self.kbb: dict[tuple[str, str], list[Comp]] = {}
        self.sold: dict[tuple[str, str], list[Comp]] = {}
        self.by_vin: dict[str, float] = {}
        for rec in records:
            make, model = _split_vehicle(rec.vehicle)
            year = int(rec.year) if rec.year.isdigit() else None
            miles = float(rec.miles) if rec.miles.replace(".", "").isdigit() else None
            if not year or not miles:
                continue
            kbb = parse_money(rec.kbb)
            if kbb and kbb > 500:
                comp = Comp(make, model, year, miles, kbb, rec.vin)
                self.kbb.setdefault(model_key(make, model), []).append(comp)
                if rec.vin:
                    self.by_vin[rec.vin] = kbb
            price = parse_money(rec.price)
            if price and price >= 500 and rec.status.startswith("Sold") and not HEAVY.search(f"{rec.announcements} {rec.notes}"):
                self.sold.setdefault(model_key(make, model), []).append(Comp(make, model, year, miles, price, rec.vin))

    def copy(self) -> "KbbEstimator":
        clone = KbbEstimator.__new__(KbbEstimator)
        clone.__dict__.update(self.__dict__)
        clone.kbb = {k: list(v) for k, v in self.kbb.items()}
        clone.by_vin = dict(self.by_vin)
        return clone

    def add_rows(self, rows: list[dict[str, str]]) -> None:
        """KBB из заметок текущих страниц тоже становится «похожими» для остальных машин."""
        for row in rows:
            kbb = parse_money(row.get("kbb_private_party_usd"))
            year = row.get("year", "")
            miles = parse_money(row.get("odometer_miles"))
            if kbb and kbb > 500 and str(year).isdigit() and miles:
                comp = Comp(row.get("make", ""), row.get("model", ""), int(year), miles, kbb, row.get("vin", ""))
                self.kbb.setdefault(model_key(comp.make, comp.model), []).append(comp)
                if comp.vin:
                    self.by_vin.setdefault(comp.vin, kbb)

    @classmethod
    def from_history(cls, costs: dict, path: Path = HISTORY_PATH) -> "KbbEstimator":
        return cls(load_history(path), costs)

    def _adjust(self, comp: Comp, year: int, miles: float) -> float:
        value = comp.value * (1 + self.age) ** (comp.year - year)          # моложе — дороже
        return value * (max(miles, 1000) / max(comp.miles, 1000)) ** self.elasticity

    def _from(self, comps: list[Comp], year: int, miles: float, exclude_vin: str) -> list[float]:
        near = [c for c in comps if abs(c.year - year) <= self.max_years and c.vin != exclude_vin or (not c.vin and abs(c.year - year) <= self.max_years)]
        near = [c for c in near if not (exclude_vin and c.vin == exclude_vin)]
        return [self._adjust(c, year, miles) for c in near]

    def estimate(self, make: str, model: str, year: int | None, miles: float | None, vin: str = "", exclude_vin: str = "") -> Estimate | None:
        if vin and vin != exclude_vin and vin in self.by_vin:
            return Estimate(self.by_vin[vin], "точно (KBB этой машины)", 1, 0.0)
        if not year or not miles:
            return None
        key = model_key(make, model)
        for comps, label, divisor in ((self.kbb.get(key, []), "по KBB похожих", 1.0), (self.sold.get(key, []), "по рынку: продаж", self.share)):
            values = self._from(comps, year, miles, exclude_vin)
            if len(values) >= self.min_comps:
                values.sort()
                n = len(values)
                median = statistics.median(values) / divisor
                spread = (values[(3 * n) // 4] - values[n // 4]) / statistics.median(values) if n >= 4 else float("nan")
                return Estimate(median, f"{label} {n}", n, spread)
        return None


def validate(records: list[Result], costs: dict) -> None:
    """Каждая машина с известным KBB оценивается по остальным — насколько ошибается оценка."""
    estimator = KbbEstimator(records, costs)
    errors: dict[str, list[float]] = {"по KBB похожих": [], "по рынку": []}
    for rec in records:
        kbb = parse_money(rec.kbb)
        if not kbb or not rec.year.isdigit() or not rec.miles.isdigit():
            continue
        make, model = _split_vehicle(rec.vehicle)
        est = estimator.estimate(make, model, int(rec.year), float(rec.miles), exclude_vin=rec.vin or "-")
        if est:
            kind = "по KBB похожих" if est.source.startswith("по KBB") else "по рынку"
            errors[kind].append(abs(est.value - kbb) / kbb)
    for kind, errs in errors.items():
        if errs:
            errs.sort()
            print(f"{kind}: машин {len(errs)}, ошибка — медиана {statistics.median(errs):.0%}, у 80% не больше {errs[int(0.8 * len(errs)) - 1]:.0%}")
        else:
            print(f"{kind}: не хватает данных")


def main() -> int:
    from .bid import load_costs

    records = load_history()
    print(f"История: {len(records)} записей, из них с KBB: {sum(1 for r in records if r.kbb)}")
    validate(records, load_costs())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
