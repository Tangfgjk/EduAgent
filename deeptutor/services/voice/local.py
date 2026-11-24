"""Opt-in, bounded offline Chinese speech on the deployment server.

Models are operator-installed shared assets. Audio/text stay in per-request
private temporary directories; personal provider selections take priority.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import wave

from filelock import FileLock, Timeout as LockTimeout

from .base import VoiceProviderError


def model_root() -> Path:
    from deeptutor.runtime.home import get_runtime_home
    return get_runtime_home() / "data/system/voice/models"


def available(kind: str) -> bool:
    if os.environ.get("WENJIN_LOCAL_VOICE_ENABLED", "").lower() not in {"1", "true", "yes"}:
        return False
    if kind == "tts":
        return importlib.util.find_spec("sherpa_onnx") is not None and all(
            (model_root() / "vits-melo-tts-zh_en" / name).is_file()
            for name in ("model.onnx", "tokens.txt", "lexicon.txt")
        )
    if kind == "stt":
        return importlib.util.find_spec("vosk") is not None and (model_root() / "vosk-model-small-cn-0.22/am/final.mdl").is_file()
    return False


def fallback_allowed(kind: str, catalog=None) -> bool:
    if not available(kind):
        return False
    if catalog is None:
        from deeptutor.services.config import get_model_catalog_service
        catalog = get_model_catalog_service().load()
    return not (catalog.get("services", {}).get(kind, {}).get("active"))


def signature() -> str:
    if not available("tts"):
        raise ValueError("服务器中文语音尚未安装完成，请配置个人语音模型或联系管理员")
    model = model_root() / "vits-melo-tts-zh_en/model.onnx"
    stat = model.stat()
    return "server-melo-v1:" + hashlib.sha256(f"{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()


def _execute(kind, directory: Path, payload: dict, check=lambda: None):
    directory.mkdir(parents=True, exist_ok=True)
    packet = directory / "local-voice-request.json"
    packet.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    packet.chmod(0o600)
    locks = model_root().parent / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(locks / (kind + ".lock")), timeout=15):
            check()
            started = time.monotonic()
            with subprocess.Popen(
                [sys.executable, "-m", "deeptutor.services.voice.local_worker", kind, str(packet)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1"),
            ) as process:
                try:
                    while True:
                        check()
                        if time.monotonic() - started > 180:
                            raise VoiceProviderError("本地语音处理超时，请缩短内容后重试")
                        try:
                            _, _stderr = process.communicate(timeout=0.25)
                            break
                        except subprocess.TimeoutExpired:
                            pass
                    if process.returncode:
                        raise VoiceProviderError("本地语音处理未完成，请检查模型安装或重新录音")
                except BaseException:
                    process.kill()
                    process.wait()
                    raise
    except LockTimeout as exc:
        raise VoiceProviderError("语音服务正忙，请稍后重试") from exc
    finally:
        packet.unlink(missing_ok=True)


def synthesize_segments(parts: list[str], directory: Path, rate=1.0, check=lambda: None):
    if not available("tts"):
        raise ValueError("服务器中文语音尚未就绪")
    if not parts or sum(map(len, parts)) > 6000 or not 0.75 <= rate <= 1.5:
        raise ValueError("配音内容或语速无效，请缩短内容")
    _execute("tts", directory, {"parts": parts, "rate": rate, "model": str(model_root() / "vits-melo-tts-zh_en")}, check)


async def synthesize(text: str, response_format: str | None = None) -> tuple[bytes, str]:
    if response_format not in (None, "wav", "mp3"):
        raise ValueError("本地语音支持 WAV 或 MP3 格式")
    with tempfile.TemporaryDirectory(prefix="wenjin-local-tts-") as temp:
        directory = Path(temp)
        await asyncio.to_thread(synthesize_segments, [text], directory)
        audio = directory / "raw-0.wav"
        if response_format == "mp3":
            await asyncio.to_thread(_encode_mp3, audio, directory / "speech.mp3")
            return (directory / "speech.mp3").read_bytes(), "audio/mpeg"
        return audio.read_bytes(), "audio/wav"


def _encode_mp3(source, target):
    import imageio_ffmpeg
    result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-v", "error", "-i", str(source), str(target)],
                            capture_output=True, timeout=30)
    if result.returncode:
        raise VoiceProviderError("语音格式转换失败")


async def transcribe(audio: bytes, language: str | None = None) -> str:
    from .audio import normalize_wav
    if language and language.lower().split("-")[0] not in {"zh", "cmn"}:
        raise ValueError("内置语音识别当前支持中文，其他语言请配置个人识别模型")
    normalized = await normalize_wav(audio, max_seconds=121)
    try:
        with wave.open(io.BytesIO(normalized), "rb") as source:
            seconds = source.getnframes() / source.getframerate()
    except (wave.Error, EOFError) as exc:
        raise ValueError("录音格式无效，请重新录制") from exc
    if seconds > 120:
        raise ValueError("每段录音最多两分钟，请缩短后重试")
    with tempfile.TemporaryDirectory(prefix="wenjin-local-stt-") as temp:
        directory = Path(temp)
        (directory / "speech.wav").write_bytes(normalized)
        await asyncio.to_thread(_execute, "stt", directory, {"model": str(model_root() / "vosk-model-small-cn-0.22")})
        return json.loads((directory / "transcript.json").read_text(encoding="utf-8"))["text"]
