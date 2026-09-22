"""Запись результатов: TSV, XLSX и накопительная таблица.

Правило про ручной труд: колонки с manual=True никогда не затираются повторным
разбором. Их значения берутся из valuations/manual_values.tsv либо сохраняются
из предыдущей версии накопительной таблицы.
"""

from __future__ import annotations

import csv
from pathlib import Path

from .normalize import clean_cell, clean_vin, squeeze
from .schema import BY_TITLE, COLUMN_KEYS, COLUMN_TITLES, COLUMNS, IDENTITY_KEYS, MANUAL_KEYS, empty_row

# ---------------------------------------------------------------- TSV


def write_tsv(rows: list[dict[str, str]], path: Path) -> Path:
    """Пишет таблицу в TSV (UTF-8 с BOM — чтобы Excel открывал без искажений)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        writer.writerow(COLUMN_TITLES)
        for row in rows:
            writer.writerow([clean_cell(row.get(key, ""), 2000) for key in COLUMN_KEYS])
    return path


def read_tsv(path: Path) -> list[dict[str, str]]:
    """Читает таблицу, записанную write_tsv. Неизвестные колонки игнорируются."""
    if not path.exists():
        return []
    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for record in csv.DictReader(handle, delimiter="\t"):
            row = empty_row()
            for title, value in record.items():
                column = BY_TITLE.get(squeeze(title))
                if column is not None:
                    row[column.key] = squeeze(value)
            rows.append(row)
    return rows


# ---------------------------------------------------------------- XLSX


def write_xlsx(rows: list[dict[str, str]], path: Path, sheet_title: str = "Лоты") -> Path:
    """Пишет XLSX: закреплённая шапка, автофильтр, числовые колонки числами.

    Строки, где есть замечания в колонке «Проверить», подсвечиваются — их надо
    смотреть глазами перед решением о ставке.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_title

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="2F5597")
    manual_fill = PatternFill("solid", fgColor="FFF2CC")   # ручные колонки — светло-жёлтые
    review_fill = PatternFill("solid", fgColor="FCE4E4")   # есть замечания — светло-красный

    sheet.append(list(COLUMN_TITLES))
    for index, column in enumerate(COLUMNS, start=1):
        cell = sheet.cell(row=1, column=index)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        sheet.column_dimensions[get_column_letter(index)].width = column.width
    sheet.row_dimensions[1].height = 30

    for row_index, row in enumerate(rows, start=2):
        has_notes = bool(squeeze(row.get("needs_review", "")))
        for col_index, column in enumerate(COLUMNS, start=1):
            raw = squeeze(row.get(column.key, ""))
            cell = sheet.cell(row=row_index, column=col_index)
            if column.numeric and raw:
                try:
                    number = float(raw)
                    cell.value = int(number) if number.is_integer() else number
                    cell.number_format = "#,##0" if column.key != "year" else "0"
                except ValueError:
                    cell.value = raw
            else:
                cell.value = raw
            cell.alignment = Alignment(vertical="top", wrap_text=column.width >= 30)
            if column.manual:
                cell.fill = manual_fill
            elif has_notes and column.key == "needs_review":
                cell.fill = review_fill

    sheet.freeze_panes = "D2"
    if rows:
        sheet.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{len(rows) + 1}"

    _add_legend_sheet(workbook)
    workbook.save(path)
    return path


def _add_legend_sheet(workbook) -> None:
    """Лист-памятка: что заполняется автоматически, а что руками."""
    from openpyxl.styles import Font

    sheet = workbook.create_sheet("Памятка")
    sheet.column_dimensions["A"].width = 26
    sheet.column_dimensions["B"].width = 78
    sheet["A1"] = "Колонка"
    sheet["B1"] = "Откуда берётся"
    sheet["A1"].font = sheet["B1"].font = Font(bold=True)
    notes = {
        "kbb_private_party_usd": "Вручную: KBB Private Party Value с kbb.com, либо из valuations/manual_values.tsv",
        "mmr_adjusted_usd": "Вручную: Adjusted MMR из Manheim, либо из valuations/manual_values.tsv",
        "cargurus_retail_usd": "Вручную: цена ритейла с CarGurus, цель — рейтинг Great Deal",
        "cargurus_deal_rating": "Вручную: рейтинг CarGurus (Great Deal / Good Deal / Fair Deal)",
        "max_bid_usd": "Вручную: ваша максимальная ставка по лоту",
        "carfax_autocheck": "Вручную: выводы из отчёта Carfax / AutoCheck",
        "condition_report": "Вручную: выводы из Condition Report (Manheim / ACV)",
        "needs_review": "Автоматически: поля, которые парсер не нашёл или посчитал спорными",
        "photo_urls": "Автоматически: ссылки на фото. Дефекты по фото не распознаются автоматически",
    }
    line = 2
    for column in COLUMNS:
        sheet.cell(row=line, column=1, value=column.title)
        sheet.cell(
            row=line,
            column=2,
            value=notes.get(column.key, "Автоматически: со страницы лота"),
        )
        line += 1


