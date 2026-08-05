"""Unit tests for `openkos curate`'s stage engine (`openkos.cli.curate`) and
its thin Typer command wiring (`cli/main.py`), slice 1 (issue #266).

Follows `test_next.py`'s "engine apart from shell" pattern for the
sequencer itself: fake `Stage` descriptors swapped in for `curate._STAGES`
via `monkeypatch`, no bundle, no CliRunner. CLI-level tests (cost lines,
gates, per-item confirms, exit codes, `--auto`) follow `test_adjudicate.py`'s
`CliRunner` + `input=`/`monkeypatch` conventions.
"""

import os
import sys
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos import config
from openkos.cli import curate, observability
from openkos.cli.main import app
from openkos.llm.base import EMBED_DIM
from openkos.llm.ollama import OllamaError, OllamaModelNotFound, OllamaUnavailable
from openkos.resolution.adjudication import AdjudicatedCandidate, Verdict
from openkos.resolution.candidates import CandidateGroup, Tier
from tests.unit.cli.conftest import changed_paths, disable_local_exemption
from tests.unit.cli.conftest import snapshot_with_mtime as _snapshot
from tests.unit.conftest import LOCAL_BACKEND_LOCALITY
from tests.unit.vcs.conftest import isolate_git_identity

runner = CliRunner()


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _init_apply_workspace(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_init_workspace` plus a real, isolated git identity -- Identity
    auto-commits each accepted merge (mirrors
    `test_adjudicate.py::_init_apply_workspace`)."""
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path_factory.mktemp("git-identity-config")
    isolate_git_identity(
        monkeypatch, config_dir, name="Isolated Tester", email="tester@example.invalid"
    )
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _simulate_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


def _write_doc(path: Path, *, doc_type: str = "Concept", title: str = "Stub") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: {doc_type}\ntitle: {title}\n---\n# {title}\n",
        encoding="utf-8",
    )


class _OfflineOllama:
    locality = LOCAL_BACKEND_LOCALITY
    """Stands in for `OllamaClient.locality` (issue #240): the CLI reads it
    for the embedding-host advisory and the confidential local exemption,
    and a fake without it raises `AttributeError` inside a fail-open
    handler -- a fixture gap that would read as a degrade."""

    def chat(self, messages: object) -> str:
        return '{"extract": false}'

    def embed(self, texts: "list[str]") -> "list[list[float]]":
        return [[1.0] + [0.0] * (EMBED_DIM - 1) for _ in texts]


class _RaisingOllamaClient:
    """A sentinel that raises if constructed at all -- proves no client is
    built when every gate is declined."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("OllamaClient must never be constructed here")


class _FakeConfig:
    def __init__(self, model: str = "stub-model", review: bool = True) -> None:
        self.model = model
        self.review = review


class _FakeLayout:
    """Just enough surface for engine-only tests (fake stages never touch
    it): a `bundle_dir`/`vectors_db_path` pair, matching
    `config.WorkspaceLayout`'s public shape."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.bundle_dir = root / "bundle"
        self.vectors_db_path = root / ".openkos" / "vectors.db"


def _fake_ctx(tmp_path: Path, *, auto: bool = False) -> curate.CurateContext:
    return curate.CurateContext(
        root=tmp_path,
        layout=_FakeLayout(tmp_path),  # type: ignore[arg-type]
        cfg=_FakeConfig(),  # type: ignore[arg-type]
        auto=auto,
    )


def _fake_stage(
    name: str,
    *,
    noun: str = "thing",
    probe: Callable[[curate.CurateContext], curate.StageProbe] | None = None,
    run: Callable[[curate.CurateContext, curate.StageProbe], curate.StageOutcome]
    | None = None,
    needs_llm: bool = True,
    writes: bool = True,
    unattended_hint: str | None = "openkos stub --apply",
    halts_run: bool = False,
    live: bool = True,
) -> curate.Stage:
    def _default_probe(ctx: curate.CurateContext) -> curate.StageProbe:
        return curate.StageProbe()

    def _default_run(
        ctx: curate.CurateContext, probe: curate.StageProbe
    ) -> curate.StageOutcome:
        return curate.StageOutcome(status="applied", applied=1)

    return curate.Stage(
        name=name,
        noun=noun,
        probe=probe or _default_probe,
        run=run or _default_run,
        needs_llm=needs_llm,
        writes=writes,
        unattended_hint=unattended_hint,
        halts_run=halts_run,
        live=live,
    )


class _FakeStdin:
    """A `sys.stdin` stand-in exposing only `isatty()`, for engine-level
    tests that call `curate.gate`/`curate.run_curate` directly (no
    `CliRunner` in play, so patching `_NamedTextIOWrapper` has no effect)."""

    def __init__(self, is_tty: bool) -> None:
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


def _patch_stdin_isatty(monkeypatch: pytest.MonkeyPatch, is_tty: bool) -> None:
    # `curate.py` reads `sys.stdin.isatty()` through its own `import sys`,
    # which is the same module object patched here.
    monkeypatch.setattr(sys, "stdin", _FakeStdin(is_tty))


def _stub_later_stages_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force Structure/Metadata/Contradictions' probes to report an empty
    queue (slice 2). An Identity-only test built before these stages went
    live otherwise picks up a real, unmocked cost-gate prompt for whichever
    of them the seeded bundle happens to have candidates for -- this keeps
    those tests scoped to Identity alone, exactly as originally written."""
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: [])
    monkeypatch.setattr("openkos.cli.curate._concept_type_names", lambda *a, **k: [])
    monkeypatch.setattr(
        "openkos.cli.curate._contradiction_pairs", lambda *a, **k: ([], 0)
    )


# ---------------------------------------------------------------------------
# 1.1/1.2 -- frozen dataclasses exist with design's fields
# ---------------------------------------------------------------------------


def test_stage_probe_has_designed_fields() -> None:
    probe = curate.StageProbe(
        items=(1, 2), llm_calls=2, unavailable=None, empty_message=None
    )
    assert probe.items == (1, 2)
    assert probe.llm_calls == 2
    with pytest.raises(AttributeError):
        probe.items = (3,)  # type: ignore[misc]


def test_stage_outcome_has_designed_fields_including_not_live() -> None:
    outcome = curate.StageOutcome(status="not-live", applied=0, skipped=0, notice="n/a")
    assert outcome.status == "not-live"
    assert outcome.applied == 0
    assert outcome.skipped == 0
    assert outcome.notice == "n/a"


def test_stage_has_designed_fields() -> None:
    stage = _fake_stage("Stub")
    assert stage.name == "Stub"
    assert stage.needs_llm is True
    assert stage.writes is True
    assert stage.halts_run is False
    assert stage.live is True


# ---------------------------------------------------------------------------
# 1.3/1.4 -- cost_line
# ---------------------------------------------------------------------------


def test_cost_line_matches_the_pinned_literal() -> None:
    stage = _fake_stage("Structure", noun="untyped edge")
    probe = curate.StageProbe(items=tuple(range(6)), llm_calls=6)
    assert curate.cost_line(stage, probe) == "6 untyped edge(s) -> 6 LLM call(s)"


# ---------------------------------------------------------------------------
# 1.5/1.6 -- gate() over the D3 table
# ---------------------------------------------------------------------------


def test_gate_tty_auto_is_accepted_without_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_stdin_isatty(monkeypatch, True)
    ctx = _fake_ctx(Path("unused-root"), auto=True)
    stage = _fake_stage("Identity")
    probe = curate.StageProbe(items=(1,), llm_calls=1)
    assert curate.gate(stage, probe, ctx) is True


def test_gate_tty_decline_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_stdin_isatty(monkeypatch, True)
    monkeypatch.setattr("typer.confirm", lambda *a, **k: False)
    ctx = _fake_ctx(Path("unused-root"), auto=False)
    stage = _fake_stage("Identity")
    probe = curate.StageProbe(items=(1,), llm_calls=1)
    assert curate.gate(stage, probe, ctx) is False


def test_gate_non_tty_no_auto_declines(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_stdin_isatty(monkeypatch, False)
    ctx = _fake_ctx(Path("unused-root"), auto=False)
    stage = _fake_stage("Contradictions", writes=False)
    probe = curate.StageProbe(items=(1,), llm_calls=1)
    assert curate.gate(stage, probe, ctx) is False


def test_gate_non_tty_auto_writes_false_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_stdin_isatty(monkeypatch, False)
    ctx = _fake_ctx(Path("unused-root"), auto=True)
    stage = _fake_stage("Contradictions", writes=False)
    probe = curate.StageProbe(items=(1,), llm_calls=1)
    assert curate.gate(stage, probe, ctx) is True


def test_gate_non_tty_auto_writes_true_is_accepted_by_gate_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`gate()` itself only decides SPEND consent -- the write-consent
    decline is `run_curate`'s job (design D3 rule 2), so `gate` alone
    returns `True` here too."""
    _patch_stdin_isatty(monkeypatch, False)
    ctx = _fake_ctx(Path("unused-root"), auto=True)
    stage = _fake_stage("Identity", writes=True)
    probe = curate.StageProbe(items=(1,), llm_calls=1)
    assert curate.gate(stage, probe, ctx) is True


# ---------------------------------------------------------------------------
# StageProbe.notice (#378 slice 2): Structure's candidate-edge cap notice
# ---------------------------------------------------------------------------


def test_gate_does_not_echo_the_probe_notice(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate prints the cost line and nothing else. `run_curate` owns the
    notice, so it survives the branches that never reach the gate (#378
    correction)."""
    _patch_stdin_isatty(monkeypatch, True)
    ctx = _fake_ctx(Path("unused-root"), auto=True)
    stage = _fake_stage("Structure", noun="untyped edge")
    probe = curate.StageProbe(
        items=(1,), llm_calls=1, notice="50 of 60 candidate edge(s) shown (cap reached)"
    )

    assert curate.gate(stage, probe, ctx) is True

    err = capsys.readouterr().err
    assert "cap reached" not in err
    assert "1 untyped edge(s) -> 1 LLM call(s)" in err


def test_run_curate_reports_truncation_even_when_the_queue_is_empty(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The branch the review caught: the cap truncated the candidate set, but
    every survivor was then filtered out downstream, so the stage takes the
    empty-queue exit and never reaches the gate. The reader must still be
    told the set was truncated -- otherwise "truncation is never silent"
    holds only on the path that asks to spend (#378 correction)."""
    _patch_stdin_isatty(monkeypatch, False)
    notice = "50 of 60 candidate edge(s) shown (cap reached)"

    def _probe(ctx: curate.CurateContext) -> curate.StageProbe:
        return curate.StageProbe(
            items=(),
            llm_calls=0,
            empty_message="Structure: nothing to do.",
            notice=notice,
        )

    monkeypatch.setattr(
        curate,
        "_STAGES",
        (_fake_stage("Structure", probe=_probe, noun="untyped edge"),),
    )

    outcomes = curate.run_curate(_fake_ctx(Path("unused-root"), auto=True))

    assert [o.status for o in outcomes] == ["empty"]
    err = capsys.readouterr().err
    assert notice in err
    # The gate never ran, so no spend was previewed -- the notice reached the
    # reader on its own, not as a side effect of the cost line.
    assert "LLM call(s)" not in err


def test_run_curate_prints_nothing_extra_when_the_probe_has_no_notice(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_stdin_isatty(monkeypatch, False)

    def _probe(ctx: curate.CurateContext) -> curate.StageProbe:
        return curate.StageProbe(
            items=(), llm_calls=0, empty_message="Structure: nothing to do."
        )

    monkeypatch.setattr(
        curate,
        "_STAGES",
        (_fake_stage("Structure", probe=_probe, noun="untyped edge"),),
    )

    curate.run_curate(_fake_ctx(Path("unused-root"), auto=True))

    assert "cap reached" not in capsys.readouterr().err


class _FakeCandidateStore:
    def __init__(self, candidate_report: object) -> None:
        self.candidate_report = candidate_report

    def __enter__(self) -> "_FakeCandidateStore":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_structure_probe_notice_set_when_the_candidate_cap_truncates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openkos.graph.sqlite_graph import CandidateReport

    ctx = _fake_ctx(tmp_path)
    pairs = tuple((f"concepts/a{i:03d}", f"concepts/b{i:03d}") for i in range(1, 61))
    monkeypatch.setattr(
        "openkos.cli.curate.build_graph",
        lambda *a, **k: _FakeCandidateStore(
            CandidateReport(produced=60, retained=50, pairs=pairs)
        ),
    )
    monkeypatch.setattr("openkos.cli.main._open_proximity_or_degrade", lambda p: None)
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: [])

    probe = curate._structure_probe(ctx)

    assert probe.notice == "50 of 60 candidate edge(s) shown (cap reached)"


def test_structure_probe_notice_is_none_under_the_candidate_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openkos.graph.sqlite_graph import CandidateReport

    ctx = _fake_ctx(tmp_path)
    monkeypatch.setattr(
        "openkos.cli.curate.build_graph",
        lambda *a, **k: _FakeCandidateStore(
            CandidateReport(
                produced=1, retained=1, pairs=(("concepts/a", "concepts/b"),)
            )
        ),
    )
    monkeypatch.setattr("openkos.cli.main._open_proximity_or_degrade", lambda p: None)
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: [])

    probe = curate._structure_probe(ctx)

    assert probe.notice is None


def test_structure_probe_notice_suppressed_when_every_dropped_pair_is_confidential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#378 post-review correction: `curate`'s Structure gate must observe
    the SAME sensitivity-restricted notice as `suggest-relations` and
    `contradictions` -- a truncated run whose dropped pairs are all
    confidential must stay silent."""
    from openkos.graph.sqlite_graph import CandidateReport

    ctx = _fake_ctx(tmp_path)
    (tmp_path / "bundle" / "concepts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "bundle" / "concepts" / "keep.md").write_text(
        "---\ntype: Concept\ntitle: keep\nsensitivity: private\n---\nBody.\n",
        encoding="utf-8",
    )
    (tmp_path / "bundle" / "concepts" / "secret.md").write_text(
        "---\ntype: Concept\ntitle: secret\nsensitivity: confidential\n---\nBody.\n",
        encoding="utf-8",
    )
    report = CandidateReport(
        produced=2,
        retained=1,
        pairs=(
            ("concepts/hub", "concepts/keep"),
            ("concepts/hub", "concepts/secret"),
        ),
    )
    monkeypatch.setattr(
        "openkos.cli.curate.build_graph",
        lambda *a, **k: _FakeCandidateStore(report),
    )
    monkeypatch.setattr("openkos.cli.main._open_proximity_or_degrade", lambda p: None)
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: [])

    probe = curate._structure_probe(ctx)

    assert probe.notice is None


# ---------------------------------------------------------------------------
# 1.7 -- stage order
# ---------------------------------------------------------------------------


def test_full_run_visits_stages_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_stdin_isatty(monkeypatch, True)
    visited: list[str] = []

    def _make(name: str) -> curate.Stage:
        def _probe(ctx: curate.CurateContext) -> curate.StageProbe:
            visited.append(name)
            return curate.StageProbe(items=(1,), llm_calls=1)

        def _run(
            ctx: curate.CurateContext, probe: curate.StageProbe
        ) -> curate.StageOutcome:
            return curate.StageOutcome(status="applied", applied=1)

        return _fake_stage(name, probe=_probe, run=_run)

    fake_stages = tuple(
        _make(name)
        for name in (
            "Preconditions",
            "Identity",
            "Structure",
            "Metadata",
            "Contradictions",
        )
    )
    monkeypatch.setattr(curate, "_STAGES", fake_stages)
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)

    ctx = _fake_ctx(Path("unused-root"), auto=True)
    curate.run_curate(ctx)

    assert visited == [
        "Preconditions",
        "Identity",
        "Structure",
        "Metadata",
        "Contradictions",
    ]


# ---------------------------------------------------------------------------
# 1.8 -- a decline does not abort later stages
# ---------------------------------------------------------------------------


def test_declined_stage_does_not_abort_later_stages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_stdin_isatty(monkeypatch, False)
    visited: list[str] = []

    def _make(name: str) -> curate.Stage:
        def _probe(ctx: curate.CurateContext) -> curate.StageProbe:
            visited.append(name)
            return curate.StageProbe(items=(1,), llm_calls=1)

        return _fake_stage(name, probe=_probe)

    fake_stages = (_make("Structure"), _make("Metadata"))
    monkeypatch.setattr(curate, "_STAGES", fake_stages)
    ctx = _fake_ctx(
        Path("unused-root"), auto=False
    )  # non-TTY, no --auto -> every gate declines

    outcomes = curate.run_curate(ctx)

    assert visited == ["Structure", "Metadata"]
    assert [o.status for o in outcomes] == ["declined", "declined"]


# ---------------------------------------------------------------------------
# 1.9 -- no cached state between iterations
# ---------------------------------------------------------------------------


def test_sequencer_re_derives_each_stage_fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stage's probe reflects state a PRIOR stage's `run` just wrote --
    proves `run_curate` never memoizes anything across stages (design D4)."""
    _patch_stdin_isatty(monkeypatch, True)
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)
    shared_state = {"count": 1}

    def _first_run(
        ctx: curate.CurateContext, probe: curate.StageProbe
    ) -> curate.StageOutcome:
        shared_state["count"] = 0
        return curate.StageOutcome(status="applied", applied=1)

    def _second_probe(ctx: curate.CurateContext) -> curate.StageProbe:
        return curate.StageProbe(
            items=tuple(range(shared_state["count"])), llm_calls=shared_state["count"]
        )

    first = _fake_stage(
        "First",
        probe=lambda ctx: curate.StageProbe(items=(1,), llm_calls=1),
        run=_first_run,
        writes=False,
    )
    second = _fake_stage("Second", probe=_second_probe, writes=False)
    monkeypatch.setattr(curate, "_STAGES", (first, second))

    ctx = _fake_ctx(Path("unused-root"), auto=True)
    outcomes = curate.run_curate(ctx)

    assert outcomes[1].status == "empty"


# ---------------------------------------------------------------------------
# 1.10/1.11 -- live=False stage: probe never called, still in summary
# ---------------------------------------------------------------------------


def test_not_live_stage_probe_is_never_called(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raising_probe(ctx: curate.CurateContext) -> curate.StageProbe:
        raise AssertionError("must not be called")

    stage = _fake_stage("Structure", probe=_raising_probe, live=False)
    monkeypatch.setattr(curate, "_STAGES", (stage,))

    ctx = _fake_ctx(Path("unused-root"))
    outcomes = curate.run_curate(ctx)

    assert outcomes == [
        curate.StageOutcome(
            status="not-live", notice="Structure: not yet available in this version"
        )
    ]


def test_not_live_stage_appears_in_summary() -> None:
    # render_summary zips against the real `_STAGES`, so exercise it directly:
    # every not-live outcome's notice must survive into its summary line
    # (index 2 is Structure, the first not-live stage in D1 order).
    lines = curate.render_summary(
        [
            curate.StageOutcome(
                status="not-live",
                notice=f"{s.name}: not yet available in this version",
            )
            for s in curate._STAGES
        ]
    )
    assert "not yet available in this version" in lines[2]


# ---------------------------------------------------------------------------
# 1.12/1.13 -- render_summary always returns 5 entries
# ---------------------------------------------------------------------------


def test_render_summary_always_returns_five_entries_for_the_real_stages() -> None:
    outcomes = [curate.StageOutcome(status="empty") for _ in curate._STAGES]
    lines = curate.render_summary(outcomes)
    assert len(lines) == 5


def test_render_summary_with_fake_stages_matches_outcome_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_stages = (_fake_stage("A"), _fake_stage("B"), _fake_stage("C"))
    monkeypatch.setattr(curate, "_STAGES", fake_stages)
    outcomes = [curate.StageOutcome(status="empty") for _ in fake_stages]
    assert len(curate.render_summary(outcomes)) == 3


# ---------------------------------------------------------------------------
# 1.14/1.15 -- lazy OllamaClient, short-circuit, generic failure
# ---------------------------------------------------------------------------


def test_no_ollama_client_built_when_every_gate_is_declined(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: False)
    monkeypatch.setattr("openkos.cli.curate.OllamaClient", _RaisingOllamaClient)
    stage = _fake_stage(
        "Identity", probe=lambda ctx: curate.StageProbe(items=(1,), llm_calls=1)
    )
    monkeypatch.setattr(curate, "_STAGES", (stage,))

    ctx = _fake_ctx(Path("unused-root"), auto=False)
    curate.run_curate(ctx)

    assert ctx.ollama_client is None


def test_ollama_unavailable_short_circuits_later_needs_llm_stages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_stdin_isatty(monkeypatch, True)
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)
    calls: list[str] = []

    def _failing_run(
        ctx: curate.CurateContext, probe: curate.StageProbe
    ) -> curate.StageOutcome:
        calls.append("First")
        raise OllamaUnavailable("connection refused")

    def _second_run(
        ctx: curate.CurateContext, probe: curate.StageProbe
    ) -> curate.StageOutcome:
        calls.append("Second")
        return curate.StageOutcome(status="applied", applied=1)

    first = _fake_stage(
        "First",
        probe=lambda ctx: curate.StageProbe(items=(1,), llm_calls=1),
        run=_failing_run,
        writes=False,
    )
    second = _fake_stage(
        "Second",
        probe=lambda ctx: curate.StageProbe(items=(1,), llm_calls=1),
        run=_second_run,
        writes=False,
    )
    monkeypatch.setattr(curate, "_STAGES", (first, second))

    ctx = _fake_ctx(Path("unused-root"), auto=True)
    outcomes = curate.run_curate(ctx)

    assert calls == ["First"]  # `_second_run` never even attempted
    assert outcomes[0].status == "unavailable"
    assert outcomes[1].status == "unavailable"
    assert "see above" in (outcomes[1].notice or "")


def test_ollama_model_not_found_also_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_stdin_isatty(monkeypatch, True)
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)

    def _failing_run(
        ctx: curate.CurateContext, probe: curate.StageProbe
    ) -> curate.StageOutcome:
        raise OllamaModelNotFound("model missing")

    stage = _fake_stage(
        "First",
        probe=lambda ctx: curate.StageProbe(items=(1,), llm_calls=1),
        run=_failing_run,
        writes=False,
    )
    monkeypatch.setattr(curate, "_STAGES", (stage,))

    ctx = _fake_ctx(Path("unused-root"), auto=True)
    curate.run_curate(ctx)

    assert ctx.ollama_unavailable_notice is not None


def test_generic_ollama_error_fails_only_that_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_stdin_isatty(monkeypatch, True)
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)
    calls: list[str] = []

    def _failing_run(
        ctx: curate.CurateContext, probe: curate.StageProbe
    ) -> curate.StageOutcome:
        calls.append("First")
        raise OllamaError("weird response")

    def _second_run(
        ctx: curate.CurateContext, probe: curate.StageProbe
    ) -> curate.StageOutcome:
        calls.append("Second")
        return curate.StageOutcome(status="applied", applied=1)

    first = _fake_stage(
        "First",
        probe=lambda ctx: curate.StageProbe(items=(1,), llm_calls=1),
        run=_failing_run,
        writes=False,
    )
    second = _fake_stage(
        "Second",
        probe=lambda ctx: curate.StageProbe(items=(1,), llm_calls=1),
        run=_second_run,
        writes=False,
    )
    monkeypatch.setattr(curate, "_STAGES", (first, second))

    ctx = _fake_ctx(Path("unused-root"), auto=True)
    outcomes = curate.run_curate(ctx)

    assert calls == ["First", "Second"]
    assert outcomes[0].status == "failed"
    assert outcomes[1].status == "applied"
    assert ctx.ollama_unavailable_notice is None


# ---------------------------------------------------------------------------
# 1.16/1.17 -- Preconditions halts the run
# ---------------------------------------------------------------------------


def test_missing_vectors_db_halts_before_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["curate"])

    assert result.exit_code == 0
    assert "vector index is missing or empty" in result.stdout
    assert "openkos reindex" in result.stdout
    assert "Identity: applied" not in result.stdout


def test_preconditions_probe_reports_unavailable_when_vectors_db_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    ctx = curate.CurateContext(
        root=tmp_path,
        layout=config.WorkspaceLayout(tmp_path),
        cfg=config.read_config(tmp_path),
    )
    probe = curate._preconditions_probe(ctx)
    assert probe.unavailable is not None
    assert "openkos reindex" in probe.unavailable


# ---------------------------------------------------------------------------
# 1.18 -- accepted Identity pair commits per-item
# ---------------------------------------------------------------------------


def _reindexed_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`init` then a real `reindex` so `vectors.db` is present and non-empty
    -- required for Preconditions to pass."""
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient", lambda *a, **k: _OfflineOllama()
    )
    result = runner.invoke(app, ["reindex"])
    assert result.exit_code == 0


def test_accepted_identity_pair_commits_via_shared_merge_cores(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_later_stages_empty(monkeypatch)
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _reindexed_workspace(tmp_path, monkeypatch)

    group = CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/a", "concepts/b"),
        tier=Tier.HIGH,
        trigger="stub",
    )
    monkeypatch.setattr("openkos.cli.curate.find_candidates", lambda *a, **k: [group])
    monkeypatch.setattr(
        "openkos.cli.curate.adjudicate_candidates",
        lambda *a, **k: [
            AdjudicatedCandidate(
                candidate=group, verdict=Verdict.SAME, confidence=0.9, rationale="same"
            )
        ],
    )
    _simulate_tty(monkeypatch)

    # Two stdin answers: the Identity cost gate's `typer.confirm` consumes
    # the first "y", the per-pair `[y/N/skip]` `typer.prompt` the second.
    result = runner.invoke(app, ["curate"], input="y\ny\n")

    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / "concepts" / "b.md").exists()
    assert "Identity: applied 1, skipped 0." in result.stdout


# ---------------------------------------------------------------------------
# 1.19 -- N>2 group prints pairwise commands, no merge
# ---------------------------------------------------------------------------


def test_identity_n_gt2_group_prints_pairwise_commands_no_merge(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_later_stages_empty(monkeypatch)
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    for name in ("a", "b", "c"):
        _write_doc(tmp_path / "bundle" / "concepts" / f"{name}.md", title=name.upper())
    _reindexed_workspace(tmp_path, monkeypatch)

    group = CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/a", "concepts/b", "concepts/c"),
        tier=Tier.HIGH,
        trigger="stub",
    )
    monkeypatch.setattr("openkos.cli.curate.find_candidates", lambda *a, **k: [group])
    monkeypatch.setattr(
        "openkos.cli.curate.adjudicate_candidates",
        lambda *a, **k: [
            AdjudicatedCandidate(
                candidate=group, verdict=Verdict.SAME, confidence=0.9, rationale="same"
            )
        ],
    )
    _simulate_tty(monkeypatch)

    # One stdin answer: the cost gate's `typer.confirm` consumes it; an N>2
    # group never reaches the per-pair prompt, so no second answer exists.
    result = runner.invoke(app, ["curate"], input="y\n")

    assert result.exit_code == 0
    assert "skipped (N>2, merge manually)" in result.stdout
    assert "openkos merge concepts/a concepts/b" in result.stdout
    assert "openkos merge concepts/a concepts/c" in result.stdout
    assert (tmp_path / "bundle" / "concepts" / "b.md").exists()
    assert (tmp_path / "bundle" / "concepts" / "c.md").exists()


# ---------------------------------------------------------------------------
# 1.20/1.21 -- TOCTOU drift guard exits 3, nothing written
# ---------------------------------------------------------------------------


def test_identity_toctou_drift_exits_three_nothing_written(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _reindexed_workspace(tmp_path, monkeypatch)

    group = CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/a", "concepts/b"),
        tier=Tier.HIGH,
        trigger="stub",
    )
    monkeypatch.setattr("openkos.cli.curate.find_candidates", lambda *a, **k: [group])
    monkeypatch.setattr(
        "openkos.cli.curate.adjudicate_candidates",
        lambda *a, **k: [
            AdjudicatedCandidate(
                candidate=group, verdict=Verdict.SAME, confidence=0.9, rationale="same"
            )
        ],
    )
    _simulate_tty(monkeypatch)

    survivor_path = tmp_path / "bundle" / "concepts" / "a.md"
    concurrent = "hand-edited while the prompt waited\n"
    before = _snapshot(tmp_path)

    # The TOCTOU window under test opens AFTER `_prepare_one_merge`'s
    # snapshot and closes at `_merge_drift_targets` -- in curate's Identity
    # walk that window is the per-pair `[y/N/skip]` `typer.prompt`, not the
    # cost gate's `typer.confirm` (which fires BEFORE the snapshot, where an
    # edit would be baked into the baseline and drift undetectable). So the
    # edit is injected from a `typer.prompt` stub, `confirm_after`-style,
    # and the cost gate is answered through stdin instead.
    def _prompt_edits_then_accepts(*args: object, **kwargs: object) -> str:
        survivor_path.write_text(concurrent, encoding="utf-8")
        return "y"

    monkeypatch.setattr("typer.prompt", _prompt_edits_then_accepts)

    result = runner.invoke(app, ["curate"], input="y\n")

    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert "refusing to write --" in result.stderr
    after = _snapshot(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {Path("bundle/concepts/a.md")}
    assert survivor_path.read_text(encoding="utf-8") == concurrent


def test_identity_non_tty_auto_declines_write_walk_with_hint(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_later_stages_empty(monkeypatch)
    """D3 rule 2 at the sequencer level, with a REAL non-empty queue: on a
    non-TTY, `--auto` consents to model spend but never to a per-item write,
    so a `writes=True` stage whose gate `--auto`-accepted must decline its
    walk BEFORE `stage.run` -- no adjudication, no `OllamaClient`
    construction, no prompt, no write -- and print the `unattended_hint`
    pointing at the standalone batch verb."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _reindexed_workspace(tmp_path, monkeypatch)

    group = CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/a", "concepts/b"),
        tier=Tier.HIGH,
        trigger="stub",
    )
    monkeypatch.setattr("openkos.cli.curate.find_candidates", lambda *a, **k: [group])
    # The write-decline must fire BEFORE the client is ever built: a
    # constructed client here means model spend leaked past the consent
    # boundary (the sentinel raises on construction).
    monkeypatch.setattr("openkos.cli.curate.OllamaClient", _RaisingOllamaClient)
    # No `_simulate_tty`: CliRunner's stdin is the non-TTY side of the D3
    # matrix under test.

    before = _snapshot(tmp_path)
    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0
    assert "openkos adjudicate --apply-same --confirm-count" in result.stdout
    assert "non-interactive write consent unavailable" in result.stdout
    assert changed_paths(before, _snapshot(tmp_path)) == set()


def test_identity_confidential_member_never_reaches_the_llm_payload(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_later_stages_empty(monkeypatch)
    """Sensitivity threading end-to-end (fail-closed, S3a): a confidential
    member of a REAL candidate group is dropped by the UNPATCHED
    `adjudicate_candidates` before its content is ever read, so its body
    never appears in any `llm.chat` payload and nothing is merged. Only the
    transport (`OllamaClient`) is faked -- candidate discovery, member
    filtering, and the sequencer all run for real."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "alpha.md", title="Same Title")
    secret_path = tmp_path / "bundle" / "concepts" / "beta.md"
    secret_path.write_text(
        "---\ntype: Concept\ntitle: Same Title\nsensitivity: confidential\n---\n"
        "# Same Title\nTOP-SECRET-BODY\n",
        encoding="utf-8",
    )
    _reindexed_workspace(tmp_path, monkeypatch)

    payloads: list[str] = []

    class _RecordingOllama:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def chat(self, messages: object) -> str:
            payloads.append(str(messages))
            return '{"verdict": "UNCERTAIN", "confidence": 0.4, "rationale": "stub"}'

    monkeypatch.setattr("openkos.cli.curate.OllamaClient", _RecordingOllama)
    _simulate_tty(monkeypatch)
    # `curate` resolves the confidential local exemption from its own
    # client, and the suite's stand-in reports a LOCAL backend -- where #240
    # legitimately includes confidential content. This test is about the
    # fail-closed FILTER, so opt out through the same workspace switch a
    # user would use.
    disable_local_exemption(tmp_path)

    before = _snapshot(tmp_path)
    # One stdin answer accepts the Identity cost gate; the UNCERTAIN verdict
    # never reaches the per-pair prompt, so no second answer exists.
    result = runner.invoke(app, ["curate"], input="y\n")

    assert result.exit_code == 0
    assert all("TOP-SECRET-BODY" not in payload for payload in payloads)
    assert secret_path.exists()
    assert changed_paths(before, _snapshot(tmp_path)) == set()


# ---------------------------------------------------------------------------
# 1.22/1.23 -- Identity probe/run wiring, all five stages declared
# ---------------------------------------------------------------------------


def test_all_five_stages_declared_in_d1_order() -> None:
    names = [stage.name for stage in curate._STAGES]
    assert names == [
        "Preconditions",
        "Identity",
        "Structure",
        "Metadata",
        "Contradictions",
    ]


def test_identity_probe_reads_find_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    group = CandidateGroup(
        okf_type="Concept", member_ids=("a", "b"), tier=Tier.HIGH, trigger="stub"
    )
    monkeypatch.setattr("openkos.cli.curate.find_candidates", lambda *a, **k: [group])
    ctx = curate.CurateContext(
        root=tmp_path,
        layout=config.WorkspaceLayout(tmp_path),
        cfg=config.read_config(tmp_path),
    )
    probe = curate._identity_probe(ctx)
    assert probe.items == (group,)
    assert probe.llm_calls == 1


# ---------------------------------------------------------------------------
# 1.24/1.25 -- CLI command wiring
# ---------------------------------------------------------------------------


def test_curate_forwards_flags_into_context(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _reindexed_workspace(tmp_path, monkeypatch)

    captured: dict[str, curate.CurateContext] = {}
    real_run_curate = curate.run_curate

    def _capturing_run_curate(ctx: curate.CurateContext) -> list[curate.StageOutcome]:
        captured["ctx"] = ctx
        return real_run_curate(ctx)

    monkeypatch.setattr(
        "openkos.cli.main.curate_module.run_curate", _capturing_run_curate
    )

    result = runner.invoke(
        app,
        ["curate", "--auto", "--include-confidential", "--include-deprecated"],
    )

    assert result.exit_code == 0
    assert captured["ctx"].auto is True
    assert captured["ctx"].include_confidential is True
    assert captured["ctx"].include_deprecated is True


def test_curate_refuses_outside_a_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["curate"])
    assert result.exit_code == 1
    assert "openkos curate: refusing to run --" in result.stderr


def test_curate_exits_two_on_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    result = runner.invoke(app, ["curate", "--no-such-flag"])
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# 1.26 -- NO_COLOR + piped stdout, no ANSI/prompts
# ---------------------------------------------------------------------------


def test_piped_no_color_run_has_no_ansi_or_prompts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0
    assert "\x1b[" not in result.stdout
    assert "\x1b[" not in result.stderr


# ---------------------------------------------------------------------------
# 1.27 -- summary names every stage outcome
# ---------------------------------------------------------------------------


def test_end_of_run_summary_names_all_five_stages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["curate"])

    assert result.exit_code == 0
    for name in (
        "Preconditions",
        "Identity",
        "Structure",
        "Metadata",
        "Contradictions",
    ):
        assert f"{name}:" in result.stdout


# ---------------------------------------------------------------------------
# 1.28 -- warn_if_walk_incomplete fires once per run
# ---------------------------------------------------------------------------


def test_warn_if_walk_incomplete_fires_once_per_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    calls: list[object] = []
    real_warn = observability.warn_if_walk_incomplete

    def _spy(*args: object, **kwargs: object) -> None:
        calls.append((args, kwargs))
        real_warn(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("openkos.cli.main.observability.warn_if_walk_incomplete", _spy)

    result = runner.invoke(app, ["curate"])

    assert result.exit_code == 0
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# #356 -- the incomplete-inputs advisory, which no hatch suppresses
# ---------------------------------------------------------------------------


def _break_os_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force `okf._walk_errors` to report exactly one directory-scan error,
    deterministically -- mirrors `tests/unit/model/test_okf.py`'s onerror
    monkeypatch pattern, without relying on real `chmod` bits (the same
    helper the other five sensitivity-filter verbs' modules already
    define)."""
    original_walk = os.walk
    walk_error = OSError(13, "Permission denied", "locked")

    def fake_walk(
        top: str | os.PathLike[str],
        topdown: bool = True,
        onerror: Callable[[OSError], object] | None = None,
        followlinks: bool = False,
    ) -> Iterator[tuple[str, list[str], list[str]]]:
        if onerror is not None:
            onerror(walk_error)
        yield from original_walk(top, topdown, onerror, followlinks)

    monkeypatch.setattr(os, "walk", fake_walk)


def test_curate_local_exemption_keeps_the_general_advisory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a STOCK workspace -- default local backend, so the confidential
    local exemption applies (#240) -- an incomplete walk still prints the
    #356 incomplete-inputs advisory, and the filter-scoped one stays
    suppressed. The run exits 0: this is a signal, never a refusal.

    `curate` is the verb where the gap was widest, because it fans one walk
    out over five stages: Identity's candidates, Structure's untyped edges,
    Metadata's per-type sampling and its missing-`sensitivity` report, and
    Contradictions' related pairs all shrink together when a subtree cannot
    be listed -- and every one of them still reports a confident-looking
    zero."""
    _init_workspace(tmp_path, monkeypatch)
    _break_os_walk(monkeypatch)

    result = runner.invoke(app, ["curate"])

    assert result.exit_code == 0
    assert "this command's inputs are incomplete" in result.stderr
    assert "confidential-content filter" not in result.stderr


def test_curate_include_confidential_keeps_the_general_advisory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--include-confidential` suppresses the FILTER-SCOPED advisory only;
    the #356 one still prints and the run still exits 0.

    The exemption is disabled first so the FLAG is what discriminates: on a
    stock workspace the exemption would suppress the filter-scoped message
    on its own, and this test would keep passing even if
    `--include-confidential` were dropped from `curate`'s single
    `warn_if_walk_incomplete` call."""
    _init_workspace(tmp_path, monkeypatch)
    disable_local_exemption(tmp_path)
    _break_os_walk(monkeypatch)

    result = runner.invoke(app, ["curate", "--include-confidential"])

    assert result.exit_code == 0
    assert "this command's inputs are incomplete" in result.stderr
    assert "confidential-content filter" not in result.stderr


def test_curate_emits_both_advisories_when_no_hatch_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the exemption disabled and no flag, BOTH advisories print: the
    inputs are incomplete AND the confidential filter could not inspect
    every document. Each is asserted through text unique to it, since the
    two now share the `bundle scan was incomplete` opening."""
    _init_workspace(tmp_path, monkeypatch)
    disable_local_exemption(tmp_path)
    _break_os_walk(monkeypatch)

    result = runner.invoke(app, ["curate"])

    assert result.exit_code == 0
    assert "this command's inputs are incomplete" in result.stderr
    assert "confidential-content filter" in result.stderr


def test_curate_no_advisory_on_a_clean_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fully readable bundle produces NEITHER advisory -- the walk coming
    back empty is the only thing that silences the #356 one, so a stock
    `curate` over a healthy workspace stays exactly as quiet as before."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["curate"])

    assert result.exit_code == 0
    assert "this command's inputs are incomplete" not in result.stderr
    assert "confidential-content filter" not in result.stderr


# ===========================================================================
# Slice 2 (issue #266, PR2): relate/set-volatility core extraction,
# Structure, Metadata, Contradictions made live.
# ===========================================================================

# ---------------------------------------------------------------------------
# 2.1-2.6 -- prepare_relate / relate_core (design D5)
# ---------------------------------------------------------------------------


def test_prepare_relate_returns_snapshot_baseline_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "a.md", title="A")
    _write_doc(tmp_path / "bundle" / "b.md", title="B")
    before = _snapshot(tmp_path)

    from openkos.cli.main import PreparedRelate, prepare_relate

    prepared = prepare_relate(
        tmp_path / "bundle" / "a.md",
        tmp_path / "bundle" / "log.md",
        "a",
        "b",
        "references",
        tmp_path,
        now=datetime.now(UTC),
    )

    assert isinstance(prepared, PreparedRelate)
    assert prepared.already_present is False
    assert "references" in prepared.new_source_text
    assert changed_paths(before, _snapshot(tmp_path)) == set()


