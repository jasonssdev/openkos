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
from typing import Any

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos import config
from openkos.bundle import decisions as bundle_decisions
from openkos.cli import curate, observability
from openkos.cli.main import app
from openkos.graph import sqlite_graph
from openkos.llm.base import EMBED_DIM
from openkos.llm.ollama import OllamaError, OllamaModelNotFound, OllamaUnavailable
from openkos.model import okf
from openkos.resolution.adjudication import (
    AdjudicatedCandidate,
    AdjudicationBatch,
    Verdict,
)
from openkos.resolution.candidates import CandidateGroup, CandidateGroupReport, Tier
from openkos.resolution.contradiction import CandidatePlan, _CandidateSpec
from tests.unit.cli.conftest import changed_paths, disable_local_exemption
from tests.unit.cli.conftest import snapshot_with_mtime as _snapshot
from tests.unit.conftest import LOCAL_BACKEND_LOCALITY
from tests.unit.vcs.conftest import isolate_git_identity

runner = CliRunner()


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


def _lines(output: str) -> list[str]:
    """Split captured output so summary assertions compare WHOLE lines.
    `render_summary` is the only owner of the `"{stage}: "` prefix, and a
    substring check cannot tell a correct line from one whose notice
    repeated that prefix -- `"Identity: applied 1, skipped 0."` is a
    substring of `"Identity: Identity: applied 1, skipped 0."` (#504)."""
    return output.splitlines()


def _unavailable_line(stage: str, detail: str, progress: str | None = None) -> str:
    """The WHOLE summary line the sequencer prints when a stage's `run`
    raises `OllamaUnavailable` -- assembled once so every caller compares
    the entire line instead of a doubling-blind prefix of it (#504).

    `progress` is the stage-authored disclosure of what a mid-batch failure
    had ALREADY written before the availability failure surfaced (#468 item
    4): pass it for a partial batch, leave it `None` for a stage that raised
    before applying anything."""
    line = (
        f"{stage}: unavailable -- {detail}. Start it with `ollama serve`, "
        "then try again. Or run `openkos doctor` to diagnose the environment."
    )
    return line if progress is None else f"{line} {progress}"


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


def _empty_plan() -> CandidatePlan:
    """A Contradictions plan with nothing to judge -- the stub every test
    that stubs out later stages installs."""
    return CandidatePlan(specs=(), edge_total=0, merged_total=0)


def _edge_plan(*pairs: tuple[str, str]) -> CandidatePlan:
    """A Contradictions plan carrying one typed-edge candidate per pair,
    built through the real `_CandidateSpec` so the stub cannot drift from
    the shape `find_contradictions` consumes."""
    specs = tuple(
        _CandidateSpec(pair_ids=pair, relation_type="related_to") for pair in pairs
    )
    return CandidatePlan(specs=specs, edge_total=len(specs), merged_total=0)


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
    def __init__(
        self,
        model: str = "stub-model",
        review: bool = True,
        chat_timeout: float = config.DEFAULT_CHAT_TIMEOUT,
        max_generation_tokens: int = config.DEFAULT_MAX_GENERATION_TOKENS,
        models: dict[str, str] | None = None,
    ) -> None:
        self.model = model
        self.review = review
        self.chat_timeout = chat_timeout
        self.max_generation_tokens = max_generation_tokens
        self.models = models or {}


class _FakeLayout:
    """Just enough surface for engine-only tests (fake stages never touch
    it): a `bundle_dir`/`vectors_db_path` pair, matching
    `config.WorkspaceLayout`'s public shape."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.bundle_dir = root / "bundle"
        self.vectors_db_path = root / ".openkos" / "vectors.db"


def _fake_ctx(
    tmp_path: Path,
    *,
    auto: bool = False,
    models: dict[str, str] | None = None,
) -> curate.CurateContext:
    return curate.CurateContext(
        root=tmp_path,
        layout=_FakeLayout(tmp_path),  # type: ignore[arg-type]
        cfg=_FakeConfig(models=models),  # type: ignore[arg-type]
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
    task: str | None = None,
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
        task=task,
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
        "openkos.cli.curate._contradiction_plan", lambda *a, **k: _empty_plan()
    )


def _set_review(tmp_path: Path, value: bool) -> None:
    """Rewrite `openkos.yaml`'s `review:` line. Issue #385 makes `curate`
    honor the knob that 16 sites in `main.py` already read, so tests need
    to set it the same way an operator would -- in the file, not through a
    flag."""
    config_path = tmp_path / "openkos.yaml"
    lines = config_path.read_text(encoding="utf-8").splitlines()
    rewritten = [
        f"review: {'true' if value else 'false'}"
        if line.startswith("review:")
        else line
        for line in lines
    ]
    assert any(line.startswith("review:") for line in rewritten), (
        "openkos.yaml has no `review:` line to rewrite"
    )
    config_path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")


def _same_verdict_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    """A COMPLETE 1-group Identity queue whose only pair adjudicates SAME
    -- the shape whose merge would DELETE `concepts/b` if it were ever
    auto-applied."""
    group = CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/a", "concepts/b"),
        tier=Tier.HIGH,
        trigger="stub-same",
    )
    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(groups=(group,), produced=1, retained=1),
    )
    monkeypatch.setattr(
        "openkos.cli.curate.adjudicate_candidates",
        lambda *a, **k: AdjudicationBatch(
            results=[
                AdjudicatedCandidate(
                    candidate=group,
                    verdict=Verdict.SAME,
                    confidence=0.9,
                    rationale="same",
                )
            ]
        ),
    )


def _one_type_metadata_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """A COMPLETE 1-type Metadata queue with a valid tier suggestion.
    Installed AFTER a Structure fixture so it overrides that fixture's
    empty `_concept_type_names` stub."""
    from openkos.resolution.volatility_typing import (
        TierSuggestion,
        TierSuggestionBatch,
    )

    monkeypatch.setattr(
        "openkos.cli.curate._concept_type_names", lambda *a, **k: ["Concept"]
    )
    monkeypatch.setattr(
        "openkos.cli.curate.suggest_volatility",
        lambda *a, **k: TierSuggestionBatch(
            results=[
                TierSuggestion(
                    type_name="Concept",
                    current_default="static",
                    suggested_tier="volatile",
                    rationale="tier rationale",
                )
            ]
        ),
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
            empty_message="Nothing to do.",
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
        return curate.StageProbe(items=(), llm_calls=0, empty_message="Nothing to do.")

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
            status="not-live", notice="not yet available in this version"
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

    # #515 re-keyed this from a run-scoped flag to a per-model map; the
    # assertion is the same claim, addressed by the model that failed.
    assert list(ctx.ollama_unavailable_notices) == ["stub-model"]


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
    # A generic `OllamaError` marks NO model unreachable, so nothing is
    # skipped downstream -- the ordering discipline #515 left untouched.
    assert ctx.ollama_unavailable_notices == {}


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
    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(groups=(group,), produced=1, retained=1),
    )
    monkeypatch.setattr(
        "openkos.cli.curate.adjudicate_candidates",
        lambda *a, **k: AdjudicationBatch(
            results=[
                AdjudicatedCandidate(
                    candidate=group,
                    verdict=Verdict.SAME,
                    confidence=0.9,
                    rationale="same",
                )
            ]
        ),
    )
    _simulate_tty(monkeypatch)

    # Two stdin answers: the Identity cost gate's `typer.confirm` consumes
    # the first "y", the per-pair `[y/N/skip]` `typer.prompt` the second.
    result = runner.invoke(app, ["curate"], input="y\ny\n")

    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / "concepts" / "b.md").exists()
    assert "Identity: applied 1, skipped 0." in _lines(result.stdout)


# ---------------------------------------------------------------------------
# issue #441 -- a partial adjudication batch keeps its completed verdicts
# ---------------------------------------------------------------------------


def _partial_identity_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: OllamaError
) -> None:
    """Seed a 2-group Identity queue whose first group adjudicated SAME and
    whose second group's chat raised `failure` -- the #441 mid-batch shape."""
    group_done = CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/a", "concepts/b"),
        tier=Tier.HIGH,
        trigger="stub-done",
    )
    group_failed = CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/c", "concepts/d"),
        tier=Tier.LOW,
        trigger="stub-failed",
    )
    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(
            groups=(group_done, group_failed), produced=2, retained=2
        ),
    )
    monkeypatch.setattr(
        "openkos.cli.curate.adjudicate_candidates",
        lambda *a, **k: AdjudicationBatch(
            results=[
                AdjudicatedCandidate(
                    candidate=group_done,
                    verdict=Verdict.SAME,
                    confidence=0.9,
                    rationale="same",
                )
            ],
            failure=failure,
            failed_index=2,
        ),
    )


