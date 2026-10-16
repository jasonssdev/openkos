"""Unit tests for the `openkos serve` CLI verb (local-http-api, S5b).

`serve` hands off to `openkos.localapi.launch.serve`, imported LAZILY inside
the verb body so the optional `[api]` extra (fastapi, uvicorn, pydantic) never
loads on any other verb's start-up path. A missing extra is a usage failure
(exit 2) with the install remedy on stderr and nothing on stdout.
"""

from __future__ import annotations

import importlib.abc
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.main import get_group
from typer.testing import CliRunner

import openkos.cli.main as cli_main
from openkos.cli.main import app

runner = CliRunner()


def _serve_command() -> Any:
    return get_group(app).commands["serve"]


def test_help_exposes_workspace_and_no_token_option() -> None:
    """The token travels on stdin only: the option set is `--workspace`,
    nothing that could carry a secret on argv."""
    command = _serve_command()
    flags = {opt for param in command.params for opt in param.opts}
    assert flags == {"--workspace"}


class _FailingFinder(importlib.abc.MetaPathFinder):
    """Make `import openkos.localapi.launch` raise `ModuleNotFoundError(name)`.

    Works whether or not another test already imported the module: the cached
    module and the package attribute are removed (and restored by the
    monkeypatch), so `from openkos.localapi import launch` must import again.
    """

    def __init__(self, missing: str) -> None:
        self.missing = missing

    def find_spec(
        self,
        fullname: str,
        path: object = None,
        target: object = None,
    ) -> None:
        if fullname == "openkos.localapi.launch":
            raise ModuleNotFoundError(
                f"No module named {self.missing!r}", name=self.missing
            )


def _fail_launch_import(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    import openkos.localapi

    monkeypatch.delitem(sys.modules, "openkos.localapi.launch", raising=False)
    monkeypatch.delattr(openkos.localapi, "launch", raising=False)
    monkeypatch.setattr(sys, "meta_path", [_FailingFinder(missing), *sys.meta_path])


@pytest.mark.parametrize("missing", ["fastapi", "uvicorn", "pydantic"])
def test_missing_api_extra_exits_2_with_the_install_remedy(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    _fail_launch_import(monkeypatch, missing)

    result = runner.invoke(app, ["serve"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "openkos[api]" in result.stderr
    assert "pip install 'openkos[api]'" in result.stderr
    assert "Traceback" not in result.stderr


def test_another_import_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the three extra packages mean "extra missing"; any other missing
    module is a real defect and must not be dressed up as a remedy."""
    _fail_launch_import(monkeypatch, "anyio")

    result = runner.invoke(app, ["serve"])

    assert isinstance(result.exception, ModuleNotFoundError)
    assert result.exception.name == "anyio"
    assert result.exit_code != 2


def test_serve_is_self_locking_and_not_wrapped_by_the_lock_guard() -> None:
    assert "serve" in cli_main._SELF_LOCKING_COMMANDS
    commands = {
        info.name or (info.callback.__name__ if info.callback else ""): info
        for info in app.registered_commands
    }
    callback = commands["serve"].callback
    assert callback is not None
    assert getattr(callback, "__openkos_locked_command__", None) is None


def test_serve_sits_in_the_explore_panel() -> None:
    commands = {
        info.name: info for info in app.registered_commands if info.name is not None
    }
    assert commands["serve"].rich_help_panel == "Explore"


def test_verb_forwards_the_absolute_workspace_and_the_return_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []

    def _fake_serve(root: Path) -> int:
        calls.append(root)
        return 7

    from openkos.localapi import launch

    monkeypatch.setattr(launch, "serve", _fake_serve)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["serve", "--workspace", "sub"])

    assert result.exit_code == 7
    assert calls == [Path("sub").absolute()]
    assert calls[0].is_absolute()


_FORBIDDEN = ("fastapi", "uvicorn", "starlette", "pydantic", "openkos.localapi")


def _loaded_after(statement: str) -> str:
    script = (
        "import sys; "
        f"{statement}; "
        f"print([m for m in {_FORBIDDEN!r} if m in sys.modules])"
    )
    result = subprocess.run(  # noqa: S603 -- fixed argv, no shell
        [sys.executable, "-I", "-c", script],
        capture_output=True,
        timeout=30,
        text=True,
    )
    assert result.returncode in (0, 2), result.stderr
    return result.stdout.strip().splitlines()[-1]


def test_cli_startup_does_not_import_the_api_extra() -> None:
    assert _loaded_after("import openkos.cli.main") == "[]"


def test_help_and_version_do_not_import_the_api_extra() -> None:
    for argv in (["--help"], ["--version"]):
        stmt = f"from openkos.cli.main import app; app({argv!r}, standalone_mode=False)"
        assert _loaded_after(stmt) == "[]", argv
