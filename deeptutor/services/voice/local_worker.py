"""Short-lived CPU speech worker; no model download or network access at runtime."""
import json
from pathlib import Path
import sys
import wave


def main():
    kind, packet = sys.argv[1:]
    request = Path(packet)
    data = json.loads(request.read_text(encoding="utf-8"))
    directory = request.parent
    root = Path(data["model"])
    if kind == "tts":
        import numpy as np
        import sherpa_onnx
        model = sherpa_onnx.OfflineTtsModelConfig(
            vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                model=str(root / "model.onnx"), tokens=str(root / "tokens.txt"),
                lexicon=str(root / "lexicon.txt"), dict_dir=str(root / "dict"),
            ), num_threads=1, debug=False, provider="cpu",
        )
        rules = [root / name for name in ("date.fst", "number.fst", "phone.fst") if (root / name).is_file()]
        config = sherpa_onnx.OfflineTtsConfig(model=model, rule_fsts=",".join(map(str, rules)), max_num_sentences=1)
        if not config.validate():
            raise RuntimeError("Invalid offline voice model")
        voice = sherpa_onnx.OfflineTts(config)
        for index, part in enumerate(data["parts"]):
            audio = voice.generate(part, sid=0, speed=float(data["rate"]))
            if len(audio.samples) == 0:
                raise RuntimeError("Empty speech output")
            pcm = (np.clip(audio.samples, -1, 1) * 32767).astype("<i2").tobytes()
            with wave.open(str(directory / f"raw-{index}.wav"), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(audio.sample_rate)
                output.writeframes(pcm)
    elif kind == "stt":
        import vosk
        vosk.SetLogLevel(-1)
        model = vosk.Model(str(root))
        pieces = []
        with wave.open(str(directory / "speech.wav"), "rb") as source:
            recognizer = vosk.KaldiRecognizer(model, source.getframerate())
            while chunk := source.readframes(4000):
                if recognizer.AcceptWaveform(chunk):
                    pieces.append(json.loads(recognizer.Result()).get("text", ""))
            pieces.append(json.loads(recognizer.FinalResult()).get("text", ""))
        text = "".join(pieces).replace(" ", "").strip()
        (directory / "transcript.json").write_text(json.dumps({"text": text}, ensure_ascii=False), encoding="utf-8")
    else:
        raise ValueError("Unknown speech worker mode")


if __name__ == "__main__":
    main()