# ---------------------------------------------------------------- ручные оценки

MANUAL_TEMPLATE_HEADER = ("VIN", *(column.title for column in COLUMNS if column.manual))


def write_manual_template(path: Path, vins: list[str]) -> Path:
    """Создаёт/дополняет файл ручных оценок, не теряя уже введённые значения."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, dict[str, str]] = {}
    order: list[str] = []
    if path.exists():
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for record in csv.DictReader(handle, delimiter="\t"):
                vin = clean_vin(record.get("VIN", ""))
                if vin:
                    existing[vin] = {k: squeeze(v) for k, v in record.items() if k}
                    order.append(vin)
    for vin in vins:
        if vin and vin not in existing:
            existing[vin] = {"VIN": vin}
            order.append(vin)

    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(MANUAL_TEMPLATE_HEADER)
        for vin in order:
            record = existing.get(vin, {})
            writer.writerow([record.get(title, "") if title != "VIN" else vin for title in MANUAL_TEMPLATE_HEADER])
    return path


def load_manual_values(path: Path) -> dict[str, dict[str, str]]:
    """Читает ручные оценки: VIN -> {ключ колонки: значение}."""
    if not path.exists():
        return {}
    values: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for record in csv.DictReader(handle, delimiter="\t"):
            vin = clean_vin(record.get("VIN", ""))
            if not vin:
                continue
            row: dict[str, str] = {}
            for title, value in record.items():
                column = BY_TITLE.get(squeeze(title or ""))
                if column is not None and column.manual and squeeze(value):
                    row[column.key] = squeeze(value)
            if row:
                values[vin] = row
    return values


def apply_manual_values(rows: list[dict[str, str]], manual: dict[str, dict[str, str]]) -> int:
    """Проставляет ручные оценки по VIN. Возвращает число обновлённых строк."""
    updated = 0
    for row in rows:
        vin = clean_vin(row.get("vin", ""))
        if vin and vin in manual:
            for key, value in manual[vin].items():
                if value:
                    row[key] = value
            updated += 1
    return updated


# ---------------------------------------------------------------- накопительная таблица


def row_identity(row: dict[str, str]) -> str:
    """Ключ строки: VIN, иначе аукцион+лот. Так дубликаты не плодятся."""
    vin = clean_vin(row.get("vin", ""))
    if vin:
        return f"vin:{vin}"
    auction = squeeze(row.get("auction", "")).lower()
    lot = squeeze(row.get("lot_number", "")).lower()
    if lot:
        return f"lot:{auction}/{lot}"
    return f"file:{squeeze(row.get('source_file', ''))}"


def merge_cumulative(new_rows: list[dict[str, str]], path: Path) -> tuple[list[dict[str, str]], int, int]:
    """Объединяет новые строки с накопительной таблицей.

    -> (итоговые строки, добавлено, обновлено). Ручные колонки существующей
    строки сохраняются, если новый разбор их не заполнил.
    """
    existing = read_tsv(path)
    index = {row_identity(row): position for position, row in enumerate(existing)}
    added = updated = 0

    for new_row in new_rows:
        identity = row_identity(new_row)
        if identity in index:
            position = index[identity]
            old_row = existing[position]
            merged = dict(new_row)
            for key in MANUAL_KEYS:
                if not squeeze(merged.get(key, "")) and squeeze(old_row.get(key, "")):
                    merged[key] = old_row[key]
            existing[position] = merged
            updated += 1
        else:
            index[identity] = len(existing)
            existing.append(new_row)
            added += 1

    existing.sort(key=lambda row: (squeeze(row.get("sale_date", "")) or "9999", squeeze(row.get("auction", ""))))
    return existing, added, updated


def identity_fields(row: dict[str, str]) -> str:
    """Короткое описание строки для лога запуска."""
    parts = [row.get(key, "") for key in IDENTITY_KEYS if row.get(key)]
    label = " / ".join(parts) if parts else row.get("source_file", "?")
    name = " ".join(x for x in (row.get("year", ""), row.get("make", ""), row.get("model", "")) if x)
    return f"{label} — {name}" if name else label
