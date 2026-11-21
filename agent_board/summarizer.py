import itertools
import json
import logging
import os
import queue
import signal
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from .transcript import Transcript

log = logging.getLogger("agent_board")
BUDGET = 20000  # символів розмови в промпті (~8–10 тис. токенів кирилицею)

PROMPT_HEADER = """Ти пишеш короткий звіт про розмову розробника з агентом Claude Code, щоб людина за 5 секунд згадала, про що вона.
Розмову запущено в проєкті: {project} (тека {cwd}).
Відомі проєкти: {known}.
Поверни ЛИШЕ JSON без пояснень, українською:
{{"about": "про що розмова, одне речення",
  "done": ["що вже зроблено, до 5 коротких пунктів"],
  "waiting_for": "чого агент чекає від людини, одне речення, або порожній рядок",
  "related_projects": ["інші проєкти зі списку відомих, яких стосується розмова"],
  "likely_project": "проєкт зі списку відомих, про який насправді розмова"}}

Розмова:
"""


def _line(role: str, text: str) -> str:
    return f"{'КОРИСТУВАЧ' if role == 'user' else 'АГЕНТ'}: {text}"


def build_prompt(t: Transcript, project: str, known: list[str]) -> str:
    parts = []
    if t.first_user and ("user", t.first_user) not in t.head:
        parts.append(f"ПЕРШЕ ПОВІДОМЛЕННЯ: {t.first_user}")
    parts += [_line(r, x) for r, x in t.head]
    used = sum(len(p) for p in parts)
    chosen: list[str] = []
    for index, role, text in reversed(t.tail):
        if index < len(t.head):
            break
        line = _line(role, text)
        if used + len(line) > BUDGET:
            break
        chosen.append(line)
        used += len(line)
    chosen.reverse()
    if len(t.head) + len(chosen) < t.version:
        parts.append("[…пропущено…]")
    parts += chosen
    if t.files:
        parts.append("ФАЙЛИ: " + ", ".join(sorted(t.files)[:30]))
    header = PROMPT_HEADER.format(project=project, cwd=t.cwd or "?", known=", ".join(known) or "—")
    return header + "\n".join(parts)


def parse_response(stdout: str) -> dict:
    outer = json.loads(stdout)
    if outer.get("is_error"):
        raise ValueError(str(outer.get("result") or "claude error"))
    text = str(outer.get("result", ""))
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON in result")
    data = json.loads(text[start:end + 1])

    def as_list(value):
        return [str(x) for x in value] if isinstance(value, list) else []

    return {
        "about": str(data.get("about", "")).strip(),
        "done": as_list(data.get("done"))[:5],
        "waiting_for": str(data.get("waiting_for", "")).strip(),
        "related_projects": as_list(data.get("related_projects")),
        "likely_project": str(data.get("likely_project", "")).strip(),
    }


def run_claude(prompt: str, command: list[str], timeout: float, cwd: Path) -> str:
    """Сталий cwd: CLI не заводить нову теку в ~/.claude/projects на кожен виклик.
    Окрема група процесів: після тайм-ауту вбиваємо й нащадків."""
    cwd.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}  # завжди особистий акаунт ~/.claude
    proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, cwd=cwd, env=env, start_new_session=True)
    try:
        stdout, stderr = proc.communicate(prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise
    if proc.returncode != 0:
        raise RuntimeError(stderr.strip()[:500] or f"exit {proc.returncode}")
    return stdout


class Summarizer:
    """Черга з пріоритетом: один запит на сесію, ліміт на годину, пауза після N помилок поспіль.

    У черзі лежить лише sid. Промпт збирає prepare(sid) -> (key, prompt) | None уже ПІСЛЯ отримання слоту,
    тож у чергу не потрапляють застарілі версії, а слот без виклику повертається.
    on_result(sid, key, summary | None, error | None) викликається з робочого потоку.
    """

    def __init__(self, command: list[str], timeout: float, concurrency: int, prepare, on_result,
                 per_hour: int = 20, breaker_failures: int = 5, breaker_seconds: float = 3600,
                 cwd: Path | None = None, starts: list[float] | None = None, on_start=None):
        self._command, self._timeout = command, timeout
        self._prepare, self._on_result, self._on_start = prepare, on_result, on_start
        self._per_hour, self._breaker_failures, self._breaker_seconds = per_hour, breaker_failures, breaker_seconds
        self._cwd = cwd or Path.home() / ".local/share/agent-board/haiku"
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._pending: set[str] = set()
        self._starts: deque = deque(sorted(starts or []))
        self._failures = 0
        self.paused_until = 0.0
        self.last_error: str | None = None
        self._lock = threading.Lock()
        self._seq = itertools.count()
        self._stop = threading.Event()
        self._threads = [threading.Thread(target=self._worker, daemon=True) for _ in range(max(1, concurrency))]
        for thread in self._threads:
            thread.start()

    def is_pending(self, sid: str) -> bool:
        with self._lock:
            return sid in self._pending

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def status(self) -> dict:
        with self._lock:
            paused = self.paused_until if self.paused_until > time.time() else None
            return {"pending": len(self._pending), "paused_until": paused, "last_error": self.last_error}

    def request(self, sid: str, priority: tuple) -> bool:
        with self._lock:
            if sid in self._pending:
                return False
            self._pending.add(sid)
        self._queue.put((priority, next(self._seq), sid))
        return True

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=5)

    def _reserve_slot(self) -> float | None:
        while not self._stop.is_set():
            with self._lock:
                now = time.time()
                while self._starts and self._starts[0] <= now - 3600:
                    self._starts.popleft()
                if now >= self.paused_until and len(self._starts) < self._per_hour:
                    self._starts.append(now)
                    return now
            self._stop.wait(0.5)
        return None

    def _release_slot(self, ts: float) -> None:
        with self._lock:
            try:
                self._starts.remove(ts)
            except ValueError:
                pass

    def _done(self, sid: str) -> None:
        with self._lock:
            self._pending.discard(sid)

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                _, _, sid = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            slot = self._reserve_slot()
            if slot is None:
                return
            try:
                job = self._prepare(sid)
            except Exception:
                log.exception("summary prepare failed for %s", sid)
                job = None
            if job is None:
                self._release_slot(slot)
                self._done(sid)
                continue
            key, prompt = job
            if self._on_start:
                try:
                    self._on_start(slot)
                except Exception:
                    log.exception("recording summary call failed")
            summary, error = None, None
            try:
                summary = parse_response(run_claude(prompt, self._command, self._timeout, self._cwd))
            except Exception as exc:  # будь-яка помилка: лишаємо сирий текст
                error = f"{type(exc).__name__}: {exc}"
            with self._lock:
                if summary is None:
                    self._failures += 1
                    self.last_error = error
                    if self._failures >= self._breaker_failures:
                        self.paused_until = time.time() + self._breaker_seconds
                        self._failures = 0
                        log.warning("Haiku paused for %ss after repeated failures", self._breaker_seconds)
                else:
                    self._failures = 0
                    self.last_error = None
            try:
                self._on_result(sid, key, summary, error)
            except Exception:
                log.exception("summary callback failed for %s", sid)
            finally:
                self._done(sid)
