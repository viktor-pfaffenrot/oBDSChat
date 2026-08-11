from pathlib import Path

import pytest

from backend.config import DEFAULT_OPENAI_MODEL, Settings, load_settings


def test_load_settings_uses_default_model() -> None:
    settings = load_settings({"OPENAI_API_KEY": " secret "})

    assert settings.openai_api_key == "secret"
    assert settings.openai_model == DEFAULT_OPENAI_MODEL
    assert "secret" not in repr(settings)


def test_missing_api_key_fails_when_required() -> None:
    settings = load_settings({})

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        settings.require_openai_api_key()


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
