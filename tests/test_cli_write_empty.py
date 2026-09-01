from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from coworker.cli import cmd_write


def _run(body: str, target: Path) -> int:
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=body))], usage=None
    )
    args = Namespace(
        provider=None, model=None, profile="write", context=[], spec="test",
        target=str(target), max_tokens=None, task_id=None, no_log=True,
        allow_code=False, stdout=False, append=False,
    )
    with patch.multiple(
        "coworker.cli",
        load_providers=lambda: {},
        load_profile=lambda _: {"system_prompt": ""},
        resolve_provider_and_model=lambda *_: ("deepseek", {}, "model"),
        call_with_fallback=lambda *_, **__: (response, "deepseek", {}, "model", 0),
    ):
        return cmd_write(args)


@pytest.mark.parametrize("body", ["", " \t\n"])
@pytest.mark.parametrize("initial", [None, b"keep-this-byte-for-byte\n"])
def test_empty_write_response_fails_closed(body, initial, tmp_path):
    target = tmp_path / "generated.md"
    if initial is not None:
        target.write_bytes(initial)
    before = target.read_bytes() if target.exists() else None

    assert _run(body, target) == 3
    assert target.exists() is (initial is not None)
    if before is not None:
        assert target.read_bytes() == before


def test_normal_write_response_remains_successful(tmp_path):
    target = tmp_path / "generated.md"

    assert _run("generated document", target) == 0
    assert target.read_text() == "generated document"
