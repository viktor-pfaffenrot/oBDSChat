"""Tests for shared public API response contracts."""

import ast
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app import QueryResponse as BackendQueryResponse
from backend.xsd import SchemaEnumValue as BackendSchemaEnumValue
from backend.xsd import SchemaSourceLine as BackendSchemaSourceLine
from frontend.api import QueryResponse as FrontendQueryResponse
from frontend.api import XsdEvidenceResponse as FrontendXsdEvidenceResponse
from obdschat_api.models import (
    QueryResponse,
    SchemaEnumValue,
    SchemaSourceLine,
    SourceReference,
    XsdEvidenceResponse,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_backend_and_frontend_use_shared_response_models() -> None:
    assert BackendQueryResponse is QueryResponse
    assert FrontendQueryResponse is QueryResponse
    assert FrontendXsdEvidenceResponse is XsdEvidenceResponse
    assert BackendSchemaEnumValue is SchemaEnumValue
    assert BackendSchemaSourceLine is SchemaSourceLine


def test_shared_response_models_preserve_validation_constraints() -> None:
    with pytest.raises(ValidationError):
        SourceReference(
            title="Leitfaden",
            url="https://example.test/source",
            source_type="umsetzungsleitfaden",
            source_id=0,
        )

    with pytest.raises(ValidationError):
        SchemaSourceLine(number=0, content="", highlighted=False)


def test_shared_contract_package_is_dependency_light() -> None:
    models_path = PROJECT_ROOT / "src" / "obdschat_api" / "models.py"
    syntax_tree = ast.parse(models_path.read_text(encoding="utf-8"))
    imported_packages: set[str] = set()
    for node in ast.walk(syntax_tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_packages.add(node.module.split(".", maxsplit=1)[0])
        if isinstance(node, ast.Import):
            imported_packages.update(
                alias.name.split(".", maxsplit=1)[0] for alias in node.names
            )
    assert imported_packages == {"pydantic", "typing"}


def test_shared_contract_package_is_in_project_wheel() -> None:
    pyproject = tomllib.loads(
        (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    wheel_packages = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    assert "src/obdschat_api" in wheel_packages
