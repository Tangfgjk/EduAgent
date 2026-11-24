"""Runtime helpers for request-scoped model selection."""

from __future__ import annotations

from contextvars import Token
from dataclasses import replace
import os
from typing import Any

from deeptutor.services.config.provider_runtime import ResolvedLLMConfig, resolve_llm_runtime_config
from deeptutor.services.llm import config as llm_config_module
from deeptutor.services.llm.config import LLMConfig


def llm_config_from_resolved(resolved: ResolvedLLMConfig) -> LLMConfig:
    """Convert provider-runtime output into the LLM service config shape."""
    return LLMConfig(
        model=resolved.model,
        api_key=resolved.api_key,
        base_url=resolved.base_url,
        effective_url=resolved.effective_url,
        binding=resolved.binding,
        provider_name=resolved.provider_name,
        provider_mode=resolved.provider_mode,
        api_version=resolved.api_version,
        extra_headers=resolved.extra_headers,
        wire_api=resolved.wire_api,
        api_format=resolved.api_format,
        reasoning_effort=resolved.reasoning_effort,
        context_window=resolved.context_window,
    )


def resolve_llm_config_for_selection(selection: Any) -> LLMConfig:
    """Resolve the LLM config for a chat/session selection reference."""
    if selection is None:
        return llm_config_module.get_llm_config()
    return llm_config_from_resolved(resolve_llm_runtime_config(llm_selection=selection))


def tutoring_config(config: LLMConfig) -> LLMConfig:
    """Copy an automatic interactive setting; explicit user preferences win."""
    provider = str(config.provider_name or config.binding).lower()
    model = str(config.model or "").lower().split("/")[-1]
    if config.reasoning_effort is not None or provider != "deepseek" or not model.startswith(("deepseek-v4", "deepseek-flash")):
        return config
    effort = os.environ.get("WENJIN_MASTERY_REASONING_EFFORT", "none").strip().lower()
    if effort not in {"none", "low", "high", "max", "native"}:
        raise ValueError("WENJIN_MASTERY_REASONING_EFFORT must be none, low, high, max or native")
    return config if effort == "native" else replace(config, reasoning_effort=effort)


def activate_llm_selection(selection: Any, *, tutoring: bool = False) -> tuple[LLMConfig, Token[LLMConfig | None]]:
    """Resolve and install a scoped LLM config for the current async context."""
    config = resolve_llm_config_for_selection(selection)
    if tutoring:
        config = tutoring_config(config)
    token = llm_config_module.set_scoped_llm_config(config)
    return config, token


def reset_llm_selection(token: Token[LLMConfig | None] | None) -> None:
    if token is not None:
        llm_config_module.reset_scoped_llm_config(token)


__all__ = [
    "activate_llm_selection",
    "llm_config_from_resolved",
    "reset_llm_selection",
    "resolve_llm_config_for_selection",
]
