"""Reconcile: connect extracted events to existing project records.

Two Spark passes. Pass 1 links commitments and requests to existing tasks or proposes new ones.
Pass 2 judges completion claims, progress, blockers and contradictions against the tasks plus
the commitments pass 1 just created, so a claim made in the same meeting can still find its target.

Spark only judges. This module decides what that means: which records are created, which items
need a human, and what is never done automatically (a claim never resolves anything by itself).
"""
import re
from dataclasses import asdict
from typing import Callable, Literal
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict

from app import db, llm
from app.match import MIN_SCORE, TOP_K, Candidate, candidates_for, resolve_project, restates_finished_work, score_task

Confidence = Literal["high", "medium", "low"]
LIVE_STATES = ("proposed", "open", "resolution_proposed")
DUPLICATE_SIMILARITY = 0.7
ATTACHMENT = re.compile(r"^attachments?:\s*\S+", re.I | re.M)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Pass1Decision(_Strict):
    event_id: int
    action: Literal["link", "new_task"]
    notion_page_id: str
    confidence: Confidence
    rationale: str


class Pass1Result(_Strict):
    decisions: list[Pass1Decision]


class Pass2Decision(_Strict):
    event_id: int
    relation: Literal["completion", "progress", "blocker", "contradiction", "unrelated"]
    target: str
    confidence: Confidence
    rationale: str


class Pass2Result(_Strict):
    decisions: list[Pass2Decision]


PASS1_SYSTEM = """You link what people committed to in a meeting to the project's existing tasks.

For each event (a commitment or a request) decide:
- link: it is the same deliverable, owned by the same person, as one candidate task. Set notion_page_id to that candidate's id.
- new_task: none of the candidates is the same deliverable. Set notion_page_id to "".

Rules:
- Use only the candidate ids given for that event.
- Similar titles are not enough: the deliverable and the owner must agree.
- If the owner is unknown, judge on the deliverable alone and lower your confidence when more than one candidate fits.
- confidence is high when deliverable and owner clearly agree, medium when the deliverable agrees but the owner or details are unclear, low when unsure.
- rationale is one short sentence."""

PASS2_SYSTEM = """You decide how new statements from a meeting bear on existing work.

For each event decide its relation to one candidate:
- completion: the speaker says that candidate's work is finished or delivered.
- progress: work on it has started or advanced but is not finished.
- blocker: something is preventing it from being finished.
- contradiction: the statement conflicts with the candidate's recorded status, owner or deadline.
- unrelated: no candidate is what the statement is about.

Rules:
- target is the candidate's id exactly as given, or "" when unrelated. Use only the ids given for that event.
- Someone saying work is done is a claim, not proof. Report it as completion anyway; the system handles verification.
- Link a claim to a candidate only if it is plausibly the same deliverable.
- confidence is high, medium or low. rationale is one short sentence."""


LLMCall = Callable[[str, str, str, dict], dict]


def _q(ev: dict) -> str:
    """An excerpt for use inside a sentence: without its own end punctuation, so quoting it never doubles up."""
    return ev["excerpt"].rstrip(" .!?")


def _who(ev: dict) -> str:
    return ev["owner_name"] or ev["speaker_name"] or f"speaker {ev['speaker_label']}"


def _when(ev: dict) -> str:
    d = ev["deadline_date"]
    if d:
        return f"{d.strftime('%b')} {d.day}" + (" (inferred)" if ev["deadline_basis"] == "inferred" else "")
    return f'"{ev["deadline_text"]}" (date unclear)' if ev["deadline_text"] else ""


def _turn_mates(events: list[dict], ev: dict) -> list[dict]:
    """Other events said in the same turn. A blocker rarely names its task, but the progress update beside it does."""
    if ev["turn_index"] is None:
        return []
    return [x for x in events if x["turn_index"] == ev["turn_index"] and x["id"] != ev["id"]]


