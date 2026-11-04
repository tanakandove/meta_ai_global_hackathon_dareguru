"""Reconcile against the real database with a fake model.

The fake judges by taking the top-ranked candidate, so these tests exercise matching, project
scoping, merging, and the policy that turns judgments into records and review items.
"""
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from psycopg.types.json import Jsonb

from app import db
from app.reconcile import reconcile_source

PREFIX = "rtest-"


@pytest.fixture
def world():
    """Two test projects with look-alike tasks, one source, and a helper to add events."""
    with db.connect() as conn:
        mid = conn.execute("INSERT INTO projects (name) VALUES (%s) RETURNING id", (PREFIX + "MidTown",)).fetchone()["id"]
        harbor = conn.execute("INSERT INTO projects (name) VALUES (%s) RETURNING id", (PREFIX + "Harbor",)).fetchone()["id"]
        for pid, title, owner, project, due, status in [
            (PREFIX + "specs", "Send revised hardware specifications for facilities", "Dana", mid, date(2026, 11, 6), "In-progress"),
            (PREFIX + "cabling", "Finalize cabling and network-drop plan", "Priya", mid, date(2026, 11, 3), "Not-started"),
            (PREFIX + "nuc", "Prepare NUC configuration and deployment checklist", "Alex", mid, date(2026, 11, 4), "Not-started"),
            (PREFIX + "mount", "Confirm display mounting locations and measurements", "Marcus", mid, date(2026, 11, 2), "In-progress"),
            (PREFIX + "h-specs", "Send revised hardware specifications for kiosks", "Dana", harbor, date(2026, 11, 9), "Not-started"),
        ]:
            conn.execute(
                """INSERT INTO notion_tasks (notion_page_id, title, owner_name, due_date, status, project_id, notion_last_edited)
                   VALUES (%s, %s, %s, %s, %s, %s, now())""",
                (pid, title, owner, due, status, project),
            )
        sid = conn.execute(
            """INSERT INTO sources (kind, occurred_at, timezone, raw_text, content_hash, extraction)
               VALUES ('audio', %s, 'America/New_York', 'x', %s, %s) RETURNING id""",
            (datetime(2026, 10, 31, 10, tzinfo=ZoneInfo("America/New_York")), PREFIX + "src",
             Jsonb({"meeting_project": PREFIX + "MidTown", "speakers": []})),
        ).fetchone()["id"]

    def add(type_, deliverable, owner=None, speaker="A", name=None, text="", dl_date=None, basis="none", turn=None):
        with db.connect() as conn:
            return conn.execute(
                """INSERT INTO events (source_id, type, speaker_label, speaker_name, owner_name, deliverable, deadline_text,
                       deadline_date, deadline_basis, excerpt, turn_index) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (sid, type_, speaker, name, owner, deliverable, text, dl_date, basis, f"said {deliverable}", turn),
            ).fetchone()["id"]

    yield {"sid": sid, "add": add, "mid": mid, "harbor": harbor}

    with db.connect() as conn:
        conn.execute("DELETE FROM review_items WHERE source_id = %s", (sid,))
        cids = [r["id"] for r in conn.execute("SELECT id FROM commitments WHERE project_id = ANY(%s)", ([mid, harbor],))]
        conn.execute("DELETE FROM audit_log WHERE entity_type = 'commitment' AND entity_id = ANY(%s)", (cids,))
        conn.execute("DELETE FROM commitments WHERE id = ANY(%s)", (cids,))
        conn.execute("DELETE FROM sources WHERE id = %s", (sid,))
        conn.execute("DELETE FROM notion_tasks WHERE notion_page_id LIKE %s", (PREFIX + "%",))
        conn.execute("DELETE FROM projects WHERE name LIKE %s", (PREFIX + "%",))


def fake_model(prompts: list[str]):
    """Judge each event by its top-ranked candidate."""
    def call(system, user, name, schema):
        prompts.append(user)
        decisions = []
        for block in user.split("\n\n")[1:]:
            eid = int(re.match(r"event (\d+) \[(\w+)\]", block)[1])
            first = re.search(r"id=(\S+) \| .+? \| owner .+? \| match ([0-9.]+)", block)
            cand, score = (first[1], float(first[2])) if first else ("", 0.0)
            etype = re.match(r"event \d+ \[(\w+)\]", block)[1]
            if name == "Pass1":
                link = bool(cand) and score >= 0.6
                decisions.append({"event_id": eid, "action": "link" if link else "new_task", "notion_page_id": cand if link else "",
                                  "confidence": "high" if link else "medium", "rationale": "top candidate"})
            else:
                rel = {"completion_claim": "completion", "progress": "progress", "blocker": "blocker"}[etype] if cand else "unrelated"
                decisions.append({"event_id": eid, "relation": rel, "target": cand, "confidence": "high", "rationale": "top candidate"})
        return {"decisions": decisions}
    return call


def rows(sql, *args):
    with db.connect() as conn:
        return conn.execute(sql, args).fetchall()


def test_full_meeting_outcome(world):
    add = world["add"]
    add("commitment", "revised hardware specifications", owner="Dana", name="Dana", text="by Friday", dl_date=date(2026, 11, 6), basis="explicit")
    add("request", "NUC configuration checklist", owner="Alex", speaker="A", name="Dana", text="before next Wednesday", dl_date=date(2026, 11, 4), basis="inferred")
    add("commitment", "NUC configuration checklist", owner="Alex", speaker="E", name="Alex", text="by then", dl_date=date(2026, 11, 4), basis="inferred")
    add("commitment", "order spare patch cables", owner="Priya", name="Priya")
    add("suggestion", "contact the vendor about rack mounts", speaker="B")
    add("completion_claim", "cabling plan", speaker="C")
    add("progress", "display mounting location measurements", speaker="D")
    add("blocker", "second floor access for display mounting measurements", speaker="D")

    prompts: list[str] = []
    stats = reconcile_source(world["sid"], call=fake_model(prompts))

    # Project scoping: look-alike tasks in the other project were never shown to the model.
    assert prompts and not any(PREFIX + "h-specs" in p for p in prompts)

    live = {r["notion_page_id"]: r for r in rows("SELECT * FROM commitments WHERE project_id = %s", world["mid"])}
    assert live[PREFIX + "specs"]["state"] == "proposed"
    assert live[PREFIX + "nuc"]["state"] == "proposed"
    assert live[None]["deliverable"] == "order spare patch cables" and live[None]["state"] == "proposed"
    # Existing tasks that evidence touched are tracked as open; nothing was written to Notion.
    assert live[PREFIX + "cabling"]["state"] == "resolution_proposed"
    assert live[PREFIX + "mount"]["state"] == "open"

    # Request + acceptance of the same deliverable merge into one commitment with two origin events.
    nuc = live[PREFIX + "nuc"]["id"]
    assert len(rows("SELECT 1 FROM commitments WHERE notion_page_id = %s", PREFIX + "nuc")) == 1
    assert len(rows("SELECT 1 FROM evidence WHERE commitment_id = %s AND relation = 'origin'", nuc)) == 2

    kinds = sorted(r["kind"] for r in rows("SELECT kind FROM review_items WHERE source_id = %s", world["sid"]))
    assert kinds == ["potential_completion", "suggestion", "task_match", "task_match", "task_match"]

    # Progress and blockers are evidence only: attached, never reviewed, never resolving anything.
    mount = live[PREFIX + "mount"]["id"]
    assert {r["relation"] for r in rows("SELECT relation FROM evidence WHERE commitment_id = %s", mount)} == {"progress", "blocker"}
    assert live[PREFIX + "mount"]["state"] == "open"

    # A spoken claim is stored as claimed and only proposes a resolution.
    claim = rows("SELECT strength FROM evidence WHERE commitment_id = %s AND relation = 'completion'", live[PREFIX + "cabling"]["id"])
    assert [c["strength"] for c in claim] == ["claimed"]
    assert stats["commitments"] == 3 and stats["evidence"] == 3

    assert rows("SELECT count(*) n FROM audit_log WHERE entity_type = 'commitment' AND entity_id = %s", live[PREFIX + "cabling"]["id"])[0]["n"] == 2


def test_rerun_creates_nothing_and_skips_the_model(world):
    world["add"]("commitment", "revised hardware specifications", owner="Dana")
    reconcile_source(world["sid"], call=fake_model([]))
    before = (len(rows("SELECT 1 FROM commitments WHERE project_id = %s", world["mid"])),
              len(rows("SELECT 1 FROM review_items WHERE source_id = %s", world["sid"])))

    def boom(*a):
        raise AssertionError("a processed source must not call the model")

    assert reconcile_source(world["sid"], call=boom) == {"commitments": 0, "review_items": 0, "evidence": 0}
    assert before == (len(rows("SELECT 1 FROM commitments WHERE project_id = %s", world["mid"])),
                      len(rows("SELECT 1 FROM review_items WHERE source_id = %s", world["sid"])))


def test_unknown_owner_is_flagged_with_the_tasks_owner_as_a_suggestion_not_applied(world):
    world["add"]("commitment", "revised hardware specifications", speaker="A", text="by Friday", dl_date=date(2026, 11, 6), basis="explicit")
    reconcile_source(world["sid"], call=fake_model([]))
    (item,) = rows("SELECT * FROM review_items WHERE source_id = %s", world["sid"])
    assert item["kind"] == "ambiguous_owner_or_deadline"
    assert item["proposed_action"]["suggested_owner"] == "Dana"
    assert rows("SELECT owner_name FROM commitments WHERE project_id = %s", world["mid"])[0]["owner_name"] is None


def test_deadline_that_differs_from_notion_raises_a_conflict(world):
    world["add"]("commitment", "NUC configuration checklist", owner="Alex", text="Wednesday the 11th", dl_date=date(2026, 11, 11), basis="explicit")
    reconcile_source(world["sid"], call=fake_model([]))
    assert sorted(r["kind"] for r in rows("SELECT kind FROM review_items WHERE source_id = %s", world["sid"])) == ["conflict", "task_match"]


def test_a_target_outside_the_shortlist_is_not_trusted(world):
    world["add"]("completion_claim", "cabling plan", speaker="C")

    def rogue(system, user, name, schema):
        eid = int(re.search(r"event (\d+)", user)[1])
        return {"decisions": [{"event_id": eid, "relation": "completion", "target": PREFIX + "h-specs", "confidence": "high", "rationale": "x"}]}

    reconcile_source(world["sid"], call=rogue)
    (item,) = rows("SELECT * FROM review_items WHERE source_id = %s", world["sid"])
    assert item["kind"] == "uncertain_extraction"
    assert rows("SELECT 1 FROM commitments WHERE project_id = %s", world["mid"]) == []


def test_follow_up_restating_a_tracked_commitment_is_not_duplicated(world):
    world["add"]("commitment", "revised hardware specifications", owner="Dana")
    reconcile_source(world["sid"], call=fake_model([]))
    with db.connect() as conn:
        sid2 = conn.execute(
            """INSERT INTO sources (kind, occurred_at, timezone, raw_text, content_hash, extraction)
               VALUES ('text', now(), 'America/New_York', 'x', %s, NULL) RETURNING id""", (PREFIX + "src2",)).fetchone()["id"]
        conn.execute("""INSERT INTO events (source_id, type, speaker_label, owner_name, deliverable, excerpt, project_hint)
                        VALUES (%s, 'commitment', 'A', 'Dana', 'revised hardware specifications', 'again', %s)""",
                     (sid2, PREFIX + "MidTown"))
    try:
        reconcile_source(sid2, call=fake_model([]))
        assert len(rows("SELECT 1 FROM commitments WHERE notion_page_id = %s", PREFIX + "specs")) == 1
        assert [r["kind"] for r in rows("SELECT kind FROM review_items WHERE source_id = %s", sid2)] == ["possible_duplicate"]
    finally:
        with db.connect() as conn:
            conn.execute("DELETE FROM review_items WHERE source_id = %s", (sid2,))
            conn.execute("DELETE FROM sources WHERE id = %s", (sid2,))


def test_blocker_that_does_not_name_its_task_follows_the_progress_said_in_the_same_turn(world):
    add = world["add"]
    add("progress", "display mounting location measurements", speaker="D", turn=3)
    add("blocker", "second floor access", speaker="D", turn=3)
    prompts: list[str] = []
    reconcile_source(world["sid"], call=fake_model(prompts))

    assert any("same turn also said" in p for p in prompts)
    (c,) = rows("SELECT * FROM commitments WHERE notion_page_id = %s", PREFIX + "mount")
    assert {r["relation"] for r in rows("SELECT relation FROM evidence WHERE commitment_id = %s", c["id"])} == {"progress", "blocker"}
    assert rows("SELECT 1 FROM review_items WHERE source_id = %s", world["sid"]) == []


def test_a_lone_unrelated_blocker_still_goes_to_review(world):
    world["add"]("blocker", "second floor access", speaker="D", turn=3)
    reconcile_source(world["sid"], call=fake_model([]))
    assert [r["kind"] for r in rows("SELECT kind FROM review_items WHERE source_id = %s", world["sid"])] == ["uncertain_extraction"]


def test_restating_finished_work_creates_no_card_and_asks_the_model_nothing(world):
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO notion_tasks (notion_page_id, title, owner_name, due_date, status, project_id, notion_last_edited)
               VALUES (%s, 'Pick up and deliver the digital display to the site', 'John', NULL, 'Done', %s, now())""",
            (PREFIX + "done-deliver", world["mid"]))
    world["add"]("completion_claim", "the display delivery", speaker="A", name="John")

    def boom(*a):
        raise AssertionError("a restatement of finished work needs no model call")

    assert reconcile_source(world["sid"], call=boom) == {"commitments": 0, "review_items": 0, "evidence": 0}
    assert rows("SELECT 1 FROM review_items WHERE source_id = %s", world["sid"]) == []
    assert rows("SELECT 1 FROM events WHERE source_id = %s AND reconciled_at IS NULL", world["sid"]) == []
