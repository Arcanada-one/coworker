"""Unit tests for coworker.plugins.rtk_passthrough.

Tests run against a tmp_path-scoped store via the ``store_path=`` kwarg —
never touch operator's real ~/.config/coworker/rtk-passthrough.json.
"""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from coworker.plugins import rtk_passthrough as rp


# ---------- seed_default ----------


def test_seed_default_creates_store_with_canonical_defaults(tmp_path):
    store = tmp_path / "rtk-passthrough.json"
    assert not store.exists()

    created = rp.seed_default(store_path=store)
    assert created is True
    assert store.exists()

    data = json.loads(store.read_text())
    assert set(data["patterns"]) == set(rp.DEFAULT_PATTERNS)


def test_seed_default_is_idempotent_when_operator_data_present(tmp_path):
    """Re-seed must NOT overwrite operator-added patterns."""
    store = tmp_path / "rtk-passthrough.json"
    rp.seed_default(store_path=store)
    rp.add_pattern("operator-custom-pattern", store_path=store)

    created = rp.seed_default(store_path=store)
    assert created is False, "store with content must not be re-seeded"

    patterns = rp.list_patterns(store_path=store)
    assert "operator-custom-pattern" in patterns
    # Defaults still there.
    assert "git push" in patterns


def test_seed_default_force_rewinds_to_canonical(tmp_path):
    store = tmp_path / "rtk-passthrough.json"
    rp.seed_default(store_path=store)
    rp.add_pattern("operator-custom-pattern", store_path=store)

    created = rp.seed_default(store_path=store, force=True)
    assert created is True
    patterns = rp.list_patterns(store_path=store)
    assert "operator-custom-pattern" not in patterns
    assert set(patterns) == set(rp.DEFAULT_PATTERNS)


# ---------- add / remove / list ----------


def test_default_seed_has_at_least_13_patterns(tmp_path):
    """AC-2 floor: default allowlist covers ≥13 canonical git/gh markers."""
    store = tmp_path / "rtk-passthrough.json"
    rp.seed_default(store_path=store)
    assert rp.count(store_path=store) >= 13


def test_add_then_list_includes_new_pattern(tmp_path):
    store = tmp_path / "rtk-passthrough.json"
    rp.seed_default(store_path=store)
    added = rp.add_pattern("glab mr", store_path=store)
    assert added is True
    assert "glab mr" in rp.list_patterns(store_path=store)


def test_add_dedup_returns_false_on_duplicate(tmp_path):
    store = tmp_path / "rtk-passthrough.json"
    rp.seed_default(store_path=store)
    first = rp.add_pattern("git push", store_path=store)
    assert first is False  # already in defaults
    # Count must remain unchanged after duplicate add.
    n_before = rp.count(store_path=store)
    rp.add_pattern("git push", store_path=store)
    assert rp.count(store_path=store) == n_before


def test_add_empty_pattern_rejected(tmp_path):
    store = tmp_path / "rtk-passthrough.json"
    rp.seed_default(store_path=store)
    n_before = rp.count(store_path=store)
    assert rp.add_pattern("   ", store_path=store) is False
    assert rp.add_pattern("", store_path=store) is False
    assert rp.count(store_path=store) == n_before


def test_remove_existing_pattern_returns_true(tmp_path):
    store = tmp_path / "rtk-passthrough.json"
    rp.seed_default(store_path=store)
    assert rp.remove_pattern("git push", store_path=store) is True
    assert "git push" not in rp.list_patterns(store_path=store)


def test_remove_missing_pattern_soft_fail(tmp_path, capsys):
    store = tmp_path / "rtk-passthrough.json"
    rp.seed_default(store_path=store)
    removed = rp.remove_pattern("nonexistent-pattern", store_path=store)
    assert removed is False
    err = capsys.readouterr().err
    assert "not in store" in err


def test_list_patterns_sorted_deterministic(tmp_path):
    store = tmp_path / "rtk-passthrough.json"
    rp.seed_default(store_path=store)
    rp.add_pattern("zzz-last", store_path=store)
    rp.add_pattern("aaa-first", store_path=store)
    patterns = rp.list_patterns(store_path=store)
    assert patterns == sorted(patterns)


def test_add_roundtrip_via_remove(tmp_path):
    """AC-3 round-trip: add → list contains → remove → list does not contain."""
    store = tmp_path / "rtk-passthrough.json"
    rp.seed_default(store_path=store)
    rp.add_pattern("glab mr", store_path=store)
    assert "glab mr" in rp.list_patterns(store_path=store)
    rp.remove_pattern("glab mr", store_path=store)
    assert "glab mr" not in rp.list_patterns(store_path=store)


# ---------- load_patterns fallback behaviour ----------


def test_load_patterns_absent_store_returns_defaults(tmp_path):
    store = tmp_path / "does-not-exist.json"
    assert rp.load_patterns(store_path=store) == sorted(set(rp.DEFAULT_PATTERNS))


def test_load_patterns_malformed_json_warns_and_falls_back(tmp_path, capsys):
    store = tmp_path / "rtk-passthrough.json"
    store.write_text("this is not json {{{")
    patterns = rp.load_patterns(store_path=store)
    assert patterns == sorted(set(rp.DEFAULT_PATTERNS))
    err = capsys.readouterr().err
    assert "unreadable" in err


def test_load_patterns_wrong_shape_falls_back(tmp_path):
    store = tmp_path / "rtk-passthrough.json"
    store.write_text(json.dumps(["a", "b", "c"]))  # top-level list, not dict
    patterns = rp.load_patterns(store_path=store)
    assert patterns == sorted(set(rp.DEFAULT_PATTERNS))


def test_load_patterns_empty_patterns_array_falls_back(tmp_path):
    store = tmp_path / "rtk-passthrough.json"
    store.write_text(json.dumps({"patterns": []}))
    patterns = rp.load_patterns(store_path=store)
    # Empty store ⇒ defaults (guard-friendly fail-safe).
    assert patterns == sorted(set(rp.DEFAULT_PATTERNS))


# ---------- env-var override ----------


def test_env_var_override_resolves_store_path(tmp_path, monkeypatch):
    env_store = tmp_path / "env-override.json"
    monkeypatch.setenv("COWORKER_RTK_PASSTHROUGH_PATH", str(env_store))
    rp.seed_default()  # no explicit store_path → consults env
    assert env_store.exists()


# ---------- cmd_passthrough dispatch ----------


def test_cmd_passthrough_list_prints_one_per_line(tmp_path, capsys):
    store = tmp_path / "rtk-passthrough.json"
    rp.seed_default(store_path=store)
    ns = Namespace(passthrough_action="list", store_path=str(store))
    rc = rp.cmd_passthrough(ns)
    assert rc == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert "git push" in out
    assert len(out) >= 13


def test_cmd_passthrough_add_persists(tmp_path, capsys):
    store = tmp_path / "rtk-passthrough.json"
    rp.seed_default(store_path=store)
    ns = Namespace(passthrough_action="add", pattern="glab mr", store_path=str(store))
    rc = rp.cmd_passthrough(ns)
    assert rc == 0
    assert "glab mr" in rp.list_patterns(store_path=store)


def test_cmd_passthrough_remove_soft_fail_exits_zero(tmp_path):
    store = tmp_path / "rtk-passthrough.json"
    rp.seed_default(store_path=store)
    ns = Namespace(passthrough_action="remove", pattern="nonexistent", store_path=str(store))
    rc = rp.cmd_passthrough(ns)
    assert rc == 0  # idempotent UX
