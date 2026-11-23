"""Chapter-aware classroom narration, timed captions and resumable slide videos."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import time
import wave


class Cancelled(Exception):
    pass


def run(args, *, check=lambda: None, timeout=180, env=None):
    """Fixed argument lists only; cancellation terminates the active encoder/TTS."""
    started = time.monotonic()
    with subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    ) as proc:
        try:
            while True:
                check()
                if time.monotonic() - started > timeout:
                    raise TimeoutError("媒体处理超时")
                try:
                    stdout, stderr = proc.communicate(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    continue
            if proc.returncode:
                raise RuntimeError("媒体处理失败，请检查语音或编码环境")
            return stdout
        except BaseException:
            proc.kill()
            proc.communicate()
            raise


def ffmpeg():
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def stamp(seconds):
    ms = round(seconds * 1000)
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02}.{ms % 1000:03}"


def captions(path, cues):
    path.write_text(
        "WEBVTT\n\n" + "\n\n".join(f"{stamp(a)} --> {stamp(b)}\n{t}" for a, b, t in cues),
        encoding="utf-8",
    )


def speech(text):
    """Chinese reading of common school algebra; never send literal TeX commands to TTS."""
    text = re.sub(r"\\(?:begin|end)\{[^}]+\}", "", text)
    for _ in range(5):
        changed = re.sub(r"\\(?:d?frac)\{([^{}]+)\}\{([^{}]+)\}", r"，\2分之\1，", text)
        changed = re.sub(r"\\sqrt\{([^{}]+)\}", r"根号下\1", changed)
        if changed == text:
            break
        text = changed
    text = re.sub(r"\\(?:text|mathrm|mathbf|mathbb)\{([^{}]*)\}", r"\1", text)
    for command, word in [
        ("Rightarrow", "，所以，"),
        ("rightarrow", "，得到，"),
        ("neq", "不等于"),
        ("leq", "小于等于"),
        ("geq", "大于等于"),
        ("times", "乘以"),
        ("cdot", "乘以"),
        ("div", "除以"),
        ("pm", "正负"),
        ("in", "属于"),
    ]:
        text = re.sub(r"\\" + command + r"\b", word, text)
    text = re.sub(r"\^\{?2\}?", "的平方", text)
    text = re.sub(r"\^\{?3\}?", "的三次方", text)
    text = re.sub(r"\\\\", "；", text)
    text = re.sub(r"\\(?:left|right|quad|qquad|,|;|!)", " ", text)
    text = re.sub(r"\\[a-zA-Z]+", " ", text)
    text = re.sub(r"(?<![0-9A-Za-z)])-(?=[0-9A-Za-z])", "负", text)
    text = text.translate(
        str.maketrans(
            {
                "=": "等于",
                "+": "加",
                "-": "减",
                ">": "大于",
                "<": "小于",
                "×": "乘以",
                "÷": "除以",
                "≠": "不等于",
                "≤": "小于等于",
                "≥": "大于等于",
            }
        )
    )
    text = re.sub(r"[${}&`#*]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def script(page):
    visual = page.get("visual") or {}
    # New slides provide a spoken teaching script. Legacy slides retain all panels and formula.
    if visual.get("spoken_script"):
        return speech(visual["spoken_script"])
    if not visual and page.get("kind") != "quiz" and page.get("narration"):
        return speech(page["narration"])
    chunks = [page["title"] + "。", page["content"]]
    chunks += [p["heading"] + "。" + p["text"] for p in visual.get("panels", [])]
    if visual.get("formula"):
        chunks += ["请看这一页的公式。" + visual["formula"]]
    if not visual:
        chunks += page.get("steps", [])
        if page.get("kind") == "quiz":
            chunks += [page.get("question", ""), "；".join(page.get("options", []))]
    return speech("\n".join(chunks))


def chunks(text, limit=90):
    result = []
    for sentence in re.findall(r"[^。！？；!?;]+[。！？；!?;]?", text):
        sentence = sentence.strip()
        while len(sentence) > limit:
            split = max(sentence.rfind("，", 0, limit), sentence.rfind("、", 0, limit))
            split = split + 1 if split >= limit // 3 else limit
            result.append(sentence[:split])
            sentence = sentence[split:]
        if sentence:
            result.append(sentence)
    return result


def config_signature(engine):
    if engine == "local":
        return "windows-zh-auto-v2"
    from deeptutor.services.config.provider_runtime import resolve_tts_runtime_config

    try:
        c = resolve_tts_runtime_config()
    except Exception as exc:
        raise ValueError("请先到设置 → 语音配置自己的语音模型") from exc
    data = [c.provider_name, c.model, c.voice, c.speed, c.sample_rate, c.instructions, c.base_url]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode()).hexdigest()


def capabilities():
    configured = False
    try:
        config_signature("configured")
        configured = True
    except ValueError:
        pass
    return {"local": os.name == "nt", "configured": configured}


def duration(path):
    with wave.open(str(path), "rb") as audio:
        if not audio.getnframes():
            raise ValueError("语音文件为空")
        return audio.getnframes() / audio.getframerate()


def combine(paths, output, pad=0):
    with wave.open(str(output), "wb") as target:
        expected = None
        for path in paths:
            with wave.open(str(path), "rb") as source:
                params = source.getparams()[:3]
                if expected is None:
                    expected = params
                    target.setparams(source.getparams())
                if expected != params:
                    raise ValueError("配音格式不一致，请重新生成")
                # Stream instead of loading a whole lesson into memory.
                while block := source.readframes(24000):
                    target.writeframes(block)
        if pad:
            missing = (-target.getnframes()) % pad
            if missing:
                target.writeframes(bytes(missing * target.getnchannels() * target.getsampwidth()))


def local_segments(parts, root, rate, check):
    if os.name != "nt":
        raise ValueError("本机语音仅支持 Windows，请在设置配置语音模型后选择“我的语音模型”")
    (root / "speech.json").write_text(json.dumps(parts, ensure_ascii=False), encoding="utf-8")
    ps = r"""$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Speech
