import pytest

from paperless_mistral_ocr.keys import (
    ENV_COMMAND,
    ENV_FILE,
    ENV_KEY,
    KEYCHAIN_COMMAND,
    ApiKeyError,
    resolve_api_key,
)


def test_env_var():
    assert resolve_api_key({ENV_KEY: "  abc  "}) == "abc"


def test_file(tmp_path):
    key_file = tmp_path / "key"
    key_file.write_text("\n  from-file \nsecond line\n")
    assert resolve_api_key({ENV_FILE: str(key_file)}) == "from-file"


def test_missing_or_empty_file(tmp_path):
    with pytest.raises(ApiKeyError, match="cannot read"):
        resolve_api_key({ENV_FILE: str(tmp_path / "nope")})
    empty = tmp_path / "empty"
    empty.write_text("\n")
    with pytest.raises(ApiKeyError, match="empty"):
        resolve_api_key({ENV_FILE: str(empty)})


def test_command():
    assert resolve_api_key({ENV_COMMAND: "printf 'from-cmd\\nmore'"}) == "from-cmd"


def test_command_failure_and_empty_output():
    with pytest.raises(ApiKeyError, match="failed"):
        resolve_api_key({ENV_COMMAND: "exit 3"})
    with pytest.raises(ApiKeyError, match="printed no key"):
        resolve_api_key({ENV_COMMAND: "true"})


def test_keychain_alias_expands(monkeypatch):
    seen = []

    def fake_run(command, **kwargs):
        seen.append(command)

        class Result:
            returncode = 0
            stdout = "kc-key\n"
            stderr = ""

        return Result()

    monkeypatch.setattr("paperless_mistral_ocr.keys.subprocess.run", fake_run)
    assert resolve_api_key({ENV_COMMAND: "keychain"}) == "kc-key"
    assert seen == [KEYCHAIN_COMMAND]


def test_precedence(tmp_path):
    key_file = tmp_path / "key"
    key_file.write_text("from-file")
    env = {ENV_KEY: "from-env", ENV_FILE: str(key_file), ENV_COMMAND: "echo cmd"}
    assert resolve_api_key(env) == "from-env"
    del env[ENV_KEY]
    assert resolve_api_key(env) == "from-file"
    del env[ENV_FILE]
    assert resolve_api_key(env) == "cmd"


def test_nothing_configured_lists_options():
    with pytest.raises(ApiKeyError) as info:
        resolve_api_key({ENV_KEY: "   "})
    message = str(info.value)
    assert ENV_KEY in message and ENV_FILE in message and ENV_COMMAND in message
