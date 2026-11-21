from .live import LiveSession
from .store import Mark
from .transcript import Transcript


def compute_state(live: LiveSession | None, t: Transcript, mark: Mark | None, now: float) -> str:
    if mark and t.last_activity <= mark.at_activity:
        if mark.kind == "done":
            return "done"
        if mark.kind == "snooze" and mark.until and now < mark.until:
            return "snoozed"
    if live:
        if live.status == "busy":
            return "working"
        if live.status == "waiting":
            return "waiting"     # агент поставив питання або чекає дозволу — найтерміновіше
        if live.status == "shell":
            return "waiting_bg"  # хід закінчено, фонова команда ще йде (часто її запустив субагент)
        if live.status != "idle" or t.last_speaker != "assistant":
            return "idle_live"
        return "waiting_bg" if t.pending_background(now) else "waiting"
    return "sleeping"
