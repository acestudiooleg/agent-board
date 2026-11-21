import unittest
from pathlib import Path

from agent_board.live import LiveSession
from agent_board.status import compute_state
from agent_board.store import Mark
from agent_board.transcript import Transcript


def tr(speaker="assistant", activity=100.0, bg=None):
    t = Transcript("s", Path("/x"))
    t.last_speaker, t.last_activity = speaker, activity
    t.bg_pending = dict(bg or {})
    return t


def live(status="idle"):
    return LiveSession(1, "s", "/x", "n", status, 0)


class StateTest(unittest.TestCase):
    def test_live_states(self):
        self.assertEqual(compute_state(live("busy"), tr(), None, 200), "working")
        self.assertEqual(compute_state(live("weird"), tr(), None, 200), "idle_live")  # невідомий статус ≠ «працює»
        self.assertEqual(compute_state(live("shell"), tr("user"), None, 200), "waiting_bg")
        self.assertEqual(compute_state(live("waiting"), tr("user"), None, 200), "waiting")  # питання / дозвіл
        self.assertEqual(compute_state(live(), tr("assistant"), None, 200), "waiting")
        self.assertEqual(compute_state(live(), tr("user"), None, 200), "idle_live")
        self.assertEqual(compute_state(live(), tr(None), None, 200), "idle_live")

    def test_waiting_for_background_tasks(self):
        self.assertEqual(compute_state(live(), tr(bg={"a1": 150.0}), None, 200), "waiting_bg")
        self.assertEqual(compute_state(live(), tr(bg={"a1": 150.0}), None, 150.0 + 4 * 3600), "waiting")

    def test_closed_is_sleeping(self):
        self.assertEqual(compute_state(None, tr(), None, 200), "sleeping")

    def test_done_until_new_activity(self):
        mark = Mark("done", None, 100.0)
        self.assertEqual(compute_state(None, tr(activity=100.0), mark, 200), "done")
        self.assertEqual(compute_state(live(), tr(activity=150.0), mark, 200), "waiting")

    def test_snooze_expires(self):
        mark = Mark("snooze", 300.0, 100.0)
        self.assertEqual(compute_state(None, tr(), mark, 200), "snoozed")
        self.assertEqual(compute_state(None, tr(), mark, 400), "sleeping")


if __name__ == "__main__":
    unittest.main()
