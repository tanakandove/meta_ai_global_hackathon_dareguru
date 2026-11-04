"""The ProjectPulse LangGraph workflow (see docs/graph-design.md for the diagram).

ingest -> transcribe (audio only) -> retrieve_context -> extract -> wait_turn -> match -> reconcile
       -> human_review (interrupt) -> validate -> execute -> persist

Built to be driven by a UI:
- Every node records itself in the `runs` table, so a UI shows progress without reading checkpoints.
- The graph stops in exactly two ways: it ends with a terminal status, or it pauses at human_review
  (status waiting_for_review) and holds no process until Apply resumes it.
- Graph state carries ids only; Postgres is the source of truth.
- An exception leaves the checkpoint before the failing node, so a retry resumes from there.
"""
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, TypedDict

from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app import actions, db, extract, ingest, llm, notion_api, notion_sync, reconcile, transcribe


class State(TypedDict, total=False):
    thread_id: str
    kind: str          # audio | transcript | email | text
    audio_path: str
    text: str
    occurred_at: str   # ISO 8601 with offset
    tz: str
    source_id: int
    reuse_source: int  # set on a re-run: analyse this stored source instead of ingesting one
    duplicate: bool
    event_count: int
    matches: dict
    ready: list[dict]  # validated actions waiting for execute
    failed_count: int  # approvals that could not proceed in the latest validate


@dataclass
class Deps:
    """Everything the graph calls out to, so tests can substitute fakes."""
    llm_call: Callable = llm.structured_call
    gateway: object = field(default_factory=notion_api.NotionGateway)
    transcribe_fn: Callable = transcribe.transcribe_wav
    refresh_fn: Callable = notion_sync.sync


# ---- run bookkeeping ----------------------------------------------------------------------------

def set_run(thread_id: str, status: str, node: str | None = None, message: str = "", source_id: int | None = None,
            skipped_review: bool | None = None) -> None:
    with db.connect() as conn:
        conn.execute(
            """UPDATE runs SET status = %s, current_node = COALESCE(%s, current_node), message = %s,
                      source_id = COALESCE(%s, source_id), skipped_review = COALESCE(%s, skipped_review),
                      updated_at = now() WHERE thread_id = %s""",
            (status, node, message, source_id, skipped_review, thread_id))


def step(name: str, fn: Callable[[State], dict]) -> Callable[[State], dict]:
    """Wrap a node: record progress, retry once, and record a failure before re-raising.

    The re-raise keeps the checkpoint before this node, so retrying the run resumes here.
    """
    def run(state: State) -> dict:
        set_run(state["thread_id"], "running", name, f"Running {name.replace('_', ' ')}")
        for attempt in (1, 2):
            try:
                return fn(state)
            except GraphBubbleUp:
                raise  # an interrupt is how the graph pauses, not an error
            except Exception as e:
                if attempt == 2:
                    set_run(state["thread_id"], "failed", name, f"{name.replace('_', ' ').capitalize()} failed: {e}")
                    raise
                time.sleep(1)
    return run


# ---- ordering -----------------------------------------------------------------------------------

WAIT_POLL_SECONDS = 2.0


def unfinished_earlier_runs(thread_id: str) -> int:
    """Runs that started before this one and are not yet applied or ended. A failed run does not block."""
    with db.connect() as conn:
        return conn.execute(
            """SELECT count(*) AS n FROM runs me JOIN runs r ON r.thread_id <> me.thread_id
                AND split_part(r.thread_id, '-', 1) = split_part(me.thread_id, '-', 1)
                AND (r.created_at, r.thread_id) < (me.created_at, me.thread_id)
               WHERE me.thread_id = %s AND r.status IN ('running', 'waiting_for_review')""", (thread_id,)).fetchone()["n"]


def wait_for_turn(thread_id: str) -> bool:
    """Block until every earlier run has finished. Returns True if it had to wait.

    Sources are analysed in parallel, but a source may only read Notion and propose changes once the sources
    that arrived before it are done, so a later source always sees what an earlier one changed.
    """
    waited = False
    while (n := unfinished_earlier_runs(thread_id)):
        waited = True
        set_run(thread_id, "running", "wait_turn",
                f"Waiting for {n} earlier source{'s' if n > 1 else ''} to finish. Sources are applied in the order they arrived.")
        time.sleep(WAIT_POLL_SECONDS)
    return waited


# ---- graph --------------------------------------------------------------------------------------

