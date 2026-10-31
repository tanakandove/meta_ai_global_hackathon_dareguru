"""Thin wrapper over the Notion API for the tasks database.

Notion keeps schema and rows on a data source rather than the database itself,
so every call resolves the database's data source first.
"""
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache

from notion_client import Client

from app import config


@dataclass(frozen=True)
class NotionProject:
    page_id: str
    name: str
    aliases: list[str]
    client: str


@dataclass(frozen=True)
class NotionTask:
    page_id: str
    title: str
    owner: str
    due: date | None
    status: str
    project: str
    project_page_id: str | None
    evidence: str
    last_edited: datetime


@lru_cache
def client() -> Client:
    return Client(auth=config.NOTION_TOKEN)


@lru_cache
def data_source_id() -> str:
    db = client().databases.retrieve(config.NOTION_TASKS_DB_ID)
    return db["data_sources"][0]["id"]


def _plain(rich: list[dict]) -> str:
    return "".join(part["plain_text"] for part in rich)


def parse_task(page: dict) -> NotionTask:
    props = page["properties"]
    due = (props["Due"]["date"] or {}).get("start")
    # A task links to at most one project. has_more is not checked: a single-property
    # relation to a project never approaches the 25-reference page limit.
    linked = props["Project Link"]["relation"]
    return NotionTask(
        page_id=page["id"],
        title=_plain(props["Name"]["title"]),
        owner=_plain(props["Owner"]["rich_text"]),
        due=date.fromisoformat(due[:10]) if due else None,
        status=(props["Status"]["select"] or {}).get("name", ""),
        project=_plain(props["Project"]["rich_text"]),
        project_page_id=linked[0]["id"] if linked else None,
        evidence=_plain(props["Evidence"]["rich_text"]),
        last_edited=datetime.fromisoformat(page["last_edited_time"]),
    )


@lru_cache
def projects_data_source_id() -> str:
    db = client().databases.retrieve(config.NOTION_PROJECTS_DB_ID)
    return db["data_sources"][0]["id"]


def parse_project(page: dict) -> NotionProject:
    props = page["properties"]
    aliases = [a.strip() for a in _plain(props["Aliases"]["rich_text"]).split(",") if a.strip()]
    return NotionProject(
        page_id=page["id"],
        name=_plain(props["Name"]["title"]),
        aliases=aliases,
        client=_plain(props["Client"]["rich_text"]),
    )


def _query_all(ds_id: str) -> list[dict]:
    pages: list[dict] = []
    cursor = None
    while True:
        resp = client().data_sources.query(ds_id, **({"start_cursor": cursor} if cursor else {}))
        pages.extend(resp["results"])
        if not resp["has_more"]:
            return pages
        cursor = resp["next_cursor"]


def fetch_projects() -> list[NotionProject]:
    return [parse_project(p) for p in _query_all(projects_data_source_id())]


def fetch_tasks() -> list[NotionTask]:
    return [parse_task(p) for p in _query_all(data_source_id())]
