"""Where the ERP's model calls go (api/llm_provider.py).

    python -m tests.test_llm_provider
"""

import os
from contextlib import contextmanager
from unittest.mock import patch

import httpx

from api.llm_provider import (
    DEFAULT_LAMBDA_BASE_URL, ERP_USER_HEADER, client_kwargs, llm_configured, llm_settings, not_configured_message,
)

T = httpx.Timeout(5.0)
_VARS = ("LAMBDA_ERP_LLM_PROVIDER", "LAMBDA_LLM_API_KEY", "LAMBDA_LLM_BASE_URL", "OPENAI_API_KEY")


@contextmanager
def env(**values):
    clean = {k: v for k, v in os.environ.items() if k not in _VARS}
    with patch.dict(os.environ, {**clean, **values}, clear=True):
        yield


def check_llm_provider():
    # OpenAI is the default: no base URL of ours, no ERP user header.
    with env(OPENAI_API_KEY="sk-abc"):
        assert client_kwargs(T, erp_user="anna") == {"api_key": "sk-abc", "timeout": T}
        assert llm_settings().provider == "openai" and llm_configured()

    # Lambda: its own key and gateway; names the ERP user.
    with env(LAMBDA_ERP_LLM_PROVIDER="lambda", LAMBDA_LLM_API_KEY="lambda_xyz", OPENAI_API_KEY="sk-not-used"):
        kwargs = client_kwargs(T, erp_user="anna")
        assert kwargs["api_key"] == "lambda_xyz"
        assert kwargs["base_url"] == DEFAULT_LAMBDA_BASE_URL
        assert kwargs["default_headers"] == {ERP_USER_HEADER: "anna"}
        assert "default_headers" not in client_kwargs(T)  # no user, no header
    with env(LAMBDA_ERP_LLM_PROVIDER="Lambda", LAMBDA_LLM_API_KEY="k",
             LAMBDA_LLM_BASE_URL="https://platform.lambdadev.dev/v1/llm/openai/"):
        assert client_kwargs(T)["base_url"] == "https://platform.lambdadev.dev/v1/llm/openai"

    # Not configured: the message names what is missing.
    for values, message in [
        ({}, "OPENAI_API_KEY is not configured"),
        ({"OPENAI_API_KEY": "sk-your-key-here"}, "OPENAI_API_KEY is not configured"),
        ({"LAMBDA_ERP_LLM_PROVIDER": "lambda", "OPENAI_API_KEY": "sk-abc"}, "LAMBDA_LLM_API_KEY is not configured"),
        ({"LAMBDA_ERP_LLM_PROVIDER": "anthropic"}, "LAMBDA_ERP_LLM_PROVIDER='anthropic': use 'openai' or 'lambda'"),
    ]:
        with env(**values):
            assert not llm_configured(), values
            assert not_configured_message() == message, (values, not_configured_message())

    print("PASS: LLM provider settings (openai default, lambda gateway, missing-key messages)")


if __name__ == "__main__":
    check_llm_provider()