def test_identity_partial_batch_applies_completed_then_reports_failed_with_counts(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A generic mid-batch `OllamaError` no longer discards Identity's
    completed verdicts (#441): the completed SAME pair still goes through
    the existing merge walk, the stage reports failed with
    completed-of-total counts, later stages still run, and the exit code
    stays 0 (curate is not a CI gate)."""
    _stub_later_stages_empty(monkeypatch)
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _reindexed_workspace(tmp_path, monkeypatch)
    _partial_identity_batch(tmp_path, monkeypatch, OllamaError("boom"))
    _simulate_tty(monkeypatch)

    # Two stdin answers: the Identity cost gate's `typer.confirm` consumes
    # the first "y", the per-pair `[y/N/skip]` `typer.prompt` the second.
    result = runner.invoke(app, ["curate"], input="y\ny\n")

    assert result.exit_code == 0
    # The accepted merge DELETED `b.md` before the failure surfaced (#468
    # item 4): the failure notice below must therefore disclose the write
    # counts, exactly as Structure's and Metadata's already do. A notice
    # that names only "1 of 2 adjudicated" leaves the operator unable to
    # tell whether a concept was destroyed.
    assert not (tmp_path / "bundle" / "concepts" / "b.md").exists()
    assert (
        "Identity: failed -- boom (adjudicated 1 of 2 candidate group(s); "
        "applied 1, skipped 0)." in _lines(result.stdout)
    )
    # Later stages still ran: their summary lines are present (pinned
    # invariant -- a generic OllamaError fails only its own stage).
    assert "Structure:" in result.stdout
    assert "Contradictions:" in result.stdout


def test_identity_partial_batch_unavailable_still_walks_then_skips_later_stages(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An `OllamaUnavailable` mid-batch keeps the completed verdicts too --
    the merge walk needs no model -- but still surfaces through the
    sequencer's run-scoped unavailable handling (`ollama serve` notice), so
    later `needs_llm` stages are not asked to spend against a dead server
    (#441)."""
    _stub_later_stages_empty(monkeypatch)
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _reindexed_workspace(tmp_path, monkeypatch)
    _partial_identity_batch(
        tmp_path, monkeypatch, OllamaUnavailable("connection refused")
    )
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["curate"], input="y\ny\n")

    assert result.exit_code == 0
    # A concept was ABSORBED AND DELETED before Ollama went down. The
    # re-raise discards the stage's return value, so without #468 item 4
    # the sequencer builds this outcome with its default `applied=0` and
    # the summary reports the dead server while staying silent about the
    # destroyed concept.
    assert not (tmp_path / "bundle" / "concepts" / "b.md").exists()
    assert _unavailable_line(
        "Identity",
        "connection refused",
        "Already applied 1, skipped 0 before the failure.",
    ) in _lines(result.stdout)


def test_identity_partial_batch_model_not_found_still_walks_then_skips_later_stages(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An `OllamaModelNotFound` mid-batch takes the same re-raise arm
    `OllamaUnavailable` does (#441): the completed verdicts still go through
    the merge walk -- the merge cores need no model -- and the failure then
    surfaces through the sequencer's run-scoped handling, with the
    model-specific `ollama pull` remediation, so later `needs_llm` stages are
    not asked to spend against a misconfigured server.

    Unlike `test_ollama_model_not_found_also_short_circuits`, which raises
    from a fake stage, this drives the class through `_identity_run`'s own
    RETURNED-batch split, where a completed merge was already applied."""
    _stub_later_stages_empty(monkeypatch)
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _reindexed_workspace(tmp_path, monkeypatch)

    from openkos.graph.base import Edge

    # Structure keeps a non-empty queue (overriding `_stub_later_stages_empty`)
    # so the run-scoped SKIP is observable: an empty queue reports "empty"
    # before the sequencer ever consults the unavailable notice.
    edge = Edge(source_id="concepts/a", target_id="concepts/b", relation_type=None)
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: [edge])
    # `edge_typing: null` declines the packaged default (#513) so Structure
    # shares Identity's model again. Without it Structure resolves
    # `gemma2:27b`, a DIFFERENT model, and is correctly no longer skipped --
    # which would make this test about the per-model keying rather than
    # about the skip reaching later stages. That keying has its own test
    # (`test_unavailability_no_longer_skips_a_stage_on_a_DIFFERENT_model`).
    cfg_path = tmp_path / "openkos.yaml"
    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8") + "\nmodels:\n  edge_typing: null\n",
        encoding="utf-8",
    )
    _partial_identity_batch(tmp_path, monkeypatch, OllamaModelNotFound("model missing"))
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["curate"], input="y\ny\n")

    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / "concepts" / "b.md").exists()
    assert (
        f"Identity: unavailable -- model '{config.DEFAULT_MODEL}' is not "
        f"installed. Pull it with `ollama pull {config.DEFAULT_MODEL}`, then "
        "try again. Already applied 1, skipped 0 before the failure."
    ) in _lines(result.stdout)
    assert "Structure: skipped -- Ollama unavailable (see above)." in _lines(
        result.stdout
    )


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
    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(groups=(group,), produced=1, retained=1),
    )
    monkeypatch.setattr(
        "openkos.cli.curate.adjudicate_candidates",
        lambda *a, **k: AdjudicationBatch(
            results=[
                AdjudicatedCandidate(
                    candidate=group,
                    verdict=Verdict.SAME,
                    confidence=0.9,
                    rationale="same",
                )
            ]
        ),
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
    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(groups=(group,), produced=1, retained=1),
    )
    monkeypatch.setattr(
        "openkos.cli.curate.adjudicate_candidates",
        lambda *a, **k: AdjudicationBatch(
            results=[
                AdjudicatedCandidate(
                    candidate=group,
                    verdict=Verdict.SAME,
                    confidence=0.9,
                    rationale="same",
                )
            ]
        ),
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
    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(groups=(group,), produced=1, retained=1),
    )
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
    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(groups=(group,), produced=1, retained=1),
    )
    ctx = curate.CurateContext(
        root=tmp_path,
        layout=config.WorkspaceLayout(tmp_path),
        cfg=config.read_config(tmp_path),
    )
    probe = curate._identity_probe(ctx)
    assert probe.items == (group,)
    assert probe.llm_calls == 1


# ---------------------------------------------------------------------------
# curate-call-budget: Identity's cost line discloses `find_candidates_report`
# truncation (curate-command delta: Identity Cost Line Discloses Truncation,
# Below-Cap Cost-Line Output Is Byte-Identical To Pre-Change Behavior)
# ---------------------------------------------------------------------------


def test_identity_probe_notice_and_bounded_llm_calls_above_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Above the cap, the Identity probe's `notice` carries the "N of M ...
    shown (cap reached)" disclosure and `probe.llm_calls` reflects the
    bounded (`retained`) count, not the pre-cap (`produced`) count."""
    _init_workspace(tmp_path, monkeypatch)
    group = CandidateGroup(
        okf_type="Concept", member_ids=("a", "b"), tier=Tier.HIGH, trigger="stub"
    )
    report = CandidateGroupReport(groups=(group,) * 50, produced=80, retained=50)
    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report", lambda *a, **k: report
    )
    ctx = curate.CurateContext(
        root=tmp_path,
        layout=config.WorkspaceLayout(tmp_path),
        cfg=config.read_config(tmp_path),
    )

    probe = curate._identity_probe(ctx)

    assert probe.llm_calls == 50
    assert probe.notice == "50 of 80 candidate group(s) shown (cap reached)"


def test_identity_notice_prints_before_the_cost_line_via_run_curate(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end through `run_curate`'s SAME notice-then-gate ordering
    `test_gate_does_not_echo_the_probe_notice`/`test_run_curate_reports_
    truncation_even_when_the_queue_is_empty` already pin for Structure: an
    Identity probe carrying a truncation notice prints it BEFORE the cost
    line, never inside `gate()` itself."""
    _patch_stdin_isatty(monkeypatch, False)
    notice = "50 of 80 candidate group(s) shown (cap reached)"

    def _probe(ctx: curate.CurateContext) -> curate.StageProbe:
        return curate.StageProbe(items=(1,) * 50, llm_calls=50, notice=notice)

    def _run(
        ctx: curate.CurateContext, probe: curate.StageProbe
    ) -> curate.StageOutcome:
        return curate.StageOutcome(status="declined")

    monkeypatch.setattr(
        curate,
        "_STAGES",
        (_fake_stage("Identity", probe=_probe, run=_run, noun="candidate group"),),
    )

    curate.run_curate(_fake_ctx(Path("unused-root"), auto=True))

    err = capsys.readouterr().err
    notice_index = err.index(notice)
    cost_line_index = err.index("50 candidate group(s) -> 50 LLM call(s)")
    assert notice_index < cost_line_index


def test_identity_probe_below_cap_notice_is_none_and_cost_line_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Below the cap, the Identity probe prints no truncation notice, and
    the cost line stays byte-identical to its pre-change wording."""
    _init_workspace(tmp_path, monkeypatch)
    group = CandidateGroup(
        okf_type="Concept", member_ids=("a", "b"), tier=Tier.HIGH, trigger="stub"
    )
    report = CandidateGroupReport(groups=(group,) * 6, produced=6, retained=6)
    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report", lambda *a, **k: report
    )
    ctx = curate.CurateContext(
        root=tmp_path,
        layout=config.WorkspaceLayout(tmp_path),
        cfg=config.read_config(tmp_path),
    )

    probe = curate._identity_probe(ctx)

    assert probe.notice is None
    assert probe.llm_calls == 6
    assert curate.cost_line(curate._STAGES[1], probe) == (
        "6 candidate group(s) -> 6 LLM call(s)"
    )


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
    from openkos.resolution.edge_typing import EdgeSuggestion, EdgeSuggestionBatch

    edge = Edge(source_id="concepts/a", target_id="concepts/b", relation_type=None)
    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(),
    )
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: [edge])
    monkeypatch.setattr("openkos.cli.curate._concept_type_names", lambda *a, **k: [])
    monkeypatch.setattr(
        "openkos.cli.curate._contradiction_plan", lambda *a, **k: _empty_plan()
    )

    def _fake_suggest(edges: object, **kwargs: object) -> EdgeSuggestionBatch:
        on_progress = kwargs.get("on_progress")
        suggestion = EdgeSuggestion(
            edge=edge, suggested_type="references", rationale="stub rationale"
        )
        if on_progress is not None:
            on_progress(1, 1, suggestion)  # type: ignore[operator]
        return EdgeSuggestionBatch(results=[suggestion])

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
    assert "Structure: applied 1, skipped 0." in _lines(result.stdout)


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
    from openkos.resolution.edge_typing import EdgeSuggestion, EdgeSuggestionBatch

    edge = Edge(source_id="concepts/a", target_id="concepts/b", relation_type=None)
    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(),
    )
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: [edge])
    monkeypatch.setattr(
        "openkos.cli.curate.suggest_edge_types",
        lambda edges, **k: EdgeSuggestionBatch(
            results=[
                EdgeSuggestion(edge=edge, suggested_type="references", rationale="stub")
            ]
        ),
    )
    monkeypatch.setattr("openkos.cli.curate._concept_type_names", lambda *a, **k: [])
    monkeypatch.setattr(
        "openkos.cli.curate._contradiction_plan", lambda *a, **k: _empty_plan()
    )
    _simulate_tty(monkeypatch)

    before = _snapshot(tmp_path)
    # No Identity candidates: Identity finds an empty queue and does not
    # prompt. Structure's cost gate consumes "y"; the per-suggestion
    # [y/N/skip] prompt is declined with "n".
    result = runner.invoke(app, ["curate"], input="y\nn\n")

    assert result.exit_code == 0
    assert "Structure: applied 0, skipped 1." in _lines(result.stdout)
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
    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(groups=(group,), produced=1, retained=1),
    )
    monkeypatch.setattr(
        "openkos.cli.curate.adjudicate_candidates",
        lambda *a, **k: AdjudicationBatch(
            results=[
                AdjudicatedCandidate(
                    candidate=group,
                    verdict=Verdict.SAME,
                    confidence=0.9,
                    rationale="same",
                )
            ]
        ),
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
        "openkos.cli.curate._contradiction_plan", lambda *a, **k: _empty_plan()
    )
    _simulate_tty(monkeypatch)

    # Identity's cost gate + per-pair prompt consume "y\ny"; Structure's
    # cost gate is never reached (its probe reports an empty queue).
    result = runner.invoke(app, ["curate"], input="y\ny\n")

    assert result.exit_code == 0
    assert seen_survivors == [True, False]


# ---------------------------------------------------------------------------
# issue #441 -- a partial edge-suggestion batch keeps its completed items
# ---------------------------------------------------------------------------


def _partial_structure_batch(
    monkeypatch: pytest.MonkeyPatch, failure: OllamaError
) -> None:
    """Seed a 2-edge Structure queue whose first edge completed with a valid
    suggestion and whose second edge's chat raised `failure` -- the #441
    mid-batch shape. Identity/Metadata/Contradictions probe empty so only
    Structure prompts."""
    from openkos.graph.base import Edge
    from openkos.resolution.edge_typing import EdgeSuggestion, EdgeSuggestionBatch

    edge_done = Edge(source_id="concepts/a", target_id="concepts/b")
    edge_failed = Edge(source_id="concepts/c", target_id="concepts/d")
    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(),
    )
    monkeypatch.setattr(
        "openkos.cli.curate.candidate_edges", lambda *a, **k: [edge_done, edge_failed]
    )
    monkeypatch.setattr(
        "openkos.cli.curate.suggest_edge_types",
        lambda *a, **k: EdgeSuggestionBatch(
            results=[
                EdgeSuggestion(
                    edge=edge_done, suggested_type="references", rationale="kept work"
                )
            ],
            failure=failure,
            failed_index=2,
        ),
    )
    monkeypatch.setattr("openkos.cli.curate._concept_type_names", lambda *a, **k: [])
    monkeypatch.setattr(
        "openkos.cli.curate._contradiction_plan", lambda *a, **k: _empty_plan()
    )


