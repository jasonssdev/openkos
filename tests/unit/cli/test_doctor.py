"""Unit tests for the `doctor` CLI command: read-only environment health scan.

`doctor` runs ALL twelve checks (workspace-initialized, config-valid,
Ollama-reachable, model-installed, embedding-model-installed,
task-models-installed,
bundle-readable, workspace-vector-index-present, vector-extension-loadable,
git-available, git-filter-repo-available, backend-host-locality), renders
every result
unconditionally (accumulate-then-exit-once, D5), and exits 1 iff any CRITICAL
check failed. `embedding-model-installed`, `workspace-vector-index-present`,
`vector-extension-loadable`, the two git
checks, and `backend-host-locality` are all informational (non-critical):
the git checks exist for the (not-yet-wired, PR2) `purge` verb, so a failing
check must not flip the exit code, and the locality check (issue #240)
reports rather than judges -- it is `[PASS]` while Ollama is reachable and
`[SKIP]` when it is not (#389), never `[FAIL]`, and carries its finding in
the detail on both branches. Every test patches `openkos.cli.main.OllamaClient` with a fake
stub (D-seam) -- zero network, zero real Ollama process.
"""

import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from openkos.bundle import ledger as bundle_ledger
from openkos.cli.main import app
from openkos.config import DEFAULT_EMBEDDING_MODEL, DEFAULT_MODEL, WorkspaceLayout
from openkos.llm.ollama import (
    BackendHostLocality,
    InstalledModel,
    OllamaClient,
    OllamaError,
    OllamaUnavailable,
)
from openkos.model import okf
from tests.unit.cli.conftest import disable_local_exemption
from tests.unit.cli.conftest import snapshot_bytes as _snapshot

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
    """Build a fake `OllamaClient` factory: returns `installed` tags (wrapped
    as `InstalledModel` with `family=None`) from `list_models()`, or raises
    `error` if given. When `record` is provided, each constructor call
    appends its `{"model": ..., **kwargs}` to it, so a test can assert how
    `doctor` built the client (e.g. the preflight `timeout`); `doctor`
    otherwise only calls the constructor and `list_models()`."""

    class _FakeOllamaClient:
        def __init__(self, model: str, **kwargs: object) -> None:
            self.model = model
            if record is not None:
                record.append({"model": model, **kwargs})
            # A REAL client, built the same way, purely so `locality`
            # answers exactly as production would (issue #240). Only the
            # network methods are faked here; host resolution is not
            # something a stub should re-implement, and re-implementing it
            # would let the fake and the real predicate drift.
            self._real = OllamaClient(model=model, **kwargs)  # type: ignore[arg-type]

        @property
        def locality(self) -> BackendHostLocality:
            return self._real.locality

        def list_models(self) -> list[InstalledModel]:
            if error is not None:
                raise error
            return [InstalledModel(tag=tag, family=None) for tag in (installed or [])]

    return _FakeOllamaClient


def test_doctor_all_healthy_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fully healthy workspace prints one `[PASS]` per applicable check
    (twelve total: #240 added the informational backend-locality report,
    and #513 added the informational task-models check) and exits 0
    (Scenario: Healthy workspace prints all applicable checks).
    `.openkos/vectors.db` and `.openkos/fts.db` are pre-created so the #142
    and #553 workspace index presence checks also pass."""
    _init_workspace(tmp_path, monkeypatch)
    openkos_dir = tmp_path / ".openkos"
    openkos_dir.mkdir(parents=True, exist_ok=True)
    (openkos_dir / "vectors.db").write_bytes(b"")
    (openkos_dir / "fts.db").write_bytes(b"")
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(
            # `gemma2:27b` is the packaged `edge_typing` default (#513): a
            # workspace missing it is no longer "fully healthy".
            installed=[DEFAULT_MODEL, DEFAULT_EMBEDDING_MODEL, "gemma2:27b"]
        ),
    )
    monkeypatch.setattr("openkos.cli.main.probe_vec_loadable", lambda: True)
    monkeypatch.setattr("openkos.vcs.git.git_available", lambda: True)
    monkeypatch.setattr("openkos.vcs.git.filter_repo_available", lambda: True)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert result.stdout.count("[PASS]") == 15
    assert "[FAIL]" not in result.stdout
    assert "[SKIP]" not in result.stdout
    assert "[PASS] Workspace initialized" in result.stdout
    assert f"[PASS] Config valid — model {DEFAULT_MODEL}" in result.stdout
    assert f"[PASS] Model '{DEFAULT_MODEL}' installed" in result.stdout
    assert f"[PASS] Embedding model '{DEFAULT_EMBEDDING_MODEL}' installed" in (
        result.stdout
    )
    assert "[PASS] Workspace vector index present" in result.stdout
    assert "[PASS] Vector extension loadable" in result.stdout
    assert "[PASS] git available" in result.stdout
    assert "[PASS] git-filter-repo available" in result.stdout


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
    # Pin `shutil.which` so the remediation is deterministic regardless of
    # whether the test host has the `ollama` binary on PATH (CI does not):
    # this scenario is "installed but off" -> `ollama serve`. The not-on-PATH
    # variant is covered by test_doctor_no_ollama_binary_on_path_*.
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/local/bin/ollama")

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


