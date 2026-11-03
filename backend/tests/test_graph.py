"""The whole workflow against real Postgres (including the LangGraph checkpointer) with fake model and Notion.

These tests are the contract a UI depends on: when the graph stops, what it reports, what it refuses,
and what it never does twice.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app import actions, config, db, runner
from app.graph import Deps, build_graph
from tests.fakes import FakeGateway
from tests.test_reconcile import fake_model

P = "gtest-"
MEETING = datetime(2026, 10, 31, 10, 0, tzinfo=ZoneInfo("America/New_York"))
TZ = "America/New_York"

TEXT = """Dana: I'll send the revised hardware specifications to Facilities by Friday.
Sam: We could consider contacting the vendor about the rack mounts.
Priya: I sent the cabling plan to Facilities yesterday.
Marcus: I've started measuring the display mounting locations, but I'm blocked on access to the second floor."""


def ev(type_, turn, owner, deliverable, excerpt, dl_text="", dl_date="", basis="none"):
    return {"type": type_, "turn_index": turn, "owner": owner, "deliverable": deliverable, "project_hint": "",
            "deadline_text": dl_text, "deadline_date": dl_date, "deadline_basis": basis, "excerpt": excerpt}


MEETING_EVENTS = [
    ev("commitment", 0, "Dana", "revised hardware specifications", "I'll send the revised hardware specifications to Facilities by Friday",
       "by Friday", "2026-11-06", "explicit"),
    ev("suggestion", 1, "", "contact the vendor about rack mounts", "We could consider contacting the vendor about the rack mounts"),
    ev("completion_claim", 2, "Priya", "cabling plan", "I sent the cabling plan to Facilities yesterday"),
    ev("progress", 3, "Marcus", "display mounting location measurements", "I've started measuring the display mounting locations"),
    ev("blocker", 3, "Marcus", "second floor access", "I'm blocked on access to the second floor"),
]


@pytest.fixture(scope="module")
def pool():
    p = ConnectionPool(config.DATABASE_URL, kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row}, open=True)
    PostgresSaver(p).setup()
    yield p
    p.close()


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    monkeypatch.setattr("app.graph.time.sleep", lambda s: None)


def purge(pool) -> None:
    """Remove everything these tests create. Runs before and after each test so a crashed run cannot poison the next."""
    with db.connect() as conn:
        mids = [r["id"] for r in conn.execute("SELECT id FROM projects WHERE name LIKE %s", (P + "%",))]
        runs = conn.execute("SELECT thread_id, source_id FROM runs").fetchall()
        sids = [r["source_id"] for r in runs if r["source_id"]]
        threads = [r["thread_id"] for r in runs]
        cids = [r["id"] for r in conn.execute("SELECT id FROM commitments WHERE project_id = ANY(%s)", (mids,))]
        conn.execute("DELETE FROM executions WHERE payload->>'commitment_id' = ANY(%s)", ([str(c) for c in cids],))
        conn.execute("DELETE FROM decisions WHERE review_item_id IN (SELECT id FROM review_items WHERE source_id = ANY(%s))", (sids,))
        conn.execute("DELETE FROM review_items WHERE source_id = ANY(%s)", (sids,))
        conn.execute("DELETE FROM audit_log WHERE entity_type = 'commitment' AND entity_id = ANY(%s)", (cids,))
        conn.execute("DELETE FROM audit_log WHERE entity_type = 'review_item'")
        conn.execute("DELETE FROM commitments WHERE id = ANY(%s)", (cids,))
        conn.execute("DELETE FROM runs WHERE thread_id = ANY(%s)", (threads,))
        conn.execute("DELETE FROM sources WHERE id = ANY(%s)", (sids,))
        conn.execute("DELETE FROM notion_tasks WHERE notion_page_id LIKE %s OR notion_page_id LIKE 'fake-%%' OR project_id = ANY(%s)", (P + "%", mids))
        conn.execute("DELETE FROM projects WHERE id = ANY(%s)", (mids,))
    with pool.connection() as c:
        for tbl in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            c.execute(f"DELETE FROM {tbl} WHERE thread_id = ANY(%s)", (threads,))


@pytest.fixture
def world(pool):
    purge(pool)
    gw = FakeGateway()
    with db.connect() as conn:
        mid = conn.execute("INSERT INTO projects (name, notion_page_id) VALUES (%s, %s) RETURNING id", (P + "MidTown", P + "proj-mid")).fetchone()["id"]
        for page, title, owner, due, status in [
            (P + "specs", "Send revised hardware specifications for facilities", "Dana", date(2026, 11, 6), "In-progress"),
            (P + "cabling", "Finalize cabling and network-drop plan", "Priya", date(2026, 11, 3), "Not-started"),
            (P + "mount", "Confirm display mounting locations and measurements", "Marcus", date(2026, 11, 2), "In-progress"),
        ]:
            t = gw.add(page, title, owner, due, status, P + "proj-mid", P + "MidTown")
            conn.execute(
                """INSERT INTO notion_tasks (notion_page_id, title, owner_name, due_date, status, project_name, project_page_id,
                       project_id, notion_last_edited) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (page, title, owner, due, status, P + "MidTown", P + "proj-mid", mid, t.last_edited))

    def make_graph(events=MEETING_EVENTS):
        model = fake_model([])
        calls = {"n": 0}

        def llm_call(system, user, name, schema):
            calls["n"] += 1
            if name == "Extraction":
                names = ["Dana", "Sam", "Priya", "Marcus"]
                return {"meeting_project": P + "MidTown", "events": events,
                        "speakers": [{"label": n, "name": n, "confidence": "high", "basis": "written"} for n in names]}
            return model(system, user, name, schema)

        deps = Deps(llm_call=llm_call, gateway=gw, refresh_fn=lambda: None)
        return build_graph(PostgresSaver(pool), deps), calls

    yield {"gw": gw, "make_graph": make_graph, "mid": mid}

    purge(pool)