def test_structure_partial_batch_applies_completed_then_reports_failed_with_counts(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A generic mid-batch `OllamaError` no longer discards Structure's
    completed suggestions (#441): the completed suggestion still goes through
    the existing relate walk (and its accepted WRITE lands, issue #468), the
    stage reports failed with completed-of-total AND applied/skipped counts,
    later stages still run, and the exit code stays 0 (curate is not a CI
    gate)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _reindexed_workspace(tmp_path, monkeypatch)
    _partial_structure_batch(monkeypatch, OllamaError("boom"))
    _simulate_tty(monkeypatch)

    # Two stdin answers: Structure's cost gate `typer.confirm` consumes the
    # first "y", the per-suggestion `[y/N/skip]` prompt the second.
    result = runner.invoke(app, ["curate"], input="y\ny\n")

    assert result.exit_code == 0
    source_text = (tmp_path / "bundle" / "concepts" / "a.md").read_text(
        encoding="utf-8"
    )
    # The accepted relation was WRITTEN before the failure surfaced (#468):
    # the failure notice below must therefore disclose the write counts.
    assert "references" in source_text
    assert "concepts/b" in source_text
    assert (
        "Structure: failed -- boom (suggested 1 of 2 untyped edge(s); "
        "applied 1, skipped 0)." in _lines(result.stdout)
    )
    # Later stages still ran: their summary lines are present (pinned
    # invariant -- a generic OllamaError fails only its own stage).
    assert "Metadata:" in result.stdout
    assert "Contradictions:" in result.stdout


def test_structure_partial_batch_unavailable_still_walks_then_skips_later_stages(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An `OllamaUnavailable` mid-batch keeps the completed suggestions too
    -- the relate walk needs no model -- but still surfaces through the
    sequencer's run-scoped unavailable handling (`ollama serve` notice), so
    later `needs_llm` stages are not asked to spend against a dead server
    (#441)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _reindexed_workspace(tmp_path, monkeypatch)
    _partial_structure_batch(monkeypatch, OllamaUnavailable("connection refused"))
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["curate"], input="y\ny\n")

    assert result.exit_code == 0
    source_text = (tmp_path / "bundle" / "concepts" / "a.md").read_text(
        encoding="utf-8"
    )
    assert "references" in source_text
    # The accepted relation was WRITTEN before Ollama went down (#468 item
    # 4): the availability notice must disclose it, since the re-raise
    # otherwise drops the counts the stage had already computed.
    assert _unavailable_line(
        "Structure",
        "connection refused",
        "Already applied 1, skipped 0 before the failure.",
    ) in _lines(result.stdout)


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

    from openkos.resolution.volatility_typing import (
        TierSuggestion,
        TierSuggestionBatch,
    )

    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(),
    )
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: [])
    monkeypatch.setattr(
        "openkos.cli.curate._concept_type_names", lambda *a, **k: ["Concept"]
    )
    monkeypatch.setattr(
        "openkos.cli.curate._contradiction_plan", lambda *a, **k: _empty_plan()
    )

    def _fake_suggest_volatility(
        bundle_dir: Path, **kwargs: object
    ) -> TierSuggestionBatch:
        on_progress = kwargs.get("on_progress")
        suggestion = TierSuggestion(
            type_name="Concept",
            current_default="static",
            suggested_tier="volatile",
            rationale="stub rationale",
        )
        if on_progress is not None:
            on_progress(1, 1, suggestion)  # type: ignore[operator]
        return TierSuggestionBatch(results=[suggestion])

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
    assert "Metadata: applied 1, skipped 0." in _lines(result.stdout)


def test_metadata_sensitivity_gap_reported_never_written(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    gap_path = tmp_path / "bundle" / "concepts" / "a.md"
    _write_doc(gap_path, title="Concept A")
    _reindexed_workspace(tmp_path, monkeypatch)

    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(),
    )
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: [])
    monkeypatch.setattr(
        "openkos.cli.curate._concept_type_names", lambda *a, **k: ["Concept"]
    )
    from openkos.resolution.volatility_typing import TierSuggestionBatch

    monkeypatch.setattr(
        "openkos.cli.curate.suggest_volatility",
        lambda *a, **k: TierSuggestionBatch(results=[]),
    )
    monkeypatch.setattr(
        "openkos.cli.curate._contradiction_plan", lambda *a, **k: _empty_plan()
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

    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(),
    )
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: [])
    monkeypatch.setattr(
        "openkos.cli.curate._contradiction_plan", lambda *a, **k: _empty_plan()
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
# issue #441 -- a partial tier-suggestion batch keeps its completed items
# ---------------------------------------------------------------------------


def _partial_metadata_batch(
    monkeypatch: pytest.MonkeyPatch, failure: OllamaError
) -> None:
    """Seed a 2-type Metadata queue whose first type completed with a valid
    suggestion and whose second type's chat raised `failure` -- the #441
    mid-batch shape. Identity/Structure/Contradictions probe empty so only
    Metadata prompts."""
    from openkos.resolution.volatility_typing import (
        TierSuggestion,
        TierSuggestionBatch,
    )

    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(),
    )
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: [])
    monkeypatch.setattr(
        "openkos.cli.curate._concept_type_names",
        lambda *a, **k: ["Concept", "Person"],
    )
    monkeypatch.setattr(
        "openkos.cli.curate.suggest_volatility",
        lambda *a, **k: TierSuggestionBatch(
            results=[
                TierSuggestion(
                    type_name="Concept",
                    current_default="static",
                    suggested_tier="volatile",
                    rationale="kept work",
                )
            ],
            failure=failure,
            failed_index=2,
        ),
    )
    monkeypatch.setattr(
        "openkos.cli.curate._contradiction_plan", lambda *a, **k: _empty_plan()
    )


def test_metadata_partial_batch_applies_completed_then_reports_failed_with_counts(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A generic mid-batch `OllamaError` no longer discards Metadata's
    completed suggestions (#441): the completed suggestion still goes
    through the existing set-volatility walk (and its accepted WRITE lands,
    issue #468), the stage reports failed with completed-of-total AND
    applied/skipped counts, later stages still run, and the exit code stays
    0 (curate is not a CI gate)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _reindexed_workspace(tmp_path, monkeypatch)
    _partial_metadata_batch(monkeypatch, OllamaError("boom"))
    _simulate_tty(monkeypatch)

    # Two stdin answers: Metadata's cost gate `typer.confirm` consumes the
    # first "y", the per-type [y/N/skip] prompt the second.
    result = runner.invoke(app, ["curate"], input="y\ny\n")

    assert result.exit_code == 0
    config_text = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")
    # The accepted tier was WRITTEN before the failure surfaced (#468): the
    # failure notice below must therefore disclose the write counts.
    assert "volatile" in config_text
    assert (
        "Metadata: failed -- boom (suggested 1 of 2 concept type(s); "
        "applied 1, skipped 0)." in _lines(result.stdout)
    )
    # The later stage still ran: its summary line is present (pinned
    # invariant -- a generic OllamaError fails only its own stage).
    assert "Contradictions:" in result.stdout


def test_metadata_partial_batch_unavailable_still_walks_then_skips_later_stages(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An `OllamaUnavailable` mid-batch keeps the completed suggestions too
    -- the set-volatility core needs no model -- but still surfaces through
    the sequencer's run-scoped unavailable handling (`ollama serve`
    notice), so later `needs_llm` stages are not asked to spend against a
    dead server (#441)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _reindexed_workspace(tmp_path, monkeypatch)
    _partial_metadata_batch(monkeypatch, OllamaUnavailable("connection refused"))
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["curate"], input="y\ny\n")

    assert result.exit_code == 0
    config_text = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")
    assert "volatile" in config_text
    # The accepted tier was WRITTEN before Ollama went down (#468 item 4).
    assert _unavailable_line(
        "Metadata",
        "connection refused",
        "Already applied 1, skipped 0 before the failure.",
    ) in _lines(result.stdout)


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
        ContradictionBatch,
        ContradictionVerdict,
    )
    from openkos.resolution.contradiction import (
        Verdict as CVerdict,
    )

    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(),
    )
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: [])
    monkeypatch.setattr("openkos.cli.curate._concept_type_names", lambda *a, **k: [])
    monkeypatch.setattr(
        "openkos.cli.curate._contradiction_plan",
        lambda *a, **k: _edge_plan(("concepts/a", "concepts/b")),
    )

    order: list[str] = []

    def _fake_find_contradictions(
        bundle_dir: Path, **kwargs: object
    ) -> tuple[ContradictionBatch, int]:
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
        return ContradictionBatch(results=[verdict]), 1

    monkeypatch.setattr(
        "openkos.cli.curate.find_contradictions", _fake_find_contradictions
    )
    _simulate_tty(monkeypatch)

    # Scoped to `bundle/`, not the whole workspace (durable-pending-work
    # A3.3): the stage now persists each finding to `.openkos/findings.db`,
    # which is NOT a bundle write -- the curate-command spec delta states
    # that distinction explicitly (spec: "Persisting a finding is not a
    # bundle write").
    before = _snapshot(tmp_path / "bundle")
    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0
    assert order == ["Contradictions"]
    assert "CONTRADICTS" in result.stdout
    assert changed_paths(before, _snapshot(tmp_path / "bundle")) == set()


# ---------------------------------------------------------------------------
# issue #441 -- a partial contradiction batch keeps its completed verdicts
# ---------------------------------------------------------------------------


def _partial_contradictions_batch(
    monkeypatch: pytest.MonkeyPatch, failure: OllamaError
) -> None:
    """Seed a 2-candidate Contradictions queue whose first candidate
    completed with a high-confidence verdict and whose second candidate's
    chat raised `failure` -- the #441 mid-batch shape. Earlier stages probe
    empty so only Contradictions runs."""
    from openkos.resolution.contradiction import (
        ContradictionBatch,
        ContradictionVerdict,
    )
    from openkos.resolution.contradiction import Verdict as CVerdict

    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(),
    )
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: [])
    monkeypatch.setattr("openkos.cli.curate._concept_type_names", lambda *a, **k: [])
    monkeypatch.setattr(
        "openkos.cli.curate._contradiction_plan",
        lambda *a, **k: _edge_plan(
            ("concepts/a", "concepts/b"), ("concepts/c", "concepts/d")
        ),
    )
    verdict = ContradictionVerdict(
        pair_ids=("concepts/a", "concepts/b"),
        verdict=CVerdict.CONTRADICTS,
        confidence=0.9,
        rationale="kept work",
        conflicting_claims=("claim one",),
    )
    monkeypatch.setattr(
        "openkos.cli.curate.find_contradictions",
        lambda *a, **k: (
            ContradictionBatch(results=[verdict], failure=failure, failed_index=2),
            2,
        ),
    )


