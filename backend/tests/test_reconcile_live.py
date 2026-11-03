"""Full extract + reconcile on the demo meeting with live Muse Spark and the synced Notion tasks.

Costs tokens, output varies, and it needs `python -m app.notion_sync` to have run against the demo
Notion workspace. Assertions cover the demo trace in docs/graph-design.md. Run: uv run pytest -m live
"""
import pytest

from app import db, extract, ingest, notion_sync, reconcile
from tests.conftest import MEETING_AT, TZ

pytestmark = pytest.mark.live
H = "test-live-reconcile"


@pytest.fixture
def meeting(meeting_turns):
    notion_sync.sync()
    sid, _ = ingest.ingest_turns(meeting_turns, content_hash=H, occurred_at=MEETING_AT, tz=TZ)
    yield sid
    with db.connect() as conn:
        cids = [r["id"] for r in conn.execute(
            """SELECT DISTINCT c.id FROM commitments c
               LEFT JOIN evidence ev ON ev.commitment_id = c.id LEFT JOIN events e ON e.id IN (ev.event_id, c.origin_event_id)
               WHERE e.source_id = %s""", (sid,))]
        conn.execute("DELETE FROM review_items WHERE source_id = %s", (sid,))
        conn.execute("DELETE FROM audit_log WHERE entity_type = 'commitment' AND entity_id = ANY(%s)", (cids,))
        conn.execute("DELETE FROM commitments WHERE id = ANY(%s)", (cids,))
        conn.execute("DELETE FROM sources WHERE id = %s", (sid,))


def rows(sql, *args):
    with db.connect() as conn:
        return conn.execute(sql, args).fetchall()


def test_demo_meeting_end_to_end(meeting):
    extract.extract_events(meeting)
    reconcile.reconcile_source(meeting)

    tracked = {r["title"]: r for r in rows(
        """SELECT c.state, t.title, p.name project FROM commitments c
           JOIN notion_tasks t ON t.notion_page_id = c.notion_page_id JOIN projects p ON p.id = t.project_id""")}

    # Everything linked to the MidTown project; the look-alike tasks in other projects were never touched.
    assert {r["project"] for r in tracked.values()} == {"MidTown Community Dashboard"}
    assert tracked["Send revised hardware specifications for facilities"]["state"] == "proposed"
    assert tracked["Prepare NUC configuration and deployment checklist"]["state"] == "proposed"

    # A spoken completion claim proposes a resolution; it never resolves anything.
    assert tracked["Finalize cabling and network-drop plan"]["state"] == "resolution_proposed"
    assert not any(r["state"] == "resolved" for r in tracked.values())

    # Progress and the blocker both land on the display mounting task as evidence.
    rel = {r["relation"] for r in rows(
        """SELECT ev.relation FROM evidence ev JOIN commitments c ON c.id = ev.commitment_id
           JOIN notion_tasks t ON t.notion_page_id = c.notion_page_id WHERE t.title LIKE 'Confirm display mounting%%'""")}
    assert {"progress", "blocker"} <= rel

    kinds = [r["kind"] for r in rows("SELECT kind FROM review_items WHERE source_id = %s", meeting)]
    assert "suggestion" in kinds and "potential_completion" in kinds

    # Alex asked then accepted: one commitment, not two.
    assert len(rows("SELECT 1 FROM commitments WHERE deliverable ILIKE '%%NUC%%'")) == 1
