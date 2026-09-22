"""Формирование результатов: сводный CSV и Markdown-карточка на каждый лот."""
from __future__ import annotations

import csv
from pathlib import Path

CSV_COLUMNS = [
    "lot_id", "vin", "year", "make", "model", "mileage", "condition_grade", "title_status",
    "decision", "max_bid_usd", "market_price_usd", "buyer_fee_usd", "transport_usd",
    "recon_usd", "other_fees_usd", "selling_costs_usd", "holdback_usd", "holdback_basis",
    "comps_found", "comps_used", "supply_label", "price_range_p25_p75", "avg_days_on_market",
    "carrier", "carrier_contact", "reasons",
]


def write_csv(results: list[dict], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in results:
            writer.writerow(row)
    return path


def _fmt(value, suffix="") -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:,.0f}{suffix}"
    return f"{value}{suffix}"


def build_card(result: dict) -> str:
    """Markdown-карточка: пошаговый расчёт, чтобы ставку можно было проверить глазами."""
    estimate = result["_estimate"]
    lot = result["_lot"]
    bid = result.get("_bid")
    transport = result["_transport"]
    recon = result["_recon"]

    title = f"{_fmt(lot.get('year'))} {lot.get('make', '')} {lot.get('model', '')} {lot.get('trim', '') or ''}".strip()
    recon_labels = {
        "base_by_condition": "базовая ставка по состоянию",
        "detail": "детейлинг",
        "safety_inspection": "проверка безопасности",
        "title_and_registration": "оформление титула и регистрация",
    }

    lines = [
        f"# Лот {result['lot_id']} — {title}",
        "",
        f"**Решение: {result['decision']}**",
        "",
        f"- VIN: `{lot.get('vin') or '—'}`",
        f"- Пробег: {_fmt(float(lot['mileage']) if str(lot.get('mileage') or '').strip() else None, ' миль')}",
        f"- Состояние: {lot.get('condition_grade') or '—'} · Титул: {lot.get('title_status') or '—'}",
        f"- Аукцион: {lot.get('auction') or '—'}, {lot.get('auction_location') or '—'}",
        "",
        "## 1. Рынок",
        "",
    ]

    if result.get("_market_skipped"):
        lines += [
            "Рынок не оценивался: лот отсечён стоп-правилом до расчёта.",
            "",
        ]
    else:
        lines += [
            f"- Найдено объявлений конкурентов: **{estimate.comps_found}**, взято в расчёт: **{estimate.comps_used}**"
            + (f" (отброшено выбросов: {estimate.comps_dropped_outliers})" if estimate.comps_dropped_outliers else ""),
            f"- Плотность предложения: **{estimate.supply_label}**"
            + (f", средний срок экспозиции {estimate.avg_days_on_market:.0f} дн." if estimate.avg_days_on_market else ""),
            f"- Диапазон цен конкурентов: {_fmt(estimate.price_min, ' $')} … {_fmt(estimate.price_max, ' $')}"
            f" (середина {_fmt(estimate.price_p25, ' $')} … {_fmt(estimate.price_p75, ' $')})",
            "",
        ]

    if not result.get("_market_skipped") and estimate.price is not None:
        lines += [
            "| Шаг | Сумма |",
            "|---|---:|",
            f"| Медиана конкурентов | {_fmt(estimate.base_median, ' $')} |",
            f"| Поправка на пробег | {estimate.mileage_adjustment:+,.0f} $ |",
            f"| Поправка на состояние | {estimate.condition_adjustment:+,.0f} $ |",
        ]
        if estimate.supply_adjustment:
            lines.append(f"| Поправка на предложение | {estimate.supply_adjustment:+,.0f} $ |")
        if estimate.corridor_cap is not None:
            applied = "применён" if estimate.corridor_applied else "не потребовался"
            lines.append(
                f"| Коридор CarGurus ({estimate.corridor_rating}) | предел {_fmt(estimate.corridor_cap, ' $')}, {applied} |"
            )
        lines.append(f"| Скидка на быструю продажу | −{estimate.quick_sale_discount:,.0f} $ |")
        lines.append(f"| **Цена быстрой продажи** | **{_fmt(estimate.price, ' $')}** |")
        lines.append("")
    elif not result.get("_market_skipped"):
        median = _fmt(estimate.base_median, " $")
        lines += [
            f"**Цена быстрой продажи не рассчитана.** Медиана по найденным объявлениям — {median}, "
            "но данных не хватает для ставки (см. пометки ниже).",
            "",
        ]

    if bid is not None:
        lines += [
            "## 2. Затраты и запас",
            "",
            "| Составляющая | Сумма |",
            "|---|---:|",
            f"| Цена продажи (рынок) | {_fmt(estimate.price, ' $')} |",
            f"| Логистика | −{_fmt(transport.amount, ' $')} |",
            f"| Подготовка | −{_fmt(recon.amount, ' $')} |",
            f"| Сборы аукциона (фикс.) | −{_fmt(result['other_fees_usd_raw'], ' $')} |",
            f"| Расходы на продажу | −{_fmt(result['selling_costs_usd_raw'], ' $')} |",
            f"| Запас ({result['holdback_basis']}) | −{_fmt(bid.holdback, ' $')} |",
            f"| Сбор аукциона от ставки | −{_fmt(bid.buyer_fee, ' $')} |",
            f"| **Максимальная ставка** | **{_fmt(bid.max_bid, ' $')}** |",
            "",
            f"Итого вложений при этой ставке: **{_fmt(bid.total_out_the_door, ' $')}** "
            f"(ставка + сборы + логистика + подготовка + продажа).",
            "",
            "### Логистика",
            "",
            f"- Расчёт: {transport.source or '—'}",
        ]
        if transport.carrier_name:
            lines.append(f"- Перевозчик: {transport.carrier_name} ({transport.carrier_contact or 'контакт не указан'})")
        lines.append("")
        lines.append("### Подготовка")
        lines.append("")
        for key, value in recon.breakdown.items():
            lines.append(f"- {recon_labels.get(key, key)}: {value:,.0f} $")
        lines.append("")

    reasons = result.get("reasons") or ""
    notes = list(estimate.notes) + list(transport.notes) + list(recon.notes)
    if bid is not None:
        notes += list(bid.notes)
    lines += ["## 3. Пометки", ""]
    if reasons:
        lines.append(f"- Сработавшие правила: {reasons}")
    for note in notes:
        lines.append(f"- {note}")
    if not reasons and not notes:
        lines.append("- замечаний нет")
    lines += [
        "",
        "---",
        "",
        "_Ставка рассчитана на данных, поданных в оценщик. Цифры ставок и сборов берутся "
        "из файла конфигурации — проверьте, что там ваши реальные значения._",
    ]
    return "\n".join(lines) + "\n"


def write_cards(results: list[dict], directory: str | Path) -> list[Path]:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for result in results:
        path = directory / f"{result['lot_id']}.md"
        path.write_text(build_card(result), encoding="utf-8")
        written.append(path)
    return written
