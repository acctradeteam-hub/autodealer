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

from .market import HEAVY as _HEAVY
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
    "branded_title": r"salvage|rebuilt|reconstructed|total loss|\bjunk\b|lemon|buy\s*back|non[\s-]*repairable|certificate of destruction|restored title|спасён|восстановленн",
    "odometer_problem": r"odometer (rollback|problem|discrepancy|tamper)|mileage (inconsistency|discrepancy)|rollback|not[\s-]*actual|\btmu\b|true mileage unknown|скрут",
    "structural_damage": r"structural (damage|alteration)|frame damage|unibody damage|frame/unibody damage|повреждени[ея] рамы|\bрам[аы]\b",
    "airbag_deployed": r"airbags? deployed|подушк\w* (безопасности )?сработал",
    # «LR Tail Lamp: Water Damage» — влага в фонаре, не затопление машины.
    "flood": r"\bflood\b|(?<!lamp: )(?<!light: )water damage|water intrusion|затоплен|утоплен",
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
_TITLE_ABSENT = r"title absent|title (delay|missing)|no title(?!\s*(issues?|problems?))|титул отсутств"
# Калифорния: для оформления понадобится форма REG 227 (дубликат титула).
_POSSIBLE_227 = r"(possible|app) 227|\breg[\s-]?227\b"
# Продавец пишет «не на ходу», даже если отчёт говорит обратное.
_NON_RUNNER = r"non[\s-]*runner|no runner|does not run"


def _positive_hits(text: str, pattern: str) -> list[re.Match]:
    """Находки шаблона, перед которыми нет отрицания («no», «без», «0»)."""
    hits = []
    for match in re.finditer(pattern, text, re.I):
        before = text[max(0, match.start() - 30):match.start()]
        if not _NEGATION.search(before):
            hits.append(match)
    return hits


_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                 "один": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5}


def _words_to_digits(text: str) -> str:
    """«Two owners, three accidents» -> «2 owners, 3 accidents»."""
    return re.sub(r"\b(" + "|".join(_NUMBER_WORDS) + r")\b", lambda m: str(_NUMBER_WORDS[m.group(1).lower()]), text, flags=re.I)


@dataclass
class HistoryFlags:
    skip: list[str] = field(default_factory=list)          # причины «пропустить»
    discounts: dict[str, float] = field(default_factory=dict)  # название -> доля скидки
    notes: list[str] = field(default_factory=list)
    extra_days: int = 0                                     # лишние дни до продажи (оформление титула)


def title_policy(costs: dict) -> str:
    """strict — только с титулом; allow_227 — REG 227 можно, «Title Absent» нельзя; allow_all — всё, с лишними днями."""
    if costs.get("title_policy"):
        return str(costs["title_policy"])
    return "strict" if costs.get("title_required") else "allow_all"


def _title_rules(text: str, costs: dict, flags: "HistoryFlags") -> None:
    policy = title_policy(costs)
    absent = bool(_positive_hits(text, _TITLE_ABSENT))
    reg227 = bool(_positive_hits(text, _POSSIBLE_227))
    days_227 = int(costs.get("reg227_extra_days", 10))
    days_absent = int(costs.get("title_absent_extra_days", 35))
    if policy == "strict" and (absent or reg227):
        flags.skip.append("нет титула / REG 227 — режим «только с титулом»")
    elif absent and policy == "allow_227":
        flags.skip.append("нет титула на руках (Title Absent) — продать нельзя, пока его не пришлют")
    elif absent:
        flags.extra_days += days_absent
        flags.notes.append(f"нет титула на руках — продать нельзя, пока не пришлют (+{days_absent} дн. в расчёте)")
    elif reg227:
        flags.extra_days += days_227
        flags.notes.append(f"REG 227 вместо титула — продажа через дилера с REG 227, +{days_227} дн. на оформление")


