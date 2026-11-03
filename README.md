# ProjectPulse

**The follow-through agent for project teams.** ProjectPulse listens to what your team says in meetings and emails, finds the commitments, matches them to your existing Notion tasks, and asks a person before it changes anything. When later evidence shows a commitment was met, it proposes closing the loop, and a human approves it.

> Built for the Meta Global AI Developer Hackathon (Indie track), using **Muse Voice Transcribe** (diarized meeting audio) and **Muse Spark** (structured extraction and reconciliation) through the Meta Model API.

## What it does

1. **Capture** a meeting recording, transcript, email or text update.
2. **Understand** it: speakers are identified from context, and commitments, requests, suggestions, progress, blockers and completion claims are extracted with the exact quote behind each.
3. **Reconcile** with existing work: project first, then task, so near-identical tasks in other projects are never mixed up. A suggestion never becomes a task, and a claim never closes one.
4. **Review**: a person approves, edits or rejects each item, seeing the quote, the reason and exactly what will change.
5. **Close the loop**: approved changes are written to Notion safely (re-checked against current state, idempotent, retryable), and the Follow-Up Center shows what is outstanding, overdue, blocked or resolved, and why.

The model interprets. The graph coordinates. The backend validates. The human resolves uncertainty. The system executes approved actions. See [`docs/graph-design.md`](docs/graph-design.md) for the workflow and `python scripts/explain_decisions.py` for the reasoning behind each design decision.

## Run it

Requirements: Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 20+, PostgreSQL, a Meta Model API key and a Notion token.

```bash
cp .env.example .env            # then fill in MODEL_API_KEY, NOTION_TOKEN and the Notion database ids
createdb projectpulse

cd backend
uv sync
uv run python -m app.db         # create the schema
uv run python -m app.notion_sync

cd ../frontend
npm install
npm run build                   # the API serves the built UI

cd ../backend
uv run uvicorn app.api:app --port 8077
```

Open <http://localhost:8077>. (For UI development run `npm run dev` in `frontend/`; it proxies `/api` to port 8077.)

### Notion setup

Two databases: **Tasks** (`Name`, `Owner`, `Due`, `Status` with a `Done` option, `Project`, `Evidence`, and a `Project Link` relation to Projects) and **Projects** (`Name`, `Aliases`, `Client`). Project aliases are how "the dashboard install" in a meeting finds the right project.

### Tests

```bash
cd backend
uv run pytest            # fast suite: real Postgres, fake model and Notion
uv run pytest -m live    # calls the real Meta and Notion APIs (costs tokens)
```

## Data

The demo uses **synthetic** data: a scripted meeting rendered with text-to-speech voices (`scripts/make_meeting_audio.py`), and a test Notion workspace. Real meetings would be sent to the Meta Model API, so check what your organization allows before using real recordings.
