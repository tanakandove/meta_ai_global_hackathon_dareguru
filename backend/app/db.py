from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from app import config

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


@contextmanager
def connect():
    """Yield a connection that commits on success and rolls back on error."""
    with psycopg.connect(config.DATABASE_URL, row_factory=dict_row) as conn:
        yield conn


def init_schema() -> None:
    with connect() as conn:
        conn.execute(SCHEMA_PATH.read_text())


if __name__ == "__main__":
    init_schema()
    print("schema applied")