def assess_history(text: str, costs: dict) -> HistoryFlags:
    """Разбирает текст истории (титул, Carfax/AutoCheck, CR, повреждения) по ключевым словам."""
    flags = HistoryFlags()
    text = _words_to_digits(squeeze(text))
    if not text:
        flags.notes.append("истории нет — проверьте Carfax")
        return flags

    skip_enabled = set(costs.get("history_skip", _SKIP_PATTERNS))
    for name, pattern in _SKIP_PATTERNS.items():
        if name in skip_enabled and _positive_hits(text, pattern):
            flags.skip.append(_SKIP_TEXT[name])

    rates = costs.get("history_discounts", {})
    count_match = re.search(r"(\d+)\s*(accidents?|дтп|аварi?\w*)", text, re.I)
    accident_hits = _positive_hits(text, r"accident|damage reported|damage history|\bдтп\b|авари")
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

    if _positive_hits(text, r"rental|fleet|\btaxi\b|police|non-?personal use|аренд|такси|прокат"):
        flags.discounts["аренда/флит"] = rates.get("rental_fleet", 0.03)
    if _positive_hits(text, r"recovered theft|theft recovery|theft history|stolen vehicle|угон"):
        flags.discounts["был в угоне"] = rates.get("theft_recovery", 0.10)
    _title_rules(text, costs, flags)
    if _positive_hits(text, _NON_RUNNER):
        flags.notes.append("продавец пишет «не на ходу» — заложен резерв, проверьте на месте")
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


def grade_recon(grade: float | None, auction: str, costs: dict) -> float:
    """Надбавка к ремонту по CR grade (шкала 0–5): чем ниже оценка, тем больше вложений."""
    cfg = costs.get("recon_by_grade") or {}
    if grade is None or not cfg.get("steps"):
        return 0.0
    names = [a.lower() for a in cfg.get("auctions", [])]
    if names and not any(n in (auction or "").lower() for n in names):
        return 0.0
    for floor, amount in sorted(cfg["steps"], key=lambda x: -float(x[0])):
        if grade >= float(floor):
            return float(amount)
    return 0.0


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


def target_profit(sale: float, costs: dict) -> float:
    """Цель прибыли: ступени profit_tiers [[цена продажи до, $ или доля]], иначе большее из $ и %."""
    for upto, value in costs.get("profit_tiers") or []:
        if sale <= float(upto):
            value = float(value)
            return sale * value if value < 1 else value
    return max(float(costs.get("profit_min_usd", 0)), sale * float(costs.get("profit_min_pct_of_sale", 0)))


def win_chance(bid: float, base: float, points: list) -> float | None:
    """Доля лотов, ушедших не дороже bid, по точкам распределения [[цена ÷ база, накопленная доля], …]."""
    if not base or not points:
        return None
    ratio = bid / base
    pts = sorted((float(r), float(q)) for r, q in points)
    if ratio <= pts[0][0]:
        return pts[0][1] * ratio / pts[0][0] if pts[0][0] else 0.0
    for (r1, q1), (r2, q2) in zip(pts, pts[1:]):
        if ratio <= r2:
            return q1 + (q2 - q1) * (ratio - r1) / (r2 - r1)
    return pts[-1][1]


def transport_cost(location: str, costs: dict) -> float:
    """Доставка: по площадке из transport_by_location (дальние аукционы), иначе transport_usd."""
    lowered = (location or "").lower()
    for place, amount in (costs.get("transport_by_location") or {}).items():
        if place.lower() in lowered:
            return float(amount)
    state = re.search(r",\s*([A-Z]{2})\b", location or "")
    if state and state.group(1) != costs.get("home_state", "CA"):
        return float(costs.get("transport_out_of_state_usd", 0))   # другой штат, цена не задана — оценка
    return float(costs.get("transport_usd", 0))


def dmv_fees(text: str) -> float:
    """Долги DMV из объявлений CarMax: «Dmv $71», «Dmv fee $144» — платит покупатель."""
    amounts = {int(m) for m in re.findall(r"\bdmv(?:\s*fees?)?\s*\$\s?(\d{1,5})", text, re.I)}
    return float(sum(amounts))


