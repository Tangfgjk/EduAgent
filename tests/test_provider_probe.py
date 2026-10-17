from app.llm.probe import load_config, probe
from app.llm.provider import ProviderConfig
import pytest


def test_config_from_environment(monkeypatch):
    monkeypatch.setenv("WENJIN_PROVIDER", "deepseek")
    monkeypatch.setenv("WENJIN_MODEL", "configured-model")
    monkeypatch.setenv("WENJIN_PROVIDER_KEY", "local-secret")
    monkeypatch.setenv("WENJIN_PROVIDER_URL", "https://api.deepseek.com")
    cfg = load_config()
    assert cfg.model == "configured-model" and cfg.max_output_tokens == 128
    assert "local-secret" not in repr(cfg)


@pytest.mark.asyncio
async def test_probe_has_no_database_or_student_context(monkeypatch, capsys):
    from app.llm import probe as module
    from app.llm.provider import FakeProvider
    class CapturingFake(FakeProvider):
        async def generate(self, request):
            assert len(request.messages) == 1
            assert request.max_output_tokens == 128
            assert request.messages[0].content == "开发连通测试：请只回答 2+3 的计算结果。"
            return await super().generate(request)
    monkeypatch.setattr(module, "create_provider", lambda cfg: CapturingFake(("private response",)))
    await probe(ProviderConfig())
    output = capsys.readouterr().out
    assert "private response" not in output
    assert "has_text" in output and "True" in output
