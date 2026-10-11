"""Sample-document harness.

Every directory under ``tests/samples/`` holding an ``expected.toml`` is a
sample: the source document, a recorded ``response.json`` from the Mistral
API and assertions. Samples are not tracked in git (they may hold personal
data); the test skips when none are present. The full offline pipeline
(recorded response -> content text + archive PDF) runs for each and the
assertions are checked.
"""

import json
import tomllib
from pathlib import Path

import pytest

from paperless_mistral_ocr.content import response_to_text
from paperless_mistral_ocr.layer import build_archive, extract_text

SAMPLES_DIR = Path(__file__).resolve().parent / "samples"
SAMPLE_DIRS = sorted(p.parent for p in SAMPLES_DIR.glob("*/expected.toml"))


def _ids(path: Path | None) -> str:
    return "none" if path is None else path.name


@pytest.fixture(params=SAMPLE_DIRS or [None], ids=_ids)
def sample(request):
    if request.param is None:
        pytest.skip(
            "no sample documents in tests/samples/ (record one with "
            "'python -m paperless_mistral_ocr.sample record'); see "
            "tests/samples/README.md"
        )
    sample_dir: Path = request.param
    expected = tomllib.loads((sample_dir / "expected.toml").read_text("utf-8"))
    response = json.loads((sample_dir / "response.json").read_text("utf-8"))
    return sample_dir, expected, response


def test_sample_pipeline(sample, tmp_path):
    sample_dir, expected, response = sample
    document = sample_dir / expected["document"]
    assert document.exists()

    content = response_to_text(response)
    for needle in expected.get("content", {}).get("contains", []):
        assert needle in content, f"content lacks {needle!r}"
    for needle in expected.get("content", {}).get("not_contains", []):
        assert needle not in content, f"content should not contain {needle!r}"

    archive = tmp_path / "archive.pdf"
    written = build_archive(document, expected["mime_type"], response["pages"], archive)
    assert written == len(response["pages"])
    assert archive.stat().st_size > 0

    import pymupdf

    with pymupdf.open(str(archive)) as doc:
        assert doc.page_count == expected["page_count"]
    archive_text = extract_text(archive)
    for needle in expected.get("archive_text", {}).get("contains", []):
        assert needle in archive_text, f"archive text lacks {needle!r}"
