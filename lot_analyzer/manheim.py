"""Разбор карточки лота Manheim (search.manheim.com, Simulcast / OVE).

Вёрстка страницы Manheim — это фильтры поиска (списки марок, годов до 2027 и
т.п.), по которым общий разбор находит «марку» и «год» не того лота. Зато в
страницу встроен JSON объявления из API Cox Automotive — объект, который
начинается с {"href":"https://api.coxautoinc.com/…/listings/id/…». В нём есть
всё: VIN, пробег, MMR с поправками, розничная оценка, AutoCheck, Condition
Report с повреждениями и шинами, объявления продавца, дата, дорожка и номер.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from .normalize import squeeze

_LISTING_START = '{"href":"https://api.coxautoinc.com/wholesale-marketplace/enablement/listings-search/listings/id/'


@dataclass
class ManheimDetail:
    data: dict
    fragment_html: str
    listings_on_page: int = 1


# Карточка лота: {"href":"https://api.coxautoinc.com/…"; карточки списка поиска: {"source":"OVE"|"Simulcast",…
_JSON_NODE = re.compile(r'^\s*\{"(?:href":"https://api\.coxautoinc\.com/wholesale-marketplace/enablement/listings-search|source":")')


def find_listings(html: str) -> list[dict]:
    """Все объявления Manheim, встроенные в страницу (карточка лота или результаты поиска)."""
    if "coxautoinc" not in html and '{"source":"' not in html and "lot-analyzer-all-pages" not in html:
        return []
    soup = BeautifulSoup(html, "lxml")
    listings: list[dict] = []
    seen: set[str] = set()
    # Закладка «все страницы» кладёт машины из API в #lot-analyzer-all-pages — там поля могут идти в любом порядке.
    box = soup.find(id="lot-analyzer-all-pages")
    texts = [d.get_text() for d in box.find_all("div", class_="stockwave-vehicle-info")] if box else []
    for text in texts + list(soup.find_all(string=_JSON_NODE)):
        try:
            data = json.loads(str(text).strip())
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("vin") and data.get("id", data["vin"]) not in seen:
            seen.add(data.get("id", data["vin"]))
            listings.append(data)
    return listings


def detail_from(data: dict, listings_on_page: int = 1) -> ManheimDetail:
    title = squeeze((data.get("designatedDescriptionEnrichment") or {}).get("manheimStandardDescription", {}).get("shortDescription", ""))
    fragment = f"<html><head><title>{title} | Manheim</title></head><body></body></html>"
    return ManheimDetail(data=data, fragment_html=fragment, listings_on_page=listings_on_page)


def find_detail(html: str) -> ManheimDetail | None:
    """JSON объявления Manheim со страницы или None."""
    listings = find_listings(html)
    if not listings:
        return None
    data = listings[0]
    title = squeeze(data.get("designatedDescriptionEnrichment", {}).get("manheimStandardDescription", {}).get("shortDescription", ""))
    fragment = f"<html><head><title>{title} | Manheim</title></head><body></body></html>"
    return ManheimDetail(data=data, fragment_html=fragment, listings_on_page=len(listings))


def _money(value) -> str:
    try:
        return f"{float(value):.0f}" if value not in (None, "") else ""
    except (TypeError, ValueError):
        return ""


def lot_url(vin: str, source: str = "") -> str:
    """Карточка лота на Manheim (фото, CR, ставка): search.manheim.com/results#/details/VIN/Simulcast|OVE."""
    source = "OVE" if source.upper() in ("OVE", "TIMED SALE", "TIMED_SALE") else (source or "Simulcast")
    return f"https://search.manheim.com/results#/details/{vin}/{source}"