def market_config(auction: str, costs: dict) -> dict:
    """Доли рынка для аукциона: общие costs["market"] + поправки costs["market_by_auction"][аукцион].

    На Manheim MMR — это и есть средняя цена его же торгов, поэтому там база — MMR
    («prefer": "mmr"), а не KBB, как у CarMax.
    """
    market = dict(costs.get("market") or {})
    overrides = costs.get("market_by_auction") or {}
    key = resolve_auction(auction, costs) or squeeze(auction)
    for name, extra in overrides.items():
        if name.startswith("_"):
            continue
        if name == key or name.lower() in (auction or "").lower():
            market.update(extra)
            break
    return market


def _market_base(data: "BidInput", market: dict) -> str:
    """Какая база у доли рынка: «kbb» или «mmr»."""
    if market.get("prefer") == "mmr" and data.mmr and market.get("mmr_clean"):
        return "mmr"
    return "kbb" if data.kbb_private_party and market.get("kbb_clean") else "mmr"


def expected_market_price(data: "BidInput", costs: dict) -> tuple[float | None, str]:
    """Сколько обычно платят на торгах за такую машину: KBB (или MMR) × доля из истории результатов.

    Доли — в costs["market"], их пересчитывает `python3 -m lot_analyzer.market … --update-config`.
    Машины с тяжёлыми дефектами (коробка, мотор, рама, титул) уходят дешевле — для них своя доля.
    """
    market = market_config(data.auction, costs)
    heavy = bool(_HEAVY.search(f"{data.history_text} {data.defects_text}"))
    kind = "heavy" if heavy else "clean"
    label = "с тяжёлыми дефектами" if heavy else "без тяжёлых дефектов"
    note = f"; {market['note']}" if market.get("note") else ""
    if _market_base(data, market) == "mmr" and data.mmr and market.get(f"mmr_{kind}"):
        share = float(market[f"mmr_{kind}"])
        return data.mmr * share, f"MMR × {share:g} — {label}{note}"
    if data.kbb_private_party and market.get(f"kbb_{kind}"):
        share = float(market[f"kbb_{kind}"])
        return data.kbb_private_party * share, f"KBB × {share:g} — медиана торгов {label}"
    if data.mmr and market.get(f"mmr_{kind}"):
        share = float(market[f"mmr_{kind}"])
        return data.mmr * share, f"MMR × {share:g} — медиана торгов {label}"
    return None, ""


# ---------------------------------------------------------------- расчёт


@dataclass
class BidInput:
    auction: str = ""
    location: str = ""                   # площадка / город — для стоимости доставки
    no_photos: bool = False              # лот без фотографий: осматривать самому, конкурентов меньше
    sale_price: float | None = None      # своя оценка цены продажи
    kbb_private_party: float | None = None
    kbb_source: str = ""                 # пусто — KBB из заметки; иначе — своя оценка (откуда)
    auction_retail: float | None = None  # розничная оценка самого аукциона (Manheim, ADESA)
    mmr: float | None = None             # MMR или оптовая оценка аукциона
    recon: float | None = None           # своя оценка ремонта
    grade: float | None = None           # CR grade аукциона (0–5), если есть
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
    market_price: float | None = None    # сколько обычно платят на торгах (по истории результатов)
    market_source: str = ""
    win_chance: float | None = None      # доля похожих лотов, ушедших не дороже потолка
    inspect: list[str] = field(default_factory=list)   # почему нужен личный осмотр
    max_bid_if_defect: int | None = None  # потолок, если объявленный дефект подтвердится
    verdict: str = ""
    lines: list[str] = field(default_factory=list)  # расчёт по статьям

    def breakdown(self) -> str:
        return "; ".join(self.lines)


def _usd(value: float) -> str:
    return f"${value:,.0f}"


# Объявления CarMax о тяжёлом дефекте, которые на деле часто не подтверждаются (опыт владельца:
# Civic 2015 и Kia Sportage с «Major Engine Defect» оказались исправны). Такие лоты — в «личный осмотр».
MAJOR_DEFECT_RE = re.compile(r"major\s+(engine|transmission)\s+defect", re.I)


