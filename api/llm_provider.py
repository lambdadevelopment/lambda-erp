"""Where the ERP's model calls go.

    LAMBDA_ERP_LLM_PROVIDER=openai   (default) OpenAI directly with OPENAI_API_KEY.
                                     OPENAI_BASE_URL is honoured by the SDK as
                                     before, for any OpenAI-compatible endpoint.
    LAMBDA_ERP_LLM_PROVIDER=lambda   Lambda's model gateway (lambda.dev): the
                                     same OpenAI API, billed to the Lambda
                                     organization the key belongs to.
                                       LAMBDA_LLM_API_KEY   a Lambda model key
                                                            (scope llm:erp)
                                       LAMBDA_LLM_BASE_URL  default
                                                            https://platform.lambda.dev/v1/llm/openai

The gateway relays what the ERP uses: Responses (chat, report specialist,
titles), file uploads for attachments and voice transcription. Chat
Completions (ERP_CHAT_API=chat) is not available through it. With the lambda
provider each call names the ERP user (``X-Lambda-ERP-User``, the login name),
so Lambda's usage view can split the organization's cost by person; OpenAI
never sees that header.

Every OpenAI client in the ERP is built from ``client_kwargs()``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import httpx
from openai import OpenAI

DEFAULT_LAMBDA_BASE_URL = "https://platform.lambda.dev/v1/llm/openai"
ERP_USER_HEADER = "X-Lambda-ERP-User"
_PLACEHOLDERS = {"", "sk-your-key-here"}


@dataclass(frozen=True)
class LLMSettings:
    provider: str            # "openai" or "lambda"
    api_key: str
    base_url: Optional[str]  # None: the SDK's default (or OPENAI_BASE_URL)
    key_variable: str        # the env variable the key comes from, for messages

    @property
    def configured(self) -> bool:
        return self.api_key.strip() not in _PLACEHOLDERS


def llm_settings() -> LLMSettings:
    """Read on every call, so a changed environment applies without a restart."""
    provider = (os.environ.get("LAMBDA_ERP_LLM_PROVIDER") or "openai").strip().lower()
    if provider == "lambda":
        return LLMSettings(
            provider="lambda",
            api_key=os.environ.get("LAMBDA_LLM_API_KEY", ""),
            base_url=(os.environ.get("LAMBDA_LLM_BASE_URL") or DEFAULT_LAMBDA_BASE_URL).rstrip("/"),
            key_variable="LAMBDA_LLM_API_KEY",
        )
    if provider != "openai":
        raise RuntimeError(f"LAMBDA_ERP_LLM_PROVIDER={provider!r}: use 'openai' or 'lambda'")
    return LLMSettings(provider="openai", api_key=os.environ.get("OPENAI_API_KEY", ""),
                       base_url=None, key_variable="OPENAI_API_KEY")


def llm_configured() -> bool:
    try:
        return llm_settings().configured
    except RuntimeError:
        return False


def not_configured_message() -> str:
    try:
        settings = llm_settings()
    except RuntimeError as exc:
        return str(exc)
    return f"{settings.key_variable} is not configured"


def client_kwargs(timeout: httpx.Timeout, erp_user: Optional[str] = None) -> dict:
    """Keyword arguments for ``OpenAI(...)`` with the configured provider."""
    settings = llm_settings()
    headers = {}
    if settings.provider == "lambda" and erp_user:
        headers[ERP_USER_HEADER] = str(erp_user)[:100]
    kwargs = {"api_key": settings.api_key, "timeout": timeout}
    if settings.base_url:
        kwargs["base_url"] = settings.base_url
    if headers:
        kwargs["default_headers"] = headers
    return kwargs


def openai_client(timeout: httpx.Timeout, erp_user: Optional[str] = None) -> OpenAI:
    """An OpenAI client for the configured provider."""
    return OpenAI(**client_kwargs(timeout, erp_user))
