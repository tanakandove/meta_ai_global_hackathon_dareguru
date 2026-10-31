"""Run extraction against live Muse Spark on the demo meeting.

Costs tokens and output varies between runs, so assertions cover only what the
transcript makes unambiguous. Run with: uv run pytest -m live
"""
import pytest

from app import extract, ingest
from tests.conftest import MEETING_AT, TZ

pytestmark = pytest.mark.live


def test_demo_meeting_extraction(meeting_turns, clean_source):
    h = "test-live-extract"
    clean_source.append(h)
    source_id, _ = ingest.ingest_turns(meeting_turns, content_hash=h, occurred_at=MEETING_AT, tz=TZ)
    events = extract.extract_events(source_id)

    types = {e["type"] for e in events}
    assert {"commitment", "request", "suggestion", "completion_claim", "progress", "blocker"} <= types

    # A suggestion must never be typed as a commitment.
    vendor = [e for e in events if "vendor" in e["deliverable"].lower() or "rack" in e["deliverable"].lower()]
    assert vendor and all(e["type"] == "suggestion" for e in vendor)

    # Every excerpt is found verbatim in a real turn.
    assert all(e["excerpt_verified"] for e in events)

    specs = next(e for e in events if e["type"] == "commitment" and "specification" in e["deliverable"].lower())
    assert str(specs["deadline_date"]) == "2026-11-06" and specs["deadline_basis"] == "explicit"

    # Alex is only identifiable from context: addressed by name, then speaker E answers.
    alex = next(e for e in events if e["type"] == "commitment" and "NUC" in e["deliverable"])
    assert alex["speaker_name"] == "Alex" and alex["owner_name"] == "Alex"

    from app import db
    with db.connect() as conn:
        meeting = conn.execute("SELECT extraction FROM sources WHERE id = %s", (source_id,)).fetchone()["extraction"]
    assert meeting["meeting_project"] == "MidTown Community Dashboard"
