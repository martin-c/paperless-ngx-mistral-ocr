# Sample documents (untracked)

This directory is scanned by `tests/test_samples.py`. Its contents are
deliberately not committed: real scans tend to contain personal data. Only
this file is tracked; everything else here is ignored by git.

Layout, one directory per sample:

    tests/samples/<name>/document.pdf   (or .png, .jpg, ...)  the source document
    tests/samples/<name>/response.json  the recorded Mistral OCR response
    tests/samples/<name>/expected.toml  assertions: document, mime_type,
                                        page_count, [content] contains /
                                        not_contains, [archive_text] contains

Record a sample with the live API (needs an API key, see the README):

    python -m paperless_mistral_ocr.sample record path/to/scan.pdf <name>

then fill in `expected.toml`. Optionally inspect the offline result with

    python -m paperless_mistral_ocr.sample render tests/samples/<name> out/

When no sample directories exist the harness skips.
