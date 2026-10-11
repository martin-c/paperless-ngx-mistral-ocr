# Development shortcuts. Recipes must be indented with a TAB, not spaces.
#
# Variables can be overridden on the command line or exported from the shell:
#   make test-live KEY_COMMAND="secret-tool lookup service mistral-api-key"
#   make record SAMPLE=path/to/scan.pdf NAME=utility_bill

PYTHON      ?= .venv/bin/python
KEY_COMMAND ?= keychain
NAME        ?= $(basename $(notdir $(SAMPLE)))

.PHONY: test test-live record lint format build clean

test:
	$(PYTHON) -m pytest

test-live:
	MISTRAL_LIVE_TESTS=1 MISTRAL_API_KEY_COMMAND="$(KEY_COMMAND)" $(PYTHON) -m pytest tests/test_live.py

record:
	@test -n "$(SAMPLE)" || { echo "usage: make record SAMPLE=path/to/document.pdf [NAME=sample_name]"; exit 2; }
	MISTRAL_API_KEY_COMMAND="$(KEY_COMMAND)" $(PYTHON) -m paperless_mistral_ocr.sample record "$(SAMPLE)" "$(NAME)"

lint:
	$(PYTHON) -m ruff check src tests
	$(PYTHON) -m ruff format --check src tests

format:
	$(PYTHON) -m ruff format src tests

build:
	uv build --wheel

clean:
	rm -rf dist build .pytest_cache .ruff_cache
