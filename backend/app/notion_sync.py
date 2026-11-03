"""Cache the Notion Projects and Tasks databases into Postgres.

Idempotent: running it repeatedly converges on the current Notion state.
Tasks deleted in Notion are not removed here because commitments may reference them.
People are not a Notion database, so owner names are clustered to absorb typos.
"""
from app import db, notion_api
from app.names import cluster_names


def _sync_projects(conn) -> dict[str, int]:
    """Upsert projects from Notion; return notion page id -> projects.id."""
    ids: dict[str, int] = {}
    for p in notion_api.fetch_projects():
        row = conn.execute(
            """INSERT INTO projects (name, aliases, notion_page_id, client)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (name) DO UPDATE SET
                   aliases = EXCLUDED.aliases, notion_page_id = EXCLUDED.notion_page_id,
                   client = EXCLUDED.client
               RETURNING id""",
            (p.name, p.aliases, p.page_id, p.client),
        ).fetchone()
        ids[p.page_id] = row["id"]
    return ids


def _sync_people(conn, owner_names: list[str]) -> tuple[dict[str, str], dict[str, int]]:
    """Upsert people with typo-collapsed names; return raw -> canonical and canonical -> id."""
    canonical = cluster_names(owner_names)
    variants: dict[str, set[str]] = {}
    for raw, canon in canonical.items():
        variants.setdefault(canon, set())
        if raw != canon:
            variants[canon].add(raw)
    ids: dict[str, int] = {}
    for canon, aliases in variants.items():
        row = conn.execute(
            """INSERT INTO people (name, aliases) VALUES (%s, %s)
               ON CONFLICT (name) DO UPDATE
               SET aliases = (SELECT ARRAY(SELECT DISTINCT unnest(people.aliases || EXCLUDED.aliases)))
               RETURNING id""",
            (canon, sorted(aliases)),
        ).fetchone()
        ids[canon] = row["id"]
    return canonical, ids


def _upsert_task(conn, t: notion_api.NotionTask, owner: str, project_name: str,
                 person_id: int | None, project_id: int | None) -> None:
    conn.execute(
        """INSERT INTO notion_tasks
               (notion_page_id, title, owner_name, due_date, status, project_name,
                project_page_id, evidence_text, person_id, project_id, notion_last_edited, synced_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
           ON CONFLICT (notion_page_id) DO UPDATE SET
               title = EXCLUDED.title, owner_name = EXCLUDED.owner_name,
               due_date = EXCLUDED.due_date, status = EXCLUDED.status,
               project_name = EXCLUDED.project_name, project_page_id = EXCLUDED.project_page_id,
               evidence_text = EXCLUDED.evidence_text, person_id = EXCLUDED.person_id,
               project_id = EXCLUDED.project_id,
               notion_last_edited = EXCLUDED.notion_last_edited, synced_at = now()""",
        (t.page_id, t.title, owner, t.due, t.status, project_name, t.project_page_id, t.evidence,
         person_id, project_id, t.last_edited),
    )


def refresh_task(conn, t: notion_api.NotionTask) -> None:
    """Refresh the cached snapshot of one task (after reading or writing it in Notion)."""
    project = conn.execute("SELECT id, name FROM projects WHERE notion_page_id = %s", (t.project_page_id,)).fetchone()
    person = conn.execute("SELECT id FROM people WHERE name = %s", (t.owner,)).fetchone()
    _upsert_task(conn, t, t.owner, project["name"] if project else "", person["id"] if person else None,
                 project["id"] if project else None)


def sync() -> dict[str, int]:
    tasks = notion_api.fetch_tasks()

    with db.connect() as conn:
        project_ids = _sync_projects(conn)
        project_names = {
            r["notion_page_id"]: r["name"]
            for r in conn.execute("SELECT notion_page_id, name FROM projects WHERE notion_page_id IS NOT NULL")
        }
        owner_canonical, person_ids = _sync_people(conn, [t.owner for t in tasks])

        for t in tasks:
            owner = owner_canonical.get(t.owner, t.owner)
            _upsert_task(conn, t, owner, project_names.get(t.project_page_id, ""),
                         person_ids.get(owner), project_ids.get(t.project_page_id))
    return {"tasks": len(tasks), "projects": len(project_ids), "people": len(person_ids)}


if __name__ == "__main__":
    print(sync())
