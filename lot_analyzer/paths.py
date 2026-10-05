"""Где программа хранит свои данные: одна постоянная папка, не зависящая от папки программы.

Раньше данные лежали в data/ внутри папки программы, и каждая новая версия (новый ZIP — новая папка)
начинала с нуля: без истории торгов, базы KBB, ваших ставок и настроек отправки. Теперь — ~/LotAnalyzer/data
(можно сменить переменной LOT_ANALYZER_DATA). При первом запуске сюда копируется прежняя data/ из старых папок программы.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

DATA_DIR = Path(os.environ.get("LOT_ANALYZER_DATA") or (Path.home() / "LotAnalyzer" / "data")).expanduser()


def _old_data_dirs() -> list[Path]:
    """data/ из папки, где запущена программа, и из старых распакованных версий в «Загрузках» / на рабочем столе."""
    found = [Path("data").resolve()]
    for base in (Path.home() / "Downloads", Path.home() / "Desktop", Path.cwd().parent):
        try:
            found += [d / "data" for d in base.iterdir() if d.is_dir() and d.name.lower().startswith("autodealer")]
        except OSError:
            pass
    return [d for d in dict.fromkeys(found) if d.is_dir() and d.resolve() != DATA_DIR.resolve()]


def migrate() -> list[str]:
    """Копирует в DATA_DIR файлы данных, которых там ещё нет (из самой свежей старой копии каждого). Ничего не удаляет."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    best: dict[str, Path] = {}
    for folder in _old_data_dirs():
        for file in folder.iterdir():
            if file.is_file() and not file.name.startswith("."):
                if file.name not in best or file.stat().st_mtime > best[file.name].stat().st_mtime:
                    best[file.name] = file
    copied = []
    for name, file in best.items():
        target = DATA_DIR / name
        if not target.exists():
            shutil.copy2(file, target)
            copied.append(f"{name} ← {file.parent}")
    return copied
