"""Unit tests for `openkos curate`'s stage engine (`openkos.cli.curate`) and
its thin Typer command wiring (`cli/main.py`), slice 1 (issue #266).

Follows `test_next.py`'s "engine apart from shell" pattern for the
sequencer itself: fake `Stage` descriptors swapped in for `curate._STAGES`
via `monkeypatch`, no bundle, no CliRunner. CLI-level tests (cost lines,
gates, per-item confirms, exit codes, `--auto`) follow `test_adjudicate.py`'s
`CliRunner` + `input=`/`monkeypatch` conventions.
"""

import sys
from collections.abc import Callable
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
from tests.unit.cli.conftest import changed_paths
from tests.unit.cli.conftest import snapshot_with_mtime as _snapshot
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


def test_structure_metadata_contradictions_are_not_live() -> None:
    live_by_name = {stage.name: stage.live for stage in curate._STAGES}
    assert live_by_name["Preconditions"] is True
    assert live_by_name["Identity"] is True
    assert live_by_name["Structure"] is False
    assert live_by_name["Metadata"] is False
    assert live_by_name["Contradictions"] is False


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