def test_relate_core_performs_only_write_atomic_twice_and_propagates_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "a.md", title="A")

    from openkos.cli import main as cli_main

    source_path = tmp_path / "bundle" / "a.md"
    log_path = tmp_path / "bundle" / "log.md"
    prepared = cli_main.prepare_relate(
        source_path, log_path, "a", "b", "references", tmp_path, now=datetime.now(UTC)
    )

    calls: list[Path] = []
    from openkos import fsio as fsio_module

    real_write_atomic = fsio_module.write_atomic

    def _spy(path: Path, text: str) -> None:
        calls.append(path)
        real_write_atomic(path, text)

    monkeypatch.setattr(fsio_module, "write_atomic", _spy)
    cli_main.relate_core(source_path, log_path, prepared)
    assert calls == [source_path, log_path]

    def _raise(path: Path, text: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(fsio_module, "write_atomic", _raise)
    with pytest.raises(OSError, match="disk full"):
        cli_main.relate_core(source_path, log_path, prepared)


def test_relate_test_suite_regression_unedited() -> None:
    """Documents the D5 gate (task 2.6): `test_relate.py` is run UNEDITED
    as part of the regression command (task 2.23); there is nothing to
    assert here beyond the file's own existence, which pytest already
    verifies by collecting it."""
    import tests.unit.cli.test_relate as test_relate_module

    assert test_relate_module is not None


# ---------------------------------------------------------------------------
# 2.7-2.12 -- prepare_set_volatility / set_volatility_core (design D5)
# ---------------------------------------------------------------------------


def test_prepare_set_volatility_returns_snapshot_baseline_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)

    from openkos.cli.main import PreparedSetVolatility, prepare_set_volatility

    layout = config.WorkspaceLayout(tmp_path)
    prepared = prepare_set_volatility(layout.config_path, "Person", "volatile")

    assert isinstance(prepared, PreparedSetVolatility)
    assert "volatile" in prepared.new_config_text
    assert changed_paths(before, _snapshot(tmp_path)) == set()