def test_contradictions_partial_batch_reports_completed_then_fails_with_counts(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A generic mid-batch `OllamaError` no longer discards Contradictions'
    completed verdicts (#441): the completed high-confidence verdict is
    still reported, the stage reports failed with completed-of-total counts
    ONLY (the stage is read-only, so there are no write counts to
    disclose), and the exit code stays 0 (curate is not a CI gate)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _reindexed_workspace(tmp_path, monkeypatch)
    _partial_contradictions_batch(monkeypatch, OllamaError("boom"))

    # Scoped to `bundle/` -- see `test_contradictions_runs_last_and_never_writes`.
    before = _snapshot(tmp_path / "bundle")
    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0
    assert "[CONTRADICTS] concepts/a <-> concepts/b" in result.stdout
    assert "kept work" in result.stdout
    assert "Contradictions: failed -- boom (judged 1 of 2 candidate(s))." in _lines(
        result.stdout
    )
    assert changed_paths(before, _snapshot(tmp_path / "bundle")) == set()


def test_contradictions_partial_batch_unavailable_still_reports_completed(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An `OllamaUnavailable` mid-batch keeps the completed verdicts too --
    they are already-paid-for reports -- but still surfaces through the
    sequencer's run-scoped unavailable handling (`ollama serve` notice)
    (#441). Contradictions is the last stage, so the run-scoped skip has no
    later stage to protect; the class split is kept anyway so the summary
    wording stays uniform across all four batch stages."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _reindexed_workspace(tmp_path, monkeypatch)
    _partial_contradictions_batch(monkeypatch, OllamaUnavailable("connection refused"))

    # Scoped to `bundle/` -- see `test_contradictions_runs_last_and_never_writes`.
    before = _snapshot(tmp_path / "bundle")
    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0
    assert "[CONTRADICTS] concepts/a <-> concepts/b" in result.stdout
    assert _unavailable_line("Contradictions", "connection refused") in _lines(
        result.stdout
    )
    assert changed_paths(before, _snapshot(tmp_path / "bundle")) == set()


# ---------------------------------------------------------------------------
# durable-pending-work, tasks A3.1-A3.4: the Contradictions stage persists
# each verdict to `.openkos/findings.db` after its report loop, and neither
# writes to `bundle/` nor changes how its own candidate queue is derived.
# ---------------------------------------------------------------------------


def test_contradictions_stage_persists_every_verdict_to_findings_db(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A3.1/A3.2: `findings.record_findings` is called once per verdict in
    `batch.results`, after the existing echo loop, before `_contradictions_run`
    returns its `StageOutcome` -- a later, unrelated process can read the same
    verdict, confidence, and rationale back without a new LLM call (pending-work
    spec: "A finding survives the process")."""
    from openkos.resolution.contradiction import (
        ContradictionBatch,
        ContradictionVerdict,
    )
    from openkos.resolution.contradiction import Verdict as CVerdict
    from openkos.state import derived, findings

    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _reindexed_workspace(tmp_path, monkeypatch)

    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(),
    )
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: [])
    monkeypatch.setattr("openkos.cli.curate._concept_type_names", lambda *a, **k: [])
    monkeypatch.setattr(
        "openkos.cli.curate._contradiction_plan",
        lambda *a, **k: _edge_plan(("concepts/a", "concepts/b")),
    )

    verdict = ContradictionVerdict(
        pair_ids=("concepts/a", "concepts/b"),
        verdict=CVerdict.CONTRADICTS,
        confidence=0.9,
        rationale="stated conflicting claims",
        conflicting_claims=("claim one",),
    )
    monkeypatch.setattr(
        "openkos.cli.curate.find_contradictions",
        lambda *a, **k: (ContradictionBatch(results=[verdict]), 1),
    )
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["curate", "--auto"])
    assert result.exit_code == 0

    layout = config.WorkspaceLayout(tmp_path)
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        (row,) = findings.open_findings(conn)
    finally:
        conn.close()

    assert row.pair_ids == ("concepts/a", "concepts/b")
    assert row.verdict == "contradicts"
    assert row.confidence == pytest.approx(0.9)
    assert row.rationale == "stated conflicting claims"
    assert row.merged_absorbed_id is None


def test_contradictions_stage_persists_a_merged_body_verdict_with_ledger_digests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A merged-body verdict's persisted input digests are computed over the
    ledger entry's OWN `survivor_before`/`absorbed_snapshot` strings, never a
    second file read (design Decision 2's merged-body input row)."""
    from openkos.resolution.contradiction import (
        ContradictionBatch,
        ContradictionVerdict,
        _CandidateSpec,
    )
    from openkos.resolution.contradiction import Verdict as CVerdict
    from openkos.state import derived, findings

    _init_workspace(tmp_path, monkeypatch)
    _write_survivor_with_merges(
        tmp_path / "bundle" / "concepts" / "survivor.md",
        title="Survivor",
        absorbed=["concepts/absorbed"],
    )
    _reindexed_workspace(tmp_path, monkeypatch)

    entries = _read_ledger_entries_for_test(tmp_path / "bundle", "concepts/survivor")
    plan = CandidatePlan(
        specs=(
            _CandidateSpec(
                pair_ids=("concepts/survivor", "concepts/survivor"),
                relation_type=None,
                merge_entry=entries[0],
            ),
        ),
        edge_total=0,
        merged_total=1,
    )
    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(),
    )
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: [])
    monkeypatch.setattr("openkos.cli.curate._concept_type_names", lambda *a, **k: [])
    monkeypatch.setattr("openkos.cli.curate._contradiction_plan", lambda *a, **k: plan)

    verdict = ContradictionVerdict(
        pair_ids=("concepts/survivor", "concepts/survivor"),
        verdict=CVerdict.CONSISTENT,
        confidence=0.2,
        rationale="stub",
        conflicting_claims=(),
        merged_absorbed_id="concepts/absorbed",
    )
    monkeypatch.setattr(
        "openkos.cli.curate.find_contradictions",
        lambda *a, **k: (ContradictionBatch(results=[verdict]), 1),
    )
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["curate", "--auto"])
    assert result.exit_code == 0

    layout = config.WorkspaceLayout(tmp_path)
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        (row,) = findings.open_findings(conn)
    finally:
        conn.close()

    assert row.merged_absorbed_id == "concepts/absorbed"
    refs = {digest.input_ref for digest in row.input_digests}
    assert any("survivor_before" in ref for ref in refs)
    assert any("absorbed_snapshot" in ref for ref in refs)


# ---------------------------------------------------------------------------
# pending-work spec, "Declined Findings Are Hidden By Default": a declined
# contradiction pair is re-derived and re-persisted every run (design D4's
# no-memoization rule + `_persist_findings`), but the Contradictions stage's
# own echo loop must not surface it again.
# ---------------------------------------------------------------------------


def _write_decision(
    bundle_dir: Path,
    concept_id: str,
    *,
    pair_ids: tuple[str, str],
    merged_absorbed_id: str | None = None,
    state: bundle_decisions.DecisionState = "declined",
) -> Path:
    """Construct one `bundle/.state/decisions/<concept_id>.decisions.okf`
    sidecar holding a single record, via `bundle.decisions.write_decisions`
    directly -- mirrors `tests/unit/cli/test_purge.py::_write_decision`,
    since no CLI writer verb exists yet for this store (D6 slicing
    rationale)."""
    decision_key = bundle_decisions.decision_key_for(pair_ids, merged_absorbed_id)
    record = bundle_decisions.DecisionRecord(
        decision_key=decision_key,
        pair_ids=pair_ids,
        merged_absorbed_id=merged_absorbed_id,
        state=state,
        decided_at="2026-08-12T00:00:00Z",
    )
    return bundle_decisions.write_decisions(concept_id, bundle_dir, records=[record])


def test_contradictions_stage_hides_a_declined_verdict_from_its_echo_loop(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pending-work spec: "Declined Findings Are Hidden By Default, With An
    Explicit Listing View", scenario "A declined finding stays out of
    ordinary output" -- a contradiction pair the operator already declined
    via a `bundle.decisions` record must not appear in `curate`'s own
    Contradictions echo loop when the stage recomputes that verdict, even
    though the verdict is still judged and still persisted to
    `findings.db` (the filter is on the DISPLAYED list only, mirroring
    `cli.main._is_contradiction_declined`'s use in the `contradictions`
    verb)."""
    from openkos.resolution.contradiction import (
        ContradictionBatch,
        ContradictionVerdict,
    )
    from openkos.resolution.contradiction import Verdict as CVerdict
    from openkos.state import derived, findings

    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _reindexed_workspace(tmp_path, monkeypatch)

    _write_decision(
        tmp_path / "bundle",
        "concepts/a",
        pair_ids=("concepts/a", "concepts/b"),
    )

    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(),
    )
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: [])
    monkeypatch.setattr("openkos.cli.curate._concept_type_names", lambda *a, **k: [])
    monkeypatch.setattr(
        "openkos.cli.curate._contradiction_plan",
        lambda *a, **k: _edge_plan(("concepts/a", "concepts/b")),
    )

    verdict = ContradictionVerdict(
        pair_ids=("concepts/a", "concepts/b"),
        verdict=CVerdict.CONTRADICTS,
        confidence=0.9,
        rationale="stub",
        conflicting_claims=("claim one",),
    )
    monkeypatch.setattr(
        "openkos.cli.curate.find_contradictions",
        lambda *a, **k: (ContradictionBatch(results=[verdict]), 1),
    )
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0
    assert "CONTRADICTS" not in result.stdout
    assert "claim one" not in result.stdout
    assert "Contradictions: no high-confidence contradictions found." in _lines(
        result.stdout
    )

    # Still persisted -- a declined finding is still a real finding
    # (design constraint: "Keep persisting").
    layout = config.WorkspaceLayout(tmp_path)
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        (row,) = findings.open_findings(conn)
    finally:
        conn.close()
    assert row.pair_ids == ("concepts/a", "concepts/b")


def test_contradictions_stage_still_shows_a_non_declined_verdict_in_the_same_batch(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Companion to the hides-a-declined-verdict test above: a declined pair
    and a NOT-declined pair judged in the same batch must not degenerate
    into hiding everything -- only the declined pair drops from the
    display."""
    from openkos.resolution.contradiction import (
        ContradictionBatch,
        ContradictionVerdict,
    )
    from openkos.resolution.contradiction import Verdict as CVerdict

    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _write_doc(tmp_path / "bundle" / "concepts" / "c.md", title="Concept C")
    _write_doc(tmp_path / "bundle" / "concepts" / "d.md", title="Concept D")
    _reindexed_workspace(tmp_path, monkeypatch)

    _write_decision(
        tmp_path / "bundle",
        "concepts/a",
        pair_ids=("concepts/a", "concepts/b"),
    )

    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(),
    )
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: [])
    monkeypatch.setattr("openkos.cli.curate._concept_type_names", lambda *a, **k: [])
    monkeypatch.setattr(
        "openkos.cli.curate._contradiction_plan",
        lambda *a, **k: _edge_plan(
            ("concepts/a", "concepts/b"), ("concepts/c", "concepts/d")
        ),
    )

    declined_verdict = ContradictionVerdict(
        pair_ids=("concepts/a", "concepts/b"),
        verdict=CVerdict.CONTRADICTS,
        confidence=0.9,
        rationale="declined pair",
        conflicting_claims=("claim one",),
    )
    kept_verdict = ContradictionVerdict(
        pair_ids=("concepts/c", "concepts/d"),
        verdict=CVerdict.CONTRADICTS,
        confidence=0.9,
        rationale="kept pair",
        conflicting_claims=("claim two",),
    )
    monkeypatch.setattr(
        "openkos.cli.curate.find_contradictions",
        lambda *a, **k: (
            ContradictionBatch(results=[declined_verdict, kept_verdict]),
            2,
        ),
    )
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0
    assert "concepts/a <-> concepts/b" not in result.stdout
    assert "declined pair" not in result.stdout
    assert "concepts/c <-> concepts/d" in result.stdout
    assert "kept pair" in result.stdout
    assert "Contradictions: 1 high-confidence contradiction(s) found." in _lines(
        result.stdout
    )


def _read_ledger_entries_for_test(
    bundle_dir: Path, survivor_id: str
) -> list[okf.MergeLedgerEntry]:
    from openkos.bundle import ledger as bundle_ledger

    return bundle_ledger.read_entries(survivor_id, bundle_dir)


def test_curate_resumability_ignores_whether_a_finding_is_already_persisted(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A3.4: two consecutive `curate` invocations re-derive the Contradictions
    candidate queue from current bundle state regardless of whether a finding
    for a pair is already persisted (curate-command spec: "A persisted finding
    is not a resume checkpoint")."""
    from openkos.resolution.contradiction import (
        ContradictionBatch,
        ContradictionVerdict,
    )
    from openkos.resolution.contradiction import Verdict as CVerdict
    from openkos.state import derived, findings

    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _reindexed_workspace(tmp_path, monkeypatch)

    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(),
    )
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: [])
    monkeypatch.setattr("openkos.cli.curate._concept_type_names", lambda *a, **k: [])
    monkeypatch.setattr(
        "openkos.cli.curate._contradiction_plan",
        lambda *a, **k: _edge_plan(("concepts/a", "concepts/b")),
    )
    verdict = ContradictionVerdict(
        pair_ids=("concepts/a", "concepts/b"),
        verdict=CVerdict.CONTRADICTS,
        confidence=0.9,
        rationale="stub",
        conflicting_claims=("claim",),
    )
    monkeypatch.setattr(
        "openkos.cli.curate.find_contradictions",
        lambda *a, **k: (ContradictionBatch(results=[verdict]), 1),
    )
    _simulate_tty(monkeypatch)

    first = runner.invoke(app, ["curate", "--auto"])
    assert first.exit_code == 0
    second = runner.invoke(app, ["curate", "--auto"])
    assert second.exit_code == 0

    # The same candidate pair was re-derived and re-judged both times --
    # a persisted finding did not short-circuit or skip it -- so the store
    # now holds TWO rows for the same pair, not one.
    layout = config.WorkspaceLayout(tmp_path)
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        rows = findings.open_findings(conn)
    finally:
        conn.close()

    assert len(rows) == 2
    assert all(row.pair_ids == ("concepts/a", "concepts/b") for row in rows)


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
        "openkos.cli.curate._contradiction_plan", lambda *a, **k: _empty_plan()
    )

    result = runner.invoke(app, ["curate"])

    assert result.exit_code == 0
    assert "Identity: No candidate groups found." in _lines(result.stdout)


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
    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(groups=(group,), produced=1, retained=1),
    )

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
    assert "Identity: applied 0, skipped 0." in _lines(result.stdout)


