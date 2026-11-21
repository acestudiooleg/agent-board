import os
import tempfile
import unittest
from pathlib import Path

from agent_board.config import load_config

ROOT = Path(__file__).resolve().parent.parent


class LoadConfigTest(unittest.TestCase):
    def test_defaults_when_file_missing(self):
        cfg = load_config(Path("/nonexistent/config.toml"))
        self.assertEqual((cfg.port, cfg.max_age_days, cfg.board_name), (8091, 14, "Свої"))
        self.assertEqual(cfg.families, [])
        self.assertTrue(cfg.summary_enabled)
        self.assertEqual(cfg.summary_per_hour, 20)
        self.assertEqual(cfg.summary_exclude_families, [])
        self.assertEqual(cfg.config_dir_env(), "")
        for flag in ("--no-session-persistence", "--setting-sources", "--strict-mcp-config", "--disable-slash-commands"):
            self.assertIn(flag, cfg.summary_command)

    def test_reads_board_families_and_projects(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.toml"
            path.write_text(
                '[board]\nboard_name = "Робота"\nport = 9000\nmax_age_days = 3\n'
                'claude_dir = "~/.claude-work"\nsummary_exclude_families = []\nsummary_exclude_paths = ["~/server/acme/"]\n'
                '[[family]]\nname = "Магазин"\nicon = "🛒"\nprojects = ["shop-bot"]\n'
                '[[project]]\npath = "~/studio/"\nname = "studio"\n',
                encoding="utf-8",
            )
            with self.assertLogs("agent_board", level="WARNING"):  # чужа claude_dir → Haiku вимкнено
                cfg = load_config(path)
        self.assertEqual((cfg.board_name, cfg.port, cfg.max_age_days), ("Робота", 9000, 3))
        self.assertFalse(cfg.summary_enabled)
        self.assertEqual(cfg.claude_dir, Path.home() / ".claude-work")
        self.assertEqual(cfg.config_dir_env(), str(Path.home() / ".claude-work"))
        self.assertEqual(cfg.summary_exclude_families, [])
        self.assertEqual(cfg.summary_exclude_paths, [str(Path.home() / "server/acme")])
        self.assertEqual((cfg.families[0].name, cfg.families[0].icon, cfg.families[0].projects),
                         ("Магазин", "🛒", ["shop-bot"]))
        self.assertEqual(cfg.project_paths, [(str(Path.home() / "studio"), "studio")])

    def test_env_overrides(self):
        env = {"AGENT_BOARD_CLAUDE_DIR": "/tmp/fake-claude", "AGENT_BOARD_DB": "/tmp/fake.db",
               "AGENT_BOARD_NO_SUMMARY": "1"}
        os.environ.update(env)
        try:
            cfg = load_config(None)
        finally:
            for key in env:
                del os.environ[key]
        self.assertEqual(cfg.claude_dir, Path("/tmp/fake-claude"))
        self.assertEqual(cfg.db_path, Path("/tmp/fake.db"))
        self.assertFalse(cfg.summary_enabled)

    def test_foreign_claude_dir_forces_haiku_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.toml"
            path.write_text('[board]\nclaude_dir = "~/.claude-work"\nsummary_enabled = true\n', encoding="utf-8")
            with self.assertLogs("agent_board", level="WARNING"):
                cfg = load_config(path)
        self.assertFalse(cfg.summary_enabled)

    def test_example_configs_parse(self):
        personal = load_config(ROOT / "config.example.toml")
        work = load_config(ROOT / "config.work.example.toml")
        self.assertNotEqual(personal.port, work.port)
        self.assertNotEqual(personal.db_path, work.db_path)
        self.assertNotEqual(work.claude_dir, Path.home() / ".claude")
        self.assertFalse(work.summary_enabled)
        self.assertTrue(personal.summary_enabled)
        self.assertEqual((personal.summary_exclude_families, work.planet), (["Work"], "mars"))
        self.assertIn(str(Path.home() / "work"), personal.summary_exclude_paths)


if __name__ == "__main__":
    unittest.main()
