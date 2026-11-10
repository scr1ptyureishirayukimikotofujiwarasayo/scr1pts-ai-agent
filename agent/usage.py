"""Token estimation, context percentages and cost estimates."""

from __future__ import annotations

import json
from typing import Any, Iterable

CHARS_PER_TOKEN = 4.0


def estimate_tokens(text: str | None) -> int:
    if not text:
        return 0
    return max(1, int(len(text) / CHARS_PER_TOKEN))


def estimate_messages_tokens(messages: Iterable[dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        total += 4  # per-message overhead
        content = message.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif content is not None:
            total += estimate_tokens(json.dumps(content, default=str))
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            total += estimate_tokens(str(function.get("name") or ""))
            total += estimate_tokens(str(function.get("arguments") or ""))
    return total


def estimate_specs_tokens(specs: Iterable[dict[str, Any]] | None) -> int:
    if not specs:
        return 0
    return estimate_tokens(json.dumps(list(specs), default=str))


def format_tokens(value: int | float) -> str:
    number = float(value or 0)
    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M".replace(".0M", "M")
    if number >= 1_000:
        return f"{number / 1_000:.1f}k".replace(".0k", "k")
    return str(int(number))


def format_cost(value: float) -> str:
    number = float(value or 0)
    if number <= 0:
        return "$0"
    if number < 0.01:
        text = f"{number:.5f}".rstrip("0").rstrip(".")
    else:
        text = f"{number:.4f}".rstrip("0").rstrip(".")
    return f"${text}"


def format_percent(tokens: int, limit: int) -> str:
    if limit <= 0:
        return ""
    return f"{tokens / limit * 100:.1f}%"


def context_limit(meta: dict[str, Any] | None) -> int:
    limit = (meta or {}).get("limit")
    if not isinstance(limit, dict):
        return 0
    try:
        return int(limit.get("context") or 0)
    except (TypeError, ValueError):
        return 0


def output_limit(meta: dict[str, Any] | None) -> int:
    limit = (meta or {}).get("limit")
    if not isinstance(limit, dict):
        return 0
    try:
        return int(limit.get("output") or 0)
    except (TypeError, ValueError):
        return 0


def prices(meta: dict[str, Any] | None) -> tuple[float, float]:
    cost = (meta or {}).get("cost")
    if not isinstance(cost, dict):
        return (0.0, 0.0)
    try:
        return (float(cost.get("input") or 0), float(cost.get("output") or 0))
    except (TypeError, ValueError):
        return (0.0, 0.0)


def cost_for(prompt_tokens: int, completion_tokens: int, meta: dict[str, Any] | None) -> float | None:
    """Estimated USD for a token pair, or None when the model has no pricing."""
    price_in, price_out = prices(meta)
    if price_in <= 0 and price_out <= 0:
        return None
    return (prompt_tokens / 1_000_000) * price_in + (completion_tokens / 1_000_000) * price_out
