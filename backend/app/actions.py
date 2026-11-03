"""What happens after a human decides: record, validate, execute, apply.

The model proposed; a person decided. Before anything is written, the approved action is re-checked
against current state (validate). Notion writes are idempotent (execute). Commitment states change only
here, only after a decision (apply).
"""
import copy
import hashlib
from datetime import date

from psycopg.types.json import Jsonb

from app import db, notion_api, notion_sync
from app.match import DONE_STATUSES, score_task

EDITABLE = {"owner", "due", "title", "notion_page_id"}
DUPLICATE_TASK_SIMILARITY = 0.85
LIVE_STATES = ("proposed", "open", "resolution_proposed")


class DecisionError(ValueError):
    """A decision that cannot be recorded; the message is safe to show a user."""


# ---- recording decisions ------------------------------------------------------------------------

def record_decision(item_id: int, action: str, edits: dict | None = None, decided_by: str = "user") -> None:
    """Save or change a decision. Allowed until the run's Apply consumes it."""
    if action not in ("approve", "reject", "edit"):
        raise DecisionError(f"Unknown decision '{action}'. Use approve, reject or edit.")
    edits = edits or {}
    if action == "edit" and not edits:
        raise DecisionError("An edit needs at least one change.")
    if unknown := set(edits) - EDITABLE:
        raise DecisionError(f"Cannot edit: {', '.join(sorted(unknown))}.")
    if "due" in edits and edits["due"]:
        try:
            date.fromisoformat(edits["due"])
        except ValueError as e:
            raise DecisionError("due must be a date like 2026-11-06.") from e
    with db.connect() as conn:
        item = conn.execute("SELECT status, locked_at FROM review_items WHERE id = %s", (item_id,)).fetchone()
        if not item:
            raise DecisionError(f"Review item {item_id} does not exist.")
        if item["status"] != "open" or item["locked_at"]:
            raise DecisionError("This item is already resolved and can no longer be changed.")
        conn.execute(
            """INSERT INTO decisions (review_item_id, action, edits, decided_by) VALUES (%s, %s, %s, %s)
               ON CONFLICT (review_item_id) DO UPDATE
               SET action = EXCLUDED.action, edits = EXCLUDED.edits, decided_by = EXCLUDED.decided_by, decided_at = now()""",
            (item_id, action, Jsonb(edits), decided_by),
        )


def undecided_ids(source_id: int) -> list[int]:
    """Open items of a run that still lack a decision. Apply is refused while this is non-empty."""
    with db.connect() as conn:
        return [r["id"] for r in conn.execute(
            """SELECT r.id FROM review_items r LEFT JOIN decisions d ON d.review_item_id = r.id
               WHERE r.source_id = %s AND r.status = 'open' AND d.id IS NULL ORDER BY r.id""", (source_id,))]


def lock_decided(source_id: int) -> list[int]:
    """Mark decided open items as consumed by the graph, so they can no longer be changed. Returns their ids."""
    with db.connect() as conn:
        return [r["id"] for r in conn.execute(
            """UPDATE review_items r SET locked_at = COALESCE(r.locked_at, now())
               FROM decisions d WHERE d.review_item_id = r.id AND r.source_id = %s AND r.status = 'open'
               RETURNING r.id""", (source_id,))]


def decided_open(conn, source_id: int, unvalidated_only: bool = False) -> list[dict]:
    """Decided items not yet applied. With unvalidated_only, just the ones this review round still has to check."""
    return conn.execute(
        f"""SELECT r.*, d.action AS decision, d.edits AS edits FROM review_items r
            JOIN decisions d ON d.review_item_id = r.id
            WHERE r.source_id = %s AND r.status = 'open'{' AND r.validated_at IS NULL' if unvalidated_only else ''}
            ORDER BY r.id""", (source_id,)).fetchall()


def mark_validated(conn, item_id: int) -> None:
    conn.execute("UPDATE review_items SET validated_at = now() WHERE id = %s", (item_id,))


# ---- validate -----------------------------------------------------------------------------------

def effective_action(item: dict) -> dict:
    """The proposed action with the reviewer's edits applied."""
    action = copy.deepcopy(item["proposed_action"])
    edits = item["edits"] or {}
    if action["type"] == "link_task" and edits.get("notion_page_id"):
        action["notion_page_id"] = edits["notion_page_id"]
    if action["type"] == "create_task":
        for key in ("title", "owner", "due"):
            if key in edits:
                action["task"][key] = edits[key]
    action["edits"] = edits
    return action


def _changed_since_snapshot(live: notion_api.NotionTask, snap: dict) -> bool:
    return (live.last_edited > snap["notion_last_edited"] or live.status != snap["status"]
            or live.due != snap["due_date"] or live.title != snap["title"])


