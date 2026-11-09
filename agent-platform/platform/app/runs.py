"""Run API: проверка роли и входа → вызов агента → проверка выхода → журнал."""

from __future__ import annotations

import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from jsonschema import Draft202012Validator, FormatChecker

from . import config
from .db import Jsonb, connect
from .registry import Agent


class RunRejected(Exception):
    def __init__(self, status: str, http_status: int, message: str, run_id: str):
        super().__init__(message)
        self.status, self.http_status, self.message, self.run_id = status, http_status, message, run_id


def schema_errors(instance, schema) -> list[str]:
    """Понятные сообщения: «поле task: слишком короткое значение…»."""
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    messages = []
    for err in sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path)):
        path = ".".join(str(p) for p in err.absolute_path) or "(корень)"
        messages.append(f"поле {path}: {err.message}")
    return messages


def _insert_run(run_id, agent: Agent, person: dict, status: str, input, error=None):
    with connect() as conn:
        conn.execute(
            """INSERT INTO runs (id, agent, agent_version, person, role, status, input, error, finished_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CASE WHEN %s = 'running' THEN NULL ELSE now() END)""",
            (run_id, agent.name, agent.version, person["id"], person["role"], status, Jsonb(input), error, status),
        )


def log_event(run_id, kind: str, data: dict) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO run_events (run_id, kind, data) VALUES (%s, %s, %s)", (run_id, kind, Jsonb(data)))


def issue_token(run_id, role: str, sections: list[str]) -> str:
    token = "run_" + secrets.token_urlsafe(24)
    expires = datetime.now(timezone.utc) + timedelta(seconds=config.RUN_TOKEN_TTL_SECONDS)
    with connect() as conn:
        conn.execute("INSERT INTO run_tokens (token, run_id, role, sections, expires_at) VALUES (%s, %s, %s, %s, %s)",
                     (token, run_id, role, sections, expires))
    return token


def resolve_token(token: str) -> dict | None:
    with connect() as conn:
        return conn.execute(
            """SELECT t.run_id, t.role, t.sections, r.agent FROM run_tokens t JOIN runs r ON r.id = t.run_id
               WHERE t.token = %s AND t.expires_at > now() AND r.status = 'running'""",
            (token,),
        ).fetchone()


def execute(agent: Agent, person: dict, input: dict) -> dict:
    run_id = str(uuid.uuid4())

    # 1. Роль и вход проверяются до вызова агента
    if person["role"] not in agent.roles:
        msg = (f"Роль «{person['role']}» не может запускать агента «{agent.name}». "
               f"Разрешено: {', '.join(agent.roles)}")
        _insert_run(run_id, agent, person, "denied", input, msg)
        raise RunRejected("denied", 403, msg, run_id)
    errors = schema_errors(input, agent.input)
    if errors:
        msg = "Вход не соответствует схеме агента: " + "; ".join(errors)
        _insert_run(run_id, agent, person, "invalid_input", input, msg)
        raise RunRejected("invalid_input", 422, msg, run_id)

    _insert_run(run_id, agent, person, "running", input)
    token = issue_token(run_id, person["role"], agent.context)
    started = time.monotonic()
    status, output, steps, error = "error", None, [], None

    # 2–4. Вызов агента: он сам ходит в Context API и LLM Gateway с этим токеном
    try:
        resp = httpx.post(f"{agent.url}/run",
                          json={"input": input, "run_token": token, "platform_url": config.PLATFORM_INTERNAL_URL},
                          timeout=config.AGENT_TIMEOUT_SECONDS)
        body = _json(resp)
        steps = body.get("steps") or []
        if resp.status_code != 200:
            error = f"Агент вернул ошибку ({resp.status_code}): {body.get('error') or resp.text[:500]}"
        elif "output" not in body:
            error = "Ответ агента без поля output: ожидается {output, steps}"
        else:
            output = body["output"]
            # 5. Выход проверяется по схеме
            out_errors = schema_errors(output, agent.output)
            if out_errors:
                status = "invalid_output"
                error = "Выход агента не соответствует схеме: " + "; ".join(out_errors)
            else:
                status = "ok"
    except httpx.HTTPError as e:
        error = f"Агент недоступен по адресу {agent.url}: {type(e).__name__}: {e}"
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM run_tokens WHERE run_id = %s", (run_id,))

    with connect() as conn:
        totals = conn.execute(
            """SELECT coalesce(sum((data->'usage'->>'input_tokens')::int), 0) AS i,
                      coalesce(sum((data->'usage'->>'output_tokens')::int), 0) AS o,
                      coalesce(sum((data->>'cost_usd')::numeric), 0) AS c
               FROM run_events WHERE run_id = %s AND kind = 'llm'""", (run_id,)).fetchone()
        conn.execute(
            """UPDATE runs SET status = %s, output = %s, steps = %s, error = %s, finished_at = now(),
                 duration_ms = %s, input_tokens = %s, output_tokens = %s, cost_usd = %s WHERE id = %s""",
            (status, Jsonb(output) if output is not None else None, Jsonb(steps), error,
             round((time.monotonic() - started) * 1000), totals["i"], totals["o"], totals["c"], run_id),
        )
    return get_run(run_id)


