"""Tests for deterministic OpenAPI documentation generation."""

import json
from pathlib import Path

from backend.app import app
from scripts.export_openapi import (
    DEFAULT_OUTPUT_PATH,
    openapi_schema_is_current,
    serialize_openapi_schema,
    write_openapi_schema,
)


def test_serialized_openapi_schema_matches_fastapi() -> None:
    assert json.loads(serialize_openapi_schema()) == app.openapi()


def test_openapi_schema_generation_and_freshness(tmp_path: Path) -> None:
    output_path = tmp_path / "openapi.json"

    assert not openapi_schema_is_current(output_path)
    assert write_openapi_schema(output_path)
    assert openapi_schema_is_current(output_path)
    assert not write_openapi_schema(output_path)


def test_committed_openapi_schema_is_current() -> None:
    assert openapi_schema_is_current(DEFAULT_OUTPUT_PATH)