def validate(conn, item: dict, gw) -> tuple[dict | None, str | None]:
    """Re-check an approved item against current state. Returns (action, None) or (None, reason it cannot proceed)."""
    action = effective_action(item)
    kind = action["type"]
    if kind == "none":
        return action, None
    c = conn.execute("SELECT * FROM commitments WHERE id = %s", (action["commitment_id"],)).fetchone()
    if not c:
        return None, "the commitment no longer exists"

    if kind == "link_task":
        if c["state"] != "proposed":
            return None, f"the commitment is {c['state']}, not waiting for a link"
        live = gw.get_task(action["notion_page_id"])
        if live is None:
            return None, "the Notion task no longer exists"
        notion_sync.refresh_task(conn, live)
        if live.status.lower() in DONE_STATUSES:
            return None, f'the Notion task "{live.title}" is already marked {live.status}'
        other = conn.execute(
            "SELECT id FROM commitments WHERE notion_page_id = %s AND state = ANY(%s) AND id <> %s",
            (action["notion_page_id"], list(LIVE_STATES), c["id"])).fetchone()
        return (None, f"that task is already tracked by commitment #{other['id']}") if other else (action, None)

    if kind == "create_task":
        task = action["task"]
        if c["state"] != "proposed":
            return None, f"the commitment is {c['state']}, not waiting for a task"
        if not (task.get("title") or "").strip():
            return None, "the new task has no title"
        if not task.get("project_id"):
            return None, "the project for the new task is unknown, so it cannot be placed"
        rows = conn.execute("SELECT * FROM notion_tasks WHERE project_id = %s", (task["project_id"],)).fetchall()
        twin = next((r for r in rows if r["status"].lower() not in DONE_STATUSES
                     and score_task(task["title"], task.get("owner"), r) >= DUPLICATE_TASK_SIMILARITY), None)
        return (None, f'a similar task already exists: "{twin["title"]}"') if twin else (action, None)

    # resolve_task
    if c["state"] != "resolution_proposed":
        return None, f"the commitment is {c['state']}, not waiting for resolution"
    page = action.get("notion_page_id")
    if not page:
        return None, "the commitment has no Notion task yet, so there is nothing to mark done"
    live = gw.get_task(page)
    if live is None:
        return None, "the Notion task no longer exists"
    snap = conn.execute("SELECT * FROM notion_tasks WHERE notion_page_id = %s", (page,)).fetchone()
    changed = snap is not None and _changed_since_snapshot(live, snap)
    notion_sync.refresh_task(conn, live)
    if live.status.lower() in DONE_STATUSES:
        action["already_done"] = True
        return action, None
    if changed:
        return None, f'"{live.title}" was edited in Notion after it was last synced (it is now {live.status})'
    return action, None


def fail_item(conn, item: dict, reason: str, thread_id: str | None) -> int:
    """An approved action could not proceed. Resolve the original item and open a fresh one for another look."""
    action = effective_action(item)
    action.pop("edits", None)
    new_id = conn.execute(
        """INSERT INTO review_items (kind, reason, source_id, event_id, commitment_id, proposed_action, graph_thread_id)
           VALUES ('conflict', %s, %s, %s, %s, %s, %s) RETURNING id""",
        (f"Your approval could not be applied: {reason}. The task was refreshed from Notion; please review it again.",
         item["source_id"], item["event_id"], item["commitment_id"], Jsonb({**action, "retry_of": item["id"]}), thread_id),
    ).fetchone()["id"]
    conn.execute("UPDATE review_items SET status = 'resolved', locked_at = COALESCE(locked_at, now()) WHERE id = %s", (item["id"],))
    _audit(conn, "review_item", item["id"], "open", "resolved", {"validation_failed": reason, "replaced_by": new_id})
    return new_id


# ---- execute ------------------------------------------------------------------------------------

