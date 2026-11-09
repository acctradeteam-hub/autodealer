"""LLM Gateway: единственная точка доступа агентов к моделям.

Ключ Anthropic API один и хранится только у платформы (.env). Gateway считает
токены и стоимость каждого вызова и пишет их в журнал запуска.
Без ключа работает демо-режим: возвращает mock-ответ, который прислал агент.
"""

from __future__ import annotations

import time

from . import config

# Цена за 1 млн токенов, USD: (вход, выход)
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}

_client = None


class LLMError(Exception):
    pass


def mock_mode() -> bool:
    return config.LLM_MODE == "mock" or (config.LLM_MODE == "auto" and not config.ANTHROPIC_API_KEY)


def cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICES.get(model, PRICES[config.LLM_DEFAULT_MODEL])
    return round((input_tokens * price_in + output_tokens * price_out) / 1_000_000, 6)


def complete(body: dict) -> dict:
    """body: {messages, system?, model?, max_tokens?, mock_response?} → {text, model, usage, cost_usd, mock}."""
    model = body.get("model") or config.LLM_DEFAULT_MODEL
    if model not in PRICES:
        raise LLMError(f"Модель {model} не разрешена. Доступны: {', '.join(PRICES)}")
    messages = body.get("messages") or []
    if not messages:
        raise LLMError("Пустой запрос: нет messages")
    started = time.monotonic()

    if mock_mode():
        text = body.get("mock_response")
        if text is None:
            text = "[демо-режим: ключ ANTHROPIC_API_KEY не задан, модель не вызывалась]"
        prompt_chars = len(body.get("system") or "") + sum(len(str(m.get("content", ""))) for m in messages)
        # Оценка токенов, чтобы в демо было видно, сколько стоил бы запуск
        usage = {"input_tokens": prompt_chars // 3, "output_tokens": len(text) // 3}
        return {"text": text, "model": model, "usage": usage, "mock": True,
                "cost_usd": cost(model, **usage), "ms": round((time.monotonic() - started) * 1000)}

    import anthropic

    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    params = {"model": model, "max_tokens": int(body.get("max_tokens") or 4000), "messages": messages}
    if body.get("system"):
        params["system"] = body["system"]
    try:
        resp = _client.messages.create(**params)
    except anthropic.APIStatusError as e:
        raise LLMError(f"Anthropic API {e.status_code}: {e.message}") from None
    except anthropic.APIConnectionError as e:
        raise LLMError(f"Нет связи с Anthropic API: {e}") from None
    if resp.stop_reason == "refusal":
        raise LLMError("Модель отказалась отвечать на запрос")
    text = "".join(b.text for b in resp.content if b.type == "text")
    usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
    return {"text": text, "model": resp.model, "usage": usage, "mock": False,
            "cost_usd": cost(resp.model, **usage), "ms": round((time.monotonic() - started) * 1000)}
