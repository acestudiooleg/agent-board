import json
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

MAX_TEXT = 2000
MAX_COMMANDS = 300   # скільки аргументів tool-викликів тримати для перевірки виключення
HEAD = 6            # перші репліки розмови зберігаються окремо
TAIL = 40           # і останні
CHUNK = 8 * 1024 * 1024
BG_MAX_AGE = 3 * 3600    # задача без сповіщення про кінець (TaskStop, compaction) не висить довше
PATH_KEYS = ("file_path", "path", "notebook_path")
SKIP_PREFIXES = (b'{"type":"api-request', b'{"type":"file-history', b'{"type":"queue-operation')
COMMAND_RE = re.compile(r"<command-name>(.*?)</command-name>", re.S)
ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.S)
AGENT_ID_RE = re.compile(r"agentId: ([A-Za-z0-9]+)")
BASH_ID_RE = re.compile(r"background with ID: ([A-Za-z0-9]+)")
WORKFLOW_ID_RE = re.compile(r"Task ID: ([A-Za-z0-9]+)")
BG_ID_RE = {"agent": AGENT_ID_RE, "bash": BASH_ID_RE, "workflow": WORKFLOW_ID_RE}
REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
# Повідомлення про завершення шукаємо в СИРОМУ рядку: воно буває в рядку task-notification,
# у <system-reminder> звичайної репліки і в рядку queue-operation. Рядки агента й tool_result
# (там буває процитований код із таким самим текстом) не перевіряємо.
NOTIFICATION_RE = re.compile(rb"<task-id>([A-Za-z0-9]+)</task-id>(.*?)</task-notification>", re.S)
TASK_END_RE = re.compile(rb"<status>(completed|failed|killed|stopped)</status>")


@dataclass
class Transcript:
    session_id: str
    path: Path
    offset: int = 0
    version: int = 0          # кількість текстових реплік; ключ кешу підсумку
    humans: int = 0
    title: str | None = None
    first_user: str | None = None
    cwd: str | None = None    # ПЕРШИЙ cwd розмови
    branch: str | None = None
    last_speaker: str | None = None   # "user" | "assistant"
    last_activity: float = 0.0
    sdk: bool = False
    head: list = field(default_factory=list)                         # [(role, text)]
    tail: deque = field(default_factory=lambda: deque(maxlen=TAIL))  # [(index, role, text)]
    files: set = field(default_factory=set)
    dirs: set = field(default_factory=set)
    bg_tools: dict = field(default_factory=dict)    # tool_use_id -> "agent" | "bash"
    bg_pending: dict = field(default_factory=dict)  # task_id -> час запуску
    bg_done: set = field(default_factory=set)       # завершені id (завершення буває записане раніше за запуск)
    commands: set = field(default_factory=set)      # рядкові аргументи tool-викликів (Bash command тощо) для виключення
    uuids: set = field(default_factory=set)         # запасна ознака копії розмови, переведеної у фон
    last_uuid: str | None = None
    continued_in: str | None = None                 # явна ознака: {"type":"continued-in","continuedInSessionId":…}
    continued_at: int = -1                          # version у момент continued-in; нові репліки після нього — окрема розмова

    def last(self, role: str) -> str:
        return next((text for _, r, text in reversed(self.tail) if r == role), "")

    def pending_background(self, now: float) -> int:
        return sum(1 for started in self.bg_pending.values() if started > now - BG_MAX_AGE)

    def signature(self) -> tuple:
        return (self.version, self.last_activity, self.title, self.last_speaker, len(self.bg_pending), self.sdk)


def _ts(value) -> float | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _add(t: Transcript, role: str, text: str) -> None:
    text = text[:MAX_TEXT]
    if len(t.head) < HEAD:
        t.head.append((role, text))
    t.tail.append((t.version, role, text))
    t.version += 1


def _tool_result(t: Transcript, block: dict) -> None:
    kind = t.bg_tools.pop(block.get("tool_use_id"), None)
    if not kind:
        return
    content = block.get("content")
    text = content if isinstance(content, str) else _text(content)
    if kind == "agent" and "in the background" not in text:
        return  # субагент на передньому плані вже повернув звіт
    match = BG_ID_RE[kind].search(text)
    if match:
        if match.group(1) not in t.bg_done:
            t.bg_pending[match.group(1)] = t.last_activity


def _user(t: Transcript, d: dict, content) -> None:
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                _tool_result(t, block)
    origin = (d.get("origin") or {}).get("kind")
    text = _text(content)
    if origin == "task-notification" or text.lstrip().startswith("<task-notification>"):
        return  # завершення фонових задач уже враховано в _apply_raw
    if origin == "human":
        said = REMINDER_RE.sub("", text).strip()
        if not said:
            has_image = isinstance(content, list) and any(
                isinstance(b, dict) and b.get("type") == "image" for b in content)
            if not has_image:
                return  # репліка складалася лише зі службових <system-reminder>
            said = "[зображення]"
    elif origin is None and d.get("promptSource") != "system" and (m := COMMAND_RE.search(text)):
        name = m.group(1).strip()
        args = ARGS_RE.search(text)
        said = f"{name if name.startswith('/') else '/' + name} {args.group(1).strip() if args else ''}".strip()
    else:
        return
    _human(t, said)