def calculate(data: BidInput, costs: dict) -> BidResult:
    """Потолок ставки. Для «Major Engine/Transmission Defect» — два сценария и пометка «личный осмотр»."""
    inspect: list[str] = []
    if data.no_photos:
        inspect.append("без фото")
    announced = sorted({m.group(0).title() for m in MAJOR_DEFECT_RE.finditer(f"{data.history_text} {data.defects_text}")})
    if not announced or not costs.get("inspect_major_defects", True):
        result = _calculate(data, costs)
        result.inspect = inspect
        return result

    inspect.insert(0, "объявлен " + ", ".join(announced))
    confirmed = _calculate(data, costs)                     # дефект подтвердится: резерв на мотор / коробку
    clean = BidInput(**{**data.__dict__,
                        "history_text": MAJOR_DEFECT_RE.sub("", data.history_text),
                        "defects_text": MAJOR_DEFECT_RE.sub("", data.defects_text)})
    result = _calculate(clean, costs)                       # дефект не подтвердится
    result.inspect = inspect
    result.max_bid_if_defect = confirmed.max_bid
    if result.max_bid is None:                              # стоп-факторы или нет оценки — как есть
        return result

    # Рынок для таких лотов — как для машин с тяжёлым дефектом: другие дилеры ставят с поправкой на него.
    result.market_price, result.market_source = confirmed.market_price, confirmed.market_source
    market = market_config(data.auction, costs)
    base_kind = _market_base(data, market)
    base_value = data.kbb_private_party if base_kind == "kbb" else data.mmr
    heavy_curve = market.get(f"{base_kind}_heavy_curve") or []
    result.win_chance = win_chance(result.max_bid, base_value, heavy_curve) if base_value else None
    bid = result.max_bid
    verdict = f"ОСМОТР: до {_usd(bid)}, если дефект не подтвердится"
    verdict += f" (подтвердится — до {_usd(confirmed.max_bid)})" if confirmed.max_bid else " (подтвердится — не брать)"
    if result.market_price:
        verdict += f"; рынок ≈ {_usd(result.market_price)} — лоты с таким объявлением уходят дешевле"
        if result.win_chance is not None:
            verdict += f", выигрывает ~{result.win_chance:.0%} похожих"
    if data.current_bid and data.current_bid > bid:
        verdict = f"ДОРОЖЕ ПОТОЛКА: ставка {_usd(data.current_bid)} > {_usd(bid)} (даже без дефекта)"
    rest = [x for x in result.verdict.split("; ")[1:] if not x.startswith("рынок ≈")]
    result.verdict = "; ".join([verdict] + rest)
    result.lines.append(f"если дефект подтвердится: потолок {_usd(confirmed.max_bid) if confirmed.max_bid else 'нет'}")
    return result


