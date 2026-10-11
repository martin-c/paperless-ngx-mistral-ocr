"""Optional live test against the real Mistral API.

Skipped unless ``MISTRAL_LIVE_TESTS=1`` is set and an API key can be resolved
(see ``paperless_mistral_ocr.keys``). The document is generated on the fly:
a rasterised page of fictional text, so no private sample is needed.
"""

import os

import pytest

from paperless_mistral_ocr.client import MistralOcrClient
from paperless_mistral_ocr.config import Settings
from paperless_mistral_ocr.content import response_to_text
from paperless_mistral_ocr.keys import ApiKeyError, resolve_api_key

pytestmark = pytest.mark.live


@pytest.fixture
def live_settings():
    if os.environ.get("MISTRAL_LIVE_TESTS") != "1":
        pytest.skip("set MISTRAL_LIVE_TESTS=1 to run live API tests")
    try:
        key = resolve_api_key()
    except ApiKeyError as e:
        pytest.skip(str(e))
    return Settings(api_key=key, max_pages=2)


def test_live_scan_ocr(live_settings, scan_pdf):
    response = MistralOcrClient(live_settings).ocr_file(scan_pdf, "application/pdf")
    assert len(response["pages"]) == 1
    assert response["pages"][0]["blocks"]
    text = response_to_text(response)
    assert "Jane Example" in text
    assert "500.00" in text
