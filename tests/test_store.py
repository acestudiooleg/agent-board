import os
import stat
import tempfile
import unittest
from pathlib import Path

from agent_board.store import Mark, Store


class StoreTest(unittest.TestCase):
    def open(self, path):
        store = Store(path)
        self.addCleanup(store.close)
        return store

    def test_newer_summary_wins_older_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.open(Path(tmp) / "sub" / "b.db")
            self.assertIsNone(store.get_summary("s"))
            self.assertTrue(store.put_summary("s", 3, {"about": "перше"}))
            self.assertTrue(store.put_summary("s", 5, {"about": "друге"}))
            self.assertFalse(store.put_summary("s", 4, {"about": "застаріле"}))
            self.assertEqual(store.get_summary("s"), (5, {"about": "друге"}))

    def test_marks_survive_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "b.db"
            self.open(path).set_mark("s", Mark("snooze", 500.0, 100.0))
            store = self.open(path)
            self.assertEqual(store.marks(), {"s": Mark("snooze", 500.0, 100.0)})
            store.set_mark("s", Mark("done", None, 120.0))
            self.assertEqual(store.marks()["s"].kind, "done")
            store.clear_mark("s")
            self.assertEqual(store.marks(), {})

    def test_calls_survive_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "b.db"
            store = self.open(path)
            store.record_call(1000.0)
            store.record_call(2000.0)
            store.close()
            self.assertEqual(self.open(path).calls_since(1500.0), [2000.0])

    def test_projects(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.open(Path(tmp) / "b.db")
            store.save_project("/w/a", "Альфа")
            store.save_project("/w/b", "Бета")
            store.save_project("/w/a2", "Альфа", old_path="/w/a")  # перенесли теку
            self.assertEqual(store.projects(), [("/w/a2", "Альфа"), ("/w/b", "Бета")])
            store.delete_project("/w/b")
            self.assertEqual(store.projects(), [("/w/a2", "Альфа")])

    def test_project_icons(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.open(Path(tmp) / "b.db")
            store.set_project_icon("ohif", "🩻")
            store.set_project_icon("tpl", "📄")
            store.set_project_icon("tpl", "")  # порожній — прибрати
            self.assertEqual(store.project_icons(), {"ohif": "🩻"})

    def test_db_is_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "b.db"
            self.open(path)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
