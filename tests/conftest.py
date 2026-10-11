"""Shared fixtures: synthetic OCR response, generated scans, paperless stubs."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pymupdf
import pytest

TESTS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = TESTS_DIR / "fixtures"
SAMPLES_DIR = TESTS_DIR / "samples"

SCAN_LINES = (
    "ACME PAINTING INVOICE",
    "Billed to: Jane Example",
    "12 Example Street, Exampletown 12345",
    "Total Labor: $500.00",
)


def make_scan_pdf(path: Path, lines: tuple[str, ...] = SCAN_LINES, dpi: int = 150):
    """Write an image-only ("scanned") one-page PDF showing ``lines``.

    The text is rendered, rasterised and re-embedded as a picture so the
    resulting PDF has no text layer at all.
    """
    with pymupdf.open() as source:
        page = source.new_page(width=595, height=842)
        for i, line in enumerate(lines):
            page.insert_text((50, 100 + 40 * i), line, fontsize=22)
        pixmap = page.get_pixmap(dpi=dpi)
    with pymupdf.open() as scan:
        target = scan.new_page(width=595, height=842)
        target.insert_image(target.rect, pixmap=pixmap)
        scan.save(str(path))
    return path


@pytest.fixture
def samples_dir() -> Path:
    return SAMPLES_DIR


@pytest.fixture
def sample_response() -> dict:
    return json.loads((FIXTURES_DIR / "sample_response.json").read_text("utf-8"))


@pytest.fixture
def scan_pdf(tmp_path) -> Path:
    return make_scan_pdf(tmp_path / "scan.pdf")


class StubParseError(Exception):
    """Stand-in for documents.parsers.ParseError."""


def _make_module(name: str, **attrs) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


@pytest.fixture
def paperless_stubs(monkeypatch, tmp_path):
    """Inject minimal ``django`` and ``paperless`` modules into ``sys.modules``.

    Returns a namespace exposing the stub objects and a ``calls`` list that
    records invocations of the stubbed helper functions.
    """
    calls: list[tuple] = []
    scratch = tmp_path / "scratch"

    def make_thumbnail_from_pdf(in_path, temp_dir, logging_group=None):
        calls.append(("thumbnail", Path(in_path), Path(temp_dir)))
        out = Path(temp_dir) / "convert.webp"
        out.write_bytes(b"webp")
        return out

    def extract_pdf_text(path, log=None):
        calls.append(("extract_pdf_text", Path(path)))
        return "  born   digital\n   text  "

    def post_process_text(text):
        return " ".join(text.split()) if text else None

    def get_page_count_for_pdf(path, log=None):
        calls.append(("page_count", Path(path)))
        return 7

    def extract_pdf_metadata(path, log=None):
        calls.append(("metadata", Path(path)))
        return [{"namespace": "n", "prefix": "p", "key": "k", "value": "v"}]

    settings = types.SimpleNamespace(SCRATCH_DIR=scratch)
    modules = {
        "django": _make_module("django"),
        "django.conf": _make_module("django.conf", settings=settings),
        "documents": _make_module("documents"),
        "documents.parsers": _make_module(
            "documents.parsers",
            ParseError=StubParseError,
            make_thumbnail_from_pdf=make_thumbnail_from_pdf,
        ),
        "paperless": _make_module("paperless"),
        "paperless.parsers": _make_module(
            "paperless.parsers", ParserContext=object, MetadataEntry=dict
        ),
        "paperless.parsers.utils": _make_module(
            "paperless.parsers.utils",
            extract_pdf_text=extract_pdf_text,
            post_process_text=post_process_text,
            get_page_count_for_pdf=get_page_count_for_pdf,
            extract_pdf_metadata=extract_pdf_metadata,
        ),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    return types.SimpleNamespace(
        calls=calls, scratch=scratch, ParseError=StubParseError, settings=settings
    )
