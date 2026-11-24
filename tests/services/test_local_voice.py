import io
import wave
from unittest.mock import AsyncMock

import pytest

from deeptutor.services.voice import local


def test_local_voice_is_explicitly_enabled_and_requires_installed_models(tmp_path,monkeypatch):
    monkeypatch.setattr(local,"model_root",lambda:tmp_path)
    monkeypatch.setenv("WENJIN_LOCAL_VOICE_ENABLED","false")
    assert not local.available("tts")
    monkeypatch.setenv("WENJIN_LOCAL_VOICE_ENABLED","true")
    monkeypatch.setattr(local.importlib.util,"find_spec",lambda _:object())
    assert not local.available("tts")
    folder=tmp_path/"vits-melo-tts-zh_en"
    folder.mkdir()
    for name in ["model.onnx","tokens.txt","lexicon.txt"]:(folder/name).write_bytes(b"model")
    assert local.available("tts")


def test_local_fallback_never_replaces_an_explicit_personal_selection(monkeypatch):
    monkeypatch.setattr(local,"available",lambda _:True)
    assert local.fallback_allowed("tts",{"services":{"tts":{"active":None}}})
    assert not local.fallback_allowed("tts",{"services":{"tts":{"active":{"profile_id":"my-provider","model_id":"my-model"}}}})


@pytest.mark.asyncio
async def test_recordings_longer_than_two_minutes_are_rejected_before_inference(monkeypatch):
    from deeptutor.services.voice import audio
    output=io.BytesIO()
    with wave.open(output,"wb") as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(bytes(121*16000*2))
    monkeypatch.setattr(audio,"normalize_wav",AsyncMock(return_value=output.getvalue()))
    with pytest.raises(ValueError,match="两分钟"):
        await local.transcribe(b"audio")


@pytest.mark.asyncio
async def test_facade_uses_local_speech_only_when_no_provider_is_selected(monkeypatch):
    from deeptutor.services import voice
    def missing(**kwargs):raise ValueError("not configured")
    monkeypatch.setattr("deeptutor.services.config.provider_runtime.resolve_tts_runtime_config",missing)
    monkeypatch.setattr(local,"available",lambda _:True)
    synth=AsyncMock(return_value=(b"RIFF-audio","audio/wav"))
    monkeypatch.setattr(local,"synthesize",synth)
    result=await voice.synthesize_speech("中文讲解",catalog={"services":{"tts":{"active":None}}})
    assert result==(b"RIFF-audio","audio/wav")
    synth.reset_mock()
    with pytest.raises(ValueError):
        await voice.synthesize_speech("中文讲解",catalog={"services":{"tts":{"active":{"model_id":"missing"}}}})
    synth.assert_not_called()
