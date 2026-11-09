"""Postgres: схема и подключение."""

from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge (
    id          text PRIMARY KEY,          -- путь файла без .md: pricing/ai-audit
    section     text NOT NULL,
    title       text NOT NULL,
    access      text[] NOT NULL,           -- роли, которым видна запись
    owner       text,
    updated     date,
    meta        jsonb NOT NULL DEFAULT '{}',
    body        text NOT NULL,
    tsv         tsvector GENERATED ALWAYS AS (
                    to_tsvector('russian', title || ' ' || body || ' ' || meta::text)) STORED,
    synced_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS knowledge_tsv ON knowledge USING gin (tsv);

CREATE TABLE IF NOT EXISTS runs (
    id            uuid PRIMARY KEY,
    agent         text NOT NULL,
    agent_version text,
    person        text NOT NULL,
    role          text NOT NULL,
    status        text NOT NULL,           -- running | ok | denied | invalid_input | invalid_output | error
    input         jsonb,
    output        jsonb,                   -- оригинал от агента
    steps         jsonb,
    error         text,
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,
    duration_ms   integer,
    input_tokens  integer NOT NULL DEFAULT 0,
    output_tokens integer NOT NULL DEFAULT 0,
    cost_usd      numeric(12, 6) NOT NULL DEFAULT 0,
    review_status text NOT NULL DEFAULT 'draft',   -- draft | approved | sent
    edited_output jsonb,                   -- правки сотрудника, рядом с оригиналом
    edited_by     text,
    edited_at     timestamptz
);

CREATE TABLE IF NOT EXISTS run_tokens (
    token       text PRIMARY KEY,
    run_id      uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    role        text NOT NULL,
    sections    text[] NOT NULL,
    expires_at  timestamptz NOT NULL
);

-- Что платформа выдала агенту и что запретила: основа трассировки
CREATE TABLE IF NOT EXISTS run_events (
    id      bigserial PRIMARY KEY,
    run_id  uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    at      timestamptz NOT NULL DEFAULT now(),
    kind    text NOT NULL,                 -- context | llm | denied | review
    data    jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS run_events_run ON run_events (run_id, id);
"""


@contextmanager
def connect():
    with psycopg.connect(config.DATABASE_URL, row_factory=dict_row, autocommit=True) as conn:
        yield conn


def init_schema() -> None:
    with connect() as conn:
        conn.execute(SCHEMA)


def reset_schema() -> None:
    """Только для тестов: чистая база."""
    with connect() as conn:
        conn.execute("DROP TABLE IF EXISTS run_events, run_tokens, runs, knowledge CASCADE")
        conn.execute(SCHEMA)


__all__ = ["connect", "init_schema", "reset_schema", "Jsonb"]