def _calculate(data: BidInput, costs: dict) -> BidResult:
    result = BidResult()
    lines = result.lines

    # 0. Стоп-факторы — до всего остального: такую машину не берём при любой цене.
    flags = assess_history(data.history_text, costs)
    if flags.skip:
        result.verdict = "ПРОПУСТИТЬ: " + ", ".join(flags.skip)
        lines.append("стоп-факторы в истории")
        return result

    # 1. Цена продажи
    if data.sale_price:
        result.sale_price, result.sale_source = data.sale_price, "своя оценка"
    elif data.kbb_private_party:
        factor = float(costs.get("kbb_private_party_factor", 1.0))
        offset = float(costs.get("kbb_private_party_offset_usd", 0))
        result.sale_price = data.kbb_private_party * factor + offset
        result.sale_source = f"KBB PP × {factor:g}" + (f" {offset:+,.0f}" if offset else "")
        if data.kbb_source:
            result.sale_source += f"; KBB — прикидка (на kbb.com не смотрели): {data.kbb_source}"
            cap = float(costs.get("kbb_estimate_max_to_mmr", 0))
            if cap and data.mmr and result.sale_price > data.mmr * cap:
                # Прикидка считает машину «Good», а низкий MMR обычно значит плохое состояние или историю.
                result.sale_price = data.mmr * cap
                result.sale_source += f"; ограничено MMR × {cap:g} — прикидка KBB слишком высока для такого опта"
    elif data.auction_retail:
        factor = float(costs.get("auction_retail_factor", 0.9))
        result.sale_price = data.auction_retail * factor
        result.sale_source = f"ритейл аукциона × {factor:g} — ориентир, сверьте с Facebook"
    elif data.mmr and float(costs.get("mmr_retail_factor", 0)):
        factor = float(costs["mmr_retail_factor"])
        result.sale_price = data.mmr * factor
        result.sale_source = f"опт/MMR × {factor:g} — грубый ориентир, впишите KBB или FB"
    if not result.sale_price:
        result.verdict = "НЕТ ОЦЕНКИ: впишите цену продажи или KBB Private Party"
        if data.no_photos:
            result.verdict += "; БЕЗ ФОТО — осмотрите сами: другие дилеры по таким почти не торгуются"
        if flags.notes:
            result.verdict += "; " + "; ".join(flags.notes)
        return result
    sale = result.sale_price
    lines.append(f"продажа {_usd(sale)} ({result.sale_source})")

    # 2. Скидка за историю
    discount_pct = sum(flags.discounts.values())
    discount = sale * discount_pct
    if discount:
        lines.append("история −" + _usd(discount) + " (" + ", ".join(f"{k} {v:.0%}" for k, v in flags.discounts.items()) + ")")

    # 3. Расходы, не зависящие от ставки
    if data.recon is not None:
        recon, recon_note = data.recon, "своя оценка"
    else:
        recon, found = estimate_recon(f"{data.defects_text} {data.history_text}", costs)
        extra = grade_recon(data.grade, data.auction, costs)
        if extra:
            recon += extra
            found = found + [f"CR grade {data.grade:g} +{_usd(extra)}"]
        recon_note = "по умолчанию" + (": " + ", ".join(found) if found else "") + " — проверьте CR"
    fixed = {
        "ремонт": recon,
        "детейлинг": float(costs.get("detailing_usd", 0)),
        "смог": float(costs.get("smog_usd", 0)),
        "доставка": transport_cost(data.location, costs),
        "дилер": float(costs.get("dealer_fee_usd", 0)),
        "реклама": float(costs.get("selling_usd", 0)),
        "содержание": (float(costs.get("days_to_sell", 0)) + flags.extra_days) * float(costs.get("holding_per_day_usd", 0)),
        "резерв": sale * float(costs.get("reserve_pct_of_sale", 0)),
        "DMV": dmv_fees(f"{data.defects_text} {data.history_text}"),
    }
    fixed_total = sum(fixed.values())
    lines.append(f"ремонт {_usd(recon)} ({recon_note})")
    lines.append(", ".join(f"{k} {_usd(v)}" for k, v in fixed.items() if v and k != "ремонт"))

    profit = target_profit(sale, costs)
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
    min_bid = float(costs.get("min_max_bid_usd", 0))
    if bid < min_bid:
        result.verdict = f"НЕВЫГОДНО: потолок {_usd(bid)} ниже {_usd(min_bid)} — слишком дешёвая машина"
        return result

    fee = auction_fee(bid, auction, costs)
    lines.append(f"сборы аукциона при потолке {_usd(fee)}")
    result.max_bid = bid
    result.costs_over_bid = fee + fixed_total
    result.profit_at_max = sale - discount - bid - result.costs_over_bid

    verdict = f"МОЖНО до {_usd(bid)}"
    if data.current_bid and data.current_bid > bid:
        verdict = f"ДОРОЖЕ ПОТОЛКА: ставка {_usd(data.current_bid)} > {_usd(bid)}"
    market, market_source = expected_market_price(data, costs)
    photo_discount = float(costs.get("no_photo_market_discount", 0)) if data.no_photos else 0.0
    if market and photo_discount:
        market *= 1 - photo_discount
        market_source += f"; без фото −{photo_discount:.0%}"
    result.market_price, result.market_source = market, market_source
    market_cfg = market_config(data.auction, costs)
    base_kind = _market_base(data, market_cfg)
    base_value = data.kbb_private_party if base_kind == "kbb" else data.mmr
    heavy = bool(_HEAVY.search(f"{data.history_text} {data.defects_text}"))
    points = market_cfg.get(f"{base_kind}_{'heavy' if heavy else 'clean'}_curve") or []
    chance = win_chance(bid, base_value, points) if base_value else None
    result.win_chance = chance
    if market:
        lines.append(f"рынок ≈ {_usd(market)} ({market_source})")
        if bid < market * 0.95:
            verdict += f"; рынок ≈ {_usd(market)} — потолок ниже на {_usd(market - bid)}, выиграть вряд ли"
        else:
            verdict += f"; рынок ≈ {_usd(market)} — шанс есть"
        if chance is not None:
            verdict += f" (выигрывает ~{chance:.0%} похожих лотов)"
    elif data.mmr:
        if bid > data.mmr * float(costs.get("mmr_warn_high", 1.15)):
            verdict += f"; потолок выше опта/MMR {_usd(data.mmr)} — проверьте цену продажи"
        elif bid < data.mmr * float(costs.get("mmr_warn_low", 0.7)):
            verdict += f"; потолок сильно ниже опта/MMR {_usd(data.mmr)} — шанс выиграть мал"
    if result.sale_source.startswith("опт/MMR"):
        verdict += "; цена продажи грубо по MMR — впишите KBB"
    if data.no_photos:
        verdict += "; БЕЗ ФОТО — осмотрите сами: другие дилеры по таким почти не торгуются"
    if flags.notes:
        verdict += "; " + "; ".join(flags.notes)
    result.verdict = verdict
    return result


