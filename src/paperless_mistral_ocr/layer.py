"""Build the archive PDF: original pages plus an invisible text layer.

Mistral returns, per page, a list of blocks with pixel coordinates in the
space of ``page["dimensions"]`` (origin top-left). Each block is mapped onto
the PDF page rectangle and its text is written with PDF render mode 3
(invisible) so viewers can search and copy text that sits where the scan
shows it. Pages that already carry an invisible-only text layer (an earlier
OCR pass) have that layer stripped first; pages with visible text are left
untouched.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymupdf

from paperless_mistral_ocr.content import strip_markdown, table_rows

logger = logging.getLogger(__name__)

INVISIBLE = 3
FONT_NAME = "helv"
MIN_FONT_SIZE = 2.0
MAX_LINE_FONT_RATIO = 0.8
SINGLE_LINE_MIN_RATIO = 0.35
SHRINK_FACTOR = 0.9
NATIVE_IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/png"})
MATH_RE = re.compile(r"\$\$?([^$]+)\$\$?")


@dataclass(frozen=True, slots=True)
class Box:
    """Axis-aligned rectangle in PDF points."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        """Width in points."""
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        """Height in points."""
        return self.y1 - self.y0

    @property
    def is_empty(self) -> bool:
        """True when the box has no usable area."""
        return self.width <= 0 or self.height <= 0

    def to_rect(self) -> pymupdf.Rect:
        """Convert to a PyMuPDF rectangle."""
        return pymupdf.Rect(self.x0, self.y0, self.x1, self.y1)


def scale_factors(
    dimensions: dict[str, Any],
    page_width: float,
    page_height: float,
) -> tuple[float, float]:
    """Return (x, y) factors mapping OCR pixel space to PDF points.

    Args:
        dimensions: The ``dimensions`` object of an OCR page (``width``,
            ``height`` in pixels).
        page_width: PDF page width in points.
        page_height: PDF page height in points.

    Raises:
        ValueError: If the OCR dimensions are missing or non-positive.
    """
    width = float(dimensions.get("width") or 0)
    height = float(dimensions.get("height") or 0)
    if width <= 0 or height <= 0:
        raise ValueError(f"invalid OCR page dimensions: {dimensions!r}")
    return page_width / width, page_height / height


def block_box(block: dict[str, Any], sx: float, sy: float) -> Box:
    """Map a block's pixel coordinates to a ``Box`` in PDF points."""
    return Box(
        float(block["top_left_x"]) * sx,
        float(block["top_left_y"]) * sy,
        float(block["bottom_right_x"]) * sx,
        float(block["bottom_right_y"]) * sy,
    )


def block_lines(block: dict[str, Any]) -> list[str]:
    """Return the text lines to write for a block.

    Image blocks yield nothing. Table blocks yield one line per table row.
    Other blocks yield a single line of flattened markdown.
    """
    content = (block.get("content") or "").strip()
    if block.get("type") == "image" or not content:
        return []
    if block.get("type") == "table":
        rows = (" ".join(c for c in row if c) for row in table_rows(content))
        return [row for row in rows if row]
    text = MATH_RE.sub(r"\1", strip_markdown(content))
    text = " ".join(text.split())
    return [text] if text else []


def fit_font_size(
    font: pymupdf.Font,
    text: str,
    width: float,
    max_size: float,
) -> float:
    """Largest font size at which ``text`` fits ``width`` on one line."""
    unit_width = font.text_length(text, fontsize=1.0)
    size = max_size if unit_width <= 0 else min(max_size, width / unit_width)
    return max(size, MIN_FONT_SIZE)


class TextLayerWriter:
    """Writes invisible text for OCR blocks onto a single PDF page."""

    def __init__(self, page: pymupdf.Page) -> None:
        self._page = page
        self._font = pymupdf.Font(FONT_NAME)

    def write_block(self, box: Box, lines: list[str]) -> None:
        """Write one block's lines into ``box``.

        A single line is placed on one baseline when it fits reasonably;
        otherwise it is wrapped as a paragraph. Several lines (table rows)
        are spaced evenly over the box height.
        """
        if box.is_empty or not lines:
            return
        if len(lines) == 1:
            self._write_single(box, lines[0])
        else:
            self._write_rows(box, lines)

    def _write_single(self, box: Box, text: str) -> None:
        """Write a block that the API returned as one piece of text."""
        size = fit_font_size(
            self._font, text, box.width, box.height * MAX_LINE_FONT_RATIO
        )
        if size >= box.height * SINGLE_LINE_MIN_RATIO:
            self._write_line((box.x0, box.y1 - box.height * 0.2), text, size)
        else:
            self._write_paragraph(box, text)

    def _write_rows(self, box: Box, lines: list[str]) -> None:
        """Write table rows evenly spaced over the box height."""
        row_height = box.height / len(lines)
        for i, line in enumerate(lines):
            size = fit_font_size(self._font, line, box.width, row_height * 0.7)
            baseline_y = box.y0 + row_height * (i + 1) - row_height * 0.25
            self._write_line((box.x0, baseline_y), line, size)

    def _write_line(self, origin: tuple[float, float], text: str, size: float) -> None:
        """Write one line of invisible text starting at ``origin`` (baseline)."""
        writer = pymupdf.TextWriter(self._page.rect)
        writer.append(origin, text, font=self._font, fontsize=size)
        writer.write_text(self._page, render_mode=INVISIBLE)

    def _write_paragraph(self, box: Box, text: str) -> None:
        """Wrap text inside the box, shrinking the font until it fits."""
        size = box.height * MAX_LINE_FONT_RATIO
        rect = box.to_rect()
        while size >= MIN_FONT_SIZE:
            writer = pymupdf.TextWriter(self._page.rect)
            unfitted = writer.fill_textbox(rect, text, font=self._font, fontsize=size)
            if not unfitted:
                writer.write_text(self._page, render_mode=INVISIBLE)
                return
            size *= SHRINK_FACTOR
        logger.debug("paragraph did not fit its block even at minimum size")


