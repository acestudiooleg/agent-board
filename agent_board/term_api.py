"""API термінала й диктування: зв'язує картки дошки з tmux, глядачами, «Забрати» і Whisper."""
import base64
import logging
import shlex

from .attach import Viewer, Viewers, size_ok
from .dictation import Dictation, DictationError
from .live import LiveSession, read_live
from .takeover import NeedsConfirm, Takeover
from .terminal import PREFIX, TerminalError, Terminals

log = logging.getLogger("agent_board")
MAX_INPUT = 64 * 1024
PING_SECONDS = 5


class TermService:
    def __init__(self, board, terminals: Terminals, dictation: Dictation):
        self.board, self.terminals, self.dictation = board, terminals, dictation
        self.viewers = Viewers(terminals)
        self.sessions_dir = board.cfg.claude_dir / "sessions"
        self.takeover = Takeover(self.sessions_dir, terminals, state_of=lambda sid: (board.card(sid) or {}).get("state"))
        board.term_info = self.info

    # ---------- для дошки ----------
    def info(self):
        """Знімок на один скан: функція (sid, LiveSession|None) → поля картки."""
        try:
            panes = self.terminals.panes()
        except TerminalError:
            panes = {}

        def fields(sid: str, lv: LiveSession | None) -> dict:
            return {"term": bool(lv and self.terminals.name_for_pid(lv.pid, panes)), "live_kind": lv.kind if lv else None,
                    "armed": sid in self.takeover.armed, "take_error": self.takeover.errors.get(sid)}
        return fields

    def tick(self) -> None:
        if self.takeover.armed and self.takeover.tick():
            self.board.scan()

    def close(self) -> None:
        self.viewers.close_all()
        self.dictation.close()

    # ---------- пошук ----------
    def _occupant(self, name: str) -> LiveSession | None:
        panes = self.terminals.panes()
        return next((lv for lv in read_live(self.sessions_dir).values() if self.terminals.name_for_pid(lv.pid, panes) == name), None)

    def _running(self, sid: str) -> str | None:
        """Термінал дошки, у якому працює (або запускається) розмова."""
        lv = read_live(self.sessions_dir).get(sid)
        if lv:
            return self.terminals.name_for_pid(lv.pid)
        name = PREFIX + sid
        if name not in self.terminals.panes():
            return None
        if self._occupant(name):
            raise TerminalError("у терміналі цієї розмови вже інша (після /clear) — відкрийте її картку")
        return name

    def jump_info(self, sid: str) -> dict | None:
        """Для «Перейти»: команда під'єднання й глядачі, чиє вікно можна сфокусувати."""
        try:
            name = self._running(sid)
            own = {v.proc.pid for v in list(self.viewers.by_key.values())}  # це вікна браузера, а не окремий термінал
            return name and {"command": shlex.join(self.terminals.attach_argv(name)),
                             "pids": [p for p in self.terminals.client_pids(name) if p not in own]}
        except TerminalError:
            return None

    # ---------- POST ----------
    def post(self, path: str, body: dict) -> tuple[int, dict]:
        action = getattr(self, "_" + path.removeprefix("/api/term/"), None) if path in PATHS else None
        if action is None:
            return 404, {"error": "not found"}
        try:
            return action(body)
        except NeedsConfirm as exc:
            return 409, {"error": str(exc), "confirm": True}
        except TerminalError as exc:
            return 409, {"error": str(exc)}

    def _card(self, body: dict) -> dict | None:
        sid = body.get("id")
        return self.board.card(sid) if isinstance(sid, str) else None

    def _viewer(self, body: dict) -> Viewer:
        viewer = self.viewers.get(body.get("key"))
        if viewer is None:
            raise TerminalError("термінал від'єднано")
        return viewer

    def _open(self, body):
        card, cols, rows = self._card(body), body.get("cols"), body.get("rows")
        if not card:
            return 404, {"error": "unknown session"}
        if not size_ok(cols, rows):
            return 400, {"error": "bad size"}
        sid, name = card["id"], self._running(card["id"])
        if not name:
            if read_live(self.sessions_dir).get(sid):
                return 409, {"error": "розмова відкрита в іншому терміналі", "live": True}
            if body.get("start") is not True:
                return 409, {"error": "розмову закрито", "closed": True}
            name = self.terminals.start(sid, card.get("cwd") or "", cols, rows)
        return 200, {"key": self.viewers.open(sid, name, cols, rows).key}

    def _input(self, body):
        data = body.get("data")
        if not isinstance(data, str) or len(data) > MAX_INPUT:
            return 400, {"error": "bad input"}
        self._viewer(body).write(data.encode("utf-8", "replace"))
        return 200, {"ok": True}

    def _paste(self, body):
        text = body.get("text")
        if not isinstance(text, str) or len(text) > MAX_INPUT:
            return 400, {"error": "bad text"}
        viewer = self._viewer(body)
        lv = self._occupant(viewer.name)
        if lv is None:
            raise TerminalError("Claude ще запускається — зачекайте кілька секунд")
        if lv.status == "waiting":  # Enter після тексту міг би підтвердити дозвіл
            raise TerminalError("Claude показує меню — дайте відповідь у терміналі, текст лишився в полі")
        self.terminals.paste(viewer.name, text)
        return 200, {"ok": True}

    def _resize(self, body):
        if not size_ok(body.get("cols"), body.get("rows")):
            return 400, {"error": "bad size"}
        self._viewer(body).resize(body["cols"], body["rows"])
        return 200, {"ok": True}

    def _detach(self, body):
        self.viewers.close(self._viewer(body))
        return 200, {"ok": True}

    def _takeover(self, body):
        card = self._card(body)
        if not card:
            return 404, {"error": "unknown session"}
        if body.get("cancel") is True:
            self.takeover.cancel(card["id"])
            result = "cancelled"
        else:
            result = self.takeover.request(card["id"], card.get("cwd") or "", body.get("confirm") is True)
        self.board.scan()
        return 200, {"ok": True, "result": result}

    def _stop(self, body):
        card = self._card(body)
        if not card:
            return 404, {"error": "unknown session"}
        name = self._running(card["id"])
        if not name:
            raise TerminalError("розмова не працює в дошці")
        self.terminals.stop(name)
        self.board.scan()
        return 200, {"ok": True}

    def dictate(self, pcm: bytes, lang: str) -> tuple[int, dict]:
        try:
            return 200, self.dictation.transcribe(pcm, lang)
        except DictationError as exc:
            return 422, {"error": str(exc)}

    # ---------- потік ----------
    def stream(self, viewer: Viewer, write) -> None:
        try:
            while True:
                chunk = viewer.read(PING_SECONDS)
                if chunk is None:
                    write(b"event: end\ndata: end\n\n")
                    return
                write(b"data: " + base64.b64encode(chunk) + b"\n\n" if chunk else b": ping\n\n")
        except (BrokenPipeError, ConnectionResetError):
            return
        finally:
            self.viewers.close(viewer)


PATHS = {f"/api/term/{x}" for x in ("open", "input", "paste", "resize", "detach", "takeover", "stop")}
