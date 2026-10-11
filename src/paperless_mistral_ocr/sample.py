"""Command-line helper for the sample-document test harness.

Usage:
    python -m paperless_mistral_ocr.sample record DOCUMENT NAME [--samples-dir DIR]
    python -m paperless_mistral_ocr.sample render SAMPLE_DIR OUTPUT_DIR

``record`` runs the live Mistral API on DOCUMENT and writes ``tests/samples/NAME/``
with a copy of the document, ``response.json`` and a skeleton ``expected.toml``.
``render`` runs the offline content conversion pipeline on a recorded sample and writes
``content.txt`` and ``archive.pdf`` for manual inspection.

The API key is resolved by ``paperless_mistral_ocr.keys`` (``MISTRAL_API_KEY``,
``MISTRAL_API_KEY_FILE`` or ``MISTRAL_API_KEY_COMMAND``).
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import shutil
import sys
from pathlib import Path

from paperless_mistral_ocr.client import MistralOcrClient
from paperless_mistral_ocr.config import Settings
from paperless_mistral_ocr.content import response_to_text
from paperless_mistral_ocr.keys import ApiKeyError, resolve_api_key
from paperless_mistral_ocr.layer import build_archive, document_payload, extract_text

DEFAULT_SAMPLES_DIR = Path(__file__).resolve().parents[2] / "tests" / "samples"

EXPECTED_TEMPLATE = """\
# Assertions for the sample-document harness (tests/test_samples.py).
document = "{document}"
mime_type = "{mime_type}"
page_count = {page_count}

[content]
# Substrings that must appear in the paperless content text.
contains = []
# Substrings that must not appear in the content text.
not_contains = []

[archive_text]
# Substrings that must appear in the text extracted back from the archive PDF.
contains = []
"""


def guess_mime_type(path: Path) -> str:
    """Guess a document's MIME type from its extension."""
    mime_type, _ = mimetypes.guess_type(path.name)
    if mime_type is None:
        raise SystemExit(f"Cannot determine MIME type of {path}")
    return mime_type


def _user_path(value: str) -> Path:
    """Parse a CLI path argument, expanding a leading ``~``.

    Shells do not expand ``~`` inside quoted values or ``NAME=value`` make
    arguments, so the expansion is done here.
    """
    return Path(value).expanduser()


def record(document: Path, name: str, samples_dir: Path, max_pages: int) -> Path:
    """Run the live Mistral API on ``document`` and store a new sample directory."""
    mime_type = guess_mime_type(document)
    try:
        api_key = resolve_api_key()
    except ApiKeyError as e:
        raise SystemExit(str(e)) from e
    settings = Settings(api_key=api_key, max_pages=max_pages)
    data, payload_mime = document_payload(document, mime_type)
    response = MistralOcrClient(settings).ocr_bytes(data, payload_mime)

    sample_dir = samples_dir / name
    sample_dir.mkdir(parents=True, exist_ok=True)
    target = sample_dir / f"document{document.suffix.lower()}"
    shutil.copyfile(document, target)
    (sample_dir / "response.json").write_text(
        json.dumps(response, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    expected = sample_dir / "expected.toml"
    if not expected.exists():
        expected.write_text(
            EXPECTED_TEMPLATE.format(
                document=target.name,
                mime_type=mime_type,
                page_count=len(response.get("pages") or []),
            ),
            encoding="utf-8",
        )
    return sample_dir


def render(sample_dir: Path, output_dir: Path) -> None:
    """Run the offline content conversion pipeline on a sample for inspection."""
    import tomllib

    expected = tomllib.loads((sample_dir / "expected.toml").read_text("utf-8"))
    response = json.loads((sample_dir / "response.json").read_text("utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "content.txt").write_text(response_to_text(response) + "\n", "utf-8")
    archive = output_dir / "archive.pdf"
    build_archive(
        sample_dir / expected["document"],
        expected["mime_type"],
        response["pages"],
        archive,
    )
    (output_dir / "archive_text.txt").write_text(extract_text(archive), "utf-8")


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m paperless_mistral_ocr.sample``."""
    parser = argparse.ArgumentParser(prog="python -m paperless_mistral_ocr.sample")
    sub = parser.add_subparsers(dest="command", required=True)

    rec = sub.add_parser("record", help="record a sample with the live Mistral API")
    rec.add_argument("document", type=_user_path)
    rec.add_argument("name")
    rec.add_argument("--samples-dir", type=_user_path, default=DEFAULT_SAMPLES_DIR)
    rec.add_argument("--max-pages", type=int, default=50)

    ren = sub.add_parser(
        "render", help="run the offline content conversion pipeline on a sample"
    )
    ren.add_argument("sample_dir", type=_user_path)
    ren.add_argument("output_dir", type=_user_path)

    args = parser.parse_args(argv)
    if args.command == "record":
        sample_dir = record(args.document, args.name, args.samples_dir, args.max_pages)
        print(f"recorded sample in {sample_dir}; edit expected.toml")
    else:
        render(args.sample_dir, args.output_dir)
        print(f"wrote content.txt, archive.pdf, archive_text.txt to {args.output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
