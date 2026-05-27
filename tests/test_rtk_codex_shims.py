"""Unit tests for the Codex CLI parity layer (PATH shim + config patch).

No live codex calls. Filesystem isolated via tmp_path + monkeypatched
SHIM_DIR / CODEX_CONFIG / HOME.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest


@pytest.fixture
def isolated(monkeypatch, tmp_path: Path):
    """Redirect all module-level paths into tmp_path."""
    from coworker.plugins import rtk_codex_shims as mod

    shim_dir = tmp_path / "rtk-shims"
    fake_rtk = tmp_path / "fake-rtk"
    fake_rtk.write_text("#!/bin/bash\necho rtk-mock\n")
    fake_rtk.chmod(0o755)

    # Fake real binaries for a couple of commands.
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    for cmd in ("ls", "git", "grep"):
        real = bin_dir / cmd
        real.write_text(f"#!/bin/bash\necho real-{cmd}\n")
        real.chmod(0o755)

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".claude").mkdir()
    zprofile = fake_home / ".zprofile"
    bash_profile = fake_home / ".bash_profile"

    monkeypatch.setattr(mod, "SHIM_DIR", shim_dir)
    monkeypatch.setattr(mod, "ZPROFILE", zprofile)
    monkeypatch.setattr(mod, "BASH_PROFILE", bash_profile)
    monkeypatch.setattr(mod, "_rtk_binary_path", lambda: str(fake_rtk))
    # Force macOS branch so ZPROFILE is always patched even when missing.
    monkeypatch.setattr(mod.sys, "platform", "darwin")
    # Limit shim list so tests aren't gated on the host having psql/kubectl/etc.
    monkeypatch.setattr(mod, "SHIM_COMMANDS", ("ls", "git", "grep"))

    # Override PATH so resolver picks our fake bin dir.
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}/bin")
    monkeypatch.setenv("HOME", str(fake_home))

    return {
        "mod": mod,
        "shim_dir": shim_dir,
        "zprofile": zprofile,
        "bash_profile": bash_profile,
        "fake_rtk": fake_rtk,
        "fake_bin": bin_dir,
        "fake_home": fake_home,
    }


def test_install_shims_writes_executable_wrappers(isolated):
    mod = isolated["mod"]
    written, paths = mod.install_shims(verbose=False)
    assert written == 3
    assert len(paths) == 3
    for cmd in ("ls", "git", "grep"):
        shim = isolated["shim_dir"] / cmd
        assert shim.exists()
        mode = stat.S_IMODE(shim.stat().st_mode)
        assert mode & 0o111, f"{shim} should be executable"
        body = shim.read_text()
        assert "Coworker RTK shim" in body
        assert str(isolated["fake_rtk"]) in body
        assert str(isolated["fake_bin"] / cmd) in body
        assert "_COWORKER_RTK_SHIM_ACTIVE" in body


def test_shim_dir_is_user_only(isolated):
    mod = isolated["mod"]
    mod.install_shims(verbose=False)
    mode = stat.S_IMODE(isolated["shim_dir"].stat().st_mode)
    assert mode == 0o700, f"expected 0o700, got {oct(mode)}"


def test_install_refuses_world_writable_dir(isolated):
    mod = isolated["mod"]
    isolated["shim_dir"].mkdir()
    isolated["shim_dir"].chmod(0o777)
    with pytest.raises(RuntimeError, match="world/group-writable"):
        mod.install_shims(verbose=False)


def test_install_skips_existing_non_shim_file(isolated, capsys):
    mod = isolated["mod"]
    isolated["shim_dir"].mkdir(mode=0o700)
    foreign = isolated["shim_dir"] / "ls"
    foreign.write_text("#!/bin/bash\necho NOT_OUR_SHIM\n")
    written, paths = mod.install_shims(verbose=True)
    assert written == 2  # only git and grep, ls skipped
    assert foreign.read_text() == "#!/bin/bash\necho NOT_OUR_SHIM\n"
    err = capsys.readouterr().err
    assert "refusing to overwrite" in err


def test_remove_shims_idempotent(isolated):
    mod = isolated["mod"]
    mod.install_shims(verbose=False)
    removed1, _ = mod.remove_shims(verbose=False)
    assert removed1 == 3
    assert not isolated["shim_dir"].exists()
    removed2, _ = mod.remove_shims(verbose=False)
    assert removed2 == 0


def test_inject_codex_path_idempotent(isolated):
    mod = isolated["mod"]
    isolated["zprofile"].write_text('model = "gpt-5"\n')
    assert mod.inject_codex_path(verbose=False) is True
    text1 = isolated["zprofile"].read_text()
    assert mod.MARKER_BEGIN in text1
    assert mod.MARKER_END in text1
    assert str(isolated["shim_dir"]) in text1
    # Re-run — should not duplicate.
    assert mod.inject_codex_path(verbose=False) is True
    text2 = isolated["zprofile"].read_text()
    assert text1 == text2


def test_inject_appends_after_existing_profile_content(isolated):
    """Existing user profile content is preserved; our block is appended."""
    mod = isolated["mod"]
    user_content = '# user content\nalias gs="git status"\n'
    isolated["zprofile"].write_text(user_content)
    assert mod.inject_codex_path(verbose=False) is True
    text = isolated["zprofile"].read_text()
    assert text.startswith(user_content)
    assert mod.MARKER_BEGIN in text
    assert str(isolated["shim_dir"]) in text


def test_remove_codex_path_idempotent(isolated):
    mod = isolated["mod"]
    isolated["zprofile"].write_text('model = "gpt-5"\n')
    mod.inject_codex_path(verbose=False)
    assert mod.remove_codex_path(verbose=False) is True
    text = isolated["zprofile"].read_text()
    assert mod.MARKER_BEGIN not in text
    assert text.strip() == 'model = "gpt-5"'
    # Re-run no-op.
    assert mod.remove_codex_path(verbose=False) is False


def test_enable_disable_round_trip(isolated):
    mod = isolated["mod"]
    isolated["zprofile"].write_text('# existing config\nmodel = "gpt-5"\n')
    assert mod.enable_codex_parity(verbose=False) == 0
    assert isolated["shim_dir"].exists()
    assert mod.MARKER_BEGIN in isolated["zprofile"].read_text()
    assert mod.disable_codex_parity(verbose=False) == 0
    assert not isolated["shim_dir"].exists()
    final = isolated["zprofile"].read_text()
    assert mod.MARKER_BEGIN not in final
    assert 'model = "gpt-5"' in final


def test_status_reports_state(isolated):
    mod = isolated["mod"]
    s = mod.status()
    assert s["shims_present"] is False
    assert s["codex_block_present"] is False

    mod.enable_codex_parity(verbose=False)
    s = mod.status()
    assert s["shims_present"] is True
    assert s["codex_block_present"] is True
    assert s["shim_files_count"] == 3
