"""Gemini client — the only model provider in this project (Rule R4).

There is exactly one place a chat model is constructed. Every agent goes through `structured()`,
which returns a validated Pydantic object or degrades; nothing in the graph parses free-form text.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, TypeVar

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from src.config import ConfigError, settings
from src.resilience import DegradedResult, with_resilience

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


@lru_cache(maxsize=4)
def get_llm(model: str | None = None, temperature: float | None = None) -> Any:
    """Build (once) the Gemini chat model. Fails loudly if the key is missing."""
    from langchain_google_genai import ChatGoogleGenerativeAI

    settings.validate_provider_rules()  # Rule R4: no other provider may be configured
    api_key = settings.require_api_key()

    return ChatGoogleGenerativeAI(
        model=model or settings.gemini_model,
        temperature=settings.gemini_temperature if temperature is None else temperature,
        google_api_key=api_key,
        timeout=settings.model_timeout_s,
        max_retries=0,  # retry policy lives in src/resilience.py, not in two places
    )


async def structured(
    schema: type[T],
    *,
    system: str,
    user: str,
    component: str,
    temperature: float | None = None,
) -> T | DegradedResult:
    """Ask Gemini for a validated `schema` instance. Degrades rather than raising (NFR-04)."""
    model = get_llm(temperature=temperature).with_structured_output(schema)
    messages = [SystemMessage(content=system), HumanMessage(content=user)]

    async def _call() -> T:
        result = await model.ainvoke(messages)
        if isinstance(result, schema):
            return result
        return schema.model_validate(result)  # some providers hand back a dict

    return await with_resilience(
        _call, component=component, timeout_s=settings.model_timeout_s
    )


async def text(
    *, system: str, user: str, component: str, temperature: float | None = None
) -> str | DegradedResult:
    """Free-text completion — used only by the summarization middleware (SPEC-03 §2.3)."""
    model = get_llm(temperature=temperature)

    async def _call() -> str:
        resp = await model.ainvoke(
            [SystemMessage(content=system), HumanMessage(content=user)]
        )
        return resp.content if isinstance(resp.content, str) else str(resp.content)

    return await with_resilience(
        _call, component=component, timeout_s=settings.model_timeout_s
    )


def probe() -> tuple[bool, str]:
    """Cheap startup check the CLI uses to fail fast with a legible message."""
    try:
        get_llm()
        return True, f"Gemini ready ({settings.gemini_model})"
    except ConfigError as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, f"Gemini unavailable: {type(exc).__name__}: {exc}"
