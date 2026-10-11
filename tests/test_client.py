import base64
import json

import httpx
import pytest

from paperless_mistral_ocr.client import (
    MistralApiError,
    MistralOcrClient,
    MistralOcrError,
    MistralResponseError,
    MistralTransportError,
    backoff_seconds,
    build_request_body,
    page_range,
    to_data_uri,
)
from paperless_mistral_ocr.config import Settings

SETTINGS = Settings(api_key="secret", model="m", max_pages=3, timeout=5)
OK_PAYLOAD = {"pages": [{"index": 0, "markdown": "hi"}], "model": "m"}


def _client(handler, **kwargs) -> MistralOcrClient:
    sleeps: list[float] = []
    client = MistralOcrClient(
        SETTINGS,
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
        **kwargs,
    )
    client.sleeps = sleeps  # type: ignore[attr-defined]
    return client


def test_to_data_uri():
    assert to_data_uri(b"abc", "image/png") == "data:image/png;base64,YWJj"


def test_page_range():
    assert page_range(50) == "0-49"
    assert page_range(1) == "0-0"
    assert page_range(0) is None


def test_build_request_body_pdf_and_image():
    body = build_request_body("data:x", "application/pdf", "m", 50)
    assert body["document"] == {"type": "document_url", "document_url": "data:x"}
    assert body["model"] == "m"
    assert body["include_blocks"] is True
    assert body["table_format"] == "markdown"
    assert body["confidence_scores_granularity"] == "block"
    assert body["pages"] == "0-49"

    body = build_request_body("data:y", "image/jpeg", "m", 0)
    assert body["document"] == {"type": "image_url", "image_url": "data:y"}
    assert "pages" not in body


def test_backoff_seconds():
    assert backoff_seconds(1) == 1.0
    assert backoff_seconds(3) == 4.0
    assert backoff_seconds(20) == 30.0
    assert backoff_seconds(1, "7") == 7.0
    assert backoff_seconds(1, "later") == 1.0


def test_requires_api_key():
    with pytest.raises(MistralOcrError):
        MistralOcrClient(Settings(api_key=None))


def test_successful_request_sends_expected_payload():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=OK_PAYLOAD)

    result = _client(handler).ocr_bytes(b"%PDF", "application/pdf")
    assert result == OK_PAYLOAD
    request = seen[0]
    assert request.url == httpx.URL(SETTINGS.endpoint)
    assert request.headers["Authorization"] == "Bearer secret"
    assert request.headers["Content-Type"] == "application/json"
    body = json.loads(request.content)
    assert body["pages"] == "0-2"
    expected_uri = "data:application/pdf;base64," + base64.b64encode(b"%PDF").decode()
    assert body["document"]["document_url"] == expected_uri


def test_retries_on_429_then_succeeds():
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(429, headers={"Retry-After": "2"}, text="slow")
        return httpx.Response(200, json=OK_PAYLOAD)

    client = _client(handler)
    assert client.ocr_bytes(b"x", "image/png") == OK_PAYLOAD
    assert len(attempts) == 3
    assert client.sleeps == [2.0, 2.0]


def test_gives_up_after_max_attempts_on_5xx():
    def handler(request):
        return httpx.Response(503, text="down")

    client = _client(handler, max_attempts=2)
    with pytest.raises(MistralApiError) as info:
        client.ocr_bytes(b"x", "image/png")
    assert info.value.status_code == 503
    assert client.sleeps == [1.0]


def test_client_error_is_not_retried():
    attempts = []

    def handler(request):
        attempts.append(1)
        return httpx.Response(401, json={"message": "bad key"})

    client = _client(handler)
    with pytest.raises(MistralApiError) as info:
        client.ocr_bytes(b"x", "image/png")
    assert info.value.status_code == 401
    assert "bad key" in str(info.value)
    assert len(attempts) == 1
    assert client.sleeps == []


def test_transport_errors_are_retried_then_raised():
    def handler(request):
        raise httpx.ConnectError("refused")

    client = _client(handler, max_attempts=3)
    with pytest.raises(MistralTransportError):
        client.ocr_bytes(b"x", "image/png")
    assert client.sleeps == [1.0, 2.0]


@pytest.mark.parametrize("body", ["not json", '{"nope": 1}', '{"pages": "x"}'])
def test_bad_payloads_raise_response_error(body):
    def handler(request):
        return httpx.Response(200, content=body)

    with pytest.raises(MistralResponseError):
        _client(handler).ocr_bytes(b"x", "image/png")


def test_ocr_file_reads_bytes(tmp_path):
    path = tmp_path / "a.png"
    path.write_bytes(b"png-bytes")
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=OK_PAYLOAD)

    _client(handler).ocr_file(path, "image/png")
    assert seen[0]["document"]["image_url"].endswith(
        base64.b64encode(b"png-bytes").decode()
    )
