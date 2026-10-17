"""New, bounded provider boundary. Does not perform database I/O or execute tools."""
from __future__ import annotations

import asyncio
from collections import deque
from time import perf_counter
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator


class ProviderError(RuntimeError):
    """Safe error code only: never include upstream bodies, headers or prompts."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class Value(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Message(Value):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=16000, repr=False)


class GenerationRequest(Value):
    messages: tuple[Message, ...] = Field(min_length=1, max_length=32, repr=False)
    max_output_tokens: int = Field(default=128, ge=1, le=4096, strict=True)
    temperature: float = Field(default=0.2, ge=0, le=2)

    @field_validator("messages")
    @classmethod
    def bounded_input(cls, messages):
        if sum(len(m.content) for m in messages) > 24000:
            raise ValueError("input character limit exceeded")
        return messages


class Usage(Value):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class GenerationResult(Value):
    provider: str
    requested_model: str
    returned_model: str | None = None
    text: str = Field(repr=False)
    finish_reason: Literal["stop", "length"]
    usage: Usage = Field(default_factory=Usage)
    elapsed_ms: int = Field(ge=0)
    simulated: bool = False


class Provider(Protocol):
    async def generate(self, request: GenerationRequest) -> GenerationResult: ...


class ProviderConfig(Value):
    vendor: Literal["deepseek", "openai_compatible", "fake"] = "fake"
    model: str = Field(default="scripted", min_length=1, max_length=160, pattern=r"^[a-zA-Z0-9_.:/-]+$")
    base_url: str = "https://api.deepseek.com"
    api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    timeout_seconds: float = Field(default=30, ge=1, le=120)
    max_output_tokens: int = Field(default=128, ge=1, le=4096, strict=True)

    @field_validator("base_url")
    @classmethod
    def safe_url(cls, value):
        url = httpx.URL(value)
        if url.scheme != "https" or not url.host or url.userinfo or url.query or url.fragment:
            raise ValueError("HTTPS endpoint without credentials/query/fragment required")
        return str(url).rstrip("/")


class ChatCompletionsProvider:
    """One request, no retries or redirects; caller owns durable budgets/recovery."""

    def __init__(self, config: ProviderConfig, transport: httpx.AsyncBaseTransport | None = None):
        if config.vendor not in ("deepseek", "openai_compatible"):
            raise ProviderError("unsupported_protocol")
        if config.vendor == "deepseek" and config.base_url != "https://api.deepseek.com":
            raise ProviderError("invalid_deepseek_endpoint")
        if not config.api_key.get_secret_value().strip():
            raise ProviderError("missing_api_key")
        self.config = config
        self.transport = transport

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        if request.max_output_tokens > self.config.max_output_tokens:
            raise ProviderError("output_budget_exceeded")
        body = {
            "model": self.config.model,
            "messages": [message.model_dump() for message in request.messages],
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
            "stream": False,
        }
        if self.config.vendor == "deepseek":
            body["thinking"] = {"type": "disabled"}
        started = perf_counter()
        try:
            async with asyncio.timeout(self.config.timeout_seconds):
                async with httpx.AsyncClient(
                    timeout=self.config.timeout_seconds, transport=self.transport,
                    follow_redirects=False, trust_env=False,
                ) as client:
                    response = await client.post(
                        self.config.base_url + "/chat/completions", json=body,
                        headers={"Authorization": "Bearer " + self.config.api_key.get_secret_value()},
                    )
        except (httpx.TimeoutException, TimeoutError):
            raise ProviderError("timeout") from None
        except httpx.HTTPError:
            raise ProviderError("transport_error") from None
        status = response.status_code
        if status in (401, 403):
            raise ProviderError("authentication_failed")
        if status == 429:
            raise ProviderError("rate_limited")
        if status >= 500:
            raise ProviderError("upstream_unavailable")
        if status != 200:
            raise ProviderError("upstream_rejected")
        try:
            data = response.json()
            choice = data["choices"][0]
            text = choice["message"]["content"]
            finish = choice["finish_reason"]
            if not isinstance(text, str) or not text.strip() or finish not in ("stop", "length"):
                raise ValueError("unexpected content")
            if choice["message"].get("tool_calls"):
                raise ValueError("tools not supported")
            usage = data.get("usage") or {}
            return GenerationResult(
                provider=self.config.vendor, requested_model=self.config.model,
                returned_model=data.get("model"), text=text, finish_reason=finish,
                usage=Usage(input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens")),
                elapsed_ms=int((perf_counter() - started) * 1000),
            )
        except (KeyError, IndexError, TypeError, ValueError, ValidationError):
            raise ProviderError("invalid_response") from None


class FakeProvider:
    """Explicit scripted simulation; empty script fails instead of inventing success."""

    def __init__(self, responses: tuple[str | ProviderError, ...] = ()):
        self.responses = deque(responses)

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        if not self.responses:
            raise ProviderError("fake_exhausted")
        item = self.responses.popleft()
        if isinstance(item, ProviderError):
            raise item
        return GenerationResult(provider="fake", requested_model="scripted", text=item,
                                finish_reason="stop", elapsed_ms=0, simulated=True)


def create_provider(config: ProviderConfig) -> Provider:
    if config.vendor == "fake":
        return FakeProvider()
    return ChatCompletionsProvider(config)
