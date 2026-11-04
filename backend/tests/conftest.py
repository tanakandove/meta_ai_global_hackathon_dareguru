import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app import db
from app.transcribe import Turn

FIXTURES = Path(__file__).resolve().parent / "fixtures"      # test-only data
DEMO = Path(__file__).resolve().parents[2] / "fixtures"     # the demo recording and texts
MEETING_AT = datetime(2026, 10, 31, 10, 0, tzinfo=ZoneInfo("America/New_York"))  # a Saturday
TZ = "America/New_York"


@pytest.fixture(scope="session")
def meeting_turns() -> list[Turn]:
    return [Turn(**t) for t in json.loads((FIXTURES / "sample_meeting.turns.json").read_text())]


@pytest.fixture
def clean_source():
    """Yield a function that registers a content hash to delete after the test."""
    hashes: list[str] = []
    yield hashes
    with db.connect() as conn:
        for h in hashes:
            conn.execute("DELETE FROM sources WHERE content_hash = %s", (h,))


@pytest.fixture(autouse=True)
def no_queue_wait(monkeypatch):
    """Tests share a database (and the 'run-' id namespace) with real runs; a real run waiting for review must not
    stall them. The ordering tests in test_ordering.py put the real check back."""
    monkeypatch.setattr("app.graph.unfinished_earlier_runs", lambda thread_id: 0)
