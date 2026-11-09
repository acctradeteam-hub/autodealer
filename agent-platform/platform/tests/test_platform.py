"""Проверки границ платформы. Запуск: make test."""

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]

KP_INPUT = {
    "client": "Ромашка Ритейл",
    "task": "Поддержка не справляется с обращениями клиентов в чате, нужна интеграция с amoCRM",
    "budget": 3000000,
    "deadline": "2027-06-30",
    "language": "ru",
}


def cost_records():
    """Записи с себестоимостью: всё, что видят только ceo и finance в разделе pricing."""
    out = []
    for path in (ROOT / "knowledge" / "pricing").glob("*.md"):
        meta = yaml.safe_load(path.read_text(encoding="utf-8").split("---")[1])
        if meta.get("kind") == "cost":
            out.append({"id": f"pricing/{path.stem}", "cost": meta["cost"]})
    assert out, "в тестовых данных должна быть себестоимость"
    return out


def leaks_cost(text: str) -> list[str]:
    found = []
    if re.search(r"себестоимост", text, re.I):
        found.append("слово «себестоимость»")
    for rec in cost_records():
        if rec["id"] in text:
            found.append(rec["id"])
        cost = int(rec["cost"])
        grouped = f"{cost:,}"
        for variant in {str(cost), grouped, grouped.replace(",", " "), grouped.replace(",", " ")}:
            # число целиком: не часть большего числа, не дробная часть (микросекунды в датах)
            if re.search(rf"(?<![\d.])(?<!\d[ , ]){re.escape(variant)}(?!\d)(?![ , ]\d)", text):
                found.append(f"сумма {cost}")
    return found


def test_sales_sees_no_cost_and_trace_shows_hidden(run, get):
    status, result = run("ivan", "commercial-proposal", KP_INPUT)
    assert status == 200 and result["status"] == "ok", result.get("error")

    trace = get("ivan", f"/api/runs/{result['id']}")
    assert leaks_cost(json.dumps(result["output"], ensure_ascii=False)) == []
    assert leaks_cost(json.dumps(trace, ensure_ascii=False)) == []

    hidden = sum(e["data"].get("hidden_by_role", 0) for e in trace["events"] if e["kind"] == "context")
    assert hidden >= len(cost_records()), "в трассировке должно быть видно, сколько записей скрыто ролью"


def test_ceo_gets_cost_records_in_context(run, get):
    status, result = run("olga", "commercial-proposal", KP_INPUT)
    assert result["status"] == "ok", result.get("error")
    trace = get("olga", f"/api/runs/{result['id']}")
    given = {r["id"] for e in trace["events"] if e["kind"] == "context" for r in e["data"]["records"]}
    assert {c["id"] for c in cost_records()} <= given
    # но в КП себестоимость всё равно не попадает: агент строит смету только по ценам
    assert leaks_cost(json.dumps(result["output"], ensure_ascii=False)) == []


def test_delivery_is_refused_before_agent_is_called(run, get, fake_agent):
    calls = []
    name = fake_agent("counting-agent", lambda input, ctx: calls.append(1) or {},
                      context=["cases"], roles=["sales", "ceo"])
    status, result = run("dmitry", name, {"x": "1"})
    assert status == 403 and result["status"] == "denied"
    assert "delivery" in result["error"]
    assert calls == [], "агент не должен вызываться"

    status, result = run("dmitry", "commercial-proposal", KP_INPUT)
    assert status == 403 and result["status"] == "denied"
    trace = get("olga", f"/api/runs/{result['id']}")
    assert trace["events"] == [] and trace["steps"] is None


def test_undeclared_section_is_refused(run, get, fake_agent):
    def rogue(input, ctx):
        ctx.get("cases")       # объявлен — можно
        ctx.get("pricing")     # не объявлен — отказ
        return {}

    name = fake_agent("rogue-agent", rogue, context=["cases"], roles=["ceo"])
    status, result = run("olga", name, {"x": "1"})
    assert result["status"] == "error"
    assert "pricing" in result["error"] and "не объявлен" in result["error"]
    trace = get("olga", f"/api/runs/{result['id']}")
    denied = [e for e in trace["events"] if e["kind"] == "denied"]
    assert denied and denied[0]["data"]["sections"] == ["pricing"]


def test_search_in_undeclared_section_is_refused(run, fake_agent):
    name = fake_agent("rogue-search", lambda input, ctx: ctx.search("себестоимость", section="pricing"),
                      context=["cases"], roles=["ceo"])
    status, result = run("olga", name, {"x": "1"})
    assert result["status"] == "error" and "не объявлен" in result["error"]


def test_output_not_matching_schema_gives_clear_error(run, fake_agent):
    name = fake_agent(
        "bad-output", lambda input, ctx: {"total_price": "дорого"},
        context=[], roles=["ceo"],
        output={"type": "object", "required": ["total_price", "markdown"],
                "properties": {"total_price": {"type": "number"}, "markdown": {"type": "string"}}})
    status, result = run("olga", name, {"x": "1"})
    assert result["status"] == "invalid_output"
    assert "Выход агента не соответствует схеме" in result["error"]
    assert "total_price" in result["error"] and "number" in result["error"]
    assert "markdown" in result["error"]
    assert result["output"] == {"total_price": "дорого"}, "сырой выход сохраняется для разбора"


def test_invalid_input_is_refused_with_field_names(run):
    status, result = run("ivan", "commercial-proposal", {**KP_INPUT, "language": "de", "budget": -5})
    assert status == 422 and result["status"] == "invalid_input"
    assert "language" in result["error"] and "budget" in result["error"]


def test_run_token_dies_with_run(run, platform, fake_agent):
    import httpx

    leaked = {}
    name = fake_agent("token-keeper", lambda input, ctx: leaked.update(token=ctx.run_token) or {},
                      context=["cases"], roles=["ceo"])
    run("olga", name, {"x": "1"})
    r = httpx.get(f"{platform}/api/context/records/cases", headers={"Authorization": f"Bearer {leaked['token']}"})
    assert r.status_code == 401


def test_edit_and_status_kept_next_to_original(run, platform, tokens):
    import httpx

    status, result = run("ivan", "client-brief", {"client": "ТрансЛогистик"})
    edited = {**result["output"], "brief": "Исправленная справка"}
    headers = {"Authorization": f"Bearer {tokens['ivan']}"}
    r = httpx.patch(f"{platform}/api/runs/{result['id']}", headers=headers,
                    json={"edited_output": edited, "review_status": "approved"})
    assert r.status_code == 200
    saved = r.json()
    assert saved["edited_output"]["brief"] == "Исправленная справка"
    assert saved["output"]["brief"] != "Исправленная справка"
    assert saved["review_status"] == "approved"

    # другой продавец чужой запуск не видит
    r = httpx.get(f"{platform}/api/runs/{result['id']}", headers={"Authorization": f"Bearer {tokens['maria']}"})
    assert r.status_code == 403
