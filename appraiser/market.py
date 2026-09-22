"""Оценка цены быстрой продажи по объявлениям конкурентов и коридору CarGurus."""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from .io_tables import model_key, norm_text, to_float, to_int


@dataclass
class MarketEstimate:
    price: float | None
    base_median: float | None = None
    median_mileage: float | None = None
    mileage_adjustment: float = 0.0
    condition_adjustment: float = 0.0
    supply_adjustment: float = 0.0
    corridor_cap: float | None = None
    corridor_rating: str | None = None
    corridor_applied: bool = False
    quick_sale_discount: float = 0.0
    comps_found: int = 0
    comps_used: int = 0
    comps_dropped_outliers: int = 0
    price_min: float | None = None
    price_max: float | None = None
    price_p25: float | None = None
    price_p75: float | None = None
    avg_days_on_market: float | None = None
    supply_label: str = "UNKNOWN"
    used_comp_ids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _percentile(values: list[float], pct: float) -> float:
    """Линейная интерполяция перцентиля (без numpy)."""
    if not values:
        raise ValueError("пустой список значений")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * pct / 100.0
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def select_comps(lot: dict, comps: list[dict], cfg: dict) -> tuple[list[dict], list[str]]:
    """Отбирает объявления, подходящие лоту: модель, окно года, радиус."""
    market_cfg = cfg["market"]
    notes: list[str] = []
    lot_key = model_key(lot.get("make"), lot.get("model"))
    lot_year = to_int(lot.get("year"))
    radius = market_cfg["max_comp_distance_miles"]
    window = market_cfg["year_window"]

    selected = []
    for comp in comps:
        if model_key(comp.get("make"), comp.get("model")) != lot_key:
            continue
        comp_year = to_int(comp.get("year"))
        if lot_year is not None and comp_year is not None and abs(comp_year - lot_year) > window:
            continue
        distance = to_float(comp.get("distance_miles"))
        if distance is not None and radius is not None and distance > radius:
            continue
        if to_float(comp.get("price")) is None:
            notes.append(f"объявление {comp.get('comp_id', '?')} без цены — пропущено")
            continue
        selected.append(comp)
    return selected, notes


def _drop_outliers(comps: list[dict], cfg: dict) -> tuple[list[dict], int]:
    """Отбрасывает объявления с выбивающейся ценой, сохраняя связь со строкой.

    Фильтруются именно объявления (а не голые цены), чтобы пробег, срок экспозиции
    и список взятых comp_id считались по тем же строкам, что и медиана цены.
    """
    market_cfg = cfg["market"]
    prices = [to_float(c.get("price")) for c in comps]
    if len(comps) < market_cfg["outlier_min_sample"]:
        return comps, 0
    median = statistics.median(prices)
    mad = statistics.median([abs(p - median) for p in prices])
    if mad <= 0:
        return comps, 0
    limit = market_cfg["outlier_mad_k"] * mad
    kept = [c for c, p in zip(comps, prices) if abs(p - median) <= limit]
    return (kept, len(comps) - len(kept)) if kept else (comps, 0)


def supply_label(found: int, cfg: dict) -> str:
    thresholds = cfg["market"]["supply_thresholds"]
    if found >= thresholds["high_supply_min_listings"]:
        return "HIGH"
    if found <= thresholds["low_supply_max_listings"]:
        return "LOW"
    return "MEDIUM"


def find_deal_range(lot: dict, ranges: list[dict]) -> dict | None:
    """Ищет строку коридора CarGurus, подходящую марке/модели/году лота."""
    lot_key = model_key(lot.get("make"), lot.get("model"))
    lot_year = to_int(lot.get("year"))
    for row in ranges:
        if model_key(row.get("make"), row.get("model")) != lot_key:
            continue
        year_from = to_int(row.get("year_from"))
        year_to = to_int(row.get("year_to"))
        if lot_year is not None:
            if year_from is not None and lot_year < year_from:
                continue
            if year_to is not None and lot_year > year_to:
                continue
        return row
    return None


