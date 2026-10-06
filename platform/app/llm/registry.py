"""Configured provider adapter with explicit fallback and secret-free provenance.

Generation returns an untrusted candidate; teaching consumers must construct a
typed ActionEnvelope and govern it before delivery. Network calls never belong
inside a Storage transaction.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.actions import ActionEnvelope, ActionType
from app.core.rules import ActionGovernor, GovernorContext
from app.llm.client import BaseLLM, FakeLLM, LLMError, OpenAICompatClient


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider_id: str = Field(min_length=1)
    kind: Literal["fake", "openai_compatible"] = "openai_compatible"
    model: str = Field(min_length=1)
    base_url: str | None = None
    api_key_env: str | None = None
    timeout_seconds: float = Field(default=30, gt=0, le=300)
    capabilities: tuple[str, ...] = ("chat", "json")
    fallback_provider: str | None = None
    config_version: str = "1.0.0"

    @model_validator(mode="after")
    def validate_url(self):
        if self.base_url:
            from urllib.parse import urlparse
            parsed = urlparse(self.base_url)
            if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("base_url must be an HTTP endpoint without credentials/query/fragment")
        return self


@dataclass(frozen=True)
class GenerationResult:
    text: str
    provenance: dict
    degraded: bool = False
    trusted_for_delivery: bool = False


class ProviderRegistry:
    client_version = "openai-httpx-v1"

    def __init__(self):
        self._configs: dict[str, ProviderConfig] = {}
        self._clients: dict[str, BaseLLM] = {}

    def register(self, config: ProviderConfig, *, client: BaseLLM | None = None) -> None:
        if config.provider_id in self._configs:
            raise ValueError(f"Provider already registered: {config.provider_id}")
        self._configs[config.provider_id] = config
        if client is not None:
            self._clients[config.provider_id] = client

    def client(self, provider_id: str) -> BaseLLM:
        config = self._configs[provider_id]
        if provider_id not in self._clients:
            if config.kind == "fake":
                self._clients[provider_id] = FakeLLM()
            else:
                if not config.base_url:
                    raise LLMError(f"Provider {provider_id} has no base_url")
                key = os.environ.get(config.api_key_env or "", "")
                if not key:
                    raise LLMError(f"Missing provider secret environment: {config.api_key_env}")
                self._clients[provider_id] = OpenAICompatClient(config.base_url, key, config.model,
                                                              timeout=config.timeout_seconds)
        return self._clients[provider_id]

    def provenance(self, provider_id: str, request_id: str, *, template_version: str = "1.0.0") -> dict:
        config = self._configs[provider_id]
        return {"provider_id": provider_id, "model": config.model,
                "client_version": "fake-scripted-v1" if config.kind == "fake" else self.client_version,
                "config_version": config.config_version,
                "timeout_seconds": config.timeout_seconds, "template_version": template_version,
                "request_id": request_id, "candidate_only": True}

    def generate(self, provider_id: str, messages: list[dict], *, governor: ActionGovernor,
                 context: GovernorContext, request_id: str | None = None,
                 template_version: str = "1.0.0", temperature: float = 0.2) -> GenerationResult:
        request_id = request_id or str(uuid4())
        request = ActionEnvelope(type=ActionType.ENV_OP,
                                 params={"operation": "llm_generate", "provider_id": provider_id},
                                 policy_provenance=self.provenance(provider_id, request_id, template_version=template_version))
        if governor.decide(request, context).decision == "deny":
            raise LLMError("Governor denied provider invocation")
        requested = provider_id
        degraded = False
        try:
            raw = self.client(provider_id).complete(messages, temperature)
        except LLMError:
            fallback = self._configs[provider_id].fallback_provider
            if not fallback or fallback == provider_id:
                raise
            provider_id = fallback
            # No recursive fallbacks: bounded, explicit, independently governed.
            fallback_request = request.model_copy(deep=True)
            fallback_request.params["provider_id"] = provider_id
            fallback_request.policy_provenance = self.provenance(provider_id, request_id, template_version=template_version)
            if governor.decide(fallback_request, context).decision == "deny":
                raise LLMError("Governor denied fallback provider")
            raw = self.client(provider_id).complete(messages, temperature)
            degraded = True
        candidate = request.model_copy(deep=True)
        candidate.params["text"] = raw
        checked = governor.decide(candidate, context)
        if checked.decision == "deny":
            raise LLMError(f"Governor denied generated candidate: {checked.rule_id}")
        provenance = self.provenance(provider_id, request_id, template_version=template_version)
        if degraded:
            provenance["fallback_from"] = requested
        return GenerationResult(str(checked.envelope.params["text"]), provenance, degraded)

    def close(self) -> None:
        for client in self._clients.values():
            if isinstance(client, OpenAICompatClient):
                client._client.close()
