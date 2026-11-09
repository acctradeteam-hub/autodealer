"""Настройки платформы из переменных окружения."""

import os
from pathlib import Path

ROOT = Path(os.environ.get("PLATFORM_ROOT", Path(__file__).resolve().parents[2]))
KNOWLEDGE_DIR = Path(os.environ.get("KNOWLEDGE_DIR", ROOT / "knowledge"))
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", ROOT / "config"))
AGENTS_DIR = Path(os.environ.get("AGENTS_DIR", ROOT / "agents"))

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://platform:platform@localhost:5432/platform")

# Адрес, по которому агенты обращаются к платформе (Context API, LLM Gateway)
PLATFORM_INTERNAL_URL = os.environ.get("PLATFORM_INTERNAL_URL", "http://platform:8000")

RUN_TOKEN_TTL_SECONDS = int(os.environ.get("RUN_TOKEN_TTL_SECONDS", "900"))  # 15 минут
AGENT_TIMEOUT_SECONDS = float(os.environ.get("AGENT_TIMEOUT_SECONDS", "600"))

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
LLM_DEFAULT_MODEL = os.environ.get("LLM_DEFAULT_MODEL", "claude-opus-5-5")
# mock — без обращения к модели; auto — mock, если ключ не задан
LLM_MODE = os.environ.get("LLM_MODE", "auto")