def test_set_volatility_core_performs_only_one_write_atomic_and_propagates_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    from openkos.cli import main as cli_main

    layout = config.WorkspaceLayout(tmp_path)
    prepared = cli_main.prepare_set_volatility(layout.config_path, "Person", "volatile")

    calls: list[Path] = []
    from openkos import fsio as fsio_module

    real_write_atomic = fsio_module.write_atomic

    def _spy(path: Path, text: str) -> None:
        calls.append(path)
        real_write_atomic(path, text)

    monkeypatch.setattr(fsio_module, "write_atomic", _spy)
    cli_main.set_volatility_core(layout.config_path, prepared)
    assert calls == [layout.config_path]

    def _raise(path: Path, text: str) -> None:
        raise ValueError("bad shape")

    monkeypatch.setattr(fsio_module, "write_atomic", _raise)
    with pytest.raises(ValueError, match="bad shape"):
        cli_main.set_volatility_core(layout.config_path, prepared)


def test_set_volatility_test_suite_regression_unedited() -> None:
    """Documents the D5 gate (task 2.12): `test_set_volatility.py` is run
    UNEDITED as part of the regression command (task 2.23)."""
    import tests.unit.cli.test_set_volatility as test_set_volatility_module

    assert test_set_volatility_module is not None


# ---------------------------------------------------------------------------
# 2.13-2.15 -- Structure stage (candidate_edges probe, suggest_edge_types run)
# ---------------------------------------------------------------------------


