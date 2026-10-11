import pymupdf
import pytest

from paperless_mistral_ocr.layer import (
    Box,
    TextLayerWriter,
    block_box,
    block_lines,
    build_archive,
    extract_text,
    fit_font_size,
    image_to_pdf_bytes,
    overlay_page,
    page_text_kind,
    scale_factors,
    strip_text,
)


def test_scale_factors_maps_pixels_to_points():
    sx, sy = scale_factors({"width": 764, "height": 1019}, 597.79, 797.06)
    assert sx == pytest.approx(597.79 / 764)
    assert sy == pytest.approx(797.06 / 1019)


@pytest.mark.parametrize("dims", [{}, {"width": 0, "height": 10}, {"width": 5}])
def test_scale_factors_rejects_bad_dimensions(dims):
    with pytest.raises(ValueError):
        scale_factors(dims, 100, 100)


def test_block_box_scales_corners():
    block = {
        "top_left_x": 10,
        "top_left_y": 20,
        "bottom_right_x": 110,
        "bottom_right_y": 70,
    }
    box = block_box(block, 0.5, 2.0)
    assert box == Box(5.0, 40.0, 55.0, 140.0)
    assert box.width == 50.0
    assert box.height == 100.0
    assert not box.is_empty
    assert Box(1, 1, 1, 5).is_empty


def test_block_lines_by_type():
    assert block_lines({"type": "image", "content": "![x](x)"}) == []
    assert block_lines({"type": "text", "content": "   "}) == []
    assert block_lines({"type": "title", "content": "# **Big** title"}) == ["Big title"]
    assert block_lines({"type": "text", "content": "costs $ 5 $"}) == ["costs 5"]
    table = "| A | B |\n| --- | --- |\n| 1 | 2 |\n|  |  |\n| x |  |"
    assert block_lines({"type": "table", "content": table}) == ["A B", "1 2", "x"]


def test_fit_font_size_fits_width_and_caps():
    font = pymupdf.Font("helv")
    size = fit_font_size(font, "Hello world", width=100.0, max_size=50.0)
    assert font.text_length("Hello world", fontsize=size) == pytest.approx(100.0)
    assert fit_font_size(font, "Hi", width=1000.0, max_size=12.0) == 12.0
    assert fit_font_size(font, "x" * 500, width=1.0, max_size=12.0) == 2.0


def _blank_pdf(width=200, height=100) -> pymupdf.Document:
    doc = pymupdf.open()
    doc.new_page(width=width, height=height)
    return doc


def test_overlay_page_writes_invisible_text_in_block_position():
    doc = _blank_pdf()
    page = doc[0]
    ocr_page = {
        "index": 0,
        "dimensions": {"width": 400, "height": 200},
        "blocks": [
            {
                "type": "text",
                "content": "Hello",
                "top_left_x": 200,
                "top_left_y": 100,
                "bottom_right_x": 400,
                "bottom_right_y": 140,
            }
        ],
    }
    assert overlay_page(page, ocr_page) is True
    assert page_text_kind(page) == "invisible"
    words = page.get_text("words")
    assert [w[4] for w in words] == ["Hello"]
    x0, y0, x1, y1 = words[0][:4]
    assert 99 <= x0 <= 102 and y0 >= 48 and y1 <= 72


def test_build_archive_with_fixture_response(scan_pdf, sample_response, tmp_path):
    out = tmp_path / "archive.pdf"
    pages = sample_response["pages"]
    assert build_archive(scan_pdf, "application/pdf", pages, out) == 1
    text = extract_text(out)
    assert "Jane Example" in text
    assert "Wall paint 2 $30.00 $60.00" in text
    assert "img-0" not in text


def test_overlay_page_handles_table_rows_and_paragraphs():
    doc = _blank_pdf(width=300, height=300)
    page = doc[0]
    ocr_page = {
        "index": 0,
        "dimensions": {"width": 300, "height": 300},
        "blocks": [
            {
                "type": "table",
                "content": "| A | B |\n| --- | --- |\n| row1 | 1 |\n| row2 | 2 |",
                "top_left_x": 0,
                "top_left_y": 0,
                "bottom_right_x": 300,
                "bottom_right_y": 90,
            },
            {
                "type": "text",
                "content": "A fairly long paragraph of text that needs wrapping",
                "top_left_x": 0,
                "top_left_y": 100,
                "bottom_right_x": 120,
                "bottom_right_y": 160,
            },
        ],
    }
    overlay_page(page, ocr_page)
    text = page.get_text()
    assert "row1 1" in text
    assert "row2 2" in text
    assert "needs" in text and "wrapping" in text
    rows = sorted(b[1] for b in page.get_text("blocks"))
    assert rows[0] < 90


