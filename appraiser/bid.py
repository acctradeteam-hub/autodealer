"""Итоговая максимальная ставка: запас дилера и решение уравнения со сбором аукциона."""
from __future__ import annotations

from dataclasses import dataclass, field

from .costs import buyer_fee


@dataclass
class BidResult:
    max_bid: float
    buyer_fee: float
    holdback: float
    holdback_basis: str
    fixed_costs: float
    total_out_the_door: float
    notes: list[str] = field(default_factory=list)


def holdback_amount(market_price: float, cfg: dict) -> tuple[float, str]:
    """Запас дилера: минимальная прибыль в долларах или процент — берём большее."""
    margin = cfg["margin"]
    floor = float(margin["min_profit_usd"])
    pct_value = market_price * float(margin["profit_pct_of_market_price"]) / 100.0
    if pct_value >= floor:
        return round(pct_value, 2), f"{margin['profit_pct_of_market_price']}% от цены продажи"
    return round(floor, 2), f"минимальная прибыль {floor:,.0f} $"


def solve_max_bid(market_price: float, fixed_costs: float, holdback: float, cfg: dict) -> BidResult:
    """Находит наибольшую ставку, при которой цена + все затраты + запас укладываются в рынок.

    Сбор аукциона зависит от самой ставки (ступенчатая шкала), поэтому ставка
    подбирается делением отрезка пополам, а не вычитается «в лоб».
    """
    budget = market_price - fixed_costs - holdback
    notes: list[str] = []
    if budget <= 0:
        return BidResult(
            max_bid=0.0,
            buyer_fee=0.0,
            holdback=holdback,
            holdback_basis="",
            fixed_costs=round(fixed_costs, 2),
            total_out_the_door=round(fixed_costs + holdback, 2),
            notes=["затраты и запас уже превышают цену продажи — ставка невозможна"],
        )

    low, high = 0.0, budget
    for _ in range(60):
        mid = (low + high) / 2
        if mid + buyer_fee(mid, cfg) <= budget:
            low = mid
        else:
            high = mid

    step = float(cfg["rules"]["bid_rounding_usd"])
    bid = (low // step) * step if step > 0 else low
    if bid + buyer_fee(bid, cfg) > budget and bid >= step:
        bid -= step
        notes.append("ставка снижена на один шаг: округление вверх выходило за бюджет")

    fee = buyer_fee(bid, cfg) if bid > 0 else 0.0
    return BidResult(
        max_bid=round(bid, 2),
        buyer_fee=round(fee, 2),
        holdback=holdback,
        holdback_basis="",
        fixed_costs=round(fixed_costs, 2),
        total_out_the_door=round(bid + fee + fixed_costs, 2),
        notes=notes,
    )
