"""Read models for the UI: what each screen needs, shaped so the frontend does no business logic.

Review Inbox:   inbox(), run_detail()
Follow-Up Center: followups(), commitment_detail()
"""
from datetime import date, datetime, timedelta, timezone

from app import actions, db

UPCOMING_DAYS = 7
RECENTLY_RESOLVED_DAYS = 14
LIVE = ("proposed", "open", "resolution_proposed")


def notion_url(page_id: str | None) -> str | None:
    return f"https://app.notion.com/p/{page_id.replace('-', '')}" if page_id and "-" in page_id else None


def _day(d: date | None) -> str | None:
    return d.isoformat() if d else None


def people() -> list[str]:
    with db.connect() as conn:
        return [r["name"] for r in conn.execute("SELECT name FROM people ORDER BY name")]


def projects() -> list[dict]:
    with db.connect() as conn:
        return [{"id": r["id"], "name": r["name"]} for r in conn.execute("SELECT id, name FROM projects ORDER BY name")]


# ---- review inbox -------------------------------------------------------------------------------

def _source_summary(src: dict) -> dict:
    ext = src["extraction"] or {}
    return {
        "id": src["id"], "kind": src["kind"], "occurred_at": src["occurred_at"].isoformat(), "timezone": src["timezone"],
        "filename": src["audio_filename"], "meeting_project": ext.get("meeting_project"),
        "speakers": ext.get("speakers", []),
    }


def _item(conn, r: dict, tasks: dict[str, dict]) -> dict:
    action = r["proposed_action"] or {}
    ev = conn.execute("SELECT * FROM events WHERE id = %s", (r["event_id"],)).fetchone() if r["event_id"] else None
    c = conn.execute("SELECT * FROM commitments WHERE id = %s", (r["commitment_id"],)).fetchone() if r["commitment_id"] else None
    page = action.get("notion_page_id") or (c or {}).get("notion_page_id")
    task = tasks.get(page) if page else None
    alternatives = []
    if action.get("type") == "link_task":
        project = (task or {}).get("project_id") or (c or {}).get("project_id")
        alternatives = [
            {"page_id": t["notion_page_id"], "title": t["title"], "owner": t["owner_name"], "due": _day(t["due_date"])}
            for t in tasks.values() if t["project_id"] == project and t["status"].lower() not in ("done", "completed")
        ]
    return {
        "id": r["id"], "kind": r["kind"], "reason": r["reason"], "status": r["status"],
        "action": {k: v for k, v in action.items() if k != "edits"},
        "decision": {"action": r["decision"], "edits": r["edits"] or {}} if r.get("decision") else None,
        "locked": r["locked_at"] is not None,
        "event": {
            "type": ev["type"], "excerpt": ev["excerpt"], "speaker": ev["speaker_name"] or f"speaker {ev['speaker_label']}",
            "owner": ev["owner_name"], "deliverable": ev["deliverable"], "deadline_text": ev["deadline_text"],
            "deadline_date": _day(ev["deadline_date"]), "deadline_basis": ev["deadline_basis"],
            "start_ms": ev["start_ms"], "verified_quote": ev["excerpt_verified"],
        } if ev else None,
        "commitment": {"id": c["id"], "state": c["state"], "owner": c["owner_name"], "deliverable": c["deliverable"]} if c else None,
        "task": {"page_id": page, "title": task["title"], "owner": task["owner_name"], "due": _day(task["due_date"]),
                 "status": task["status"], "url": notion_url(page)} if task else None,
        "alternatives": alternatives,
    }


def run_detail(thread_id: str) -> dict | None:
    from app import runner
    run = runner.get_run(thread_id)
    if not run:
        return None
    out = {**{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in run.items()}, "source": None, "items": []}
    if run["source_id"] is None:
        return out
    with db.connect() as conn:
        src = conn.execute("SELECT * FROM sources WHERE id = %s", (run["source_id"],)).fetchone()
        tasks = {t["notion_page_id"]: t for t in conn.execute("SELECT * FROM notion_tasks")}
        rows = conn.execute(
            """SELECT r.*, d.action AS decision, d.edits AS edits FROM review_items r
               LEFT JOIN decisions d ON d.review_item_id = r.id WHERE r.source_id = %s ORDER BY r.id""", (run["source_id"],)).fetchall()
        out["source"] = _source_summary(src)
        out["items"] = [_item(conn, r, tasks) for r in rows]
    return out


def inbox() -> list[dict]:
    """Every run that is paused for a human, with its items."""
    from app import runner
    return [d for r in runner.list_runs(200) if r["status"] == "waiting_for_review" and (d := run_detail(r["thread_id"]))]


# ---- follow-up center ---------------------------------------------------------------------------

