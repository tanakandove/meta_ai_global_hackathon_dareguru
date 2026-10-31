"""Extract: turn a diarized transcript into typed events.

Muse Spark interprets. This module validates what comes back instead of trusting it:
excerpts must appear verbatim in the transcript, and deadlines must be plausible.
"""
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Literal
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict

from app import db, llm
from app.transcribe import Turn

EventType = Literal["commitment", "request", "decision", "progress", "completion_claim", "blocker", "suggestion"]
Basis = Literal["explicit", "inferred", "none"]
Confidence = Literal["high", "medium", "low"]

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MAX_DEADLINE_DAYS = 366


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Speaker(_Strict):
    label: str
    name: str
    confidence: Confidence
    basis: str


class RawEvent(_Strict):
    type: EventType
    turn_index: int
    owner: str
    deliverable: str
    project_hint: str
    deadline_text: str
    deadline_date: str
    deadline_basis: Basis
    excerpt: str


class Extraction(_Strict):
    meeting_project: str
    speakers: list[Speaker]
    events: list[RawEvent]


@dataclass(frozen=True)
class ProjectInfo:
    name: str
    aliases: list[str]
    client: str


@dataclass(frozen=True)
class Roster:
    people: list[str]
    projects: list[ProjectInfo]


SYSTEM_PROMPT = """You analyze meeting transcripts for a project team and extract structured events.

Event types:
- commitment: a person agrees or promises to do something ("I'll send...", "I'll have it done by then").
- request: a person asks someone else to do something.
- decision: the group settles on something.
- progress: work has started or advanced but is not complete.
- completion_claim: someone says they already finished or delivered something.
- blocker: something prevents work from continuing.
- suggestion: an idea or possibility nobody has agreed to ("we could consider..."). A suggestion is NOT a commitment.

Rules:
- One event per distinct statement. A turn may hold several. Ignore small talk.
- excerpt is copied verbatim from the transcript: a contiguous span from a single turn. turn_index is that turn's index.
- owner is the person responsible. For a commitment or completion_claim it is usually the speaker. For a request it is the person asked; if that person accepts in a later turn, also emit a commitment for them. For a suggestion, decision or blocker, the person it concerns, or "" if unknown.
- Speakers are labeled A, B, C... Infer each label's real name from the conversation (someone is addressed by name and then answers; someone introduces themselves) and from the known people list. Give a name only when the conversation supports it; otherwise name "" with confidence "low". Spell names exactly as in the known people list when one matches. basis is one short sentence on how you know.
- deliverable is a short noun phrase for what is owed or discussed.
- meeting_project is the exact name of the project the meeting is about, chosen from the project list using names, aliases and client; "unknown" if unclear. project_hint is an exact project name only when that event is clearly about a different project than the meeting; otherwise "".
- Dates resolve against the meeting date. deadline_text is the phrase as spoken, "" if none. deadline_date is YYYY-MM-DD, or "" if none. deadline_basis is "explicit" for a calendar date or a plain weekday/today/tomorrow, "inferred" when you had to interpret (for example "next Wednesday", "end of the week", "before next Friday"), and "none" when there is no deadline. A plain weekday means the next such day after the meeting date.
- Only commitments and requests normally have deadlines."""


def build_user_prompt(turns: list[Turn], roster: Roster, occurred_on: date, tz: str) -> str:
    projects = "\n".join(
        f"- {p.name} | aliases: {', '.join(p.aliases) or 'none'} | client: {p.client or 'none'}" for p in roster.projects
    )
    lines = "\n".join(f"[turn {t.index}] speaker {t.speaker}: {t.text}" for t in turns)
    return (
        f"Meeting date: {occurred_on.isoformat()} ({WEEKDAYS[occurred_on.weekday()].capitalize()}), timezone {tz}.\n\n"
        f"Known people: {', '.join(roster.people) or 'none'}\n\n"
        f"Projects:\n{projects or '- none'}\n\n"
        f"Transcript:\n{lines}"
    )


def _norm(s: str) -> str:
    """Lowercase, strip punctuation, collapse stutters ('quick quick' -> 'quick') so ASR repeats don't fail matching."""
    out: list[str] = []
    for w in re.sub(r"[^a-z0-9' ]+", " ", s.lower()).split():
        if not out or out[-1] != w:
            out.append(w)
    return " ".join(out)


def locate_excerpt(excerpt: str, turns: list[Turn], claimed: int) -> int | None:
    """Return the index of the turn containing the excerpt (preferring the claimed one), or None."""
    needle = _norm(excerpt)
    if not needle:
        return None
    ordered = sorted(turns, key=lambda t: t.index != claimed)
    return next((t.index for t in ordered if needle in _norm(t.text)), None)