def test_doctor_non_str_model_fails_and_exits_one_without_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`model: yes` (a YAML 1.1 bool, not a string) prints `[FAIL] Config
    valid`, exits 1, and renders no traceback -- `read_config`'s str-type
    guard (issue #128, defect #1) raises `ValueError` inside `doctor`'s
    existing `except (OSError, ValueError)` handling, so `cfg` stays `None`
    and later checks still fall back to `config.DEFAULT_MODEL` (design:
    "RESOLVED FORK: #1 subsumes #3 at the source"). Regression test only --
    NO production change to `main.py`/`ollama.py` (defect #3 is fully
    subsumed by the `read_config` guard alone)."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "openkos.yaml").write_text("model: yes\n", encoding="utf-8")
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[DEFAULT_MODEL, DEFAULT_EMBEDDING_MODEL]),
    )
    # Stubbed so the fourth check the spec scenario names is asserted against a
    # controlled value rather than whatever the host's SQLite build happens to
    # support -- otherwise this assertion would be environment-dependent.
    monkeypatch.setattr("openkos.cli.main.probe_vec_loadable", lambda: True)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "[FAIL] Config valid" in result.stdout
    assert "  -> fix openkos.yaml" in result.stdout
    assert isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.stdout
    # The spec scenario "Other applicable checks still run despite the malformed
    # model" names four checks: Ollama-reachable, embedding-model installed,
    # bundle readable, and vector-extension loadable. All four are asserted below
    # -- plus model-installed, which the scenario does not name but which the
    # `read_config` fallback to DEFAULT_MODEL makes worth pinning here too.
    assert "[PASS] Bundle readable" in result.stdout
    assert "[PASS] Ollama reachable" in result.stdout
    assert f"[PASS] Model '{DEFAULT_MODEL}' installed" in result.stdout
    assert f"[PASS] Embedding model '{DEFAULT_EMBEDDING_MODEL}' installed" in (
        result.stdout
    )
    assert "[PASS] Vector extension loadable" in result.stdout


def test_doctor_non_str_embedding_model_with_valid_model_exits_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`embedding_model: yes` (a YAML 1.1 bool) with an otherwise valid
    `model` no longer crashes with a `TypeError` -- this was the residual
    crash path (design: `":" in True` at the embedding-model-installed
    check) that `read_config`'s str-type guard on `embedding_model` alone
    closes, with zero doctor-side change."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "openkos.yaml").write_text(
        f"model: {DEFAULT_MODEL}\nembedding_model: yes\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[DEFAULT_MODEL]),
    )

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "[FAIL] Config valid" in result.stdout
    assert isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.stdout


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
    the command exits 0 (S2). `.openkos/vectors.db` and `.openkos/fts.db`
    are pre-created so the #142/#553 workspace index presence checks do not
    add an unrelated `[FAIL]` to this test's "no failures" assertion."""
    _init_workspace(tmp_path, monkeypatch)
    openkos_dir = tmp_path / ".openkos"
    openkos_dir.mkdir(parents=True, exist_ok=True)
    (openkos_dir / "vectors.db").write_bytes(b"")
    (openkos_dir / "fts.db").write_bytes(b"")
    configured_model = "qwen3"
    (tmp_path / "openkos.yaml").write_text(
        f"model: {configured_model}\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(
            installed=[
                f"{configured_model}:latest",
                DEFAULT_EMBEDDING_MODEL,
                # #513 packages a per-task default for `edge_typing`, so a
                # workspace is only fully healthy when that model is present
                # too. Listed here so this test keeps pinning `:latest`
                # normalization rather than the packaged default's absence.
                "gemma2:27b",
            ]
        ),
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


# --- embedding-model-installed check (non-fatal) -------------------------------


def test_doctor_embedding_model_installed_shows_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A configured embedding model tag present in the installed list prints
    `[PASS] Embedding model '<tag>' installed` and does not affect the exit
    code (Scenario: embedding-model-installed check passes)."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[DEFAULT_MODEL, DEFAULT_EMBEDDING_MODEL]),
    )

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert (
        f"[PASS] Embedding model '{DEFAULT_EMBEDDING_MODEL}' installed" in result.stdout
    )


def test_doctor_embedding_model_missing_shows_pull_remediation_but_exit_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A configured embedding model tag absent from the installed list prints
    `[FAIL] Embedding model '<tag>' installed` with an `ollama pull <tag>`
    remediation, but the check is informational so the process still exits 0
    (Scenario: embedding-model-installed check fails, stays non-fatal)."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[DEFAULT_MODEL]),
    )

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert (
        f"[FAIL] Embedding model '{DEFAULT_EMBEDDING_MODEL}' installed" in result.stdout
    )
    assert f"  -> ollama pull {DEFAULT_EMBEDDING_MODEL}" in result.stdout


def test_doctor_embedding_model_check_skips_when_ollama_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When Ollama is unreachable, the embedding-model-installed check prints
    `[SKIP]` (not `[FAIL]`), reusing the same `reachable` flag as the
    chat-model check -- one root cause, not double-reported (Scenario:
    embedding-model-installed check skips when Ollama is unreachable)."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(error=OllamaUnavailable("Ollama not reachable")),
    )

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert (
        f"[SKIP] Embedding model '{DEFAULT_EMBEDDING_MODEL}' installed" in result.stdout
    )
    assert (
        f"[FAIL] Embedding model '{DEFAULT_EMBEDDING_MODEL}' installed"
        not in result.stdout
    )


