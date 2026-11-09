"""Роли, сотрудники и агенты — из config/ и agents/*/agent.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from . import config


@dataclass
class Agent:
    name: str
    version: str
    owner: str
    description: str
    input: dict
    output: dict
    context: list[str]
    roles: list[str]
    url: str
    tests: list[dict] = field(default_factory=list)

    def card(self) -> dict:
        return {k: getattr(self, k) for k in
                ("name", "version", "owner", "description", "input", "output", "context", "roles")}


class Registry:
    def __init__(self):
        self.roles: dict[str, dict] = {}
        self.people: dict[str, dict] = {}
        self.agents: dict[str, Agent] = {}
        self.errors: list[str] = []

    def load(self) -> None:
        self.errors = []
        self.roles = yaml.safe_load((config.CONFIG_DIR / "roles.yaml").read_text(encoding="utf-8")) or {}
        people = yaml.safe_load((config.CONFIG_DIR / "people.yaml").read_text(encoding="utf-8")) or []
        self.people = {}
        for p in people:
            if p.get("role") not in self.roles:
                self.errors.append(f"people.yaml: у {p.get('id')} неизвестная роль {p.get('role')}")
                continue
            self.people[p["id"]] = p
        self.agents = {}
        for path in sorted(config.AGENTS_DIR.glob("*/agent.yaml")):
            if path.parent.name.startswith("_"):
                continue
            try:
                agent = load_agent(path)
            except (ValueError, KeyError, yaml.YAMLError) as e:
                self.errors.append(f"{path}: {e}")
                continue
            unknown = [r for r in agent.roles if r not in self.roles]
            if unknown:
                self.errors.append(f"{path}: неизвестные роли {unknown}")
            self.agents[agent.name] = agent

    def register(self, agent: Agent) -> None:
        """Добавить агента без файла (используется в тестах)."""
        self.agents[agent.name] = agent

    def person_by_token(self, token: str) -> dict | None:
        return next((p for p in self.people.values() if p.get("token") == token), None)


def load_agent(path: Path) -> Agent:
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    name = spec["name"]
    if name != path.parent.name:
        raise ValueError(f"name «{name}» не совпадает с папкой «{path.parent.name}»")
    for key in ("input", "output"):
        Draft202012Validator.check_schema(spec[key])
    tests_file = path.parent / "tests" / "cases.yaml"
    tests = yaml.safe_load(tests_file.read_text(encoding="utf-8")) if tests_file.exists() else []
    # Адрес агента: AGENT_URL_<ИМЯ> → поле url → http://<имя>:8080 (сервис docker compose)
    env_url = os.environ.get("AGENT_URL_" + name.upper().replace("-", "_"))
    return Agent(
        name=name,
        version=str(spec.get("version", "0.0.0")),
        owner=spec.get("owner", ""),
        description=spec.get("description", ""),
        input=spec["input"],
        output=spec["output"],
        context=list(spec.get("context", [])),
        roles=list(spec.get("roles", [])),
        url=env_url or spec.get("url") or f"http://{name}:8080",
        tests=tests or [],
    )


registry = Registry()
