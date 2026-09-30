"""Unit tests for the `openkos mcp` CLI verb (design Decision 14).

`mcp` is read-only (`_READ_ONLY_COMMANDS`) and never takes the workspace
lock: it validates `--workspace` with the same `config.require_workspace`
+ `config.read_config` gate every read command uses, prints the same
"refusing to serve" shape on refusal, and hands off to
`openkos.mcp.server.serve` -- imported LAZILY, inside the command's own
body, so `asyncio` never loads on any other verb's ordinary startup path
(covered structurally by `tests/unit/mcp/test_layering.py`; the tests here
pin the CLI-visible half: the module is absent from `sys.modules` after a
plain `import openkos.cli.main`).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

import openkos.cli.main as cli_main
from openkos.cli.main import app

runner = CliRunner()


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _patch_serve(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Patch `openkos.mcp.server.serve` to a recorder returning `0`,
    without ever running a real stdio session."""
    calls: list[dict[str, object]] = []

    def _fake_serve(root: Path, *, expose_confidential: bool) -> int:
        calls.append({"root": root, "expose_confidential": expose_confidential})
        return 0

    monkeypatch.setattr("openkos.mcp.server.serve", _fake_serve)
    return calls


def test_refuses_non_workspace_before_serving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-workspace `--workspace` directory exits 1, prints the refusal
    on stderr, and never writes a byte to stdout -- no protocol frame is
    ever emitted (spec: "An invalid workspace directory refuses to
    serve"). A workspace whose `openkos.yaml` fails `read_config` also
    exits 1 with nothing on stdout."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["mcp", "--workspace", str(tmp_path)])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert result.stdout == ""
    assert result.stderr.startswith("openkos mcp: refusing to serve -- ")
    assert "Traceback" not in result.stderr

    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "openkos.yaml").write_text("model: [unclosed\n", encoding="utf-8")

    bad_config_result = runner.invoke(app, ["mcp", "--workspace", str(tmp_path)])

    assert bad_config_result.exit_code == 1
    assert isinstance(bad_config_result.exception, SystemExit)
    assert bad_config_result.stdout == ""
    assert bad_config_result.stderr.startswith(
        "openkos mcp: failed while reading the workspace -- "
    )
    assert "Traceback" not in bad_config_result.stderr


def test_flags_reach_serve_and_command_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--workspace`/`--expose-confidential` reach `server.serve` resolved
    and typed correctly; the flag defaults to off; `mcp` is a read-only
    command in the `Explore` panel (spec: "A valid workspace directory
    serves normally", "The flag defaults to off", "mcp is classified as a
    read-only command")."""
    _init_workspace(tmp_path, monkeypatch)
    calls = _patch_serve(monkeypatch)

    result = runner.invoke(
        app, ["mcp", "--workspace", str(tmp_path), "--expose-confidential"]
    )

    assert result.exit_code == 0
    assert len(calls) == 1
    assert calls[0]["root"] == tmp_path.resolve()
    assert calls[0]["expose_confidential"] is True

    default_result = runner.invoke(app, ["mcp", "--workspace", str(tmp_path)])

    assert default_result.exit_code == 0
    assert len(calls) == 2
    assert calls[1]["expose_confidential"] is False

    assert "mcp" in cli_main._READ_ONLY_COMMANDS
    commands = {
        command.callback.__name__: command
        for command in app.registered_commands
        if command.callback is not None
    }
    assert commands["mcp_cmd"].rich_help_panel == "Explore"


def test_mcp_cmd_embed_site_uses_the_embed_client_delegator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`mcp`'s embedding-host advisory client is built through
    `cli_main._embed_client(cfg)` (issue #1057 Phase 10, task 10.11-10.12),
    not a direct `OllamaClient(model=cfg.embedding_model)` construction.
    **RED today**: `mcp_cmd` still constructs `OllamaClient` directly."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_serve(monkeypatch)
    calls: list[object] = []
    original_embed_client = cli_main._embed_client

    def _spy(cfg: object) -> object:
        calls.append(cfg)
        return original_embed_client(cfg)  # type: ignore[arg-type]

    monkeypatch.setattr(cli_main, "_embed_client", _spy)

    result = runner.invoke(app, ["mcp", "--workspace", str(tmp_path)])

    assert result.exit_code == 0, result.stdout
    assert len(calls) == 1


def test_mcp_module_not_imported_at_cli_startup() -> None:
    """`openkos.mcp` is absent from `sys.modules` after a fresh `import
    openkos.cli.main` -- the lazy-import half of "The CLI imports mcp
    lazily, only inside the verb" (import-timing, not import-shape;
    `test_layering.py` covers the AST half)."""
    # `S603`/`S607`: a fixed argv, no shell, no caller-supplied input --
    # matches `tests/unit/mcp/test_stdio_subprocess.py`'s pattern. A real
    # subprocess is required: this process may have already imported
    # `openkos.mcp` from an earlier test in the same session.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import openkos.cli.main; print('openkos.mcp' in sys.modules)",
        ],
        capture_output=True,
        timeout=15,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"


def test_nonlocal_embed_host_advisory_at_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a configured embedding host that is not local, launching
    `openkos mcp` logs one stderr advisory before serving, mirroring the
    CLI's `_warn_if_nonlocal_embed_host` (threat matrix: "Environment
    inheritance"). A local host logs no such advisory."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_serve(monkeypatch)
    monkeypatch.setenv("OLLAMA_HOST", "http://remote.example:11434")

    result = runner.invoke(app, ["mcp", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "embedding host 'remote.example:11434' is not this machine" in result.stderr

    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")

    local_result = runner.invoke(app, ["mcp", "--workspace", str(tmp_path)])

    assert local_result.exit_code == 0
    assert "embedding host" not in local_result.stderr
