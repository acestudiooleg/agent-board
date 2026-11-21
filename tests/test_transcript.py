import tempfile
import unittest
from pathlib import Path

import agent_board.transcript as transcript_module
from agent_board.transcript import HEAD, TAIL, Transcript, update
from tests.helpers import (api_request, assistant, command, human, human_with_reminder, local_caveat,
                           queued_notification, queued_prompt, queued_task_done, task_notification, title,
                           tool_result, write_lines)


class TranscriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "abc.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def parse(self, lines):
        write_lines(self.path, lines)
        t = Transcript("abc", self.path)
        update(t)
        return t

    def test_parses_conversation_and_keeps_first_cwd(self):
        t = self.parse([
            api_request(),
            human("Яку схему БД взяти?", cwd="/home/u/server/tutor-app/apps/landing"),
            tool_result(),
            assistant(tool_paths=["/home/u/server/d7/db.py", "relative.txt"], cwd="/tmp/scratch"),
            assistant("Пропоную Postgres. Видаляти стару таблицю?", cwd="/tmp/scratch"),
            title("Схема БД"),
            "{broken json",
        ])
        self.assertEqual(t.title, "Схема БД")
        self.assertEqual(t.first_user, "Яку схему БД взяти?")
        self.assertEqual(t.cwd, "/home/u/server/tutor-app/apps/landing")
        self.assertEqual(t.dirs, {"/home/u/server/tutor-app/apps/landing", "/tmp/scratch", "/home/u/server/tutor-app"})
        self.assertEqual(t.files, {"/home/u/server/d7/db.py"})
        self.assertEqual(t.branch, "main")
        self.assertEqual(t.last_speaker, "assistant")
        self.assertEqual(t.head, [("user", "Яку схему БД взяти?"), ("assistant", "Пропоную Postgres. Видаляти стару таблицю?")])
        self.assertEqual((t.version, t.humans), (2, 1))
        self.assertEqual(t.last("assistant"), "Пропоную Postgres. Видаляти стару таблицю?")
        self.assertGreater(t.last_activity, 0)

    def test_incremental_and_partial_line(self):
        t = self.parse([human("перше")])
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(assistant("відповідь") + "\n" + human("друге")[:20])
        self.assertTrue(update(t))
        self.assertEqual((t.version, t.last_speaker), (2, "assistant"))
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(human("друге")[20:] + "\n")
        self.assertTrue(update(t))
        self.assertEqual((t.version, t.last_speaker), (3, "user"))
        self.assertFalse(update(t))

    def test_service_lines_are_not_human(self):
        t = self.parse([human("агентне", sidechain=True), tool_result(), local_caveat(), task_notification("x")])
        self.assertIsNone(t.first_user)
        self.assertEqual((t.version, t.humans), (0, 0))

    def test_image_only_message(self):
        line = human("x").replace('"content":"x"', '"content":[{"type":"image","source":{}}]')
        self.assertEqual(self.parse([line]).first_user, "[зображення]")

    def test_slash_command_is_human(self):
        t = self.parse([command("/five-critics", "план"), assistant("Запускаю")])
        self.assertEqual(t.first_user, "/five-critics план")
        self.assertEqual(t.humans, 1)

    def test_unclosed_command_tag_is_ignored(self):
        from tests.helpers import _user
        t = self.parse([human("привіт"), _user("текст про тег <command-name> без закриття"), assistant("ок")])
        self.assertEqual(t.humans, 1)

    def test_background_tasks(self):
        t = self.parse([
            human("запусти критиків"),
            assistant(tools=[("t1", "Agent", {"prompt": "p"}), ("t2", "Bash", {"command": "x", "run_in_background": True}),
                             ("t3", "Agent", {"prompt": "p"}), ("t6", "Workflow", {"script": "s"})]),
            tool_result("t1", "Async agent launched successfully.\nagentId: a1 (internal ID)\nThe agent is working in the background."),
            tool_result("t2", "Command running in background with ID: b2. Output is being written to: /tmp/x"),
            tool_result("t3", "Звіт субагента: усе добре. agentId: a3"),
            tool_result("t6", "Workflow launched in background. Task ID: w6"),
            assistant(tools=[("t4", "Agent", {"prompt": "p"}), ("t5", "Agent", {"prompt": "p"})]),
            tool_result("t4", "agentId: a4\nThe agent is working in the background."),
            tool_result("t5", "agentId: a5\nThe agent is working in the background."),
            assistant("Чекаю на критиків"),
        ])
        self.assertEqual(set(t.bg_pending), {"a1", "b2", "a4", "a5", "w6"})
        self.assertEqual(t.pending_background(t.last_activity + 60), 5)
        self.assertEqual(t.pending_background(t.last_activity + 4 * 3600), 0)  # старші за 3 год не рахуються
        quoted = "<task-id>a1</task-id><status>completed</status></task-notification>"
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(assistant(f"Цитую код: {quoted}") + "\n" + tool_result("r", f"файл: {quoted}") + "\n")
        update(t)
        self.assertIn("a1", t.bg_pending)  # цитата в рядках агента й tool_result не закриває задачу
        with self.path.open("a", encoding="utf-8") as fh:
            for line in (task_notification("a1"), task_notification("b2", "failed"), queued_notification("a4"),
                         human_with_reminder("що далі?", "a5"), task_notification("w6")):
                fh.write(line + "\n")
        update(t)
        self.assertEqual(t.bg_pending, {})
        self.assertEqual(t.last("user"), "що далі?")  # <system-reminder> вирізано з репліки

    def test_continued_in(self):
        t = self.parse([human("a"), '{"type":"continued-in","sessionId":"abc","continuedInSessionId":"new"}'])
        self.assertEqual((t.continued_in, t.continued_at, t.version), ("new", 1, 1))

    def test_queued_prompt_is_human(self):
        t = self.parse([human("перше"), assistant(tools=[("t1", "Bash", {"command": "ls"})]),
                        queued_prompt("ще одне, поки ти працюєш"), assistant(tools=[("t2", "Read", {"file_path": "/a"})])])
        self.assertEqual((t.humans, t.version), (2, 2))
        self.assertEqual(t.last("user"), "ще одне, поки ти працюєш")
        self.assertEqual(t.last_speaker, "assistant")
        t = self.parse([queued_prompt("лише в черзі")])
        self.assertEqual((t.first_user, t.last_speaker), ("лише в черзі", "user"))

    def test_background_done_via_attachment_and_before_launch(self):
        t = self.parse([
            human("x"),
            assistant(tools=[("t1", "Bash", {"command": "x", "run_in_background": True}),
                             ("t2", "Bash", {"command": "y", "run_in_background": True})]),
            queued_notification("b2"),  # завершення записане раніше за tool_result запуску
            tool_result("t1", "Command running in background with ID: b1."),
            tool_result("t2", "Command running in background with ID: b2."),
            queued_task_done("b1"),
        ])
        self.assertEqual(t.bg_pending, {})

    def test_collects_command_arguments(self):
        t = self.parse([assistant(tools=[("t1", "Bash", {"command": "cd ~/server/acme/work && ls", "description": "list"})])])
        self.assertEqual(t.commands, {"cd ~/server/acme/work && ls"})

    def test_tracks_uuids(self):
        t = self.parse([human("a").replace('"parentUuid":null', '"uuid":"u1","parentUuid":null'),
                        assistant("b", uuid="u2"), '{"type":"system","uuid":"u3"}'])
        self.assertEqual((t.uuids, t.last_uuid), ({"u1", "u2", "u3"}, "u3"))

    def test_sdk_flag(self):
        t = self.parse([assistant("ok", entrypoint="sdk-cli", promptSource="sdk")])
        self.assertTrue(t.sdk)

    def test_head_and_tail(self):
        lines = []
        for i in range(30):
            lines += [human(f"u{i}"), assistant(f"a{i}")]
        t = self.parse(lines)
        self.assertEqual(len(t.head), HEAD)
        self.assertEqual(t.head[0], ("user", "u0"))
        self.assertEqual(len(t.tail), TAIL)
        self.assertEqual(t.tail[-1], (59, "assistant", "a29"))

    def test_small_chunks_give_same_result(self):
        old = transcript_module.CHUNK
        transcript_module.CHUNK = 64
        try:
            t = self.parse([human("перше " * 20), assistant("друге " * 20), human("третє")])
        finally:
            transcript_module.CHUNK = old
        self.assertEqual((t.version, t.last_speaker, t.first_user), (3, "user", ("перше " * 20).strip()))

    def test_truncated_file_is_reparsed(self):
        t = self.parse([human("a"), assistant("b"), human("c")])
        write_lines(self.path, [human("нове")])
        update(t)
        self.assertEqual((t.first_user, t.version), ("нове", 1))

    def test_missing_file(self):
        self.assertFalse(update(Transcript("abc", Path(self.tmp.name) / "none.jsonl")))


if __name__ == "__main__":
    unittest.main()
