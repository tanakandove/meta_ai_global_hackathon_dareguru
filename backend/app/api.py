"""HTTP API for the ProjectPulse UI.

Starting, applying and retrying a run return at once and do the work on a background thread; the UI
follows progress by polling GET /api/runs/{id}, which reads the `runs` table.
"""
import shutil
import threading
import uuid
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import actions, notion_sync, queries, runner
from app.graph import set_run
from app.transcribe import AudioError, check_wav

ROOT = Path(__file__).resolve().parents[2]
UPLOAD_DIR = ROOT / "backend" / "data" / "uploads"
FRONTEND_DIST = ROOT / "frontend" / "dist"
KINDS = {"audio", "transcript", "email", "text"}

app = FastAPI(title="ProjectPulse")
app.state.graph = None  # tests inject a graph built with fakes


def _graph():
    return app.state.graph or runner.get_graph()


def _in_background(events) -> None:
    """Drive a run on a thread. Failures are recorded on the run by the runner, so nothing is lost here."""
    threading.Thread(target=lambda: [None for _ in events], daemon=True).start()


@app.exception_handler(actions.DecisionError)
def _decision_error(_: Request, e: actions.DecisionError) -> JSONResponse:
    return JSONResponse({"detail": str(e)}, status_code=409)


@app.exception_handler(AudioError)
def _audio_error(_: Request, e: AudioError) -> JSONResponse:
    return JSONResponse({"detail": str(e)}, status_code=400)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/runs", status_code=202)
def create_run(
    kind: str = Form(...), meeting_date: str = Form(...), meeting_time: str = Form("09:00"), tz: str = Form("UTC"),
    text: str | None = Form(None), file: UploadFile | None = File(None),
) -> dict:
    if kind not in KINDS:
        raise HTTPException(400, f"kind must be one of {sorted(KINDS)}.")
    try:
        zone = ZoneInfo(tz)
        when = datetime.combine(date.fromisoformat(meeting_date), time.fromisoformat(meeting_time), tzinfo=zone)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise HTTPException(400, f"Invalid date, time or timezone: {e}") from e

    if kind == "audio":
        if not file or not file.filename:
            raise HTTPException(400, "Choose a WAV recording to upload.")
        folder = UPLOAD_DIR / uuid.uuid4().hex[:8]
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / Path(file.filename).name
        with path.open("wb") as out:
            shutil.copyfileobj(file.file, out)
        try:
            check_wav(path)
        except AudioError:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        thread_id, events = runner.stream_start("audio", occurred_at=when, tz=tz, audio_path=path, graph=_graph())
    else:
        body = text or (file.file.read().decode("utf-8", "replace") if file and file.filename else "")
        if not body.strip():
            raise HTTPException(400, "Paste some text or choose a text file.")
        thread_id, events = runner.stream_start(kind, occurred_at=when, tz=tz, text=body, graph=_graph())
    _in_background(events)
    return {"thread_id": thread_id}


@app.get("/api/runs")
def list_runs() -> list[dict]:
    return [{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in r.items()} for r in runner.list_runs()]


@app.get("/api/runs/{thread_id}")
def get_run(thread_id: str) -> dict:
    detail = queries.run_detail(thread_id)
    if not detail:
        raise HTTPException(404, "No such run.")
    return detail


class Decision(BaseModel):
    action: str
    edits: dict = Field(default_factory=dict)


@app.post("/api/review/{item_id}/decision")
def decide(item_id: int, body: Decision) -> dict:
    runner.submit_decision(item_id, body.action, body.edits)
    return {"ok": True}


@app.post("/api/runs/{thread_id}/apply", status_code=202)
def apply(thread_id: str) -> dict:
    events = runner.stream_apply(thread_id, _graph())  # raises 409 with the missing items when not ready
    set_run(thread_id, "running", "human_review", "Applying your decisions.")
    _in_background(events)
    return {"thread_id": thread_id}


@app.post("/api/runs/{thread_id}/retry", status_code=202)
def retry(thread_id: str) -> dict:
    events = runner.stream_retry(thread_id, _graph())
    set_run(thread_id, "running", None, "Retrying.")
    _in_background(events)
    return {"thread_id": thread_id}


@app.get("/api/inbox")
def inbox() -> list[dict]:
    return queries.inbox()


@app.get("/api/followups")
def followups() -> dict:
    return queries.followups()


@app.get("/api/commitments/{commitment_id}")
def commitment(commitment_id: int) -> dict:
    detail = queries.commitment_detail(commitment_id)
    if not detail:
        raise HTTPException(404, "No such commitment.")
    return detail


@app.get("/api/people")
def people() -> list[str]:
    return queries.people()


@app.post("/api/notion/sync")
def sync_notion() -> dict:
    return notion_sync.sync()


if FRONTEND_DIST.exists():  # one server for the demo: API plus the built UI
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        target = FRONTEND_DIST / path
        return FileResponse(target if path and target.is_file() else FRONTEND_DIST / "index.html")
