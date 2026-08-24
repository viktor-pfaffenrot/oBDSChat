"""Tests for deployable container configuration."""

from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _compose_config() -> dict[str, Any]:
    compose_path = PROJECT_ROOT / "docker-compose.yaml"
    return yaml.safe_load(compose_path.read_text(encoding="utf-8"))


def test_compose_loads_runtime_configuration_from_dotenv() -> None:
    services = _compose_config()["services"]

    assert set(services) == {
        "obdschat-db",
        "source-sync",
        "backend",
        "frontend",
    }
    for service_name in ("obdschat-db", "source-sync", "backend"):
        assert services[service_name]["env_file"] == ".env"
        assert "environment" not in services[service_name]
    assert services["frontend"]["environment"] == {"BACKEND_URL": "http://backend:8000"}


def test_compose_mounts_secrets_only_where_required() -> None:
    config = _compose_config()
    services = config["services"]

    assert set(config["secrets"]) == {"obdschat_db_password", "llm_api_key"}
    assert services["obdschat-db"]["secrets"] == ["obdschat_db_password"]
    assert services["source-sync"]["secrets"] == ["obdschat_db_password"]
    assert services["backend"]["secrets"] == [
        "obdschat_db_password",
        "llm_api_key",
    ]
    assert "secrets" not in services["frontend"]


def test_compose_orders_source_sync_before_applications() -> None:
    services = _compose_config()["services"]

    assert services["source-sync"]["depends_on"]["obdschat-db"]["condition"] == (
        "service_healthy"
    )
    assert services["backend"]["depends_on"]["source-sync"]["condition"] == (
        "service_completed_successfully"
    )
    assert services["frontend"]["depends_on"]["backend"]["condition"] == (
        "service_healthy"
    )


def test_source_sync_shares_xsd_volume_with_backend() -> None:
    services = _compose_config()["services"]

    assert services["source-sync"]["command"] == [
        "python",
        "-m",
        "scripts.sync_sources",
    ]
    assert services["source-sync"]["volumes"] == ["xsd-data:/app/data/xsd"]
    assert services["backend"]["volumes"] == ["xsd-data:/app/data/xsd:ro"]


def test_compose_uses_non_conflicting_default_ports() -> None:
    services = _compose_config()["services"]

    assert services["obdschat-db"]["ports"] == [
        "127.0.0.1:${OBDSCHAT_DB_PUBLISHED_PORT:-55434}:5432"
    ]
    assert services["backend"]["ports"] == ["${OBDSCHAT_BACKEND_PORT:-18000}:8000"]
    assert services["frontend"]["ports"] == ["${OBDSCHAT_FRONTEND_PORT:-17860}:7860"]


def test_dockerfiles_install_only_their_dependency_group() -> None:
    backend_dockerfile = (PROJECT_ROOT / "Dockerfile.backend").read_text(
        encoding="utf-8"
    )
    frontend_dockerfile = (PROJECT_ROOT / "Dockerfile.frontend").read_text(
        encoding="utf-8"
    )

    assert "--group backend" in backend_dockerfile
    assert "--group frontend" not in backend_dockerfile
    assert "--group frontend" in frontend_dockerfile
    assert "--group backend" not in frontend_dockerfile
