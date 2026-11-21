import json
import tempfile
import unittest
from pathlib import Path

from agent_board.live import read_live


def session(pid, sid, status="idle", kind="interactive"):
    return {"pid": pid, "sessionId": sid, "cwd": "/home/u/server/x", "startedAt": 1795090045539,
            "kind": kind, "name": f"n-{sid}", "status": status}


class ReadLiveTest(unittest.TestCase):
    def test_filters_dead_and_unknown_kinds(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "1.json").write_text(json.dumps(session(1, "a", "busy")))
            (d / "2.json").write_text(json.dumps(session(2, "b")))
            (d / "3.json").write_text(json.dumps(session(3, "c", kind="bg")))   # розмова, переведена у фон
            (d / "6.json").write_text(json.dumps(session(6, "f", kind="sdk")))
            (d / "7.json").write_text(json.dumps({**session(7, "w", "waiting"), "waitingFor": "input needed"}))
            (d / "4.json").write_text("{broken")
            (d / "5.key").write_text("x")
            live = read_live(d, alive=lambda pid: pid != 2)
        self.assertEqual(set(live), {"a", "c", "w"})
        self.assertEqual((live["w"].status, live["w"].waiting_for), ("waiting", "input needed"))
        self.assertEqual((live["a"].status, live["a"].name, live["a"].kind), ("busy", "n-a", "interactive"))
        self.assertEqual(live["c"].kind, "bg")
        self.assertAlmostEqual(live["a"].started_at, 1795090045.539)

    def test_missing_dir(self):
        self.assertEqual(read_live(Path("/nonexistent/sessions")), {})


if __name__ == "__main__":
    unittest.main()