def test_structure_accepted_suggestion_writes_via_extracted_relate_core(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _reindexed_workspace(tmp_path, monkeypatch)

    from openkos.graph.base import Edge
    from openkos.resolution.edge_typing import EdgeSuggestion

    edge = Edge(source_id="concepts/a", target_id="concepts/b", relation_type=None)
    monkeypatch.setattr("openkos.cli.curate.find_candidates", lambda *a, **k: [])
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: [edge])
    monkeypatch.setattr("openkos.cli.curate._concept_type_names", lambda *a, **k: [])
    monkeypatch.setattr(
        "openkos.cli.curate._contradiction_pairs", lambda *a, **k: ([], 0)
    )

    def _fake_suggest(edges: object, **kwargs: object) -> list[EdgeSuggestion]:
        on_progress = kwargs.get("on_progress")
        suggestion = EdgeSuggestion(
            edge=edge, suggested_type="references", rationale="stub rationale"
        )
        if on_progress is not None:
            on_progress(1, 1, suggestion)  # type: ignore[operator]
        return [suggestion]

    monkeypatch.setattr("openkos.cli.curate.suggest_edge_types", _fake_suggest)
    _simulate_tty(monkeypatch)

    # Identity's queue is empty (no prompt). Structure's cost gate consumes
    # one "y"; the per-suggestion [y/N/skip] prompt consumes a second "y".
    result = runner.invoke(app, ["curate"], input="y\ny\n")

    assert result.exit_code == 0
    source_text = (tmp_path / "bundle" / "concepts" / "a.md").read_text(
        encoding="utf-8"
    )
    assert "references" in source_text
    assert "concepts/b" in source_text
    assert "Structure: applied 1, skipped 0." in result.stdout


