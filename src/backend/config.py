"""Backend configuration."""

from collections.abc import Mapping
from pathlib import Path
from typing import Final
from urllib.parse import quote

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

DEFAULT_OPENAI_MODEL: Final = "gpt-5.6-terra"
DEFAULT_DB_PASSWORD_FILE: Final = (
    Path("config") / "secrets" / "obdschat_db_password.txt"
)


class Settings(BaseSettings):
    """Validated application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        frozen=True,
        validate_by_name=True,
    )

    openai_api_key: str | None = Field(
        default=None,
        repr=False,
        validation_alias="OPENAI_API_KEY",
    )
    openai_model: str = Field(
        default=DEFAULT_OPENAI_MODEL,
        validation_alias="OPENAI_MODEL",
    )
    base_dir: Path = Field(
        default_factory=Path.cwd,
        validation_alias="OBDSCHAT_BASE_DIR",
    )
    db_user: str | None = Field(
        default=None,
        validation_alias="OBDSCHAT_DB_USER",
    )
    db_password: SecretStr | None = Field(
        default=None,
        repr=False,
        validation_alias="OBDSCHAT_DB_PASSWORD",
    )
    db_password_file: Path | None = Field(
        default=None,
        repr=False,
        validation_alias="OBDSCHAT_DB_PASSWORD_FILE",
    )
    db_host: str = Field(
        default="localhost",
        validation_alias="OBDSCHAT_DB_HOST",
    )
    db_port: int = Field(
        default=5432,
        ge=1,
        le=65535,
        validation_alias="OBDSCHAT_DB_PORT",
    )
    db_name: str | None = Field(
        default=None,
        validation_alias="OBDSCHAT_DB_NAME",
    )

    @field_validator("openai_api_key", mode="after")
    @classmethod
    def normalize_api_key(cls, value: str | None) -> str | None:
        """Normalize an optional API key without exposing it."""
        if value is None:
            return None
        return value.strip() or None

    @field_validator("openai_model", mode="after")
    @classmethod
    def validate_model(cls, value: str) -> str:
        """Normalize and validate the configured model name."""
        model = value.strip()
        if not model:
            raise ValueError("OPENAI_MODEL must not be empty")
        return model

    @field_validator("db_user", "db_name", mode="after")
    @classmethod
    def normalize_optional_database_value(cls, value: str | None) -> str | None:
        """Normalize optional database values."""
        if value is None:
            return None
        return value.strip() or None

    @field_validator("db_host", mode="after")
    @classmethod
    def validate_database_host(cls, value: str) -> str:
        """Normalize and validate the database host."""
        host = value.strip()
        if not host:
            raise ValueError("OBDSCHAT_DB_HOST must not be empty")
        return host

    @field_validator("base_dir", mode="after")
    @classmethod
    def resolve_base_directory(cls, value: Path) -> Path:
        """Resolve the application base directory."""
        return value.expanduser().resolve()

    def require_openai_api_key(self) -> str:
        """Return the configured API key or fail before making a request."""
        if self.openai_api_key is None:
            raise RuntimeError("OPENAI_API_KEY is required by the backend")
        return self.openai_api_key

    def require_database_password(self) -> str:
        """Return the configured database password."""
        if self.db_password is not None:
            password = self.db_password.get_secret_value().strip()
            if password:
                return password

        password_file = (self.db_password_file or DEFAULT_DB_PASSWORD_FILE).expanduser()
        if not password_file.is_absolute():
            password_file = self.base_dir / password_file

        try:
            password = password_file.read_text(encoding="utf-8").strip()
        except FileNotFoundError as error:
            raise RuntimeError(
                "Database password is required. Set OBDSCHAT_DB_PASSWORD or "
                "OBDSCHAT_DB_PASSWORD_FILE."
            ) from error

        if not password:
            raise RuntimeError(f"Database password file is empty: {password_file}")
        return password

    @property
    def postgres_uri(self) -> str:
        """Build the PostgreSQL URI from validated settings."""
        if self.db_user is None or self.db_name is None:
            missing_variables = [
                variable
                for variable, value in (
                    ("OBDSCHAT_DB_USER", self.db_user),
                    ("OBDSCHAT_DB_NAME", self.db_name),
                )
                if value is None
            ]
            missing = ", ".join(missing_variables)
            raise RuntimeError(f"Database settings missing: {missing}")

        user = quote(self.db_user, safe="")
        password = quote(self.require_database_password(), safe="")
        database = quote(self.db_name, safe="")
        return (
            f"postgresql://{user}:{password}@{self.db_host}:{self.db_port}/{database}"
        )


class _ExplicitSettings(Settings):
    """Settings variant that accepts only explicitly supplied values."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Disable ambient settings sources for deterministic mapping loads."""
        return (init_settings,)


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Load settings from process sources or an explicit environment mapping."""
    if environ is None:
        return Settings()
    return _ExplicitSettings.model_validate(environ)
