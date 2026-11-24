import glob
import logging
import threading
import time
from pathlib import Path

from .config import Config
from .jump import resume_command
from .live import read_live
from .projects import ProjectRef, ProjectResolver, family_of, related_projects
from .status import compute_state
from .store import Mark, Store
from .summarizer import Summarizer, build_prompt
from .transcript import Transcript, update

log = logging.getLogger("agent_board")
SKIP_SUMMARY_STATES = ("working", "done")


class Board:
    def __init__(self, cfg: Config, store: Store):
        self.cfg, self.store = cfg, store
        self.resolver = ProjectResolver(str(cfg.home), cfg.project_paths, cfg.worktree_rules, store.projects())
        self.transcripts: dict[str, Transcript] = {}
        self.live = {}
        self.failed: dict[str, tuple[int, int, float]] = {}  # sid -> (version, attempts, next_try)
        self.errors: dict[str, str] = {}
        self.known_public: list[str] = []  # проєкти, назви яких можна показати Haiku
        self.version = 0
        self._cards: list[dict] = []
        self._status: dict = {}
        self._lock = threading.RLock()
        self.changed = threading.Condition()
        self.term_info = None  # TermService: стан термінала картки
        self.summarizer = None
        if cfg.summary_enabled:
            self.summarizer = Summarizer(
                cfg.summary_command, cfg.summary_timeout_seconds, cfg.summary_concurrency,
                self._prepare, self._on_summary,
                per_hour=cfg.summary_per_hour, breaker_failures=cfg.summary_breaker_failures,
                breaker_seconds=cfg.summary_breaker_seconds, cwd=cfg.haiku_cwd,
                starts=store.calls_since(time.time() - 3600), on_start=store.record_call,
            )

    def close(self) -> None:
        if self.summarizer:
            self.summarizer.stop()
        self.store.close()

    # ---------- читання ----------
    def _files(self, now: float) -> dict[str, Path]:
        cutoff = now - self.cfg.max_age_days * 86400
        out = {}
        for name in glob.glob(str(self.cfg.claude_dir / "projects" / "*" / "*.jsonl")):
            path = Path(name)
            try:
                mtime = path.stat().st_mtime
            except FileNotFoundError:
                continue
            if mtime >= cutoff or path.stem in self.live:
                out[path.stem] = path
        return out

    def scan(self, now: float | None = None) -> bool:
        now = now or time.time()
        with self._lock:
            self.live = read_live(self.cfg.claude_dir / "sessions")
            files = self._files(now)
            for sid in [s for s in self.transcripts if s not in files and s not in self.live]:
                del self.transcripts[sid]
            for sid, path in files.items():
                t = self.transcripts.get(sid)
                if t is None or t.path != path:
                    t = self.transcripts[sid] = Transcript(sid, path)
                update(t)
            cards = self._build(now)
            self._enqueue(cards, now)
            return self._publish(cards)

    # ---------- картки ----------
    def _copies(self) -> dict[str, str]:
        """Копія -> розмова, що її продовжує (розмову перевели у фон, історію скопійовано в новий файл).

        Головна ознака — рядок continued-in в оригіналі. Запасна — останній uuid оригіналу є в іншому,
        більшому транскрипті. Справжня гілка (fork) розходиться з оригіналом і не ховається.
        """
        hidden = {}
        items = [t for t in self.transcripts.values()]
        for t in items:
            if t.continued_in and t.continued_in in self.transcripts:
                if t.version == t.continued_at:  # після continued-in у старій вкладці не писали
                    hidden[t.session_id] = t.continued_in
                continue
            if not t.last_uuid:
                continue
            for other in items:
                if other is t or t.last_uuid not in other.uuids:
                    continue
                if len(other.uuids) > len(t.uuids) or (len(other.uuids) == len(t.uuids) and other.session_id > t.session_id):
                    hidden[t.session_id] = other.session_id
                    break
        return hidden

    def _excluded(self, family: str, related: list[str], t: Transcript, cwd: str) -> bool:
        """Робоча розмова не йде в Haiku: за родиною проєкту, суміжних проєктів або за шляхами."""
        excluded = set(self.cfg.summary_exclude_families)
        if family in excluded:
            return True
        if any(family_of(p, self.cfg.families)[0] in excluded for p in related):
            return True
        paths = {cwd, *t.dirs, *t.files}
        if any(p == root or p.startswith(root + "/") for p in paths if p for root in self.cfg.summary_exclude_paths):
            return True
        # Bash-команди та інші рядкові аргументи: «cd ~/work/…», «ls /home/…/work»
        home = str(self.cfg.home)
        needles = [n for root in self.cfg.summary_exclude_paths
                   for n in {root, "~" + root[len(home):] if root.startswith(home + "/") else root}]
        return any(n in c for c in t.commands for n in needles)

    def _by_files(self, t: Transcript, ref: ProjectRef, cwd: str) -> ProjectRef:
        """Розмова, що почалась в одній теці, а працювала здебільшого з файлами вашого проєкту з панелі «Проєкти»,
        належить тому проєкту. Кожна згадка шляху (файл чи команда) зараховується найглибшій теці; батьківська
        тека теки старту розмову не забирає. Потрібно щонайменше 5 згадок і вдвічі більше, ніж у теки старту."""
        if not self.resolver.user or not cwd:
            return ref
        home = str(self.cfg.home)

        def under(value: str, root: str) -> bool:
            forms = [root] + (["~" + root[len(home):]] if root.startswith(home + "/") else [])
            return any(value == r or (r + "/") in value or value.endswith(" " + r) for r in forms)

        start = ref.root or cwd
        roots = sorted({r for r, _ in self.resolver.user} | {start}, key=len, reverse=True)
        score: dict[str, int] = {}
        for value in (*t.files, *t.commands):
            root = next((r for r in roots if under(value, r)), None)
            if root:
                score[root] = score.get(root, 0) + 1
        names = dict(self.resolver.user)
        candidates = [(n, r) for r, n in score.items()
                      if r != start and r in names and not (cwd == r or cwd.startswith(r + "/"))]
        if not candidates:
            return ref
        best_n, best = max(candidates)
        # Вкладений проєкт важливіший за батьківський, якщо має хоча б половину його згадок (~/studio і ~/studio/brands/x).
        for n, r in sorted(candidates, key=lambda x: -len(x[1])):
            if r.startswith(best + "/") and 2 * n >= best_n and n >= 5:
                best_n, best = n, r
                break
        if best_n >= 5 and best_n > 2 * score.get(start, 0):
            return ProjectRef(names[best], None, "user", best)
        return ref

    def _build(self, now: float) -> list[dict]:
        marks = self.store.marks()
        project_icons = self.store.project_icons()
        copies = self._copies()
        live_copies = {target for sid, target in copies.items() if sid in self.live}
        rows = []
        for sid in set(self.transcripts) | set(self.live):
            if sid in copies:
                continue
            t = self.transcripts.get(sid) or Transcript(sid, Path("/dev/null"))
            lv = self.live.get(sid)
            if t.sdk or (not t.humans and not (lv and lv.kind == "interactive")):
                continue  # SDK, закриті без реплік і порожні фонові сесії не показуємо
            cwd = t.cwd or (lv.cwd if lv else "") or ""
            rows.append((sid, t, lv, cwd, self._by_files(t, self.resolver.resolve(cwd), cwd)))
        excluded_families = set(self.cfg.summary_exclude_families)
        known = sorted({ref.name for *_, ref in rows if ref.kind != "dir"})
        self.known_public = [p for p in known if family_of(p, self.cfg.families)[0] not in excluded_families]
        cards = []
        term_fields = self.term_info() if self.term_info else lambda sid, lv: {}
        for sid, t, lv, cwd, ref in rows:
            family, icon = family_of(ref.name, self.cfg.families)
            related = set(related_projects(self.resolver, t.files, t.dirs, ref.name))
            excluded = self._excluded(family, sorted(related), t, cwd)
            stored = None if excluded else self.store.get_summary(sid)
            summary = stored[1] if stored else None
            likely = None
            if summary:
                related |= {p for p in summary.get("related_projects", []) if p in known and p != ref.name}
                guess = summary.get("likely_project")
                if guess and guess in known and guess != ref.name:
                    likely = guess
            mark = marks.get(sid)
            cards.append({
                "id": sid,
                "title": t.title or (summary or {}).get("about") or (t.first_user or "")[:80] or (lv.name if lv else sid[:8]),
                "name": lv.name if lv else None,
                "project": ref.name, "worktree": ref.worktree, "family": family, "icon": icon, "project_icon": project_icons.get(ref.name),
                "project_root": ref.root, "project_kind": ref.kind,
                "cwd": cwd, "branch": t.branch,
                "resume": resume_command(cwd if cwd.startswith("/") else "", sid, self.cfg.config_dir_env()),
                "state": compute_state(lv, t, mark, now),
                "live_status": lv.status if lv else None,
                "pid": lv.pid if lv else None,
                "live_waiting_for": lv.waiting_for if lv else "",
                "background": t.pending_background(now),
                "old_tab_live": sid in live_copies,
                "last_activity": t.last_activity or (lv.started_at if lv else 0),
                "first_user": (t.first_user or "")[:500],
                "last_user": t.last("user")[:500],
                "last_agent": t.last("assistant"),  # уся репліка (до MAX_TEXT): панель показує її повністю
                "related": sorted(related),
                "likely_project": likely,
                "summary": summary,
                "summary_mode": "disabled" if not self.summarizer else ("excluded" if excluded else "on"),  # без Haiku 🔒 нічого не означає
                "summary_fresh": bool(stored and stored[0] == t.version),
                "summary_error": self.errors.get(sid),
                "mark": {"kind": mark.kind, "until": mark.until} if mark else None,
                "version": t.version,
                **term_fields(sid, lv),
            })
        cards.sort(key=lambda c: -c["last_activity"])
        return cards

    def _enqueue(self, cards: list[dict], now: float) -> None:
        if not self.summarizer:
            return
        for c in cards:
            sid, version = c["id"], c["version"]
            if c["summary_mode"] != "on" or c["summary_fresh"] or not version:
                continue
            if c["state"] in SKIP_SUMMARY_STATES or now - c["last_activity"] < self.cfg.summary_quiet_seconds:
                continue
            fail = self.failed.get(sid)
            if fail and fail[0] == version and now < fail[2]:
                continue
            self.summarizer.request(sid, (0 if c["live_status"] else 1, -c["last_activity"]))

    def _prepare(self, sid: str) -> tuple[str, str] | None:
        """Викликається чергою вже після отримання слоту: промпт для поточної версії або None."""
        with self._lock:
            card = next((c for c in self._cards if c["id"] == sid), None)
            t = self.transcripts.get(sid)
            if not card or not t or not t.version or card["summary_mode"] != "on":
                return None
            stored = self.store.get_summary(sid)
            if stored and stored[0] == t.version:
                return None
            return str(t.version), build_prompt(t, card["project"], self.known_public)

    def _on_summary(self, sid: str, key: str, summary: dict | None, error: str | None) -> None:
        """Лише записує результат; на дошці він з'явиться на наступному scan (без шторму SSE)."""
        version = int(key)
        with self._lock:
            if summary is None:
                prev = self.failed.get(sid)
                attempts = prev[1] + 1 if prev and prev[0] == version else 1
                delay = min(self.cfg.summary_retry_seconds * 2 ** (attempts - 1), self.cfg.summary_backoff_max_seconds)
                self.failed[sid] = (version, attempts, time.time() + delay)
                self.errors[sid] = error or "unknown"
                log.warning("summary failed for %s (attempt %s): %s", sid, attempts, error)
            elif self.store.put_summary(sid, version, summary):
                self.failed.pop(sid, None)
                self.errors.pop(sid, None)

    def _publish(self, cards: list[dict]) -> bool:
        status = self.summary_status()
        with self.changed:
            if cards == self._cards and status == self._status:
                return False
            self._cards, self._status = cards, status
            self.version += 1
            self.changed.notify_all()
            return True

    # ---------- API для сервера ----------
    def snapshot(self) -> tuple[int, list[dict]]:
        with self.changed:
            return self.version, list(self._cards)

    def summary_status(self) -> dict:
        if not self.summarizer:
            return {"enabled": False, "pending": 0, "paused_until": None, "last_error": None}
        return {"enabled": True, **self.summarizer.status()}

    def wait(self, version: int, timeout: float) -> int:
        with self.changed:
            self.changed.wait_for(lambda: self.version != version, timeout)
            return self.version

    def card(self, sid: str) -> dict | None:
        return next((c for c in self.snapshot()[1] if c["id"] == sid), None)

    # ---------- ваші проєкти ----------
    def projects_overview(self) -> dict:
        """Ваші проєкти й знайдені автоматично, з кількістю розмов у кожному."""
        cards = self.snapshot()[1]
        icons = self.store.project_icons()
        user = []
        for path, name in self.store.projects():
            count = sum(1 for c in cards if c["project_kind"] == "user" and c["project_root"] == path)
            user.append({"path": path, "name": name, "count": count, "icon": icons.get(name)})
        detected: dict[tuple[str, str], int] = {}
        for c in cards:
            if c["project_kind"] != "user" and c["project_root"]:
                key = (c["project"], c["project_root"])
                detected[key] = detected.get(key, 0) + 1
        return {"user": user,
                "detected": [{"name": n, "path": p, "count": k, "icon": icons.get(n)} for (n, p), k in sorted(detected.items(), key=lambda x: -x[1])]}

    def save_project(self, path: str, name: str, old_path: str | None = None) -> None:
        with self._lock:
            self.store.save_project(path, name, old_path)
            self.resolver.set_user(self.store.projects())
            self._publish(self._build(time.time()))

    def set_project_icon(self, name: str, icon: str) -> None:
        with self._lock:
            self.store.set_project_icon(name, icon)
            self._publish(self._build(time.time()))

    def delete_project(self, path: str) -> None:
        with self._lock:
            self.store.delete_project(path)
            self.resolver.set_user(self.store.projects())
            self._publish(self._build(time.time()))

    def mark(self, sid: str, kind: str, days: float = 1, until: float | None = None) -> bool:
        with self._lock:
            if self.card(sid) is None:
                return False
            if kind == "clear":
                self.store.clear_mark(sid)
            else:
                t = self.transcripts.get(sid)
                if kind == "snooze":
                    until = until or time.time() + days * 86400
                else:
                    until = None
                self.store.set_mark(sid, Mark(kind, until, t.last_activity if t else 0))
            self._publish(self._build(time.time()))
            return True