def rows(sql, *args):
    with db.connect() as conn:
        return conn.execute(sql, args).fetchall()


def start(graph, text=TEXT):
    return runner.start_run("transcript", occurred_at=MEETING, tz=TZ, text=text, graph=graph)


def items_by_kind(run):
    return {r["kind"]: r for r in rows("SELECT * FROM review_items WHERE source_id = %s AND status = 'open' ORDER BY id", run["source_id"])}


def approve_all(run):
    for r in rows("SELECT id FROM review_items WHERE source_id = %s AND status = 'open'", run["source_id"]):
        runner.submit_decision(r["id"], "approve")


def commitments():
    return {r["notion_page_id"] or r["deliverable"]: r for r in rows("SELECT * FROM commitments")}


# ---- the contract -------------------------------------------------------------------------------

def test_graph_pauses_for_review_then_applies(world):
    graph, _ = world["make_graph"]()
    run = start(graph)

    # The graph stops here and reports it, holding no process.
    assert run["status"] == "waiting_for_review" and run["current_node"] == "human_review"
    assert run["can_apply"] is False and len(run["undecided_item_ids"]) == 3
    kinds = items_by_kind(run)
    assert set(kinds) == {"task_match", "suggestion", "potential_completion"}
    assert world["gw"].calls.count("mark_done_applied") == 0  # nothing is written before a decision

    # Apply is refused until every item has a decision, and says which are missing.
    runner.submit_decision(kinds["task_match"]["id"], "approve")
    with pytest.raises(actions.DecisionError, match="still need a decision"):
        runner.apply_run(run["thread_id"], graph)

    approve_all(run)
    done = runner.apply_run(run["thread_id"], graph)
    assert done["status"] == "completed"

    c = commitments()
    assert c[P + "specs"]["state"] == "open"            # link approved
    assert c[P + "cabling"]["state"] == "resolved"      # completion approved
    assert c[P + "mount"]["state"] == "open"            # evidence only, untouched
    assert world["gw"].pages[P + "cabling"].status == "Done"
    assert world["gw"].pages[P + "specs"].status == "In-progress"  # linking never writes to Notion
    assert world["gw"].calls.count("mark_done_applied") == 1
    assert all(r["status"] == "resolved" for r in rows("SELECT status FROM review_items WHERE source_id = %s", run["source_id"]))
    assert [r["status"] for r in rows("SELECT status FROM executions")] == ["done"]


def test_a_decision_can_change_until_apply_then_is_locked(world):
    graph, _ = world["make_graph"]()
    run = start(graph)
    first = items_by_kind(run)["task_match"]["id"]
    runner.submit_decision(first, "reject")
    runner.submit_decision(first, "approve")  # changed mind
    approve_all(run)
    runner.apply_run(run["thread_id"], graph)
    with pytest.raises(actions.DecisionError, match="already resolved"):
        runner.submit_decision(first, "reject")


def test_rejecting_everything_writes_nothing_and_restores_state(world):
    graph, _ = world["make_graph"]()
    run = start(graph)
    for r in rows("SELECT id FROM review_items WHERE source_id = %s", run["source_id"]):
        runner.submit_decision(r["id"], "reject")
    assert runner.apply_run(run["thread_id"], graph)["status"] == "completed"
    c = commitments()
    assert c[P + "specs"]["state"] == "rejected"
    assert c[P + "cabling"]["state"] == "open"  # the proposed resolution was declined
    assert "mark_done_applied" not in world["gw"].calls and rows("SELECT 1 FROM executions") == []