def followups(today: date | None = None) -> dict:
    """Confirmed outstanding work, with flags the UI uses to group it. Resolved items are limited to recent ones."""
    today = today or date.today()
    cutoff = datetime.now(timezone.utc) - timedelta(days=RECENTLY_RESOLVED_DAYS)
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT c.*, p.name AS project, t.title AS task_title, t.status AS task_status,
                      oe.type AS origin_type,
                      EXISTS (SELECT 1 FROM evidence e WHERE e.commitment_id = c.id AND e.relation = 'blocker') AS has_blocker,
                      (SELECT count(*) FROM evidence e WHERE e.commitment_id = c.id) AS evidence_count
               FROM commitments c LEFT JOIN projects p ON p.id = c.project_id
               LEFT JOIN notion_tasks t ON t.notion_page_id = c.notion_page_id
               LEFT JOIN events oe ON oe.id = c.origin_event_id
               WHERE c.state IN ('proposed', 'open', 'resolution_proposed') OR (c.state = 'resolved' AND c.updated_at > %s)
               ORDER BY c.deadline_date NULLS LAST, c.id""", (cutoff,)).fetchall()
    items = []
    for r in rows:
        due = r["deadline_date"]
        live = r["state"] in LIVE
        items.append({
            "id": r["id"], "state": r["state"], "title": r["task_title"] or r["deliverable"], "deliverable": r["deliverable"],
            "owner": r["owner_name"], "project": r["project"], "deadline": _day(due), "deadline_basis": r["deadline_basis"], "task_title": r["task_title"], "task_status": r["task_status"],
            "notion_url": notion_url(r["notion_page_id"]), "evidence_count": r["evidence_count"],
            "is_blocked": bool(r["has_blocker"]) and r["state"] == "open",
            "is_request": r["origin_type"] == "request" and r["state"] in ("proposed", "open"),
            "is_overdue": bool(live and due and due < today),
            "is_upcoming": bool(live and due and today <= due <= today + timedelta(days=UPCOMING_DAYS)),
            "needs_approval": r["state"] in ("proposed", "resolution_proposed"),
            "updated_at": r["updated_at"].isoformat(),
        })
    return {"as_of": today.isoformat(), "commitments": items}


def commitment_detail(commitment_id: int) -> dict | None:
    with db.connect() as conn:
        c = conn.execute(
            """SELECT c.*, p.name AS project FROM commitments c LEFT JOIN projects p ON p.id = c.project_id WHERE c.id = %s""",
            (commitment_id,)).fetchone()
        if not c:
            return None
        task = conn.execute("SELECT * FROM notion_tasks WHERE notion_page_id = %s", (c["notion_page_id"],)).fetchone()
        origin = conn.execute(
            """SELECT e.*, s.kind AS source_kind, s.occurred_at, s.audio_filename FROM events e JOIN sources s ON s.id = e.source_id
               WHERE e.id = %s""", (c["origin_event_id"],)).fetchone() if c["origin_event_id"] else None
        evidence = conn.execute(
            """SELECT ev.relation, ev.strength, ev.rationale, e.type, e.excerpt, e.speaker_name, e.speaker_label,
                      s.kind AS source_kind, s.occurred_at
               FROM evidence ev JOIN events e ON e.id = ev.event_id JOIN sources s ON s.id = e.source_id
               WHERE ev.commitment_id = %s ORDER BY s.occurred_at, ev.id""", (commitment_id,)).fetchall()
        history = conn.execute(
            "SELECT from_state, to_state, detail, at FROM audit_log WHERE entity_type = 'commitment' AND entity_id = %s ORDER BY at, id",
            (commitment_id,)).fetchall()
        open_items = conn.execute(
            "SELECT id, kind FROM review_items WHERE commitment_id = %s AND status = 'open' ORDER BY id", (commitment_id,)).fetchall()
    who = lambda e: e["speaker_name"] or f"speaker {e['speaker_label']}"
    return {
        "id": c["id"], "state": c["state"], "deliverable": c["deliverable"], "owner": c["owner_name"], "project": c["project"],
        "deadline": _day(c["deadline_date"]), "deadline_basis": c["deadline_basis"],
        "original": {"type": origin["type"], "excerpt": origin["excerpt"], "speaker": who(origin), "source_kind": origin["source_kind"],
                     "occurred_at": origin["occurred_at"].isoformat(), "filename": origin["audio_filename"]} if origin else None,
        "task": {"page_id": task["notion_page_id"], "title": task["title"], "owner": task["owner_name"], "due": _day(task["due_date"]),
                 "status": task["status"], "url": notion_url(task["notion_page_id"])} if task else None,
        "evidence": [{"relation": e["relation"], "strength": e["strength"], "rationale": e["rationale"], "type": e["type"],
                      "excerpt": e["excerpt"], "speaker": who(e), "source_kind": e["source_kind"],
                      "occurred_at": e["occurred_at"].isoformat()} for e in evidence],
        "history": [{"from": h["from_state"], "to": h["to_state"], "at": h["at"].isoformat(), "detail": h["detail"]} for h in history],
        "pending_review_items": [{"id": i["id"], "kind": i["kind"]} for i in open_items],
        "actions": [a for a in (
            {"id": "open_in_notion", "label": "Open in Notion", "url": notion_url(c["notion_page_id"])} if c["notion_page_id"] else None,
            {"id": "review", "label": "Go to Review Inbox"} if open_items else None) if a],
    }