def test_doctor_embedding_model_check_runs_outside_workspace_against_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outside an initialized workspace, the embedding-model-installed check
    still runs, against `config.DEFAULT_EMBEDDING_MODEL`, and stays
    informational (Scenario: embedding-model-installed check runs outside a
    workspace)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[DEFAULT_MODEL, DEFAULT_EMBEDDING_MODEL]),
    )

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert (
        f"[PASS] Embedding model '{DEFAULT_EMBEDDING_MODEL}' installed" in result.stdout
    )


def test_doctor_embedding_model_check_does_not_construct_extra_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The embedding-model-installed check reuses the `installed` list and
    `reachable` flag already fetched by the Ollama-reachable check -- it does
    NOT construct a second `OllamaClient` (D: reuse, not a new preflight)."""
    _init_workspace(tmp_path, monkeypatch)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(
            installed=[DEFAULT_MODEL, DEFAULT_EMBEDDING_MODEL], record=calls
        ),
    )

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert len(calls) == 1


# --- vector-extension-loadable check (non-fatal, no SKIP branch) -----------


def test_doctor_vector_extension_loadable_shows_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A loadable `sqlite-vec` extension prints `[PASS] Vector extension
    loadable` and does not affect the exit code."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[DEFAULT_MODEL, DEFAULT_EMBEDDING_MODEL]),
    )
    monkeypatch.setattr("openkos.cli.main.probe_vec_loadable", lambda: True)
    monkeypatch.setattr("openkos.vcs.git.git_available", lambda: True)
    monkeypatch.setattr("openkos.vcs.git.filter_repo_available", lambda: True)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "[PASS] Vector extension loadable" in result.stdout
    assert result.stdout.count("[PASS]") == 12


def test_doctor_vector_extension_not_loadable_fails_but_exit_stays_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-loadable `sqlite-vec` extension prints `[FAIL] Vector extension
    loadable` with a remediation naming an extension-capable interpreter
    (not system/Homebrew Python), but stays informational: the process still
    exits 0 when every critical check passes."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[DEFAULT_MODEL, DEFAULT_EMBEDDING_MODEL]),
    )
    monkeypatch.setattr("openkos.cli.main.probe_vec_loadable", lambda: False)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "[FAIL] Vector extension loadable" in result.stdout
    assert "uv" in result.stdout
    assert "system Python" not in result.stdout
    assert "Homebrew" not in result.stdout


def test_doctor_vector_extension_check_runs_even_when_ollama_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unlike the embedding-model check, the vector-extension check has no
    SKIP branch: it runs (and can `[PASS]`) even when Ollama is unreachable,
    since it shares no root cause with the Ollama checks."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(error=OllamaUnavailable("Ollama not reachable")),
    )
    monkeypatch.setattr("openkos.cli.main.probe_vec_loadable", lambda: True)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "[PASS] Vector extension loadable" in result.stdout
    assert "[SKIP] Vector extension loadable" not in result.stdout


def test_doctor_vector_extension_check_runs_outside_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outside an initialized workspace, the vector-extension check still
    runs -- it depends on neither workspace state nor Ollama."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[DEFAULT_MODEL, DEFAULT_EMBEDDING_MODEL]),
    )
    monkeypatch.setattr("openkos.cli.main.probe_vec_loadable", lambda: True)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "[PASS] Vector extension loadable" in result.stdout


# --- workspace-vector-index-present check (purge-transactional-cleanup #142)


def test_doctor_workspace_vectors_present_shows_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A present `.openkos/vectors.db` prints `[PASS] Workspace vector index
    present` (doctor-command spec: "Present workspace vectors.db passes")."""
    _init_workspace(tmp_path, monkeypatch)
    openkos_dir = tmp_path / ".openkos"
    openkos_dir.mkdir(parents=True, exist_ok=True)
    (openkos_dir / "vectors.db").write_bytes(b"")

    result = runner.invoke(app, ["doctor"])

    assert "[PASS] Workspace vector index present" in result.stdout


def test_doctor_workspace_vectors_absent_shows_fail_with_reindex_remediation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An absent `.openkos/vectors.db` prints `[FAIL] Workspace vector index
    present` with an indented `openkos reindex` remediation line, and stays
    informational -- exit 0 when every critical check otherwise passes
    (doctor-command spec: "Absent workspace vectors.db fails with a reindex
    remediation")."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[DEFAULT_MODEL, DEFAULT_EMBEDDING_MODEL]),
    )
    monkeypatch.setattr("openkos.cli.main.probe_vec_loadable", lambda: True)
    monkeypatch.setattr("openkos.vcs.git.git_available", lambda: True)
    monkeypatch.setattr("openkos.vcs.git.filter_repo_available", lambda: True)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "[FAIL] Workspace vector index present" in result.stdout
    assert "openkos reindex" in result.stdout


