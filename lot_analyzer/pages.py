"""Чтение входных файлов: .html, .htm и .mhtml/.mht (Chrome «Веб-страница, один файл»)."""

from __future__ import annotations

from pathlib import Path

from . import manheim_csv

SUPPORTED_SUFFIXES = (".html", ".htm", ".xhtml", ".mhtml", ".mht")


def read_page(path: Path) -> str:
    """Возвращает HTML страницы. Для .mhtml достаёт html-часть из архива."""
    if path.suffix.lower() in (".mhtml", ".mht"):
        return _read_mhtml(path)
    data = path.read_bytes()
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _read_mhtml(path: Path) -> str:
    """Собирает html-части MHTML-архива (Chrome сохраняет так «один файл»)."""
    import email
    import email.policy

    message = email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
    chunks: list[str] = []
    for part in message.walk():
        if part.get_content_type() != "text/html":
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            chunks.append(payload.decode(charset, errors="replace"))
        except LookupError:
            chunks.append(payload.decode("utf-8", errors="replace"))
    if not chunks:
        raise ValueError(f"в {path.name} не найдено html-содержимого")
    return "\n".join(chunks)


def collect_inputs(targets: list[str]) -> list[Path]:
    """Разворачивает файлы и папки в список страниц для разбора."""
    pages: list[Path] = []
    for target in targets:
        path = Path(target).expanduser()
        if path.is_dir():
            for candidate in sorted(path.rglob("*")):
                if candidate.is_file() and candidate.suffix.lower() in SUPPORTED_SUFFIXES:
                    pages.append(candidate)
                elif candidate.is_file() and candidate.suffix.lower() == ".csv" and manheim_csv.is_export(candidate):
                    pages.append(candidate)
        elif path.is_file():
            pages.append(path)
        else:
            raise FileNotFoundError(f"не найдено: {path}")
    # Исключаем дубликаты, сохраняя порядок.
    seen: set[Path] = set()
    unique: list[Path] = []
    for page in pages:
        resolved = page.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(page)
    return unique
