"""HTTP API платформы и интерфейс.

Сотрудники и их программы:  Authorization: Bearer <токен сотрудника из config/people.yaml>
Агенты во время запуска:     Authorization: Bearer <run_token, живёт 15 минут>
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, db, knowledge, llm, runs
from .registry import registry

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app):
    db.init_schema()
    registry.load()
    knowledge.sync()  # знания загружаются при каждом старте
    yield


app = FastAPI(title="Agent Platform", lifespan=lifespan)


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(401, "Нужен заголовок Authorization: Bearer <токен>")
    return header[7:].strip()


def person_of(request: Request) -> dict:
    person = registry.person_by_token(_bearer(request))
    if not person:
        raise HTTPException(401, "Неизвестный токен сотрудника")
    return person


def run_context(request: Request) -> dict:
    tok = runs.resolve_token(_bearer(request))
    if not tok:
        raise HTTPException(401, "Токен запуска недействителен: истёк или запуск завершён")
    return tok


def _check_sections(tok: dict, sections: list[str]) -> None:
    undeclared = [s for s in sections if s not in tok["sections"]]
    if undeclared:
        msg = (f"Раздел {', '.join(undeclared)} не объявлен в agent.yaml агента «{tok['agent']}». "
               f"Объявлены: {', '.join(tok['sections'])}")
        runs.log_event(tok["run_id"], "denied", {"sections": undeclared, "reason": msg})
        raise HTTPException(403, msg)


# --- Context API (для агентов) ------------------------------------------------

@app.get("/api/context/records/{section}")
def context_records(section: str, request: Request, limit: int = 100):
    tok = run_context(request)
    _check_sections(tok, [section])
    filters = {k: v for k, v in request.query_params.items() if k != "limit"}
    visible, hidden = knowledge.records(section, filters, tok["role"])
    visible = visible[:limit]
    runs.log_event(tok["run_id"], "context", {
        "call": "get", "section": section, "filters": filters, "hidden_by_role": hidden,
        "records": [{"id": r["id"], "title": r["title"]} for r in visible]})
    return {"records": visible}


@app.get("/api/context/search")
def context_search(request: Request, q: str, section: list[str] | None = Query(None), limit: int = 5):
    tok = run_context(request)
    sections = section or tok["sections"]
    _check_sections(tok, sections)
    visible, hidden = knowledge.search(q, sections, tok["role"], limit)
    runs.log_event(tok["run_id"], "context", {
        "call": "search", "sections": sections, "query": q, "hidden_by_role": hidden,
        "records": [{"id": r["id"], "title": r["title"]} for r in visible]})
    return {"records": visible}


# --- LLM Gateway (для агентов) ------------------------------------------------

@app.post("/api/llm/messages")
def llm_messages(request: Request, body: dict = Body(...)):
    tok = run_context(request)
    try:
        result = llm.complete(body)
    except llm.LLMError as e:
        runs.log_event(tok["run_id"], "llm", {"error": str(e)})
        raise HTTPException(502, str(e))
    runs.log_event(tok["run_id"], "llm", {k: result[k] for k in ("model", "usage", "cost_usd", "mock", "ms")})
    return result


# --- Run API (для сотрудников и их программ) ----------------------------------

@app.post("/api/agents/{name}/run")
def run_agent(name: str, request: Request, body: dict = Body(default={})):
    person = person_of(request)
    agent = registry.agents.get(name)
    if not agent:
        raise HTTPException(404, f"Агент «{name}» не найден")
    try:
        return runs.execute(agent, person, body.get("input", {}))
    except runs.RunRejected as e:
        return JSONResponse({"id": e.run_id, "status": e.status, "error": e.message}, status_code=e.http_status)


def _visible_run(run_id: str, person: dict) -> dict:
    run = runs.get_run(run_id)
    if not run:
        raise HTTPException(404, "Запуск не найден")
    if person["role"] != "ceo" and run["person"] != person["id"]:
        raise HTTPException(403, "Запуск другого сотрудника")
    return run


@app.get("/api/runs")
def list_runs(request: Request, agent: str | None = None):
    person = person_of(request)
    return runs.list_runs(None if person["role"] == "ceo" else person, agent)


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, request: Request):
    return _visible_run(run_id, person_of(request))


@app.patch("/api/runs/{run_id}")
def review_run(run_id: str, request: Request, body: dict = Body(...)):
    person = person_of(request)
    run = _visible_run(run_id, person)
    if run["status"] != "ok":
        raise HTTPException(409, "Править можно только успешный запуск")
    try:
        return runs.review(run_id, person, body.get("edited_output"), body.get("review_status"),
                           registry.agents.get(run["agent"]))
    except ValueError as e:
        raise HTTPException(422, str(e))


# --- Обзор для интерфейса -------------------------------------------------------

@app.get("/api/people")
def people():
    """Демо: список сотрудников с токенами для переключателя «Смотреть как»."""
    return [{k: p[k] for k in ("id", "name", "position", "role", "token")} for p in registry.people.values()]


@app.get("/api/overview")
def overview(request: Request):
    person = person_of(request)
    stats = runs.agent_stats()
    return {
        "person": {k: person[k] for k in ("id", "name", "position", "role")},
        "llm": {"mock": llm.mock_mode(), "default_model": config.LLM_DEFAULT_MODEL},
        "roles": registry.roles,
        "sections": knowledge.overview(person["role"]),
        "access_matrix": knowledge.access_matrix(list(registry.roles)),
        "agents": [a.card() | {"can_run": person["role"] in a.roles, "stats": stats.get(a.name)}
                   for a in registry.agents.values()],
        "errors": registry.errors,
    }


@app.get("/api/knowledge/{section}")
def knowledge_section(section: str, request: Request):
    person = person_of(request)
    visible, hidden = knowledge.section_records(section, person["role"])
    return {"section": section, "records": visible, "hidden_by_role": hidden}


@app.post("/api/knowledge/sync")
def knowledge_sync(request: Request):
    person_of(request)
    result = knowledge.sync()
    registry.load()
    return result


@app.get("/health")
def health():
    return {"ok": True}


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