def test_doctor_workspace_vectors_check_skipped_outside_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outside an initialized workspace, the check prints `[SKIP]` and does
    not affect the exit code (doctor-command spec: "Check is skipped
    outside a workspace")."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[DEFAULT_MODEL, DEFAULT_EMBEDDING_MODEL]),
    )
    monkeypatch.setattr("openkos.cli.main.probe_vec_loadable", lambda: True)

    result = runner.invoke(app, ["doctor"])

    assert "[SKIP] Workspace vector index present" in result.stdout


def test_doctor_workspace_vectors_check_distinct_from_extension_loadable_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two vector checks are independent: an absent workspace
    `vectors.db` (`[FAIL]`) coexists with a loadable extension (`[PASS]`),
    proving neither check's outcome depends on the other."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[DEFAULT_MODEL, DEFAULT_EMBEDDING_MODEL]),
    )
    monkeypatch.setattr("openkos.cli.main.probe_vec_loadable", lambda: True)

    result = runner.invoke(app, ["doctor"])

    assert "[FAIL] Workspace vector index present" in result.stdout
    assert "[PASS] Vector extension loadable" in result.stdout


# --- git-available / git-filter-repo-available checks (non-fatal) ----------
# (privacy-purge Slice 1, PR1: probes for the not-yet-wired `purge` verb)


def test_doctor_git_and_filter_repo_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both `git` and `git-filter-repo` available prints `[PASS] git
    available` + `[PASS] git-filter-repo available`, and does not affect the
    exit code (ADDED requirement scenario: both available)."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[DEFAULT_MODEL, DEFAULT_EMBEDDING_MODEL]),
    )
    monkeypatch.setattr("openkos.cli.main.probe_vec_loadable", lambda: True)
    monkeypatch.setattr("openkos.vcs.git.git_available", lambda: True)
    monkeypatch.setattr("openkos.vcs.git.filter_repo_available", lambda: True)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "[PASS] git available" in result.stdout
    assert "[PASS] git-filter-repo available" in result.stdout


def test_doctor_git_filter_repo_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`git-filter-repo` absent prints `[FAIL] git-filter-repo available`
    with an install remediation, but stays informational (no effect on exit
    code) -- ADDED requirement scenario: git-filter-repo missing."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[DEFAULT_MODEL, DEFAULT_EMBEDDING_MODEL]),
    )
    monkeypatch.setattr("openkos.cli.main.probe_vec_loadable", lambda: True)
    monkeypatch.setattr("openkos.vcs.git.git_available", lambda: True)
    monkeypatch.setattr("openkos.vcs.git.filter_repo_available", lambda: False)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "[FAIL] git-filter-repo available" in result.stdout
    fail_line_and_after = result.stdout.split("[FAIL] git-filter-repo available", 1)[1]
    assert "  -> " in fail_line_and_after
    assert "git-filter-repo" in fail_line_and_after


def test_doctor_git_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`git` itself absent prints `[FAIL] git available` with an install
    remediation, but stays informational -- ADDED requirement scenario: git
    itself missing."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[DEFAULT_MODEL, DEFAULT_EMBEDDING_MODEL]),
    )
    monkeypatch.setattr("openkos.cli.main.probe_vec_loadable", lambda: True)
    monkeypatch.setattr("openkos.vcs.git.git_available", lambda: False)
    monkeypatch.setattr("openkos.vcs.git.filter_repo_available", lambda: False)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "[FAIL] git available" in result.stdout
    fail_line_and_after = result.stdout.split("[FAIL] git available", 1)[1]
    assert "  -> " in fail_line_and_after


def test_doctor_git_checks_run_pre_init_independent_of_ollama(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The git checks run BEFORE `openkos init` and regardless of Ollama
    reachability -- they share no root cause with the workspace or Ollama
    checks (ADDED requirement scenario: runs pre-init and independent of
    Ollama unreachability)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(error=OllamaUnavailable("Ollama not reachable")),
    )
    monkeypatch.setattr("openkos.vcs.git.git_available", lambda: True)
    monkeypatch.setattr("openkos.vcs.git.filter_repo_available", lambda: True)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1  # Ollama unreachable still fails critically
    assert "[FAIL] Workspace initialized" in result.stdout
    assert "[PASS] git available" in result.stdout
    assert "[PASS] git-filter-repo available" in result.stdout


def test_doctor_prints_version_banner_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`doctor` prints the `openkos {version}` banner as its first stdout
    line, before any check line, without changing the check count or exit
    code (ADDED requirement: Doctor Prints A Leading Version Banner)."""
    _init_workspace(tmp_path, monkeypatch)
    openkos_dir = tmp_path / ".openkos"
    openkos_dir.mkdir(parents=True, exist_ok=True)
    (openkos_dir / "vectors.db").write_bytes(b"")
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(
            installed=[DEFAULT_MODEL, DEFAULT_EMBEDDING_MODEL, "gemma2:27b"]
        ),
    )
    monkeypatch.setattr("openkos.cli.main.probe_vec_loadable", lambda: True)
    monkeypatch.setattr("openkos.vcs.git.git_available", lambda: True)
    monkeypatch.setattr("openkos.vcs.git.filter_repo_available", lambda: True)

    result = runner.invoke(app, ["doctor"])

    lines = result.stdout.splitlines()
    assert re.match(r"^openkos \d+\.\d+\.\d+", lines[0])
    assert lines[1] == f"openkos doctor: checking environment at {tmp_path}"
    assert result.exit_code == 0
    assert result.stdout.count("[PASS]") == 14


# --- issue #240: the informational backend-locality check --------------------


def _healthy_doctor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Everything green except whatever the calling test varies."""
    _init_workspace(tmp_path, monkeypatch)
    openkos_dir = tmp_path / ".openkos"
    openkos_dir.mkdir(parents=True, exist_ok=True)
    (openkos_dir / "vectors.db").write_bytes(b"")
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[DEFAULT_MODEL, DEFAULT_EMBEDDING_MODEL]),
    )
    monkeypatch.setattr("openkos.cli.main.probe_vec_loadable", lambda: True)
    monkeypatch.setattr("openkos.vcs.git.git_available", lambda: True)
    monkeypatch.setattr("openkos.vcs.git.filter_repo_available", lambda: True)


