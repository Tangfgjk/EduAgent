import os

import pytest

from app.config import Settings


@pytest.fixture
def clean_environment(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("RSI_", "WENJIN_")):
            monkeypatch.delenv(name)


def test_environment_local_default_precedence_and_empty_overrides(tmp_path, monkeypatch, clean_environment):
    defaults = tmp_path / ".env"
    local = tmp_path / ".env.local"
    defaults.write_text("RSI_LOCAL_AUTH_ENABLED=false\nRSI_LOCAL_AUTH_PASSWORD_HASH=\nRSI_LOCAL_PARENT_TOKEN=default\nRSI_LEARNER_ID=default\nRSI_LLM_MODEL=default-model\n", encoding="utf-8")
    local.write_text("RSI_LOCAL_AUTH_ENABLED=true\nRSI_LOCAL_AUTH_PASSWORD_HASH=local-test-hash\nRSI_LOCAL_PARENT_TOKEN=local-test-token\nRSI_LEARNER_ID=local\nRSI_LLM_MODEL=\n", encoding="utf-8")
    monkeypatch.setenv("RSI_LEARNER_ID", "explicit-environment")
    values = Settings.load(defaults)
    assert values.local_auth_enabled and values.local_auth_password_hash == "local-test-hash"
    assert values.local_parent_token == "local-test-token"
    assert values.learner_id == "explicit-environment" and values.llm_model == ""
    assert "RSI_LOCAL_AUTH_ENABLED" not in os.environ
    monkeypatch.setenv("RSI_LOCAL_AUTH_ENABLED", "false")
    monkeypatch.setenv("RSI_LOCAL_AUTH_PASSWORD_HASH", "")
    assert not Settings.load(defaults).local_auth_enabled
    assert Settings.load(defaults).local_auth_password_hash == ""


def test_reloading_reads_updated_files_and_retains_fallback(tmp_path, clean_environment):
    defaults, local = tmp_path / ".env", tmp_path / ".env.local"
    defaults.write_text("RSI_LLM_MODEL=default-model\nRSI_DB_PATH=test.sqlite3\n", encoding="utf-8")
    local.write_text("RSI_LLM_MODEL=first-model\n", encoding="utf-8")
    assert Settings.load(defaults).llm_model == "first-model"
    local.write_text("RSI_LLM_MODEL=second-model\n", encoding="utf-8")
    assert Settings.load(defaults).llm_model == "second-model"
    assert Settings.load(defaults).db_path == "test.sqlite3"


def test_catalog_local_overlays_and_invalid_shape(tmp_path, clean_environment):
    defaults, local = tmp_path / ".env", tmp_path / ".env.local"
    defaults.write_text("RSI_LEARNING_CATALOG_OVERLAYS=[]\n", encoding="utf-8")
    local.write_text('RSI_LEARNING_CATALOG_OVERLAYS=["one.json","two.json"]\n', encoding="utf-8")
    assert Settings.load(defaults).learning_catalog_overlay_paths == ("one.json", "two.json")
    local.write_text('RSI_LEARNING_CATALOG_OVERLAYS={"not":"an array"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="JSON array"):
        Settings.load(defaults)


def test_wenjin_alias_source_then_prefix_precedence(tmp_path, monkeypatch, clean_environment):
    defaults = tmp_path / ".env"
    defaults.write_text("WENJIN_LLM_MODEL=modern-default\nWENJIN_PORT=8003\n", encoding="utf-8")
    defaults.with_name(".env.local").write_text("RSI_LLM_MODEL=legacy-local\nRSI_PORT=8004\n", encoding="utf-8")
    assert Settings.load(defaults).llm_model == "legacy-local"
    assert Settings.load(defaults).port == 8004
    monkeypatch.setenv("RSI_LLM_MODEL", "legacy-env")
    assert Settings.load(defaults).llm_model == "legacy-env"
    monkeypatch.setenv("WENJIN_LLM_MODEL", "")
    assert Settings.load(defaults).llm_model == ""
    monkeypatch.setenv("WENJIN_PORT", "8005")
    assert Settings.load(defaults).port == 8005
