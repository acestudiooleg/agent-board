"""«Забрати в дошку»: завершити claude у чужому терміналі й продовжити ту саму розмову в tmux дошки."""
import logging
import os
import signal
import threading
import time
from pathlib import Path

from .live import LiveSession, read_live
from .terminal import TerminalError, Terminals

log = logging.getLogger("agent_board")
EXIT_SECONDS = 10


class NeedsConfirm(TerminalError):
    """Щось загубиться: питання агента або фонова команда."""


def proc_start(pid: int) -> str | None:
    """Час старту процесу; None — процесу немає або він уже завершився (зомбі)."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    except (OSError, IndexError):
        return None
    return None if fields[0] in "ZX" else fields[19]


class Takeover:
    def __init__(self, sessions_dir: Path, terminals: Terminals, state_of=lambda sid: None, kill=os.kill, exit_seconds: float = EXIT_SECONDS):
        self.sessions_dir, self.terminals, self.state_of, self.kill = sessions_dir, terminals, state_of, kill
        self.exit_seconds = exit_seconds
        self.armed: dict[str, str] = {}   # sid -> тека: забрати, коли агент закінчить
        self.errors: dict[str, str] = {}  # чому не вдалося забрати автоматично
        self.lock = threading.Lock()

    def live(self, sid: str) -> LiveSession | None:
        return read_live(self.sessions_dir).get(sid)

    def _check(self, sid: str) -> LiveSession:
        lv = self.live(sid)
        if lv is None:
            raise TerminalError("розмова вже закрита — її можна просто продовжити")
        if self.terminals.name_for_pid(lv.pid):
            raise TerminalError("розмова вже працює в дошці")
        if lv.kind != "interactive":
            raise TerminalError("фонову розмову (claude --bg) забрати не можна")
        if not lv.proc_start or proc_start(lv.pid) != lv.proc_start:
            raise TerminalError("не вдалося впевнитися, що це саме процес цієї розмови")
        return lv

    def request(self, sid: str, cwd: str, confirm: bool = False) -> str:
        with self.lock:
            self.errors.pop(sid, None)
            lv = self._check(sid)
            if lv.status == "busy":
                self.armed[sid] = cwd
                return "armed"
            if not confirm and (lv.status != "idle" or self.state_of(sid) == "waiting_bg"):
                raise NeedsConfirm("агент чекає відповіді — питання доведеться повторити" if lv.status == "waiting"
                                   else "фонову команду цієї розмови буде зупинено")
            self.armed.pop(sid, None)
            self._take(sid, lv, cwd)
            return "done"

    def cancel(self, sid: str) -> None:
        with self.lock:
            self.armed.pop(sid, None)
            self.errors.pop(sid, None)

    def tick(self) -> bool:
        """Забрати відкладені розмови, які закінчили роботу. True — щось змінилося."""
        changed = False
        for sid, cwd in list(self.armed.items()):
            with self.lock:
                if sid not in self.armed:
                    continue
                try:
                    lv = self._check(sid)
                    if lv.status != "idle" or self.state_of(sid) in ("waiting_bg", "working"):
                        continue
                    self._take(sid, lv, cwd)
                except TerminalError as exc:
                    log.warning("takeover %s: %s", sid, exc)
                    self.errors[sid] = str(exc)
                self.armed.pop(sid, None)
                changed = True
        return changed

    def _take(self, sid: str, lv: LiveSession, cwd: str) -> None:
        if not Path(cwd).is_dir():
            raise TerminalError(f"теки розмови немає: {cwd or '—'}")  # перевіряємо до завершення процесу
        try:
            self.kill(lv.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            raise TerminalError("немає прав завершити цей процес") from None
        deadline = time.monotonic() + self.exit_seconds
        while proc_start(lv.pid) == lv.proc_start:
            if time.monotonic() > deadline:
                raise TerminalError("Claude не завершився за 10 с — закрийте його там вручну")
            time.sleep(0.1)
        self.terminals.start(sid, cwd)
