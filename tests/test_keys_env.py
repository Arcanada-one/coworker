"""keys.env loading: provider keys on disk must reach non-interactive callers.

Regression origin: keys sat in ~/.config/coworker/keys.env while `coworker`
read only os.environ, so any agent invoking it without an interactive shell
got "env var 'X_API_KEY' not set" and every delegation call failed — a total
delegation outage with the credentials already present on the host.
"""

import pathlib

import pytest

from coworker.config import load_keys_env


def _write(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    p = tmp_path / "keys.env"
    p.write_text(body)
    return p


def test_loads_export_prefixed_keys(tmp_path, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    p = _write(tmp_path, "export GROQ_API_KEY=gsk_abc123\n")

    assert load_keys_env(p) == ["GROQ_API_KEY"]

    import os

    assert os.environ["GROQ_API_KEY"] == "gsk_abc123"


def test_loads_bare_assignment_without_export(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    p = _write(tmp_path, "OPENROUTER_API_KEY=sk-or-xyz\n")

    assert load_keys_env(p) == ["OPENROUTER_API_KEY"]


def test_existing_env_var_wins(tmp_path, monkeypatch):
    """`X=... coworker ask` and CI secrets must override the file."""
    monkeypatch.setenv("GROQ_API_KEY", "from-environment")
    p = _write(tmp_path, "export GROQ_API_KEY=from-file\n")

    assert load_keys_env(p) == []

    import os

    assert os.environ["GROQ_API_KEY"] == "from-environment"


def test_missing_file_is_not_an_error(tmp_path):
    """No keys.env is the normal case for a fresh install."""
    assert load_keys_env(tmp_path / "absent.env") == []


def test_comments_blank_lines_and_malformed_entries_are_skipped(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("REAL_API_KEY", raising=False)
    p = _write(
        tmp_path,
        "\n"
        "# a comment = not-a-key\n"
        "   \n"
        "NOT_AN_ASSIGNMENT\n"
        "BAD-NAME=value\n"
        "EMPTY_VALUE=\n"
        "export REAL_API_KEY=v1\n",
    )

    assert load_keys_env(p) == ["REAL_API_KEY"]


@pytest.mark.parametrize("quote", ["'", '"'])
def test_quoted_values_are_unwrapped(tmp_path, monkeypatch, quote):
    monkeypatch.delenv("QUOTED_API_KEY", raising=False)
    p = _write(tmp_path, f"export QUOTED_API_KEY={quote}sk-quoted{quote}\n")

    load_keys_env(p)

    import os

    assert os.environ["QUOTED_API_KEY"] == "sk-quoted"


def test_return_value_carries_names_never_values(tmp_path, monkeypatch):
    """Diagnostics must not become a credential-disclosure path."""
    monkeypatch.delenv("SECRET_API_KEY", raising=False)
    p = _write(tmp_path, "export SECRET_API_KEY=sk-do-not-leak-me\n")

    loaded = load_keys_env(p)

    assert loaded == ["SECRET_API_KEY"]
    assert "sk-do-not-leak-me" not in repr(loaded)


def test_unreadable_file_does_not_crash(tmp_path):
    """A directory at the keys.env path must not raise."""
    d = tmp_path / "keys.env"
    d.mkdir()

    assert load_keys_env(d) == []


def test_cli_main_loads_keys_env_end_to_end(tmp_path, monkeypatch):
    """The call site in main() must be load-bearing.

    Runs the real CLI in a subprocess with XDG_CONFIG_HOME pointed at a
    fixture keys.env and no provider key in the environment — the exact
    non-interactive condition that produced the delegation outage. If main()
    stops calling load_keys_env(), provider resolution reports the key as
    unset and this test fails.
    """
    import os
    import subprocess
    import sys

    cfg = tmp_path / "coworker"
    cfg.mkdir()
    (cfg / "keys.env").write_text("export GROQ_API_KEY=gsk_fixture_key\n")
    (cfg / "providers.yaml").write_text(
        "groq:\n"
        "  base_url: https://127.0.0.1:9/v1\n"
        "  env_key: GROQ_API_KEY\n"
        "  default_model: fixture-model\n"
    )
    (cfg / "profiles.yaml").write_text(
        "code:\n"
        "  system_prompt: fixture\n"
        "  recommended_provider: groq\n"
    )
    (tmp_path / "doc.md").write_text("fixture body\n")

    env = dict(os.environ)
    env["XDG_CONFIG_HOME"] = str(tmp_path)
    env["COWORKER_NO_LOG"] = "1"
    env.pop("GROQ_API_KEY", None)

    r = subprocess.run(
        [
            sys.executable, "-m", "coworker.cli", "ask",
            "--provider", "groq",
            "--paths", str(tmp_path / "doc.md"),
            "--question", "probe",
        ],
        capture_output=True, text=True, env=env,
    )

    combined = r.stdout + r.stderr
    assert "env var 'GROQ_API_KEY' not set" not in combined, (
        "keys.env was not loaded by main(); non-interactive callers are broken"
    )
    assert "gsk_fixture_key" not in combined, "key value must never be printed"


def test_key_command_still_wins_over_keys_env(tmp_path, monkeypatch):
    """keys.env must not weaken the scoped-credential path.

    `key_command` is the preferred mechanism (call-time, no long-lived env
    secret). Loading keys.env populates os.environ, so it must not shadow a
    provider that declares key_command.
    """
    from coworker.providers import resolve_api_key

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    load_keys_env(_write(tmp_path, "export GROQ_API_KEY=from-keys-env\n"))

    prov_cfg = {
        "env_key": "GROQ_API_KEY",
        "key_command": "printf from-key-command",
    }

    assert resolve_api_key(prov_cfg) == "from-key-command"


def test_env_key_path_uses_keys_env_when_no_key_command(tmp_path, monkeypatch):
    """Without key_command, the keys.env value is what reaches the provider."""
    from coworker.providers import resolve_api_key

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    load_keys_env(_write(tmp_path, "export GROQ_API_KEY=from-keys-env\n"))

    assert resolve_api_key({"env_key": "GROQ_API_KEY"}) == "from-keys-env"
