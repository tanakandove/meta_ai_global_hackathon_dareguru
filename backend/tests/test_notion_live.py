"""The real Notion gateway and the full graph with real audio, Spark and Notion reads.

Costs tokens and writes to the real workspace, so it only creates a temporary task and archives it.
Demo tasks are never modified. Run: uv run pytest -m live
"""
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app import db, notion_api, runner
from app.notion_api import DONE_STATUS, NotionGateway
from tests.conftest import DEMO

pytestmark = pytest.mark.live


def test_gateway_create_find_and_mark_done_is_idempotent():
    gw = NotionGateway()
    marker = "pp-test" + uuid.uuid4().hex[:6]
    with db.connect() as conn:
        project = conn.execute("SELECT name, notion_page_id FROM projects WHERE name = 'University Circle Dashboard'").fetchone()
    page_id = None
    try:
        page_id = gw.create_task(marker=marker, title="TEMP: ProjectPulse gateway test", owner="Adam", due="2026-11-14",
                                 project_name=project["name"], project_page_id=project["notion_page_id"], evidence="Created by a test")
        task = gw.get_task(page_id)
        assert task.title == "TEMP: ProjectPulse gateway test" and task.status == "Not-started"
        assert task.project_page_id == project["notion_page_id"]  # the relation was written

        assert gw.find_by_marker(marker) == page_id               # a retry can find its own earlier write
        assert gw.find_by_marker("pp-nosuchmarker") is None

        gw.mark_done(page_id, marker=marker, note="Resolved by a test")
        gw.mark_done(page_id, marker=marker, note="Resolved by a test")  # applying twice changes nothing more
        done = gw.get_task(page_id)
        assert done.status == DONE_STATUS and done.evidence.count(f"[{marker}]") == 1
    finally:
        if page_id:
            notion_api.client().pages.update(page_id, in_trash=True)
    assert gw.get_task(page_id) is None  # archived pages read as gone


def test_full_run_with_real_audio_pauses_for_review_without_writing():
    when = datetime(2026, 11, 13, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    before = {t.page_id: (t.status, t.evidence) for t in notion_api.fetch_tasks()}
    run = runner.start_run("audio", audio_path=DEMO / "meeting.wav", occurred_at=when, tz="America/New_York")
    try:
        assert run["status"] == "waiting_for_review" and run["current_node"] == "human_review"
        with db.connect() as conn:
            kinds = [r["kind"] for r in conn.execute("SELECT kind FROM review_items WHERE source_id = %s", (run["source_id"],))]
        assert "potential_completion" in kinds
        assert before == {t.page_id: (t.status, t.evidence) for t in notion_api.fetch_tasks()}  # nothing written before a decision
    finally:
        with db.connect() as conn:
            sid = run["source_id"]
            cids = [r["id"] for r in conn.execute(
                """SELECT DISTINCT c.id FROM commitments c LEFT JOIN evidence ev ON ev.commitment_id = c.id
                   LEFT JOIN events e ON e.id IN (ev.event_id, c.origin_event_id) WHERE e.source_id = %s""", (sid,))]
            conn.execute("DELETE FROM decisions WHERE review_item_id IN (SELECT id FROM review_items WHERE source_id = %s)", (sid,))
            conn.execute("DELETE FROM review_items WHERE source_id = %s", (sid,))
            conn.execute("DELETE FROM audit_log WHERE entity_type = 'commitment' AND entity_id = ANY(%s)", (cids,))
            conn.execute("DELETE FROM commitments WHERE id = ANY(%s)", (cids,))
            conn.execute("DELETE FROM runs WHERE thread_id = %s", (run["thread_id"],))
            conn.execute("DELETE FROM sources WHERE id = %s", (sid,))
        with runner.get_graph().checkpointer.conn.connection() as c:
            for tbl in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                c.execute(f"DELETE FROM {tbl} WHERE thread_id = %s", (run["thread_id"],))
