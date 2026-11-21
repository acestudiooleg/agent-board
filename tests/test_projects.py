import tempfile
import unittest
from pathlib import Path

from agent_board.config import FALLBACK_WORKTREE_RULES, Family
from agent_board.projects import ProjectRef, ProjectResolver, family_of, related_projects


class ResolverTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        h = self.home = Path(self.tmp.name)
        (h / "server/tutor-app/.git").mkdir(parents=True)
        (h / "server/tutor-app/apps/landing").mkdir(parents=True)
        (h / "server/acme/work/viewer/.git").mkdir(parents=True)
        wt = h / "server/acme/work/.worktrees/viewer/TASK-34"
        (wt / "src").mkdir(parents=True)
        (wt / ".git").write_text(f"gitdir: {h}/server/acme/work/viewer/.git/worktrees/TASK-34\n")
        (h / "studio/core/.git").mkdir(parents=True)
        (h / "studio/brands/x").mkdir(parents=True)
        self.r = ProjectResolver(str(h), [(str(h / "studio"), "studio")], FALLBACK_WORKTREE_RULES)

    def tearDown(self):
        self.tmp.cleanup()

    def test_subdir_resolves_to_git_root(self):
        self.assertEqual(self.r.resolve(f"{self.home}/server/tutor-app/apps/landing"),
                         ProjectRef("tutor-app", None, "git"))

    def test_worktree_git_file(self):
        expected = ProjectRef("viewer", "TASK-34", "worktree")
        self.assertEqual(self.r.resolve(f"{self.home}/server/acme/work/.worktrees/viewer/TASK-34"), expected)
        self.assertEqual(self.r.resolve(f"{self.home}/server/acme/work/.worktrees/viewer/TASK-34/src/"), expected)

    def test_git_beats_table_and_table_covers_non_git(self):
        self.assertEqual(self.r.resolve(f"{self.home}/studio/core"), ProjectRef("core", None, "git"))
        self.assertEqual(self.r.resolve(f"{self.home}/studio/brands/x"), ProjectRef("studio", None, "table"))

    def test_longest_table_prefix_wins(self):
        (self.home / "studio/brands/alpha").mkdir(parents=True)
        r = ProjectResolver(str(self.home), [(str(self.home / "studio"), "studio"),
                                             (str(self.home / "studio/brands/alpha"), "alpha")], FALLBACK_WORKTREE_RULES)
        self.assertEqual(r.resolve(f"{self.home}/studio/brands/alpha/x"), ProjectRef("alpha", None, "table"))
        self.assertEqual(r.resolve(f"{self.home}/studio/brands"), ProjectRef("studio", None, "table"))

    def test_rule_and_dir_fallbacks(self):
        self.assertEqual(self.r.resolve(f"{self.home}/server/x/.worktrees/foo/bar/baz"),
                         ProjectRef("foo", "bar", "rule"))
        self.assertEqual(self.r.resolve(str(self.home)), ProjectRef(self.home.name, None, "dir"))
        self.assertEqual(self.r.resolve(""), ProjectRef("?", None, "dir"))

    def test_user_project_beats_git_and_is_updatable(self):
        landing = f"{self.home}/server/tutor-app/apps/landing"
        self.assertEqual(self.r.resolve(landing).name, "tutor-app")
        self.r.set_user([(f"{self.home}/server/tutor-app/apps/landing/", "Лендинг ШІколи")])
        ref = self.r.resolve(landing + "/src")
        self.assertEqual((ref.name, ref.kind, ref.root), ("Лендинг ШІколи", "user", landing))
        self.assertEqual(self.r.resolve(f"{self.home}/server/tutor-app").name, "tutor-app")  # поза текою — як раніше

    def test_detected_root(self):
        self.assertEqual(self.r.resolve(f"{self.home}/server/tutor-app/apps/landing").root, f"{self.home}/server/tutor-app")
        wt = self.r.resolve(f"{self.home}/server/acme/work/.worktrees/viewer/TASK-34/src")
        self.assertEqual(wt.root, f"{self.home}/server/acme/work/viewer")

    def test_related_projects(self):
        files = {f"{self.home}/server/acme/work/viewer/a.ts", f"{self.home}/server/tutor-app/b.py", "/etc/hosts"}
        dirs = {f"{self.home}/studio/brands/x", "/tmp/scratch"}
        self.assertEqual(related_projects(self.r, files, dirs, "tutor-app"), ["studio", "viewer"])


class FamilyOfTest(unittest.TestCase):
    def test_known(self):
        self.assertEqual(family_of("shop-bot", [Family("Магазин", "🛒", ["shop-bot"])]), ("Магазин", "🛒"))

    def test_unknown(self):
        self.assertEqual(family_of("uamgp", []), ("uamgp", "📁"))


if __name__ == "__main__":
    unittest.main()
