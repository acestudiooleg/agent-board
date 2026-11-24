"""Диктування: окремий процес Whisper, який живе, поки ним користуються. Звук не потрапляє на диск і в мережу."""
import json
import os
import select
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # звідси знаходиться agent_board.dictate_worker
LANGS = ("auto", "uk", "ru", "en")
MAX_PCM = 4_000_000  # 2 хв 16 кГц s16le
LOAD_SECONDS, WORK_SECONDS = 60, 180


class DictationError(Exception):
    pass


class Dictation:
    def __init__(self, model: str, threads: int, languages: list[str], idle_seconds: float = 600, command=None,
                 load_seconds: float = LOAD_SECONDS, work_seconds: float = WORK_SECONDS):
        self.command = command or [sys.executable, "-m", "agent_board.dictate_worker", model, str(threads), ",".join(languages)]
        self.idle_seconds, self.load_seconds, self.work_seconds = idle_seconds, load_seconds, work_seconds
        self.proc, self.timer, self.buf = None, None, b""
        self.lock = threading.Lock()

    def transcribe(self, pcm: bytes, lang: str) -> dict:
        if lang not in LANGS:
            raise DictationError("невідома мова")
        if not pcm or len(pcm) % 2 or len(pcm) > MAX_PCM:
            raise DictationError("порожній або завеликий запис")
        with self.lock:
            if self.timer:
                self.timer.cancel()
            try:
                self._start()
                try:
                    self.proc.stdin.write(json.dumps({"len": len(pcm), "lang": lang}).encode() + b"\n" + pcm)
                    self.proc.stdin.flush()
                except OSError:
                    raise DictationError("розпізнавання впало, спробуйте ще раз") from None
                answer = self._read(self.work_seconds)
            except DictationError:
                self._stop()
                raise
            self.timer = threading.Timer(self.idle_seconds, self.close)
            self.timer.daemon = True
            self.timer.start()
        if answer.get("error"):
            raise DictationError(answer["error"])
        return {"text": answer.get("text", ""), "lang": answer.get("lang", "")}

    def _start(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        self._stop()
        try:
            self.proc = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                         start_new_session=True, cwd=ROOT, env={**os.environ, "HF_HUB_OFFLINE": "1"})
        except OSError as exc:
            raise DictationError(f"Whisper не запустився: {exc}") from None
        answer = self._read(self.load_seconds)
        if not answer.get("ready"):
            raise DictationError(answer.get("error") or "Whisper не запустився")

    def _read(self, timeout: float) -> dict:
        fd, deadline = self.proc.stdout.fileno(), time.monotonic() + timeout
        while b"\n" not in self.buf:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([fd], [], [], left)[0]:
                raise DictationError("розпізнавання тривало задовго")
            chunk = os.read(fd, 65536)
            if not chunk:
                raise DictationError("розпізнавання впало, спробуйте ще раз")
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        try:
            return json.loads(line)
        except ValueError:
            raise DictationError("незрозуміла відповідь Whisper") from None

    def _stop(self) -> None:
        proc, self.proc, self.buf = self.proc, None, b""
        if proc:
            proc.kill()
            proc.wait()
            for stream in (proc.stdin, proc.stdout):
                try:
                    stream.close()
                except OSError:
                    pass

    def close(self) -> None:
        with self.lock:
            if self.timer:
                self.timer.cancel()
            self._stop()
