# paperless-ngx-mistral-ocr

A parser plugin for [paperless-ngx](https://docs.paperless-ngx.com/) 3.3 that
OCRs PDFs and images with Mistral's Document AI OCR API instead of the built-in
tesseract/ocrmypdf step.

Source and issues: https://github.com/martin-c/paperless-ngx-mistral-ocr

Not affiliated with or endorsed by paperless-ngx or Mistral AI.

## What it does and why

Paperless normally runs every scanned document through tesseract (via
ocrmypdf). On handwriting, poor scans and tables the results are mediocre,
which is why people bolt on a second pass with tools such as paperless-gpt
that re-OCR documents through an LLM and write the text back. With this plugin
Mistral OCR runs as the parser itself: the text paperless indexes and the
searchable archive PDF both come straight from Mistral, so a separate
post-processing pass is no longer needed.

For each PDF or image the plugin:

- sends the document to Mistral OCR once (as a base64 data URI),
- turns the returned markdown into the plain-text content paperless stores and
  indexes (tables substituted inline, image references dropped),
- builds the archive PDF from the original pages plus an invisible text layer
  placed where Mistral located each text block, so searching and copying text
  in the viewer works and highlights the right spot on the scan.

## How it works

Parser selection. Paperless 3.3 discovers parsers through the
`paperless_ngx.parsers` entry point group. Every candidate parser is asked for
a score; the highest wins. Tesseract scores 10, the built-in remote (Azure)
parser 20. This plugin scores 20 when `PAPERLESS_MISTRAL_API_KEY` is set and
declines (scores `None`) when it is not, so without a key paperless behaves
exactly as before. At equal scores an external parser beats a built-in one.

Remote-service flag. The parser declares `uses_remote_service = True`, like
the built-in Azure parser. Paperless only considers such parsers when
`PAPERLESS_REMOTE_OCR_MODE` is `always` (the default) or a workflow enabled
remote OCR for the document. Leave that setting at its default (or set it to
`always`) and every supported document goes through Mistral.

Parse flow. The consumer first decides whether an archive copy is wanted
(`ARCHIVE_FILE_GENERATION`, default `auto`): images always get one; a PDF gets
one unless paperless considers it born-digital (tagged PDF, or pdftotext
already finds more than 50 characters). Then:

- PDF, archive wanted: the PDF goes to Mistral; content and archive come from
  the response.
- PDF, no archive wanted (born-digital): the remote call is skipped and the
  text is read locally with pdftotext, mirroring paperless' own remote parser.
  No API cost.
- Image (PNG, JPEG, TIFF, BMP, GIF, WebP): JPEG and PNG are sent as images;
  other formats are converted to a PDF with PyMuPDF first (this also handles
  multi-page TIFFs). The archive is a PDF page built from the image plus the
  text layer, and it is also used for the thumbnail.

Content. Mistral returns one markdown string per page in which tables appear
as `[tbl-0.md](tbl-0.md)` placeholders and images as `![img-0.jpeg](...)`.
The plugin replaces each placeholder with the table's rows (cells joined with
`|`), removes image references, strips heading and emphasis markers, includes
page header/footer text when present, and normalises whitespace. Pages are
joined with a blank line.

Archive PDF. Block coordinates come back in pixels of the image Mistral
rendered (`dimensions.width` x `height`) and are scaled to the PDF page size.
Each block's text is written in PDF render mode 3 (invisible): a single line is
fitted to the block width; longer text is wrapped inside the block, shrinking
the font until it fits; table blocks are written one row per line, spaced
evenly over the block height. Before writing, a page that already carries an
invisible-only text layer (an earlier tesseract run) has that layer removed so
old and new text do not mix; a page with visible text (born-digital) is left
untouched. Rotated pages are normalised so the layer lines up with what the
viewer shows.

Confidence. Mistral reports per-block confidence. Blocks below 0.5 are logged
at INFO level but their text is kept; nothing is dropped.

Failures. Configuration errors, API errors (after retries on 429/5xx and
network failures with exponential backoff) and archive-building errors raise
paperless' `ParseError`, so the consume task fails visibly in the UI and can be
retried from the task list. Nothing is stored with empty content.

