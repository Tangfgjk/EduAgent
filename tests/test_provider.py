import httpx
import pytest
from pydantic import ValidationError

from app.llm.provider import (
    ChatCompletionsProvider, FakeProvider, GenerationRequest, Message,
    ProviderConfig, ProviderError, create_provider,
)


def config(**changes):
    return ProviderConfig(vendor="deepseek", model="test-model", api_key="test-secret", **changes)


def request(**changes):
    return GenerationRequest(messages=(Message(role="user", content="2+3"),), **changes)


@pytest.mark.parametrize('mode',['text','json_object'])
@pytest.mark.asyncio
async def test_deepseek_contract_and_no_reasoning_leak(mode):
    def handle(req):
        import json
        body = json.loads(req.content)
        assert str(req.url) == "https://api.deepseek.com/chat/completions"
        assert req.headers["authorization"] == "Bearer test-secret"
        assert body["max_tokens"] == 128 and body["stream"] is False
        assert body["thinking"] == {"type": "disabled"}
        assert body.get('response_format') == ({'type':'json_object'} if mode=='json_object' else None)
        return httpx.Response(200, json={"model": "resolved-model", "choices": [{
            "finish_reason": "stop", "message": {"content": "5", "reasoning_content": "private"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 1}})
    result = await ChatCompletionsProvider(config(), httpx.MockTransport(handle)).generate(request(response_format=mode))
    assert result.text == "5" and result.returned_model == "resolved-model"
    assert result.usage.input_tokens == 5 and not result.simulated
    assert "private" not in result.model_dump_json() and "text=" not in repr(result)


@pytest.mark.parametrize("status,code", [(401, "authentication_failed"), (403, "authentication_failed"),
    (429, "rate_limited"), (503, "upstream_unavailable"), (302, "upstream_rejected"), (400, "upstream_rejected")])
@pytest.mark.asyncio
async def test_safe_errors_no_retry(status, code):
    calls = []
    def handle(req):
        calls.append(req)
        return httpx.Response(status, text="test-secret and student-private-text", headers={"location": "https://other.example"})
    with pytest.raises(ProviderError, match=code) as error:
        await ChatCompletionsProvider(config(), httpx.MockTransport(handle)).generate(request())
    assert len(calls) == 1
    assert "test-secret" not in str(error.value)


@pytest.mark.asyncio
async def test_timeout_not_retried():
    calls = []
    def handle(req):
        calls.append(req)
        raise httpx.ReadTimeout("private", request=req)
    with pytest.raises(ProviderError, match="timeout"):
        await ChatCompletionsProvider(config(), httpx.MockTransport(handle)).generate(request())
    assert len(calls) == 1


@pytest.mark.parametrize("data", [{}, {"choices": []}, {"choices": [{"message": {"content": None}}]},
    {"choices": [{"message": {"content": "5", "tool_calls": [{}]}, "finish_reason": "stop"}]},
    {"choices": [{"message": {"content": "5"}, "finish_reason": "content_filter"}]}])
@pytest.mark.asyncio
async def test_malformed_and_unsupported_outputs(data):
    with pytest.raises(ProviderError, match="invalid_response"):
        await ChatCompletionsProvider(config(), httpx.MockTransport(lambda req: httpx.Response(200, json=data))).generate(request())


@pytest.mark.asyncio
async def test_budget_rejected_before_network():
    with pytest.raises(ProviderError, match="output_budget_exceeded"):
        await ChatCompletionsProvider(config()).generate(request(max_output_tokens=129))


@pytest.mark.asyncio
async def test_fake_script_and_exhaustion():
    provider = FakeProvider(("5", ProviderError("timeout")))
    assert (await provider.generate(request())).simulated
    with pytest.raises(ProviderError, match="timeout"):
        await provider.generate(request())
    with pytest.raises(ProviderError, match="fake_exhausted"):
        await provider.generate(request())
    assert isinstance(create_provider(ProviderConfig()), FakeProvider)


@pytest.mark.parametrize("url", ["http://api.deepseek.com", "https://key:secret@example.com", "https://example.com?key=secret"])
def test_endpoint_validation(url):
    with pytest.raises(ValidationError):
        config(base_url=url)


def test_fixed_deepseek_host_and_secret_redaction():
    with pytest.raises(ProviderError, match="invalid_deepseek_endpoint"):
        ChatCompletionsProvider(config(base_url="https://other.example"))
    assert "test-secret" not in repr(config())
    with pytest.raises(ValidationError):
        GenerationRequest(messages=(Message(role="user", content="x" * 13000),) * 2)


@pytest.mark.asyncio
async def test_compatible_path_preserved_and_length_is_visible():
    def handle(req):
        assert str(req.url) == "https://example.com/api/v3/chat/completions"
        assert b'"thinking"' not in req.content
        return httpx.Response(200, json={"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}]})
    cfg = ProviderConfig(vendor="openai_compatible", model="model", api_key="test", base_url="https://example.com/api/v3")
    result = await ChatCompletionsProvider(cfg, httpx.MockTransport(handle)).generate(request())
    assert result.finish_reason == "length" and result.usage.input_tokens is None
