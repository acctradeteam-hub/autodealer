"""Точка входа: python3 -m lot_analyzer <файл-или-папка> [...]

Примеры:
    python3 -m lot_analyzer samples/
    python3 -m lot_analyzer samples/copart_lot.html --auction Copart
    python3 -m lot_analyzer samples/ --out out --no-cumulative
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from .pages import collect_inputs, read_page
from .parsers import parse_lot
from .report import (
    apply_manual_values,
    identity_fields,
    load_manual_values,
    merge_cumulative,
    write_manual_template,
    write_tsv,
    write_xlsx,
)

DEFAULT_MANUAL_PATH = Path("valuations/manual_values.tsv")
CUMULATIVE_STEM = "lots_cumulative"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lot_analyzer",
        description="Разбирает сохранённые HTML-страницы аукционных лотов в таблицу TSV/XLSX.",
    )
    parser.add_argument("targets", nargs="+", help="файлы .html/.mhtml или папки с ними")
    parser.add_argument("--out", default="out", help="папка для результатов (по умолчанию out)")
    parser.add_argument("--name", default="", help="имя файлов прогона (по умолчанию lots_ГГГГ-ММ-ДД)")
    parser.add_argument("--auction", default="", help="задать аукцион вручную, если он не определился")
    parser.add_argument(
        "--manual",
        default=str(DEFAULT_MANUAL_PATH),
        help="файл ручных оценок KBB/MMR/CarGurus (по умолчанию valuations/manual_values.tsv)",
    )
    parser.add_argument("--no-cumulative", action="store_true", help="не обновлять накопительную таблицу")
    parser.add_argument("--quiet", action="store_true", help="меньше вывода")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out_dir = Path(args.out)
    say = (lambda *a: None) if args.quiet else (lambda *a: print(*a))

    try:
        pages = collect_inputs(args.targets)
    except FileNotFoundError as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 2

    if not pages:
        print(
            "Не найдено ни одной страницы (.html/.htm/.mhtml).\n"
            "Положите сохранённые страницы лотов в папку samples/ и запустите снова.",
            file=sys.stderr,
        )
        return 1

    say(f"Страниц на разбор: {len(pages)}")
    rows: list[dict[str, str]] = []
    failures: list[tuple[Path, str]] = []

    for page in pages:
        try:
            html = read_page(page)
            row = parse_lot(html, source_name=page.name, auction_hint=args.auction)
        except Exception as error:  # одна битая страница не должна валить весь прогон
            failures.append((page, f"{type(error).__name__}: {error}"))
            say(f"  ! {page.name}: не разобрано ({type(error).__name__}: {error})")
            continue
        rows.append(row)
        marker = "!" if row["needs_review"] else "+"
        say(f"  {marker} {page.name}: {identity_fields(row)}")
        if row["needs_review"]:
            say(f"      проверить: {row['needs_review']}")

    if not rows:
        print("Ни одна страница не разобрана — таблица не создана.", file=sys.stderr)
        return 1

    # Ручные оценки: шаблон дополняем новыми VIN, затем подставляем значения.
    manual_path = Path(args.manual)
    write_manual_template(manual_path, [row["vin"] for row in rows if row["vin"]])
    manual = load_manual_values(manual_path)
    if manual:
        touched = apply_manual_values(rows, manual)
        say(f"Ручные оценки подставлены в строк: {touched} (из {manual_path})")

    stem = args.name or f"lots_{dt.date.today().isoformat()}"
    run_tsv = write_tsv(rows, out_dir / f"{stem}.tsv")
    run_xlsx = write_xlsx(rows, out_dir / f"{stem}.xlsx")
    say(f"\nПрогон:      {run_tsv}\n             {run_xlsx}")

    if not args.no_cumulative:
        cumulative_path = out_dir / f"{CUMULATIVE_STEM}.tsv"
        merged, added, updated = merge_cumulative(rows, cumulative_path)
        write_tsv(merged, cumulative_path)
        cumulative_xlsx = write_xlsx(merged, out_dir / f"{CUMULATIVE_STEM}.xlsx", sheet_title="Все лоты")
        say(
            f"Накопительно: {cumulative_path}\n"
            f"             {cumulative_xlsx}\n"
            f"             всего строк {len(merged)}: новых {added}, обновлено {updated}"
        )

    needs_review = [row for row in rows if row["needs_review"]]
    say(f"\nИтог: строк {len(rows)}, с замечаниями {len(needs_review)}, не разобрано файлов {len(failures)}")
    if needs_review:
        say("Строки с замечаниями проверьте глазами — колонка «Проверить» в XLSX подсвечена.")
    return 0 if not failures else 3


if __name__ == "__main__":
    raise SystemExit(main())
