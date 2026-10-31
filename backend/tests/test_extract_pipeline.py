"""Extract-and-store against the real database with a fake model."""
from app import db, extract, ingest
from tests.conftest import MEETING_AT, TZ


def fake_call(system, user, name, schema):
    assert "Meeting date: 2026-10-31 (Saturday)" in user
    assert "[turn 0] speaker A:" in user
    return {
        "meeting_project": "MidTown Community Dashboard",
        "speakers": [{"label": "A", "name": "Dana", "confidence": "high", "basis": "opens the meeting"}],
        "events": [{
            "type": "commitment", "turn_index": 0, "owner": "Dana", "deliverable": "revised hardware specifications",
            "project_hint": "", "deadline_text": "by Friday", "deadline_date": "2026-11-06",
            "deadline_basis": "explicit", "excerpt": "I'll send the revised hardware specifications to facilities by Friday",
        }],
    }


def test_extract_stores_events_and_is_idempotent(meeting_turns, clean_source):
    h = "test-extract-pipeline"
    clean_source.append(h)
    source_id, dup = ingest.ingest_turns(meeting_turns, content_hash=h, occurred_at=MEETING_AT, tz=TZ)
    assert not dup

    first = extract.extract_events(source_id, call=fake_call)
    assert len(first) == 1 and first[0]["excerpt_verified"]
    assert str(first[0]["deadline_date"]) == "2026-11-06"

    def boom(*a):
        raise AssertionError("second run must not call the model")

    again = extract.extract_events(source_id, call=boom)
    assert [e["id"] for e in again] == [e["id"] for e in first]

    with db.connect() as conn:
        assert conn.execute("SELECT count(*) n FROM events WHERE source_id = %s", (source_id,)).fetchone()["n"] == 1
        assert conn.execute("SELECT extraction FROM sources WHERE id = %s", (source_id,)).fetchone()["extraction"]["meeting_project"]


def test_duplicate_source_is_detected_by_hash(meeting_turns, clean_source):
    h = "test-dup-source"
    clean_source.append(h)
    a, dup_a = ingest.ingest_turns(meeting_turns, content_hash=h, occurred_at=MEETING_AT, tz=TZ)
    b, dup_b = ingest.ingest_turns(meeting_turns, content_hash=h, occurred_at=MEETING_AT, tz=TZ)
    assert (a == b) and not dup_a and dup_b
