"""Notion sync edge cases. Each test runs in a transaction that is rolled back, so no real data is touched."""
import psycopg
import pytest
from psycopg.rows import dict_row

from app import config
from app.notion_api import NotionProject
from app.notion_sync import _sync_projects, prune_stale_tasks

PAGE = "aaaaaaaa-0000-4000-8000-000000000001"
OTHER = "bbbbbbbb-0000-4000-8000-000000000002"
GONE = "cccccccc-0000-4000-8000-000000000003"
KEPT = "dddddddd-0000-4000-8000-000000000004"


@pytest.fixture
def conn():
    c = psycopg.connect(config.DATABASE_URL, row_factory=dict_row)
    yield c
    c.rollback()
    c.close()


def project(page_id, name):
    return NotionProject(page_id, name, ["alias"], "Client")


def test_renaming_a_project_in_notion_renames_it_instead_of_adding_another(conn):
    first = _sync_projects(conn, [project(PAGE, "stest Old name")])[PAGE]
    again = _sync_projects(conn, [project(PAGE, "stest New name")])[PAGE]
    assert again == first
    rows = conn.execute("SELECT name FROM projects WHERE notion_page_id = %s", (PAGE,)).fetchall()
    assert [r["name"] for r in rows] == ["stest New name"]


def test_a_project_stored_before_page_ids_were_kept_is_adopted_by_name(conn):
    legacy = conn.execute("INSERT INTO projects (name) VALUES ('stest Legacy') RETURNING id").fetchone()["id"]
    assert _sync_projects(conn, [project(OTHER, "stest Legacy")])[OTHER] == legacy
    assert conn.execute("SELECT notion_page_id FROM projects WHERE id = %s", (legacy,)).fetchone()["notion_page_id"] == OTHER


def test_tasks_deleted_in_notion_are_forgotten_unless_something_still_points_at_them(conn):
    for page in (GONE, KEPT, "stest-not-a-notion-id"):
        conn.execute("INSERT INTO notion_tasks (notion_page_id, title, notion_last_edited) VALUES (%s, 'stale', now())", (page,))
    conn.execute("INSERT INTO commitments (deliverable, notion_page_id) VALUES ('still tracked', %s)", (KEPT,))

    prune_stale_tasks(conn, live_ids=[])
    left = {r["notion_page_id"] for r in conn.execute("SELECT notion_page_id FROM notion_tasks WHERE title = 'stale'")}
    assert GONE not in left          # deleted in Notion, nothing refers to it
    assert KEPT in left             # a commitment still refers to it
    assert "stest-not-a-notion-id" in left  # not a real Notion id (such as test data): never touched
