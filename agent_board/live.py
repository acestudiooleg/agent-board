import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class LiveSession:
    pid: int
    session_id: str
    cwd: str
    name: str
    status: str        # "idle" | "busy" | інше
    started_at: float
    kind: str = "interactive"   # "interactive" | "bg" (розмова, переведена у фон)
    waiting_for: str = ""       # для status "waiting": чого чекає (напр. «input needed»)


LIVE_KINDS = ("interactive", "bg")


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_live(sessions_dir: Path, alive=pid_alive) -> dict[str, LiveSession]:
    out: dict[str, LiveSession] = {}
    if not sessions_dir.is_dir():
        return out
    for path in sorted(sessions_dir.glob("*.json")):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(d, dict) or d.get("kind") not in LIVE_KINDS or not d.get("sessionId"):
            continue
        pid = d.get("pid")
        if not isinstance(pid, int) or not alive(pid):
            continue
        out[d["sessionId"]] = LiveSession(
            pid=pid, session_id=d["sessionId"], cwd=d.get("cwd") or "", name=d.get("name") or "",
            status=d.get("status") or "idle", started_at=(d.get("startedAt") or 0) / 1000, kind=d["kind"],
            waiting_for=d.get("waitingFor") or "",
        )
    return out
