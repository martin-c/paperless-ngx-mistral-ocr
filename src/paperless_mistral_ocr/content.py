"""Convert a Mistral OCR response into plain text for the paperless content field.

The API returns one markdown string per page in which tables are referenced
as ``[tbl-0.md](tbl-0.md)`` placeholders and images as ``![img-0.jpeg](...)``.
This module substitutes the table text, drops image references, flattens
light markdown syntax and normalises whitespace.
"""

from __future__ import annotations

import re
from typing import Any

TABLE_REF_RE = re.compile(r"\[(?P<id>tbl-\d+\.md)\]\((?P=id)\)")
IMAGE_REF_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]*", re.MULTILINE)
EMPHASIS_RE = re.compile(r"(\*\*|__|`)")
HTML_BREAK_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
SEPARATOR_ROW_RE = re.compile(r"^\|?[\s\-:|]*\|?$")
LOW_CONFIDENCE_THRESHOLD = 0.5


def table_rows(markdown: str) -> list[list[str]]:
    """Parse a markdown table into rows of cell strings.

    Separator rows (``| --- | --- |``) and blank rows are dropped and cells
    are stripped.

    Args:
        markdown: Markdown table text as returned by the API.

    Returns:
        A list of rows, each a list of cell strings (empty cells kept).
    """
    rows: list[list[str]] = []
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or SEPARATOR_ROW_RE.match(line):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        rows.append([HTML_BREAK_RE.sub(" ", cell) for cell in cells])
    return rows


def table_to_text(markdown: str) -> str:
    """Flatten a markdown table to one line per row with cells joined by ``|``."""
    lines = []
    for row in table_rows(markdown):
        cells = [cell for cell in row if cell]
        if cells:
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def strip_markdown(text: str) -> str:
    """Remove heading markers, emphasis markers and image references."""
    text = IMAGE_REF_RE.sub("", text)
    text = HEADING_RE.sub("", text)
    text = EMPHASIS_RE.sub("", text)
    return HTML_BREAK_RE.sub("\n", text)


def normalize_whitespace(text: str) -> str:
    """Collapse runs of blanks, trim line ends and squeeze blank lines."""
    text = text.replace("\0", " ")
    text = re.sub(r"[^\S\r\n]+", " ", text)
    text = re.sub(r"[^\S\r\n]+(\r?\n)", r"\1", text)
    text = re.sub(r"(\r?\n)[^\S\r\n]+", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def page_to_text(page: dict[str, Any]) -> str:
    """Convert one OCR page to plain text.

    Args:
        page: A page object from the OCR response.

    Returns:
        Plain text with tables substituted, images removed and markdown
        flattened. Header and footer text are included when present.
    """
    tables = {t.get("id"): t.get("content", "") for t in page.get("tables") or []}
    markdown = page.get("markdown") or ""

    def _substitute(match: re.Match[str]) -> str:
        return table_to_text(tables.get(match.group("id"), ""))

    body = TABLE_REF_RE.sub(_substitute, markdown)
    parts = [page.get("header") or "", body, page.get("footer") or ""]
    return normalize_whitespace(strip_markdown("\n\n".join(p for p in parts if p)))


def response_to_text(response: dict[str, Any]) -> str:
    """Convert a full OCR response to the document content string.

    Pages are ordered by their ``index`` and separated by a blank line.
    """
    pages = sorted(response.get("pages") or [], key=lambda p: p.get("index", 0))
    return "\n\n".join(t for t in (page_to_text(p) for p in pages) if t)


def low_confidence_blocks(
    page: dict[str, Any],
    threshold: float = LOW_CONFIDENCE_THRESHOLD,
) -> list[dict[str, Any]]:
    """Return blocks whose average content confidence is below ``threshold``.

    Blocks without a confidence score (e.g. images) are never reported.
    """
    found = []
    for block in page.get("blocks") or []:
        scores = block.get("confidence_scores") or {}
        score = scores.get("average_content_confidence_score")
        if score is not None and score < threshold:
            found.append(block)
    return found
