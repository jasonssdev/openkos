"""Unit tests for the `doctor` CLI command: read-only environment health scan.

`doctor` runs ALL five checks (workspace-initialized, config-valid,
Ollama-reachable, model-installed, bundle-readable), renders every result
unconditionally (accumulate-then-exit-once, D5), and exits 1 iff any
CRITICAL check failed. Every test patches `openkos.cli.main.OllamaClient`
with a fake stub (D-seam) -- zero network, zero real Ollama process.
"""

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from openkos.cli.main import app
from openkos.config import DEFAULT_MODEL
from openkos.llm.ollama import OllamaError, OllamaUnavailable

runner = CliRunner()


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _fake_ollama_client(
    *,
    installed: list[str] | None = None,
    error: Exception | None = None,
    record: list[dict[str, Any]] | None = None,
) -> Callable[..., Any]:
    """Build a fake `OllamaClient` factory: returns `installed` from
    `list_models()`, or raises `error` if given. When `record` is provided,
    each constructor call appends its `{"model": ..., **kwargs}` to it, so a
    test can assert how `doctor` built the client (e.g. the preflight
    `timeout`); `doctor` otherwise only calls the constructor and
    `list_models()`."""

    class _FakeOllamaClient:
        def __init__(self, model: str, **kwargs: object) -> None:
            self.model = model
            if record is not None:
                record.append({"model": model, **kwargs})

        def list_models(self) -> list[str]:
            if error is not None:
                raise error
            return list(installed or [])

    return _FakeOllamaClient


def _snapshot_entry(path: Path) -> bytes | None:
    if path.is_dir():
        return None
    return path.read_bytes()


def _snapshot(root: Path) -> dict[Path, bytes | None]:
    return {path.relative_to(root): _snapshot_entry(path) for path in root.rglob("*")}


def test_doctor_all_healthy_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fully healthy workspace prints one `[PASS]` per applicable check
    and exits 0 (Scenario: Healthy workspace prints all applicable checks)."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[DEFAULT_MODEL]),
    )

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert result.stdout.count("[PASS]") == 5
    assert "[FAIL]" not in result.stdout
    assert "[SKIP]" not in result.stdout
    assert "[PASS] Workspace initialized" in result.stdout
    assert f"[PASS] Config valid — model {DEFAULT_MODEL}" in result.stdout
    assert f"[PASS] Model '{DEFAULT_MODEL}' installed" in result.stdout


def test_doctor_ollama_down_shows_start_server_remediation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ollama unreachable prints `[FAIL] Ollama reachable` with an
    `ollama serve` remediation line, skips the model check, and exits 1
    (Scenario: Ollama down shows a start-server remediation)."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(error=OllamaUnavailable("Ollama not reachable")),
    )

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "[FAIL] Ollama reachable" in result.stdout
    assert "  -> ollama serve" in result.stdout
    assert f"[SKIP] Model '{DEFAULT_MODEL}' installed" in result.stdout


