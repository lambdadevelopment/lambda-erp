"""Pricing data for external LLM APIs the chat orchestrator uses.

Mirrors the shape of `lambda-web/backend/providers.py`. Includes active models
and GPT-6 alternatives for comparison. Prices are Standard USD per 1M tokens;
see docs/llm-pricing.md for sources and billing scope.

Consumers import `cost_of_openai_call` to turn an SDK `response.usage` object
into a USD amount. Both Chat Completions and Responses usage shapes are
accepted because the orchestrator and report specialist use different calls.
"""

from __future__ import annotations

from typing import Any, Optional


# ---------------------------------------------------------------------------
# Pricing dicts
# ---------------------------------------------------------------------------

OPENAI_PRICING: dict[str, dict[str, Any]] = {
    # Official model pages, verified 2026-09-29. Above 272K input tokens,
    # the higher rates apply to the entire request (including cache hits).
    "gpt-6-astra": {
        "tiered": True,
        "tiers": [
            {"max_input_tokens": 272_000, "input": 10.00, "cached_input": 1.00, "cache_write": 12.50, "output": 50.00},
            {"max_input_tokens": None,    "input": 20.00, "cached_input": 2.00, "cache_write": 25.00, "output": 75.00},
        ],
    },
    "gpt-6-sol": {
        "tiered": True,
        "tiers": [
            {"max_input_tokens": 272_000, "input": 2.00, "cached_input": 0.20, "cache_write": 2.50, "output": 10.00},
            {"max_input_tokens": None,    "input": 4.00, "cached_input": 0.40, "cache_write": 5.00, "output": 15.00},
        ],
    },
    "gpt-6-luna": {
        "tiered": True,
        "tiers": [
            {"max_input_tokens": 272_000, "input": 0.10, "cached_input": 0.01, "cache_write": 0.125, "output": 0.50},
            {"max_input_tokens": None,    "input": 0.20, "cached_input": 0.02, "cache_write": 0.25, "output": 0.75},
        ],
    },
    "gpt-6.1-sol": {
        "tiered": True,
        "tiers": [
            {"max_input_tokens": 272_000, "input": 2.00, "cached_input": 0.10, "cache_write": 2.50, "output": 10.00},
            {"max_input_tokens": None,    "input": 4.00, "cached_input": 0.20, "cache_write": 5.00, "output": 15.00},
        ],
    },
    "gpt-5.4": {
        "tiered": True,
        "tiers": [
            {"max_input_tokens": 272_000, "input": 2.50, "cached_input": 0.25, "output": 15.00},
            {"max_input_tokens": None,    "input": 5.00, "cached_input": 0.50, "output": 22.50},
        ],
    },
    # GPT-5.6 Terra — tiered like gpt-5.4 (official pricing: 2x input, 1.5x
    # output above 272K input; the 5.6 family also prices cache writes).
    "gpt-5.6-terra": {
        "tiered": True,
        "tiers": [
            {"max_input_tokens": 272_000, "input": 2.00, "cached_input": 0.20, "cache_write": 2.50, "output": 12.00},
            {"max_input_tokens": None,    "input": 4.00, "cached_input": 0.40, "cache_write": 5.00, "output": 18.00},
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

# Speech-to-text, priced per minute of audio (not per token).
TRANSCRIBE_PRICING: dict[str, dict[str, float]] = {
    "gpt-4o-transcribe":      {"per_minute": 0.006},
    "gpt-4o-mini-transcribe": {"per_minute": 0.003},
    "whisper-1":              {"per_minute": 0.006},
}


# ---------------------------------------------------------------------------
# Rate lookup — preserve the legacy gpt-5.4 estimate for unknown model IDs.
# Register a model explicitly before using it; this fallback is not a price cap.
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


# ---------------------------------------------------------------------------
# Cost calculators. `usage` is the SDK's raw usage object; we accept None
# and degrade to 0 so callers don't need to pre-check.
# ---------------------------------------------------------------------------

def cost_of_openai_call(model: str, usage: Optional[Any]) -> float:
    """USD cost of one OpenAI Chat Completions or Responses API call.

    Understands either endpoint's cached-token breakdown so cache hits are
    billed at the discounted rate."""
    if usage is None:
        return 0.0
    prompt_tokens = int(
        getattr(usage, "prompt_tokens", 0)
        or getattr(usage, "input_tokens", 0)
        or 0
    )
    completion_tokens = int(
        getattr(usage, "completion_tokens", 0)
        or getattr(usage, "output_tokens", 0)
        or 0
    )

    cached = 0
    details = (
        getattr(usage, "prompt_tokens_details", None)
        or getattr(usage, "input_tokens_details", None)
    )
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
def cost_of_transcription(model: str, audio_seconds: float) -> float:
    """USD cost of one speech-to-text call, billed per minute of audio.

    Unknown models fall back to the most expensive STT rate so we never
    under-count. `audio_seconds` is the (estimated) audio duration."""
    rates = TRANSCRIBE_PRICING.get(model)
    if rates is None:
        rates = max(TRANSCRIBE_PRICING.values(), key=lambda r: r["per_minute"])
    return max(float(audio_seconds or 0.0), 0.0) / 60.0 * rates["per_minute"]
