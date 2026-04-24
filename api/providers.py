"""Pricing data for external LLM APIs the chat orchestrator uses.

Mirrors the shape of `lambda-web/backend/providers.py`. Only the models
this app actually calls are listed — refresh this dict when model
choices change. Prices are USD per 1M tokens.

Consumers import `cost_of_openai_call` / `cost_of_anthropic_call` to
turn an SDK `response.usage` object into a USD amount.
"""

from __future__ import annotations

from typing import Any, Optional


# ---------------------------------------------------------------------------
# Pricing dicts
# ---------------------------------------------------------------------------

OPENAI_PRICING: dict[str, dict[str, Any]] = {
    "gpt-5.4": {
        "tiered": True,
        "tiers": [
            {"max_input_tokens": 272_000, "input": 2.50, "cached_input": 0.25, "output": 15.00},
            {"max_input_tokens": None,    "input": 5.00, "cached_input": 0.50, "output": 22.50},
        ],
    },
    "gpt-4.1-nano": {"input": 0.10, "cached_input": 0.025, "output": 0.40},
    "gpt-4.1-mini": {"input": 0.40, "cached_input": 0.10,  "output": 1.60},
    "gpt-4.1":      {"input": 2.00, "cached_input": 0.50,  "output": 8.00},
    "gpt-5":        {"input": 1.25, "cached_input": 0.125, "output": 10.00},
    "gpt-5-mini":   {"input": 0.25, "cached_input": 0.025, "output": 2.00},
    "gpt-5-nano":   {"input": 0.05, "cached_input": 0.005, "output": 0.40},
    "gpt-4o":       {"input": 2.50, "cached_input": 1.25,  "output": 10.00},
    "gpt-4o-mini":  {"input": 0.15, "cached_input": 0.075, "output": 0.60},
}

ANTHROPIC_PRICING: dict[str, dict[str, Any]] = {
    "claude-opus-4-7":   {"input": 5.00, "output": 25.00, "cache_write": 6.25, "cache_read": 0.50},
    "claude-opus-4-6":   {"input": 5.00, "output": 25.00, "cache_write": 6.25, "cache_read": 0.50},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30},
    "claude-haiku-4-5":  {"input": 1.00, "output": 5.00,  "cache_write": 1.25, "cache_read": 0.10},
}


# ---------------------------------------------------------------------------
# Rate lookup — unknown models fall back to the most expensive model in each
# family so we never under-count a call we forgot to register.
# ---------------------------------------------------------------------------

def get_openai_rates(model: str, input_tokens: int = 0) -> dict[str, float]:
    entry = OPENAI_PRICING.get(model)
    if entry is None:
        entry = OPENAI_PRICING["gpt-5.4"]
    if entry.get("tiered"):
        for tier in entry["tiers"]:
            cap = tier.get("max_input_tokens")
            if cap is None or input_tokens <= cap:
                return tier
        return entry["tiers"][-1]
    return entry


def get_anthropic_rates(model: str) -> dict[str, float]:
    return ANTHROPIC_PRICING.get(model) or ANTHROPIC_PRICING["claude-opus-4-7"]


# ---------------------------------------------------------------------------
# Cost calculators. `usage` is the SDK's raw usage object; we accept None
# and degrade to 0 so callers don't need to pre-check.
# ---------------------------------------------------------------------------

def cost_of_openai_call(model: str, usage: Optional[Any]) -> float:
    """USD cost of one OpenAI chat.completions call from `response.usage`.

    Understands the `prompt_tokens_details.cached_tokens` breakdown so cache
    hits are billed at the discounted rate."""
    if usage is None:
        return 0.0
    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)

    cached = 0
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        cached = int(getattr(details, "cached_tokens", 0) or 0)

    rates = get_openai_rates(model, input_tokens=prompt_tokens)
    uncached = max(prompt_tokens - cached, 0)
    cached_rate = rates.get("cached_input", rates["input"])
    total = (
        uncached * rates["input"]
        + cached * cached_rate
        + completion_tokens * rates["output"]
    )
    return total / 1_000_000


def cost_of_anthropic_call(model: str, usage: Optional[Any]) -> float:
    """USD cost of one Anthropic messages.create call from `response.usage`."""
    if usage is None:
        return 0.0
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    cache_create = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)

    rates = get_anthropic_rates(model)
    total = (
        input_tokens * rates["input"]
        + cache_create * rates.get("cache_write", rates["input"])
        + cache_read * rates.get("cache_read", rates["input"])
        + output_tokens * rates["output"]
    )
    return total / 1_000_000
