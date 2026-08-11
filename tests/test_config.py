import pytest

from backend.config import DEFAULT_OPENAI_MODEL, Settings, load_settings


def test_load_settings_uses_default_model() -> None:
    settings = load_settings({"OPENAI_API_KEY": " secret "})

    assert settings.openai_api_key == "secret"
    assert settings.openai_model == DEFAULT_OPENAI_MODEL
    assert "secret" not in repr(settings)


def test_missing_api_key_fails_when_required() -> None:
    settings = Settings()

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        settings.require_openai_api_key()
