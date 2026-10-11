from paperless_mistral_ocr.content import (
    low_confidence_blocks,
    normalize_whitespace,
    page_to_text,
    response_to_text,
    strip_markdown,
    table_rows,
    table_to_text,
)

TABLE = (
    "| Item | Qty |\n"
    "| --- | --- |\n"
    "| Paint | 1 |\n"
    "|   |   |\n"
    "| Total<br>incl. tax | $5 |\n"
)


def test_table_rows_drops_separator_and_blank_rows_and_strips_cells():
    rows = table_rows(TABLE)
    assert rows[0] == ["Item", "Qty"]
    assert rows[1] == ["Paint", "1"]
    assert rows[2] == ["Total incl. tax", "$5"]
    assert len(rows) == 3


def test_table_to_text_joins_non_empty_cells():
    assert table_to_text(TABLE) == "Item | Qty\nPaint | 1\nTotal incl. tax | $5"


def test_strip_markdown_removes_headings_emphasis_and_images():
    text = "# Title\n\n![img-0.jpeg](img-0.jpeg)\n\nSome **bold** and `code`."
    assert strip_markdown(text) == "Title\n\n\n\nSome bold and code."


def test_strip_markdown_keeps_blank_line_before_heading():
    assert strip_markdown("12345\n\n# LABOR") == "12345\n\nLABOR"


def test_normalize_whitespace():
    assert normalize_whitespace("  a   b \n\n\n\n  c\t d  \n") == "a b\n\nc d"


def test_page_to_text_substitutes_tables_and_includes_header_footer():
    page = {
        "index": 0,
        "markdown": "# Heading\n\n[tbl-0.md](tbl-0.md)\n\n![img-0.jpeg](img-0.jpeg)",
        "tables": [{"id": "tbl-0.md", "content": TABLE, "format": "markdown"}],
        "header": "Page header",
        "footer": "Page footer",
    }
    text = page_to_text(page)
    assert text.splitlines()[0] == "Page header"
    assert "Heading" in text
    assert "Paint | 1" in text
    assert "tbl-0.md" not in text
    assert "img-0.jpeg" not in text
    assert text.endswith("Page footer")


def test_page_to_text_unknown_table_placeholder_is_removed():
    page = {"markdown": "Before\n\n[tbl-9.md](tbl-9.md)\n\nAfter", "tables": []}
    assert page_to_text(page) == "Before\n\nAfter"


def test_response_to_text_orders_pages_by_index():
    response = {
        "pages": [
            {"index": 1, "markdown": "second"},
            {"index": 0, "markdown": "first"},
            {"index": 2, "markdown": ""},
        ]
    }
    assert response_to_text(response) == "first\n\nsecond"


def test_fixture_response_content(sample_response):
    text = response_to_text(sample_response)
    assert "Total Labor: $500.00" in text
    assert "Wall paint | 2 | $30.00 | $60.00" in text
    assert "TOTAL | $580.00" in text
    assert "Exampletown 12345\n\nLABOR" in text
    assert "img-0.jpeg" not in text and "tbl-0.md" not in text


def test_low_confidence_blocks(sample_response):
    page = sample_response["pages"][0]
    flagged = [b["content"] for b in low_confidence_blocks(page)]
    assert flagged == ["LOGO TEXT"]
    assert low_confidence_blocks({"blocks": [{"type": "image"}]}) == []