def check_deadline(text: str, iso: str, basis: str, occurred_on: date) -> tuple[date | None, str]:
    """Return (date, basis). An implausible date is dropped (date None, basis 'none'); the spoken text is kept elsewhere."""
    if not iso:
        return None, "none"
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return None, "none"
    if d < occurred_on or d > occurred_on + timedelta(days=MAX_DEADLINE_DAYS):
        return None, "none"
    named = {w for w in WEEKDAYS if re.search(rf"\b{w}\b", text.lower())}
    if len(named) == 1 and WEEKDAYS[d.weekday()] not in named:
        return None, "none"
    return d, ("inferred" if basis == "none" else basis)


def validate_events(
    raw: Extraction, turns: list[Turn], occurred_on: date, roster: Roster
) -> list[dict]:
    """Turn the model's events into rows, correcting turn indexes and flagging what can't be verified."""
    by_index = {t.index: t for t in turns}
    names = {s.label: s.name for s in raw.speakers if s.name and s.confidence != "low"}
    people = {p.lower(): p for p in roster.people}
    rows: list[dict] = []
    for ev in raw.events:
        if not ev.deliverable.strip():
            continue
        found = locate_excerpt(ev.excerpt, turns, ev.turn_index)
        turn = by_index.get(found if found is not None else ev.turn_index)
        deadline, basis = check_deadline(ev.deadline_text, ev.deadline_date, ev.deadline_basis, occurred_on)
        owner = ev.owner.strip()
        rows.append({
            "type": ev.type,
            "speaker_label": turn.speaker if turn else None,
            "speaker_name": names.get(turn.speaker) if turn else None,
            "owner_name": people.get(owner.lower(), owner) or None,
            "deliverable": ev.deliverable.strip(),
            "deadline_text": ev.deadline_text,
            "deadline_date": deadline,
            "deadline_basis": basis,
            "excerpt": ev.excerpt,
            "turn_index": turn.index if turn else None,
            "start_ms": turn.start_ms if turn else None,
            "end_ms": turn.end_ms if turn else None,
            "excerpt_verified": found is not None,
            "project_hint": ev.project_hint or None,
        })
    return rows


def load_roster(conn) -> Roster:
    return Roster(
        people=[r["name"] for r in conn.execute("SELECT name FROM people ORDER BY name")],
        projects=[
            ProjectInfo(r["name"], r["aliases"], r["client"])
            for r in conn.execute("SELECT name, aliases, client FROM projects ORDER BY name")
        ],
    )


LLMCall = Callable[[str, str, str, dict], dict]


def extract_events(source_id: int, call: LLMCall = llm.structured_call) -> list[dict]:
    """Extract and store events for a source. Idempotent: a source already extracted is returned as is."""
    with db.connect() as conn:
        existing = conn.execute("SELECT * FROM events WHERE source_id = %s ORDER BY id", (source_id,)).fetchall()
        if existing:
            return existing
        src = conn.execute("SELECT * FROM sources WHERE id = %s", (source_id,)).fetchone()
        roster = load_roster(conn)

    turns = [Turn(**t) for t in src["turns"]]
    occurred_on = src["occurred_at"].astimezone(ZoneInfo(src["timezone"])).date()
    result = call(
        SYSTEM_PROMPT,
        build_user_prompt(turns, roster, occurred_on, src["timezone"]),
        "Extraction",
        Extraction.model_json_schema(),
    )
    extraction = Extraction.model_validate(result)
    rows = validate_events(extraction, turns, occurred_on, roster)

    with db.connect() as conn:
        conn.execute(
            "UPDATE sources SET extraction = %s WHERE id = %s",
            (Jsonb({"meeting_project": extraction.meeting_project, "speakers": [s.model_dump() for s in extraction.speakers]}), source_id),
        )
        return [
            conn.execute(
                """INSERT INTO events (source_id, type, speaker_label, speaker_name, owner_name, deliverable,
                       deadline_text, deadline_date, deadline_basis, excerpt, turn_index, start_ms, end_ms,
                       excerpt_verified, project_hint)
                   VALUES (%(source_id)s, %(type)s, %(speaker_label)s, %(speaker_name)s, %(owner_name)s,
                       %(deliverable)s, %(deadline_text)s, %(deadline_date)s, %(deadline_basis)s, %(excerpt)s,
                       %(turn_index)s, %(start_ms)s, %(end_ms)s, %(excerpt_verified)s, %(project_hint)s)
                   RETURNING *""",
                {**row, "source_id": source_id},
            ).fetchone()
            for row in rows
        ]
