import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app import db
from app.transcribe import Turn

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
MEETING_AT = datetime(2026, 10, 31, 10, 0, tzinfo=ZoneInfo("America/New_York"))  # a Saturday
TZ = "America/New_York"


@pytest.fixture(scope="session")
def meeting_turns() -> list[Turn]:
    return [Turn(**t) for t in json.loads((FIXTURES / "meeting.turns.json").read_text())]


@pytest.fixture
def clean_source():
    """Yield a function that registers a content hash to delete after the test."""
    hashes: list[str] = []
    yield hashes
    with db.connect() as conn:
        for h in hashes:
            conn.execute("DELETE FROM sources WHERE content_hash = %s", (h,))
