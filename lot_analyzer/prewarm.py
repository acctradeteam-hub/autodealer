"""Первый разбор сохранённых страниц — параллельно, в нескольких процессах (на нескольких ядрах Mac).

Страница CarMax разбирается ~1,5 с; после установки программы (или когда автосбор сохранил сразу десятки страниц)
окно ждало, пока они разберутся по одной. Здесь те же страницы разбираются заранее, в фоне, сразу в несколько процессов —
готовое ложится в кэш разобранных страниц на диске (data/page_cache), и окно берёт его оттуда.
Окно разбирает с самых свежих страниц, фон — с самых старых: встречаются посередине.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

_RUNNING = threading.Lock()


def parse_one(path: str, disk: str) -> None:
    """В отдельном процессе: разобрать страницу и положить разобранное на диск."""
    from .app import PageCache
    try:
        PageCache(disk=Path(disk)).rows(Path(path))
    except Exception:  # битый файл — разберёт окно и покажет, что не так
        pass


def start(pages: list[Path], cache, workers: int | None = None) -> threading.Thread | None:
    """Страницы, которых ещё нет в кэше на диске, — в фоне, в несколько процессов. Уже идёт — второй раз не запускаем."""
    todo = [p for p in reversed(pages) if not cache.on_disk(p)]
    if len(todo) < 3 or not _RUNNING.acquire(blocking=False):
        return None
    workers = workers or max(1, min(6, (os.cpu_count() or 2) - 1))

    def run() -> None:
        try:
            import multiprocessing
            from concurrent.futures import ProcessPoolExecutor
            with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn")) as pool:
                list(pool.map(parse_one, [str(p) for p in todo], [str(cache.disk)] * len(todo)))
        except Exception:  # процессы не запустились — окно разберёт само, как раньше
            pass
        finally:
            _RUNNING.release()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread
