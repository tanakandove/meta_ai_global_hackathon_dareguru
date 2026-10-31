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
class NotionTask:
    page_id: str
    title: str
    owner: str
    due: date | None
    status: str
    project: str
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
    return NotionTask(
        page_id=page["id"],
        title=_plain(props["Name"]["title"]),
        owner=_plain(props["Owner"]["rich_text"]),
        due=date.fromisoformat(due[:10]) if due else None,
        status=(props["Status"]["select"] or {}).get("name", ""),
        project=_plain(props["Project"]["rich_text"]),
        evidence=_plain(props["Evidence"]["rich_text"]),
        last_edited=datetime.fromisoformat(page["last_edited_time"]),
    )


def fetch_tasks() -> list[NotionTask]:
    tasks: list[NotionTask] = []
    cursor = None
    while True:
        resp = client().data_sources.query(data_source_id(), **({"start_cursor": cursor} if cursor else {}))
        tasks.extend(parse_task(p) for p in resp["results"])
        if not resp["has_more"]:
            return tasks
        cursor = resp["next_cursor"]
