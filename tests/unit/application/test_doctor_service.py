"""Direct unit tests for `openkos.application.doctor`: the `doctor`
command's read core, extracted out of `cli/main.py` (issue #995, PR 6 --
the LAST slice of MVP 3's "prerequisite zero").

Mirrors `test_lint_service.py`'s posture: these exercise `run_diagnostics`
directly against a real (tmp-path) workspace and a FAKE `BackendDiagnostics`
client, never a CLI invocation. `tests/unit/cli/test_doctor.py` stays the
black-box, rendered-output contract exercising every check's exact wording
end to end; this file targets what is NEW at this layer and cannot be
proved at the CLI layer alone: that `run_diagnostics` never imports a
concrete backend and still tells `BackendUnavailable` apart from a generic
`BackendError` (WALL 1), that the `git_available`/`filter_repo_available`/
`reset_point_available` booleans are genuinely INJECTED rather than
computed (WALL 2), and that `build_client` -- the factory that replaced a
constructed `client` parameter, issue #1002 item B -- is always called
with the SAME model check 2's own `CheckResult` reports, across every
fallback branch config-valid has (outside a workspace, inside one, and on
a malformed `openkos.yaml`).

Written BEFORE `openkos.application.doctor` existed (strict TDD)."""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from openkos import config, read_outcome
from openkos.application import doctor as doctor_service
from openkos.bundle import ledger as bundle_ledger
from openkos.llm.base import (
    BackendError,
    BackendHostLocality,
    BackendUnavailable,
    InstalledModel,
)
from openkos.model import okf


@pytest.fixture(autouse=True)
def _private_umask() -> Iterator[None]:
    """Hand-built `.openkos/` fixtures below get the modes a user with a
    private umask would have, so the owner-only state check (#1135) stays
    quiet and the fixed check count these tests pin is unchanged."""
    old = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(old)


def _workspace(
    tmp_path: Path,
    *,
    model: str = config.DEFAULT_MODEL,
    embedding_model: str = config.DEFAULT_EMBEDDING_MODEL,
) -> config.WorkspaceLayout:
    """A workspace root with a real `bundle/` directory carrying the two
    files `config.require_workspace` checks for -- `run_diagnostics` reads
    the real filesystem directly (not through a CLI `init`), so both must
    exist before check 1 reports "initialized"."""
    config.write_config(tmp_path, model=model, embedding_model=embedding_model)
    layout = config.WorkspaceLayout(tmp_path)
    layout.bundle_dir.mkdir(parents=True, exist_ok=True)
    (layout.bundle_dir / "index.md").write_text("# Index\n", encoding="utf-8")
    (layout.bundle_dir / "log.md").write_text("# Log\n", encoding="utf-8")
    return layout


_LOCAL_LOCALITY = BackendHostLocality(is_local=True, display_host="localhost:11434")


@dataclass
class _FakeBackend:
    """A minimal `BackendDiagnostics` test double: a plain dataclass with a
    `locality` ATTRIBUTE (not a `@property`) -- structural typing accepts
    both, and using a plain attribute here keeps the fixture simple. Never
    touches the network."""

    tags: list[str] = field(default_factory=list)
    error: Exception | None = None
    locality: BackendHostLocality = field(default_factory=lambda: _LOCAL_LOCALITY)

    def list_models(self) -> list[InstalledModel]:
        if self.error is not None:
            raise self.error
        return [InstalledModel(tag=tag, family=None) for tag in self.tags]


class _CustomBackendUnavailable(BackendUnavailable):
    """A LOCAL subclass of the generic `BackendUnavailable` -- deliberately
    NOT `ollama.OllamaUnavailable` -- so a test that raises this and still
    sees check 3 branch correctly proves `run_diagnostics` catches the
    GENERIC base type from `openkos.llm.base`, not a concrete Ollama
    exception it must never import (WALL 1)."""


class _CustomBackendError(BackendError):
    """The `BackendError` analogue of `_CustomBackendUnavailable` above."""


def _by_label(
    results: tuple[doctor_service.CheckResult, ...], label: str
) -> doctor_service.CheckResult:
    matches = [r for r in results if r.label == label]
    assert len(matches) == 1, f"expected exactly one {label!r}, found {matches}"
    return matches[0]


# --- shape: fifteen checks, in order, compute-then-render ---


def test_run_diagnostics_returns_exactly_fifteen_checks(tmp_path: Path) -> None:
    """Thirteen numbered checks plus two lettered sub-checks (5b, 7b) = 15
    -- the pre-extraction docstring's "twelve" was already stale before
    this extraction (`tests/unit/cli/test_doctor.py` already asserted 15
    `[PASS]` lines); this pins the ACTUAL count at the service layer.

    Also pins the exact SEQUENCE (#1002 item E): the section header above
    this test promises "fifteen checks, in order, compute-then-render" and
    `run_diagnostics`' own docstring promises the checks run "in the SAME
    order the pre-extraction command body ran them", but until this
    assertion existed nothing checked that -- `_by_label` is a lookup, not
    an order proof, and every other test in this file reads results
    through it. A reordering of two checks (e.g. swapping the append calls
    for checks 9 and 10) would leave `len(results) == 15` green and every
    `_by_label`-based test green too, while changing which refusal an
    operator sees first. This assertion is the only thing in this file
    that can fail on order alone."""
    layout = _workspace(tmp_path)
    results = doctor_service.run_diagnostics(
        layout.root,
        build_client=lambda _cfg, _model: _FakeBackend(
            tags=[config.DEFAULT_MODEL, config.DEFAULT_EMBEDDING_MODEL]
        ),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=lambda: True,
    )
    assert len(results) == 15
    assert [r.label for r in results] == [
        "Workspace initialized",
        "Config valid",
        "Ollama reachable",
        f"Model '{config.DEFAULT_MODEL}' installed",
        f"Embedding model '{config.DEFAULT_EMBEDDING_MODEL}' installed",
        "Task models installed",
        "Bundle readable",
        "Workspace vector index present",
        "Workspace FTS index present",
        "Vector extension loadable",
        "git available",
        "git-filter-repo available",
        "Backend host locality",
        "Merge ledger torn writes",
        "Merge ledger entries free of post-merge mutation",
    ]


