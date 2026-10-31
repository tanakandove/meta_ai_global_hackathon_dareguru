"""M0.3: read the test tasks database and update one page.

Notion now keeps schema and rows on a data source, not the database itself.
"""
import json
import os

from dotenv import load_dotenv
from notion_client import Client

load_dotenv("../.env")

notion = Client(auth=os.environ["NOTION_TOKEN"])

db = notion.databases.retrieve(os.environ["NOTION_TASKS_DB_ID"])
ds_id = db["data_sources"][0]["id"]

ds = notion.data_sources.retrieve(ds_id)
print("data source:", ds_id)
print("properties:", json.dumps({k: v["type"] for k, v in ds["properties"].items()}, indent=2))

rows = notion.data_sources.query(ds_id)["results"]
print(len(rows), "rows")
for p in rows:
    print(p["id"], p["last_edited_time"], {k: v["type"] for k, v in p["properties"].items()} if not rows.index(p) else "")