def _event_block(ev: dict, cands: list[Candidate], mates: list[dict] = ()) -> str:
    lines = [
        f"event {ev['id']} [{ev['type']}] speaker={ev['speaker_name'] or 'unknown (speaker ' + str(ev['speaker_label']) + ')'}"
        f" owner={ev['owner_name'] or 'unknown'}",
        f"  deliverable: {ev['deliverable']}",
        f'  said: "{ev["excerpt"]}"',
    ]
    if ev["deadline_text"]:
        lines.append(f"  deadline: {ev['deadline_text']} -> {ev['deadline_date'] or 'unclear'}")
    if mates:
        lines.append("  same turn also said: " + " | ".join(f'"{m["excerpt"]}"' for m in mates))
    lines.append("  candidates:")
    lines += [f"    - id={c.page_id} | {c.title} | owner {c.owner or '-'} | due {c.due or '-'} | {c.status} | match {c.score}" for c in cands] or ["    (none)"]
    return "\n".join(lines)


def _prompt(src: dict, meeting_project: str, blocks: list[str]) -> str:
    day = src["occurred_at"].astimezone(ZoneInfo(src["timezone"])).date()
    return f"Meeting date: {day.isoformat()}. Meeting project: {meeting_project or 'unknown'}.\n\n" + "\n\n".join(blocks)


def _audit(conn, entity: str, entity_id: int, from_state: str | None, to_state: str, detail: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO audit_log (entity_type, entity_id, from_state, to_state, detail) VALUES (%s, %s, %s, %s, %s)",
        (entity, entity_id, from_state, to_state, Jsonb(detail or {})),
    )


def _review(conn, kind: str, reason: str, ev: dict, commitment_id: int | None, action: dict) -> int:
    return conn.execute(
        """INSERT INTO review_items (kind, reason, source_id, event_id, commitment_id, proposed_action)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
        (kind, reason, ev["source_id"], ev["id"], commitment_id, Jsonb(action)),
    ).fetchone()["id"]


def _evidence(conn, commitment_id: int, event_id: int, relation: str, rationale: str, strength: str = "claimed") -> None:
    conn.execute(
        """INSERT INTO evidence (commitment_id, event_id, relation, strength, rationale)
           VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
        (commitment_id, event_id, relation, strength, rationale),
    )


def _strength(src: dict, relation: str) -> str:
    """A spoken or written claim is 'claimed'. An email that carries the artifact is independent evidence."""
    return "verified" if relation == "completion" and src["kind"] == "email" and ATTACHMENT.search(src["raw_text"]) else "claimed"


def _live_commitment(conn, page_id: str) -> dict | None:
    return conn.execute(
        "SELECT * FROM commitments WHERE notion_page_id = %s AND state = ANY(%s)", (page_id, list(LIVE_STATES))
    ).fetchone()


def _track_task(conn, task: dict) -> dict:
    """Start tracking an existing Notion task as an open commitment. Database only; Notion is untouched."""
    live = _live_commitment(conn, task["notion_page_id"])
    if live:
        return live
    row = conn.execute(
        """INSERT INTO commitments (state, owner_name, deliverable, deadline_date, deadline_basis, project_id, notion_page_id)
           VALUES ('open', %s, %s, %s, %s, %s, %s) RETURNING *""",
        (task["owner_name"] or None, task["title"], task["due_date"], "explicit" if task["due_date"] else "none",
         task["project_id"], task["notion_page_id"]),
    ).fetchone()
    _audit(conn, "commitment", row["id"], None, "open", {"adopted_from_notion_task": task["notion_page_id"]})
    return row


def _similar(a: str, b: str) -> float:
    return score_task(a, None, {"title": b, "owner_name": ""})


# ---- pass 1 -------------------------------------------------------------------------------------