def test_run_diagnostics_never_raises_outside_a_workspace_with_unreachable_backend_and_both_vcs_booleans_false(
    tmp_path: Path,
) -> None:
    """Renamed from `..._never_raises_for_any_injected_failure_mode`
    (#1002 item E): that name overclaimed. It passes `tmp_path` DIRECTLY
    rather than a real workspace, so `in_workspace` is False and checks
    6/12/13 all take their `skip` branch instead of reaching
    `okf.survey_bundle`/`bundle_ledger.scan_torn_writes`/
    `bundle_ledger.scan_nesting_violations` -- THREE of the four paths
    "THE RAISE CONTRACT" (`run_diagnostics`' own docstring) names as able
    to raise straight out of this function. This test never enters any of
    them, and its `reset_point_available` lambda returns a plain `False`
    rather than raising, so the fourth path is untouched too -- a test
    named for "any injected failure mode" was, in fact, injecting a
    failure mode none of the four raise paths can see.

    What this test genuinely proves, and the reason it is RENAMED rather
    than widened into a workspace: OUTSIDE a workspace, with an
    UNREACHABLE backend and BOTH `openkos.vcs` booleans false,
    `run_diagnostics` still accumulates and returns all 15 results instead
    of raising (D5) -- a real, distinct scenario (a machine with no
    workspace and no git at all, pointed at `doctor` before `ollama
    serve` has ever run), not a placeholder for the four raise paths.

    What it deliberately does NOT cover, so as not to duplicate existing
    coverage: the four documented raise paths themselves. Three are
    already exercised, post-ADR-0022, by
    `test_run_diagnostics_reports_not_run_when_survey_bundle_scan_torn_writes_or_scan_nesting_violations_raises`
    (they now degrade to `not-run` rather than propagate) and by
    `test_run_diagnostics_reports_the_integrity_check_as_not_run_when_reset_point_available_raises`.
    The contract is also NOT "never raises" in the absolute --
    `test_run_diagnostics_lets_a_bundle_dot_directory_value_error_propagate_uncaught`
    and
    `test_run_diagnostics_lets_an_unrelated_reset_point_available_exception_propagate_uncaught`
    both assert a `ValueError`/`RuntimeError` DOES propagate uncaught.
    Widening THIS test to enter a workspace would duplicate those four,
    not add coverage; this test's job is the outside-workspace,
    everything-unreachable case those four never touch."""
    results = doctor_service.run_diagnostics(
        tmp_path,
        build_client=lambda _cfg, _model: _FakeBackend(
            error=_CustomBackendUnavailable("down")
        ),
        git_available=False,
        filter_repo_available=False,
        reset_point_available=lambda: False,
    )
    assert len(results) == 15


# --- item B (#1002): one config read, and the client is built from it ---


