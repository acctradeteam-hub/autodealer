"""Файлы базы (итоги торгов, журнал KBB): чтение — один раз, пока файл не менялся; запись — целиком.

Раньше каждый поиск в окне заново разбирал auction_results.csv и kbb_history.csv (по 3–4 раза за поиск), а запись шла
поверх файла: читатель в этот момент получал полфайла («UnicodeDecodeError» в errors.log 25.11 — обрыв посреди
русской буквы) — а полфайла итогов, сохранённые обратно, потеряли бы остальные записи. Теперь новый файл пишется
рядом и подменяет старый одним действием (os.replace): читатель видит либо старый файл целиком, либо новый."""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable

_CACHE: dict[str, tuple[tuple, dict]] = {}
_LOCK = threading.Lock()


def stamp(path: Path) -> tuple | None:
    """Время, размер и номер файла: поменялось что-то из этого — файл другой."""
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def load(path: Path, parse: Callable[[Path], dict]) -> dict:
    """Разобранный файл — общий для всех: менять его нельзя (нужна своя копия — copy())."""
    key = stamp(path)
    if key is None:
        return {}
    with _LOCK:
        hit = _CACHE.get(str(path))
        if hit and hit[0] == key:
            return hit[1]
    data = parse(path)
    if stamp(path) == key:                     # пока читали, файл не переписали — запоминаем
        with _LOCK:
            _CACHE[str(path)] = (key, data)
    return data


def copy(data: dict) -> dict:
    return {k: dict(v) for k, v in data.items()}


def write(path: Path, fill: Callable) -> None:
    """Пишет файл через временный рядом и подменяет одним действием. fill(handle) — само содержимое."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="") as handle:
            fill(handle)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
