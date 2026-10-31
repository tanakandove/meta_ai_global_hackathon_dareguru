"""Cache the Notion tasks database into Postgres.

Idempotent: running it repeatedly converges on the current Notion state.
Tasks deleted in Notion are not removed here because commitments may reference them.
"""
from app import db, notion_api
from app.names import cluster_names


def _upsert_named(conn, table: str, canonical: dict[str, str]) -> dict[str, int]:
    """Upsert canonical names (recording variant spellings as aliases); return name -> id."""
    aliases: dict[str, set[str]] = {}
    for raw, canon in canonical.items():
        aliases.setdefault(canon, set())
        if raw != canon:
            aliases[canon].add(raw)

    ids: dict[str, int] = {}
    for canon, alias_set in aliases.items():
        row = conn.execute(
            f"""INSERT INTO {table} (name, aliases) VALUES (%s, %s)
                ON CONFLICT (name) DO UPDATE
                SET aliases = (SELECT ARRAY(SELECT DISTINCT unnest({table}.aliases || EXCLUDED.aliases)))
                RETURNING id""",
            (canon, sorted(alias_set)),
        ).fetchone()
        ids[canon] = row["id"]
    return ids


def sync() -> dict[str, int]:
    tasks = notion_api.fetch_tasks()
    project_map = cluster_names([t.project for t in tasks])
    person_map = cluster_names([t.owner for t in tasks])

    with db.connect() as conn:
        project_ids = _upsert_named(conn, "projects", project_map)
        person_ids = _upsert_named(conn, "people", person_map)

        for t in tasks:
            project = project_map.get(t.project)
            owner = person_map.get(t.owner)
            conn.execute(
                """INSERT INTO notion_tasks
                       (notion_page_id, title, owner_name, due_date, status, project_name,
                        evidence_text, person_id, project_id, notion_last_edited, synced_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                   ON CONFLICT (notion_page_id) DO UPDATE SET
                       title = EXCLUDED.title, owner_name = EXCLUDED.owner_name,
                       due_date = EXCLUDED.due_date, status = EXCLUDED.status,
                       project_name = EXCLUDED.project_name, evidence_text = EXCLUDED.evidence_text,
                       person_id = EXCLUDED.person_id, project_id = EXCLUDED.project_id,
                       notion_last_edited = EXCLUDED.notion_last_edited, synced_at = now()""",
                (t.page_id, t.title, owner or t.owner, t.due, t.status, project or t.project,
                 t.evidence, person_ids.get(owner), project_ids.get(project), t.last_edited),
            )
    return {"tasks": len(tasks), "projects": len(project_ids), "people": len(person_ids)}


if __name__ == "__main__":
    print(sync())
