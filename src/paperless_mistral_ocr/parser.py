"""Paperless-ngx ``ParserProtocol`` adapter for Mistral OCR.

Only this module talks to paperless/Django, and it imports those modules
lazily inside methods so the package can be imported and tested without
paperless installed. The class shape mirrors paperless' built-in
``paperless.parsers.remote.RemoteDocumentParser`` (v3.3.0).
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

from paperless_mistral_ocr import __version__
from paperless_mistral_ocr.client import MistralOcrClient, MistralOcrError
from paperless_mistral_ocr.config import ConfigError, Settings
from paperless_mistral_ocr.content import low_confidence_blocks, response_to_text
from paperless_mistral_ocr.layer import build_archive, document_payload

if TYPE_CHECKING:
    import datetime
    from types import TracebackType

    from paperless.parsers import MetadataEntry, ParserContext

logger = logging.getLogger("paperless.parsing.mistral")

SUPPORTED_MIME_TYPES: dict[str, str] = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/tiff": ".tiff",
    "image/bmp": ".bmp",
    "image/gif": ".gif",
    "image/webp": ".webp",
}
SCORE = 20


class MistralOcrParser:
    """Parse PDFs and images by sending them to Mistral Document AI.

    The parser produces the document content from the OCR markdown and an
    archive PDF consisting of the original pages with an invisible text layer.
    Any failure raises ``documents.parsers.ParseError`` so the consume task
    fails loudly and can be retried.
    """

    name: str = "Mistral Document AI OCR"
    version: str = __version__
    author: str = "Martin Clemons"
    url: str = "https://github.com/martin-c/paperless-ngx-mistral-ocr"
    uses_remote_service: bool = True

    @classmethod
    def supported_mime_types(cls) -> dict[str, str]:
        """Return the MIME types this parser handles, mapped to extensions."""
        return SUPPORTED_MIME_TYPES

    @classmethod
    def score(
        cls,
        mime_type: str,
        filename: str,
        path: Path | None = None,
    ) -> int | None:
        """Return 20 when an API key is configured, otherwise None (decline)."""
        if mime_type not in SUPPORTED_MIME_TYPES:
            return None
        try:
            settings = Settings.from_env()
        except ConfigError as e:
            logger.warning("Mistral OCR parser disabled: %s", e)
            return None
        return SCORE if settings.is_configured else None

    @property
    def can_produce_archive(self) -> bool:
        """Always True: an archive PDF with a text layer can be produced."""
        return True

    @property
    def requires_pdf_rendition(self) -> bool:
        """Always False: browsers can display PDFs and images natively."""
        return False

    def __init__(self, logging_group: object = None) -> None:
        from django.conf import settings as django_settings

        django_settings.SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
        scratch_dir = django_settings.SCRATCH_DIR
        self._tempdir = Path(
            tempfile.mkdtemp(prefix="paperless-mistral-", dir=scratch_dir),
        )
        self._logging_group = logging_group
        self._log = logging.LoggerAdapter(logger, {"group": logging_group})
        self._text: str | None = None
        self._archive_path: Path | None = None

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self._log.debug("Cleaning up temporary directory %s", self._tempdir)
        shutil.rmtree(self._tempdir, ignore_errors=True)

    def configure(self, context: ParserContext) -> None:
        """Accept the ingestion context (unused by this parser)."""

    def parse(
        self,
        document_path: Path,
        mime_type: str,
        *,
        produce_archive: bool = True,
    ) -> None:
        """OCR the document and populate text and archive state.

        When ``produce_archive`` is False for a PDF, paperless has decided the
        file is born-digital; the remote call is skipped and the text is
        extracted locally with pdftotext, mirroring the built-in remote parser.

        Raises:
            documents.parsers.ParseError: If configuration, the API call or
                archive generation fails.
        """
        document_path = Path(document_path)
        settings = self._load_settings()
        if not produce_archive and mime_type == "application/pdf":
            self._log.debug("Mistral OCR skipped: born-digital PDF, no archive wanted")
            self._text = self._local_pdf_text(document_path)
            return
        response = self._run_ocr(settings, document_path, mime_type)
        self._text = response_to_text(response)
        self._log_low_confidence(response)
        archive_path = self._tempdir / "archive.pdf"
        self._build_archive(document_path, mime_type, response, archive_path)
        if produce_archive:
            self._archive_path = archive_path
        self._thumbnail_source = archive_path

    def get_text(self) -> str:
        """Return the plain-text content extracted during parse."""
        return self._text or ""

    def get_date(self) -> datetime.datetime | None:
        """Return None: paperless' own date detection runs on the content."""
        return None

    def get_archive_path(self) -> Path | None:
        """Return the archive PDF path, or None if none was requested."""
        return self._archive_path

    def get_thumbnail(self, document_path: Path, mime_type: str) -> Path:
        """Render a thumbnail from the archive PDF (or the original)."""
        from documents.parsers import make_thumbnail_from_pdf

        source = self._archive_path or getattr(self, "_thumbnail_source", None)
        return make_thumbnail_from_pdf(
            source or Path(document_path), self._tempdir, self._logging_group
        )

    def get_page_count(self, document_path: Path, mime_type: str) -> int | None:
        """Return the page count for PDFs, None for images."""
        if mime_type != "application/pdf":
            return None
        from paperless.parsers.utils import get_page_count_for_pdf

        return get_page_count_for_pdf(Path(document_path), log=logger)

    def extract_metadata(
        self,
        document_path: Path,
        mime_type: str,
    ) -> list[MetadataEntry]:
        """Return PDF metadata entries; empty for other MIME types."""
        if mime_type != "application/pdf":
            return []
        from paperless.parsers.utils import extract_pdf_metadata

        return extract_pdf_metadata(Path(document_path), log=logger)

    def _load_settings(self) -> Settings:
        """Read settings, converting problems into ParseError."""
        try:
            settings = Settings.from_env()
        except ConfigError as e:
            raise self._parse_error(f"Mistral OCR configuration error: {e}") from e
        if not settings.is_configured:
            raise self._parse_error("PAPERLESS_MISTRAL_API_KEY is not set")
        return settings

    def _run_ocr(
        self,
        settings: Settings,
        document_path: Path,
        mime_type: str,
    ) -> dict[str, Any]:
        """Send the document to the API and return the response."""
        try:
            data, payload_mime = document_payload(document_path, mime_type)
            self._log.info(
                "Mistral OCR: sending %s (%s, %d bytes) to %s",
                document_path.name,
                payload_mime,
                len(data),
                settings.endpoint,
            )
            response = MistralOcrClient(settings).ocr_bytes(data, payload_mime)
        except MistralOcrError as e:
            self._log.error("Mistral OCR failed: %s", e)
            raise self._parse_error(f"Mistral OCR failed: {e}") from e
        except Exception as e:
            self._log.exception("Mistral OCR failed unexpectedly")
            raise self._parse_error(f"Mistral OCR failed: {e}") from e
        usage = response.get("usage_info") or {}
        self._log.info(
            "Mistral OCR: processed %s page(s) with model %s",
            usage.get("pages_processed", len(response.get("pages") or [])),
            response.get("model", settings.model),
        )
        return response

    def _build_archive(
        self,
        document_path: Path,
        mime_type: str,
        response: dict[str, Any],
        archive_path: Path,
    ) -> None:
        """Write the archive PDF, converting failures into ParseError."""
        try:
            written = build_archive(
                document_path, mime_type, response.get("pages") or [], archive_path
            )
        except Exception as e:
            self._log.exception("Building the archive PDF failed")
            raise self._parse_error(f"Building the archive PDF failed: {e}") from e
        self._log.debug("Mistral OCR: text layer written on %d page(s)", written)

    def _local_pdf_text(self, document_path: Path) -> str:
        """Extract text from a born-digital PDF with paperless' pdftotext helper."""
        from paperless.parsers.utils import extract_pdf_text, post_process_text

        return post_process_text(extract_pdf_text(document_path, log=logger)) or ""

    def _log_low_confidence(self, response: dict[str, Any]) -> None:
        """Log blocks with low recognition confidence (text is kept regardless)."""
        for page in response.get("pages") or []:
            for block in low_confidence_blocks(page):
                score = block["confidence_scores"]["average_content_confidence_score"]
                self._log.info(
                    "Mistral OCR: low confidence (%.2f) on page %s block %r",
                    score,
                    page.get("index"),
                    (block.get("content") or "")[:60],
                )

    @staticmethod
    def _parse_error(message: str) -> Exception:
        """Instantiate paperless' ParseError (imported lazily)."""
        from documents.parsers import ParseError

        return ParseError(message)