# ---------------------------------------------------------------------------
# The Contradictions cost gate must state the spend the stage actually makes
# (issue #446). `cost_line` prints `probe.llm_calls` and `gate()` asks the
# operator to approve exactly that number before any model call, so the probe
# and `find_contradictions` must count the same candidate list.
# ---------------------------------------------------------------------------


def _merge_ledger_entry(absorbed_id: str) -> object:
    """One real `MergeLedgerEntry` whose `survivor_before`/`absorbed_snapshot`
    are parseable `.md` texts, built through the real codec rather than
    hand-spliced YAML (mirrors `test_contradiction.py`'s helper)."""
    from openkos.model import okf

    return okf.MergeLedgerEntry(
        schema=okf.MERGE_LEDGER_SCHEMA_V3,
        merged_at="2026-01-01T00:00:00Z",
        absorbed_id=absorbed_id,
        absorbed_snapshot=okf.dump_frontmatter(
            {"type": "Concept", "title": "Absorbed"}, "Absorbed body."
        ),
        survivor_before=okf.dump_frontmatter(
            {"type": "Concept", "title": "Survivor"}, "Survivor before."
        ),
        index_before="",
        log_before="",
        link_rewrites=[],
        sensitivity_before="private",
        sensitivity_after="private",
    )


def _write_survivor_with_merges(path: Path, *, title: str, absorbed: list[str]) -> None:
    """Write a concept `.md` file, and -- when `absorbed` is non-empty --
    its ledger sidecar under `bundle/.state/ledger/`, via the real
    `ledger.write_entries` primitive (design: "Relocate the merge ledger to
    `bundle/.state/ledger/`"; PR#1/PR#2). Mirrors `test_contradiction.py`'s
    own `_write_survivor_with_merges`: the concept's OWN frontmatter never
    carries `merged_from` any more."""
    from openkos.bundle import ledger as bundle_ledger
    from openkos.model import okf

    path.parent.mkdir(parents=True, exist_ok=True)
    metadata: dict[str, object] = {
        "type": "Concept",
        "title": title,
        "sensitivity": "private",
    }
    path.write_text(okf.dump_frontmatter(metadata, "Current body."), encoding="utf-8")
    if absorbed:
        bundle_dir = path.parents[1]
        survivor_id = okf.concept_id_for(path, bundle_dir)
        entries = [_merge_ledger_entry(a) for a in absorbed]
        bundle_ledger.write_entries(
            survivor_id,
            bundle_dir,
            survivor_id=survivor_id,
            entries=entries,  # type: ignore[arg-type]
        )


class _CountingLLM:
    """Counts `chat` calls and always returns a well-formed verdict."""

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages: object) -> str:
        self.calls += 1
        return (
            '{"verdict": "consistent", "confidence": 0.0, '
            '"conflicting_claims": [], "rationale": "stub"}'
        )


