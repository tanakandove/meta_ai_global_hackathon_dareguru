"""What a UI calls. Each function corresponds to something a person does:

    start_run      upload a recording or paste text
    stream_run     watch progress node by node
    get_run        see a run's status (running, waiting_for_review, completed, ...)
    submit_decision / apply_run   decide on review items, then press Apply
    retry_run      retry a run that failed

The graph stops in exactly two ways: it ends with a terminal status (completed, duplicate, no_events,
failed) or it pauses at waiting_for_review, where it holds no process until apply_run resumes it.
"""
import uuid
from datetime import datetime
from pathlib import Path
from typing import Iterator

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app import actions, config, db
from app.graph import Deps, build_graph, set_run

TERMINAL = {"completed", "duplicate", "no_events", "failed"}
_graph = None


def get_graph(deps: Deps | None = None):
    """Build the graph once, with a Postgres checkpointer. Pass deps only to substitute fakes."""
    global _graph
    if _graph is None or deps is not None:
        pool = ConnectionPool(config.DATABASE_URL, kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row}, open=True)
        saver = PostgresSaver(pool)
        saver.setup()
        graph = build_graph(saver, deps)
        if deps is not None:
            return graph
        _graph = graph
    return _graph


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def get_run(thread_id: str) -> dict | None:
    """Status for a UI: the run row plus how many review items still need a decision."""
    with db.connect() as conn:
        run = conn.execute("SELECT * FROM runs WHERE thread_id = %s", (thread_id,)).fetchone()
    if not run:
        return None
    if run["source_id"] is not None:
        undecided = actions.undecided_ids(run["source_id"])
        run = {**run, "undecided_item_ids": undecided, "can_apply": run["status"] == "waiting_for_review" and not undecided}
    return run


def list_runs(limit: int = 50) -> list[dict]:
    with db.connect() as conn:
        return conn.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT %s", (limit,)).fetchall()


def _drive(graph, thread_id: str, payload) -> Iterator[dict]:
    """Run the graph until it stops, yielding one event per node so a UI can show progress."""
    try:
        for chunk in graph.stream(payload, _config(thread_id), stream_mode="updates"):
            for node in chunk:
                if node == "__interrupt__":
                    yield {"event": "waiting_for_review"}
                else:
                    yield {"event": "node", "node": node}
    except Exception as e:  # the failing node already recorded details; make sure the run reads as failed
        run = get_run(thread_id)
        if run and run["status"] != "failed":
            set_run(thread_id, "failed", None, f"Run failed: {e}")
        yield {"event": "failed", "message": str(e)}
        return
    yield {"event": "stopped", "status": get_run(thread_id)["status"]}


def stream_start(kind: str, *, occurred_at: datetime, tz: str, audio_path: Path | None = None,
                 text: str | None = None, graph=None) -> tuple[str, Iterator[dict]]:
    """Create a run and return (thread_id, progress events). The run starts as the events are consumed."""
    if kind == "audio" and not audio_path:
        raise ValueError("An audio run needs an audio file.")
    if kind != "audio" and not text:
        raise ValueError("A text run needs text.")
    if occurred_at.tzinfo is None:
        raise ValueError("occurred_at must include a timezone offset.")
    thread_id = "run-" + uuid.uuid4().hex[:12]
    with db.connect() as conn:
        conn.execute("INSERT INTO runs (thread_id, kind, message) VALUES (%s, %s, 'Queued')", (thread_id, kind))
    payload = {"thread_id": thread_id, "kind": kind, "occurred_at": occurred_at.isoformat(), "tz": tz,
               **({"audio_path": str(audio_path)} if audio_path else {"text": text})}
    return thread_id, _drive(graph or get_graph(), thread_id, payload)


def start_run(kind: str, *, graph=None, **kwargs) -> dict:
    thread_id, events = stream_start(kind, graph=graph, **kwargs)
    for _ in events:
        pass
    return get_run(thread_id)


def submit_decision(item_id: int, action: str, edits: dict | None = None) -> None:
    """Save a decision. It can be changed until Apply."""
    actions.record_decision(item_id, action, edits)


def stream_apply(thread_id: str, graph=None) -> Iterator[dict]:
    run = get_run(thread_id)
    if not run or run["status"] != "waiting_for_review":
        raise actions.DecisionError("This run is not waiting for review.")
    if run["undecided_item_ids"]:
        raise actions.DecisionError(f"{len(run['undecided_item_ids'])} item(s) still need a decision: {run['undecided_item_ids']}.")
    return _drive(graph or get_graph(), thread_id, Command(resume={"apply": True}))


def apply_run(thread_id: str, graph=None) -> dict:
    for _ in stream_apply(thread_id, graph):
        pass
    return get_run(thread_id)


def stream_retry(thread_id: str, graph=None) -> Iterator[dict]:
    run = get_run(thread_id)
    if not run or run["status"] != "failed":
        raise actions.DecisionError("Only a failed run can be retried.")
    return _drive(graph or get_graph(), thread_id, None)  # None resumes from the last checkpoint


def retry_run(thread_id: str, graph=None) -> dict:
    for _ in stream_retry(thread_id, graph):
        pass
    return get_run(thread_id)


def stream_rerun(thread_id: str, graph=None) -> tuple[str, Iterator[dict]]:
    """Analyse a run's source again against the current Notion data. Returns (new_thread_id, progress events).

    Only a run that is still waiting for review can be re-run, and only while none of its decisions have been
    applied. Its unreviewed proposals are discarded, and the stored source (and transcript) is reused.
    """
    run = get_run(thread_id)
    if not run or run["status"] != "waiting_for_review":
        raise actions.DecisionError("Only a run that is waiting for review can be re-run.")
    with db.connect() as conn:
        applied = conn.execute("SELECT 1 FROM review_items WHERE source_id = %s AND status = 'resolved'", (run["source_id"],)).fetchone()
        if applied:
            raise actions.DecisionError("Some of this run's decisions were already applied, so it can't be re-run.")
        src = conn.execute("SELECT occurred_at, timezone FROM sources WHERE id = %s", (run["source_id"],)).fetchone()
        actions.discard_for_rerun(conn, run["source_id"])
        new_id = "run-" + uuid.uuid4().hex[:12]
        conn.execute("UPDATE runs SET status = 'superseded', message = 'Replaced by a re-run.', updated_at = now() WHERE thread_id = %s", (thread_id,))
        # A re-run keeps its original place in the queue, so sources that arrived after it still wait for it.
        conn.execute("INSERT INTO runs (thread_id, source_id, kind, message, rerun_of, created_at) VALUES (%s, %s, %s, 'Queued', %s, %s)",
                     (new_id, run["source_id"], run["kind"], thread_id, run["created_at"]))
    payload = {"thread_id": new_id, "kind": run["kind"], "occurred_at": src["occurred_at"].isoformat(), "tz": src["timezone"],
               "reuse_source": run["source_id"]}
    return new_id, _drive(graph or get_graph(), new_id, payload)


def recover_stale_runs(thread_prefix: str = "") -> int:
    """Mark runs left 'running' by a server that stopped as failed, so the UI offers Retry.

    Runs execute on background threads, so any run still 'running' when the server starts is one whose
    thread died. Retry resumes it from its last checkpoint. thread_prefix limits which runs are touched.
    """
    with db.connect() as conn:
        return conn.execute(
            """UPDATE runs SET status = 'failed', updated_at = now(),
                      message = 'The server restarted while this run was in progress. Retry to continue.'
               WHERE status = 'running' AND thread_id LIKE %s RETURNING thread_id""", (thread_prefix + "%",)).rowcount