def test_structure_declined_suggestion_writes_nothing(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _reindexed_workspace(tmp_path, monkeypatch)

    from openkos.graph.base import Edge
    from openkos.resolution.edge_typing import EdgeSuggestion

    edge = Edge(source_id="concepts/a", target_id="concepts/b", relation_type=None)
    monkeypatch.setattr("openkos.cli.curate.find_candidates", lambda *a, **k: [])
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: [edge])
    monkeypatch.setattr(
        "openkos.cli.curate.suggest_edge_types",
        lambda edges, **k: [
            EdgeSuggestion(edge=edge, suggested_type="references", rationale="stub")
        ],
    )
    monkeypatch.setattr("openkos.cli.curate._concept_type_names", lambda *a, **k: [])
    monkeypatch.setattr(
        "openkos.cli.curate._contradiction_pairs", lambda *a, **k: ([], 0)
    )
    _simulate_tty(monkeypatch)

    before = _snapshot(tmp_path)
    # No Identity candidates: Identity finds an empty queue and does not
    # prompt. Structure's cost gate consumes "y"; the per-suggestion
    # [y/N/skip] prompt is declined with "n".
    result = runner.invoke(app, ["curate"], input="y\nn\n")

    assert result.exit_code == 0
    assert "Structure: applied 0, skipped 1." in result.stdout
    assert changed_paths(before, _snapshot(tmp_path)) == set()