# ---------------------------------------------------------------- строка таблицы


def _grade(text: str) -> float | None:
    match = re.match(r"\s*([0-5](?:\.\d)?)\b", text or "")
    return float(match.group(1)) if match else None


def input_from_row(row: dict[str, str]) -> BidInput:
    """Собирает вход расчёта из строки таблицы «Аналитика лотов»."""
    history = " | ".join(
        squeeze(row.get(key, ""))
        for key in ("title_type", "odometer_brand", "damage_primary", "damage_secondary", "history_page", "carfax_autocheck", "condition_report")
        if squeeze(row.get(key, ""))
    )
    return BidInput(
        auction=row.get("auction", ""),
        location=row.get("location", ""),
        no_photos=row.get("no_photos", "") == "да",
        sale_price=parse_money(row.get("retail_estimate_usd")) or parse_money(row.get("cargurus_retail_usd")),
        kbb_private_party=parse_money(row.get("kbb_private_party_usd")),
        mmr=parse_money(row.get("mmr_adjusted_usd")) or parse_money(row.get("wholesale_usd")),
        auction_retail=parse_money(row.get("auction_retail_usd")),
        recon=parse_money(row.get("recon_estimate_usd")),
        grade=_grade(row.get("condition_grade", "")),
        current_bid=parse_money(row.get("current_bid_usd")),
        history_text=history,
        defects_text=" | ".join(squeeze(row.get(k, "")) for k in ("defects", "lot_description") if squeeze(row.get(k, ""))),
    )


def _proxy_note(proxy: float | None, ceiling: int | None) -> str:
    """Сверка своей прокси-ставки с расчётным потолком."""
    if not proxy:
        return ""
    if ceiling is None:
        return f"; ваш прокси {_usd(proxy)}"
    if proxy > ceiling:
        return f"; ваш прокси {_usd(proxy)} ВЫШЕ потолка на {_usd(proxy - ceiling)}"
    return f"; ваш прокси {_usd(proxy)} в пределах потолка"


