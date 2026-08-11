"""Backend configuration."""

import os
from collections.abc import Mapping
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_OPENAI_MODEL: Final = "gpt-5.6-terra"


class Settings(BaseModel):
    """Validated backend settings."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    openai_api_key: str | None = Field(default=None, repr=False)
    openai_model: str = DEFAULT_OPENAI_MODEL

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

    def require_openai_api_key(self) -> str:
        """Return the configured API key or fail before making a request."""
        if self.openai_api_key is None:
            raise RuntimeError("OPENAI_API_KEY is required by the backend")
        return self.openai_api_key


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Load backend settings from an environment mapping."""
    source = os.environ if environ is None else environ
    model = source.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
    return Settings(
        openai_api_key=source.get("OPENAI_API_KEY"),
        openai_model=model,
    )
