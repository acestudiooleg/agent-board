"""Процес розпізнавання: заголовок JSON рядком + PCM s16le 16 кГц у stdin, відповідь JSON рядком у stdout."""
import json
import os
import sys


def main() -> None:
    out = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)  # випадковий друк бібліотек не псує протокол
    os.environ["HF_HUB_OFFLINE"] = "1"

    def reply(**obj):
        out.write(json.dumps(obj, ensure_ascii=False) + "\n")
        out.flush()

    model_name, threads, languages = sys.argv[1], int(sys.argv[2]), sys.argv[3].split(",")
    try:
        import numpy as np
        from faster_whisper import WhisperModel
        model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=threads, local_files_only=True)
    except Exception as exc:
        return reply(error=f"Whisper не запустився: {exc}")
    reply(ready=True)
    for line in sys.stdin.buffer:
        head = json.loads(line)
        audio = np.frombuffer(sys.stdin.buffer.read(head["len"]), np.int16).astype(np.float32) / 32768
        try:
            lang = head["lang"]
            if lang == "auto":  # лише між вашими мовами: короткий запис легко сплутати з болгарською чи польською
                probs = dict(model.detect_language(audio)[2])
                lang = max(languages, key=lambda code: probs.get(code, 0))
            segments, _ = model.transcribe(audio, language=lang, beam_size=1, vad_filter=True, condition_on_previous_text=False,
                                             temperature=0.0)  # без повторних спроб: на нерозбірливому звуці вони тривали 20 с
            reply(text=" ".join(s.text.strip() for s in segments).strip(), lang=lang)
        except Exception as exc:
            reply(error=f"Не розпізнано: {exc}")


if __name__ == "__main__":
    main()