def _human(t: Transcript, said: str) -> None:
    t.humans += 1
    if t.first_user is None:
        t.first_user = said[:MAX_TEXT]
    _add(t, "user", said)
    t.last_speaker = "user"


def _attachment(t: Transcript, d: dict) -> None:
    """Репліка, надіслана, поки агент працював, пишеться лише як attachment/queued_command."""
    att = d.get("attachment") or {}
    if att.get("type") != "queued_command" or att.get("commandMode") != "prompt":
        return
    if (att.get("origin") or {}).get("kind") != "human" or not isinstance(att.get("prompt"), str):
        return
    said = REMINDER_RE.sub("", att["prompt"]).strip()
    if not said:
        return
    ts = _ts(att.get("timestamp") or d.get("timestamp"))
    if ts:
        t.last_activity = max(t.last_activity, ts)
    _human(t, said)


def _assistant(t: Transcript, content) -> None:
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            args = block.get("input") or {}
            if len(t.commands) < MAX_COMMANDS:
                t.commands.update(v[:500] for v in args.values() if isinstance(v, str) and "/" in v)
            for key in PATH_KEYS:
                value = args.get(key)
                if isinstance(value, str) and value.startswith("/"):
                    t.files.add(value)
            if block.get("name") == "Agent":
                t.bg_tools[block.get("id")] = "agent"
            elif block.get("name") == "Workflow":
                t.bg_tools[block.get("id")] = "workflow"
            elif block.get("name") == "Bash" and args.get("run_in_background"):
                t.bg_tools[block.get("id")] = "bash"
    t.last_speaker = "assistant"
    text = _text(content).strip()
    if text:
        _add(t, "assistant", text)


def apply_line(t: Transcript, d: dict) -> None:
    kind = d.get("type")
    if isinstance(d.get("uuid"), str):
        t.uuids.add(d["uuid"])
        t.last_uuid = d["uuid"]
    if kind == "continued-in" and isinstance(d.get("continuedInSessionId"), str):
        t.continued_in = d["continuedInSessionId"]
        t.continued_at = t.version
        return
    if kind == "attachment" and not d.get("isSidechain"):
        _attachment(t, d)
        return
    if kind == "ai-title" and d.get("aiTitle"):
        t.title = d["aiTitle"]
        return
    if kind not in ("user", "assistant") or d.get("isSidechain"):
        return
    if d.get("entrypoint") == "sdk-cli" or d.get("promptSource") == "sdk":
        t.sdk = True
    if d.get("cwd"):
        if t.cwd is None:
            t.cwd = d["cwd"]
        t.dirs.add(d["cwd"])
    if d.get("gitBranch"):
        t.branch = d["gitBranch"]
    ts = _ts(d.get("timestamp"))
    if ts:
        t.last_activity = max(t.last_activity, ts)
    content = (d.get("message") or {}).get("content")
    if kind == "user":
        _user(t, d, content)
    else:
        _assistant(t, content)


def _notification_line(raw: bytes) -> bool:
    if b"</task-notification>" not in raw:
        return False
    if raw.startswith(b'{"type":"queue-operation'):
        return True
    if b'"type":"attachment"' in raw and b'"queued_command"' in raw:
        return True
    return b'"type":"user"' in raw and b'"tool_result"' not in raw


def _apply_raw(t: Transcript, raw: bytes) -> None:
    if _notification_line(raw):
        for match in NOTIFICATION_RE.finditer(raw):
            if TASK_END_RE.search(match.group(2)):
                task_id = match.group(1).decode()
                t.bg_pending.pop(task_id, None)
                t.bg_done.add(task_id)
    if not raw.strip() or raw.startswith(SKIP_PREFIXES):
        return
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        return
    if isinstance(d, dict):
        apply_line(t, d)


def update(t: Transcript) -> bool:
    """Дочитує нові повні рядки шматками по CHUNK. True — якщо змінилось щось видиме."""
    try:
        size = t.path.stat().st_size
    except FileNotFoundError:
        return False
    if size < t.offset:
        t.__dict__.update(Transcript(t.session_id, t.path).__dict__)
    if size == t.offset:
        return False
    before = t.signature()
    with t.path.open("rb") as fh:
        fh.seek(t.offset)
        carry = b""
        while True:
            chunk = fh.read(CHUNK)
            if not chunk:
                break
            data = carry + chunk
            end = data.rfind(b"\n")
            if end < 0:
                carry = data
                continue
            for raw in data[:end].split(b"\n"):
                _apply_raw(t, raw)
            t.offset += end + 1
            carry = data[end + 1:]
    return before != t.signature()
