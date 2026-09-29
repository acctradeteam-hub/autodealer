"""Расчёт максимальной ставки по лоту.

Идея: от ожидаемой цены продажи отнимаем всё, что машина будет стоить сверх
ставки (сборы аукциона, ремонт, детейлинг, смог, доставка, содержание, резерв)
и целевую прибыль. Остаток — сколько можно заплатить «всё включено». Потолок
ставки — наибольшая ставка, при которой ставка + сборы аукциона в него влезают
(сбор зависит от ставки ступенями, поэтому потолок ищется перебором).

Все суммы и проценты — в config/costs.json. Отдельный запуск для одной машины:
    python3 -m lot_analyzer.bid --sale 8500 --auction Manheim --carfax "1 accident, 3 owners"
"""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from .normalize import parse_money, squeeze

DEFAULT_COSTS_PATH = Path("config/costs.json")

# Ключи строки таблицы, которые этот модуль заполняет.
CALC_KEYS = ("sale_estimate_usd", "calc_max_bid_usd", "calc_costs_usd", "calc_profit_usd", "calc_verdict", "calc_breakdown")


def load_costs(path: Path = DEFAULT_COSTS_PATH) -> dict:
    """Читает настройки расходов; пояснения (ключи с «_») отбрасываются."""
    with Path(path).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return {key: value for key, value in data.items() if not key.startswith("_")}


# ---------------------------------------------------------------- история машины

# Слова перед находкой, которые её отменяют: «No accidents reported», «без ДТП».
_NEGATION = re.compile(r"(\bno\b|\bnot\b|\bnone\b|\bwithout\b|\bzero\b|\b0\b|\bбез\b|\bнет\b|\bне\b)[\w\s/-]{0,20}$", re.I)

_SKIP_PATTERNS = {
    "branded_title": r"salvage|rebuilt|reconstructed|\bjunk\b|lemon|buy\s*back|non[\s-]*repairable|certificate of destruction|restored title|спасён|восстановленн",
    "odometer_problem": r"odometer (rollback|problem|discrepancy|tamper)|mileage (inconsistency|discrepancy)|rollback|not[\s-]*actual|\btmu\b|true mileage unknown|скрут",
    "structural_damage": r"structural (damage|alteration)|frame damage|unibody damage|frame/unibody damage|повреждени[ея] рамы|\bрам[аы]\b",
    "airbag_deployed": r"airbags? deployed|подушк\w* (безопасности )?сработал",
    "flood": r"\bflood\b|water damage|затоплен|утоплен",
    "mechanical_severe": r"engine does not crank|cranks,? does not start|does not stay running|vehicle inop|does not move|coolant intermix|не заводится",
}
_SKIP_TEXT = {
    "branded_title": "брендированный титул (salvage/rebuilt/lemon…)",
    "odometer_problem": "проблема с пробегом",
    "structural_damage": "повреждение рамы / кузова",
    "airbag_deployed": "срабатывали подушки безопасности",
    "flood": "затопление",
    "mechanical_severe": "не заводится / не едет / антифриз в масле",
}

# Не стоп-фактор, но продать машину нельзя, пока нет титула.
_TITLE_ABSENT = r"title absent|title (delay|missing)|no title|титул отсутств"


def _positive_hits(text: str, pattern: str) -> list[re.Match]:
    """Находки шаблона, перед которыми нет отрицания («no», «без», «0»)."""
    hits = []
    for match in re.finditer(pattern, text, re.I):
        before = text[max(0, match.start() - 30):match.start()]
        if not _NEGATION.search(before):
            hits.append(match)
    return hits


@dataclass
class HistoryFlags:
    skip: list[str] = field(default_factory=list)          # причины «пропустить»
    discounts: dict[str, float] = field(default_factory=dict)  # название -> доля скидки
    notes: list[str] = field(default_factory=list)