def test_structure_sees_post_merge_identity_state(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Post-merge freshness (task 2.15, design D4): Structure's probe is
    called AFTER Identity's `run` has already auto-committed, so a fake
    `candidate_edges` recording `bundle_dir`'s contents at call time proves
    the survivor concept -- not the absorbed one -- is what Structure sees."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _reindexed_workspace(tmp_path, monkeypatch)

    group = CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/a", "concepts/b"),
        tier=Tier.HIGH,
        trigger="stub",
    )
    monkeypatch.setattr("openkos.cli.curate.find_candidates", lambda *a, **k: [group])
    monkeypatch.setattr(
        "openkos.cli.curate.adjudicate_candidates",
        lambda *a, **k: [
            AdjudicatedCandidate(
                candidate=group, verdict=Verdict.SAME, confidence=0.9, rationale="same"
            )
        ],
    )

    seen_survivors: list[bool] = []

    def _recording_candidate_edges(bundle_dir: Path, **kwargs: object) -> list[object]:
        seen_survivors.append((bundle_dir / "concepts" / "a.md").exists())
        seen_survivors.append((bundle_dir / "concepts" / "b.md").exists())
        return []

    monkeypatch.setattr(
        "openkos.cli.curate.candidate_edges", _recording_candidate_edges
    )
    monkeypatch.setattr("openkos.cli.curate._concept_type_names", lambda *a, **k: [])
    monkeypatch.setattr(
        "openkos.cli.curate._contradiction_pairs", lambda *a, **k: ([], 0)
    )
    _simulate_tty(monkeypatch)

    # Identity's cost gate + per-pair prompt consume "y\ny"; Structure's
    # cost gate is never reached (its probe reports an empty queue).
    result = runner.invoke(app, ["curate"], input="y\ny\n")

    assert result.exit_code == 0
    assert seen_survivors == [True, False]


