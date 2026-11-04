"""Cache the Notion Projects and Tasks databases into Postgres.

Idempotent: running it repeatedly converges on the current Notion state.
Tasks deleted in Notion are not removed here because commitments may reference them.
People are not a Notion database, so owner names are clustered to absorb typos.
"""
from app import db, notion_api
from app.names import cluster_names


def _sync_projects(conn, projects: list[notion_api.NotionProject] | None = None) -> dict[str, int]:
    """Upsert projects from Notion; return notion page id -> projects.id.

    A Notion page id is a project's identity, so a rename in Notion renames the row instead of adding a second
    one. A project first stored by name only (before page ids were kept) is adopted by its name.
    """
    ids: dict[str, int] = {}
    for p in projects if projects is not None else notion_api.fetch_projects():
        values = (p.name, p.aliases, p.client)
        row = (
            conn.execute(
                "UPDATE projects SET name = %s, aliases = %s, client = %s WHERE notion_page_id = %s RETURNING id",
                (*values, p.page_id)).fetchone()
            or conn.execute(
                """UPDATE projects SET aliases = %s, client = %s, notion_page_id = %s
                   WHERE name = %s AND notion_page_id IS NULL RETURNING id""",
                (p.aliases, p.client, p.page_id, p.name)).fetchone()
            or conn.execute(
                "INSERT INTO projects (name, aliases, client, notion_page_id) VALUES (%s, %s, %s, %s) RETURNING id",
                (*values, p.page_id)).fetchone()
        )
        ids[p.page_id] = row["id"]
    return ids


_NOTION_ID = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


def prune_stale_tasks(conn, live_ids: list[str]) -> int:
    """Forget cached tasks that no longer exist in Notion, so they cannot compete with real ones.

    A task that a commitment or a match still points at is kept. Only real Notion ids are considered.
    """
    return conn.execute(
        f"""DELETE FROM notion_tasks WHERE notion_page_id ~ '{_NOTION_ID}' AND notion_page_id <> ALL(%s)
            AND NOT EXISTS (SELECT 1 FROM commitments c WHERE c.notion_page_id = notion_tasks.notion_page_id)
            AND NOT EXISTS (SELECT 1 FROM task_matches m WHERE m.notion_page_id = notion_tasks.notion_page_id)""",
        (live_ids,)).rowcount


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
        removed = prune_stale_tasks(conn, [t.page_id for t in tasks])
    return {"tasks": len(tasks), "projects": len(project_ids), "people": len(person_ids), "removed": removed}


if __name__ == "__main__":
    print(sync())
