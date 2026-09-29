"""Планировщик: какой сегмент и какая ставка дают больше прибыли при вашем капитале.

    python3 -m lot_analyzer.plan --capital 20000 --bids 15

Для каждого сегмента (KBB PP) и уровня ставки (доля от KBB) считается:
  * шанс выиграть — по кривой «цена ÷ KBB» из ваших результатов торгов
    (config/costs.json → market.kbb_clean_curve); лоты дешевле floor × KBB
    не считаются удачей — это почти всегда машины со скрытыми проблемами;
  * прибыль с машины — продажа по KBB − $500 минус ставка, сбор аукциона и все
    расходы из настроек (ремонт, детейлинг, смог, дилер, содержание, резерв);
  * машин в месяц — меньшее из «ставок в неделю × 4.3 × шанс» и того, сколько машин
    капитал успевает прокрутить за месяц (цикл = подготовка + дни продажи).
Дни продажи по цене — допущение (plan.sale_days_by_kbb), уточните по своим сделкам.
"""

from __future__ import annotations

import argparse

from .bid import auction_fee, load_costs, target_profit, win_chance

DEFAULT_PLAN = {
    "prep_days": 7,                                  # забрать, подготовить, выставить
    "sale_days_by_kbb": [[8000, 14], [12000, 18], [99999999, 25]],
    "floor_ratio": 0.6,
    "auction": "CarMax",
    "segments": [6000, 7000, 8000, 9000, 10000, 11000, 12000, 13000, 14000, 15000],
    "reg227_market_discount": 0.06,                  # регрессия 29.09: «титул / 227» −6% к цене
}


def sale_days(kbb: float, plan: dict) -> int:
    for upto, days in plan["sale_days_by_kbb"]:
        if kbb <= upto:
            return int(days)
    return int(plan["sale_days_by_kbb"][-1][1])


def car_economics(kbb: float, ratio: float, costs: dict, plan: dict, extra_days: int = 0) -> tuple[float, float, int]:
    """(вложено в машину, прибыль, дней цикла) при ставке ratio × KBB."""
    days = plan["prep_days"] + sale_days(kbb, plan) + extra_days
    sale = kbb * float(costs.get("kbb_private_party_factor", 1.0)) + float(costs.get("kbb_private_party_offset_usd", 0))
    bid = ratio * kbb
    fee = auction_fee(bid, plan["auction"], costs)
    fixed = (float(costs.get("recon_default_usd", 0)) + float(costs.get("detailing_usd", 0)) + float(costs.get("smog_usd", 0))
             + float(costs.get("dealer_fee_usd", 0)) + float(costs.get("selling_usd", 0)) + float(costs.get("transport_usd", 0))
             + days * float(costs.get("holding_per_day_usd", 0)) + sale * float(costs.get("reserve_pct_of_sale", 0)))
    invested = bid + fee + fixed
    return invested, sale - invested, days


def good_chance(ratio: float, curve: list, floor: float) -> float:
    """Доля лотов, которые уходят в диапазоне [floor, ratio] × KBB — «нормальные» выигрыши."""
    if not curve:
        return 0.0
    return max(0.0, (win_chance(ratio, 1.0, curve) or 0) - (win_chance(floor, 1.0, curve) or 0))


def best_plans(capital: float, bids_per_week: float, costs: dict, plan: dict, extra_days: int = 0, discount: float = 0.0):
    curve = (costs.get("market") or {}).get("kbb_clean_curve") or []
    rows = []
    for kbb in plan["segments"]:
        best = None
        for step in range(60, 91):
            ratio = step / 100
            invested, profit, days = car_economics(kbb, ratio, costs, plan, extra_days)
            if profit <= 0 or invested > capital:
                continue
            chance = good_chance(ratio / (1 - discount) if discount else ratio, curve, plan["floor_ratio"])
            capacity = capital / invested * 30 / days
            cars = min(bids_per_week * 4.3 * chance, capacity)
            monthly = cars * profit
            if best is None or monthly > best["monthly"]:
                best = dict(kbb=kbb, ratio=ratio, bid=ratio * kbb, chance=chance, profit=profit, cars=cars,
                            monthly=monthly, invested=invested, days=days, capacity=capacity,
                            target=target_profit(kbb - 500, costs))
        if best:
            rows.append(best)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lot_analyzer.plan", description="Сегмент и ставка под ваш капитал.")
    parser.add_argument("--capital", type=float, nargs="+", default=[10000, 20000, 30000, 50000], help="замороженные в машинах деньги, $")
    parser.add_argument("--bids", type=float, default=15, help="ставок в неделю")
    args = parser.parse_args(argv)
    costs = load_costs()
    plan = dict(DEFAULT_PLAN, **(costs.get("plan") or {}))
    if not (costs.get("market") or {}).get("kbb_clean_curve"):
        print("Нет кривой market.kbb_clean_curve — сначала: python3 -m lot_analyzer.market <результаты> --update-config")
        return 1
    for capital in args.capital:
        print(f"\nКапитал ${capital:,.0f}, ставок в неделю {args.bids:g}")
        print("  KBB PP   ставка        шанс  прибыль/маш  цикл  машин/мес (предел капитала)  в месяц   ваша цель по ступеням")
        rows = best_plans(capital, args.bids, costs, plan)
        for r in rows:
            print(f"  ${r['kbb']:>6,.0f}  {r['ratio']:.2f}×=${r['bid']:>6,.0f}  {r['chance']:>4.0%}  ${r['profit']:>9,.0f}  {r['days']:>3} дн  "
                  f"{r['cars']:>4.1f} ({r['capacity']:.1f})             ${r['monthly']:>7,.0f}   ${r['target']:,.0f}")
        if rows:
            top = max(rows, key=lambda r: r["monthly"])
            print(f"  → лучше всего: KBB ~${top['kbb']:,.0f}, ставка до {top['ratio']:.2f}×KBB (${top['bid']:,.0f}), "
                  f"~{top['cars']:.1f} машин/мес, ~${top['monthly']:,.0f}/мес, {top['monthly'] / capital:.0%} на капитал в месяц")
        r227 = best_plans(capital, args.bids, costs, plan, extra_days=int(costs.get("reg227_extra_days", 10)),
                          discount=float(plan["reg227_market_discount"]))
        if r227:
            top = max(r227, key=lambda r: r["monthly"])
            print(f"  → с REG 227 (+{costs.get('reg227_extra_days', 10)} дн., рынок −{plan['reg227_market_discount']:.0%}): KBB ~${top['kbb']:,.0f}, "
                  f"ставка до {top['ratio']:.2f}×KBB, ~${top['monthly']:,.0f}/мес")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