def _apply_pass1(conn, ev: dict, dec: Pass1Decision | None, cands: list[Candidate], project_id: int | None,
                 tasks: dict[str, dict], made: list[dict], stats: dict) -> None:
    valid = {c.page_id for c in cands}
    note = dec.rationale if dec else "No decision was returned."
    confidence = dec.confidence if dec else "low"
    target = dec.notion_page_id if dec and dec.action == "link" else None
    if target is not None and target not in valid:
        target, confidence, note = None, "low", "The model chose a task outside the shortlist, so no link was made."

    existing = _live_commitment(conn, target) if target else None
    if existing and existing["id"] in {m["id"] for m in made}:
        _evidence(conn, existing["id"], ev["id"], "origin", "Same commitment restated in this meeting.")
        if not existing["owner_name"] and ev["owner_name"]:
            conn.execute("UPDATE commitments SET owner_name = %s, updated_at = now() WHERE id = %s", (ev["owner_name"], existing["id"]))
        return
    if not target:
        twin = next((m for m in made if m["notion_page_id"] is None and _similar(ev["deliverable"], m["deliverable"]) >= DUPLICATE_SIMILARITY
                     and (not ev["owner_name"] or not m["owner_name"] or ev["owner_name"] == m["owner_name"])), None)
        if twin:
            _evidence(conn, twin["id"], ev["id"], "origin", "Same commitment restated in this meeting.")
            return

    if existing:  # tracked from an earlier source: do not create a second record
        _evidence(conn, existing["id"], ev["id"], "origin", "Restated in a later source.")
        _review(conn, "possible_duplicate",
                f'{_who(ev)} restated "{ev["deliverable"]}", which is already tracked (commitment #{existing["id"]}, {existing["state"]}). '
                "It was attached to the existing record instead of creating a duplicate.",
                ev, existing["id"], {"type": "none"})
        stats["review_items"] += 1
        return

    task = tasks.get(target) if target else None
    row = conn.execute(
        """INSERT INTO commitments (state, owner_name, deliverable, deadline_date, deadline_basis, project_id, origin_event_id, notion_page_id)
           VALUES ('proposed', %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (ev["owner_name"], ev["deliverable"], ev["deadline_date"], ev["deadline_basis"],
         task["project_id"] if task else project_id, ev["id"], target),
    ).fetchone()
    made.append(row)
    stats["commitments"] += 1
    _audit(conn, "commitment", row["id"], None, "proposed", {"event_id": ev["id"]})
    _evidence(conn, row["id"], ev["id"], "origin", note)
    if target:
        score = next(c.score for c in cands if c.page_id == target)
        conn.execute(
            "INSERT INTO task_matches (event_id, notion_page_id, score, rationale) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (ev["id"], target, score, note),
        )

    issues = []
    if not ev["owner_name"]:
        issues.append("owner unknown" + (f'; the matched task is owned by {task["owner_name"]}, please confirm' if task and task["owner_name"] else ""))
    if ev["deadline_text"] and not ev["deadline_date"]:
        issues.append(f'deadline "{ev["deadline_text"]}" could not be resolved to a date')
    if ev["deadline_basis"] == "inferred" and ev["deadline_date"]:
        issues.append("deadline was inferred from a relative phrase")

    deadline = f" by {_when(ev)}" if _when(ev) else ""
    if task:
        headline = f'{_who(ev)} committed to "{ev["deliverable"]}"{deadline}. Proposed link: Notion task "{task["title"]}" ({note})'
        action = {"type": "link_task", "commitment_id": row["id"], "notion_page_id": target,
                  "suggested_owner": task["owner_name"] or None}
    else:
        headline = f'{_who(ev)} committed to "{ev["deliverable"]}"{deadline}. No existing task matches; proposing a new task.'
        action = {"type": "create_task", "commitment_id": row["id"],
                  "task": {"title": ev["deliverable"], "owner": ev["owner_name"], "due": str(ev["deadline_date"] or ""), "project_id": project_id}}
    hard = [i for i in issues if not i.startswith("deadline was inferred")]
    kind = "uncertain_extraction" if confidence == "low" else "ambiguous_owner_or_deadline" if hard else "task_match"
    reason = headline + (f" Needs attention: {'; '.join(issues)}." if issues else "")
    _review(conn, kind, reason, ev, row["id"], action)
    stats["review_items"] += 1

    if task and ev["deadline_date"] and task["due_date"] and ev["deadline_date"] != task["due_date"]:
        _review(conn, "conflict",
                f'Deadline differs for "{task["title"]}": said {_when(ev)}, Notion has {task["due_date"].strftime("%b")} {task["due_date"].day}.',
                ev, row["id"], {"type": "none"})
        stats["review_items"] += 1


# ---- pass 2 -------------------------------------------------------------------------------------

def _apply_pass2(conn, src: dict, ev: dict, dec: Pass2Decision | None, cands: list[Candidate], tasks: dict[str, dict], stats: dict) -> None:
    valid = {c.page_id for c in cands}
    relation, target, confidence = (dec.relation, dec.target, dec.confidence) if dec else ("unrelated", "", "low")
    note = dec.rationale if dec else "No decision was returned."
    if relation != "unrelated" and target not in valid:
        relation, target, confidence, note = "unrelated", "", "low", "The model chose a target outside the shortlist."
    who = _who(ev)

    if relation == "unrelated" or confidence == "low":
        _review(conn, "uncertain_extraction",
                f'{who} said "{_q(ev)}" ({ev["type"].replace("_", " ")}), but no related task could be identified with confidence. {note}',
                ev, None, {"type": "none"})
        stats["review_items"] += 1
        return

    if target.startswith("new:"):
        commitment = conn.execute("SELECT * FROM commitments WHERE id = %s", (int(target[4:]),)).fetchone()
        task = None
    else:
        task = tasks[target]
        commitment = _track_task(conn, task)
    title = task["title"] if task else commitment["deliverable"]
    strength = _strength(src, relation)
    _evidence(conn, commitment["id"], ev["id"], relation, note, strength)
    stats["evidence"] += 1

    if relation in ("progress", "blocker"):
        return  # evidence only: it changes nothing authoritative, so no human is asked

    if relation == "contradiction":
        _review(conn, "conflict", f'{who} said "{_q(ev)}", which conflicts with "{title}". {note}',
                ev, commitment["id"], {"type": "none"})
        stats["review_items"] += 1
        return

    # completion: a spoken claim proposes a resolution; a human decides
    already = conn.execute(
        "SELECT 1 FROM review_items WHERE commitment_id = %s AND kind = 'potential_completion' AND status = 'open'",
        (commitment["id"],),
    ).fetchone()
    if commitment["state"] == "open":
        conn.execute("UPDATE commitments SET state = 'resolution_proposed', updated_at = now() WHERE id = %s", (commitment["id"],))
        _audit(conn, "commitment", commitment["id"], "open", "resolution_proposed", {"event_id": ev["id"]})
    if not already:
        unconfirmed = " This commitment itself is not confirmed yet." if commitment["state"] == "proposed" else ""
        _review(conn, "potential_completion",
                (f'{who} said: "{_q(ev)}". That is a claim, not verified evidence.' if strength == "claimed"
                 else f'{who} wrote: "{_q(ev)}", and the email carries an attachment, which is independent evidence.')
                + f' Possible completion of "{title}" ({note}).{unconfirmed}',
                ev, commitment["id"],
                {"type": "resolve_task", "commitment_id": commitment["id"], "notion_page_id": commitment["notion_page_id"],
                 "evidence_event_id": ev["id"], "strength": strength})
        stats["review_items"] += 1


def _virtual_candidates(deliverable: str, owner: str | None, made: list[dict]) -> list[Candidate]:
    """Commitments created in pass 1 with no Notion task yet, offered as targets for pass 2."""
    out = []
    for m in made:
        if m["notion_page_id"] is None:
            s = score_task(deliverable, owner, {"title": m["deliverable"], "owner_name": m["owner_name"] or ""})
            if s >= MIN_SCORE:
                out.append(Candidate(f"new:{m['id']}", m["deliverable"], m["owner_name"] or "", str(m["deadline_date"] or ""), "proposed (this meeting)", round(s, 2)))
    return out


# ---- match and reconcile ------------------------------------------------------------------------

def _load(source_id: int):
    with db.connect() as conn:
        src = conn.execute("SELECT * FROM sources WHERE id = %s", (source_id,)).fetchone()
        events = conn.execute("SELECT * FROM events WHERE source_id = %s AND reconciled_at IS NULL ORDER BY id", (source_id,)).fetchall()
        projects = conn.execute("SELECT id, name FROM projects").fetchall()
        tasks = {t["notion_page_id"]: t for t in conn.execute("SELECT * FROM notion_tasks").fetchall()}
    return src, events, projects, tasks


def _about(events: list[dict], e: dict) -> str:
    """What a statement is about, including its neighbours in the same turn."""
    return " ".join([e["deliverable"]] + [m["deliverable"] for m in _turn_mates(events, e)])


def match_source(source_id: int) -> dict:
    """The graph's match node: scope each event to its project, then shortlist tasks. Plain data, so it can ride in graph state."""
    src, events, projects, tasks = _load(source_id)
    meeting_project = (src["extraction"] or {}).get("meeting_project", "")
    rows = list(tasks.values())
    project_of = {e["id"]: resolve_project(e["project_hint"], meeting_project, projects) for e in events}
    cands = lambda text, e: [asdict(c) for c in candidates_for(text, e["owner_name"], rows, project_of[e["id"]])]
    return {
        "project_of": {str(k): v for k, v in project_of.items()},
        "pass1": {str(e["id"]): cands(e["deliverable"], e) for e in events if e["type"] in ("commitment", "request")},
        "pass2": {str(e["id"]): cands(_about(events, e), e) for e in events if e["type"] in ("completion_claim", "progress", "blocker")},
        # restatements of finished work: nothing is left to do, so they get no review card
        "already_done": [e["id"] for e in events if e["type"] in ("completion_claim", "progress", "blocker")
                         and restates_finished_work(f'{e["deliverable"]} {e["excerpt"]}', e["owner_name"], rows, project_of[e["id"]])],
    }


def reconcile_source(source_id: int, call: LLMCall = llm.structured_call, matches: dict | None = None) -> dict:
    """Reconcile every unprocessed event of a source. Idempotent: processed events are skipped."""
    src, events, projects, tasks = _load(source_id)
    stats = {"commitments": 0, "review_items": 0, "evidence": 0}
    if not events:
        return stats

    m = matches or match_source(source_id)
    meeting_project = (src["extraction"] or {}).get("meeting_project", "")
    project_of = {int(k): v for k, v in m["project_of"].items()}
    made: list[dict] = []

    pass1 = [e for e in events if e["type"] in ("commitment", "request")]
    cands1 = {int(k): [Candidate(**c) for c in v] for k, v in m["pass1"].items()}
    decisions1: dict[int, Pass1Decision] = {}
    if pass1:
        res = Pass1Result.model_validate(call(PASS1_SYSTEM, _prompt(src, meeting_project, [_event_block(e, cands1[e["id"]]) for e in pass1]),
                                              "Pass1", Pass1Result.model_json_schema()))
        decisions1 = {d.event_id: d for d in res.decisions if d.event_id in cands1}
    with db.connect() as conn:
        for e in pass1:
            _apply_pass1(conn, e, decisions1.get(e["id"]), cands1[e["id"]], project_of[e["id"]], tasks, made, stats)

    finished = set(m.get("already_done", []))
    pass2 = [e for e in events if e["type"] in ("completion_claim", "progress", "blocker") and e["id"] not in finished]
    cands2 = {
        e["id"]: sorted([Candidate(**c) for c in m["pass2"][str(e["id"])]] + _virtual_candidates(_about(events, e), e["owner_name"], made),
                        key=lambda c: -c.score)[:TOP_K]
        for e in pass2
    }
    decisions2: dict[int, Pass2Decision] = {}
    if pass2:
        res2 = Pass2Result.model_validate(call(PASS2_SYSTEM, _prompt(src, meeting_project, [_event_block(e, cands2[e["id"]], _turn_mates(events, e)) for e in pass2]),
                                               "Pass2", Pass2Result.model_json_schema()))
        decisions2 = {d.event_id: d for d in res2.decisions if d.event_id in cands2}
    with db.connect() as conn:
        for e in pass2:
            _apply_pass2(conn, src, e, decisions2.get(e["id"]), cands2[e["id"]], tasks, stats)
        for e in events:
            if e["type"] == "suggestion":
                _review(conn, "suggestion",
                        f'{_who(e)} suggested: "{_q(e)}". A suggestion is not an agreed commitment, so no task was created.',
                        e, None, {"type": "none"})
                stats["review_items"] += 1
        conn.execute("UPDATE events SET reconciled_at = now() WHERE id = ANY(%s)", ([e["id"] for e in events],))
    return stats
