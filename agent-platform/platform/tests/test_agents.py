"""Прогон примеров из agents/*/tests/cases.yaml через Run API."""

import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CASES = [
    pytest.param(path.parent.parent.name, case, id=f"{path.parent.parent.name}-{n}")
    for path in sorted(ROOT.glob("agents/*/tests/cases.yaml")) if not path.parent.parent.name.startswith("_")
    for n, case in enumerate(yaml.safe_load(path.read_text(encoding="utf-8")) or [], start=1)
]


def field(data, path):
    for key in path.split("."):
        data = data[key]
    return data


@pytest.mark.parametrize("agent,case", CASES)
def test_agent_case(run, agent, case):
    _, result = run(case["as"], agent, case["input"])
    expect = case.get("expect", {})
    print(f"{agent}: {case['name']}")  # видно в отчёте pytest при падении
    assert result["status"] == expect.get("status", "ok"), f"{case['name']}: {result.get('error')}"
    if result["status"] != "ok":
        return
    output = result["output"]
    text = json.dumps(output, ensure_ascii=False)
    for s in expect.get("contains", []):
        assert s in text, f"в выходе нет «{s}»"
    for s in expect.get("not_contains", []):
        assert s not in text, f"в выходе есть «{s}»"
    for path, value in expect.get("equals", {}).items():
        assert field(output, path) == value, path
    for path, value in expect.get("min", {}).items():
        assert field(output, path) >= value, path
    for path, value in expect.get("min_items", {}).items():
        assert len(field(output, path)) >= value, path
    for path, value in expect.get("max_items", {}).items():
        assert len(field(output, path)) <= value, path
