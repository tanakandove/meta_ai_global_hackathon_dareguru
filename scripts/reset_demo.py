"""Put the demo back to its starting state, so it can be rehearsed or recorded again.

    cd backend
    uv run python ../scripts/reset_demo.py --save   # once, while Notion is in its starting state
    uv run python ../scripts/reset_demo.py --yes    # restore

--save snapshots the Status and Evidence of every task in the Tasks database.
Restoring puts those two fields back on every task in the snapshot, moves any task ProjectPulse
created (recognised by the "[pp-" marker it writes) to the trash, deletes every ProjectPulse run and
record from the local database, and re-syncs Notion. It does not touch anything else in Notion.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import db, notion_api, notion_sync  # noqa: E402

BASELINE = ROOT / "backend" / "data" / "demo_baseline.json"
UPLOADS = ROOT / "backend" / "data" / "uploads"

# Children first, so foreign keys never block a delete.
TABLES = ["executions", "decisions", "review_items", "audit_log", "evidence", "task_matches", "commitments",
          "runs", "events", "sources", "checkpoint_writes", "checkpoint_blobs", "checkpoints"]


def save() -> None:
    tasks = notion_api.fetch_tasks()
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(json.dumps({t.page_id: {"title": t.title, "status": t.status, "evidence": t.evidence} for t in tasks}, indent=2))
    print(f"Saved the starting state of {len(tasks)} tasks to {BASELINE.relative_to(ROOT)}")


def restore_notion() -> None:
    baseline = json.loads(BASELINE.read_text())
    client = notion_api.client()
    for task in notion_api.fetch_tasks():
        start = baseline.get(task.page_id)
        if start is None:
            if "[pp-" in task.evidence:  # created by ProjectPulse
                client.pages.update(task.page_id, in_trash=True)
                print(f"  trashed task created by ProjectPulse: {task.title}")
            continue
        if (task.status, task.evidence) != (start["status"], start["evidence"]):
            client.pages.update(task.page_id, properties={
                "Status": {"select": {"name": start["status"]} if start["status"] else None},
                "Evidence": {"rich_text": [{"text": {"content": start["evidence"]}}] if start["evidence"] else []},
            })
            print(f"  restored: {task.title}  ({task.status or 'none'} -> {start['status'] or 'none'})")


def clear_database() -> None:
    with db.connect() as conn:
        for table in TABLES:
            conn.execute(f"DELETE FROM {table}")
    shutil.rmtree(UPLOADS, ignore_errors=True)
    print("Cleared every ProjectPulse run and record from the local database.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--save", action="store_true", help="snapshot the current Notion state as the starting state")
    ap.add_argument("--yes", action="store_true", help="confirm the restore (it deletes local ProjectPulse data)")
    args = ap.parse_args()
    if args.save:
        return save()
    if not BASELINE.exists():
        raise SystemExit("No snapshot yet. Run with --save while Notion is in its starting state.")
    if not args.yes:
        raise SystemExit("This restores Notion tasks and deletes all local ProjectPulse data. Re-run with --yes to confirm.")
    restore_notion()
    clear_database()
    print("Notion re-synced:", notion_sync.sync())


if __name__ == "__main__":
    main()
