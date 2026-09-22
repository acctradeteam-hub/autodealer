"""Сборка расчёта по одному лоту: правила -> рынок -> затраты -> ставка."""
from __future__ import annotations

from datetime import date

from . import costs as costs_mod
from . import rules as rules_mod
from .bid import holdback_amount, solve_max_bid
from .market import estimate_market_price
from .rules import DECISION_BID, DECISION_MANUAL, DECISION_REJECT


def evaluate_lot(lot: dict, comps: list[dict], ranges: list[dict], thresholds: list[dict],
                 carriers: list[dict], lanes: list[dict], cfg: dict, today: date | None = None) -> dict:
    """Возвращает строку результата по лоту вместе с объектами для карточки."""
    lot_id = str(lot.get("lot_id") or lot.get("vin") or "БЕЗ-НОМЕРА")
    hits = rules_mod.pre_bid_checks(lot, thresholds, cfg, today=today)
    transport = costs_mod.transport_cost(lot, carriers, lanes, cfg)
    recon = costs_mod.recon_cost(lot, cfg)

    result: dict = {
        "lot_id": lot_id,
        "vin": lot.get("vin", ""),
        "year": lot.get("year", ""),
        "make": lot.get("make", ""),
        "model": lot.get("model", ""),
        "mileage": lot.get("mileage", ""),
        "condition_grade": lot.get("condition_grade", ""),
        "title_status": lot.get("title_status", ""),
        "_lot": lot,
        "_transport": transport,
        "_recon": recon,
        "_bid": None,
        "other_fees_usd_raw": None,
        "selling_costs_usd_raw": None,
        "holdback_basis": "",
    }

    # Отказ по титулу или порогам модели: рынок и затраты дальше не считаем.
    if any(hit.decision == DECISION_REJECT for hit in hits):
        estimate = estimate_market_price(lot, [], ranges, cfg)
        estimate.notes.insert(0, "расчёт не выполнялся: лот отсечён стоп-правилом")
        result["_estimate"] = estimate
        result["_market_skipped"] = True
        result["decision"] = DECISION_REJECT
        result["reasons"] = "; ".join(h.reason for h in hits if h.decision == DECISION_REJECT)
        result["comps_found"] = 0
        result["comps_used"] = 0
        result["supply_label"] = ""
        return result

    estimate = estimate_market_price(lot, comps, ranges, cfg)
    result["_estimate"] = estimate

    bid_result = None
    if estimate.price is not None and transport.amount is not None:
        fixed_fees = costs_mod.fixed_auction_fees(cfg)
        selling = costs_mod.selling_costs(cfg)
        other_fees_total = sum(fixed_fees.values())
        selling_total = sum(selling.values())
        holdback, basis = holdback_amount(estimate.price, cfg)
        fixed_costs = transport.amount + recon.amount + other_fees_total + selling_total
        bid_result = solve_max_bid(estimate.price, fixed_costs, holdback, cfg)
        bid_result.holdback_basis = basis
        result.update({
            "_bid": bid_result,
            "other_fees_usd_raw": round(other_fees_total, 2),
            "selling_costs_usd_raw": round(selling_total, 2),
            "holdback_basis": basis,
        })
    elif transport.amount is None:
        hits.append(rules_mod.RuleHit(DECISION_MANUAL, "не удалось рассчитать доставку"))

    hits += rules_mod.post_bid_checks(estimate, bid_result, cfg)
    decision = rules_mod.final_decision(hits)

    result.update({
        "decision": decision,
        "reasons": "; ".join(hit.reason for hit in hits),
        "market_price_usd": estimate.price,
        "max_bid_usd": bid_result.max_bid if (bid_result and decision == DECISION_BID) else None,
        "buyer_fee_usd": bid_result.buyer_fee if bid_result else None,
        "transport_usd": transport.amount,
        "recon_usd": recon.amount,
        "other_fees_usd": result["other_fees_usd_raw"],
        "selling_costs_usd": result["selling_costs_usd_raw"],
        "holdback_usd": bid_result.holdback if bid_result else None,
        "comps_found": estimate.comps_found,
        "comps_used": estimate.comps_used,
        "supply_label": estimate.supply_label,
        "price_range_p25_p75": (
            f"{estimate.price_p25:,.0f}–{estimate.price_p75:,.0f}"
            if estimate.price_p25 is not None else ""
        ),
        "avg_days_on_market": estimate.avg_days_on_market,
        "carrier": transport.carrier_name or "",
        "carrier_contact": transport.carrier_contact or "",
    })
    return result
