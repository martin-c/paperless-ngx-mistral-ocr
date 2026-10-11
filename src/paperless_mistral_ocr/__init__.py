"""Paperless-ngx parser plugin that OCRs documents with Mistral Document AI.

The package is split into a paperless-independent core (``config``, ``client``,
``content``, ``layer``) and a thin adapter (``parser``) that implements the
``ParserProtocol`` expected by paperless-ngx 3.3.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("paperless-ngx-mistral-ocr")
except PackageNotFoundError:  # pragma: no cover - running from a source checkout
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