def test_doctor_reports_a_local_backend_and_an_active_exemption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a stock workspace with the default local backend, `doctor` reports
    the backend as this machine and the confidential local exemption as
    active (#240).

    The whole point of the check is that the state becomes INSPECTABLE
    rather than inferred: without it, a user cannot tell whether their
    confidential concepts are reaching the model except by reading the
    source."""
    _healthy_doctor(tmp_path, monkeypatch)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "[PASS] Backend host locality — this machine (localhost:11434); " in (
        result.stdout
    )
    assert "confidential local exemption active" in result.stdout


def test_doctor_reports_a_remote_backend_without_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-local backend is REPORTED, never failed (#240).

    Informational means informational: running against a remote Ollama is a
    legitimate configuration, so `[FAIL]` would be a lie and a non-zero exit
    would break every script that runs `doctor` as a gate. The exemption is
    reported inactive, which is the fact that matters."""
    _healthy_doctor(tmp_path, monkeypatch)
    monkeypatch.setenv("OLLAMA_HOST", "http://user:s3cret@remote.example:11434")

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "[PASS] Backend host locality — not this machine " in result.stdout
    assert "(remote.example:11434)" in result.stdout
    assert "confidential local exemption inactive" in result.stdout
    assert "s3cret" not in result.stdout
    assert "s3cret" not in result.stderr


def test_doctor_reports_the_workspace_opt_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A local backend with `confidential_local_exemption: false` reports the
    backend as this machine AND the exemption as inactive (#240) -- the two
    terms are distinct facts and the check must not conflate them."""
    _healthy_doctor(tmp_path, monkeypatch)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    disable_local_exemption(tmp_path)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "this machine (localhost:11434)" in result.stdout
    assert "confidential local exemption inactive" in result.stdout


def test_doctor_locality_check_never_changes_the_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The locality check is informational on EVERY path, including an
    unparseable host (#240): it never raises, never fails, and never flips
    the exit code a critical check owns."""
    _healthy_doctor(tmp_path, monkeypatch)
    monkeypatch.setenv("OLLAMA_HOST", "user:s3cret@[::1")

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "[PASS] Backend host locality — not this machine ([::1)" in result.stdout
    assert "s3cret" not in result.stdout


def test_doctor_reports_locality_outside_a_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outside a workspace the check still runs, falling back to the packaged
    `confidential_local_exemption` default exactly as checks 3-5 fall back to
    the packaged model tags (#240). Workspace state is what this test varies,
    and the check does not depend on it -- the separate skip when Ollama is
    unreachable (#389) is about how the line READS beside a failure, not
    about an inability to answer."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[DEFAULT_MODEL, DEFAULT_EMBEDDING_MODEL]),
    )
    monkeypatch.setattr("openkos.cli.main.probe_vec_loadable", lambda: True)
    monkeypatch.setattr("openkos.vcs.git.git_available", lambda: True)
    monkeypatch.setattr("openkos.vcs.git.filter_repo_available", lambda: True)

    result = runner.invoke(app, ["doctor"])

    assert "[PASS] Backend host locality — this machine (localhost:11434)" in (
        result.stdout
    )
    assert "[SKIP] Backend host locality" not in result.stdout


def test_doctor_locality_does_not_read_as_passing_while_ollama_is_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With Ollama unreachable, the backend-host-locality line is `[SKIP]`,
    not `[PASS]` (#389).

    It reports CONFIGURATION, not liveness, which is correct -- but a green
    `[PASS] Backend host locality` printed directly beneath
    `[FAIL] Ollama reachable` reads as a contradiction to anyone scanning
    the column. The configured host is still named in the detail, so the
    fact survives; only the claim that it was verified goes away, matching
    the same skip-when-unreachable rule the model and embedding checks
    already follow."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(error=OllamaUnavailable("Ollama not reachable")),
    )
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/local/bin/ollama")

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "[FAIL] Ollama reachable" in result.stdout
    assert "[PASS] Backend host locality" not in result.stdout
    locality_line = next(
        line for line in result.stdout.splitlines() if "Backend host locality" in line
    )
    assert locality_line.startswith("[SKIP]")
    # The configured host is still reported; only the verification claim goes.
    assert "configured" in locality_line


def test_doctor_locality_still_passes_when_ollama_is_reachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The skip is scoped to the unreachable case: a healthy run still
    reports locality as a `[PASS]` with its host and exemption state
    (#389)."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr("openkos.cli.main.OllamaClient", _fake_ollama_client())

    result = runner.invoke(app, ["doctor"])

    locality_line = next(
        line for line in result.stdout.splitlines() if "Backend host locality" in line
    )
    assert locality_line.startswith("[PASS]")
    assert "exemption" in locality_line


# --- #513: doctor sees the per-task models, not just the global one ---------


def test_doctor_reports_a_missing_task_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A packaged per-task default that is not installed is REPORTED.

    Packaging `edge_typing: gemma2:27b` (#513) means every workspace now
    points a task at a 15.6 GB model nobody has by default. Before this
    check, `doctor` looked only at `cfg.model` and reported a clean bill of
    health, and the operator discovered the gap when `curate`'s Structure
    stage failed part-way through a session. That is the failure this
    check exists to move earlier.
    """
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[DEFAULT_MODEL, DEFAULT_EMBEDDING_MODEL]),
    )
    monkeypatch.setattr("openkos.cli.main.probe_vec_loadable", lambda: True)
    monkeypatch.setattr("openkos.vcs.git.git_available", lambda: True)
    monkeypatch.setattr("openkos.vcs.git.filter_repo_available", lambda: True)

    result = runner.invoke(app, ["doctor"])

    assert "[FAIL] Task models installed" in result.stdout
    assert "edge_typing" in result.stdout
    assert "ollama pull gemma2:27b" in result.stdout


def test_a_missing_task_model_does_not_change_the_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The task-model check is INFORMATIONAL, never critical.

    A missing per-task model fails only the stage that named it (#515
    decision 2) — every other verb still works. Making this critical would
    exit 1 on a workspace that is fine for `ingest`, `query`, and
    `adjudicate`, which would be a false alarm rather than a diagnosis."""
    _init_workspace(tmp_path, monkeypatch)
    openkos_dir = tmp_path / ".openkos"
    openkos_dir.mkdir(parents=True, exist_ok=True)
    (openkos_dir / "vectors.db").write_bytes(b"")
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[DEFAULT_MODEL, DEFAULT_EMBEDDING_MODEL]),
    )
    monkeypatch.setattr("openkos.cli.main.probe_vec_loadable", lambda: True)
    monkeypatch.setattr("openkos.vcs.git.git_available", lambda: True)
    monkeypatch.setattr("openkos.vcs.git.filter_repo_available", lambda: True)

    result = runner.invoke(app, ["doctor"])

    assert "[FAIL] Task models installed" in result.stdout
    assert result.exit_code == 0


def test_doctor_passes_when_every_task_model_is_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the packaged model present the check passes and names it."""
    _init_workspace(tmp_path, monkeypatch)
    openkos_dir = tmp_path / ".openkos"
    openkos_dir.mkdir(parents=True, exist_ok=True)
    (openkos_dir / "vectors.db").write_bytes(b"")
    (openkos_dir / "fts.db").write_bytes(b"")
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(
            installed=[DEFAULT_MODEL, DEFAULT_EMBEDDING_MODEL, "gemma2:27b"]
        ),
    )
    monkeypatch.setattr("openkos.cli.main.probe_vec_loadable", lambda: True)
    monkeypatch.setattr("openkos.vcs.git.git_available", lambda: True)
    monkeypatch.setattr("openkos.vcs.git.filter_repo_available", lambda: True)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert result.stdout.count("[PASS]") == 15
    assert "[PASS] Task models installed" in result.stdout
    assert "[FAIL]" not in result.stdout


def test_opting_out_of_the_packaged_default_makes_the_check_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`edge_typing: null` declines the packaged default, so there is no
    per-task model left to be missing and the check passes without the
    15.6 GB pull. This is the escape hatch working end to end."""
    _init_workspace(tmp_path, monkeypatch)
    openkos_dir = tmp_path / ".openkos"
    openkos_dir.mkdir(parents=True, exist_ok=True)
    (openkos_dir / "vectors.db").write_bytes(b"")
    (openkos_dir / "fts.db").write_bytes(b"")
    cfg_path = tmp_path / "openkos.yaml"
    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8") + "\nmodels:\n  edge_typing: null\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[DEFAULT_MODEL, DEFAULT_EMBEDDING_MODEL]),
    )
    monkeypatch.setattr("openkos.cli.main.probe_vec_loadable", lambda: True)
    monkeypatch.setattr("openkos.vcs.git.git_available", lambda: True)
    monkeypatch.setattr("openkos.vcs.git.filter_repo_available", lambda: True)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "[PASS] Task models installed" in result.stdout
    assert "[FAIL]" not in result.stdout


def test_task_model_check_skips_when_ollama_is_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`[SKIP]`, never `[FAIL]`, when Ollama is down — the same D6 one-root-
    cause discipline checks 4 and 5 already follow. Reporting a model as
    "not installed" when nothing could be listed would be a guess."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(error=OllamaUnavailable("connection refused")),
    )
    monkeypatch.setattr("openkos.cli.main.probe_vec_loadable", lambda: True)
    monkeypatch.setattr("openkos.vcs.git.git_available", lambda: True)
    monkeypatch.setattr("openkos.vcs.git.filter_repo_available", lambda: True)

    result = runner.invoke(app, ["doctor"])

    assert "[SKIP] Task models installed" in result.stdout


