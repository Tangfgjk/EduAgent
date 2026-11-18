import pytest
from deeptutor.multi_user import personal_api_models as personal, model_access
from deeptutor.services.config.provider_runtime import resolve_llm_runtime_config


def test_private_key_isolation_and_autonomous_resolution(as_user, mu_isolated_root, monkeypatch):
    monkeypatch.setattr(model_access, "load_grant", lambda *_: {"models": {"llm": []}})
    monkeypatch.setattr(model_access, "admin_catalog", lambda: {"services": {"llm": {"profiles": []}}})
    with as_user("alice"):
        result = personal.save("deepseek", "", "deepseek-chat", "alice-secret")
        assert result["configured"] and "alice-secret" not in str(result)
        assert "system" in personal.service().path.parts
        assert model_access.allowed_llm_options()["active"]["profile_id"] == personal.PROFILE_ID
        config = resolve_llm_runtime_config(service_name="task")
        assert config.api_key == "alice-secret" and config.model == "deepseek-chat"
        personal.save("deepseek", "", "deepseek-chat", None)
        assert personal.profiles()[0]["api_key"] == "alice-secret"
    with as_user("bob"):
        assert personal.view() == {"configured": False}
        assert model_access.allowed_llm_options()["options"] == []
        with pytest.raises(ValueError, match="API Key"):
            resolve_llm_runtime_config(service_name="task")
        personal.save("openai", "", "gpt-test", "bob-secret")
        assert resolve_llm_runtime_config().api_key == "bob-secret"
    with as_user("alice"):
        assert resolve_llm_runtime_config().api_key == "alice-secret"
        personal.remove()
        assert not model_access.allowed_llm_options()["options"]


def test_unconfigured_user_never_reads_process_model_cache(as_user, mu_isolated_root, monkeypatch):
    from deeptutor.services.llm import config
    monkeypatch.setattr(model_access, "load_grant", lambda *_: {"models": {"llm": []}})
    monkeypatch.setattr(model_access, "admin_catalog", lambda: {"services": {"llm": {"profiles": []}}})
    monkeypatch.setattr(config, "resolve_llm_runtime_config", resolve_llm_runtime_config)
    monkeypatch.setattr(config, "_LLM_CONFIG_CACHE", object())
    with as_user("bob"):
        with pytest.raises(ValueError, match="API Key"):
            config.get_llm_config()


def test_reject_empty_or_credential_urls(as_user, mu_isolated_root):
    with as_user("alice"):
        with pytest.raises(ValueError):
            personal.save("deepseek", "", "deepseek-chat", None)
        with pytest.raises(ValueError):
            personal.save("openai", "https://key@example.org/v1", "model", "secret")
