"""CarGurus: IMV (Instant Market Value) и конкуренты в радиусе 50 миль — через Claude в Chrome на Mac.

CarGurus не даёт открытого API с ценами рынка, а к вашему браузеру программа в облаке не подключается. Поэтому так же,
как с Facebook (Muse): программа готовит короткое задание по группам машин-кандидатов (марка, модель, три года, пробег),
вы отдаёте его Claude в Chrome (вкладка с CarGurus, где вы вошли), ответ — CSV — в окно («📥 Ответ CarGurus») или
файлом cargurus*.csv в «Загрузки/Lot Analyzer».
К машине: медиана цен похожих объявлений, медиана IMV (что CarGurus считает справедливой ценой объявления), сколько
конкурентов и сколько дней они висят (ликвидность). В цену продажи: IMV × asking_to_sale — объявления на CarGurus в
основном дилерские, ваша цена на Facebook ниже (costs.json: cargurus).
"""

from __future__ import annotations

from .fb_market import Source, _num
from . import fb_market
from .paths import DATA_DIR

PATH = DATA_DIR / "cargurus.json"
HEADER = ["group", "year", "make", "model", "trim", "miles", "price", "imv", "deal", "days_on_market", "distance", "city", "seller", "url"]
DEFAULTS = {"zip": "92620", "radius_miles": 50, "per_group": 15, "max_groups": 8, "refresh_days": 14, "asked_wait_days": 3,
            "max_age_days": 30, "min_listings": 3, "asking_to_sale": 0.9, "weight": 0.5, "use_in_sale": True}
DEALS = ("great", "good", "fair", "high", "overpriced")


def _intro(cfg: dict) -> str:
    return (f"On CarGurus.com (I am signed in), search used cars near ZIP {cfg['zip']} within {cfg['radius_miles']} miles. For each line "
            f"below, list up to {cfg['per_group']} current listings of that car: same make and model, model year within the range, mileage "
            "within the range (±10,000 is OK). Skip salvage / rebuilt / frame damage titles and listings without a price.\n"
            "For every listing give: price; IMV = CarGurus Instant Market Value shown in the deal badge or price analysis (\"IMV of $…\", "
            "empty if not shown); deal = CarGurus deal rating (Great, Good, Fair, High, Overpriced or No price analysis); days_on_market = "
            "days on CarGurus; distance = miles from me; seller = dealer or private.\n"
            "Only look at listings. Do not contact dealers, do not fill in any forms, do not share any personal info.\n\n")


def _extra(rec: dict) -> dict:
    deal = next((d for d in DEALS if d in str(rec.get("deal") or "").lower()), "")
    return {"imv": _num(rec.get("imv")) or None, "deal": deal, "distance": _num(rec.get("distance")) or None}


CG = Source(name="CarGurus", agent="Claude в Chrome", path=lambda: PATH, header=HEADER, defaults=DEFAULTS, cfg_key="cargurus",
            files="cargurus", prefix="cg", intro=_intro, extra=_extra)


def unmark_asked(ids: dict) -> None:
    return fb_market.unmark_asked(ids, src=CG)


def task(rows: list[dict], costs: dict, now: float | None = None, vins: list[str] | None = None) -> dict:
    return fb_market.task(rows, costs, now, src=CG, vins=vins)


def mark_asked(ids: dict, now: float | None = None) -> None:
    fb_market.mark_asked(ids, now, src=CG)


def add_answer(text: str, now: float | None = None) -> dict:
    return fb_market.add_answer(text, now, src=CG)


def import_files(folders) -> int:
    return fb_market.import_files(folders, src=CG)


def load() -> dict:
    return fb_market.load(CG)


def attach(rows: list[dict], costs: dict, now: float | None = None) -> None:
    fb_market.attach(rows, costs, now, src=CG)