def test_notion_edited_after_sync_loops_back_for_another_review(world):
    graph, _ = world["make_graph"]()
    run = start(graph)
    approve_all(run)
    world["gw"].edit_externally(P + "cabling", due=date(2026, 11, 12))  # a person changes the task first

    again = runner.apply_run(run["thread_id"], graph)
    assert again["status"] == "waiting_for_review"           # looped back instead of overwriting
    (retry_id,) = actions.undecided_ids(run["source_id"])  # only the replacement item needs a decision
    (retry,) = rows("SELECT * FROM review_items WHERE id = %s", retry_id)
    assert retry["kind"] == "conflict" and "edited in Notion" in retry["reason"]
    assert world["gw"].pages[P + "cabling"].status == "Not-started"

    runner.submit_decision(retry["id"], "approve")
    assert runner.apply_run(run["thread_id"], graph)["status"] == "completed"
    assert world["gw"].pages[P + "cabling"].status == "Done"
    c = commitments()
    assert c[P + "cabling"]["state"] == "resolved" and c[P + "specs"]["state"] == "open"  # round-one approval survived the loop


def test_failed_write_is_recorded_retryable_and_applied_exactly_once(world):
    graph, _ = world["make_graph"]()
    run = start(graph)
    approve_all(run)
    world["gw"].fail_times["mark_done"] = 2  # the step's own retry fails too

    failed = runner.apply_run(run["thread_id"], graph)
    assert failed["status"] == "failed" and "mark_done" in failed["message"] or "failed" in failed["message"]
    assert commitments()[P + "cabling"]["state"] == "resolution_proposed"  # nothing was applied

    done = runner.retry_run(run["thread_id"], graph)
    assert done["status"] == "completed"
    assert world["gw"].calls.count("mark_done_applied") == 1
    assert world["gw"].pages[P + "cabling"].evidence.count("[pp-") == 1
    assert [r["status"] for r in rows("SELECT status FROM executions")] == ["done"]
    with pytest.raises(actions.DecisionError, match="failed run"):
        runner.retry_run(run["thread_id"], graph)


def test_creating_a_task_survives_a_crash_after_the_remote_write(world):
    graph, _ = world["make_graph"]([
        ev("commitment", 0, "Priya", "order spare patch cables", "I'll send the revised hardware specifications to Facilities by Friday")])
    run = start(graph)
    (item,) = rows("SELECT * FROM review_items WHERE source_id = %s", run["source_id"])
    assert item["proposed_action"]["type"] == "create_task"
    runner.submit_decision(item["id"], "approve")
    world["gw"].fail_after_create = 1  # Notion creates the page, then the call dies

    assert runner.apply_run(run["thread_id"], graph)["status"] == "completed"
    created = [p for p in world["gw"].pages.values() if p.title == "order spare patch cables"]
    assert len(created) == 1                                      # the retry found its own page by marker
    assert world["gw"].calls.count("create_task") == 1
    (c,) = rows("SELECT * FROM commitments WHERE deliverable = 'order spare patch cables'")
    assert c["state"] == "open" and c["notion_page_id"] == created[0].page_id


def test_editing_a_decision_changes_what_is_applied(world):
    graph, _ = world["make_graph"]([
        ev("commitment", 0, "", "revised hardware specifications", "I'll send the revised hardware specifications to Facilities by Friday")])
    run = start(graph)
    (item,) = rows("SELECT * FROM review_items WHERE source_id = %s", run["source_id"])
    assert item["kind"] == "ambiguous_owner_or_deadline" and item["proposed_action"]["suggested_owner"] == "Dana"
    runner.submit_decision(item["id"], "edit", {"owner": "Dana"})
    runner.apply_run(run["thread_id"], graph)
    c = commitments()[P + "specs"]
    assert c["owner_name"] == "Dana" and c["state"] == "open"


def test_the_same_source_twice_is_a_duplicate_and_costs_nothing(world):
    graph, calls = world["make_graph"]()
    first = start(graph)
    used = calls["n"]
    second = start(graph)
    assert second["status"] == "duplicate" and second["source_id"] == first["source_id"]
    assert calls["n"] == used  # no model call


def test_a_source_with_nothing_actionable_ends_as_no_events(world):
    graph, _ = world["make_graph"]([])
    run = start(graph)
    assert run["status"] == "no_events" and rows("SELECT 1 FROM review_items") == []


def test_nothing_to_review_goes_straight_to_completed(world):
    graph, _ = world["make_graph"]([MEETING_EVENTS[3], MEETING_EVENTS[4]])  # progress and blocker only
    run = start(graph)
    assert run["status"] == "completed" and run["undecided_item_ids"] == []
    rel = {r["relation"] for r in rows("SELECT relation FROM evidence")}
    assert rel == {"progress", "blocker"}


def test_progress_events_are_streamed_node_by_node(world):
    graph, _ = world["make_graph"]()
    thread, events = runner.stream_start("transcript", occurred_at=MEETING, tz=TZ, text=TEXT, graph=graph)
    seen = [e for e in events]
    nodes = [e["node"] for e in seen if e["event"] == "node"]
    assert nodes == ["ingest", "retrieve_context", "extract", "match", "reconcile"]
    assert seen[-2]["event"] == "waiting_for_review" and seen[-1] == {"event": "stopped", "status": "waiting_for_review"}