def test_doctor_missing_model_shows_pull_remediation_with_exact_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A configured model tag absent from the installed list prints
    `[FAIL] Model '<tag>' installed` with a `ollama pull <tag>` remediation
    naming the EXACT configured tag, and exits 1 (Scenario: Non-matching tag
    fails with pull remediation)."""
    _init_workspace(tmp_path, monkeypatch)
    configured_model = "custom-model:1b"
    (tmp_path / "openkos.yaml").write_text(
        f"model: {configured_model}\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=["other:1b"]),
    )

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert f"[FAIL] Model '{configured_model}' installed" in result.stdout
    assert f"  -> ollama pull {configured_model}" in result.stdout


def test_doctor_malformed_config_fails_and_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed `openkos.yaml` (written after `init`) prints `[FAIL]
    Config valid` and exits 1."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "openkos.yaml").write_text("model: [unclosed\n", encoding="utf-8")
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[DEFAULT_MODEL]),
    )

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "[FAIL] Config valid" in result.stdout


def test_doctor_outside_workspace_unhealthy_ollama_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outside a workspace, with Ollama unreachable: the workspace check
    fails informationally, config and bundle are skipped, and the
    Ollama/model checks (against the default model) still run and still
    determine a non-zero exit (Scenario: Unhealthy pre-init environment
    exits one)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(error=OllamaUnavailable("Ollama not reachable")),
    )

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "[FAIL] Workspace initialized" in result.stdout
    assert "  -> openkos init" in result.stdout
    assert "[SKIP] Config valid" in result.stdout
    assert "[SKIP] Bundle readable" in result.stdout
    assert "[FAIL] Ollama reachable" in result.stdout
    assert f"[SKIP] Model '{DEFAULT_MODEL}' installed" in result.stdout


def test_doctor_outside_workspace_healthy_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outside a workspace, with Ollama reachable and the default model
    installed: only the workspace check fails (informational-only), so the
    process exits 0 (Scenario: Healthy pre-init environment exits zero /
    Informational-only failure still exits zero)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[DEFAULT_MODEL]),
    )

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "[FAIL] Workspace initialized" in result.stdout
    assert "[PASS] Ollama reachable" in result.stdout
    assert f"[PASS] Model '{DEFAULT_MODEL}' installed" in result.stdout


def test_doctor_later_check_still_prints_after_earlier_critical_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ollama down AND malformed config: both fail, AND the later
    bundle-readable check still renders its own `[PASS]` -- proving
    accumulate-then-exit, no short-circuit (Scenario: A failing check does
    not stop later checks from running)."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "openkos.yaml").write_text("model: [unclosed\n", encoding="utf-8")
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(error=OllamaUnavailable("Ollama not reachable")),
    )

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "[FAIL] Config valid" in result.stdout
    assert "[FAIL] Ollama reachable" in result.stdout
    assert "[PASS] Bundle readable" in result.stdout


def test_doctor_ollama_generic_error_fails_without_serve_remediation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-transport `OllamaError` (server responded, but with a non-200
    or malformed body) still fails the reachable check but carries no
    `ollama serve` remediation -- only the transport failure does."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(error=OllamaError("Ollama request failed (500): boom")),
    )

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "[FAIL] Ollama reachable" in result.stdout
    assert "ollama serve" not in result.stdout


def test_doctor_bundle_findings_fail_but_stay_informational(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bundle §9 conformance finding (e.g. a concept missing `type`) makes
    the bundle-readable check print `[FAIL]`, but since it is informational
    (not critical), the process still exits 0 when every critical check
    passes (D7 criticality split)."""
    _init_workspace(tmp_path, monkeypatch)
    concepts_dir = tmp_path / "bundle" / "concepts"
    concepts_dir.mkdir()
    (concepts_dir / "orphan.md").write_text(
        "---\ntitle: no type here\n---\nBody.\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[DEFAULT_MODEL]),
    )

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "[FAIL] Bundle readable" in result.stdout


def test_doctor_run_leaves_workspace_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run with a mixed pass/fail outcome creates, modifies, and deletes no
    file, and executes no fix command itself (Scenario: Doctor run leaves
    the workspace unchanged)."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "openkos.yaml").write_text("model: [unclosed\n", encoding="utf-8")
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(error=OllamaUnavailable("Ollama not reachable")),
    )
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert _snapshot(tmp_path) == before


def test_doctor_builds_reachability_client_with_short_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Ollama-reachable check constructs its `OllamaClient` with the short
    preflight `timeout=5.0` (not the 120s `DEFAULT_TIMEOUT`), so a
    hung/firewalled host fails fast instead of blocking the interactive
    diagnostic (S1)."""
    _init_workspace(tmp_path, monkeypatch)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[DEFAULT_MODEL], record=calls),
    )

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert len(calls) == 1
    assert calls[0]["timeout"] == 5.0


def test_doctor_model_installed_honors_latest_normalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare configured tag (`qwen3`) counts as installed when Ollama reports
    only the `:latest`-suffixed form (`qwen3:latest`): the `<name>:latest`
    normalization flows end-to-end through the doctor model-installed check,
    not just the `model_tag_matches` helper. Every critical check passes, so
    the command exits 0 (S2)."""
    _init_workspace(tmp_path, monkeypatch)
    configured_model = "qwen3"
    (tmp_path / "openkos.yaml").write_text(
        f"model: {configured_model}\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[f"{configured_model}:latest"]),
    )

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert f"[PASS] Model '{configured_model}' installed" in result.stdout
    assert "[FAIL]" not in result.stdout


def test_doctor_no_ollama_binary_on_path_never_claims_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When `shutil.which("ollama")` finds no binary, the remediation names
    the missing binary + install URL, and NEVER claims Ollama "is not
    installed" (an over-claim `which` alone cannot support -- scenario 3.1)."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(error=OllamaUnavailable("Ollama not reachable")),
    )
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "[FAIL] Ollama reachable" in result.stdout
    assert "no `ollama` binary found on PATH" in result.stdout
    assert "https://ollama.com" in result.stdout
    assert "is not installed" not in result.stdout


def test_doctor_ollama_binary_present_keeps_serve_remediation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When `shutil.which("ollama")` finds a binary, the remediation stays
    exactly `ollama serve` -- present-but-refused is a different situation
    than not-installed (scenario 3.2)."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(error=OllamaUnavailable("Ollama not reachable")),
    )
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/local/bin/ollama")

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "[FAIL] Ollama reachable" in result.stdout
    assert "  -> ollama serve" in result.stdout
    assert "https://ollama.com" not in result.stdout
