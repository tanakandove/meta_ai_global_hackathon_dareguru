"""Sources are analysed in parallel, but they change Notion in the order they arrived."""
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from app import db, graph

REAL_CHECK = graph.unfinished_earlier_runs  # captured at import, before the autouse fixture patches it
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def real_queue(monkeypatch):
    monkeypatch.setattr(graph, "unfinished_earlier_runs", REAL_CHECK)
    monkeypatch.setattr(graph, "WAIT_POLL_SECONDS", 0.05)
    yield
    with db.connect() as conn:
        conn.execute("DELETE FROM runs WHERE thread_id LIKE 'gtest-ord-%'")


def add(name: str, status: str, minutes: int) -> str:
    tid = f"gtest-ord-{name}"
    with db.connect() as conn:
        conn.execute("INSERT INTO runs (thread_id, kind, status, created_at) VALUES (%s, 'text', %s, %s)",
                     (tid, status, T0 + timedelta(minutes=minutes)))
    return tid


def test_only_earlier_unfinished_runs_block():
    add("1", "running", 1)             # blocks: still analysing
    add("2", "completed", 2)           # done
    add("3", "waiting_for_review", 3)  # blocks: the person has not applied it yet
    add("4", "failed", 4)              # does not block: it never reached Notion
    add("5", "no_events", 5)           # done
    add("6", "superseded", 6)          # replaced by a re-run
    me = add("7", "running", 7)
    add("8", "running", 8)             # arrived later, so it waits for me, not the other way round
    assert graph.unfinished_earlier_runs(me) == 2


def test_the_first_run_never_waits():
    first = add("1", "running", 1)
    add("2", "running", 2)
    assert graph.unfinished_earlier_runs(first) == 0
    assert graph.wait_for_turn(first) is False


def test_a_later_run_waits_until_the_earlier_one_is_applied():
    earlier = add("1", "waiting_for_review", 1)
    later = add("2", "running", 2)
    seen = {}

    def finish_later():
        time.sleep(0.3)
        seen["finished_at"] = time.monotonic()
        with db.connect() as conn:
            conn.execute("UPDATE runs SET status = 'completed' WHERE thread_id = %s", (earlier,))

    threading.Thread(target=finish_later).start()
    assert graph.wait_for_turn(later) is True
    assert time.monotonic() >= seen["finished_at"]
    with db.connect() as conn:  # while it waited, the run said why
        assert "earlier source" in conn.execute("SELECT message FROM runs WHERE thread_id = %s", (later,)).fetchone()["message"]
