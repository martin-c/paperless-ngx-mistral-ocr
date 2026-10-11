"""Resolve the Mistral API key for the sample recorder and live tests.

The plugin itself reads ``PAPERLESS_MISTRAL_API_KEY``; this module serves the
developer tooling, where the key usually lives in a secret store. Sources are
tried in order:

1. ``MISTRAL_API_KEY`` - the key itself.
2. ``MISTRAL_API_KEY_FILE`` - path of a file whose first line is the key.
3. ``MISTRAL_API_KEY_COMMAND`` - a shell command whose standard output is the
   key (first line). The alias ``keychain`` expands to the macOS command
   ``security find-generic-password -s mistral-api-key -w``.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

ENV_KEY = "MISTRAL_API_KEY"
ENV_FILE = "MISTRAL_API_KEY_FILE"
ENV_COMMAND = "MISTRAL_API_KEY_COMMAND"
KEYCHAIN_COMMAND = "security find-generic-password -s mistral-api-key -w"
COMMAND_ALIASES: dict[str, str] = {"keychain": KEYCHAIN_COMMAND}
COMMAND_TIMEOUT = 30

HELP = (
    f"No Mistral API key found. Set {ENV_KEY} to the key, {ENV_FILE} to a file "
    f"containing it, or {ENV_COMMAND} to a command that prints it (for example "
    f"'{KEYCHAIN_COMMAND}', or the alias 'keychain')."
)


class ApiKeyError(RuntimeError):
    """Raised when no API key can be resolved."""


def resolve_api_key(environ: Mapping[str, str] | None = None) -> str:
    """Return the API key from the first source that yields one.

    Args:
        environ: Mapping to read variables from; defaults to ``os.environ``.

    Returns:
        The API key, stripped of surrounding whitespace.

    Raises:
        ApiKeyError: If no source is configured or the configured source
            produced no key.
    """
    env = os.environ if environ is None else environ
    key = env.get(ENV_KEY, "").strip()
    if key:
        return key
    file_name = env.get(ENV_FILE, "").strip()
    if file_name:
        return _key_from_file(Path(file_name))
    command = env.get(ENV_COMMAND, "").strip()
    if command:
        return _key_from_command(COMMAND_ALIASES.get(command, command))
    raise ApiKeyError(HELP)


def _first_line(text: str) -> str:
    """Return the first non-empty line of ``text``, stripped."""
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _key_from_file(path: Path) -> str:
    """Read the key from the first line of ``path``."""
    try:
        key = _first_line(path.read_text("utf-8"))
    except OSError as e:
        raise ApiKeyError(f"{ENV_FILE}: cannot read {path}: {e}") from e
    if not key:
        raise ApiKeyError(f"{ENV_FILE}: {path} is empty")
    return key


def _key_from_command(command: str) -> str:
    """Run ``command`` in a shell and return the first line of its output."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ApiKeyError(f"{ENV_COMMAND}: failed to run {command!r}: {e}") from e
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit status {result.returncode}"
        raise ApiKeyError(f"{ENV_COMMAND}: {command!r} failed: {detail}")
    key = _first_line(result.stdout)
    if not key:
        raise ApiKeyError(f"{ENV_COMMAND}: {command!r} printed no key")
    return key