# ---------------------------------------------------------------------------
# 2.16-2.17 -- Metadata stage (suggest_volatility run, sensitivity gap)
# ---------------------------------------------------------------------------


def test_metadata_accepted_tier_writes_via_extracted_set_volatility_core(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _reindexed_workspace(tmp_path, monkeypatch)

    from openkos.resolution.volatility_typing import TierSuggestion

    monkeypatch.setattr("openkos.cli.curate.find_candidates", lambda *a, **k: [])
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: [])
    monkeypatch.setattr(
        "openkos.cli.curate._concept_type_names", lambda *a, **k: ["Concept"]
    )
    monkeypatch.setattr(
        "openkos.cli.curate._contradiction_pairs", lambda *a, **k: ([], 0)
    )

    def _fake_suggest_volatility(
        bundle_dir: Path, **kwargs: object
    ) -> list[TierSuggestion]:
        on_progress = kwargs.get("on_progress")
        suggestion = TierSuggestion(
            type_name="Concept",
            current_default="static",
            suggested_tier="volatile",
            rationale="stub rationale",
        )
        if on_progress is not None:
            on_progress(1, 1, suggestion)  # type: ignore[operator]
        return [suggestion]

    monkeypatch.setattr(
        "openkos.cli.curate.suggest_volatility", _fake_suggest_volatility
    )
    _simulate_tty(monkeypatch)

    # Identity finds no candidates (empty queue, no prompt); Structure finds
    # no untyped edges (empty queue, no prompt); Metadata's cost gate
    # consumes "y", its per-type [y/N/skip] prompt consumes a second "y".
    result = runner.invoke(app, ["curate"], input="y\ny\n")

    assert result.exit_code == 0
    config_text = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")
    assert "Concept: volatile" in config_text or "volatile" in config_text
    assert "Metadata: applied 1, skipped 0." in result.stdout


