"""Tests for the paperless adapter, run against stub paperless/Django modules."""

import importlib
import json
from pathlib import Path

import pytest

import paperless_mistral_ocr.parser as parser_module
from paperless_mistral_ocr.client import MistralApiError
from paperless_mistral_ocr.layer import extract_text


@pytest.fixture
def parser_cls(paperless_stubs):
    return importlib.reload(parser_module).MistralOcrParser


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("PAPERLESS_MISTRAL_API_KEY", "k")


@pytest.fixture
def fake_ocr(monkeypatch, sample_response):
    """Replace the HTTP client with one returning the synthetic fixture response."""
    calls = []

    class FakeClient:
        def __init__(self, settings):
            calls.append(settings)

        def ocr_bytes(self, data, mime_type):
            calls.append((len(data), mime_type))
            return json.loads(json.dumps(sample_response))

    monkeypatch.setattr(parser_module, "MistralOcrClient", FakeClient)
    return calls


def test_class_attributes_satisfy_registry(parser_cls):
    for attr in ("name", "version", "author", "url"):
        assert isinstance(getattr(parser_cls, attr), str) and getattr(parser_cls, attr)
    assert parser_cls.uses_remote_service is True
    assert parser_cls.supported_mime_types()["application/pdf"] == ".pdf"


def test_score_requires_api_key(parser_cls, monkeypatch):
    monkeypatch.delenv("PAPERLESS_MISTRAL_API_KEY", raising=False)
    assert parser_cls.score("application/pdf", "a.pdf") is None
    monkeypatch.setenv("PAPERLESS_MISTRAL_API_KEY", "k")
    assert parser_cls.score("application/pdf", "a.pdf") == 20
    assert parser_cls.score("image/jpeg", "a.jpg", Path("a.jpg")) == 20
    assert parser_cls.score("text/plain", "a.txt") is None
    monkeypatch.setenv("PAPERLESS_MISTRAL_MAX_PAGES", "lots")
    assert parser_cls.score("application/pdf", "a.pdf") is None


def test_context_manager_creates_and_removes_tempdir(parser_cls, paperless_stubs):
    with parser_cls(logging_group="g") as parser:
        tempdir = parser._tempdir
        assert tempdir.is_dir()
        assert tempdir.parent == paperless_stubs.scratch
        assert parser.can_produce_archive is True
        assert parser.requires_pdf_rendition is False
        parser.configure(None)
    assert not tempdir.exists()


def test_parse_pdf_produces_text_and_archive(
    parser_cls, configured, fake_ocr, scan_pdf, paperless_stubs
):
    with parser_cls() as parser:
        parser.parse(scan_pdf, "application/pdf")
        text = parser.get_text()
        archive = parser.get_archive_path()
        assert "Jane Example" in text
        assert "TOTAL | $580.00" in text
        assert archive is not None and archive.parent == parser._tempdir
        assert "Jane Example" in extract_text(archive)
        assert parser.get_date() is None
        thumb = parser.get_thumbnail(scan_pdf, "application/pdf")
        assert thumb.exists()
        assert ("thumbnail", archive, parser._tempdir) in paperless_stubs.calls
    assert fake_ocr[1] == (scan_pdf.stat().st_size, "application/pdf")


def test_parse_pdf_without_archive_uses_local_text(
    parser_cls, configured, fake_ocr, scan_pdf, paperless_stubs
):
    with parser_cls() as parser:
        parser.parse(scan_pdf, "application/pdf", produce_archive=False)
        assert parser.get_text() == "born digital text"
        assert parser.get_archive_path() is None
    assert fake_ocr == []
    assert ("extract_pdf_text", scan_pdf) in paperless_stubs.calls


def test_parse_image_builds_archive_even_without_archive_request(
    parser_cls, configured, fake_ocr, tmp_path, paperless_stubs
):
    import pymupdf

    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 764, 1019), 0)
    pix.clear_with(255)
    image = tmp_path / "scan.png"
    image.write_bytes(pix.tobytes("png"))
    with parser_cls() as parser:
        parser.parse(image, "image/png", produce_archive=False)
        assert "Jane Example" in parser.get_text()
        assert parser.get_archive_path() is None
        parser.get_thumbnail(image, "image/png")
        source = paperless_stubs.calls[-1][1]
        assert source.name == "archive.pdf"
        assert parser.get_page_count(image, "image/png") is None
        assert parser.extract_metadata(image, "image/png") == []
    assert fake_ocr[1] == (image.stat().st_size, "image/png")


def test_parse_without_key_raises_parse_error(
    parser_cls, monkeypatch, scan_pdf, paperless_stubs
):
    monkeypatch.delenv("PAPERLESS_MISTRAL_API_KEY", raising=False)
    with parser_cls() as parser, pytest.raises(paperless_stubs.ParseError):
        parser.parse(scan_pdf, "application/pdf")


def test_api_failure_raises_parse_error(
    parser_cls, configured, monkeypatch, scan_pdf, paperless_stubs
):
    class FailingClient:
        def __init__(self, settings):
            pass

        def ocr_bytes(self, data, mime_type):
            raise MistralApiError(500, "boom")

    monkeypatch.setattr(parser_module, "MistralOcrClient", FailingClient)
    with parser_cls() as parser, pytest.raises(paperless_stubs.ParseError, match="500"):
        parser.parse(scan_pdf, "application/pdf")
    assert parser.get_text() == ""
    assert parser.get_archive_path() is None


def test_page_count_and_metadata_delegate_for_pdfs(
    parser_cls, scan_pdf, paperless_stubs
):
    with parser_cls() as parser:
        assert parser.get_page_count(scan_pdf, "application/pdf") == 7
        assert parser.extract_metadata(scan_pdf, "application/pdf")[0]["key"] == "k"
    assert ("page_count", scan_pdf) in paperless_stubs.calls
    assert ("metadata", scan_pdf) in paperless_stubs.calls
