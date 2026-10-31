"""Ingest: hash the raw input and dedupe before any paid call, then store the source."""
import hashlib
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from psycopg.types.json import Jsonb

from app import db
from app.transcribe import Turn, check_wav, transcribe_wav


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ingest_turns(
    turns: list[Turn], *, content_hash: str, occurred_at: datetime, tz: str,
    kind: str = "audio", audio_filename: str | None = None,
) -> tuple[int, bool]:
    """Store a diarized source. Returns (source_id, already_ingested)."""
    raw_text = "\n".join(f"[{t.speaker}] {t.text}" for t in turns)
    with db.connect() as conn:
        row = conn.execute(
            """INSERT INTO sources (kind, occurred_at, timezone, raw_text, audio_filename, turns, content_hash)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (content_hash) DO NOTHING RETURNING id""",
            (kind, occurred_at, tz, raw_text, audio_filename, Jsonb([asdict(t) for t in turns]), content_hash),
        ).fetchone()
        if row:
            return row["id"], False
        existing = conn.execute("SELECT id FROM sources WHERE content_hash = %s", (content_hash,)).fetchone()
        return existing["id"], True


def ingest_audio(path: Path, *, occurred_at: datetime, tz: str) -> tuple[int, bool]:
    """Hash the recording first so a duplicate upload never reaches Voice Transcribe."""
    check_wav(path)
    h = _hash(path.read_bytes())
    with db.connect() as conn:
        existing = conn.execute("SELECT id FROM sources WHERE content_hash = %s", (h,)).fetchone()
    if existing:
        return existing["id"], True
    return ingest_turns(
        transcribe_wav(path), content_hash=h, occurred_at=occurred_at, tz=tz, audio_filename=path.name,
    )