def assess_history(text: str, costs: dict) -> HistoryFlags:
    """Разбирает текст истории (титул, Carfax/AutoCheck, CR, повреждения) по ключевым словам."""
    flags = HistoryFlags()
    text = squeeze(text)
    if not text:
        flags.notes.append("истории нет — проверьте Carfax")
        return flags

    skip_enabled = set(costs.get("history_skip", _SKIP_PATTERNS))
    for name, pattern in _SKIP_PATTERNS.items():
        if name in skip_enabled and _positive_hits(text, pattern):
            flags.skip.append(_SKIP_TEXT[name])

    rates = costs.get("history_discounts", {})
    count_match = re.search(r"(\d+)\s*(accidents?|дтп|аварi?\w*)", text, re.I)
    accident_hits = _positive_hits(text, r"accident|damage reported|\bдтп\b|авари")
    accident_count = 0
    if count_match:
        accident_count = int(count_match.group(1))
    elif accident_hits:
        accident_count = 1
    if accident_count >= 2:
        flags.discounts["2+ ДТП"] = rates.get("accidents_multiple", 0.15)
    elif accident_count == 1:
        if re.search(r"minor|незначительн|мелк", text, re.I):
            flags.discounts["мелкое ДТП"] = rates.get("accident_minor", 0.05)
        else:
            flags.discounts["ДТП"] = rates.get("accident", 0.10)

    owners = re.search(r"(\d+)\s*(owners?|владельц\w*|влад\.)", text, re.I)
    if owners and int(owners.group(1)) >= 4:
        flags.discounts[f"{owners.group(1)} владельцев"] = rates.get("owners_4_plus", 0.03)

    if _positive_hits(text, r"rental|fleet|\btaxi\b|аренд|такси|прокат"):
        flags.discounts["аренда/флит"] = rates.get("rental_fleet", 0.03)
    if _positive_hits(text, r"recovered theft|theft recovery|stolen vehicle|угон"):
        flags.discounts["был в угоне"] = rates.get("theft_recovery", 0.10)
    if _positive_hits(text, _TITLE_ABSENT):
        flags.notes.append("нет титула на руках — продать нельзя, пока его не пришлют")
    return flags


# ---------------------------------------------------------------- сборы и ремонт


def resolve_auction(name: str, costs: dict) -> str:
    """Название аукциона из таблицы -> ключ в costs['auctions'] (или '' если нет)."""
    name = squeeze(name)
    auctions = costs.get("auctions", {})
    aliases = costs.get("auction_aliases", {})
    if name in auctions:
        return name
    if name in aliases:
        return aliases[name]
    lowered = name.lower()
    for key in auctions:
        if key.lower() in lowered:
            return key
    for alias, key in aliases.items():
        if alias.lower() in lowered:
            return key
    return ""


def _tier_fee(bid: float, tiers: list) -> float:
    """Сбор по ступенчатой сетке.

    Ступень: [до цены включительно, сбор] или [до, сбор, +за каждую $1000, свыше].
    Сбор может быть строкой с процентом: "1.25%" — доля от цены.
    """
    if not tiers:
        return 0.0
    tier = next((t for t in tiers if bid <= t[0]), tiers[-1])
    raw = tier[1]
    if isinstance(raw, str) and raw.strip().endswith("%"):
        fee = bid * float(raw.strip().rstrip("%")) / 100
    else:
        fee = float(raw)
    if len(tier) >= 4 and bid > tier[3]:
        # «$445 + $10 за каждую $1K свыше $7K». Неполную тысячу считаем целой —
        # лучше переоценить сбор на $10, чем недооценить.
        fee += float(tier[2]) * math.ceil((bid - tier[3]) / 1000)
    return fee


def auction_fee(bid: float, auction: str, costs: dict) -> float:
    """Сборы покупателя при данной ставке: основная сетка + доп. сетки + фиксированные сборы."""
    config = costs.get("auctions", {}).get(auction)
    if not config:
        return 0.0
    fee = _tier_fee(bid, config.get("fee_tiers") or [])
    fee += sum(_tier_fee(bid, tiers) for tiers in config.get("extra_fee_tiers", {}).values())
    return fee + sum(float(v) for v in config.get("extra_fees_usd", {}).values())


def estimate_recon(text: str, costs: dict) -> tuple[float, list[str]]:
    """Ремонт по умолчанию: базовый резерв + надбавки за найденные неисправности.

    recon_keywords — регулярные выражения (ищутся с начала слова), каждое
    считается один раз. Отдельно: коды OBD и протектор шин из отчёта ACV.
    """
    total = float(costs.get("recon_default_usd", 0))
    found: list[str] = []
    for pattern, amount in costs.get("recon_keywords", {}).items():
        if _positive_hits(text, r"\b(?:" + pattern + ")"):
            total += float(amount)
            found.append(f"{pattern.split('|')[0]} +{amount:g}")

    codes = sorted(set(re.findall(r"\b[PBCU][0-3][0-9A-F]{3}\b", text)))
    per_code = float(costs.get("obd_code_usd", 0))
    if codes and per_code:
        total += per_code * len(codes)
        found.append(f"коды OBD ×{len(codes)} +{per_code * len(codes):g}")

    worn = [int(d) for d in re.findall(r"(\d{1,2})\s*/\s*32", text) if int(d) <= int(costs.get("tire_min_32nds", 4))]
    tire_usd = float(costs.get("tire_usd", 0))
    if worn and tire_usd:
        total += tire_usd * len(worn)
        found.append(f"шины ×{len(worn)} +{tire_usd * len(worn):g}")
    return total, found