# --- merge-ledger integrity checks (durable-derived-state slice 1b) -------


def _make_ledger_entry(
    absorbed_id: str = "concepts/absorbed",
    *,
    survivor_before: str = "survivor text",
) -> okf.MergeLedgerEntry:
    return okf.MergeLedgerEntry(
        schema=okf.MERGE_LEDGER_SCHEMA_V3,
        merged_at="2026-07-20T00:00:00Z",
        absorbed_id=absorbed_id,
        absorbed_snapshot="absorbed text",
        survivor_before=survivor_before,
        index_before="index text",
        log_before="log text",
        link_rewrites=[],
        sensitivity_before="private",
        sensitivity_after="private",
    )


def _fake_client_and_git(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[DEFAULT_MODEL, DEFAULT_EMBEDDING_MODEL]),
    )
    monkeypatch.setattr("openkos.cli.main.probe_vec_loadable", lambda: True)
    monkeypatch.setattr("openkos.vcs.git.git_available", lambda: True)
    monkeypatch.setattr("openkos.vcs.git.filter_repo_available", lambda: True)


def test_doctor_ledger_checks_skip_outside_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[DEFAULT_MODEL, DEFAULT_EMBEDDING_MODEL]),
    )

    result = runner.invoke(app, ["doctor"])

    assert "[SKIP] Merge ledger torn writes" in result.stdout
    assert "[SKIP] Merge ledger entries free of post-merge mutation" in result.stdout


