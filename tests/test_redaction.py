"""Corpus-blob redaction tests — built-in patterns, config extension, log_call wiring."""

import json

import pytest

from coworker import logger
from coworker.redaction import (
    load_extra_patterns,
    redact_payload,
    redact_text,
)

# --- built-in patterns: each secret shape is masked -------------------------

@pytest.mark.parametrize(
    "secret,label",
    [
        ("sk-abcdefghijklmnop1234567890", "api-key"),
        ("sk-or-v1-abcdef1234567890abcdef", "api-key"),
        ("gsk_ABCDEFGHIJKLMNOPQRSTUVWX", "api-key"),
        ("AKIAIOSFODNN7EXAMPLE", "aws-key"),
    ],
)
def test_builtin_pattern_masks_secret(secret, label):
    out = redact_text(f"here is the key {secret} end")
    assert secret not in out
    assert f"[REDACTED:{label}]" in out


def test_bearer_token_masked_keeps_word():
    out = redact_text("Authorization: Bearer abcDEF123.token-value")
    assert "abcDEF123.token-value" not in out
    assert "Bearer [REDACTED:bearer]" in out


def test_pem_private_key_block_masked():
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEA1234567890\nabcdefg\n"
        "-----END RSA PRIVATE KEY-----"
    )
    out = redact_text(f"config:\n{pem}\ndone")
    assert "MIIEowIBAAKCAQEA1234567890" not in out
    assert "[REDACTED:private-key]" in out


def test_assignment_value_masked_key_name_kept():
    out = redact_text("password = hunter2secret")
    assert "hunter2secret" not in out
    assert "password" in out
    assert "[REDACTED:assignment]" in out


def test_benign_prose_untouched():
    prose = "The quick brown fox jumps over the lazy dog near the river bank."
    assert redact_text(prose) == prose


# --- payload redaction ------------------------------------------------------

def test_redact_payload_masks_messages_and_response():
    msgs = [{"role": "user", "content": "my key is sk-abcdefghijklmnop1234567890"}]
    red_msgs, red_resp = redact_payload(msgs, "leaked AKIAIOSFODNN7EXAMPLE too")
    assert "sk-abcdefghijklmnop1234567890" not in red_msgs[0]["content"]
    assert "AKIAIOSFODNN7EXAMPLE" not in red_resp
    # original list is not mutated
    assert "sk-abcdefghijklmnop1234567890" in msgs[0]["content"]


# --- config extension -------------------------------------------------------

def test_extra_patterns_additive(tmp_path):
    cfg = tmp_path / "redaction.yaml"
    cfg.write_text("- name: internal-id\n  pattern: 'ACME-[0-9]{4}'\n")
    extra = load_extra_patterns(cfg)
    labels = [lbl for lbl, _ in extra]
    assert "internal-id" in labels
    # builtins + extra applied together
    from coworker.redaction import BUILTIN_PATTERNS

    out = redact_text("ticket ACME-4242 and sk-abcdefghijklmnop1234567890", patterns=BUILTIN_PATTERNS + extra)
    assert "ACME-4242" not in out
    assert "[REDACTED:internal-id]" in out
    assert "sk-abcdefghijklmnop1234567890" not in out


def test_malformed_extra_patterns_ignored(tmp_path, capsys):
    cfg = tmp_path / "redaction.yaml"
    cfg.write_text("- name: bad\n  pattern: '([unclosed'\n- notmapping\n")
    extra = load_extra_patterns(cfg)
    # bad regex + non-mapping entry both skipped, no crash
    assert extra == []
    assert "coworker" in capsys.readouterr().err.lower()


def test_missing_extra_patterns_file_returns_empty(tmp_path):
    assert load_extra_patterns(tmp_path / "nope.yaml") == []


# --- log_call integration ---------------------------------------------------

class _FakeUsage:
    prompt_tokens = 10
    completion_tokens = 5
    prompt_tokens_details = None
    cached_tokens = 0


class _FakeChoice:
    finish_reason = "stop"


class _FakeResp:
    usage = _FakeUsage()
    choices = [_FakeChoice()]


def _run_log_call(tmp_path, monkeypatch, *, no_redact=False):
    monkeypatch.setenv("COWORKER_LOG_CORPUS", "1")
    monkeypatch.delenv("COWORKER_NO_LOG", raising=False)
    if no_redact:
        monkeypatch.setenv("COWORKER_NO_REDACT", "1")
    else:
        monkeypatch.delenv("COWORKER_NO_REDACT", raising=False)
    log_dir = tmp_path / "log"
    blobs_root = tmp_path / "blobs"
    logger.log_call(
        resp=_FakeResp(),
        provider_name="deepseek",
        provider_cfg={"pricing": None},
        model="deepseek-v4-flash",
        profile_name="code",
        subcommand="ask",
        user_messages=[{"role": "user", "content": "token = sk-abcdefghijklmnop1234567890"}],
        response_text="ok",
        latency_ms=1.0,
        task_id=None,
        system_prompt="",
        log_dir=log_dir,
        blobs_root=blobs_root,
    )
    blobs = list(blobs_root.rglob("*.json"))
    assert len(blobs) == 1
    return blobs[0].read_text()


def test_log_call_redacts_corpus_blob(tmp_path, monkeypatch):
    body = _run_log_call(tmp_path, monkeypatch)
    assert "sk-abcdefghijklmnop1234567890" not in body
    assert "[REDACTED:" in body


def test_no_redact_env_stores_raw(tmp_path, monkeypatch):
    body = _run_log_call(tmp_path, monkeypatch, no_redact=True)
    assert "sk-abcdefghijklmnop1234567890" in body


def test_redacted_blob_hash_deterministic(tmp_path, monkeypatch):
    body = _run_log_call(tmp_path, monkeypatch)
    payload = json.loads(body)
    # re-serialise + re-redact must yield identical content (stable dedup/debug)
    again, _ = redact_payload(
        [{"role": "user", "content": "token = sk-abcdefghijklmnop1234567890"}], "ok"
    )
    assert payload["user_messages"] == again