def apply_to_rows(rows: list[dict[str, str]], costs: dict, estimator=None) -> None:
    """Заполняет расчётные колонки в каждой строке.

    Если у строки нет ни KBB, ни своей цены продажи — KBB оценивается по похожим
    машинам (lot_analyzer/kbb.py): по вашим KBB из истории и заметок, иначе по торгам.
    """
    cfg = costs.get("kbb_estimate") or {}
    if estimator is None and cfg.get("enabled", True):
        estimator = _estimator(costs)
    if estimator is not None:
        estimator.add_rows(rows)
    cfg = costs.get("kbb_estimate") or {}
    for row in rows:
        row["kbb_estimate_usd"] = row["kbb_estimate_source"] = ""
        if estimator is not None and not (parse_money(row.get("kbb_private_party_usd")) or parse_money(row.get("retail_estimate_usd"))):
            year = int(row["year"]) if str(row.get("year", "")).isdigit() else None
            miles = parse_money(row.get("odometer_miles"))
            est = estimator.estimate(row.get("make", ""), row.get("model", ""), year, miles, vin=row.get("vin", ""))
            market_based = est is not None and est.source.startswith("по рынку")
            if est and (not market_based or (cfg.get("use_market_comps", True) and est.n >= int(cfg.get("min_market_comps", 5)))):
                row["kbb_estimate_usd"] = f"{est.value:.0f}"
                row["kbb_estimate_source"] = est.source + (" (грубо, ±20%)" if market_based else "")
        data = input_from_row(row)
        if not data.kbb_private_party and row.get("kbb_estimate_usd"):
            data.kbb_private_party = parse_money(row["kbb_estimate_usd"])
            data.kbb_source = row["kbb_estimate_source"]
        result = calculate(data, costs)
        row["sale_estimate_usd"] = f"{result.sale_price:.0f}" if result.sale_price else ""
        row["calc_max_bid_usd"] = str(result.max_bid) if result.max_bid else ""
        row["calc_costs_usd"] = f"{result.costs_over_bid:.0f}" if result.costs_over_bid is not None else ""
        row["calc_profit_usd"] = f"{result.profit_at_max:.0f}" if result.profit_at_max is not None else ""
        row["market_estimate_usd"] = f"{result.market_price:.0f}" if result.market_price else ""
        row["calc_win_chance_pct"] = f"{result.win_chance * 100:.0f}" if result.win_chance is not None and result.max_bid else ""
        row["calc_verdict"] = result.verdict + _proxy_note(parse_money(row.get("my_proxy_usd")), result.max_bid)
        real_kbb = parse_money(row.get("kbb_private_party_usd")) or parse_money(row.get("retail_estimate_usd"))
        if costs.get("require_kbb") and not real_kbb and result.max_bid:
            # Без настоящего KBB потолок — только прикидка по MMR: сначала KBB из приложения.
            rest = [x for x in row["calc_verdict"].split("; ")[1:] if "грубо по MMR" not in x]
            row["calc_verdict"] = "; ".join([f"НУЖЕН KBB: предварительно до {_usd(result.max_bid)} (цена продажи по MMR)"] + rest)
        if result.max_bid and "из списка:" in row.get("needs_review", ""):
            # Строка из списка поиска: истории и повреждений из карточки ещё нет.
            row["calc_verdict"] += "; предварительно — откройте карточку лота"
        row["calc_breakdown"] = result.breakdown()
        row["inspect"] = "; ".join(result.inspect)
        row["calc_max_bid_if_defect_usd"] = str(result.max_bid_if_defect) if result.max_bid_if_defect else ""


_ESTIMATOR_CACHE: dict = {}


def _estimator(costs: dict):
    """Оценщик KBB по data/market_history.csv (кэш по времени изменения файла)."""
    from .kbb import KbbEstimator
    from .market import HISTORY_PATH

    stamp = HISTORY_PATH.stat().st_mtime if HISTORY_PATH.exists() else 0
    key = (stamp, json.dumps(costs.get("kbb_estimate") or {}, sort_keys=True), (costs.get("market") or {}).get("kbb_clean"))
    if key not in _ESTIMATOR_CACHE:
        _ESTIMATOR_CACHE.clear()
        _ESTIMATOR_CACHE[key] = KbbEstimator.from_history(costs) if stamp else KbbEstimator([], costs)
    return _ESTIMATOR_CACHE[key].copy()


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
