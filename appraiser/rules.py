"""Стоп-правила: что отсекаем до расчёта и что отправляем на ручную проверку."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .io_tables import norm_text, to_int

DECISION_REJECT = "ОТКАЗ"
DECISION_MANUAL = "РУЧНАЯ ПРОВЕРКА"
DECISION_SKIP = "ПРОПУСТИТЬ"
DECISION_BID = "ТОРГОВАТЬСЯ"


@dataclass
class RuleHit:
    decision: str
    reason: str


def match_threshold(lot: dict, thresholds: list[dict]) -> dict | None:
    """Пороги по модели: точная пара марка+модель, затем марка+*, затем *+*."""
    make = norm_text(lot.get("make"))
    model = norm_text(lot.get("model"))
    candidates = [(make, model), (make, "*"), ("*", "*")]
    for want_make, want_model in candidates:
        for row in thresholds:
            if norm_text(row.get("make")) == want_make and norm_text(row.get("model")) == want_model:
                return row
    return None


def pre_bid_checks(lot: dict, thresholds: list[dict], cfg: dict, today: date | None = None) -> list[RuleHit]:
    """Проверки до расчёта: титул и пороги пробега/возраста по модели."""
    hits: list[RuleHit] = []
    today = today or date.today()

    title = norm_text(lot.get("title_status"))
    allowed = {norm_text(t) for t in cfg["rules"]["allowed_titles"]}
    if not title:
        hits.append(RuleHit(DECISION_MANUAL, "не указан тип титула"))
    elif title not in allowed:
        hits.append(RuleHit(DECISION_REJECT, f"титул '{lot.get('title_status')}' вне списка разрешённых"))

    row = match_threshold(lot, thresholds)
    if row is None:
        hits.append(RuleHit(DECISION_MANUAL, "нет порогов пробега/возраста для этой модели"))
        return hits

    mileage = to_int(lot.get("mileage"))
    max_mileage = to_int(row.get("max_mileage"))
    if mileage is not None and max_mileage is not None and mileage > max_mileage:
        hits.append(
            RuleHit(DECISION_REJECT, f"пробег {mileage:,} миль выше порога {max_mileage:,} для модели")
        )

    year = to_int(lot.get("year"))
    max_age = to_int(row.get("max_age_years"))
    if year is not None and max_age is not None:
        age = today.year - year
        if age > max_age:
            hits.append(RuleHit(DECISION_REJECT, f"возраст {age} лет выше порога {max_age} для модели"))
    return hits


def post_bid_checks(estimate, bid_result, cfg: dict) -> list[RuleHit]:
    """Проверки после расчёта: хватает ли данных и не слишком ли мала ставка."""
    hits: list[RuleHit] = []
    min_comps = cfg["market"]["min_comps_required"]
    if estimate.comps_used < min_comps:
        hits.append(
            RuleHit(
                DECISION_MANUAL,
                f"конкурентов взято {estimate.comps_used} из {estimate.comps_found} найденных, "
                f"нужно минимум {min_comps}",
            )
        )
        return hits
    if estimate.price is None:
        hits.append(RuleHit(DECISION_MANUAL, "рыночная цена не рассчитана"))
        return hits
    if bid_result is None:
        return hits
    floor = float(cfg["rules"]["min_bid_floor_usd"])
    if bid_result.max_bid <= 0:
        hits.append(RuleHit(DECISION_SKIP, "расчётная ставка нулевая или отрицательная"))
    elif bid_result.max_bid < floor:
        hits.append(RuleHit(DECISION_SKIP, f"ставка {bid_result.max_bid:,.0f} $ ниже порога {floor:,.0f} $"))
    return hits


def final_decision(hits: list[RuleHit]) -> str:
    """Приоритет решений: отказ > пропустить > ручная проверка > торговаться."""
    decisions = {hit.decision for hit in hits}
    for decision in (DECISION_REJECT, DECISION_SKIP, DECISION_MANUAL):
        if decision in decisions:
            return decision
    return DECISION_BID