def estimate_market_price(lot: dict, comps: list[dict], ranges: list[dict], cfg: dict) -> MarketEstimate:
    """Считает цену, по которой машину реально продать быстро.

    Шаги: медиана конкурентов -> поправка на пробег -> поправка на состояние ->
    поправка на плотность предложения -> ограничение коридором CarGurus ->
    скидка на скорость продажи.
    """
    market_cfg = cfg["market"]
    selected, notes = select_comps(lot, comps, cfg)
    estimate = MarketEstimate(price=None, comps_found=len(selected), notes=notes)

    if not selected:
        estimate.supply_label = supply_label(0, cfg)
        estimate.notes.append("подходящих объявлений конкурентов не найдено")
        return estimate

    kept, dropped = _drop_outliers(selected, cfg)
    kept_prices = [to_float(c.get("price")) for c in kept]
    estimate.comps_dropped_outliers = dropped
    estimate.comps_used = len(kept)
    estimate.used_comp_ids = [str(c.get("comp_id", "")) for c in kept]
    estimate.supply_label = supply_label(len(selected), cfg)
    if dropped:
        estimate.notes.append(f"отброшено выбросов по цене: {dropped}")

    estimate.price_min = min(kept_prices)
    estimate.price_max = max(kept_prices)
    estimate.price_p25 = _percentile(kept_prices, 25)
    estimate.price_p75 = _percentile(kept_prices, 75)

    days = [to_float(c.get("days_on_market")) for c in kept]
    days = [d for d in days if d is not None]
    if days:
        estimate.avg_days_on_market = round(sum(days) / len(days), 1)

    if estimate.comps_used < market_cfg["min_comps_required"]:
        estimate.base_median = statistics.median(kept_prices)
        estimate.notes.append(
            f"взято объявлений {estimate.comps_used}, требуется минимум "
            f"{market_cfg['min_comps_required']} — цена не рассчитывается"
        )
        return estimate

    base = statistics.median(kept_prices)
    estimate.base_median = base

    mileages = [to_float(c.get("mileage")) for c in kept]
    mileages = [m for m in mileages if m is not None]
    lot_mileage = to_float(lot.get("mileage"))
    if mileages and lot_mileage is not None:
        estimate.median_mileage = statistics.median(mileages)
        raw_adj = (estimate.median_mileage - lot_mileage) * market_cfg["mileage_adjustment_per_mile_usd"]
        cap = base * market_cfg["mileage_adjustment_cap_pct"] / 100.0
        estimate.mileage_adjustment = round(max(-cap, min(cap, raw_adj)), 2)
        if abs(raw_adj) > cap:
            estimate.notes.append(
                f"поправка на пробег ограничена {market_cfg['mileage_adjustment_cap_pct']}% от медианы"
            )
    elif lot_mileage is None:
        estimate.notes.append("у лота не указан пробег — поправка на пробег не применена")

    price = base + estimate.mileage_adjustment

    grade = str(lot.get("condition_grade") or "").strip().upper()
    multiplier = market_cfg["condition_multipliers"].get(grade)
    if multiplier is None:
        estimate.notes.append(
            f"состояние '{lot.get('condition_grade') or '—'}' не найдено в справочнике, множитель 1.0"
        )
        multiplier = 1.0
    estimate.condition_adjustment = round(price * (multiplier - 1.0), 2)
    price += estimate.condition_adjustment

    supply_pct = market_cfg["supply_price_adjustment_pct"].get(estimate.supply_label, 0.0)
    estimate.supply_adjustment = round(price * supply_pct / 100.0, 2)
    price += estimate.supply_adjustment

    deal_row = find_deal_range(lot, ranges)
    if deal_row:
        rating = norm_text(market_cfg["target_deal_rating"])
        column = {"great": "great_deal_max", "good": "good_deal_max", "fair": "fair_deal_max"}.get(
            rating, "good_deal_max"
        )
        cap = to_float(deal_row.get(column))
        estimate.corridor_rating = rating
        estimate.corridor_cap = cap
        if cap is not None and price > cap:
            estimate.corridor_applied = True
            estimate.notes.append(
                f"цена ограничена коридором CarGurus ({rating}): {price:,.0f} -> {cap:,.0f}"
            )
            price = cap
    else:
        estimate.notes.append("коридор CarGurus для этой модели не задан")

    discount_pct = market_cfg["quick_sale_discount_pct"]
    estimate.quick_sale_discount = round(price * discount_pct / 100.0, 2)
    price -= estimate.quick_sale_discount

    estimate.price = round(price, 2)
    return estimate