def test_contradictions_cost_gate_counts_the_merged_body_candidates_it_spends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bundle carrying merge-ledger entries: the number `cost_line` states
    must equal the number of `llm.chat` calls the stage actually makes.

    `_contradictions_probe` counted typed-edge pairs only, while
    `_contradictions_run`'s `find_contradictions` judges `edge_specs +
    merged_specs` -- so the gate understated the spend by exactly the
    merge-ledger count (issue #446)."""
    from openkos.resolution.contradiction import find_contradictions

    _init_workspace(tmp_path, monkeypatch)
    bundle = tmp_path / "bundle"
    _write_survivor_with_merges(
        bundle / "concepts" / "survivor.md",
        title="Survivor",
        absorbed=["concepts/absorbed-one", "concepts/absorbed-two"],
    )
    _reindexed_workspace(tmp_path, monkeypatch)

    ctx = curate.CurateContext(
        root=tmp_path,
        layout=config.WorkspaceLayout(tmp_path),
        cfg=config.read_config(tmp_path),
        auto=True,
    )

    stated = curate._contradictions_probe(ctx).llm_calls

    llm = _CountingLLM()
    find_contradictions(bundle, llm=llm, local_exemption=True)

    assert llm.calls == 2, "fixture must produce two merged-body candidates"
    assert stated == llm.calls


def test_contradictions_stage_runs_for_a_bundle_whose_only_candidates_are_merges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`run_curate` skips a stage entirely when `probe.items` is empty
    (`status="empty"`, `run` never invoked). Because the Contradictions probe
    counted typed-edge pairs only, a bundle whose ONLY candidates are
    merged-body ones probed empty -- so `curate` reported "No candidate pairs
    found." and never ran the stage, and #409's detection never fired through
    `curate` at all for that bundle shape.

    This is the severe half of issue #446: not merely an understated number,
    but a stage that silently did not run."""
    _init_workspace(tmp_path, monkeypatch)
    bundle = tmp_path / "bundle"
    _write_survivor_with_merges(
        bundle / "concepts" / "survivor.md",
        title="Survivor",
        absorbed=["concepts/absorbed-one"],
    )
    _reindexed_workspace(tmp_path, monkeypatch)

    ctx = curate.CurateContext(
        root=tmp_path,
        layout=config.WorkspaceLayout(tmp_path),
        cfg=config.read_config(tmp_path),
        auto=True,
    )

    probe = curate._contradictions_probe(ctx)

    # No typed edges anywhere in this bundle -- the sole candidate is the
    # merge-ledger entry.
    assert probe.llm_calls == 1
    assert probe.items, "an empty queue here makes run_curate skip the stage"
    assert probe.empty_message is None


# ---------------------------------------------------------------------------
# Issue #450: the Contradictions stage built a graph it never used, and told
# the operator about cap truncation only after the spend it describes.
# ---------------------------------------------------------------------------


def test_contradictions_run_builds_no_graph_it_does_not_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_contradictions_run` builds exactly ONE graph: the one behind the plan
    it hands to `find_contradictions`.

    It used to build a second one purely to obtain a `store` argument --
    dead work, because `find_contradictions` reads `store` only inside its
    `if plan is None:` branch, and the plan is always supplied here. Each
    `build_graph` is a full in-memory SQLite build over the bundle, so the
    waste is real rather than notional (issue #450)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _reindexed_workspace(tmp_path, monkeypatch)

    builds: list[Path] = []
    real_build_graph = sqlite_graph.build_graph

    def _counting_build_graph(bundle_dir: Path, **kwargs: Any) -> Any:
        builds.append(bundle_dir)
        return real_build_graph(bundle_dir, **kwargs)

    monkeypatch.setattr(curate, "build_graph", _counting_build_graph)
    from openkos.resolution.contradiction import ContradictionBatch as _CBatch

    monkeypatch.setattr(
        "openkos.cli.curate.find_contradictions",
        lambda *a, **k: (_CBatch(results=[]), 0),
    )

    ctx = curate.CurateContext(
        root=tmp_path,
        layout=config.WorkspaceLayout(tmp_path),
        cfg=config.read_config(tmp_path),
        auto=True,
    )
    ctx.ollama_client = _OfflineOllama()

    curate._contradictions_run(ctx, curate.StageProbe())

    assert len(builds) == 1, f"expected one graph build, got {len(builds)}"


def test_contradictions_probe_carries_the_truncation_notice_to_the_cost_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`gate()` echoes `StageProbe.notice` immediately before `cost_line`, so
    a truncation advisory set there reaches the operator BEFORE they consent
    to the spend -- the same place `_structure_probe` puts its own cap notice.

    The Contradictions probe holds the plan the notice is computed from, so
    withholding it until `run` told the operator only after the calls it
    describes had been paid for (issue #450)."""
    _init_workspace(tmp_path, monkeypatch)
    _reindexed_workspace(tmp_path, monkeypatch)

    truncated = CandidatePlan(
        specs=(
            _CandidateSpec(
                pair_ids=("concepts/a", "concepts/b"), relation_type="related_to"
            ),
        ),
        edge_total=250,
        merged_total=40,
    )
    monkeypatch.setattr(curate, "_contradiction_plan", lambda _ctx: truncated)

    ctx = curate.CurateContext(
        root=tmp_path,
        layout=config.WorkspaceLayout(tmp_path),
        cfg=config.read_config(tmp_path),
        auto=True,
    )

    probe = curate._contradictions_probe(ctx)

    assert probe.notice is not None
    assert "cap reached" in probe.notice
    assert "typed-edge" in probe.notice
    assert "merged-body" in probe.notice


# ---------------------------------------------------------------------------
# issue #398 -- the shared per-item confirm: [y/N] contract, re-prompt on
# unrecognized input, declined-item identities in the summary
# ---------------------------------------------------------------------------


def _script_prompts(monkeypatch: pytest.MonkeyPatch, answers: list[str]) -> list[str]:
    """Route `typer.prompt` through a scripted answer list and record every
    prompt text. Mimics the real widget's default handling -- empty scripted
    input returns `default`, exactly as pressing Enter would -- so the
    "empty keeps N" behavior under test is the one a user actually gets."""
    prompts: list[str] = []
    remaining = list(answers)

    def _prompt(text: str, default: str = "N", show_default: bool = False) -> str:
        prompts.append(text)
        raw = remaining.pop(0)
        return default if raw == "" else raw

    monkeypatch.setattr("typer.prompt", _prompt)
    return prompts


def test_confirm_reprompts_on_unrecognized_input_then_accepts(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The issue's typo evidence: `t` meant as `y` must re-prompt, never be
    silently treated as a decline -- and the notice names the accepted
    tokens."""
    prompts = _script_prompts(monkeypatch, ["t", "y"])

    assert curate._confirm("Apply? [y/N]") is True

    assert prompts == ["Apply? [y/N]", "Apply? [y/N]"]
    out = capsys.readouterr().out
    assert "Unrecognized answer 't'" in out
    assert "y or n" in out


def test_confirm_reprompts_on_unrecognized_input_then_declines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts = _script_prompts(monkeypatch, ["a", "n"])

    assert curate._confirm("Apply? [y/N]") is False
    assert len(prompts) == 2


def test_confirm_empty_input_keeps_the_documented_default_decline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts = _script_prompts(monkeypatch, [""])

    assert curate._confirm("Apply? [y/N]") is False
    assert len(prompts) == 1


@pytest.mark.parametrize("answer", ["y", "yes", "Y", " y "])
def test_confirm_accepts_without_reprompt(
    answer: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts = _script_prompts(monkeypatch, [answer])

    assert curate._confirm("Apply? [y/N]") is True
    assert len(prompts) == 1


@pytest.mark.parametrize("answer", ["n", "no", "N", "  "])
def test_confirm_declines_without_reprompt(
    answer: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts = _script_prompts(monkeypatch, [answer])

    assert curate._confirm("Apply? [y/N]") is False
    assert len(prompts) == 1


def test_stage_outcome_skipped_items_defaults_to_empty() -> None:
    """Every existing construction site omits the field, so it must
    default -- and stay a tuple, matching the frozen dataclass idiom."""
    outcome = curate.StageOutcome(status="applied", applied=1, skipped=0)
    assert outcome.skipped_items == ()


def test_render_summary_lists_declined_item_identities_indented() -> None:
    outcomes = [curate.StageOutcome(status="empty") for _ in curate._STAGES]
    outcomes[2] = curate.StageOutcome(
        status="applied",
        applied=1,
        skipped=2,
        notice="applied 1, skipped 2.",
        skipped_items=(
            "concepts/a -> concepts/b [references]",
            "concepts/c -> concepts/d [part_of]",
        ),
    )

    lines = curate.render_summary(outcomes)

    assert len(lines) == 7  # 5 stage lines + 2 declined-item lines
    idx = lines.index("Structure: applied 1, skipped 2.")
    assert lines[idx + 1] == "  declined: concepts/a -> concepts/b [references]"
    assert lines[idx + 2] == "  declined: concepts/c -> concepts/d [part_of]"


def test_render_summary_prints_no_declined_lines_without_declined_items() -> None:
    outcomes = [
        curate.StageOutcome(status="applied", applied=1, skipped=1)
        for _ in curate._STAGES
    ]

    lines = curate.render_summary(outcomes)

    assert len(lines) == 5
    assert all("declined:" not in line for line in lines)


# ---------------------------------------------------------------------------
# issue #504 -- `render_summary` is the ONLY owner of the `"{stage}: "` prefix
# ---------------------------------------------------------------------------


def test_sequencer_notices_leave_the_stage_prefix_to_render_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`render_summary` prefixes every line with its stage's name, so a
    notice that names its own stage renders it twice. Probe-derived
    notices (`unavailable`, `empty_message`) never did, so the block read
    half `"Identity: No candidate groups found."` and half
    `"Identity: Identity: applied 1, skipped 0."` (#504). Whole-line
    equality is the point: `"Structure: skipped -- ..."` is a SUBSTRING of
    the doubled line, which is exactly how this survived."""
    _patch_stdin_isatty(monkeypatch, False)

    def _probe(ctx: curate.CurateContext) -> curate.StageProbe:
        return curate.StageProbe(items=("one",), llm_calls=1)

    def _raise(
        ctx: curate.CurateContext, probe: curate.StageProbe
    ) -> curate.StageOutcome:
        raise OllamaUnavailable("connection refused")

    monkeypatch.setattr(
        curate,
        "_STAGES",
        (
            _fake_stage("Identity", probe=_probe, run=_raise, writes=False),
            _fake_stage("Structure", probe=_probe, writes=False),
        ),
    )

    outcomes = curate.run_curate(_fake_ctx(Path("unused-root"), auto=True))

    assert curate.render_summary(outcomes) == [
        "Identity: unavailable -- connection refused. Start it with "
        "`ollama serve`, then try again."
        " Or run `openkos doctor` to diagnose the environment.",
        "Structure: skipped -- Ollama unavailable (see above).",
    ]


def _seed_identity_pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One SAME-adjudicated 2-member candidate group over a real workspace,
    with the later stages stubbed empty -- the exact fixture shape of
    `test_accepted_identity_pair_commits_via_shared_merge_cores`, extracted
    for the #398 prompt-contract tests."""
    group = CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/a", "concepts/b"),
        tier=Tier.HIGH,
        trigger="stub",
    )
    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(groups=(group,), produced=1, retained=1),
    )
    monkeypatch.setattr(
        "openkos.cli.curate.adjudicate_candidates",
        lambda *a, **k: AdjudicationBatch(
            results=[
                AdjudicatedCandidate(
                    candidate=group,
                    verdict=Verdict.SAME,
                    confidence=0.9,
                    rationale="same",
                )
            ]
        ),
    )


def test_identity_unrecognized_answer_reprompts_then_applies(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#398's regression evidence end-to-end: an operator typo (`t` for `y`)
    at the per-pair prompt is re-asked, not silently counted as a decline --
    the corrected `y` still lands the merge."""
    _stub_later_stages_empty(monkeypatch)
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _reindexed_workspace(tmp_path, monkeypatch)
    _seed_identity_pair(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)

    # Three stdin answers: the cost gate's `typer.confirm` consumes the
    # first "y"; the per-pair prompt reads "t" (unrecognized -> re-ask),
    # then the corrective "y".
    result = runner.invoke(app, ["curate"], input="y\nt\ny\n")

    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / "concepts" / "b.md").exists()
    assert "Unrecognized answer 't'" in result.stdout
    assert "y or n" in result.stdout
    assert "Identity: applied 1, skipped 0." in _lines(result.stdout)


def test_identity_declined_pair_identity_listed_in_summary(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_later_stages_empty(monkeypatch)
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _reindexed_workspace(tmp_path, monkeypatch)
    _seed_identity_pair(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["curate"], input="y\nn\n")

    assert result.exit_code == 0
    assert (tmp_path / "bundle" / "concepts" / "b.md").exists()
    # The advertised contract is [y/N] -- `skip` is gone from the prompt.
    assert "Merge concepts/b into concepts/a? [y/N]" in result.stdout
    assert "[y/N/skip]" not in result.stdout
    assert "Identity: applied 0, skipped 1." in _lines(result.stdout)
    assert "  declined: concepts/b -> concepts/a" in result.stdout


def test_identity_applied_only_run_prints_no_declined_lines(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_later_stages_empty(monkeypatch)
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _reindexed_workspace(tmp_path, monkeypatch)
    _seed_identity_pair(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["curate"], input="y\ny\n")

    assert result.exit_code == 0
    assert "Identity: applied 1, skipped 0." in _lines(result.stdout)
    assert "  declined:" not in result.stdout


def test_identity_applied_summary_line_names_its_stage_once(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end companion to the sequencer-level #504 test: the stage's
    OWN `run` builds this notice, so the doubling reaches the operator
    through the real command, not only through a faked `_STAGES`."""
    _stub_later_stages_empty(monkeypatch)
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _reindexed_workspace(tmp_path, monkeypatch)
    _seed_identity_pair(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["curate"], input="y\ny\n")

    assert result.exit_code == 0
    assert "Identity: applied 1, skipped 0." in _lines(result.stdout)
    assert "Identity: Identity:" not in result.stdout


def test_structure_declined_edge_identity_listed_in_summary(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _reindexed_workspace(tmp_path, monkeypatch)

    from openkos.graph.base import Edge
    from openkos.resolution.edge_typing import EdgeSuggestion, EdgeSuggestionBatch

    edge = Edge(source_id="concepts/a", target_id="concepts/b", relation_type=None)
    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(),
    )
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: [edge])
    monkeypatch.setattr(
        "openkos.cli.curate.suggest_edge_types",
        lambda edges, **k: EdgeSuggestionBatch(
            results=[
                EdgeSuggestion(edge=edge, suggested_type="references", rationale="stub")
            ]
        ),
    )
    monkeypatch.setattr("openkos.cli.curate._concept_type_names", lambda *a, **k: [])
    monkeypatch.setattr(
        "openkos.cli.curate._contradiction_plan", lambda *a, **k: _empty_plan()
    )
    _simulate_tty(monkeypatch)

    # Structure's cost gate consumes "y"; the per-suggestion prompt "n".
    result = runner.invoke(app, ["curate"], input="y\nn\n")

    assert result.exit_code == 0
    assert "Relate concepts/a -> concepts/b [references]? [y/N]" in result.stdout
    assert "[y/N/skip]" not in result.stdout
    assert "Structure: applied 0, skipped 1." in _lines(result.stdout)
    assert "  declined: concepts/a -> concepts/b [references]" in result.stdout


def test_metadata_declined_tier_identity_listed_in_summary(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _reindexed_workspace(tmp_path, monkeypatch)

    from openkos.resolution.volatility_typing import (
        TierSuggestion,
        TierSuggestionBatch,
    )

    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(),
    )
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: [])
    monkeypatch.setattr(
        "openkos.cli.curate._concept_type_names", lambda *a, **k: ["Concept"]
    )
    monkeypatch.setattr(
        "openkos.cli.curate.suggest_volatility",
        lambda *a, **k: TierSuggestionBatch(
            results=[
                TierSuggestion(
                    type_name="Concept",
                    current_default="static",
                    suggested_tier="volatile",
                    rationale="stub",
                )
            ]
        ),
    )
    monkeypatch.setattr(
        "openkos.cli.curate._contradiction_plan", lambda *a, **k: _empty_plan()
    )
    _simulate_tty(monkeypatch)

    # Metadata's cost gate consumes "y"; the per-type prompt "n".
    result = runner.invoke(app, ["curate"], input="y\nn\n")

    assert result.exit_code == 0
    assert "Set Concept -> volatile? [y/N]" in result.stdout
    assert "[y/N/skip]" not in result.stdout
    assert "Metadata: applied 0, skipped 1." in _lines(result.stdout)
    assert "  declined: Concept -> volatile" in result.stdout


# ---------------------------------------------------------------------------
# issue #385 -- per-stage accept-all, with Identity structurally excluded
# ---------------------------------------------------------------------------


def _two_edge_structure_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """A COMPLETE 2-edge Structure queue with valid suggestions, and every
    other stage probing empty -- the shape that costs two per-item prompts
    today and zero once the stage is accepted (#385)."""
    from openkos.graph.base import Edge
    from openkos.resolution.edge_typing import EdgeSuggestion, EdgeSuggestionBatch

    first = Edge(source_id="concepts/a", target_id="concepts/b")
    second = Edge(source_id="concepts/a", target_id="concepts/c")
    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(),
    )
    monkeypatch.setattr(
        "openkos.cli.curate.candidate_edges", lambda *a, **k: [first, second]
    )
    monkeypatch.setattr(
        "openkos.cli.curate.suggest_edge_types",
        lambda *a, **k: EdgeSuggestionBatch(
            results=[
                EdgeSuggestion(
                    edge=first, suggested_type="references", rationale="one"
                ),
                EdgeSuggestion(
                    edge=second, suggested_type="references", rationale="two"
                ),
            ]
        ),
    )
    monkeypatch.setattr("openkos.cli.curate._concept_type_names", lambda *a, **k: [])
    monkeypatch.setattr(
        "openkos.cli.curate._contradiction_plan", lambda *a, **k: _empty_plan()
    )


