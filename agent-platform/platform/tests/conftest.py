"""Тестовый стенд: платформа и агенты поднимаются внутри процесса pytest.

Нужен только Postgres (TEST_DATABASE_URL). Платформа слушает свободный порт,
агенты из agents/ запускаются отдельными процессами, как в docker compose.
"""

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
SDK = ROOT / "platform" / "sdk"
AGENT_DIRS = sorted(p for p in (ROOT / "agents").iterdir() if (p / "agent.yaml").exists() and not p.name.startswith("_"))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_http(url: str, timeout: float = 20) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"{url} не ответил за {timeout} с")


PLATFORM_PORT = free_port()
AGENT_PORTS = {d.name: free_port() for d in AGENT_DIRS}

# Настройки задаются до импорта платформы
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "postgresql://platform:platform@localhost:5432/platform_test")
os.environ["PLATFORM_INTERNAL_URL"] = f"http://127.0.0.1:{PLATFORM_PORT}"
os.environ["PLATFORM_ROOT"] = str(ROOT)
os.environ["LLM_MODE"] = "mock"  # тесты не тратят деньги и не зависят от модели
for name, port in AGENT_PORTS.items():
    os.environ["AGENT_URL_" + name.upper().replace("-", "_")] = f"http://127.0.0.1:{port}"

sys.path.insert(0, str(ROOT / "platform"))
sys.path.insert(0, str(SDK))


def _ensure_database(url: str) -> None:
    import psycopg

    base, dbname = url.rsplit("/", 1)
    with psycopg.connect(base + "/postgres", autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)).fetchone():
            conn.execute(f'CREATE DATABASE "{dbname}"')


@pytest.fixture(scope="session")
def platform():
    import uvicorn

    from app import db

    _ensure_database(os.environ["DATABASE_URL"])
    db.reset_schema()

    from app.main import app

    procs = []
    for d in AGENT_DIRS:
        env = {**os.environ, "PORT": str(AGENT_PORTS[d.name]), "PYTHONPATH": str(SDK)}
        procs.append(subprocess.Popen([sys.executable, str(SDK / "agent_sdk.py"), "main"], cwd=d, env=env))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PLATFORM_PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    try:
        wait_http(f"http://127.0.0.1:{PLATFORM_PORT}/health")
        for name, port in AGENT_PORTS.items():
            wait_http(f"http://127.0.0.1:{port}/health")
        yield f"http://127.0.0.1:{PLATFORM_PORT}"
    finally:
        server.should_exit = True
        for p in procs:
            p.terminate()


@pytest.fixture(scope="session")
def tokens():
    import yaml

    people = yaml.safe_load((ROOT / "config" / "people.yaml").read_text(encoding="utf-8"))
    return {p["id"]: p["token"] for p in people}


@pytest.fixture(scope="session")
def run(platform, tokens):
    """run("ivan", "commercial-proposal", {...}) → (http_status, json)."""

    def _run(person: str, agent: str, input: dict):
        r = httpx.post(f"{platform}/api/agents/{agent}/run", json={"input": input},
                       headers={"Authorization": f"Bearer {tokens[person]}"}, timeout=120)
        return r.status_code, r.json()

    return _run


@pytest.fixture(scope="session")
def get(platform, tokens):
    def _get(person: str, path: str):
        r = httpx.get(f"{platform}{path}", headers={"Authorization": f"Bearer {tokens[person]}"}, timeout=30)
        return r.json()

    return _get


@pytest.fixture(scope="session")
def fake_agent(platform):
    """Агент-заглушка внутри процесса теста: fake_agent(name, fn, context=[...], roles=[...], output=...)."""
    import agent_sdk
    from app.registry import Agent, registry

    servers = []

    def _make(name, fn, *, context, roles, output=None):
        port = free_port()
        server = agent_sdk.make_server(fn, port, host="127.0.0.1")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        registry.register(Agent(
            name=name, version="test", owner="test", description="тестовый агент",
            input={"type": "object", "properties": {"x": {"type": "string"}}},
            output=output or {"type": "object"}, context=context, roles=roles,
            url=f"http://127.0.0.1:{port}"))
        return name

    yield _make
    for s in servers:
        s.shutdown()