Date detection. The parser returns no date of its own; paperless' usual date
detection runs on the extracted content and the filename.

## Configuration

All settings are environment variables read by the paperless process (web
server and task workers). Only the API key is required.

- `PAPERLESS_MISTRAL_API_KEY` - Mistral API key. Required; without it the
  plugin stays inactive and tesseract is used.
- `PAPERLESS_MISTRAL_MODEL` - OCR model, default `mistral-ocr-latest`.
- `PAPERLESS_MISTRAL_MAX_PAGES` - maximum pages sent per document, default
  `50`, `0` for no limit. Passed as the API's `pages` parameter (`0-49`);
  pages beyond the limit get neither content nor a text layer.
- `PAPERLESS_MISTRAL_TIMEOUT` - HTTP timeout per request in seconds, default
  `120`.
- `PAPERLESS_MISTRAL_ENDPOINT` - OCR endpoint, default
  `https://api.mistral.ai/v1/ocr`.

Related paperless settings: keep `PAPERLESS_REMOTE_OCR_MODE` at `always`
(default) so the parser is eligible for every document, and leave
`PAPERLESS_ARCHIVE_FILE_GENERATION` at `auto` unless you want born-digital
PDFs re-OCRed too (`always`), which costs API calls per page.

## Installation on the paperless-ngx Docker image

The image executes every file in `/custom-cont-init.d` as root before the web
server starts. The directory and its contents must be owned by root and must
not be writable by others, otherwise paperless refuses to run anything in it.

Prepare a directory on the host containing the init script from
`deploy/custom-cont-init.d/` at the top level and the plugin next to it,
either as a built wheel or as the source tree:

    10-paperless-ngx-mistral-ocr.sh
    paperless_ngx_mistral_ocr-0.1.0-py3-none-any.whl

or

    10-paperless-ngx-mistral-ocr.sh
    paperless-ngx-mistral-ocr/pyproject.toml
    paperless-ngx-mistral-ocr/README.md
    paperless-ngx-mistral-ocr/LICENSE
    paperless-ngx-mistral-ocr/src/paperless_mistral_ocr/...

Mount it read-only at `/custom-cont-init.d`. The script prefers a wheel when
one exists and installs into the image's system Python with
`uv pip install --system`, the same way paperless 3.3.0 installs its own
dependencies (its base image ships uv and Python 3.14). It compares the
installed version with the wheel or `pyproject.toml` version and skips the
install on restarts when they match, so container restarts stay fast.
Dependencies: `httpx` (already in the image) and `pymupdf` (not in paperless'
dependencies; a self-contained wheel installed alongside). Installing from
the source tree also fetches `hatchling` from PyPI, so the container needs
outbound HTTPS at start-up, which it needs for the API anyway.

Set `PAPERLESS_MISTRAL_API_KEY` (and optionally the other variables) in the
container environment and restart. The start-up log shows the `[custom-init]`
lines from the script, then `Loaded third-party parser 'Mistral Document AI
OCR' ...`, and each consumed document logs `Document handled by third-party
parser ...`.

To update, replace the wheel or source with a newer version and restart; the
script reinstalls only when the version changed.

Bare metal: `pip install /path/to/paperless-ngx-mistral-ocr` (or the wheel) into
the Python environment that runs paperless, then restart the paperless
services.

## Development

Requirements: `uv` (or any Python 3.12 or newer; the paperless 3.3.0 image
runs Python 3.14).

Setup, from the project directory:

- `uv venv --python 3.14 .venv`
- `uv pip install --python .venv/bin/python -e ".[dev]"`

Checks:

- `.venv/bin/python -m pytest` runs the unit tests and, if any are present,
  the private sample documents (see below). No API calls.
- `.venv/bin/ruff check src tests` and `.venv/bin/ruff format --check src tests`
  must pass; the code follows the Google Python Style Guide.
- `MISTRAL_LIVE_TESTS=1 .venv/bin/python -m pytest tests/test_live.py` runs one
  real API call on a generated fictional scan (skipped by default; needs an
  API key as described below).

