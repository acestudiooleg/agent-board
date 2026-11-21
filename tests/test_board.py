import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from agent_board.board import Board
from agent_board.config import Config, Family
from agent_board.store import Store
from tests.helpers import assistant, git_repo, git_worktree, human, title, tool_result, write_lines

FAKE = [sys.executable, str(Path(__file__).with_name("fake_claude.py"))]


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ts))


class BoardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        home = root / "home"
        tutor = git_repo(home / "server/tutor-app")
        (tutor / "apps/landing").mkdir(parents=True)
        viewer = git_repo(home / "server/acme/work/viewer")
        self.wt = git_worktree(home / "server/acme/work/.worktrees/viewer/pr-7", viewer)
        d7 = git_repo(home / "server/acme/work/templates")
        self.shop = shop = git_repo(home / "server/shop")
        self.claude = root / "claude"
        self.old = old = time.time() - 3600
        projects = self.claude / "projects"
        # закрита розмова: стартувала в підтеці tutor-app, потім cd у /tmp, торкалась shop
        write_lines(projects / "p1" / "aaa.jsonl", [
            human("Схема БД?", ts=iso(old), cwd=f"{tutor}/apps/landing"),
            assistant("Готово, видаляти таблицю?", tool_paths=[f"{shop}/x.json"], ts=iso(old), cwd="/tmp/x"),
            title("Схема БД"),
        ])
        # жива розмова в worktree viewer (родина Work → без підсумку)
        self.bbb = projects / "p2" / "bbb.jsonl"
        write_lines(self.bbb, [human("Рев'ю PR", ts=iso(old), cwd=str(self.wt)), assistant("Рев'ю готове", ts=iso(old), cwd=str(self.wt))])
        write_lines(projects / "p3" / "ccc.jsonl", [human("шаблони", ts=iso(old), cwd=str(d7))])
        stale = projects / "p4" / "ddd.jsonl"
        write_lines(stale, [human("давнє", ts=iso(old), cwd=str(tutor))])
        os.utime(stale, (old - 30 * 86400, old - 30 * 86400))
        write_lines(projects / "p5" / "eee.jsonl", [human("промпт", ts=iso(old), cwd=str(tutor)),
                                                    assistant("sdk", ts=iso(old), entrypoint="sdk-cli", promptSource="sdk")])
        sessions = self.claude / "sessions"
        sessions.mkdir(parents=True)
        (sessions / f"{os.getpid()}.json").write_text(json.dumps({
            "pid": os.getpid(), "sessionId": "bbb", "cwd": str(home), "startedAt": 0,  # після cd: проєкт — з першого cwd
            "kind": "interactive", "name": "viewer-rev", "status": "idle"}))
        # порожня фонова сесія без транскрипту — не показується
        (sessions / f"{os.getppid()}.json").write_text(json.dumps({
            "pid": os.getppid(), "sessionId": "zzz", "cwd": str(home), "startedAt": 0,
            "kind": "bg", "name": "zzz", "status": "idle"}))
        self.root, self.home = root, home

    def tearDown(self):
        if hasattr(self, "board"):
            self.board.close()
        self.tmp.cleanup()

    def make(self, **kw):
        options = {"claude_dir": self.claude, "db_path": self.root / "b.db", "home": self.home,
                   "summary_enabled": False, "summary_command": FAKE, "summary_quiet_seconds": 0,
                   "families": [Family("Work", "🏥", ["viewer", "templates"])], "summary_exclude_families": ["Work"]}
        options.update(kw)
        cfg = Config(**options)
        self.board = Board(cfg, Store(cfg.db_path))
        return self.board

    def cards(self):
        return {c["id"]: c for c in self.board.snapshot()[1]}

    def test_builds_cards(self):
        self.make().scan()
        cards = self.cards()
        self.assertEqual(set(cards), {"aaa", "bbb", "ccc"})  # ddd застаре, eee — SDK, zzz — порожня bg
        a, b = cards["aaa"], cards["bbb"]
        self.assertEqual((a["project"], a["state"], a["title"], a["related"]), ("tutor-app", "sleeping", "Схема БД", ["shop"]))
        self.assertEqual((b["project"], b["worktree"], b["family"], b["icon"]), ("viewer", "pr-7", "Work", "🏥"))
        self.assertEqual(b["cwd"], str(self.wt))  # перший cwd транскрипту, а не поточний cwd живої сесії
        self.assertEqual((b["state"], b["name"], b["summary_mode"]), ("waiting", "viewer-rev", "disabled"))  # Haiku вимкнено на всій дошці — замок не потрібен

    def test_summaries_arrive_except_excluded_family(self):
        board = self.make(summary_enabled=True)
        deadline = time.time() + 20
        while time.time() < deadline and not self.cards().get("aaa", {}).get("summary_fresh"):
            board.scan()
            time.sleep(0.2)
        cards = self.cards()
        self.assertEqual(cards["aaa"]["summary"]["waiting_for"], "Видаляти таблицю?")
        self.assertEqual(cards["aaa"]["summary_mode"], "on")
        self.assertIsNone(cards["aaa"]["likely_project"])  # fake каже tutor-app = власний проєкт
        self.assertIsNone(cards["bbb"]["summary"])
        self.assertIsNone(cards["ccc"]["summary"])

    def test_marks(self):
        board = self.make()
        board.scan()
        self.assertTrue(board.mark("aaa", "done"))
        self.assertEqual(self.cards()["aaa"]["state"], "done")
        board.mark("aaa", "clear")
        self.assertEqual(self.cards()["aaa"]["state"], "sleeping")
        board.mark("aaa", "snooze", until=time.time() + 3600)
        self.assertEqual(self.cards()["aaa"]["state"], "snoozed")
        self.assertFalse(board.mark("zzz", "done"))

    def test_wait_wakes_on_mark(self):
        board = self.make()
        board.scan()
        version = board.snapshot()[0]
        timer = threading.Timer(0.2, lambda: board.mark("aaa", "done"))
        timer.start()
        self.assertNotEqual(board.wait(version, 5), version)
        timer.join()
        self.assertEqual(self.cards()["aaa"]["state"], "done")

    def test_prunes_transcripts_older_than_max_age(self):
        board = self.make()
        board.scan()
        ccc = self.claude / "projects" / "p3" / "ccc.jsonl"
        os.utime(ccc, (self.old - 30 * 86400, self.old - 30 * 86400))
        board.scan()
        self.assertNotIn("ccc", self.cards())
        self.assertNotIn("ccc", board.transcripts)

    def test_backoff_grows_and_resets(self):
        board = self.make()
        board.scan()
        with self.assertLogs("agent_board", level="WARNING"):
            board._on_summary("aaa", "2", None, "err")
            board._on_summary("aaa", "2", None, "err")
        version, attempts, next_try = board.failed["aaa"]
        self.assertEqual((version, attempts), (2, 2))
        self.assertAlmostEqual(next_try - time.time(), 1200, delta=5)
        board._on_summary("aaa", "2", {"about": "ok"}, None)
        self.assertNotIn("aaa", board.failed)
        self.assertEqual(board.store.get_summary("aaa"), (2, {"about": "ok"}))

    def test_waiting_for_background_tasks(self):
        board = self.make()
        now = time.time()
        write_lines(self.bbb, [
            assistant(tools=[("t1", "Agent", {"prompt": "p"})], ts=iso(now)),
            tool_result("t1", "agentId: a1\nThe agent is working in the background.", ts=iso(now)),
            assistant("Чекаю на субагента", ts=iso(now)),
        ], mode="a")
        board.scan()
        self.assertEqual(self.cards()["bbb"]["state"], "waiting_bg")
        self.assertEqual(self.cards()["bbb"]["background"], 1)

    def test_hides_copy_of_conversation_moved_to_background(self):
        board = self.make()
        tutor = self.home / "server/tutor-app"
        lines = [human("тема", ts=iso(self.old), cwd=str(tutor)).replace('"parentUuid":null', '"uuid":"u1","parentUuid":null'),
                 assistant("відповідь", ts=iso(self.old), cwd=str(tutor), uuid="u2")]
        projects = self.claude / "projects" / "p6"
        write_lines(projects / "old.jsonl", lines)                                    # вихідна розмова
        write_lines(projects / "new.jsonl", lines + [assistant("далі", ts=iso(self.old), uuid="u3")])  # її продовження
        write_lines(projects / "fork.jsonl", lines[:1] + [assistant("інша гілка", ts=iso(self.old), uuid="u9")])
        board.scan()
        cards = self.cards()
        self.assertNotIn("old", cards)
        self.assertIn("new", cards)
        self.assertIn("fork", cards)  # справжня гілка не схована


    def test_exclusion_by_related_project_and_path(self):
        tutor, scripts = self.home / "server/tutor-app", self.home / "scripts/daily-brief"
        scripts.mkdir(parents=True)
        projects = self.claude / "projects" / "p7"
        write_lines(projects / "f1.jsonl", [human("viewer з іншого проєкту", ts=iso(self.old), cwd=str(tutor)),
                                            assistant("ok", tool_paths=[f"{self.wt}/a.ts"], ts=iso(self.old), cwd=str(tutor))])
        write_lines(projects / "f2.jsonl", [human("бриф", ts=iso(self.old), cwd=str(scripts))])
        self.make(summary_enabled=True, summary_exclude_paths=[str(self.home / "scripts")]).scan()
        cards = self.cards()
        self.assertEqual(cards["f1"]["related"], ["viewer"])
        self.assertEqual((cards["f1"]["summary_mode"], cards["f2"]["summary_mode"], cards["aaa"]["summary_mode"]),
                         ("excluded", "excluded", "on"))
        self.assertNotIn("viewer", self.board.known_public)  # назви робочих проєктів не йдуть у промпт

    def test_disabled_when_haiku_off(self):
        self.make().scan()
        self.assertEqual(self.cards()["aaa"]["summary_mode"], "disabled")

    def test_live_session_with_old_file_is_shown(self):
        os.utime(self.bbb, (self.old - 30 * 86400, self.old - 30 * 86400))
        self.make().scan()
        self.assertIn("bbb", self.cards())

    def test_quiet_window_blocks_summaries(self):
        board = self.make(summary_enabled=True, summary_quiet_seconds=10**9)
        board.scan()
        self.assertEqual(board.summarizer.pending_count(), 0)

    def test_no_republish_without_changes(self):
        board = self.make()
        board.scan()
        version = board.snapshot()[0]
        self.assertFalse(board.scan())
        self.assertEqual(board.wait(version, 0.3), version)

    def test_continued_in_hides_old_tab_and_marks_continuation(self):
        projects = self.claude / "projects" / "p8"
        write_lines(projects / "zzz.jsonl", [human("тема", ts=iso(self.old), cwd=str(self.shop)),
                                             '{"type":"continued-in","sessionId":"zzz","continuedInSessionId":"new2"}'])
        write_lines(projects / "new2.jsonl", [human("тема", ts=iso(self.old), cwd=str(self.shop)), assistant("далі", ts=iso(self.old))])
        self.make().scan()
        cards = self.cards()
        self.assertNotIn("zzz", cards)  # жива стара вкладка (bg-сесія zzz з setUp) схована
        self.assertTrue(cards["new2"]["old_tab_live"])
        self.assertFalse(cards["aaa"]["old_tab_live"])


    def test_exclusion_by_file_and_command(self):
        studio, iag = self.home / "studio", self.home / "server/acme/work"
        studio.mkdir(parents=True)
        projects = self.claude / "projects" / "p9"
        write_lines(projects / "g1.jsonl", [human("подивись файл", ts=iso(self.old), cwd=str(studio)),
                                            assistant("ok", tool_paths=[f"{iag}/x.md"], ts=iso(self.old), cwd=str(studio))])
        write_lines(projects / "g2.jsonl", [human("подивись теку", ts=iso(self.old), cwd=str(studio)),
                                            assistant("ok", tools=[("t1", "Bash", {"command": "cd ~/server/acme/work && ls"})],
                                                      ts=iso(self.old), cwd=str(studio))])
        self.make(summary_enabled=True, summary_quiet_seconds=10**9,
                  summary_exclude_paths=[str(self.home / "server/acme")]).scan()
        cards = self.cards()
        self.assertEqual((cards["g1"]["summary_mode"], cards["g2"]["summary_mode"]), ("excluded", "excluded"))

    def test_chat_goes_to_user_project_it_worked_in(self):
        tutor, hist = self.home / "server/tutor-app", self.home / "studio/brands/hist"
        hist.mkdir(parents=True)
        projects = self.claude / "projects" / "p10"
        many = [("t%d" % i, "Bash", {"command": f"ls ~/studio/brands/hist/ep{i}"}) for i in range(6)]
        write_lines(projects / "h1.jsonl", [human("канал історії", ts=iso(self.old), cwd=str(tutor)),
                                            assistant("ok", tools=many, ts=iso(self.old), cwd=str(tutor))])
        write_lines(projects / "h2.jsonl", [human("трохи глянь", ts=iso(self.old), cwd=str(tutor)),
                                            assistant("ok", tools=many[:2], ts=iso(self.old), cwd=str(tutor))])
        board = self.make()
        board.store.save_project(str(hist), "Історія")
        board.resolver.set_user(board.store.projects())
        board.scan()
        cards = self.cards()
        self.assertEqual((cards["h1"]["project"], cards["h1"]["project_kind"]), ("Історія", "user"))
        self.assertEqual(cards["h2"]["project"], "tutor-app")  # дві згадки — замало

    def test_prepare_refuses_card_that_became_excluded(self):
        board = self.make(summary_enabled=True, summary_quiet_seconds=10**9)
        board.scan()
        key, prompt = board._prepare("aaa")
        self.assertEqual(key, "2")
        self.assertIn("Схема БД?", prompt)
        board.cfg.summary_exclude_paths = [str(self.home / "server/tutor-app")]
        board.scan()
        self.assertEqual(self.cards()["aaa"]["summary_mode"], "excluded")
        self.assertIsNone(board._prepare("aaa"))  # картка стала робочою, поки чекала в черзі

    def test_prepare_refuses_fresh_summary(self):
        board = self.make(summary_enabled=True, summary_quiet_seconds=10**9)
        board.scan()
        board.store.put_summary("aaa", 2, {"about": "ok"})
        self.assertIsNone(board._prepare("aaa"))

    def test_backoff_blocks_same_version_but_not_new_one(self):
        board = self.make(summary_enabled=True, summary_per_hour=0)
        board.failed["aaa"] = (2, 1, time.time() + 3600)
        board.scan()
        self.assertFalse(board.summarizer.is_pending("aaa"))
        board.failed["aaa"] = (1, 3, time.time() + 3600)  # помилка була на старій версії
        board.scan()
        self.assertTrue(board.summarizer.is_pending("aaa"))

    def test_persisted_calls_feed_hourly_limit(self):
        store = Store(self.root / "b.db")
        store.record_call(time.time() - 60)
        store.close()
        board = self.make(summary_enabled=True, summary_per_hour=1)
        self.assertEqual(len(board.summarizer._starts), 1)

    def test_haiku_status_change_is_published(self):
        board = self.make()
        board.scan()
        board.summary_status = lambda: {"enabled": True, "pending": 0, "paused_until": 123.0, "last_error": "x"}
        self.assertTrue(board.scan())

    def test_old_tab_with_new_replies_is_not_hidden(self):
        projects = self.claude / "projects" / "p10"
        write_lines(projects / "old3.jsonl", [human("тема", ts=iso(self.old), cwd=str(self.shop)),
                                              '{"type":"continued-in","sessionId":"old3","continuedInSessionId":"new3"}',
                                              human("пишу далі в старій вкладці", ts=iso(self.old), cwd=str(self.shop))])
        write_lines(projects / "new3.jsonl", [human("тема", ts=iso(self.old), cwd=str(self.shop))])
        self.make().scan()
        self.assertIn("old3", self.cards())


if __name__ == "__main__":
    unittest.main()