def test_doctor_ledger_checks_pass_trivially_when_no_ledgers_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario: "A workspace with no ledger sidecars passes trivially"."""
    _init_workspace(tmp_path, monkeypatch)
    _fake_client_and_git(monkeypatch)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "[PASS] Merge ledger torn writes" in result.stdout
    assert "[PASS] Merge ledger entries free of post-merge mutation" in result.stdout


def test_doctor_torn_write_check_fails_with_repair_remediation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _fake_client_and_git(monkeypatch)
    bundle_dir = WorkspaceLayout(tmp_path).bundle_dir
    survivor_path = bundle_dir / "concepts" / "survivor.md"
    survivor_path.parent.mkdir(parents=True, exist_ok=True)
    survivor_text = "---\ntype: Concept\ntitle: Survivor\n---\nBody.\n"
    survivor_path.write_text(survivor_text, encoding="utf-8")
    bundle_ledger.write_pending(
        "concepts/survivor",
        bundle_dir,
        survivor_id="concepts/survivor",
        entries=[_make_ledger_entry()],
        expected_survivor_sha256=bundle_ledger.survivor_sha256(survivor_text),
    )

    result = runner.invoke(app, ["doctor"])

    # Informational: never affects the exit code (critical=False).
    assert result.exit_code == 0
    assert "[FAIL] Merge ledger torn writes" in result.stdout
    fail_and_after = result.stdout.split("[FAIL] Merge ledger torn writes", 1)[1]
    assert "  -> " in fail_and_after
    assert "openkos repair" in fail_and_after


def test_doctor_nesting_violation_check_names_both_remedies_when_reset_point_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario: "A corrupted ledger fails with both remediation paths"."""
    _init_workspace(tmp_path, monkeypatch)
    _fake_client_and_git(monkeypatch)
    bundle_dir = WorkspaceLayout(tmp_path).bundle_dir
    entry_0 = _make_ledger_entry(absorbed_id="concepts/absorbed-0")
    tampered = okf.MergeLedgerEntry(
        schema=entry_0.schema,
        merged_at=entry_0.merged_at,
        absorbed_id=entry_0.absorbed_id,
        absorbed_snapshot="TAMPERED",
        survivor_before=entry_0.survivor_before,
        index_before=entry_0.index_before,
        log_before=entry_0.log_before,
        link_rewrites=entry_0.link_rewrites,
        sensitivity_before=entry_0.sensitivity_before,
        sensitivity_after=entry_0.sensitivity_after,
    )
    embedded_metadata: dict[str, object] = {
        "type": "Concept",
        "title": "Survivor",
        "merged_from": okf.encode_merged_from([tampered]),
    }
    entry_1 = _make_ledger_entry(
        absorbed_id="concepts/absorbed-1",
        survivor_before=okf.dump_frontmatter(embedded_metadata),
    )
    bundle_ledger.write_entries(
        "concepts/survivor",
        bundle_dir,
        survivor_id="concepts/survivor",
        entries=[entry_0, entry_1],
    )
    monkeypatch.setattr("openkos.cli.main.vcs_git.has_reset_point", lambda root: True)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    label = "[FAIL] Merge ledger entries free of post-merge mutation"
    assert label in result.stdout
    fail_and_after = result.stdout.split(label, 1)[1]
    assert "  -> " in fail_and_after
    assert "openkos repair" in fail_and_after
    assert "git reset --hard" in fail_and_after
    assert "openkos reindex" in fail_and_after
    assert "reversibility" in fail_and_after.lower()
    assert "not guaranteed" in fail_and_after.lower()


