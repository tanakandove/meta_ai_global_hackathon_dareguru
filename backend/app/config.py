import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql:///projectpulse")
NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
NOTION_TASKS_DB_ID = os.environ.get("NOTION_TASKS_DB_ID", "")
MODEL_API_KEY = os.environ.get("MODEL_API_KEY", "")
MODEL_BASE_URL = os.environ.get("MODEL_BASE_URL", "https://api.meta.ai/v1")
REASONING_MODEL = os.environ.get("REASONING_MODEL", "muse-spark-1.3")