def _key(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()


def _provenance(conn, action: dict) -> str:
    ev = conn.execute(
        """SELECT e.excerpt, e.speaker_name, e.owner_name, e.speaker_label, s.occurred_at
           FROM commitments c JOIN events e ON e.id = COALESCE(%s, c.origin_event_id)
           JOIN sources s ON s.id = e.source_id WHERE c.id = %s""",
        (action.get("evidence_event_id"), action["commitment_id"])).fetchone()
    if not ev:
        return "ProjectPulse"
    who = ev["speaker_name"] or ev["owner_name"] or f"speaker {ev['speaker_label']}"
    return f'{who} on {ev["occurred_at"].date().isoformat()}: "{ev["excerpt"]}"'


def execute(action: dict, gw) -> dict:
    """Perform one validated action. Safe to call again after a partial failure."""
    kind = action["type"]
    if kind not in ("create_task", "resolve_task"):
        return {}  # link_task and none touch only the database, and only at apply time
    c_id = action["commitment_id"]
    with db.connect() as conn:
        provenance = _provenance(conn, action)
        if kind == "create_task":
            task = action["task"]
            key = _key("create", c_id, task["title"])
            payload = {"commitment_id": c_id, "task": task}
            kind_db = "create_task"
        else:
            key = _key("resolve", c_id, action["notion_page_id"])
            payload = {"commitment_id": c_id, "notion_page_id": action["notion_page_id"]}
            kind_db = "update_task"
        conn.execute(
            """INSERT INTO executions (idempotency_key, action_type, notion_page_id, payload) VALUES (%s, %s, %s, %s)
               ON CONFLICT (idempotency_key) DO NOTHING""",
            (key, kind_db, action.get("notion_page_id"), Jsonb(payload)))
        ex = conn.execute("SELECT * FROM executions WHERE idempotency_key = %s", (key,)).fetchone()
    marker = "pp-" + key[:10]

    try:
        if ex["status"] == "done":
            page_id = (ex["result"] or {}).get("page_id") or action.get("notion_page_id")
        elif kind == "create_task":
            with db.connect() as conn:
                project = conn.execute("SELECT name, notion_page_id FROM projects WHERE id = %s", (task["project_id"],)).fetchone()
            page_id = gw.find_by_marker(marker) or gw.create_task(
                marker=marker, title=task["title"], owner=task.get("owner"), due=task.get("due") or None,
                project_name=project["name"], project_page_id=project["notion_page_id"],
                evidence=f"Created by ProjectPulse from {provenance}")
        else:
            page_id = action["notion_page_id"]
            if not action.get("already_done"):
                gw.mark_done(page_id, marker=marker, note=f"Resolved via ProjectPulse ({action.get('strength', 'claimed')}): {provenance}")
    except Exception as e:
        with db.connect() as conn:
            conn.execute("UPDATE executions SET status = 'failed', result = %s WHERE idempotency_key = %s",
                         (Jsonb({"error": str(e)}), key))
        raise

    live = gw.get_task(page_id)
    with db.connect() as conn:
        if live:
            notion_sync.refresh_task(conn, live)
        if kind == "create_task":
            conn.execute("UPDATE commitments SET notion_page_id = %s, updated_at = now() WHERE id = %s", (page_id, c_id))
        conn.execute("UPDATE executions SET status = 'done', notion_page_id = %s, result = %s WHERE idempotency_key = %s",
                     (page_id, Jsonb({"page_id": page_id}), key))
    return {"page_id": page_id}


# ---- apply --------------------------------------------------------------------------------------

def _audit(conn, entity: str, entity_id: int, from_state: str | None, to_state: str, detail: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO audit_log (entity_type, entity_id, from_state, to_state, detail) VALUES (%s, %s, %s, %s, %s)",
        (entity, entity_id, from_state, to_state, Jsonb(detail or {})))


def _move(conn, commitment_id: int, allowed: tuple[str, ...], to: str, detail: dict) -> bool:
    row = conn.execute("SELECT state FROM commitments WHERE id = %s FOR UPDATE", (commitment_id,)).fetchone()
    if not row or row["state"] not in allowed:
        return False  # already moved: applying twice is harmless
    conn.execute("UPDATE commitments SET state = %s, updated_at = now() WHERE id = %s", (to, commitment_id))
    _audit(conn, "commitment", commitment_id, row["state"], to, detail)
    return True


def apply_item(conn, item: dict) -> None:
    """Apply a decided item's effect on commitments and mark the item resolved. Idempotent."""
    action = effective_action(item)
    approved = item["decision"] in ("approve", "edit")
    kind, cid = action["type"], action.get("commitment_id")
    detail = {"review_item": item["id"], "decision": item["decision"]}

    if kind in ("link_task", "create_task", "resolve_task") and cid:
        if approved:
            if kind == "link_task":
                edits = action["edits"]
                page = action["notion_page_id"]
                conn.execute(
                    """UPDATE commitments SET notion_page_id = %s, owner_name = COALESCE(%s, owner_name),
                              deadline_date = COALESCE(%s, deadline_date), updated_at = now() WHERE id = %s""",
                    (page, edits.get("owner"), edits.get("due") or None, cid))
                conn.execute(
                    """INSERT INTO task_matches (event_id, notion_page_id, score, rationale, status)
                       VALUES (%s, %s, 1.0, 'confirmed by reviewer', 'confirmed')
                       ON CONFLICT (event_id, notion_page_id) DO UPDATE SET status = 'confirmed'""",
                    (item["event_id"], page))
                _move(conn, cid, ("proposed",), "open", detail)
            elif kind == "create_task":
                _move(conn, cid, ("proposed",), "open", detail)
            else:
                _move(conn, cid, ("resolution_proposed",), "resolved", detail)
        elif kind in ("link_task", "create_task"):
            conn.execute("UPDATE task_matches SET status = 'rejected' WHERE event_id = %s", (item["event_id"],))
            _move(conn, cid, ("proposed",), "rejected", detail)
        else:
            _move(conn, cid, ("resolution_proposed",), "open", detail)

    conn.execute("UPDATE review_items SET status = 'resolved', locked_at = COALESCE(locked_at, now()) WHERE id = %s", (item["id"],))
    _audit(conn, "review_item", item["id"], "open", "resolved", detail)