def _json(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def get_run(run_id: str) -> dict | None:
    with connect() as conn:
        run = conn.execute("SELECT * FROM runs WHERE id = %s", (run_id,)).fetchone()
        if not run:
            return None
        events = conn.execute("SELECT kind, at, data FROM run_events WHERE run_id = %s ORDER BY id",
                              (run_id,)).fetchall()
    return _serialize(run) | {"events": [{**e, "at": e["at"].isoformat()} for e in events]}


def list_runs(person: dict | None = None, agent: str | None = None, limit: int = 100) -> list[dict]:
    sql, params = "SELECT * FROM runs WHERE true", []
    if person is not None:
        sql += " AND person = %s"
        params.append(person["id"])
    if agent:
        sql += " AND agent = %s"
        params.append(agent)
    sql += " ORDER BY started_at DESC LIMIT %s"
    params.append(limit)
    with connect() as conn:
        return [_serialize(r) for r in conn.execute(sql, params).fetchall()]


def agent_stats() -> dict[str, dict]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT agent, count(*) AS runs, count(*) FILTER (WHERE status = 'ok') AS ok,
                      coalesce(sum(cost_usd), 0) AS cost_usd,
                      coalesce(sum(input_tokens + output_tokens), 0) AS tokens
               FROM runs GROUP BY agent""").fetchall()
    return {r["agent"]: {**r, "cost_usd": float(r["cost_usd"])} for r in rows}


def review(run_id: str, person: dict, edited_output, review_status: str | None, agent: Agent | None) -> dict:
    run = get_run(run_id)
    if edited_output is not None and agent is not None:
        errors = schema_errors(edited_output, agent.output)
        if errors:
            raise ValueError("Правка не соответствует схеме выхода: " + "; ".join(errors))
    if review_status is not None and review_status not in ("draft", "approved", "sent"):
        raise ValueError("Статус: draft, approved или sent")
    with connect() as conn:
        conn.execute(
            """UPDATE runs SET edited_output = coalesce(%s, edited_output),
                 review_status = coalesce(%s, review_status), edited_by = %s, edited_at = now()
               WHERE id = %s""",
            (Jsonb(edited_output) if edited_output is not None else None, review_status, person["id"], run_id),
        )
    log_event(run_id, "review", {"by": person["id"], "status": review_status or run["review_status"],
                                 "edited": edited_output is not None})
    return get_run(run_id)


def _serialize(run: dict) -> dict:
    out = dict(run)
    out["id"] = str(out["id"])
    for k in ("started_at", "finished_at", "edited_at"):
        if out.get(k):
            out[k] = out[k].isoformat()
    out["cost_usd"] = float(out["cost_usd"])
    return out
