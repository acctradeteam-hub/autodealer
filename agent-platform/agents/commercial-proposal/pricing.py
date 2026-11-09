"""Детерминированный расчёт стоимости и сроков. Здесь нет LLM: цифры считает код."""

from __future__ import annotations

from datetime import date, timedelta

# Порядок этапов проекта. Внутри этапа услуги идут параллельно,
# этапы — друг за другом. Сопровождение начинается после запуска.
PHASE = {"ai-audit": 1, "training": 3, "support-sla": 4}
BUILD_PHASE = 2


def fmt_rub(amount: float) -> str:
    return f"{amount:,.0f}".replace(",", " ") + " ₽"


def price_services(selected: list[dict], catalog: dict[str, dict]) -> list[dict]:
    """selected: [{id, qty, reason}] → строки сметы по прайсу."""
    lines = []
    for item in selected:
        rec = catalog[item["id"]]
        meta = rec["meta"]
        qty = max(1, int(item.get("qty") or 1))
        unit_price = float(meta["price"])
        lines.append({
            "id": item["id"],
            "title": meta.get("title", rec["title"]),
            "qty": qty,
            "unit": meta.get("unit", ""),
            "unit_price": unit_price,
            "price": unit_price * qty,
            "weeks": float(meta.get("duration_weeks", 0)),
            "reason": item.get("reason", ""),
        })
    return lines


def plan_timeline(lines: list[dict], deadline: str | None, today: date | None = None) -> dict:
    """Старт — ближайший понедельник; длительность — сумма самых длинных услуг каждого этапа."""
    today = today or date.today()
    start = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    weeks_by_phase: dict[int, float] = {}
    for line in lines:
        phase = PHASE.get(line["id"], BUILD_PHASE)
        if phase < 4:
            weeks_by_phase[phase] = max(weeks_by_phase.get(phase, 0), line["weeks"])
    weeks = int(round(sum(weeks_by_phase.values())))
    end = start + timedelta(weeks=weeks)
    fits = None
    if deadline:
        fits = end <= date.fromisoformat(deadline)
    return {"start": start.isoformat(), "end": end.isoformat(), "weeks": weeks,
            "deadline": deadline, "fits_deadline": fits}


def services_table(lines: list[dict]) -> str:
    rows = ["| Услуга | Кол-во | Цена за ед. | Сумма |", "|---|---|---|---|"]
    for line in lines:
        rows.append(f"| {line['title']} | {line['qty']} {line['unit']} | "
                    f"{fmt_rub(line['unit_price'])} | {fmt_rub(line['price'])} |")
    return "\n".join(rows)
