"""Контекст: markdown-файлы из knowledge/ → Postgres → выдача с учётом роли."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import yaml

from . import config
from .db import Jsonb, connect

REQUIRED = ("section", "access", "owner", "updated")
COLUMNS = "id, section, title, access, owner, updated, meta, body"


class KnowledgeError(ValueError):
    pass


def parse_file(path: Path, root: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.S)
    if not m:
        raise KnowledgeError(f"{path}: нет frontmatter между строками ---")
    meta = yaml.safe_load(m.group(1)) or {}
    missing = [k for k in REQUIRED if k not in meta]
    if missing:
        raise KnowledgeError(f"{path}: в frontmatter нет полей {', '.join(missing)}")
    body = m.group(2).strip()
    heading = re.search(r"^#\s+(.+)$", body, re.M)
    title = meta.get("title") or (heading.group(1).strip() if heading else path.stem)
    access = meta["access"] if isinstance(meta["access"], list) else [meta["access"]]
    updated = meta["updated"]
    extra = {k: v for k, v in meta.items() if k not in REQUIRED}
    extra.setdefault("title", title)
    return {
        "id": path.relative_to(root).with_suffix("").as_posix(),
        "section": str(meta["section"]),
        "title": str(title),
        "access": [str(a) for a in access],
        "owner": str(meta["owner"]),
        "updated": updated if isinstance(updated, date) else date.fromisoformat(str(updated)),
        "meta": _jsonable(extra),
        "body": body,
    }


def _jsonable(value):
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, date):
        return value.isoformat()
    return value


def sync(root: Path | None = None) -> dict:
    """Перечитать knowledge/: добавить новые, обновить изменённые, удалить исчезнувшие."""
    root = root or config.KNOWLEDGE_DIR
    records, errors = [], []
    for path in sorted(root.rglob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        try:
            records.append(parse_file(path, root))
        except (KnowledgeError, yaml.YAMLError, ValueError) as e:
            errors.append(str(e))
    with connect() as conn:
        with conn.transaction():
            for r in records:
                conn.execute(
                    f"""INSERT INTO knowledge ({COLUMNS}, synced_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                        ON CONFLICT (id) DO UPDATE SET section = EXCLUDED.section,
                          title = EXCLUDED.title, access = EXCLUDED.access, owner = EXCLUDED.owner,
                          updated = EXCLUDED.updated, meta = EXCLUDED.meta, body = EXCLUDED.body,
                          synced_at = now()""",
                    (r["id"], r["section"], r["title"], r["access"], r["owner"], r["updated"],
                     Jsonb(r["meta"]), r["body"]),
                )
            ids = [r["id"] for r in records]
            removed = conn.execute("DELETE FROM knowledge WHERE NOT (id = ANY(%s)) RETURNING id", (ids,)).fetchall()
    return {"loaded": len(records), "removed": len(removed), "errors": errors}


def _row(r: dict) -> dict:
    r = dict(r)
    r["updated"] = r["updated"].isoformat() if r.get("updated") else None
    return r


def records(section: str, filters: dict[str, str], role: str) -> tuple[list[dict], int]:
    """Записи раздела по фильтрам: (видимые роли, сколько скрыто ролью)."""
    sql = f"SELECT {COLUMNS} FROM knowledge WHERE section = %s"
    params: list = [section]
    for key, value in filters.items():
        # Поле-список (tags: [...]) совпадает, если содержит значение; строка — без учёта регистра
        sql += (" AND (lower(meta->>%s) = lower(%s)"
                " OR (jsonb_typeof(meta->%s) = 'array' AND meta->%s ? %s))")
        params += [key, value, key, key, value]
    sql += " ORDER BY id"
    with connect() as conn:
        rows = [_row(r) for r in conn.execute(sql, params).fetchall()]
    visible = [r for r in rows if role in r["access"]]
    return visible, len(rows) - len(visible)


def search(query: str, sections: list[str], role: str, limit: int = 5) -> tuple[list[dict], int]:
    """Полнотекстовый поиск: любое слово запроса, по релевантности. (видимые, скрыто ролью)."""
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2][:40]
    if not words:
        return [], 0
    tsquery = " | ".join(words)
    with connect() as conn:
        rows = conn.execute(
            f"""SELECT {COLUMNS}, ts_rank(tsv, q) AS rank
                FROM knowledge, to_tsquery('russian', %s) q
                WHERE section = ANY(%s) AND tsv @@ q
                ORDER BY rank DESC, id""",
            (tsquery, sections),
        ).fetchall()
    rows = [_row(r) for r in rows]
    visible = [r for r in rows if role in r["access"]]
    hidden = len(rows) - len(visible)
    for r in visible:
        r.pop("rank", None)
    return visible[:limit], hidden


def overview(role: str) -> list[dict]:
    """Разделы: всего записей, сколько видит роль, владельцы, дата обновления."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT section, count(*) AS total,
                      count(*) FILTER (WHERE %s = ANY(access)) AS visible,
                      array_agg(DISTINCT owner) AS owners, max(updated) AS updated
               FROM knowledge GROUP BY section ORDER BY section""",
            (role,),
        ).fetchall()
    return [{**r, "updated": r["updated"].isoformat() if r["updated"] else None} for r in rows]


def access_matrix(roles: list[str]) -> dict[str, dict[str, int]]:
    """{раздел: {роль: число видимых записей}} — блок «Кто что видит»."""
    with connect() as conn:
        rows = conn.execute("SELECT section, access FROM knowledge").fetchall()
    matrix: dict[str, dict[str, int]] = {}
    for r in rows:
        cell = matrix.setdefault(r["section"], {role: 0 for role in roles} | {"_total": 0})
        cell["_total"] += 1
        for role in r["access"]:
            if role in cell:
                cell[role] += 1
    return dict(sorted(matrix.items()))


def section_records(section: str, role: str) -> tuple[list[dict], int]:
    return records(section, {}, role)
