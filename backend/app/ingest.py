"""Ingest: hash the raw input and dedupe before any paid call, then store the source.

Audio is registered first (hash, dedupe, placeholder row) and its turns are attached after
transcription, so a duplicate upload never reaches Voice Transcribe.
"""
import hashlib
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from psycopg.types.json import Jsonb

from app import db
from app.transcribe import Turn, check_wav

_NAMED_LINE = re.compile(r"^([A-Z][\w .'-]{0,40}):\s+(.+)$")


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ingest_turns(
    turns: list[Turn], *, content_hash: str, occurred_at: datetime, tz: str,
    kind: str = "audio", audio_filename: str | None = None, raw_text: str | None = None,
) -> tuple[int, bool]:
    """Store a diarized or parsed source. Returns (source_id, already_ingested)."""
    raw = raw_text if raw_text is not None else "\n".join(f"[{t.speaker}] {t.text}" for t in turns)
    with db.connect() as conn:
        row = conn.execute(
            """INSERT INTO sources (kind, occurred_at, timezone, raw_text, audio_filename, turns, content_hash)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (content_hash) DO NOTHING RETURNING id""",
            (kind, occurred_at, tz, raw, audio_filename, Jsonb([asdict(t) for t in turns]), content_hash),
        ).fetchone()
        if row:
            return row["id"], False
        existing = conn.execute("SELECT id FROM sources WHERE content_hash = %s", (content_hash,)).fetchone()
        return existing["id"], True


def register_audio(path: Path, *, occurred_at: datetime, tz: str) -> tuple[int, bool]:
    """Hash and dedupe a recording before transcription.

    A source whose transcription never finished (no turns yet) is not a duplicate: the run continues with it.
    """
    check_wav(path)
    h = _hash(path.read_bytes())
    with db.connect() as conn:
        row = conn.execute(
            """INSERT INTO sources (kind, occurred_at, timezone, raw_text, audio_filename, content_hash)
               VALUES ('audio', %s, %s, '', %s, %s) ON CONFLICT (content_hash) DO NOTHING RETURNING id""",
            (occurred_at, tz, path.name, h),
        ).fetchone()
        if row:
            return row["id"], False
        existing = conn.execute("SELECT id, turns FROM sources WHERE content_hash = %s", (h,)).fetchone()
        return existing["id"], existing["turns"] is not None


def attach_turns(source_id: int, turns: list[Turn]) -> None:
    with db.connect() as conn:
        conn.execute(
            "UPDATE sources SET turns = %s, raw_text = %s WHERE id = %s",
            (Jsonb([asdict(t) for t in turns]), "\n".join(f"[{t.speaker}] {t.text}" for t in turns), source_id),
        )


def parse_text_turns(text: str, kind: str) -> list[Turn]:
    """Turn pasted text into turns. 'Name: words' lines become named turns; an email becomes one turn from its sender."""
    if kind == "email":
        head, _, body = text.partition("\n\n")
        sender = re.search(r"^From:\s*(.+)$", head, re.M | re.I)
        subject = re.search(r"^Subject:\s*(.+)$", head, re.M | re.I)
        who = re.sub(r"\s*<.*?>", "", sender.group(1)).strip() if sender else "sender"
        words = (f"Subject: {subject.group(1).strip()}. " if subject else "") + " ".join(body.split())
        return [Turn(0, who, words, 0, 0)]
    turns = []
    for line in filter(None, (l.strip() for l in text.splitlines())):
        m = _NAMED_LINE.match(line)
        turns.append(Turn(len(turns), m.group(1) if m else "speaker", m.group(2) if m else line, 0, 0))
    return turns


def ingest_text(text: str, *, kind: str, occurred_at: datetime, tz: str) -> tuple[int, bool]:
    """Ingest a pasted transcript, email or text update. No transcription step."""
    turns = parse_text_turns(text, kind)
    return ingest_turns(turns, content_hash=_hash(text.encode()), occurred_at=occurred_at, tz=tz, kind=kind, raw_text=text)