def build_graph(checkpointer, deps: Deps | None = None):
    d = deps or Deps()

    def n_ingest(state: State) -> dict:
        if state.get("reuse_source"):  # a re-run analyses a source that is already stored
            set_run(state["thread_id"], "running", "ingest", "Re-running on the stored source.", state["reuse_source"])
            return {"source_id": state["reuse_source"], "duplicate": False}
        when = datetime.fromisoformat(state["occurred_at"])
        if state["kind"] == "audio":
            sid, dup = ingest.register_audio(Path(state["audio_path"]), occurred_at=when, tz=state["tz"])
        else:
            sid, dup = ingest.ingest_text(state["text"], kind=state["kind"], occurred_at=when, tz=state["tz"])
        if dup:
            set_run(state["thread_id"], "duplicate", "ingest", "This source was already processed.", sid)
        else:
            set_run(state["thread_id"], "running", "ingest", "Source stored.", sid)
        return {"source_id": sid, "duplicate": dup}

    def n_transcribe(state: State) -> dict:
        with db.connect() as conn:
            done = conn.execute("SELECT turns IS NOT NULL AS done FROM sources WHERE id = %s", (state["source_id"],)).fetchone()["done"]
        if not done:  # a re-run already has its transcript, so it never pays for transcription twice
            ingest.attach_turns(state["source_id"], d.transcribe_fn(Path(state["audio_path"])))
        return {}

    def n_context(state: State) -> dict:
        d.refresh_fn()  # refresh the Notion snapshot so matching never runs against stale tasks
        return {}

    def n_extract(state: State) -> dict:
        n = len(extract.extract_events(state["source_id"], d.llm_call))
        if n == 0:
            set_run(state["thread_id"], "no_events", "extract", "Nothing actionable was found in this source.")
        return {"event_count": n}

    def n_wait_turn(state: State) -> dict:
        if wait_for_turn(state["thread_id"]):
            d.refresh_fn()  # the earlier sources changed Notion, so match against what it holds now
        return {}

    def n_match(state: State) -> dict:
        return {"matches": reconcile.match_source(state["source_id"])}

    def n_reconcile(state: State) -> dict:
        reconcile.reconcile_source(state["source_id"], d.llm_call, state.get("matches"))
        with db.connect() as conn:
            conn.execute("UPDATE review_items SET graph_thread_id = %s WHERE source_id = %s AND graph_thread_id IS NULL",
                         (state["thread_id"], state["source_id"]))
        return {}

    def n_review(state: State) -> dict:
        pending = actions.undecided_ids(state["source_id"])
        set_run(state["thread_id"], "waiting_for_review", "human_review",
                f"{len(pending)} item(s) need a decision.")
        # Nothing may happen before interrupt(): on resume LangGraph re-runs this node from the top.
        interrupt({"source_id": state["source_id"], "item_ids": pending})
        missing = actions.undecided_ids(state["source_id"])
        if missing:
            raise RuntimeError(f"Resumed with undecided items {missing}; Apply must wait for every decision.")
        actions.lock_decided(state["source_id"])
        return {}

    def n_validate(state: State) -> dict:
        ready = list(state.get("ready", []))
        failed = 0
        with db.connect() as conn:
            for item in actions.decided_open(conn, state["source_id"], unvalidated_only=True):
                if item["decision"] != "reject":
                    action, reason = actions.validate(conn, item, d.gateway)
                    if reason:
                        actions.fail_item(conn, item, reason, state["thread_id"])
                        failed += 1
                        continue
                    ready.append({"item_id": item["id"], "action": action})
                actions.mark_validated(conn, item["id"])
        return {"ready": ready, "failed_count": failed}

    def n_execute(state: State) -> dict:
        for r in state.get("ready", []):
            actions.execute(r["action"], d.gateway)
        return {"ready": []}

    def n_persist(state: State) -> dict:
        with db.connect() as conn:
            items = actions.decided_open(conn, state["source_id"])
            for item in items:
                actions.apply_item(conn, item)
        if items:
            set_run(state["thread_id"], "completed", "persist", f"Applied {len(items)} decision(s).", skipped_review=False)
        else:  # every review item needs a decision, so none means nothing was ever put in front of the person
            set_run(state["thread_id"], "completed", "persist",
                    "Nothing needed your attention: the tasks this source mentions were already up to date.", skipped_review=True)
        return {}

    # -- routing: each function answers "where does the diagram's arrow go next?"
    def after_ingest(state: State) -> str:
        if state["duplicate"]:
            return "end"
        return "transcribe" if state["kind"] == "audio" else "retrieve_context"

    def after_extract(state: State) -> str:
        return "end" if state["event_count"] == 0 else "match"

    def after_reconcile(state: State) -> str:
        return "human_review" if actions.undecided_ids(state["source_id"]) else "persist"

    def after_review(state: State) -> str:
        with db.connect() as conn:
            approved = [i for i in actions.decided_open(conn, state["source_id"], unvalidated_only=True) if i["decision"] != "reject"]
        if approved:
            return "validate"
        return "execute" if state.get("ready") else "persist"

    def after_validate(state: State) -> str:
        return "human_review" if state.get("failed_count") else "execute"

    g = StateGraph(State)
    for name, fn in [("ingest", n_ingest), ("transcribe", n_transcribe), ("retrieve_context", n_context),
                     ("extract", n_extract), ("wait_turn", n_wait_turn), ("match", n_match), ("reconcile", n_reconcile),
                     ("human_review", n_review), ("validate", n_validate), ("execute", n_execute), ("persist", n_persist)]:
        g.add_node(name, step(name, fn))
    g.add_edge(START, "ingest")
    g.add_conditional_edges("ingest", after_ingest, {"end": END, "transcribe": "transcribe", "retrieve_context": "retrieve_context"})
    g.add_edge("transcribe", "retrieve_context")
    g.add_edge("retrieve_context", "extract")
    g.add_conditional_edges("extract", after_extract, {"end": END, "match": "wait_turn"})
    g.add_edge("wait_turn", "match")
    g.add_edge("match", "reconcile")
    g.add_conditional_edges("reconcile", after_reconcile, {"human_review": "human_review", "persist": "persist"})
    g.add_conditional_edges("human_review", after_review, {"validate": "validate", "execute": "execute", "persist": "persist"})
    g.add_conditional_edges("validate", after_validate, {"human_review": "human_review", "execute": "execute"})
    g.add_edge("execute", "persist")
    g.add_edge("persist", END)
    return g.compile(checkpointer=checkpointer)
