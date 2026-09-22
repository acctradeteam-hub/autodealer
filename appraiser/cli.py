"""Точка входа: python -m appraiser.cli --data-dir <папка> --out-dir <папка>."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import load_config
from .io_tables import read_table
from .pipeline import evaluate_lot
from .report import write_cards, write_csv
from .rules import DECISION_BID, DECISION_MANUAL, DECISION_REJECT, DECISION_SKIP
from .sources import ManualCsvProvider


def _read_optional(path: Path) -> list[dict]:
    return read_table(path) if path.exists() else []


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="appraiser",
        description="Оценщик ликвидности: максимальная ставка на аукционе с разбивкой по составляющим.",
    )
    parser.add_argument("--data-dir", required=True, help="папка с lots.csv, comps.csv и справочниками")
    parser.add_argument("--out-dir", default="out", help="куда положить bids.csv и карточки (по умолчанию out)")
    parser.add_argument("--config", default=None, help="путь к config.json (по умолчанию <data-dir>/config.json)")
    parser.add_argument("--lots-file", default="lots.csv")
    parser.add_argument("--quiet", action="store_true", help="не печатать сводку в консоль")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir)

    config_path = Path(args.config) if args.config else data_dir / "config.json"
    cfg = load_config(config_path if config_path.exists() else None)
    if not config_path.exists():
        print(f"[!] Конфиг {config_path} не найден — использованы значения по умолчанию (плейсхолдеры).")

    aliases = cfg.get("column_aliases", {})
    lots = read_table(data_dir / args.lots_file, aliases.get("lots"))
    provider = ManualCsvProvider(data_dir, aliases=aliases.get("comps"))
    comps = provider.comps()
    ranges = provider.deal_ranges()
    thresholds = _read_optional(data_dir / "model_thresholds.csv")
    carriers = _read_optional(data_dir / "carriers.csv")
    lanes = _read_optional(data_dir / "lanes.csv")

    if not lots:
        print(f"[!] В {data_dir / args.lots_file} нет строк лотов.", file=sys.stderr)
        return 1

    results = [
        evaluate_lot(lot, comps, ranges, thresholds, carriers, lanes, cfg)
        for lot in lots
    ]

    csv_path = write_csv(results, out_dir / "bids.csv")
    cards = write_cards(results, out_dir / "cards")

    if not args.quiet:
        _print_summary(results, csv_path, cards, provider, len(comps))
    return 0


def _print_summary(results, csv_path, cards, provider, comps_count: int) -> None:
    counts = {DECISION_BID: 0, DECISION_MANUAL: 0, DECISION_REJECT: 0, DECISION_SKIP: 0}
    for row in results:
        counts[row["decision"]] = counts.get(row["decision"], 0) + 1

    print(f"Источник рыночных данных: {provider.name} ({comps_count} объявлений конкурентов)")
    print(f"Обработано лотов: {len(results)}")
    for decision, count in counts.items():
        if count:
            print(f"  {decision}: {count}")
    print()
    header = f"{'Лот':<10} {'Авто':<28} {'Решение':<16} {'Ставка, $':>11} {'Рынок, $':>10} {'Конк.':>7}"
    print(header)
    print("-" * len(header))
    for row in results:
        car = f"{row['year']} {row['make']} {row['model']}".strip()[:28]
        bid = f"{row['max_bid_usd']:,.0f}" if row.get("max_bid_usd") else "—"
        market = f"{row['market_price_usd']:,.0f}" if row.get("market_price_usd") else "—"
        comps = f"{row.get('comps_used', 0)}/{row.get('comps_found', 0)}"
        print(f"{row['lot_id']:<10} {car:<28} {row['decision']:<16} {bid:>11} {market:>10} {comps:>7}")
    print()
    print(f"CSV: {csv_path}")
    print(f"Карточки: {len(cards)} шт. в {cards[0].parent if cards else '—'}")


if __name__ == "__main__":
    raise SystemExit(main())
