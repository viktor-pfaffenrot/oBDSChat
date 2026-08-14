"""PostgreSQL connection setup for backend services."""

from typing import Any

import psycopg
from psycopg import Connection

from backend.config import load_settings


def connect_database() -> Connection[tuple[Any, ...]]:
    """Open a PostgreSQL connection using validated backend settings."""
    return psycopg.connect(load_settings().postgres_uri)
