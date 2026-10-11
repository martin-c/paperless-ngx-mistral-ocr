"""Plugin configuration read from ``PAPERLESS_MISTRAL_*`` environment variables."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

ENV_PREFIX = "PAPERLESS_MISTRAL_"
DEFAULT_MODEL = "mistral-ocr-latest"
DEFAULT_ENDPOINT = "https://api.mistral.ai/v1/ocr"
DEFAULT_MAX_PAGES = 50
DEFAULT_TIMEOUT = 120.0


class ConfigError(ValueError):
    """Raised when an environment variable holds a value that cannot be used."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Resolved plugin settings.

    Attributes:
        api_key: Mistral API key. ``None`` or empty means the plugin is inactive.
        model: Mistral OCR model name.
        endpoint: URL of the OCR endpoint.
        max_pages: Maximum number of pages sent to the API per document
            (``0`` means no limit).
        timeout: HTTP timeout in seconds for a single OCR request.
    """

    api_key: str | None = None
    model: str = DEFAULT_MODEL
    endpoint: str = DEFAULT_ENDPOINT
    max_pages: int = DEFAULT_MAX_PAGES
    timeout: float = DEFAULT_TIMEOUT

    @property
    def is_configured(self) -> bool:
        """Return True when an API key is present and the parser may activate."""
        return bool(self.api_key)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """Build settings from environment variables.

        Args:
            environ: Mapping to read from; defaults to ``os.environ``.

        Returns:
            A populated ``Settings`` instance.

        Raises:
            ConfigError: If a numeric variable is not a valid number.
        """
        env = os.environ if environ is None else environ
        api_key = env.get(f"{ENV_PREFIX}API_KEY", "").strip() or None
        model = env.get(f"{ENV_PREFIX}MODEL", "").strip() or DEFAULT_MODEL
        endpoint = env.get(f"{ENV_PREFIX}ENDPOINT", "").strip() or DEFAULT_ENDPOINT
        max_pages = _read_int(env, f"{ENV_PREFIX}MAX_PAGES", DEFAULT_MAX_PAGES)
        timeout = _read_float(env, f"{ENV_PREFIX}TIMEOUT", DEFAULT_TIMEOUT)
        return cls(
            api_key=api_key,
            model=model,
            endpoint=endpoint,
            max_pages=max_pages,
            timeout=timeout,
        )


def _read_int(env: Mapping[str, str], name: str, default: int) -> int:
    """Read a non-negative integer variable, falling back to ``default``."""
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as e:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from e
    if value < 0:
        raise ConfigError(f"{name} must not be negative, got {value}")
    return value


def _read_float(env: Mapping[str, str], name: str, default: float) -> float:
    """Read a positive float variable, falling back to ``default``."""
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as e:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from e
    if value <= 0:
        raise ConfigError(f"{name} must be positive, got {value}")
    return value