def test_doctor_nesting_violation_check_reports_no_reset_point_without_git_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The orchestrator-flagged gap: `_autocommit` is best-effort and
    silently no-ops with no configured git identity, so a workspace whose
    ledger is corrupted AND that never had a git identity configured has
    NO reset point at all -- `doctor` must say so explicitly rather than
    print an unusable `git reset --hard` remedy."""
    _init_workspace(tmp_path, monkeypatch)
    _fake_client_and_git(monkeypatch)
    bundle_dir = WorkspaceLayout(tmp_path).bundle_dir
    entry_0 = _make_ledger_entry(absorbed_id="concepts/absorbed-0")
    tampered = okf.MergeLedgerEntry(
        schema=entry_0.schema,
        merged_at=entry_0.merged_at,
        absorbed_id=entry_0.absorbed_id,
        absorbed_snapshot="TAMPERED",
        survivor_before=entry_0.survivor_before,
        index_before=entry_0.index_before,
        log_before=entry_0.log_before,
        link_rewrites=entry_0.link_rewrites,
        sensitivity_before=entry_0.sensitivity_before,
        sensitivity_after=entry_0.sensitivity_after,
    )
    embedded_metadata: dict[str, object] = {
        "type": "Concept",
        "title": "Survivor",
        "merged_from": okf.encode_merged_from([tampered]),
    }
    entry_1 = _make_ledger_entry(
        absorbed_id="concepts/absorbed-1",
        survivor_before=okf.dump_frontmatter(embedded_metadata),
    )
    bundle_ledger.write_entries(
        "concepts/survivor",
        bundle_dir,
        survivor_id="concepts/survivor",
        entries=[entry_0, entry_1],
    )
    # No git identity was ever configured -- `_autocommit` never ran, so no
    # reset point exists, regardless of whether a `.git` directory exists.
    monkeypatch.setattr("openkos.cli.main.vcs_git.has_reset_point", lambda root: False)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    label = "[FAIL] Merge ledger entries free of post-merge mutation"
    assert label in result.stdout
    fail_and_after = result.stdout.split(label, 1)[1]
    assert "no git reset point is available" in fail_and_after
    assert "git reset --hard" not in fail_and_after
    assert "reversibility" in fail_and_after.lower()
    assert "not guaranteed" in fail_and_after.lower()


def test_doctor_ledger_checks_never_write_to_the_ledger_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario: "The check never writes" -- doctor stays read-only even
    when it finds a torn write AND a nesting violation."""
    _init_workspace(tmp_path, monkeypatch)
    _fake_client_and_git(monkeypatch)
    bundle_dir = WorkspaceLayout(tmp_path).bundle_dir
    survivor_path = bundle_dir / "concepts" / "survivor.md"
    survivor_path.parent.mkdir(parents=True, exist_ok=True)
    survivor_text = "---\ntype: Concept\ntitle: Survivor\n---\nBody.\n"
    survivor_path.write_text(survivor_text, encoding="utf-8")
    bundle_ledger.write_pending(
        "concepts/survivor",
        bundle_dir,
        survivor_id="concepts/survivor",
        entries=[_make_ledger_entry()],
        expected_survivor_sha256=bundle_ledger.survivor_sha256(survivor_text),
    )
    ledger_root = bundle_ledger.ledger_root(bundle_dir)
    before = {
        path: path.read_bytes()
        for path in sorted(ledger_root.rglob("*"))
        if path.is_file()
    }

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    after = {
        path: path.read_bytes()
        for path in sorted(ledger_root.rglob("*"))
        if path.is_file()
    }
    assert after == before


def test_doctor_workspace_fts_present_shows_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A present `.openkos/fts.db` prints `[PASS] Workspace FTS index
    present` (issue #553; doctor-command spec: Workspace FTS Index Presence
    Check)."""
    _init_workspace(tmp_path, monkeypatch)
    openkos_dir = tmp_path / ".openkos"
    openkos_dir.mkdir(parents=True, exist_ok=True)
    (openkos_dir / "fts.db").write_bytes(b"")

    result = runner.invoke(app, ["doctor"])

    assert "[PASS] Workspace FTS index present" in result.stdout


def test_doctor_workspace_fts_absent_shows_fail_with_reindex_remediation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An absent `.openkos/fts.db` prints `[FAIL] Workspace FTS index
    present` with an `openkos reindex` remediation, and stays informational
    -- exit 0 when every critical check otherwise passes. This is #553's
    exact evidence shape: doctor passed every check while the first query
    was about to answer without lexical retrieval."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[DEFAULT_MODEL, DEFAULT_EMBEDDING_MODEL]),
    )
    monkeypatch.setattr("openkos.cli.main.probe_vec_loadable", lambda: True)
    monkeypatch.setattr("openkos.vcs.git.git_available", lambda: True)
    monkeypatch.setattr("openkos.vcs.git.filter_repo_available", lambda: True)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "[FAIL] Workspace FTS index present" in result.stdout
    assert "openkos reindex" in result.stdout


def test_doctor_workspace_fts_check_skipped_outside_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outside an initialized workspace, the FTS presence check prints
    `[SKIP]` and does not affect the exit code -- mirroring the workspace
    vector index check's workspace-only shape."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        _fake_ollama_client(installed=[DEFAULT_MODEL, DEFAULT_EMBEDDING_MODEL]),
    )
    monkeypatch.setattr("openkos.cli.main.probe_vec_loadable", lambda: True)

    result = runner.invoke(app, ["doctor"])

    assert "[SKIP] Workspace FTS index present" in result.stdout