A `Makefile` wraps these commands: `make test` (offline suite, the default),
`make lint`, `make format`, `make build`, `make test-live` and
`make record SAMPLE=path/to/scan.pdf`. The live targets pass
`MISTRAL_API_KEY_COMMAND`, which defaults to the `keychain` alias; override it
with `make test-live KEY_COMMAND="..."` or by exporting `KEY_COMMAND`. `PYTHON`
defaults to the venv interpreter.

Building a wheel: `uv build --wheel` writes
`dist/paperless_ngx_mistral_ocr-<version>-py3-none-any.whl`, which can be dropped
next to the init script.

Layout: `src/paperless_mistral_ocr/config.py` (environment variables),
`client.py` (HTTP, retries, errors), `content.py` (response to plain text),
`layer.py` (archive PDF and text layer, PyMuPDF) and `keys.py` (API key
lookup for tooling) are independent of paperless; `parser.py` is the thin
adapter implementing paperless' `ParserProtocol` and imports paperless/Django
modules lazily. `sample.py` is the sample-harness CLI. Unit tests use a
hand-written fictional response in `tests/fixtures/sample_response.json` and
scans generated on the fly.

### API key for the recorder and live test

The plugin itself only reads `PAPERLESS_MISTRAL_API_KEY`. The sample recorder
and the live test resolve a key from the first of:

- `MISTRAL_API_KEY` - the key itself;
- `MISTRAL_API_KEY_FILE` - path of a file whose first line is the key;
- `MISTRAL_API_KEY_COMMAND` - a shell command that prints the key, for any
  secret store: `security find-generic-password -s mistral-api-key -w` (macOS
  keychain; the alias `keychain` expands to exactly this command),
  `secret-tool lookup service mistral-api-key` (GNOME keyring),
  `pass show mistral/api`, `op read op://vault/mistral/credential`.

If none yields a key the tools stop with a message listing these options.

### Private sample documents

`tests/samples/` holds real documents for end-to-end checks: one directory per
sample with the source document, the recorded API `response.json` and an
`expected.toml` with assertions (substrings that must or must not appear in
the content, the page count, substrings that must appear in the text read back
from the archive PDF). `tests/test_samples.py` discovers every such directory,
runs the full offline pipeline on it and checks the assertions; it skips when
there are none. Because real scans usually contain personal data the
directory's contents are gitignored and never published; only its README is
tracked.

To add a sample:

- `.venv/bin/python -m paperless_mistral_ocr.sample record path/to/scan.pdf my_sample`
  calls the live API and writes `tests/samples/my_sample/` with the document,
  `response.json` and a skeleton `expected.toml`;
- fill in the assertions in `expected.toml`;
- optionally `.venv/bin/python -m paperless_mistral_ocr.sample render tests/samples/my_sample out/`
  writes `content.txt`, `archive.pdf` and `archive_text.txt` for inspection;
- run `pytest`.

## Known limitations

- Table rows in the text layer are spaced evenly over the table block; Mistral
  gives no per-row or per-cell coordinates, so highlights inside tall tables
  can be slightly off. The content text is unaffected.
- No date detection beyond paperless' own.
- Every page sent costs API usage; born-digital PDFs are skipped only when
  paperless itself decides no archive is needed. A PDF that already has a
  garbage text layer from an earlier OCR counts as born-digital under
  `ARCHIVE_FILE_GENERATION=auto` and will not be re-OCRed unless that setting
  is `always`.
- Pages beyond `PAPERLESS_MISTRAL_MAX_PAGES` are not OCRed at all. Mistral's
  own document size and page limits apply.
- Mistral can misread graphics as text: on a handwritten invoice used during
  development the company logo came back as a short address-like string plus
  a phone number, with confidence below 0.5. Such blocks are logged but kept.
- Pages with visible (born-digital) text are not given a text layer when they
  are re-OCRed with `ARCHIVE_FILE_GENERATION=always`; their existing text
  stays as it is.
- Only `image/jpeg` and `image/png` are sent to the API as images; other
  formats are converted to PDF first. The image data URI format was verified
  for PDF and JPEG input only.

## License

MIT, see `LICENSE`.