def test_overlay_page_without_blocks_falls_back_to_page_text():
    doc = _blank_pdf()
    ocr_page = {"index": 0, "markdown": "# Fallback\n\ncontent", "blocks": []}
    assert overlay_page(doc[0], ocr_page) is True
    text = doc[0].get_text()
    assert "Fallback" in text and "content" in text


def test_overlay_page_skips_pages_with_visible_text():
    doc = _blank_pdf()
    page = doc[0]
    page.insert_text((10, 50), "visible", fontsize=12)
    assert page_text_kind(page) == "visible"
    ocr_page = {"index": 0, "markdown": "ocr", "blocks": []}
    assert overlay_page(page, ocr_page) is False
    assert "ocr" not in page.get_text()


def test_strip_text_removes_invisible_layer_but_keeps_images(scan_pdf):
    with pymupdf.open(str(scan_pdf)) as doc:
        page = doc[0]
        assert page_text_kind(page) == "none"
        page.insert_text((20, 40), "old ocr", fontsize=10, render_mode=3)
        assert page_text_kind(page) == "invisible"
        n_images = len(page.get_images())
        strip_text(page)
        assert page_text_kind(page) == "none"
        assert len(page.get_images()) == n_images == 1


def test_build_archive_from_image(tmp_path):
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 300, 200), 0)
    pix.clear_with(255)
    image = tmp_path / "scan.png"
    image.write_bytes(pix.tobytes("png"))
    with pymupdf.open("pdf", image_to_pdf_bytes(image)) as doc:
        assert doc.page_count == 1
        width, height = doc[0].rect.width, doc[0].rect.height
    pages = [
        {
            "index": 0,
            "dimensions": {"width": 300, "height": 200},
            "blocks": [
                {
                    "type": "text",
                    "content": "Image text",
                    "top_left_x": 10,
                    "top_left_y": 10,
                    "bottom_right_x": 290,
                    "bottom_right_y": 40,
                }
            ],
        }
    ]
    out = tmp_path / "archive.pdf"
    assert build_archive(image, "image/png", pages, out) == 1
    with pymupdf.open(str(out)) as doc:
        assert doc.page_count == 1
        assert (doc[0].rect.width, doc[0].rect.height) == (width, height)
        assert len(doc[0].get_images()) == 1
    assert "Image text" in extract_text(out)


def test_build_archive_ignores_out_of_range_pages(scan_pdf, tmp_path):
    pages = [{"index": 5, "markdown": "nope", "blocks": []}]
    out = tmp_path / "archive.pdf"
    assert build_archive(scan_pdf, "application/pdf", pages, out) == 0
    assert out.exists()


def _words_in(page, box):
    return [w for w in page.get_text("words") if pymupdf.Rect(w[:4]).intersects(box)]


def test_tall_narrow_block_is_written_vertically():
    """Regression: vertical margin text used to raise 'Text must start in rectangle'."""
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=800)
    writer = TextLayerWriter(page)
    text = "Photograph by Someone for a magazine feature running down the margin"
    box = Box(10, 100, 22, 700)
    writer.write_block(box, [text])
    assert page.get_text("text").split() == text.split()
    words = _words_in(page, box.to_rect())
    assert len(words) == len(text.split())
    assert all(pymupdf.Rect(w[:4]).width < 20 for w in words)


def test_paragraph_in_box_too_small_for_its_font_does_not_raise():
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=800)
    writer = TextLayerWriter(page)
    long_text = " ".join(["word"] * 80)
    writer.write_block(Box(10, 10, 30, 60), [long_text])
    writer.write_block(Box(10, 100, 400, 101), [long_text])
    assert "word" in page.get_text("text")


def test_overlay_page_survives_a_failing_block(monkeypatch, caplog):
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=800)
    calls = []

    def boom(self, box, lines):
        calls.append(lines)
        if len(calls) == 1:
            raise RuntimeError("synthetic failure")

    monkeypatch.setattr(TextLayerWriter, "write_block", boom)
    ocr_page = {
        "dimensions": {"width": 600, "height": 800, "dpi": 72},
        "blocks": [
            {
                "type": "text",
                "content": "first",
                "top_left_x": 0,
                "top_left_y": 0,
                "bottom_right_x": 100,
                "bottom_right_y": 20,
            },
            {
                "type": "text",
                "content": "second",
                "top_left_x": 0,
                "top_left_y": 40,
                "bottom_right_x": 100,
                "bottom_right_y": 60,
            },
        ],
    }
    with caplog.at_level("WARNING"):
        assert overlay_page(page, ocr_page) is True
    assert len(calls) == 2
    assert "could not write text block" in caplog.text