# ---------------------------------------------------------------- расчёт


@dataclass
class BidInput:
    auction: str = ""
    sale_price: float | None = None      # своя оценка цены продажи
    kbb_private_party: float | None = None
    mmr: float | None = None
    recon: float | None = None           # своя оценка ремонта
    current_bid: float | None = None
    history_text: str = ""               # титул, Carfax, CR, повреждения — одной строкой
    defects_text: str = ""               # описание дефектов для оценки ремонта


@dataclass
class BidResult:
    sale_price: float | None = None
    sale_source: str = ""
    max_bid: int | None = None
    costs_over_bid: float | None = None  # всё сверх ставки при потолке, включая сборы
    profit_at_max: float | None = None
    verdict: str = ""
    lines: list[str] = field(default_factory=list)  # расчёт по статьям

    def breakdown(self) -> str:
        return "; ".join(self.lines)


def _usd(value: float) -> str:
    return f"${value:,.0f}"


def calculate(data: BidInput, costs: dict) -> BidResult:
    result = BidResult()
    lines = result.lines

    # 1. Цена продажи
    if data.sale_price:
        result.sale_price, result.sale_source = data.sale_price, "своя оценка"
    elif data.kbb_private_party:
        factor = float(costs.get("kbb_private_party_factor", 1.0))
        result.sale_price = data.kbb_private_party * factor
        result.sale_source = f"KBB PP × {factor:g}"
    if not result.sale_price:
        result.verdict = "НЕТ ОЦЕНКИ: впишите цену продажи или KBB Private Party"
        return result
    sale = result.sale_price
    lines.append(f"продажа {_usd(sale)} ({result.sale_source})")

    # 2. История
    flags = assess_history(data.history_text, costs)
    if flags.skip:
        result.verdict = "ПРОПУСТИТЬ: " + ", ".join(flags.skip)
        lines.append("стоп-факторы в истории")
        return result
    discount_pct = sum(flags.discounts.values())
    discount = sale * discount_pct
    if discount:
        lines.append("история −" + _usd(discount) + " (" + ", ".join(f"{k} {v:.0%}" for k, v in flags.discounts.items()) + ")")

    # 3. Расходы, не зависящие от ставки
    if data.recon is not None:
        recon, recon_note = data.recon, "своя оценка"
    else:
        recon, found = estimate_recon(f"{data.defects_text} {data.history_text}", costs)
        recon_note = "по умолчанию" + (": " + ", ".join(found) if found else "") + " — проверьте CR"
    fixed = {
        "ремонт": recon,
        "детейлинг": float(costs.get("detailing_usd", 0)),
        "смог": float(costs.get("smog_usd", 0)),
        "доставка": float(costs.get("transport_usd", 0)),
        "дилер": float(costs.get("dealer_fee_usd", 0)),
        "реклама": float(costs.get("selling_usd", 0)),
        "содержание": float(costs.get("days_to_sell", 0)) * float(costs.get("holding_per_day_usd", 0)),
        "резерв": sale * float(costs.get("reserve_pct_of_sale", 0)),
    }
    fixed_total = sum(fixed.values())
    lines.append(f"ремонт {_usd(recon)} ({recon_note})")
    lines.append(", ".join(f"{k} {_usd(v)}" for k, v in fixed.items() if v and k != "ремонт"))

    profit = max(float(costs.get("profit_min_usd", 0)), sale * float(costs.get("profit_min_pct_of_sale", 0)))
    lines.append(f"цель прибыли {_usd(profit)}")

    all_in_limit = sale - discount - fixed_total - profit
    lines.append(f"предел «всё включено» {_usd(all_in_limit)}")

    # 4. Потолок ставки с учётом сборов
    auction = resolve_auction(data.auction, costs)
    if not auction:
        lines.append(f"аукцион «{data.auction or '?'}» не найден в настройках — сборы не учтены")
    elif not costs["auctions"][auction].get("verified", False):
        lines.append(f"сборы {auction} примерные — сверьте с аккаунтом")

    step = max(1, int(costs.get("bid_step_usd", 25)))
    bid = int(all_in_limit // step) * step
    while bid > 0 and bid + auction_fee(bid, auction, costs) > all_in_limit:
        bid -= step
    budget = float(costs.get("budget_max_bid_usd", 0))
    if budget and bid > budget:
        bid = int(budget // step) * step
        lines.append(f"ограничено бюджетом {_usd(budget)}")

    if bid <= 0:
        result.verdict = "НЕВЫГОДНО: расходы и цель прибыли съедают всю цену продажи"
        return result

    fee = auction_fee(bid, auction, costs)
    lines.append(f"сборы аукциона при потолке {_usd(fee)}")
    result.max_bid = bid
    result.costs_over_bid = fee + fixed_total
    result.profit_at_max = sale - discount - bid - result.costs_over_bid

    verdict = f"МОЖНО до {_usd(bid)}"
    if data.current_bid and data.current_bid > bid:
        verdict = f"ДОРОЖЕ ПОТОЛКА: ставка {_usd(data.current_bid)} > {_usd(bid)}"
    if data.mmr:
        if bid > data.mmr * float(costs.get("mmr_warn_high", 1.15)):
            verdict += f"; потолок выше MMR {_usd(data.mmr)} — проверьте цену продажи"
        elif bid < data.mmr * float(costs.get("mmr_warn_low", 0.7)):
            verdict += f"; потолок сильно ниже MMR {_usd(data.mmr)} — шанс выиграть мал"
    if flags.notes:
        verdict += "; " + "; ".join(flags.notes)
    result.verdict = verdict
    return result


# ---------------------------------------------------------------- строка таблицы


def input_from_row(row: dict[str, str]) -> BidInput:
    """Собирает вход расчёта из строки таблицы «Аналитика лотов»."""
    history = " | ".join(
        squeeze(row.get(key, ""))
        for key in ("title_type", "odometer_brand", "damage_primary", "damage_secondary", "history_page", "carfax_autocheck", "condition_report")
        if squeeze(row.get(key, ""))
    )
    return BidInput(
        auction=row.get("auction", ""),
        sale_price=parse_money(row.get("retail_estimate_usd")) or parse_money(row.get("cargurus_retail_usd")),
        kbb_private_party=parse_money(row.get("kbb_private_party_usd")),
        mmr=parse_money(row.get("mmr_adjusted_usd")),
        recon=parse_money(row.get("recon_estimate_usd")),
        current_bid=parse_money(row.get("current_bid_usd")),
        history_text=history,
        defects_text=" | ".join(squeeze(row.get(k, "")) for k in ("defects", "lot_description") if squeeze(row.get(k, ""))),
    )


def apply_to_rows(rows: list[dict[str, str]], costs: dict) -> None:
    """Заполняет расчётные колонки в каждой строке."""
    for row in rows:
        result = calculate(input_from_row(row), costs)
        row["sale_estimate_usd"] = f"{result.sale_price:.0f}" if result.sale_price else ""
        row["calc_max_bid_usd"] = str(result.max_bid) if result.max_bid else ""
        row["calc_costs_usd"] = f"{result.costs_over_bid:.0f}" if result.costs_over_bid is not None else ""
        row["calc_profit_usd"] = f"{result.profit_at_max:.0f}" if result.profit_at_max is not None else ""
        row["calc_verdict"] = result.verdict
        row["calc_breakdown"] = result.breakdown()


# ---------------------------------------------------------------- запуск для одной машины


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lot_analyzer.bid",
        description="Максимальная ставка по одной машине. Нужна цена продажи (--sale) или KBB (--kbb).",
    )
    parser.add_argument("--auction", default="Manheim", help="Manheim / CarMax / ADESA")
    parser.add_argument("--sale", type=float, help="за сколько реально продадите, $")
    parser.add_argument("--kbb", type=float, help="KBB Private Party для ZIP 92620, $")
    parser.add_argument("--mmr", type=float, help="Adjusted MMR, $ (для проверки)")
    parser.add_argument("--recon", type=float, help="своя оценка ремонта, $")
    parser.add_argument("--bid", type=float, help="текущая ставка на аукционе, $")
    parser.add_argument("--carfax", default="", help="выводы Carfax одной строкой: «1 accident minor, 3 owners, clean title»")
    parser.add_argument("--defects", default="", help="дефекты из описания / CR")
    parser.add_argument("--costs", default=str(DEFAULT_COSTS_PATH), help="файл настроек (config/costs.json)")
    args = parser.parse_args(argv)

    costs = load_costs(Path(args.costs))
    result = calculate(
        BidInput(
            auction=args.auction,
            sale_price=args.sale,
            kbb_private_party=args.kbb,
            mmr=args.mmr,
            recon=args.recon,
            current_bid=args.bid,
            history_text=args.carfax,
            defects_text=args.defects,
        ),
        costs,
    )
    print(result.verdict)
    for line in result.lines:
        print("  " + line)
    if result.profit_at_max is not None:
        print(f"  прибыль при потолке ≈ {_usd(result.profit_at_max)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