def page_text_kind(page: pymupdf.Page) -> str:
    """Classify the text already on a page.

    Returns:
        ``"none"`` when the page has no text, ``"invisible"`` when every text
        span uses render mode 3 (a prior OCR layer), ``"visible"`` otherwise.
    """
    modes = {span.get("type") for span in page.get_texttrace()}
    if not modes:
        return "none"
    if modes == {INVISIBLE}:
        return "invisible"
    return "visible"


def strip_text(page: pymupdf.Page) -> None:
    """Remove all text from a page while keeping images and line art."""
    page.add_redact_annot(page.rect)
    page.apply_redactions(
        images=pymupdf.PDF_REDACT_IMAGE_NONE,
        graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
        text=pymupdf.PDF_REDACT_TEXT_REMOVE,
    )


def overlay_page(page: pymupdf.Page, ocr_page: dict[str, Any]) -> bool:
    """Add the invisible text layer for ``ocr_page`` to ``page``.

    Args:
        page: Target PDF page (modified in place).
        ocr_page: The matching page object from the OCR response.

    Returns:
        True if text was written, False if the page was skipped because it
        already has visible text.
    """
    kind = page_text_kind(page)
    if kind == "visible":
        logger.debug("page %d has visible text; leaving it untouched", page.number)
        return False
    if kind == "invisible":
        logger.debug("page %d: stripping existing invisible text layer", page.number)
        strip_text(page)
    if page.rotation:
        page.remove_rotation()

    writer = TextLayerWriter(page)
    blocks = ocr_page.get("blocks") or []
    if not blocks:
        _overlay_whole_page(writer, page, ocr_page)
        return True
    sx, sy = scale_factors(
        ocr_page.get("dimensions") or {}, page.rect.width, page.rect.height
    )
    for block in blocks:
        writer.write_block(block_box(block, sx, sy), block_lines(block))
    return True


def _overlay_whole_page(
    writer: TextLayerWriter,
    page: pymupdf.Page,
    ocr_page: dict[str, Any],
) -> None:
    """Fallback for responses without blocks: wrap the page text over the page."""
    from paperless_mistral_ocr.content import page_to_text

    text = page_to_text(ocr_page)
    if text:
        box = Box(page.rect.x0, page.rect.y0, page.rect.x1, page.rect.y1)
        writer.write_block(box, [" ".join(text.split())])


def iter_page_pairs(
    doc: pymupdf.Document,
    ocr_pages: list[dict[str, Any]],
) -> Iterator[tuple[pymupdf.Page, dict[str, Any]]]:
    """Yield (pdf page, ocr page) pairs matched by the OCR ``index`` field."""
    for ocr_page in ocr_pages:
        index = ocr_page.get("index")
        if not isinstance(index, int) or not 0 <= index < doc.page_count:
            logger.warning("OCR page index %r has no matching PDF page", index)
            continue
        yield doc[index], ocr_page


def image_to_pdf_bytes(image_path: Path) -> bytes:
    """Convert an image file (any format PyMuPDF reads) to PDF bytes.

    Multi-frame images such as TIFF become multi-page PDFs.
    """
    with pymupdf.open(str(image_path)) as image_doc:
        return image_doc.convert_to_pdf()


def document_payload(document_path: Path, mime_type: str) -> tuple[bytes, str]:
    """Return the bytes and MIME type to send to the API for a document.

    PDFs, JPEGs and PNGs are sent as-is. Other image formats are converted to
    a PDF first, which also handles multi-page TIFFs.
    """
    if mime_type == "application/pdf" or mime_type in NATIVE_IMAGE_MIME_TYPES:
        return Path(document_path).read_bytes(), mime_type
    return image_to_pdf_bytes(Path(document_path)), "application/pdf"


def build_archive(
    document_path: Path,
    mime_type: str,
    ocr_pages: list[dict[str, Any]],
    output_path: Path,
) -> int:
    """Create the archive PDF for a document.

    Args:
        document_path: The original PDF or image file.
        mime_type: MIME type of the original.
        ocr_pages: ``pages`` list of the OCR response.
        output_path: Where to write the resulting PDF.

    Returns:
        The number of pages that received a text layer.
    """
    if mime_type == "application/pdf":
        doc = pymupdf.open(str(document_path))
    else:
        doc = pymupdf.open("pdf", image_to_pdf_bytes(Path(document_path)))
    with doc:
        written = sum(
            overlay_page(page, ocr_page)
            for page, ocr_page in iter_page_pairs(doc, ocr_pages)
        )
        doc.save(str(output_path), garbage=3, deflate=True)
    return written


def extract_text(pdf_path: Path) -> str:
    """Return the text of every page of a PDF (used by tests and the CLI)."""
    with pymupdf.open(str(pdf_path)) as doc:
        return "\n".join(page.get_text() for page in doc)