def apply_detail(row: dict[str, str], detail: ManheimDetail) -> list[str]:
    """Заполняет строку из JSON Manheim. Возвращает заметки для «Проверить»."""
    d = detail.data
    notes: list[str] = []
    desc = d.get("designatedDescriptionEnrichment") or {}
    cond = d.get("conditionEnrichment") or {}
    autocheck = d.get("autocheck") or {}

    row["auction"] = "Manheim"
    row["lot_number"] = str(d.get("workOrderNumber") or d.get("sblu") or "")
    row["vin"] = d.get("vin", "")
    row["year"] = str(d.get("year") or desc.get("year") or "")
    row["make"] = d.get("make") or desc.get("make", "")
    models = d.get("models") or [desc.get("model") or ""]
    row["model"] = models[0] if models else ""
    trims = d.get("trims") or [desc.get("trim") or d.get("sourceTrim") or ""]
    row["trim"] = trims[0] if trims else ""
    row["exterior_color"] = str(d.get("exteriorColor") or "")
    if d.get("odometer") is not None:
        miles = float(d["odometer"])
        if (d.get("odometerUnits") or "mi").lower().startswith("k"):
            miles *= 0.621371
            notes.append("пробег переведён из км в мили")
        row["odometer_miles"] = f"{miles:.0f}"
    row["location"] = ", ".join(x for x in (d.get("facilitationLocation") or d.get("auctionName"), d.get("pickupLocationCity"), d.get("pickupLocationState")) if x)
    if not d.get("pickupLocationCity") and d.get("pickupLocation") and d.get("pickupLocation") != row["location"]:
        row["location"] = d["pickupLocation"]          # «NV - RENO»
    row["sale_date"] = d.get("saleDate", "") or ""
    if d.get("channelSaleType") == "TIMED_SALE" and d.get("auctionEndTime"):
        row["sale_date"] = f"до {d['auctionEndTime'].replace('T', ' ').replace(':00Z', ' UTC')}"
    row["current_bid_usd"] = _money(d.get("bidPrice"))
    row["mmr_adjusted_usd"] = _money(d.get("mmrPrice") or (d.get("valuationsMmr") or {}).get("adjustedValue"))
    row["wholesale_usd"] = row["mmr_adjusted_usd"]
    row["auction_retail_usd"] = _money((d.get("valuationsRetail") or {}).get("adjustedValue") or d.get("retailPrice"))
    grade = cond.get("grade") or d.get("conditionGradeNumeric")
    row["condition_grade"] = f"{grade} {cond.get('gradeDescription', '')}".strip() if grade else ""

    # --- объявления продавца ---
    announcements = [squeeze(a.get("text", "")) for a in d.get("announcementsBySource") or [] if a.get("text")]
    announcements += [squeeze(str(a)).lstrip("*") for a in d.get("additionalAnnouncements") or []]
    if d.get("remarks"):
        announcements.append(squeeze(d["remarks"]).lstrip("*"))
    announcements += [squeeze(str(a)) for a in (d.get("announcementsEnrichment") or {}).get("announcements") or []]
    if d.get("hasFrameDamage"):
        announcements.append("Frame or Structural Damage")
    if d.get("salvageVehicle"):
        announcements.append("Salvage vehicle")
    title_status = squeeze(d.get("titleStatus") or "")
    if title_status and title_status != "Not Specified":
        announcements.append(f"Title status: {title_status}")
    # Анкета продавца (OVE): только ответы с отрицательной оценкой (connotation −1).
    announcements += [
        f"{squeeze(q.get('question', ''))}: {squeeze(q.get('answer', ''))}"
        for q in (d.get("disclosuresEnrichment") or {}).get("questionnaire") or []
        if q.get("connotation") == -1
    ]
    announcements = list(dict.fromkeys(a for a in announcements if a))
    # Свет лота Manheim: зелёный — с гарантией (арбитраж), жёлтый — есть оговорки, красный — as-is, синий — титул не на руках.
    lights = [name for key, name in (("greenLight", "Green"), ("yellowLight", "Yellow"), ("redLight", "Red"), ("blueLight", "Blue")) if d.get(key) is True]
    if d.get("asIs") and "Red" not in lights:
        lights.append("Red")
    row["auction_light"] = " + ".join(lights)
    if announcements and not row.get("auction_notes"):
        row["auction_notes"] = ", ".join(announcements)[:400]
    if re.search(r"\btmu\b|true mileage unknown|not actual|mileage unknown", " ".join(announcements), re.I):
        row["odometer_brand"] = "TMU"

    # --- повреждения: в «Дефекты» — только требующие действия ---
    damages = cond.get("damages") or []
    actionable, informational = [], []
    for dmg in damages:
        text = f"{dmg.get('item', '')}: {dmg.get('damage', '')} {dmg.get('severity', '')}".strip()
        action = dmg.get("action", "")
        if action and action != "No Action Required":
            actionable.append(f"{text} ({action})")
        else:
            informational.append(text)

    tires = [t.get("depth") for t in cond.get("tires") or [] if isinstance(t.get("depth"), (int, float))]
    keys = sum(int(k.get("quantity") or 0) for k in cond.get("keys") or [])
    defects = announcements + actionable
    if keys == 1:
        defects.append("1 key")
    if tires:
        defects.append("tires: " + ", ".join(f"{t:g}/32" for t in tires))
    row["defects"] = " | ".join(defects)[:1500]

    # --- история: AutoCheck ---
    history = []
    if autocheck:
        accidents = autocheck.get("numberOfAccidents")
        owners = autocheck.get("ownerCount")
        if accidents is not None:
            history.append(f"AutoCheck: {accidents} accidents" if accidents else "AutoCheck: 0 accidents")
        if owners:
            history.append(f"{owners} owners")
        if autocheck.get("odometerCheckOK") is False:
            history.append("AutoCheck: odometer problem")
        if autocheck.get("titleAndProblemCheckOK") is False:
            history.append("AutoCheck: title/problem check failed — possible salvage/brand")
        if autocheck.get("vehicleUseAndEventCheckOK") is False:
            history.append("AutoCheck: vehicle use/event check failed (rental/fleet/taxi?)")
        if autocheck.get("score"):
            history.append(f"score {autocheck['score']} (норма {autocheck.get('compareScoreRangeLow')}–{autocheck.get('compareScoreRangeHigh')})")
    row["history_page"] = "; ".join(history)
    row["title_type"] = "AutoCheck: title OK" if autocheck.get("titleAndProblemCheckOK") else squeeze(d.get("titleStatus", ""))
    report = ([f"grade {row['condition_grade']}"] if row["condition_grade"] else []) + announcements + actionable + informational
    row["condition_report"] = " | ".join(report)[:1500]

    row["keys_present"] = f"да ({keys})" if keys else ""
    row["damage_primary"] = row["damage_secondary"] = ""
    drivable = cond.get("drivable", d.get("isDrivable"))
    row["run_and_drive"] = "да" if drivable else ("нет" if drivable is False else "")

    # --- прочее для решения о ставке ---
    extra = []
    if d.get("bidPrice") and d.get("channelSaleType") == "TIMED_SALE":
        extra.append(f"текущая ставка OVE: ${float(d['bidPrice']):,.0f}")
    if d.get("buyNowPrice"):
        extra.append(f"Buy Now: ${float(d['buyNowPrice']):,.0f}")
    sale = d.get("sale") or {}
    if d.get("laneNumber") or d.get("runNumber"):
        extra.append(f"дорожка {d.get('laneNumber', '?')}, номер {d.get('runNumber', '?')}")
    if sale.get("saleDescription"):
        extra.append(squeeze(sale["saleDescription"]))
    if d.get("previousTimesRun"):
        extra.append(f"выставлялась раньше: {d['previousTimesRun']} раз")
    note = (d.get("note") or {}).get("content")
    if note:
        extra.append(f"заметка: {squeeze(note)}")
    if d.get("sellerName"):
        extra.append(f"продавец: {squeeze(d['sellerName'])}")
    if d.get("asIs"):
        extra.append("продаётся as-is (красный свет)")
    if tires:
        extra.append("протектор: " + ", ".join(f"{t:g}/32" for t in tires))
    row["lot_description"] = "; ".join(extra)

    images = [img.get("largeUrl") for img in d.get("images") or [] if img.get("largeUrl")]
    if images:
        row["photo_count"] = str(len(images))
        row["photo_urls"] = " ".join(images)[:1500]
    elif d.get("hasImages") is False or ("images" in d and not images and not d.get("mainImage")):
        row["photo_count"] = "0"
        row["no_photos"] = "да"
    if d.get("mComVdpUrl"):
        row["lot_url"] = d["mComVdpUrl"]
    elif row["vin"]:
        row["lot_url"] = lot_url(row["vin"], d.get("source") or str(d.get("id") or "").split(".")[0])
    main = (d.get("mainImage") or {}).get("largeUrl") or (images[0] if images else "")
    row["photo_main_url"] = main or ""
    cr_link = d.get("conditionReportUrl") or ""
    row["cr_url"] = ("https:" + cr_link) if cr_link.startswith("//") else cr_link
    if not row["condition_grade"] and d.get("conditionGrade"):
        row["condition_grade"] = str(d["conditionGrade"])

    joined = " ".join(announcements).lower()
    if re.search(r"non[\s-]*runner|does not run|no start", joined) and drivable:
        notes.append("в объявлении NON RUNNER, а в Condition Report «на ходу» — уточните у продавца")
    if d.get("asIs"):
        notes.append("as-is: претензии к состоянию после покупки не принимаются")
    if detail.listings_on_page > 1:
        notes.append(f"на странице {detail.listings_on_page} объявлений — взято первое")
    return notes