def test_metadata_sensitivity_gap_reported_never_written(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    gap_path = tmp_path / "bundle" / "concepts" / "a.md"
    _write_doc(gap_path, title="Concept A")
    _reindexed_workspace(tmp_path, monkeypatch)

    monkeypatch.setattr("openkos.cli.curate.find_candidates", lambda *a, **k: [])
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: [])
    monkeypatch.setattr(
        "openkos.cli.curate._concept_type_names", lambda *a, **k: ["Concept"]
    )
    monkeypatch.setattr("openkos.cli.curate.suggest_volatility", lambda *a, **k: [])
    monkeypatch.setattr(
        "openkos.cli.curate._contradiction_pairs", lambda *a, **k: ([], 0)
    )
    _simulate_tty(monkeypatch)

    before = _snapshot(tmp_path)
    # Metadata's cost gate ("1 concept type(s) -> 1 LLM call(s)") consumes
    # the only stdin answer; `suggest_volatility` (mocked) returns no
    # suggestions, so the gap report is the only thing Metadata prints.
    result = runner.invoke(app, ["curate"], input="y\n")

    assert result.exit_code == 0
    assert "sensitivity gap" in result.stdout
    assert "concepts/a" in result.stdout
    assert "openkos set-sensitivity" in result.stdout
    assert changed_paths(before, _snapshot(tmp_path)) == set()


def test_metadata_gap_notice_survives_an_empty_type_list(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review correction (R3-01 CRITICAL): the REAL, unmocked
    `_metadata_probe` over the exact bundle shape the gap report exists to
    flag -- a lone doc whose `sensitivity` is unset, which the fail-closed
    filter therefore ALSO excludes from `_concept_type_names`, emptying
    the probe. The gap notice must ride the empty branch's
    `empty_message`; before the correction, `run_curate`'s empty-queue
    short-circuit returned before `_metadata_run` and the notice never
    printed anywhere."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _reindexed_workspace(tmp_path, monkeypatch)

    monkeypatch.setattr("openkos.cli.curate.find_candidates", lambda *a, **k: [])
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: [])
    monkeypatch.setattr(
        "openkos.cli.curate._contradiction_pairs", lambda *a, **k: ([], 0)
    )
    # `_concept_type_names` deliberately NOT patched: the doc's unset
    # sensitivity must empty the real type list for this scenario to hold --
    # which, since #240, requires the local exemption to be off, because the
    # suite's stand-in reports a local backend and the exemption would
    # legitimately readmit the doc.
    disable_local_exemption(tmp_path)

    before = _snapshot(tmp_path)
    result = runner.invoke(app, ["curate"])

    assert result.exit_code == 0
    assert "No concept types found." in result.stdout
    assert "Sensitivity unset on: concepts/a" in result.stdout
    assert "openkos set-sensitivity" in result.stdout
    assert changed_paths(before, _snapshot(tmp_path)) == set()


# ---------------------------------------------------------------------------
# 2.18-2.19 -- Contradictions stage (report-only, terminal)
# ---------------------------------------------------------------------------


def test_contradictions_runs_last_and_never_writes(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _reindexed_workspace(tmp_path, monkeypatch)

    from openkos.resolution.contradiction import (
        ContradictionVerdict,
    )
    from openkos.resolution.contradiction import (
        Verdict as CVerdict,
    )

    monkeypatch.setattr("openkos.cli.curate.find_candidates", lambda *a, **k: [])
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: [])
    monkeypatch.setattr("openkos.cli.curate._concept_type_names", lambda *a, **k: [])
    monkeypatch.setattr(
        "openkos.cli.curate._contradiction_pairs",
        lambda *a, **k: ([("concepts/a", "concepts/b")], 1),
    )

    order: list[str] = []

    def _fake_find_contradictions(
        bundle_dir: Path, **kwargs: object
    ) -> tuple[list[object], int]:
        order.append("Contradictions")
        on_progress = kwargs.get("on_progress")
        verdict = ContradictionVerdict(
            pair_ids=("concepts/a", "concepts/b"),
            verdict=CVerdict.CONTRADICTS,
            confidence=0.9,
            rationale="stub",
            conflicting_claims=("claim one",),
        )
        if on_progress is not None:
            on_progress(1, 1, verdict)  # type: ignore[operator]
        return [verdict], 1

    monkeypatch.setattr(
        "openkos.cli.curate.find_contradictions", _fake_find_contradictions
    )
    _simulate_tty(monkeypatch)

    before = _snapshot(tmp_path)
    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0
    assert order == ["Contradictions"]
    assert "CONTRADICTS" in result.stdout
    assert changed_paths(before, _snapshot(tmp_path)) == set()


# ---------------------------------------------------------------------------
# 2.20-2.21 -- all five stages live, no "not yet available" label remains
# ---------------------------------------------------------------------------


def test_all_five_stages_are_live_in_slice_2() -> None:
    live_by_name = {stage.name: stage.live for stage in curate._STAGES}
    assert all(live_by_name.values())


def test_full_summary_has_no_not_yet_available_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["curate"])

    assert result.exit_code == 0
    assert "not yet available in this version" not in result.stdout


# ---------------------------------------------------------------------------
# Review WARNING (a): `_identity_probe`'s empty-queue branch
# ---------------------------------------------------------------------------


def test_identity_probe_empty_queue_renders_no_candidate_groups_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _reindexed_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: [])
    monkeypatch.setattr("openkos.cli.curate._concept_type_names", lambda *a, **k: [])
    monkeypatch.setattr(
        "openkos.cli.curate._contradiction_pairs", lambda *a, **k: ([], 0)
    )

    result = runner.invoke(app, ["curate"])

    assert result.exit_code == 0
    assert "Identity: No candidate groups found." in result.stdout


# ---------------------------------------------------------------------------
# Review SUGGESTION: `_preconditions_run`'s structurally-unreachable branch
# ---------------------------------------------------------------------------


def test_preconditions_run_direct_call_returns_empty_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    ctx = curate.CurateContext(
        root=tmp_path,
        layout=config.WorkspaceLayout(tmp_path),
        cfg=config.read_config(tmp_path),
    )
    outcome = curate._preconditions_run(ctx, curate.StageProbe())
    assert outcome.status == "empty"


# ---------------------------------------------------------------------------
# Review SUGGESTION: all-confidential candidate group makes no model call
# ---------------------------------------------------------------------------


def test_identity_all_confidential_group_makes_no_model_call(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    for name in ("alpha", "beta"):
        path = tmp_path / "bundle" / "concepts" / f"{name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\ntype: Concept\ntitle: Same Title\n"
            "sensitivity: confidential\n---\n# Same Title\n",
            encoding="utf-8",
        )
    _reindexed_workspace(tmp_path, monkeypatch)

    group = CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/alpha", "concepts/beta"),
        tier=Tier.HIGH,
        trigger="stub",
    )
    monkeypatch.setattr("openkos.cli.curate.find_candidates", lambda *a, **k: [group])

    class _NoChatOllama:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def chat(self, messages: object) -> str:
            raise AssertionError(
                "llm.chat must never be called for an all-confidential group"
            )

    monkeypatch.setattr("openkos.cli.curate.OllamaClient", _NoChatOllama)
    _simulate_tty(monkeypatch)
    # `curate` resolves the confidential local exemption from its own
    # client, and the suite's stand-in reports a LOCAL backend -- where #240
    # legitimately includes confidential content. This test is about the
    # fail-closed FILTER, so opt out through the same workspace switch a
    # user would use.
    disable_local_exemption(tmp_path)

    result = runner.invoke(app, ["curate"], input="y\n")

    assert result.exit_code == 0
    assert "Identity: applied 0, skipped 0." in result.stdout