$s=New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
$voices=@($s.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -like 'zh*' })
if($voices.Count -eq 0){throw 'No Chinese voice installed'}
$s.SelectVoice($voices[0].VoiceInfo.Name)
$s.Rate=[int]$env:WENJIN_SPEECH_RATE
$parts=Get-Content -LiteralPath (Join-Path $env:WENJIN_AUDIO_DIR 'speech.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$i=0
foreach($part in $parts){$s.SetOutputToWaveFile((Join-Path $env:WENJIN_AUDIO_DIR ('raw-'+$i+'.wav')));$s.Speak([string]$part);$s.SetOutputToNull();$i++}
} finally { $s.Dispose() }
"""
    (root / "speech.ps1").write_text(ps, encoding="utf-8-sig")
    env = dict(
        os.environ,
        WENJIN_AUDIO_DIR=str(root.resolve()),
        WENJIN_SPEECH_RATE=str(round((rate - 1) * 6)),
    )
    run(
        ["powershell", "-NoProfile", "-File", str(root / "speech.ps1")],
        check=check,
        env=env,
        timeout=300,
    )


def narrate(page, root, *, engine="local", rate=1.0, check=lambda: None, signature=None):
    root.mkdir(parents=True, exist_ok=True)
    text = script(page)
    if not text or len(text) > 6000:
        raise ValueError("单页讲稿为空或超过6000字，请拆分页面")
    signature = signature or config_signature(engine)
    digest = hashlib.sha256(
        json.dumps([text, engine, rate, signature, "av-v2"], ensure_ascii=False).encode()
    ).hexdigest()
    checkpoint = root / "audio-done.json"
    if checkpoint.is_file():
        saved = json.loads(checkpoint.read_text(encoding="utf-8"))
        if saved["digest"] == digest and (root / "narration.wav").is_file():
            duration(root / "narration.wav")
            return saved["duration"], saved["cues"]
    parts = chunks(text)
    if engine == "local":
        local_segments(parts, root, rate, check)
    else:
        from deeptutor.services.config.provider_runtime import resolve_tts_runtime_config
        from deeptutor.services.voice.adapters import get_tts_adapter
        from deeptutor.services.voice.base import synthesize_with_timeout

        config = resolve_tts_runtime_config()
        config.speed = rate
        for i, part in enumerate(parts):
            check()

            async def synthesize():
                task = asyncio.create_task(
                    synthesize_with_timeout(get_tts_adapter(config.adapter), part, config)
                )
                try:
                    while not task.done():
                        await asyncio.wait({task}, timeout=0.5)
                        check()
                    return await task
                except BaseException:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    raise

            data, _ = asyncio.run(synthesize())
            (root / f"raw-{i}.audio").write_bytes(data)
    cues = []
    offset = 0
    files = []
    for i, part in enumerate(parts):
        check()
        target = root / f"segment-{i}.wav"
        source = root / (f"raw-{i}.wav" if engine == "local" else f"raw-{i}.audio")
        run(
            [
                ffmpeg(),
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-ac",
                "1",
                "-ar",
                "24000",
                "-c:a",
                "pcm_s16le",
                str(target),
            ],
            check=check,
        )
        length = duration(target)
        cues.append([offset, offset + length, part])
        offset += length
        files.append(target)
    combine(files, root / "narration.wav", pad=2400)
    offset = duration(root / "narration.wav")
    captions(root / "subtitles.vtt", cues)
    (root / "script.txt").write_text(text, encoding="utf-8")
    checkpoint.write_text(
        json.dumps({"digest": digest, "duration": offset, "cues": cues}, ensure_ascii=False),
        encoding="utf-8",
    )
    return offset, cues


def video_filter(page, cues, seconds):
    filters = ["scale=1280:720", "fade=t=in:st=0:d=0.3"]
    visual = page.get("visual")
    if not visual or visual.get("layout") == "coordinate":
        return ",".join(filters)
    from deeptutor.learning.slide_visuals import elements

    boxes = [e for e in elements(page) if e["type"] == "rect" and e.get("fill") == "#ffffff"]
    markers = []
    previous = -1
    for panel in visual.get("panels", []):
        heading = speech(panel["heading"]).strip("。")
        found = next(
            (i for i, cue in enumerate(cues) if i > previous and heading and heading in cue[2]),
            None,
        )
        markers.append(found)
        if found is not None:
            previous = found
    for n, start in enumerate(markers):
        if start is None or n >= len(boxes):
            continue
        following = next((i for i in markers[n + 1 :] if i is not None), None)
        end = cues[following][0] if following is not None else seconds
        box = boxes[n]
        x, y, w, h = [round(box[k] * 1280 / 1200) for k in ("x", "y", "w", "h")]
        filters.append(
            f"drawbox=x={x}:y={y}:w={w}:h={h}:color=0x438f72@0.85:t=4:enable='between(t,{cues[start][0]:.3f},{end:.3f})'"
        )
    return ",".join(filters)


def produce(body, root, report=lambda **kw: None):
    """Resume per-page audio and video checkpoints; aggregate exact clip timings."""
    root.mkdir(parents=True, exist_ok=True)
    pages = body["pages"]
    engine = body.get("engine", "local")
    rate = body.get("rate", 1.0)
    signature = config_signature(engine)
    if body.get("voice_signature") and signature != body["voice_signature"]:
        raise ValueError("语音配置已变化，请按当前配置重新提交生成")
    timeline = []
    all_cues = []
    wavs = []
    videos = []
    offset = 0
    total = len(pages)
    for n, page in enumerate(pages):
        report(stage="正在准备配音", completed=n, total=total)
        folder = root / f"page-{n}"
        folder.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(
            json.dumps(
                [str(root.parent), script(page), engine, rate, signature, "av-v2"],
                ensure_ascii=False,
            ).encode()
        ).hexdigest()[:32]
        cache = root.parent.parent / ".audio-cache" / digest
        cache.mkdir(parents=True, exist_ok=True)
        from filelock import FileLock, Timeout

        lock = FileLock(str(cache) + ".lock")
        while True:
            report()
            try:
                lock.acquire(timeout=0)
                break
            except Timeout:
                time.sleep(0.25)
        try:
            seconds, cues = narrate(
                page, cache, engine=engine, rate=rate, check=lambda: report(), signature=signature
            )
        finally:
            lock.release()
        wavs.append(cache / "narration.wav")
        all_cues += [[a + offset, b + offset, t] for a, b, t in cues]
        timeline.append(
            {
                "page_id": page.get("id", f"scene-{n}"),
                "index": body.get("indices", list(range(total)))[n],
                "title": page["title"],
                "start": offset,
                "end": offset + seconds,
            }
        )
        offset += seconds
        if body["kind"] == "video":
            report(stage="正在合成视频", completed=n, total=total)
            target = root / f"part-{n}.mp4"
            done = root / f"part-{n}.done"
            if not done.is_file() or not target.is_file():
                if page.get("visual"):
                    from deeptutor.learning.slide_visuals import png

                    png(page, folder / "slide.png")
                else:
                    from PIL import Image, ImageDraw, ImageFont

                    from deeptutor.learning.classroom_media import font_path, page_text
                    from deeptutor.learning.slide_visuals import wrap

                    image = Image.new("RGB", (1280, 720), "#f5f7f2")
                    draw = ImageDraw.Draw(image)
                    draw.text(
                        (45, 30),
                        page["title"],
                        fill="#174d3a",
                        font=ImageFont.truetype(font_path(), 32),
                    )
                    for j, line in enumerate(wrap(page_text(page), 85)[:18]):
                        draw.text(
                            (45, 100 + j * 30),
                            line,
                            fill="#233b34",
                            font=ImageFont.truetype(font_path(), 23),
                        )
                    image.save(folder / "slide.png")
                # A gentle reveal keeps the teaching page readable, with an actual audio track.
                run(
                    [
                        ffmpeg(),
                        "-y",
                        "-loglevel",
                        "error",
                        "-loop",
                        "1",
                        "-framerate",
                        "10",
                        "-i",
                        str(folder / "slide.png"),
                        "-t",
                        str(seconds),
                        "-vf",
                        video_filter(page, cues, seconds),
                        "-c:v",
                        "libx264",
                        "-preset",
                        "ultrafast",
                        "-pix_fmt",
                        "yuv420p",
                        "-an",
                        str(target),
                    ],
                    check=lambda: report(),
                    timeout=max(180, seconds * 3),
                )
                done.write_text("ready", encoding="utf-8")
            videos.append(target)
        report(stage="页面已准备", completed=n + 1, total=total)
    report(stage="正在整理字幕与讲稿", completed=total, total=total)
    combine(wavs, root / "narration.wav")
    captions(root / "subtitles.vtt", all_cues)
    (root / "script.txt").write_text(
        "\n\n".join(p["title"] + "\n" + script(p) for p in pages), encoding="utf-8"
    )
    (root / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False), encoding="utf-8")
    files = ["narration.wav", "subtitles.vtt", "script.txt", "timeline.json"]
    if body["kind"] == "video":
        report(stage="正在封装完整视频", completed=total, total=total)
        (root / "concat.txt").write_text(
            "\n".join(f"file '{p.name}'" for p in videos), encoding="utf-8"
        )
        combined = root / "combined.mp4"
        run(
            [
                ffmpeg(),
                "-y",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(root / "concat.txt"),
                "-c",
                "copy",
                str(combined),
            ],
            check=lambda: report(),
        )
        run(
            [
                ffmpeg(),
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(combined),
                "-i",
                str(root / "narration.wav"),
                "-i",
                str(root / "subtitles.vtt"),
                "-map",
                "0:v",
                "-map",
                "1:a",
                "-map",
                "2:0",
                "-t",
                str(offset),
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-c:s",
                "mov_text",
                "-metadata:s:s:0",
                "language=zho",
                "-movflags",
                "+faststart",
                str(root / "lesson.mp4"),
            ],
            check=lambda: report(),
        )
        files.insert(0, "lesson.mp4")
    return {
        "files": files,
        "duration": offset,
        "timeline": timeline,
        "progress": {"stage": "已完成", "completed": total, "total": total},
    }
