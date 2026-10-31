"""M0.3b: prove a write round-trips, then restore the original value."""
import os

from dotenv import load_dotenv
from notion_client import Client

load_dotenv("../.env")

notion = Client(auth=os.environ["NOTION_TOKEN"])
ds_id = notion.databases.retrieve(os.environ["NOTION_TASKS_DB_ID"])["data_sources"][0]["id"]


def text(prop):
    return "".join(t["plain_text"] for t in prop["rich_text"] or prop.get("title", []))


def row_view(p):
    pr = p["properties"]
    return {
        "name": "".join(t["plain_text"] for t in pr["Name"]["title"]),
        "owner": text(pr["Owner"]),
        "due": (pr["Due"]["date"] or {}).get("start"),
        "status": (pr["Status"]["select"] or {}).get("name"),
        "project": text(pr["Project"]),
        "evidence": text(pr["Evidence"]),
    }


rows = notion.data_sources.query(ds_id)["results"]
for p in rows:
    print(p["id"], row_view(p))

target = rows[0]
original = target["properties"]["Evidence"]["rich_text"]
notion.pages.update(target["id"], properties={"Evidence": {"rich_text": [{"text": {"content": "spike write ok"}}]}})
after = notion.pages.retrieve(target["id"])
print("after write:", row_view(after)["evidence"])
notion.pages.update(target["id"], properties={"Evidence": {"rich_text": [{"text": {"content": t["plain_text"]}} for t in original]}})
print("restored:", repr(row_view(notion.pages.retrieve(target["id"]))["evidence"]))
