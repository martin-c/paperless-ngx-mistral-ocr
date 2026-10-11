import pytest

from paperless_mistral_ocr.config import (
    DEFAULT_ENDPOINT,
    DEFAULT_MAX_PAGES,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT,
    ConfigError,
    Settings,
)


def test_defaults_without_key():
    settings = Settings.from_env({})
    assert settings.api_key is None
    assert not settings.is_configured
    assert settings.model == DEFAULT_MODEL
    assert settings.endpoint == DEFAULT_ENDPOINT
    assert settings.max_pages == DEFAULT_MAX_PAGES
    assert settings.timeout == DEFAULT_TIMEOUT


def test_reads_all_variables():
    settings = Settings.from_env(
        {
            "PAPERLESS_MISTRAL_API_KEY": " key ",
            "PAPERLESS_MISTRAL_MODEL": "mistral-ocr-2505",
            "PAPERLESS_MISTRAL_MAX_PAGES": "10",
            "PAPERLESS_MISTRAL_TIMEOUT": "30.5",
            "PAPERLESS_MISTRAL_ENDPOINT": "https://example.test/ocr",
        }
    )
    assert settings.is_configured
    assert settings.api_key == "key"
    assert settings.model == "mistral-ocr-2505"
    assert settings.max_pages == 10
    assert settings.timeout == 30.5
    assert settings.endpoint == "https://example.test/ocr"


def test_blank_values_fall_back_to_defaults():
    settings = Settings.from_env(
        {"PAPERLESS_MISTRAL_API_KEY": "   ", "PAPERLESS_MISTRAL_MAX_PAGES": ""}
    )
    assert not settings.is_configured
    assert settings.max_pages == DEFAULT_MAX_PAGES


@pytest.mark.parametrize(
    "name,value",
    [
        ("PAPERLESS_MISTRAL_MAX_PAGES", "ten"),
        ("PAPERLESS_MISTRAL_MAX_PAGES", "-1"),
        ("PAPERLESS_MISTRAL_TIMEOUT", "0"),
        ("PAPERLESS_MISTRAL_TIMEOUT", "soon"),
    ],
)
def test_invalid_numbers_raise(name, value):
    with pytest.raises(ConfigError):
        Settings.from_env({name: value})