def test_build_client_gets_the_same_model_check_2_reports_even_when_config_drifts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The titular defect (#1002 item B): with TWO `config.read_config`
    calls, an `openkos.yaml` edit landing between them lets the model
    PROBED (what the injected client is built with) and the model REPORTED
    (check 2's own `CheckResult.detail`) come apart. This drives
    `read_config` to return a DIFFERENT `Config` on each call it receives
    and asserts `build_client` is handed the SAME model check 2's own
    `CheckResult` states -- and that `read_config` is called exactly ONCE,
    which is the only way that agreement can be guaranteed rather than
    coincidental."""
    layout = _workspace(tmp_path, model="model-a")
    real_cfg = config.read_config(tmp_path)
    responses = [real_cfg, dataclasses.replace(real_cfg, model="model-b")]
    calls: list[None] = []

    def _fake_read_config(root: Path) -> config.Config:
        calls.append(None)
        return responses[len(calls) - 1]

    monkeypatch.setattr(config, "read_config", _fake_read_config)

    built_with: list[str] = []

    def _build_client(cfg: config.Config | None, model: str) -> _FakeBackend:
        del cfg
        built_with.append(model)
        return _FakeBackend(tags=[model])

    results = doctor_service.run_diagnostics(
        layout.root,
        build_client=_build_client,
        git_available=True,
        filter_repo_available=True,
        reset_point_available=lambda: True,
    )

    assert len(calls) == 1, f"expected exactly one read_config call, got {len(calls)}"
    config_check = _by_label(results, "Config valid")
    assert config_check.status == "pass"
    assert config_check.detail is not None
    reported_model = config_check.detail.removeprefix("model ")
    assert built_with == [reported_model]


# --- not-run (ADR-0022, D1/D2/D3): a raising in-workspace read degrades ---
# --- only the check it belongs to, without discarding the other fourteen ---


def test_run_diagnostics_lets_a_bundle_dot_directory_value_error_propagate_uncaught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D1's `except OSError` guard around `okf.survey_bundle` (T1.6) is
    deliberately narrow: a `ValueError` from `bundle_dot_directory`'s own
    `relative_to` call is a documented CALLER bug (a path outside the
    bundle), never the environment's fault, and must keep propagating
    uncaught -- containing it would convert a programming error into a
    permanently green `not-run` (design.md Decision 3, ADR-0022
    Consequences). This is the mutation-discipline broad-direction tripwire
    for D1 (tasks.md T1.9): it must go RED if D1's guard is ever widened to
    bare `Exception`."""
    layout = _workspace(tmp_path)

    def _raise(*args: object, **kwargs: object) -> object:
        raise ValueError("path outside the bundle")

    monkeypatch.setattr("openkos.model.okf.survey_bundle", _raise)

    with pytest.raises(ValueError, match="path outside the bundle"):
        doctor_service.run_diagnostics(
            layout.root,
            build_client=lambda _cfg, _model: _FakeBackend(
                tags=[config.DEFAULT_MODEL, config.DEFAULT_EMBEDDING_MODEL]
            ),
            git_available=True,
            filter_repo_available=True,
            reset_point_available=lambda: True,
        )


@pytest.mark.parametrize(
    ("patch_target", "label"),
    [
        ("openkos.model.okf.survey_bundle", "Bundle readable"),
        ("openkos.bundle.ledger.scan_torn_writes", "Merge ledger torn writes"),
        (
            "openkos.bundle.ledger.scan_nesting_violations",
            "Merge ledger entries free of post-merge mutation",
        ),
    ],
)
def test_run_diagnostics_reports_not_run_when_survey_bundle_scan_torn_writes_or_scan_nesting_violations_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    patch_target: str,
    label: str,
) -> None:
    """D1/D2/D3 (design.md Decision 3): each of the three raising in-
    workspace reads must degrade only its own `CheckResult` to `not-run`,
    never discard the other fourteen. This is the genuine in-workspace
    counterpart `test_run_diagnostics_never_raises_for_any_injected_failure_mode`
    above cannot be: that test runs OUTSIDE a workspace, where checks 6/12/13
    never reach `okf.survey_bundle`/`scan_torn_writes`/`scan_nesting_violations`
    at all -- they take their `skip` branch instead.

    `.openkos/vectors.db` and `.openkos/fts.db` are pre-created so Decision
    7's downstream propagation (checks 7/7b consulting check 6's own not-run
    outcome) never engages here -- that cascade is Decision 7's OWN scenario,
    covered separately by
    `test_run_diagnostics_reports_the_index_checks_as_not_run_when_bundle_readable_did_not_run`
    below, which uses the opposite fixture (no pre-created index files)."""
    layout = _workspace(tmp_path)
    layout.openkos_dir.mkdir(parents=True, exist_ok=True)
    layout.vectors_db_path.write_bytes(b"")
    layout.fts_db_path.write_bytes(b"")

    def _raise(*args: object, **kwargs: object) -> object:
        raise OSError("simulated read failure")

    monkeypatch.setattr(patch_target, _raise)

    results = doctor_service.run_diagnostics(
        layout.root,
        build_client=lambda _cfg, _model: _FakeBackend(
            tags=[config.DEFAULT_MODEL, config.DEFAULT_EMBEDDING_MODEL]
        ),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=lambda: True,
    )

    assert len(results) == 15
    not_run = [r for r in results if r.status == read_outcome.NOT_RUN]
    assert len(not_run) == 1
    assert not_run[0].label == label
    assert not_run[0].detail is not None
    assert "simulated read failure" in not_run[0].detail
    other_statuses = {r.status for r in results if r.label != label}
    assert read_outcome.NOT_RUN not in other_statuses


def test_run_diagnostics_reports_the_integrity_check_as_not_run_when_reset_point_available_raises(
    tmp_path: Path,
) -> None:
    """D4 (design.md Decision 2/3): the injected `reset_point_available`
    thunk raising `ProbeUnavailable` degrades only the merge-ledger-
    integrity check, inside its `if violations:` branch -- the git-specific
    exception is translated to this application-owned type at the CLI
    boundary (`application/*` may not import `openkos.vcs`,
    `test_layering.py`), so this test injects the translated type directly,
    exactly as the CLI adapter's `_reset_point_available` thunk would."""
    layout = _workspace(tmp_path)
    layout.openkos_dir.mkdir(parents=True, exist_ok=True)
    layout.vectors_db_path.write_bytes(b"")
    layout.fts_db_path.write_bytes(b"")
    _write_nesting_violation(layout.bundle_dir)

    def _raise() -> bool:
        raise doctor_service.ProbeUnavailable("git broke")

    results = doctor_service.run_diagnostics(
        layout.root,
        build_client=lambda _cfg, _model: _FakeBackend(
            tags=[config.DEFAULT_MODEL, config.DEFAULT_EMBEDDING_MODEL]
        ),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=_raise,
    )

    assert len(results) == 15
    check = _by_label(results, "Merge ledger entries free of post-merge mutation")
    assert check.status == read_outcome.NOT_RUN
    assert check.detail is not None
    assert "git broke" in check.detail


def test_run_diagnostics_lets_an_unrelated_reset_point_available_exception_propagate_uncaught(
    tmp_path: Path,
) -> None:
    """D4's `except ProbeUnavailable` guard (T1.8) is deliberately narrow: a
    thunk that raises something OTHER than `ProbeUnavailable` (e.g. a bug in
    the thunk itself, not a git failure) must keep propagating uncaught --
    catching everything here would silently convert a caller bug into a
    permanently green `not-run`. This is the mutation-discipline
    broad-direction tripwire for D4 (tasks.md T1.11): it must go RED if D4's
    guard is ever widened to bare `Exception`."""
    layout = _workspace(tmp_path)
    layout.openkos_dir.mkdir(parents=True, exist_ok=True)
    layout.vectors_db_path.write_bytes(b"")
    layout.fts_db_path.write_bytes(b"")
    _write_nesting_violation(layout.bundle_dir)

    def _raise() -> bool:
        raise RuntimeError("bug in the thunk itself")

    with pytest.raises(RuntimeError, match="bug in the thunk itself"):
        doctor_service.run_diagnostics(
            layout.root,
            build_client=lambda _cfg, _model: _FakeBackend(
                tags=[config.DEFAULT_MODEL, config.DEFAULT_EMBEDDING_MODEL]
            ),
            git_available=True,
            filter_repo_available=True,
            reset_point_available=_raise,
        )


def test_run_diagnostics_reports_the_index_checks_as_not_run_when_bundle_readable_did_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Decision 7: checks 7/7b (workspace-vector-index-present,
    workspace-fts-present) must not guess `bundle_emptiness` when check 6
    (bundle-readable) never got to set it. Opposite fixture from the
    parametrized test above -- `.openkos/vectors.db`/`fts.db` do NOT exist
    -- so checks 7/7b would otherwise reach their `bundle_empty`-reading
    branch instead of the `index_path.exists()` short-circuit."""
    layout = _workspace(tmp_path)

    def _raise(*args: object, **kwargs: object) -> object:
        raise OSError("simulated read failure")

    monkeypatch.setattr("openkos.model.okf.survey_bundle", _raise)

    results = doctor_service.run_diagnostics(
        layout.root,
        build_client=lambda _cfg, _model: _FakeBackend(
            tags=[config.DEFAULT_MODEL, config.DEFAULT_EMBEDDING_MODEL]
        ),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=lambda: True,
    )

    bundle_readable = _by_label(results, "Bundle readable")
    vector_index = _by_label(results, "Workspace vector index present")
    fts_index = _by_label(results, "Workspace FTS index present")
    assert bundle_readable.status == read_outcome.NOT_RUN
    assert vector_index.status == read_outcome.NOT_RUN
    assert fts_index.status == read_outcome.NOT_RUN
    assert vector_index.detail is not None
    assert "Bundle readable" in vector_index.detail
    assert fts_index.detail is not None
    assert "Bundle readable" in fts_index.detail


# --- outside a workspace: checks 1/2/6/7/7b/12/13 skip or fail; 3-5b/8-11 still run ---


def test_outside_workspace_workspace_check_fails_and_config_check_skips(
    tmp_path: Path,
) -> None:
    results = doctor_service.run_diagnostics(
        tmp_path,
        build_client=lambda _cfg, _model: _FakeBackend(tags=[config.DEFAULT_MODEL]),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=lambda: False,
    )
    workspace_check = _by_label(results, "Workspace initialized")
    assert workspace_check.status == "fail"
    assert workspace_check.critical is False
    assert workspace_check.remediation == "openkos init"

    config_check = _by_label(results, "Config valid")
    assert config_check.status == "skip"
    assert config_check.critical is True


def test_outside_workspace_workspace_only_checks_all_skip(tmp_path: Path) -> None:
    results = doctor_service.run_diagnostics(
        tmp_path,
        build_client=lambda _cfg, _model: _FakeBackend(
            tags=[config.DEFAULT_MODEL, config.DEFAULT_EMBEDDING_MODEL]
        ),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=lambda: False,
    )
    for label in (
        "Bundle readable",
        "Workspace vector index present",
        "Workspace FTS index present",
        "Merge ledger torn writes",
        "Merge ledger entries free of post-merge mutation",
    ):
        assert _by_label(results, label).status == "skip", label


def test_outside_workspace_backend_checks_still_run_against_default_model(
    tmp_path: Path,
) -> None:
    """Spec: "Doctor Works Outside An Initialized Workspace" -- checks
    3/4/5/8/9/10/11 still probe `config.DEFAULT_MODEL`/
    `config.DEFAULT_EMBEDDING_MODEL`."""
    results = doctor_service.run_diagnostics(
        tmp_path,
        build_client=lambda _cfg, _model: _FakeBackend(
            tags=[config.DEFAULT_MODEL, config.DEFAULT_EMBEDDING_MODEL]
        ),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=lambda: False,
    )
    assert _by_label(results, "Ollama reachable").status == "pass"
    assert (
        _by_label(results, f"Model '{config.DEFAULT_MODEL}' installed").status == "pass"
    )
    assert (
        _by_label(
            results, f"Embedding model '{config.DEFAULT_EMBEDDING_MODEL}' installed"
        ).status
        == "pass"
    )


# --- critical/non-critical classification (mutation guard 1) ---


def test_critical_flag_matches_spec_per_check(tmp_path: Path) -> None:
    """The exit code (`any(status == "fail" and critical for r in results)`,
    adapter-side) depends entirely on each check's `critical` flag -- a
    mutation flipping ANY of these would silently change what `doctor`
    exits nonzero for. Pins every check's `critical` value in one place,
    workspace-only checks included via a real workspace."""
    layout = _workspace(tmp_path)
    results = doctor_service.run_diagnostics(
        layout.root,
        build_client=lambda _cfg, _model: _FakeBackend(
            tags=[config.DEFAULT_MODEL, config.DEFAULT_EMBEDDING_MODEL]
        ),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=lambda: True,
    )
    critical_labels = {
        "Config valid",
        "Ollama reachable",
        f"Model '{config.DEFAULT_MODEL}' installed",
    }
    for result in results:
        expected = result.label in critical_labels
        assert result.critical is expected, (
            f"{result.label!r}: expected critical={expected}, got {result.critical}"
        )
    # "Workspace initialized" is explicitly non-critical despite failing
    # outside a workspace (spec: only config/backend/model block the exit).
    assert _by_label(results, "Workspace initialized").critical is False


def test_critical_check_failure_is_distinguishable_from_noncritical_failure(
    tmp_path: Path,
) -> None:
    """A malformed `openkos.yaml` fails the CRITICAL config-valid check;
    an unreachable backend also fails a CRITICAL check (Ollama reachable);
    together they prove `critical=True` is carried on the `CheckResult`
    itself, not inferred by the adapter from the label."""
    config.write_config(tmp_path)
    layout = config.WorkspaceLayout(tmp_path)
    layout.bundle_dir.mkdir(parents=True, exist_ok=True)
    (layout.bundle_dir / "index.md").write_text("# Index\n", encoding="utf-8")
    (layout.bundle_dir / "log.md").write_text("# Log\n", encoding="utf-8")
    # Corrupt the config after `write_config` wrote a valid one.
    layout.config_path.write_text("not: [valid, yaml, mapping", encoding="utf-8")

    results = doctor_service.run_diagnostics(
        layout.root,
        build_client=lambda _cfg, _model: _FakeBackend(tags=[config.DEFAULT_MODEL]),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=lambda: False,
    )
    config_check = _by_label(results, "Config valid")
    assert config_check.status == "fail"
    assert config_check.critical is True
    assert config_check.remediation == "fix openkos.yaml"


# --- WALL 1: the Protocol/exception boundary (mutation guard 2) ---


def test_backend_unavailable_uses_the_generic_base_type_not_a_concrete_ollama_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_CustomBackendUnavailable` is NOT `ollama.OllamaUnavailable` -- if
    `run_diagnostics` ever caught the concrete Ollama exception instead of
    the generic `BackendUnavailable` from `openkos.llm.base`, this failure
    would propagate uncaught instead of becoming a `fail` `CheckResult`,
    and this test would fail with an uncaught exception rather than an
    assertion."""
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/local/bin/ollama")
    results = doctor_service.run_diagnostics(
        tmp_path,
        build_client=lambda _cfg, _model: _FakeBackend(
            error=_CustomBackendUnavailable("connection refused")
        ),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=lambda: False,
    )
    check = _by_label(results, "Ollama reachable")
    assert check.status == "fail"
    assert check.critical is True
    assert check.remediation == "ollama serve"
    assert check.detail == "connection refused"


def test_backend_unavailable_names_the_missing_binary_when_ollama_is_not_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    results = doctor_service.run_diagnostics(
        tmp_path,
        build_client=lambda _cfg, _model: _FakeBackend(
            error=_CustomBackendUnavailable("connection refused")
        ),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=lambda: False,
    )
    check = _by_label(results, "Ollama reachable")
    assert check.remediation is not None
    assert "no `ollama` binary found on PATH" in check.remediation


def test_backend_error_that_is_not_unavailable_fails_with_no_remediation(
    tmp_path: Path,
) -> None:
    """A REACHABLE-but-erroring backend (`BackendError`, not
    `BackendUnavailable`) never gets the `shutil.which`-informed
    remediation -- only a transport failure does."""
    results = doctor_service.run_diagnostics(
        tmp_path,
        build_client=lambda _cfg, _model: _FakeBackend(
            error=_CustomBackendError("Ollama request failed (500)")
        ),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=lambda: False,
    )
    check = _by_label(results, "Ollama reachable")
    assert check.status == "fail"
    assert check.critical is True
    assert check.remediation is None
    assert check.detail == "Ollama request failed (500)"


def test_unreachable_backend_blocks_model_and_embedding_checks_with_skip(
    tmp_path: Path,
) -> None:
    """D6: model-installed and embedding-model-installed share Ollama
    reachability as their root cause, so an unreachable backend `skip`s
    them rather than reporting a second, redundant `fail`."""
    results = doctor_service.run_diagnostics(
        tmp_path,
        build_client=lambda _cfg, _model: _FakeBackend(
            error=_CustomBackendUnavailable("down")
        ),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=lambda: False,
    )
    model_check = _by_label(results, f"Model '{config.DEFAULT_MODEL}' installed")
    assert model_check.status == "skip"
    assert model_check.detail == "blocked: Ollama unreachable"
    embedding_check = _by_label(
        results, f"Embedding model '{config.DEFAULT_EMBEDDING_MODEL}' installed"
    )
    assert embedding_check.status == "skip"


def test_model_tag_matches_bare_configured_against_latest_installed(
    tmp_path: Path,
) -> None:
    """Exercises the pure `model_tag_matches` (moved to `llm/base.py`,
    issue #995 PR 6) through the service: a bare configured tag (no `:`)
    matches an explicit `:latest` installed tag."""
    layout = _workspace(tmp_path, model="customtag")
    results = doctor_service.run_diagnostics(
        layout.root,
        build_client=lambda _cfg, _model: _FakeBackend(tags=["customtag:latest"]),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=lambda: True,
    )
    assert _by_label(results, "Model 'customtag' installed").status == "pass"


# --- WALL 2: git booleans are injected, never computed here ---


def test_git_available_boolean_is_injected_verbatim(tmp_path: Path) -> None:
    results_true = doctor_service.run_diagnostics(
        tmp_path,
        build_client=lambda _cfg, _model: _FakeBackend(tags=[config.DEFAULT_MODEL]),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=lambda: False,
    )
    assert _by_label(results_true, "git available").status == "pass"

    results_false = doctor_service.run_diagnostics(
        tmp_path,
        build_client=lambda _cfg, _model: _FakeBackend(tags=[config.DEFAULT_MODEL]),
        git_available=False,
        filter_repo_available=True,
        reset_point_available=lambda: False,
    )
    git_check = _by_label(results_false, "git available")
    assert git_check.status == "fail"
    assert git_check.critical is False
    assert git_check.remediation is not None


def test_filter_repo_available_boolean_is_injected_verbatim(tmp_path: Path) -> None:
    results = doctor_service.run_diagnostics(
        tmp_path,
        build_client=lambda _cfg, _model: _FakeBackend(tags=[config.DEFAULT_MODEL]),
        git_available=True,
        filter_repo_available=False,
        reset_point_available=lambda: False,
    )
    check = _by_label(results, "git-filter-repo available")
    assert check.status == "fail"
    assert check.critical is False


def _write_nesting_violation(bundle_dir: Path) -> None:
    """Two ledger entries where entry 1's embedded `survivor_before`
    disagrees with entry 0's actual snapshot -- `scan_nesting_violations`'s
    exact corruption class (#550 consequence 2), mirroring
    `tests/unit/cli/test_doctor.py`'s own fixture construction."""
    entry_0 = okf.MergeLedgerEntry(
        schema=okf.MERGE_LEDGER_SCHEMA_V3,
        merged_at="2026-07-20T00:00:00Z",
        absorbed_id="concepts/absorbed-0",
        absorbed_snapshot="absorbed text",
        survivor_before="survivor text",
        index_before="index text",
        log_before="log text",
        link_rewrites=[],
        sensitivity_before="private",
        sensitivity_after="private",
    )
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
    entry_1 = okf.MergeLedgerEntry(
        schema=okf.MERGE_LEDGER_SCHEMA_V3,
        merged_at="2026-07-20T00:00:00Z",
        absorbed_id="concepts/absorbed-1",
        absorbed_snapshot="absorbed text",
        survivor_before=okf.dump_frontmatter(embedded_metadata),
        index_before="index text",
        log_before="log text",
        link_rewrites=[],
        sensitivity_before="private",
        sensitivity_after="private",
    )
    bundle_ledger.write_entries(
        "concepts/survivor",
        bundle_dir,
        survivor_id="concepts/survivor",
        entries=[entry_0, entry_1],
    )


def test_reset_point_available_true_names_the_git_reset_remedy(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    _write_nesting_violation(layout.bundle_dir)

    results = doctor_service.run_diagnostics(
        layout.root,
        build_client=lambda _cfg, _model: _FakeBackend(tags=[config.DEFAULT_MODEL]),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=lambda: True,
    )
    check = _by_label(results, "Merge ledger entries free of post-merge mutation")
    assert check.status == "fail"
    assert check.remediation is not None
    assert "git reset --hard" in check.remediation
    assert "openkos reindex" in check.remediation


def test_reset_point_available_false_names_no_reset_remedy(tmp_path: Path) -> None:
    """The injected `reset_point_available=lambda: False` path -- a workspace with
    no git reset point -- must never print an unusable `git reset --hard`
    remedy (the gap fix this check exists for)."""
    layout = _workspace(tmp_path)
    _write_nesting_violation(layout.bundle_dir)

    results = doctor_service.run_diagnostics(
        layout.root,
        build_client=lambda _cfg, _model: _FakeBackend(tags=[config.DEFAULT_MODEL]),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=lambda: False,
    )
    check = _by_label(results, "Merge ledger entries free of post-merge mutation")
    assert check.status == "fail"
    assert check.remediation is not None
    assert "git reset --hard" not in check.remediation
    assert "no git reset point is available" in check.remediation


# --- build_client's model, in every fallback branch check 2 has (the ---
# --- coverage `resolve_diagnostic_model` used to carry on its own) ---


def _recording_build_client(
    built_with: list[str],
) -> Callable[[config.Config | None, str], _FakeBackend]:
    def _build(cfg: config.Config | None, model: str) -> _FakeBackend:
        del cfg
        built_with.append(model)
        return _FakeBackend()

    return _build


def test_build_client_gets_the_default_model_outside_a_workspace(
    tmp_path: Path,
) -> None:
    built_with: list[str] = []
    doctor_service.run_diagnostics(
        tmp_path,
        build_client=_recording_build_client(built_with),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=lambda: False,
    )
    assert built_with == [config.DEFAULT_MODEL]


def test_build_client_gets_the_configured_model_inside_a_workspace(
    tmp_path: Path,
) -> None:
    layout = _workspace(tmp_path, model="custom-model:8b")
    built_with: list[str] = []
    doctor_service.run_diagnostics(
        layout.root,
        build_client=_recording_build_client(built_with),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=lambda: True,
    )
    assert built_with == ["custom-model:8b"]


def test_build_client_gets_the_default_model_when_config_is_malformed(
    tmp_path: Path,
) -> None:
    layout = _workspace(tmp_path)
    layout.config_path.write_text("not: [valid, yaml, mapping", encoding="utf-8")
    built_with: list[str] = []
    doctor_service.run_diagnostics(
        layout.root,
        build_client=_recording_build_client(built_with),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=lambda: True,
    )
    assert built_with == [config.DEFAULT_MODEL]


# --- locality: always emitted, reuses the same client, never fails ---


def test_backend_host_locality_reads_from_the_same_client_check_3_used(
    tmp_path: Path,
) -> None:
    remote_locality = BackendHostLocality(
        is_local=False, display_host="remote.example:11434"
    )
    results = doctor_service.run_diagnostics(
        tmp_path,
        build_client=lambda _cfg, _model: _FakeBackend(
            tags=[config.DEFAULT_MODEL], locality=remote_locality
        ),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=lambda: False,
    )
    check = _by_label(results, "Backend host locality")
    assert check.status == "pass"
    assert check.detail is not None
    assert "remote.example:11434" in check.detail
    assert "not this machine" in check.detail


def test_backend_host_locality_skips_but_still_reports_when_unreachable(
    tmp_path: Path,
) -> None:
    results = doctor_service.run_diagnostics(
        tmp_path,
        build_client=lambda _cfg, _model: _FakeBackend(
            error=_CustomBackendUnavailable("down")
        ),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=lambda: False,
    )
    check = _by_label(results, "Backend host locality")
    assert check.status == "skip"
    assert check.detail is not None
    assert "not verified while Ollama is unreachable" in check.detail


def test_reset_point_thunk_is_not_called_without_a_nesting_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`reset_point_available` is a thunk so its two `git` subprocess calls
    stay unpaid on the common path.

    The pre-extraction body reached `vcs_git.repo_root`/`has_reset_point`
    only inside check 13's `if violations:` branch. A first draft of this
    extraction injected an already-computed `bool`, which moved both
    subprocess calls onto EVERY in-workspace `doctor` invocation -- a cost
    added to the command people run precisely when their workspace is
    already misbehaving. Passing a callable restores the original profile,
    and a counter is the only thing that can prove it: an eagerly computed
    value looks identical from the outside once it has been computed.

    The counting thunk returns `True`, so if it were ever consulted here
    the check-13 branch would still pass -- this test can only fail on the
    call COUNT, never on the verdict, which is the property under test."""
    layout = _workspace(tmp_path)

    calls: list[None] = []

    def _counting_thunk() -> bool:
        calls.append(None)
        return True

    doctor_service.run_diagnostics(
        layout.root,
        build_client=lambda _cfg, _model: _FakeBackend(
            tags=[config.DEFAULT_MODEL, config.DEFAULT_EMBEDDING_MODEL]
        ),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=_counting_thunk,
    )

    assert calls == []


# ---------------------------------------------------------------------------
# issue #1057 Phase 12 -- doctor wording: endpoint+source, key set/not-set,
# openai-compatible remediation (doctor-command delta spec, design Decision
# 9). `doctor` keeps its OWN command-form strings here rather than calling
# Phase 13a's `application/backends.py` wording functions (tasks-phase
# decision 2: "doctor never calls them").
#
# Deviation from tasks.md's file name for this phase ("tests/unit/
# application/test_doctor.py"): this project has no such file. The real
# test module for `application/doctor.py` is `test_doctor_service.py` (its
# own module docstring explains the split from `tests/unit/cli/
# test_doctor.py`) -- extended here rather than creating a second,
# differently-named module for the same target.
# ---------------------------------------------------------------------------


def _openai_compatible_cfg(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    base_url: str = "http://127.0.0.1:8000",
    model: str = config.DEFAULT_MODEL,
) -> config.WorkspaceLayout:
    """A workspace with a real, `backend`-less `openkos.yaml` on disk (so
    check 1 stays genuinely `pass`), but `config.read_config` monkeypatched
    to return a `backend="openai-compatible"` `Config` -- `SELECTABLE_BACKENDS`
    still refuses that value in a REAL `openkos.yaml` until Phase 14
    (design Decision 10), exactly like `test_backends.py`'s own resolver
    tests construct a `Config` directly rather than through `read_config`."""
    layout = _workspace(tmp_path, model=model)
    real_cfg = config.read_config(tmp_path)
    oc_cfg = dataclasses.replace(
        real_cfg, backend="openai-compatible", base_url=base_url
    )
    monkeypatch.setattr(config, "read_config", lambda _root: oc_cfg)
    return layout


def test_doctor_shows_endpoint_and_source_when_not_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Doctor-command spec "Doctor Shows The Effective Endpoint And Its
    Resolution Source": a configured `base_url`, no `OLLAMA_HOST`, prints
    the effective endpoint and names `base_url` as its source (task 12.1).
    RED today: no such line exists."""
    layout = _openai_compatible_cfg(
        tmp_path, monkeypatch, base_url="http://127.0.0.1:8000"
    )
    locality = BackendHostLocality(is_local=True, display_host="127.0.0.1:8000")

    results = doctor_service.run_diagnostics(
        layout.root,
        build_client=lambda _cfg, _model: _FakeBackend(
            tags=[config.DEFAULT_MODEL], locality=locality
        ),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=lambda: True,
    )
    check = _by_label(results, "OpenAI-compatible server reachable")
    assert check.status == "pass"
    assert check.detail is not None
    assert "127.0.0.1:8000" in check.detail
    assert "base_url" in check.detail


def test_doctor_names_ollama_host_when_it_wins_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`OLLAMA_HOST` set alongside a configured `base_url` (`backend:
    ollama`) -- doctor names `OLLAMA_HOST`, not `base_url`, as the source
    (task 12.2). RED today: no endpoint-and-source line exists at all."""
    layout = _workspace(tmp_path)
    real_cfg = config.read_config(tmp_path)
    cfg_with_base_url = dataclasses.replace(real_cfg, base_url="http://cfghost:8080")
    monkeypatch.setattr(config, "read_config", lambda _root: cfg_with_base_url)
    monkeypatch.setenv("OLLAMA_HOST", "http://envhost:1234")
    locality = BackendHostLocality(is_local=False, display_host="envhost:1234")

    results = doctor_service.run_diagnostics(
        layout.root,
        build_client=lambda _cfg, _model: _FakeBackend(
            tags=[config.DEFAULT_MODEL], locality=locality
        ),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=lambda: True,
    )
    check = _by_label(results, "Ollama reachable")
    assert check.detail is not None
    assert "OLLAMA_HOST" in check.detail
    assert "cfghost" not in check.detail
    assert "envhost:1234" in check.detail


def test_doctor_default_path_prints_no_endpoint_line_byte_identical(
    tmp_path: Path,
) -> None:
    """No `base_url`/`OLLAMA_HOST` set: the reachable check's detail stays
    BYTE-IDENTICAL to its pre-existing default-path wording -- no
    endpoint-and-source line at all (task 12.3, doctor-command spec's third
    ADDED scenario). This is a regression pin, not new RED: it already
    passes today, and stays green after 12.4 lands."""
    layout = _workspace(tmp_path)
    results = doctor_service.run_diagnostics(
        layout.root,
        build_client=lambda _cfg, _model: _FakeBackend(tags=[config.DEFAULT_MODEL]),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=lambda: True,
    )
    check = _by_label(results, "Ollama reachable")
    assert check.detail == "1 models"


def test_doctor_never_prints_the_api_key_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sentinel-key test (doctor-command spec "The API Key Is Never Printed
    By Doctor", task 12.5): across a PASS run and a FAIL run (unreachable
    server, whose remediation names the endpoint), the sentinel API key
    value never appears in any `CheckResult`'s `detail`/`remediation`.
    Mutation-proof below."""
    layout = _openai_compatible_cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("OPENKOS_OPENAI_API_KEY", "sk-super-secret-sentinel")
    locality = BackendHostLocality(is_local=True, display_host="127.0.0.1:8000")

    def _assert_no_leak(results: tuple[doctor_service.CheckResult, ...]) -> None:
        for r in results:
            assert "sk-super-secret-sentinel" not in (r.detail or "")
            assert "sk-super-secret-sentinel" not in (r.remediation or "")

    passing = doctor_service.run_diagnostics(
        layout.root,
        build_client=lambda _cfg, _model: _FakeBackend(
            tags=[config.DEFAULT_MODEL], locality=locality
        ),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=lambda: True,
    )
    _assert_no_leak(passing)

    failing = doctor_service.run_diagnostics(
        layout.root,
        build_client=lambda _cfg, _model: _FakeBackend(
            error=_CustomBackendUnavailable("connection refused"), locality=locality
        ),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=lambda: True,
    )
    _assert_no_leak(failing)


def test_doctor_key_absence_never_gates_a_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`OPENKOS_OPENAI_API_KEY` unset, configured server needs no key: no
    check fails, is skipped, or is reported not-run because of the key's
    absence (task 12.6)."""
    layout = _openai_compatible_cfg(tmp_path, monkeypatch)
    locality = BackendHostLocality(is_local=True, display_host="127.0.0.1:8000")

    results = doctor_service.run_diagnostics(
        layout.root,
        build_client=lambda _cfg, _model: _FakeBackend(
            tags=[config.DEFAULT_MODEL], locality=locality
        ),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=lambda: True,
    )
    reachable = _by_label(results, "OpenAI-compatible server reachable")
    assert reachable.status == "pass"
    assert reachable.detail is not None
    assert "not set" in reachable.detail
    model_check = _by_label(results, f"Model '{config.DEFAULT_MODEL}' installed")
    assert model_check.status == "pass"


def test_doctor_openai_compatible_unreachable_remediation_no_ollama_wording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`cfg.backend == "openai-compatible"`, endpoint refuses connection:
    `[FAIL]` remediation names the configured endpoint, advises verifying
    the server is running, and contains NO `ollama`/`ollama serve`/
    `shutil.which("ollama")` reference (task 12.8). RED today: check 3
    always emits the Ollama-specific `shutil.which`-driven remediation
    regardless of `cfg.backend`."""
    import shutil

    calls: list[str] = []
    real_which = shutil.which

    def _tracking_which(name: str) -> str | None:
        calls.append(name)
        return real_which(name)

    monkeypatch.setattr(shutil, "which", _tracking_which)
    layout = _openai_compatible_cfg(
        tmp_path, monkeypatch, base_url="http://127.0.0.1:9009"
    )
    locality = BackendHostLocality(is_local=True, display_host="127.0.0.1:9009")

    results = doctor_service.run_diagnostics(
        layout.root,
        build_client=lambda _cfg, _model: _FakeBackend(
            error=_CustomBackendUnavailable("connection refused"), locality=locality
        ),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=lambda: True,
    )
    check = _by_label(results, "OpenAI-compatible server reachable")
    assert check.status == "fail"
    assert check.remediation is not None
    assert "127.0.0.1:9009" in check.remediation
    assert "running" in check.remediation
    assert "ollama" not in check.remediation.lower()
    assert "ollama" not in calls


def test_doctor_openai_compatible_model_missing_lists_reported_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`cfg.backend == "openai-compatible"`, server reachable, `/v1/models`
    reports `a, b, c`, none matching the configured model: remediation
    lists `a, b, c` and advises setting `model:` to one of them; no `ollama
    pull` reference (task 12.9)."""
    layout = _openai_compatible_cfg(tmp_path, monkeypatch, model="configured-model")
    locality = BackendHostLocality(is_local=True, display_host="127.0.0.1:8000")

    results = doctor_service.run_diagnostics(
        layout.root,
        build_client=lambda _cfg, _model: _FakeBackend(
            tags=["a", "b", "c"], locality=locality
        ),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=lambda: True,
    )
    check = _by_label(results, "Model 'configured-model' installed")
    assert check.status == "fail"
    assert check.remediation is not None
    assert "a" in check.remediation
    assert "b" in check.remediation
    assert "c" in check.remediation
    assert "model:" in check.remediation
    assert "ollama pull" not in check.remediation


def test_doctor_llama_cpp_gguf_path_false_alarm_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `/v1/models` response listing a GGUF file path instead of the
    configured model name: the model-missing remediation mentions this
    llama.cpp naming quirk and that the mismatch MAY NOT mean the model is
    genuinely missing -- still `[FAIL]`, not silently passed (task 12.10)."""
    layout = _openai_compatible_cfg(tmp_path, monkeypatch, model="configured-model")
    locality = BackendHostLocality(is_local=True, display_host="127.0.0.1:8000")

    results = doctor_service.run_diagnostics(
        layout.root,
        build_client=lambda _cfg, _model: _FakeBackend(
            tags=["/models/llama-3-8b-instruct.Q4_K_M.gguf"], locality=locality
        ),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=lambda: True,
    )
    check = _by_label(results, "Model 'configured-model' installed")
    assert check.status == "fail"
    assert check.remediation is not None
    assert "GGUF" in check.remediation or "gguf" in check.remediation.lower()
    assert "--alias" in check.remediation
    assert "MAY NOT" in check.remediation or "may not" in check.remediation.lower()


def test_ollama_remediation_bytes_unchanged(tmp_path: Path) -> None:
    """The `ollama` backend's remediation stays byte-identical across the
    existing `shutil.which`-driven scenarios and the model-missing pull
    remediation (task 12.12, frozen-fixture comparison): proves that
    branching on `cfg.backend` for `openai-compatible` moved nothing on the
    default path."""
    import shutil

    layout = _workspace(tmp_path, model="customtag")

    monkeypatch_which_found = pytest.MonkeyPatch()
    monkeypatch_which_found.setattr(shutil, "which", lambda _name: "/usr/bin/ollama")
    try:
        found = doctor_service.run_diagnostics(
            layout.root,
            build_client=lambda _cfg, _model: _FakeBackend(
                error=_CustomBackendUnavailable("connection refused")
            ),
            git_available=True,
            filter_repo_available=True,
            reset_point_available=lambda: False,
        )
    finally:
        monkeypatch_which_found.undo()
    assert _by_label(found, "Ollama reachable").remediation == "ollama serve"

    monkeypatch_which_missing = pytest.MonkeyPatch()
    monkeypatch_which_missing.setattr(shutil, "which", lambda _name: None)
    try:
        missing = doctor_service.run_diagnostics(
            layout.root,
            build_client=lambda _cfg, _model: _FakeBackend(
                error=_CustomBackendUnavailable("connection refused")
            ),
            git_available=True,
            filter_repo_available=True,
            reset_point_available=lambda: False,
        )
    finally:
        monkeypatch_which_missing.undo()
    remediation = _by_label(missing, "Ollama reachable").remediation
    assert remediation is not None
    assert "no `ollama` binary found on PATH" in remediation

    installed = doctor_service.run_diagnostics(
        layout.root,
        build_client=lambda _cfg, _model: _FakeBackend(tags=["other-model"]),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=lambda: False,
    )
    assert (
        _by_label(installed, "Model 'customtag' installed").remediation
        == "ollama pull customtag"
    )


def test_doctor_openai_compatible_embedding_model_missing_no_ollama_pull(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Blanket MODIFIED-requirement text (doctor-command spec): "For the
    openai-compatible backend, doctor MUST NOT probe for or reference
    ollama... ollama pull in any remediation line" -- this applies to the
    embedding-model-installed check (5) too, not only the critical
    model-installed check (4). Beyond tasks.md's explicit 12.1-12.12 list,
    added to satisfy this MUST NOT clause in full."""
    layout = _openai_compatible_cfg(tmp_path, monkeypatch)
    real_cfg = config.read_config(tmp_path)
    oc_cfg = dataclasses.replace(
        real_cfg,
        backend="openai-compatible",
        base_url="http://127.0.0.1:8000",
        embedding_model="configured-embedder",
    )
    monkeypatch.setattr(config, "read_config", lambda _root: oc_cfg)

    results = doctor_service.run_diagnostics(
        layout.root,
        build_client=lambda _cfg, _model: _FakeBackend(tags=["some-other-model"]),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=lambda: True,
    )
    check = _by_label(results, "Embedding model 'configured-embedder' installed")
    assert check.status == "fail"
    assert check.remediation is not None
    assert "ollama pull" not in check.remediation
    assert "configured-embedder" in check.remediation


def test_doctor_openai_compatible_task_models_missing_no_ollama_pull(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same blanket MUST NOT clause, for the task-models-installed check
    (5b) when a `models:` override names a model the configured server does
    not report. Beyond tasks.md's explicit list, added for spec completeness."""
    layout = _openai_compatible_cfg(tmp_path, monkeypatch)
    real_cfg = config.read_config(tmp_path)
    oc_cfg = dataclasses.replace(
        real_cfg,
        backend="openai-compatible",
        base_url="http://127.0.0.1:8000",
        models={"edge_typing": "missing-task-model"},
    )
    monkeypatch.setattr(config, "read_config", lambda _root: oc_cfg)

    results = doctor_service.run_diagnostics(
        layout.root,
        build_client=lambda _cfg, _model: _FakeBackend(tags=[config.DEFAULT_MODEL]),
        git_available=True,
        filter_repo_available=True,
        reset_point_available=lambda: True,
    )
    check = _by_label(results, "Task models installed")
    assert check.status == "fail"
    assert check.remediation is not None
    assert "ollama pull" not in check.remediation
    assert "missing-task-model" in check.remediation
