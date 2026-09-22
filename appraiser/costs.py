"""Расчёт затрат: сборы аукциона, доставка, подготовка, расходы на продажу."""
from __future__ import annotations

from dataclasses import dataclass, field

from .io_tables import norm_text, to_float


@dataclass
class TransportCost:
    amount: float | None
    miles: float | None = None
    source: str = ""
    carrier_name: str | None = None
    carrier_contact: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class ReconCost:
    amount: float
    breakdown: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def buyer_fee(hammer_price: float, cfg: dict) -> float:
    """Сбор аукциона по ступенчатой шкале от цены молотка."""
    for tier in cfg["auction_fees"]["buyer_fee_tiers"]:
        limit = tier.get("up_to_usd")
        if limit is None or hammer_price <= limit:
            return float(tier["fee_usd"])
    return float(cfg["auction_fees"]["buyer_fee_tiers"][-1]["fee_usd"])


def fixed_auction_fees(cfg: dict) -> dict[str, float]:
    fees = cfg["auction_fees"]
    return {
        "gate_fee": float(fees["gate_fee_usd"]),
        "internet_bid_fee": float(fees["internet_bid_fee_usd"]),
        "doc_fee": float(fees["doc_fee_usd"]),
    }


def resolve_miles(lot: dict, lanes: list[dict]) -> tuple[float | None, str]:
    """Расстояние: сначала колонка лота, затем таблица маршрутов lanes.csv."""
    direct = to_float(lot.get("distance_miles"))
    if direct is not None:
        return direct, "колонка distance_miles в лоте"
    origin = norm_text(lot.get("auction_location"))
    destination = norm_text(lot.get("destination") or lot.get("storage_location"))
    for lane in lanes:
        if norm_text(lane.get("origin")) != origin:
            continue
        lane_destination = norm_text(lane.get("destination"))
        if destination and lane_destination != destination:
            continue
        miles = to_float(lane.get("miles"))
        if miles is not None:
            return miles, f"таблица маршрутов: {lane.get('origin')} -> {lane.get('destination')}"
    return None, "расстояние не найдено"


def rate_for_miles(miles: float, cfg: dict) -> float:
    for tier in cfg["transport"]["rate_tiers_usd_per_mile"]:
        limit = tier.get("up_to_miles")
        if limit is None or miles <= limit:
            return float(tier["rate_usd"])
    return float(cfg["transport"]["rate_tiers_usd_per_mile"][-1]["rate_usd"])


def _match_carrier(lot: dict, carriers: list[dict]) -> list[dict]:
    origin = norm_text(lot.get("auction_location"))
    destination = norm_text(lot.get("destination") or lot.get("storage_location"))
    matches = []
    for carrier in carriers:
        carrier_origin = norm_text(carrier.get("origin"))
        carrier_destination = norm_text(carrier.get("destination"))
        if carrier_origin not in {origin, "*", ""}:
            continue
        if destination and carrier_destination not in {destination, "*", ""}:
            continue
        if to_float(carrier.get("rate_usd")) is not None:
            matches.append(carrier)
    return matches


def transport_cost(lot: dict, carriers: list[dict], lanes: list[dict], cfg: dict) -> TransportCost:
    """Стоимость доставки: котировка перевозчика по направлению или тариф $/милю."""
    transport_cfg = cfg["transport"]
    miles, miles_source = resolve_miles(lot, lanes)
    result = TransportCost(amount=None, miles=miles)

    override = to_float(lot.get("transport_override_usd"))
    if override is not None:
        result.amount = override
        result.source = "сумма указана в строке лота (transport_override_usd)"
        return result

    matches = _match_carrier(lot, carriers)
    if transport_cfg["prefer_carrier_quote"] and matches:
        best = min(matches, key=lambda c: to_float(c.get("rate_usd")))
        result.amount = to_float(best.get("rate_usd"))
        result.source = "ставка перевозчика по направлению"
        result.carrier_name = best.get("carrier")
        result.carrier_contact = best.get("phone") or best.get("email")
        return result

    if miles is None:
        result.notes.append(
            "нет расстояния и нет ставки перевозчика: заполните distance_miles в лоте "
            "или маршрут в lanes.csv"
        )
        return result

    calculated = miles * rate_for_miles(miles, cfg)
    result.amount = round(max(calculated, float(transport_cfg["minimum_charge_usd"])), 2)
    rate = rate_for_miles(miles, cfg)
    result.source = f"тариф {rate:.2f} $/миля x {miles:.0f} миль ({miles_source})"
    if calculated < transport_cfg["minimum_charge_usd"]:
        result.notes.append(
            f"применён минимальный чек за рейс {transport_cfg['minimum_charge_usd']:.0f} $"
        )
    return result


def recon_cost(lot: dict, cfg: dict) -> ReconCost:
    """Подготовка: базовая сумма по состоянию + обязательные работы, либо сумма из лота."""
    recon_cfg = cfg["recon"]
    override = to_float(lot.get("recon_override_usd"))
    if override is not None:
        return ReconCost(
            amount=override,
            breakdown={"указано в строке лота": override},
            notes=["сумма подготовки взята из колонки recon_override_usd"],
        )

    grade = str(lot.get("condition_grade") or "").strip().upper()
    base = recon_cfg["base_by_condition"].get(grade)
    notes: list[str] = []
    if base is None:
        worst = max(recon_cfg["base_by_condition"].values())
        notes.append(
            f"состояние '{lot.get('condition_grade') or '—'}' неизвестно, взята худшая ставка {worst:.0f} $"
        )
        base = worst

    breakdown = {"base_by_condition": float(base)}
    breakdown.update({k: float(v) for k, v in recon_cfg["always_included"].items()})
    breakdown["title_and_registration"] = float(recon_cfg["title_and_registration_usd"])
    return ReconCost(amount=round(sum(breakdown.values()), 2), breakdown=breakdown, notes=notes)


def selling_costs(cfg: dict) -> dict[str, float]:
    selling = cfg["selling"]
    floorplan = float(selling["floorplan_usd_per_day"]) * int(selling["expected_days_to_sell"])
    return {
        "marketplace_listing": float(selling["marketplace_listing_usd"]),
        "floorplan": round(floorplan, 2),
    }
