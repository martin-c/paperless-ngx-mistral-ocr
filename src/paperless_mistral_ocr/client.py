"""Minimal HTTP client for the Mistral Document AI OCR endpoint.

The client sends a document as a base64 data URI and returns the decoded JSON
response. It retries on rate limiting (429), server errors (5xx) and transport
failures with exponential backoff, and raises ``MistralOcrError`` subclasses so
callers can fail loudly.
"""

from __future__ import annotations

import base64
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from paperless_mistral_ocr.config import Settings

logger = logging.getLogger(__name__)

RETRY_STATUS_CODES = frozenset({408, 409, 429, 500, 502, 503, 504})
DEFAULT_MAX_ATTEMPTS = 4
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 30.0
MAX_ERROR_BODY_CHARS = 500


class MistralOcrError(Exception):
    """Base class for all errors raised by the client."""


class MistralApiError(MistralOcrError):
    """The API answered with an unexpected HTTP status.

    Attributes:
        status_code: HTTP status code of the final response.
        body: Truncated response body for diagnostics.
    """

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body[:MAX_ERROR_BODY_CHARS]
        super().__init__(f"Mistral OCR API returned HTTP {status_code}: {self.body}")


class MistralTransportError(MistralOcrError):
    """The request could not be completed at the transport level."""


class MistralResponseError(MistralOcrError):
    """The API answered 2xx but the payload is not a usable OCR response."""


def to_data_uri(data: bytes, mime_type: str) -> str:
    """Encode raw bytes as a ``data:<mime>;base64,...`` URI."""
    return f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"


def page_range(max_pages: int) -> str | None:
    """Return the ``pages`` request value for a page cap, or None for no cap.

    Args:
        max_pages: Maximum number of pages to process; ``0`` disables the cap.

    Returns:
        A zero-based inclusive range string such as ``"0-49"``, or None.
    """
    if max_pages <= 0:
        return None
    return f"0-{max_pages - 1}"


def build_request_body(
    data_uri: str,
    mime_type: str,
    model: str,
    max_pages: int,
) -> dict[str, Any]:
    """Build the JSON body of an OCR request.

    Args:
        data_uri: The document encoded as a data URI.
        mime_type: MIME type of the document (decides document vs. image mode).
        model: Mistral OCR model name.
        max_pages: Page cap; ``0`` disables it.

    Returns:
        The request body as a JSON-serialisable dict.
    """
    if mime_type.startswith("image/"):
        document: dict[str, str] = {"type": "image_url", "image_url": data_uri}
    else:
        document = {"type": "document_url", "document_url": data_uri}
    body: dict[str, Any] = {
        "model": model,
        "document": document,
        "include_blocks": True,
        "table_format": "markdown",
        "confidence_scores_granularity": "block",
    }
    pages = page_range(max_pages)
    if pages is not None:
        body["pages"] = pages
    return body


def backoff_seconds(attempt: int, retry_after: str | None = None) -> float:
    """Compute the delay before retry number ``attempt`` (1-based).

    Args:
        attempt: Number of the attempt that just failed, starting at 1.
        retry_after: Value of a ``Retry-After`` header, if the server sent one.

    Returns:
        Seconds to sleep before the next attempt.
    """
    if retry_after:
        try:
            return min(max(float(retry_after), 0.0), BACKOFF_MAX_SECONDS)
        except ValueError:
            pass
    return min(BACKOFF_BASE_SECONDS * 2 ** (attempt - 1), BACKOFF_MAX_SECONDS)


class MistralOcrClient:
    """Synchronous client for ``POST /v1/ocr``.

    Args:
        settings: Plugin settings holding the API key, endpoint, model and
            page cap.
        transport: Optional ``httpx`` transport, used by tests to mock HTTP.
        sleep: Function used to wait between retries (injectable for tests).
        max_attempts: Total number of attempts per request.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        if not settings.api_key:
            raise MistralOcrError("Mistral API key is not configured")
        self._settings = settings
        self._transport = transport
        self._sleep = sleep
        self._max_attempts = max(1, max_attempts)

    def ocr_file(self, path: Path, mime_type: str) -> dict[str, Any]:
        """OCR a file on disk.

        Args:
            path: Path of a PDF or image file.
            mime_type: MIME type of the file.

        Returns:
            The decoded OCR response.
        """
        return self.ocr_bytes(Path(path).read_bytes(), mime_type)

    def ocr_bytes(self, data: bytes, mime_type: str) -> dict[str, Any]:
        """OCR an in-memory document.

        Args:
            data: Raw document bytes.
            mime_type: MIME type describing ``data``.

        Returns:
            The decoded OCR response (a dict with a ``pages`` list).

        Raises:
            MistralApiError: On a non-retryable or persistently failing HTTP status.
            MistralTransportError: When the request keeps failing at transport level.
            MistralResponseError: When the response is not a valid OCR payload.
        """
        body = build_request_body(
            to_data_uri(data, mime_type),
            mime_type,
            self._settings.model,
            self._settings.max_pages,
        )
        with httpx.Client(
            timeout=self._settings.timeout,
            transport=self._transport,
        ) as http:
            response = self._post_with_retries(http, body)
        return _decode_response(response)

    def _post_with_retries(
        self,
        http: httpx.Client,
        body: dict[str, Any],
    ) -> httpx.Response:
        """POST ``body`` and retry on transient failures."""
        headers = {
            "Authorization": f"Bearer {self._settings.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        last_error: MistralOcrError | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = http.post(
                    self._settings.endpoint,
                    json=body,
                    headers=headers,
                )
            except httpx.TransportError as e:
                last_error = MistralTransportError(f"{e.__class__.__name__}: {e}")
                retry_after = None
            else:
                if response.status_code < 400:
                    return response
                last_error = MistralApiError(response.status_code, response.text)
                if response.status_code not in RETRY_STATUS_CODES:
                    raise last_error
                retry_after = response.headers.get("Retry-After")
            if attempt >= self._max_attempts:
                break
            delay = backoff_seconds(attempt, retry_after)
            logger.warning(
                "Mistral OCR request failed (%s); retrying in %.1fs (attempt %d/%d)",
                last_error,
                delay,
                attempt,
                self._max_attempts,
            )
            self._sleep(delay)
        assert last_error is not None
        raise last_error


def _decode_response(response: httpx.Response) -> dict[str, Any]:
    """Decode and sanity-check a successful HTTP response."""
    try:
        payload = response.json()
    except ValueError as e:
        raise MistralResponseError("Mistral OCR API returned non-JSON body") from e
    if not isinstance(payload, dict) or not isinstance(payload.get("pages"), list):
        raise MistralResponseError("Mistral OCR response has no 'pages' list")
    return payload
