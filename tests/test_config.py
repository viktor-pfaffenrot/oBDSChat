from pathlib import Path

import pytest

from backend.config import (
    DEFAULT_OPENAI_MODEL,
    OPENAI_BASE_URL,
    REQUESTY_BASE_URL,
    REQUESTY_POLICY_ROUTE,
    LlmProvider,
    Settings,
    load_settings,
)


def test_load_settings_uses_default_openai_endpoint() -> None:
    settings = load_settings({"OPENAI_API_KEY": " secret "})
    endpoint = settings.resolve_llm_endpoint()

    assert settings.llm_provider is LlmProvider.OPENAI
    assert settings.openai_api_key == "secret"
    assert settings.openai_model == DEFAULT_OPENAI_MODEL
    assert endpoint.provider is LlmProvider.OPENAI
    assert endpoint.base_url == OPENAI_BASE_URL
    assert endpoint.route == DEFAULT_OPENAI_MODEL
    assert "secret" not in repr(settings)
    assert "secret" not in repr(endpoint)


def test_requesty_provider_resolves_requesty_endpoint() -> None:
    settings = load_settings(
        {
            "LLM_PROVIDER": " Requesty ",
            "REQUESTY_API_KEY": " requesty-secret ",
            "REQUESTY_MODEL": "ignored/concrete-model",
        }
    )
    endpoint = settings.resolve_llm_endpoint()

    assert settings.llm_provider is LlmProvider.REQUESTY
    assert endpoint.provider is LlmProvider.REQUESTY
    assert endpoint.base_url == REQUESTY_BASE_URL
    assert endpoint.route == REQUESTY_POLICY_ROUTE
    assert endpoint.api_key == "requesty-secret"
    assert "requesty-secret" not in repr(settings)
    assert "requesty-secret" not in repr(endpoint)


def test_mounted_llm_api_key_takes_precedence(tmp_path: Path) -> None:
    api_key_file = tmp_path / "llm_api_key"
    api_key_file.write_text(" mounted-secret\n", encoding="utf-8")
    settings = load_settings(
        {
            "LLM_PROVIDER": "requesty",
            "REQUESTY_API_KEY": "environment-secret",
            "LLM_API_KEY_FILE": str(api_key_file),
        }
    )

    endpoint = settings.resolve_llm_endpoint()

    assert endpoint.api_key == "mounted-secret"
    assert "mounted-secret" not in repr(settings)
    assert "mounted-secret" not in repr(endpoint)


def test_missing_llm_api_key_file_is_reported(tmp_path: Path) -> None:
    settings = load_settings({"LLM_API_KEY_FILE": str(tmp_path / "missing-secret")})

    with pytest.raises(RuntimeError, match="LLM API key file not found"):
        settings.resolve_llm_endpoint()


@pytest.mark.parametrize(
    ("environment", "missing_variable"),
    [
        ({}, "OPENAI_API_KEY"),
        ({"LLM_PROVIDER": "requesty"}, "REQUESTY_API_KEY"),
    ],
)
def test_selected_provider_requires_its_api_key(
    environment: dict[str, str],
    missing_variable: str,
) -> None:
    settings = load_settings(environment)

    with pytest.raises(RuntimeError, match=missing_variable):
        settings.resolve_llm_endpoint()


def test_postgres_uri_uses_encoded_environment_values() -> None:
    settings = load_settings(
        {
            "OBDSCHAT_DB_USER": "db user",
            "OBDSCHAT_DB_PASSWORD": "p@ss/word",
            "OBDSCHAT_DB_HOST": "db.internal",
            "OBDSCHAT_DB_PORT": "5433",
            "OBDSCHAT_DB_NAME": "oBDS chat",
        }
    )

    assert settings.postgres_uri == (
        "postgresql://db%20user:p%40ss%2Fword@db.internal:5433/oBDS%20chat"
    )
    assert "p@ss/word" not in repr(settings)


def test_postgres_uri_reads_password_file(tmp_path) -> None:
    secret_directory = tmp_path / "config" / "secrets"
    secret_directory.mkdir(parents=True)
    password_file = secret_directory / "obdschat_db_password.txt"
    password_file.write_text(" file-secret\n", encoding="utf-8")
    settings = load_settings(
        {
            "OBDSCHAT_BASE_DIR": str(tmp_path),
            "OBDSCHAT_DB_USER": "obdschat",
            "OBDSCHAT_DB_NAME": "obdschat",
        }
    )

    assert settings.postgres_uri == (
        "postgresql://obdschat:file-secret@localhost:5432/obdschat"
    )


def test_postgres_uri_reports_missing_database_settings() -> None:
    settings = load_settings({})

    with pytest.raises(
        RuntimeError,
        match="OBDSCHAT_DB_USER, OBDSCHAT_DB_NAME",
    ):
        _ = settings.postgres_uri


def test_settings_loads_database_values_from_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database_variables = (
        "OBDSCHAT_BASE_DIR",
        "OBDSCHAT_DB_HOST",
        "OBDSCHAT_DB_PORT",
        "OBDSCHAT_DB_NAME",
        "OBDSCHAT_DB_USER",
        "OBDSCHAT_DB_PASSWORD",
        "OBDSCHAT_DB_PASSWORD_FILE",
    )
    for variable in database_variables:
        monkeypatch.delenv(variable, raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        """OBDSCHAT_DB_HOST=dotenv-db
OBDSCHAT_DB_PORT=5544
OBDSCHAT_DB_NAME=dotenv-name
OBDSCHAT_DB_USER=dotenv-user
OBDSCHAT_DB_PASSWORD=dotenv-password
""",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.postgres_uri == (
        "postgresql://dotenv-user:dotenv-password@dotenv-db:5544/dotenv-name"
    )
