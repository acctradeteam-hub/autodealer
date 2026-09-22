"""Конфигурация оценщика: значения по умолчанию, загрузка и слияние с файлом.

ВНИМАНИЕ: все денежные значения здесь — ПЛЕЙСХОЛДЕРЫ, а не реальные ставки
дилера. Их нужно заменить на свои в data/example/config.json (или в своём
конфиге) до использования расчёта в торгах.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

PLACEHOLDER_NOTICE = (
    "Значения по умолчанию — плейсхолдеры. Замените на свои ставки "
    "(сборы аукциона, $/милю, подготовка, минимальная прибыль)."
)

DEFAULT_CONFIG = {
    "_placeholder_notice": PLACEHOLDER_NOTICE,
    "currency": "USD",
    "market": {
        "year_window": 2,
        "max_comp_distance_miles": 150,
        "min_comps_required": 3,
        "outlier_mad_k": 2.5,
        "outlier_min_sample": 5,
        "mileage_adjustment_per_mile_usd": 0.06,
        "mileage_adjustment_cap_pct": 12.0,
        "condition_multipliers": {"A": 1.03, "B": 1.0, "C": 0.95, "D": 0.88},
        "target_deal_rating": "good",
        "quick_sale_discount_pct": 3.0,
        "supply_thresholds": {"high_supply_min_listings": 12, "low_supply_max_listings": 4},
        "supply_price_adjustment_pct": {"HIGH": 0.0, "MEDIUM": 0.0, "LOW": 0.0},
    },
    "auction_fees": {
        "buyer_fee_tiers": [
            {"up_to_usd": 1000, "fee_usd": 200},
            {"up_to_usd": 5000, "fee_usd": 500},
            {"up_to_usd": 10000, "fee_usd": 750},
            {"up_to_usd": 20000, "fee_usd": 1000},
            {"up_to_usd": None, "fee_usd": 1250},
        ],
        "gate_fee_usd": 79.0,
        "internet_bid_fee_usd": 85.0,
        "doc_fee_usd": 95.0,
    },
    "transport": {
        "rate_tiers_usd_per_mile": [
            {"up_to_miles": 100, "rate_usd": 2.20},
            {"up_to_miles": 500, "rate_usd": 1.15},
            {"up_to_miles": None, "rate_usd": 0.85},
        ],
        "minimum_charge_usd": 225.0,
        "prefer_carrier_quote": True,
    },
    "recon": {
        "base_by_condition": {"A": 350.0, "B": 750.0, "C": 1500.0, "D": 2600.0},
        "always_included": {"detail": 150.0, "safety_inspection": 90.0},
        "title_and_registration_usd": 185.0,
    },
    "selling": {
        "marketplace_listing_usd": 120.0,
        "floorplan_usd_per_day": 8.0,
        "expected_days_to_sell": 30,
    },
    "margin": {
        "min_profit_usd": 2000.0,
        "profit_pct_of_market_price": 12.0,
    },
    "rules": {
        "allowed_titles": ["clean"],
        "min_bid_floor_usd": 500.0,
        "bid_rounding_usd": 25.0,
    },
    "column_aliases": {
        "lots": {},
        "comps": {},
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: str | Path | None) -> dict:
    """Читает JSON-конфиг и накладывает его поверх значений по умолчанию."""
    if path is None:
        return copy.deepcopy(DEFAULT_CONFIG)
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Файл конфигурации не найден: {p}")
    user_cfg = json.loads(p.read_text(encoding="utf-8"))
    cfg = _deep_merge(DEFAULT_CONFIG, user_cfg)
    validate_config(cfg)
    return cfg


def validate_config(cfg: dict) -> list[str]:
    """Возвращает список проблем конфигурации; пустой список — всё в порядке."""
    problems: list[str] = []
    margin = cfg["margin"]
    if margin["min_profit_usd"] < 0:
        problems.append("margin.min_profit_usd не может быть отрицательной")
    if margin["profit_pct_of_market_price"] < 0:
        problems.append("margin.profit_pct_of_market_price не может быть отрицательным")
    tiers = cfg["auction_fees"]["buyer_fee_tiers"]
    if not tiers or tiers[-1].get("up_to_usd") is not None:
        problems.append("последний порог buyer_fee_tiers должен иметь up_to_usd = null")
    rate_tiers = cfg["transport"]["rate_tiers_usd_per_mile"]
    if not rate_tiers or rate_tiers[-1].get("up_to_miles") is not None:
        problems.append("последний порог rate_tiers_usd_per_mile должен иметь up_to_miles = null")
    if cfg["market"]["min_comps_required"] < 1:
        problems.append("market.min_comps_required должен быть >= 1")
    if problems:
        raise ValueError("Ошибки конфигурации: " + "; ".join(problems))
    return problems