def test_accept_structure_applies_every_suggestion_without_a_per_item_prompt(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--accept structure` turns the stage's per-item walk into an
    accept-all: both suggestions are written and the `[y/N]` prompt never
    appears (spec: Per-Stage Accept-All Is Opt-In And Never Covers
    Identity). Only ONE stdin answer is supplied -- the cost gate's -- so a
    surviving per-item prompt would consume nothing and the run would
    hang or decline rather than apply 2."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _write_doc(tmp_path / "bundle" / "concepts" / "c.md", title="Concept C")
    _reindexed_workspace(tmp_path, monkeypatch)
    _two_edge_structure_queue(monkeypatch)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["curate", "--accept", "structure"], input="y\n")

    assert result.exit_code == 0
    assert "Structure: applied 2, skipped 0." in _lines(result.stdout)
    # The cost gate's own `Proceed? [y/N]` still prints, so guard on the
    # PER-ITEM prompt literal rather than on `[y/N]` alone.
    assert "Relate concepts/a" not in result.stdout
    source_text = (tmp_path / "bundle" / "concepts" / "a.md").read_text(
        encoding="utf-8"
    )
    assert "concepts/b" in source_text
    assert "concepts/c" in source_text


def test_accept_identity_is_refused_as_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--accept identity` is refused with exit 2 before any work: merging
    DELETES the absorbed concept, so no flag may turn it into an
    accept-all. The refusal names the stages that ARE acceptable rather
    than only rejecting (spec: Per-Stage Accept-All Is Opt-In And Never
    Covers Identity)."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["curate", "--accept", "identity"])

    assert result.exit_code == 2
    assert "identity" in result.stderr
    assert "structure" in result.stderr
    assert "metadata" in result.stderr


def test_accept_rejects_an_unknown_stage_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unrecognized `--accept` value is a usage error (exit 2), not a
    silently ignored no-op -- a typo must never read as "accepted"."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["curate", "--accept", "strcture"])

    assert result.exit_code == 2
    assert "strcture" in result.stderr


def test_review_false_accepts_the_non_destructive_stages(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`review: false` in `openkos.yaml` finally reaches `curate` (#385):
    with no `--accept` flag it accepts every auto-acceptable stage, so
    Structure applies both suggestions with no per-item prompt."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _write_doc(tmp_path / "bundle" / "concepts" / "c.md", title="Concept C")
    _reindexed_workspace(tmp_path, monkeypatch)
    _set_review(tmp_path, False)
    _two_edge_structure_queue(monkeypatch)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["curate"], input="y\n")

    assert result.exit_code == 0
    assert "Structure: applied 2, skipped 0." in _lines(result.stdout)
    assert "Relate concepts/a" not in result.stdout


def test_review_false_still_prompts_for_every_identity_merge(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`review: false` MUST NOT auto-apply a merge. The knob may have been
    set months ago for the standalone verbs; it cannot become retroactive
    authorization to delete a concept today (#385). The pair is declined
    here by answering `n`, which is only reachable because the prompt
    still ran."""
    _stub_later_stages_empty(monkeypatch)
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _reindexed_workspace(tmp_path, monkeypatch)
    _set_review(tmp_path, False)
    _same_verdict_pair(monkeypatch)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["curate"], input="y\nn\n")

    assert result.exit_code == 0
    assert (tmp_path / "bundle" / "concepts" / "b.md").exists()
    assert "Identity: applied 0, skipped 1." in _lines(result.stdout)


def test_explicit_accept_narrows_review_false_rather_than_widening_it(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With `review: false` AND `--accept structure`, the flag wins and
    names the EXACT accepted set: Structure applies silently, Metadata
    falls back to its per-item prompt. An explicit flag that only ever
    widened would give the operator no way to re-review one stage."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _write_doc(tmp_path / "bundle" / "concepts" / "c.md", title="Concept C")
    _reindexed_workspace(tmp_path, monkeypatch)
    _set_review(tmp_path, False)
    _two_edge_structure_queue(monkeypatch)
    _one_type_metadata_queue(monkeypatch)
    _simulate_tty(monkeypatch)

    # Structure's cost gate, Metadata's cost gate, Metadata's per-item prompt.
    result = runner.invoke(app, ["curate", "--accept", "structure"], input="y\ny\nn\n")

    assert result.exit_code == 0
    assert "Structure: applied 2, skipped 0." in _lines(result.stdout)
    assert "Set Concept -> volatile? [y/N]" in result.stdout
    assert "Metadata: applied 0, skipped 1." in _lines(result.stdout)


def test_accept_lets_a_non_tty_run_write_that_stage(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--accept` IS per-item write consent, so it lifts the non-TTY write
    refusal for exactly the named stages: `--auto --accept structure` on a
    pipe writes instead of printing the standalone-verb hint. This is
    parity with `suggest-relations --auto`, which already writes
    unattended; Identity is unreachable by this path (#385)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _write_doc(tmp_path / "bundle" / "concepts" / "c.md", title="Concept C")
    _reindexed_workspace(tmp_path, monkeypatch)
    _two_edge_structure_queue(monkeypatch)

    result = runner.invoke(app, ["curate", "--auto", "--accept", "structure"])

    assert result.exit_code == 0
    assert "Structure: applied 2, skipped 0." in _lines(result.stdout)
    assert "openkos relate" not in result.stdout


def test_review_false_never_lifts_the_non_tty_write_refusal_for_identity(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`review: false` on a PIPED run with `--auto` must still refuse
    Identity's write walk and print the standalone-verb hint (#385).

    This pins the composite invariant from the other side: the non-TTY
    refusal is lifted by `accepted_stages` membership, so if Identity ever
    became auto-acceptable it would gain unattended merge authority --
    concepts deleted by a config line and a pipe, with no prompt anywhere
    in the run to decline at."""
    _stub_later_stages_empty(monkeypatch)
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _reindexed_workspace(tmp_path, monkeypatch)
    _set_review(tmp_path, False)
    _same_verdict_pair(monkeypatch)

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0
    assert (tmp_path / "bundle" / "concepts" / "b.md").exists()
    assert (
        "Identity: non-interactive write consent unavailable -- run "
        "`openkos adjudicate --apply-same --confirm-count <n>` instead."
    ) in _lines(result.stdout)


def _mixed_structure_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """A COMPLETE 2-edge Structure queue: one SPECIFIC suggestion and one
    `related_to`, the type the prompt designates as correct when the
    documents do not support a specific claim (#508)."""
    from openkos.graph.base import Edge
    from openkos.resolution.edge_typing import EdgeSuggestion, EdgeSuggestionBatch

    specific = Edge(source_id="concepts/a", target_id="concepts/b")
    vague = Edge(source_id="concepts/a", target_id="concepts/c")
    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(),
    )
    monkeypatch.setattr(
        "openkos.cli.curate.candidate_edges", lambda *a, **k: [specific, vague]
    )
    monkeypatch.setattr(
        "openkos.cli.curate.suggest_edge_types",
        lambda *a, **k: EdgeSuggestionBatch(
            results=[
                EdgeSuggestion(
                    edge=specific, suggested_type="references", rationale="names it"
                ),
                EdgeSuggestion(
                    edge=vague, suggested_type="related_to", rationale="cannot say how"
                ),
            ]
        ),
    )
    monkeypatch.setattr("openkos.cli.curate._concept_type_names", lambda *a, **k: [])
    monkeypatch.setattr(
        "openkos.cli.curate._contradiction_plan", lambda *a, **k: _empty_plan()
    )


def test_accept_structure_still_prompts_for_a_related_to_suggestion(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--accept structure` applies the SPECIFIC suggestion silently but
    still prompts for `related_to` (#508).

    `related_to` is the answer the prompt calls correct when the documents
    do not support a specific claim, so it is the one type whose bulk
    acceptance adds no specific claim to the graph -- and, measured on a
    real bundle, 67% of accepted edges. Accepting those unreviewed is
    exactly the low-value material #385 warned about."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _write_doc(tmp_path / "bundle" / "concepts" / "c.md", title="Concept C")
    _reindexed_workspace(tmp_path, monkeypatch)
    _mixed_structure_queue(monkeypatch)
    _simulate_tty(monkeypatch)

    # The cost gate's "y", then a decline for the `related_to` prompt that
    # must still be asked.
    result = runner.invoke(app, ["curate", "--accept", "structure"], input="y\nn\n")

    assert result.exit_code == 0
    assert "Relate concepts/a -> concepts/b" not in result.stdout
    assert "Relate concepts/a -> concepts/c [related_to]? [y/N]" in result.stdout
    assert "Structure: applied 1, skipped 1." in _lines(result.stdout)
    assert "  declined: concepts/a -> concepts/c [related_to]" in result.stdout


def test_accept_structure_on_a_pipe_skips_related_to_instead_of_prompting(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a non-TTY run there is no channel to ask on, so an unacceptable
    item is SKIPPED rather than prompted (#508).

    Without this the run would reach `typer.prompt` with no terminal and
    die mid-walk -- the same failure the Identity non-TTY guard exists to
    prevent. Unattended acceptance therefore applies exactly the specific
    suggestions and leaves the rest queued for a human."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _write_doc(tmp_path / "bundle" / "concepts" / "c.md", title="Concept C")
    _reindexed_workspace(tmp_path, monkeypatch)
    _mixed_structure_queue(monkeypatch)

    result = runner.invoke(app, ["curate", "--auto", "--accept", "structure"])

    assert result.exit_code == 0
    assert "Relate concepts/a" not in result.stdout
    assert "Structure: applied 1, skipped 1." in _lines(result.stdout)
    source_text = (tmp_path / "bundle" / "concepts" / "a.md").read_text(
        encoding="utf-8"
    )
    assert "concepts/b" in source_text
    assert "concepts/c" not in source_text


def _asymmetric_structure_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """A COMPLETE 2-edge Structure queue: one `references` suggestion (the
    remaining bulk-acceptable specific type) and one ASYMMETRIC `part_of`
    (#624) -- the shape that separates what `--accept structure` may still
    write silently from what must reach the operator."""
    from openkos.graph.base import Edge
    from openkos.resolution.edge_typing import EdgeSuggestion, EdgeSuggestionBatch

    symmetric = Edge(source_id="concepts/a", target_id="concepts/b")
    asymmetric = Edge(source_id="concepts/a", target_id="concepts/c")
    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(),
    )
    monkeypatch.setattr(
        "openkos.cli.curate.candidate_edges",
        lambda *a, **k: [symmetric, asymmetric],
    )
    monkeypatch.setattr(
        "openkos.cli.curate.suggest_edge_types",
        lambda *a, **k: EdgeSuggestionBatch(
            results=[
                EdgeSuggestion(
                    edge=symmetric, suggested_type="references", rationale="names it"
                ),
                EdgeSuggestion(
                    edge=asymmetric, suggested_type="part_of", rationale="inside it"
                ),
            ]
        ),
    )
    monkeypatch.setattr("openkos.cli.curate._concept_type_names", lambda *a, **k: [])
    monkeypatch.setattr(
        "openkos.cli.curate._contradiction_plan", lambda *a, **k: _empty_plan()
    )


def test_accept_structure_still_prompts_for_an_asymmetric_suggestion(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--accept structure` applies `references` silently but still prompts
    for an asymmetric type (#624).

    #613 measured both models answering the SAME asymmetric type with
    SOURCE/TARGET swapped on nearly every edge -- there is no direction
    evidence behind an asymmetric suggestion, so bulk acceptance would
    write unverified structure the whole graph then believes. The consent
    line itself must say so."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _write_doc(tmp_path / "bundle" / "concepts" / "c.md", title="Concept C")
    _reindexed_workspace(tmp_path, monkeypatch)
    _asymmetric_structure_queue(monkeypatch)
    _simulate_tty(monkeypatch)

    # The cost gate's "y", then a decline for the `part_of` prompt that
    # must still be asked.
    result = runner.invoke(app, ["curate", "--accept", "structure"], input="y\nn\n")

    assert result.exit_code == 0
    assert "Relate concepts/a -> concepts/b" not in result.stdout
    assert (
        "Relate concepts/a -> concepts/c [part_of] "
        "(direction model-suggested, unverified)? [y/N]" in result.stdout
    )
    assert "Structure: applied 1, skipped 1." in _lines(result.stdout)
    assert "  declined: concepts/a -> concepts/c [part_of]" in result.stdout


def test_accept_structure_on_a_pipe_skips_asymmetric_instead_of_prompting(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a non-TTY run there is no channel to consent on, so an asymmetric
    suggestion is SKIPPED rather than written (#624) -- the acceptance
    criterion verbatim: `--accept structure` writes NO asymmetric type
    without per-item consent, and a pipe cannot give one."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _write_doc(tmp_path / "bundle" / "concepts" / "c.md", title="Concept C")
    _reindexed_workspace(tmp_path, monkeypatch)
    _asymmetric_structure_queue(monkeypatch)

    result = runner.invoke(app, ["curate", "--auto", "--accept", "structure"])

    assert result.exit_code == 0
    assert "Relate concepts/a" not in result.stdout
    assert "Structure: applied 1, skipped 1." in _lines(result.stdout)
    source_text = (tmp_path / "bundle" / "concepts" / "a.md").read_text(
        encoding="utf-8"
    )
    assert "concepts/b" in source_text
    assert "concepts/c" not in source_text


def test_per_item_walk_marks_direction_unverified_only_on_asymmetric_types(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The per-item consent line for an asymmetric type states the direction
    is model-suggested and unverified; a symmetric-scope prompt
    (`references`) carries no such note (#624). The human decides with the
    caveat in view, not from a bare type name."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _write_doc(tmp_path / "bundle" / "concepts" / "c.md", title="Concept C")
    _reindexed_workspace(tmp_path, monkeypatch)
    _asymmetric_structure_queue(monkeypatch)
    _simulate_tty(monkeypatch)

    # The cost gate's "y", then a decline per suggestion.
    result = runner.invoke(app, ["curate"], input="y\nn\nn\n")

    assert result.exit_code == 0
    assert "Relate concepts/a -> concepts/b [references]? [y/N]" in result.stdout
    assert (
        "Relate concepts/a -> concepts/c [part_of] "
        "(direction model-suggested, unverified)? [y/N]" in result.stdout
    )


def test_accepted_structure_discloses_that_types_go_in_unreviewed(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An accepted Structure prints ONE advisory naming what bulk
    acceptance actually spends (#513).

    `evals/edge_typing/` measures the suggester emitting a specific type
    correctly about 60% of the time, so roughly two in five bulk-written
    relation types are wrong by the rubric. The flag stays; going in blind
    does not -- and since #624 the advisory names the split: only the
    symmetric-scope types go in unreviewed, asymmetric ones ask per
    item."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _write_doc(tmp_path / "bundle" / "concepts" / "c.md", title="Concept C")
    _reindexed_workspace(tmp_path, monkeypatch)
    _two_edge_structure_queue(monkeypatch)

    result = runner.invoke(app, ["curate", "--auto", "--accept", "structure"])

    assert result.exit_code == 0
    assert result.stderr.count("applied without review") == 1
    assert (
        "openkos curate: Structure: symmetric suggested relation types are"
        in result.stderr
    )
    assert "Asymmetric types and related_to still ask per item" in result.stderr


def test_structure_without_accept_prints_no_bulk_advisory(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The advisory is scoped to bulk acceptance: a per-item walk reviews
    every type as it goes, so there is nothing to warn about and the line
    would be pure noise (#513)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _write_doc(tmp_path / "bundle" / "concepts" / "c.md", title="Concept C")
    _reindexed_workspace(tmp_path, monkeypatch)
    _two_edge_structure_queue(monkeypatch)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["curate"], input="y\nn\nn\n")

    assert result.exit_code == 0
    assert "applied without review" not in result.stderr


# --- #515: per-task models make availability a PER-MODEL fact ---------------


def test_every_llm_stage_declares_the_task_it_spends_on() -> None:
    """Each `needs_llm` stage names a task in `TASK_MODEL_KEYS`; Preconditions
    names none.

    A stage that forgets `task=` keeps the global model in silence -- the
    same silent-wrong-model failure #515 decision 2 refuses, just on the
    `curate` side of the seam. `curate.py` cannot import `main.py`, so
    nothing else forces these five to agree with the config schema.
    """
    declared = {stage.name: stage.task for stage in curate._STAGES}

    assert declared == {
        "Preconditions": None,
        "Identity": "adjudication",
        "Structure": "edge_typing",
        "Metadata": "volatility_typing",
        "Contradictions": "contradiction",
    }
    for task in declared.values():
        assert task is None or task in config.TASK_MODEL_KEYS


def test_unavailability_no_longer_skips_a_stage_on_a_DIFFERENT_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The run-scoped skip becomes PER-MODEL (#515 decision 3).

    Structure failing for want of `gemma2:27b` says nothing about whether
    Metadata's model is reachable. Before per-task models the two were the
    same tag, so one failure genuinely did settle the question; now it does
    not, and skipping on that basis would refuse work that would have
    succeeded.
    """
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
        task="edge_typing",
    )
    second = _fake_stage(
        "Second",
        probe=lambda ctx: curate.StageProbe(items=(1,), llm_calls=1),
        run=_second_run,
        writes=False,
        task="volatility_typing",
    )
    monkeypatch.setattr(curate, "_STAGES", (first, second))
    monkeypatch.setattr(curate, "OllamaClient", lambda **kwargs: _OfflineOllama())

    ctx = _fake_ctx(
        Path("unused-root"), auto=True, models={"edge_typing": "gemma2:27b"}
    )
    outcomes = curate.run_curate(ctx)

    assert calls == ["First", "Second"]
    assert outcomes[0].status == "unavailable"
    assert outcomes[1].status == "applied"


def test_unavailability_still_skips_a_later_stage_on_the_SAME_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two stages resolving the SAME tag still share one verdict: a dead
    connection settles the question for both.

    The per-model keying is what #515 changed, not the skip itself. Both
    tasks here are deliberately ones WITHOUT a packaged default (#513
    ships one for `edge_typing`), so with no `models:` override they
    resolve the same global tag -- which is the condition this test is
    about.
    """
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
        task="volatility_typing",
    )
    second = _fake_stage(
        "Second",
        probe=lambda ctx: curate.StageProbe(items=(1,), llm_calls=1),
        run=_second_run,
        writes=False,
        task="contradiction",
    )
    monkeypatch.setattr(curate, "_STAGES", (first, second))
    monkeypatch.setattr(curate, "OllamaClient", lambda **kwargs: _OfflineOllama())

    ctx = _fake_ctx(Path("unused-root"), auto=True)  # no `models:` override
    outcomes = curate.run_curate(ctx)

    assert calls == ["First"]
    assert outcomes[1].status == "unavailable"
    assert "see above" in (outcomes[1].notice or "")


def test_stages_sharing_a_model_share_one_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clients are cached BY MODEL, not rebuilt per stage.

    The cost of keying availability per model (#515 decision 3) is one
    failed connection per DISTINCT model instead of one per run. That cost
    is only bounded if stages resolving the same tag reuse one client --
    otherwise the change would multiply connections by stage count.
    """
    _patch_stdin_isatty(monkeypatch, True)
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)
    built: list[str] = []

    def _record(**kwargs: object) -> _OfflineOllama:
        built.append(str(kwargs["model"]))
        return _OfflineOllama()

    monkeypatch.setattr(curate, "OllamaClient", _record)
    stages = tuple(
        _fake_stage(
            name,
            probe=lambda ctx: curate.StageProbe(items=(1,), llm_calls=1),
            writes=False,
            task=task,
        )
        for name, task in (
            ("First", "edge_typing"),
            ("Second", "volatility_typing"),
            ("Third", "contradiction"),
        )
    )
    monkeypatch.setattr(curate, "_STAGES", stages)

    ctx = _fake_ctx(
        Path("unused-root"), auto=True, models={"edge_typing": "gemma2:27b"}
    )
    curate.run_curate(ctx)

    # `gemma2:27b` once for Structure's task; `stub-model` once, SHARED by
    # the two stages that both fall back to the global default.
    assert built == ["gemma2:27b", "stub-model"]


def test_model_not_found_names_the_STAGE_model_not_the_global_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `ollama pull` remediation names the model the stage actually
    asked for.

    Naming `cfg.model` here would send the operator to pull a model that is
    already installed while the one that is missing stays missing -- the
    remediation would be actively wrong, which is worse than absent.
    """
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
        task="edge_typing",
    )
    monkeypatch.setattr(curate, "_STAGES", (stage,))
    monkeypatch.setattr(curate, "OllamaClient", lambda **kwargs: _OfflineOllama())

    ctx = _fake_ctx(
        Path("unused-root"), auto=True, models={"edge_typing": "gemma2:27b"}
    )
    outcomes = curate.run_curate(ctx)

    notice = outcomes[0].notice or ""
    assert "ollama pull gemma2:27b" in notice
    assert "stub-model" not in notice


def test_cost_gate_names_the_model_when_the_stage_resolves_a_different_one(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The gate discloses WHICH model the operator is consenting to spend on.

    `74 untyped edge(s) -> 74 LLM call(s)` means ~1.6 minutes on the default
    and ~9 on `gemma2:27b` (#516's sweep). Asking for consent to a cost the
    line does not describe is the same disclosure gap #468 closed elsewhere.
    """
    _patch_stdin_isatty(monkeypatch, True)
    stage = _fake_stage("Structure", noun="untyped edge", task="edge_typing")
    probe = curate.StageProbe(items=(1,), llm_calls=74)
    ctx = _fake_ctx(
        Path("unused-root"), auto=True, models={"edge_typing": "gemma2:27b"}
    )

    curate.gate(stage, probe, ctx)

    err = capsys.readouterr().err
    assert "74 untyped edge(s) -> 74 LLM call(s)" in err
    assert "gemma2:27b" in err


def test_cost_gate_output_is_unchanged_when_the_stage_uses_the_global_model(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A stage on the global model prints EXACTLY the pre-#515 gate output.

    The `curate-command` spec pins this line byte-identical for a below-cap
    corpus ("Below-Cap Cost-Line Output Is Byte-Identical To Pre-Change
    Behavior"). Disclosing the model on a SEPARATE line, only when it
    differs from the global default, satisfies #515's disclosure duty
    without touching that requirement: a workspace with no `models:` sees
    output identical to today's, byte for byte.
    """
    _patch_stdin_isatty(monkeypatch, True)
    stage = _fake_stage("Identity", noun="candidate group", task="adjudication")
    probe = curate.StageProbe(items=(1,), llm_calls=6)
    ctx = _fake_ctx(Path("unused-root"), auto=True)

    curate.gate(stage, probe, ctx)

    assert capsys.readouterr().err == "6 candidate group(s) -> 6 LLM call(s)\n"


def test_cost_gate_names_the_optional_upgrade_for_an_undecided_edge_typing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """#650: with no packaged default, a workspace that never keyed
    `edge_typing` runs it on the global model -- the gate reports the
    resolved model and names `gemma2:27b` as the optional measured
    upgrade, exactly where the operator is consenting to the spend."""
    _patch_stdin_isatty(monkeypatch, True)
    stage = _fake_stage("Structure", noun="untyped edge", task="edge_typing")
    probe = curate.StageProbe(items=(1,), llm_calls=3)
    ctx = _fake_ctx(Path("unused-root"), auto=True)

    curate.gate(stage, probe, ctx)

    err = capsys.readouterr().err
    assert "3 untyped edge(s) -> 3 LLM call(s)" in err
    assert "stub-model" in err
    assert "gemma2:27b" in err


def test_cost_gate_stays_quiet_about_the_upgrade_once_the_operator_decided(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A workspace that keyed `edge_typing` -- to ANY value -- made its
    choice; the gate never nags about the recommendation on top of it."""
    _patch_stdin_isatty(monkeypatch, True)
    stage = _fake_stage("Structure", noun="untyped edge", task="edge_typing")
    probe = curate.StageProbe(items=(1,), llm_calls=3)
    ctx = _fake_ctx(Path("unused-root"), auto=True, models={"edge_typing": "qwen3:14b"})

    curate.gate(stage, probe, ctx)

    err = capsys.readouterr().err
    assert "gemma2:27b" not in err
    assert "qwen3:14b" in err
