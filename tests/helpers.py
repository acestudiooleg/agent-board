import json
from pathlib import Path

TS = "2026-11-20T10:00:00.000Z"
CWD = "/home/u/server/tutor-app"


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _user(content, ts=TS, cwd=CWD, origin=None, sidechain=False, **extra):
    d = {"parentUuid": None, "isSidechain": sidechain, "type": "user",
         "message": {"role": "user", "content": content}, "timestamp": ts, "cwd": cwd, "gitBranch": "main"}
    if origin:
        d["origin"] = {"kind": origin}
    d.update(extra)
    return _dump(d)


def human(text, ts=TS, cwd=CWD, sidechain=False):
    return _user(text, ts=ts, cwd=cwd, origin="human", sidechain=sidechain)


def command(name, args="", ts=TS, cwd=CWD):
    return _user(f"<command-name>{name}</command-name>\n<command-message>{name.lstrip('/')}</command-message>\n"
                 f"<command-args>{args}</command-args>", ts=ts, cwd=cwd)


def local_caveat(ts=TS):
    return _user("<local-command-caveat>The command below was run locally</local-command-caveat>", ts=ts)


def tool_result(tool_id="t", text="ok", ts=TS):
    return _user([{"type": "tool_result", "tool_use_id": tool_id, "content": text}], ts=ts)


def _notification(task_id, status):
    return f"<task-notification>\n<task-id>{task_id}</task-id>\n<status>{status}</status>\n</task-notification>"


def task_notification(task_id, status="completed", ts=TS):
    return _user(_notification(task_id, status), ts=ts, origin="task-notification")


def queued_notification(task_id, status="completed"):
    """Повідомлення в рядку queue-operation (такі рядки розбір пропускає без json.loads)."""
    return _dump({"type": "queue-operation", "operation": "enqueue", "content": _notification(task_id, status)})


def human_with_reminder(text, task_id, ts=TS):
    """Повідомлення про завершення, вкладене в <system-reminder> звичайної людської репліки."""
    return _user(f"<system-reminder>\n{_notification(task_id, 'completed')}\n</system-reminder>\n{text}", ts=ts, origin="human")


def assistant(text=None, tool_paths=(), tools=(), ts=TS, cwd=CWD, **extra):
    blocks = [{"type": "thinking", "thinking": "", "signature": "s"}]
    for p in tool_paths:
        blocks.append({"type": "tool_use", "id": "e", "name": "Edit", "input": {"file_path": p}})
    for tool_id, name, args in tools:
        blocks.append({"type": "tool_use", "id": tool_id, "name": name, "input": args})
    if text is not None:
        blocks.append({"type": "text", "text": text})
    d = {"parentUuid": "x", "isSidechain": False, "type": "assistant",
         "message": {"role": "assistant", "content": blocks}, "timestamp": ts, "cwd": cwd}
    d.update(extra)
    return _dump(d)


def title(text):
    return _dump({"type": "ai-title", "aiTitle": text, "sessionId": "s"})


def api_request():
    return '{"type":"api-request","sessionId":"s","params":{"model":"x"}}'


def write_lines(path: Path, lines, mode="w"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open(mode, encoding="utf-8") as fh:
        for line in lines:
            fh.write(line + "\n")


def git_repo(path: Path) -> Path:
    (path / ".git").mkdir(parents=True, exist_ok=True)
    return path


def git_worktree(path: Path, main: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").write_text(f"gitdir: {main}/.git/worktrees/{path.name}\n")
    return path


def queued_prompt(text, ts=TS):
    """Репліка, надіслана, поки агент працював: лише attachment/queued_command."""
    return _dump({"parentUuid": "x", "isSidechain": False, "type": "attachment", "timestamp": ts,
                  "attachment": {"type": "queued_command", "prompt": text, "commandMode": "prompt",
                                 "origin": {"kind": "human"}, "timestamp": ts, "humanTurn": True}})


def queued_task_done(task_id, ts=TS):
    """Завершення фонової задачі, записане лише як attachment/queued_command."""
    return _dump({"parentUuid": "x", "isSidechain": False, "type": "attachment", "timestamp": ts,
                  "attachment": {"type": "queued_command", "commandMode": "task-notification",
                                 "prompt": _notification(task_id, "completed")}})
