"""Python SDK для агентов платформы. Один файл, только стандартная библиотека.

Минимальный агент:

    from agent_sdk import agent, llm

    @agent
    def run(input, ctx):
        records = ctx.get("clients", name=input["client"])
        return {"text": llm("Кратко перескажи: " + records[0]["body"])}

Запуск: python agent_sdk.py main   (main — имя модуля с функцией @agent)

Что делает SDK:
- поднимает HTTP-сервис с POST /run и GET /health;
- даёт ctx.get() и ctx.search() — знания через Context API платформы;
- даёт llm() — модель через LLM Gateway платформы (своих ключей у агента нет);
- сам записывает шаги (steps) для трассировки; свои шаги — ctx.step().
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

__all__ = ["agent", "llm", "Context", "PlatformError", "serve"]

_registered = None
_current = threading.local()


class PlatformError(Exception):
    """Платформа отказала: нет доступа к разделу, истёк токен, ошибка модели."""


def agent(fn):
    """Помечает функцию fn(input, ctx) -> dict как точку входа агента."""
    global _registered
    _registered = fn
    return fn


class Context:
    """Всё, что агент получает от платформы во время одного запуска."""

    def __init__(self, platform_url: str, run_token: str):
        self.platform_url = platform_url.rstrip("/")
        self.run_token = run_token
        self.steps: list[dict] = []

    # --- знания ------------------------------------------------------------
    def get(self, section: str, **filters) -> list[dict]:
        """Записи раздела, которые видит роль запустившего. filters — поля frontmatter."""
        query = urllib.parse.urlencode({k: str(v) for k, v in filters.items()})
        path = f"/api/context/records/{urllib.parse.quote(section)}" + (f"?{query}" if query else "")
        started = time.monotonic()
        records = self._call("GET", path)["records"]
        self._auto_step(f"Контекст: {section}", started, filters=filters,
                        records=[r["id"] for r in records])
        return records

    def search(self, query: str, section: str | None = None, limit: int = 5) -> list[dict]:
        """Полнотекстовый поиск по разделам агента (или по одному разделу)."""
        params = {"q": query, "limit": limit}
        if section:
            params["section"] = section
        started = time.monotonic()
        records = self._call("GET", "/api/context/search?" + urllib.parse.urlencode(params))["records"]
        self._auto_step(f"Поиск: {query[:60]}", started, section=section,
                        records=[r["id"] for r in records])
        return records

    # --- модели ------------------------------------------------------------
    def llm(self, prompt: str | None = None, *, system: str | None = None,
            messages: list | None = None, model: str | None = None,
            max_tokens: int = 4000, mock: str | None = None) -> str:
        """Текст ответа модели. mock — ответ на случай, когда у платформы нет ключа API."""
        if messages is None:
            messages = [{"role": "user", "content": prompt or ""}]
        body = {"messages": messages, "max_tokens": max_tokens}
        if system:
            body["system"] = system
        if model:
            body["model"] = model
        if mock is not None:
            body["mock_response"] = mock
        started = time.monotonic()
        resp = self._call("POST", "/api/llm/messages", body)
        self._auto_step("LLM", started, model=resp["model"], mock=resp["mock"],
                        input_tokens=resp["usage"]["input_tokens"],
                        output_tokens=resp["usage"]["output_tokens"],
                        cost_usd=resp["cost_usd"])
        return resp["text"]

    # --- трассировка -------------------------------------------------------
    def step(self, title: str, **details) -> None:
        """Записать свой шаг в трассировку запуска."""
        self.steps.append({"title": title, "details": details})

    def _auto_step(self, title, started, **details):
        self.steps.append({"title": title, "details": details,
                           "ms": round((time.monotonic() - started) * 1000)})

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.platform_url + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.run_token}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read()).get("detail")
            except Exception:
                detail = e.reason
            raise PlatformError(f"{e.code}: {detail}") from None


def llm(prompt: str | None = None, **kwargs) -> str:
    """То же, что ctx.llm(), но без ctx: берёт контекст текущего запуска."""
    ctx = getattr(_current, "ctx", None)
    if ctx is None:
        raise RuntimeError("llm() можно вызывать только внутри функции @agent")
    return ctx.llm(prompt, **kwargs)


def _make_handler(fn):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # без шума в логах
            pass

        def _reply(self, status, payload):
            data = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._reply(200 if self.path == "/health" else 404, {"ok": self.path == "/health"})

        def do_POST(self):
            if self.path != "/run":
                return self._reply(404, {"error": "not found"})
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            ctx = Context(body["platform_url"], body["run_token"])
            _current.ctx = ctx
            try:
                output = fn(body.get("input", {}), ctx)
                self._reply(200, {"output": output, "steps": ctx.steps})
            except PlatformError as e:
                self._reply(500, {"error": f"Платформа отказала: {e}", "steps": ctx.steps})
            except Exception as e:
                traceback.print_exc()
                self._reply(500, {"error": f"{type(e).__name__}: {e}", "steps": ctx.steps})
            finally:
                _current.ctx = None

    return Handler


def make_server(fn, port: int = 8080, host: str = "0.0.0.0") -> ThreadingHTTPServer:
    """HTTP-сервер агента; serve_forever() запускает его."""
    return ThreadingHTTPServer((host, port), _make_handler(fn))


def serve(module_name: str = "main") -> None:
    sys.path.insert(0, os.getcwd())
    importlib.import_module(module_name)
    if _registered is None:
        sys.exit(f"В модуле {module_name} нет функции с декоратором @agent")
    port = int(os.environ.get("PORT", "8080"))
    print(f"Агент {module_name} слушает порт {port}", flush=True)
    make_server(_registered, port).serve_forever()


if __name__ == "__main__":
    # При запуске как скрипта модуль называется __main__; регистрируем его под
    # именем agent_sdk, чтобы `from agent_sdk import agent` в коде агента
    # попадал в этот же модуль, а не загружал вторую копию.
    sys.modules["agent_sdk"] = sys.modules["__main__"]
    serve(sys.argv[1] if len(sys.argv) > 1 else "main")
