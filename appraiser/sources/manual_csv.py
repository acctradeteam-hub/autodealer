"""Провайдер рыночных данных из подготовленных вами файлов."""
from __future__ import annotations

from pathlib import Path

from ..io_tables import read_table


class ManualCsvProvider:
    """Читает comps.csv и cargurus_ranges.csv из каталога с данными."""

    name = "manual_csv"

    def __init__(self, data_dir: str | Path, comps_file: str = "comps.csv",
                 ranges_file: str = "cargurus_ranges.csv", aliases: dict | None = None):
        self.data_dir = Path(data_dir)
        self.comps_file = comps_file
        self.ranges_file = ranges_file
        self.aliases = aliases or {}

    def comps(self) -> list[dict]:
        return read_table(self.data_dir / self.comps_file, self.aliases)

    def deal_ranges(self) -> list[dict]:
        path = self.data_dir / self.ranges_file
        return read_table(path) if path.exists() else []
