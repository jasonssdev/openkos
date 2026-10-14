"""Byte-identity characterization goldens for `openkos curate`'s Identity
walk, `adjudicate --apply`'s keep-distinct answer, and the Structure and
Metadata walks (the `curate-decisions-as-data` change, slices S0a and S0b).

Recorded on the tree BEFORE the per-item write sequences move into an
application service: each scenario pins stdout, stderr, the exit code, the
commit subjects made and the queue rows resolved. Set
`RECORD_CURATE_GOLDENS=1` to re-record (only ever on an unchanged tree).
"""

import contextlib
import json
import os
import sqlite3
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from openkos import config
from openkos.application import catalog_delta, curate_queue
from openkos.cli import observability
from openkos.cli.main import app
from openkos.graph.base import Edge
from openkos.llm.base import BackendUnavailable
from openkos.resolution.adjudication import (
    AdjudicatedCandidate,
    AdjudicationBatch,
    Verdict,
)
from openkos.resolution.candidates import CandidateGroup, CandidateGroupReport
from openkos.resolution.edge_typing import EdgeSuggestion, EdgeSuggestionBatch
from openkos.resolution.volatility_typing import TierSuggestion, TierSuggestionBatch
from openkos.state import derived
from openkos.state import pending_queue as pq
from tests.unit.cli.golden_support import Goldens, normalise, normalise_shas
from tests.unit.cli.test_curate_auto_merge import (
    _BAR,
    _FOO,
    _SAME,
    _build,
    _edit_config,
    _git,
    _Judge,
    _simulate_tty,
)

runner = CliRunner()

_GOLDENS = Path(__file__).parent / "fixtures" / "curate_characterization_goldens.json"
_RECORD = os.environ.get("RECORD_CURATE_GOLDENS") == "1"
_WINDOW = {"context_window: 12288": "context_window: 16384"}


@pytest.fixture(autouse=True)
def _pinned_environment(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pin everything a golden could read from the machine: the git identity
    through `GIT_CONFIG_COUNT` (`GIT_AUTHOR_*` is NOT read back by
    `git config`, so a CI runner with no global identity prints an
    `identity unset` line), an empty global config, no system config, and no
    reachable model server."""
    empty = tmp_path_factory.mktemp("git-global") / "gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "user.name")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "openkos tests")
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "user.email")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", "tests@openkos.invalid")
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9")
    # `_simulate_tty` makes stderr a terminal, so the embedding progress line
    # prints an elapsed time; a frozen clock keeps it at `0s` on any machine.
    monkeypatch.setattr(
        observability, "time", types.SimpleNamespace(monotonic=lambda: 0.0)
    )


def test_the_pin_is_set_through_the_environment_fixture() -> None:
    assert os.environ["GIT_CONFIG_COUNT"] == "2"
    assert os.environ["GIT_CONFIG_NOSYSTEM"] == "1"
    assert Path(os.environ["GIT_CONFIG_GLOBAL"]).read_text(encoding="utf-8") == ""


# --------------------------------------------------------------------------- #
# the recorder
# --------------------------------------------------------------------------- #


def _commit_subjects(root: Path, since: str) -> list[str]:
    out = _git(root, "log", "--reverse", "--format=%s", f"{since}..HEAD")
    return out.splitlines()


def _rows(root: Path) -> list[list[str | None]]:
    conn = sqlite3.connect(config.WorkspaceLayout(root).findings_db_path)
    try:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'pending_items'"
        ).fetchone():
            return []
        return [
            [kind, status, resolution]
            for kind, status, resolution in conn.execute(
                "SELECT kind, status, resolution FROM pending_items ORDER BY id"
            )
        ]
    finally:
        conn.close()


def _observe(
    root: Path, args: list[str], *, since: str, input: str | None = None
) -> dict[str, Any]:
    result = runner.invoke(app, args, input=input)

    def _clean(text: str) -> str:
        return normalise_shas(normalise(text, root))

    return {
        "stdout": _clean(result.stdout),
        "stderr": _clean(result.stderr),
        "exit_code": result.exit_code,
        "commits": _commit_subjects(root, since),
        "rows": _rows(root),
    }


def _head(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD").strip()


def _settle(scenario: str, actual: dict[str, Any]) -> None:
    """Record the scenario under `RECORD_CURATE_GOLDENS=1`, else compare."""
    if _RECORD:
        data = (
            json.loads(_GOLDENS.read_text(encoding="utf-8"))
            if _GOLDENS.exists()
            else {}
        )
        data[scenario] = actual
        _GOLDENS.parent.mkdir(parents=True, exist_ok=True)
        _GOLDENS.write_text(
            json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return
    Goldens(_GOLDENS).check(scenario, actual)


def _workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    groups: list[CandidateGroup],
    verdicts: dict[tuple[str, ...], tuple[Verdict, float]],
) -> tuple[Path, _Judge]:
    """`_build` plus an existing (empty) pending queue, so the stage's rows are
    enqueued and the human's answer visibly resolves or keeps them."""
    root, judge = _build(tmp_path, monkeypatch, groups, verdicts)
    layout = config.WorkspaceLayout(root)
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        pq.ensure_schema(conn)
    finally:
        conn.close()
    return root, judge


def _walk_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    groups: list[CandidateGroup] | None = None,
) -> tuple[Path, _Judge]:
    """In-class SAME groups the accept-recommended pre-pass cannot offer (a
    different context window makes the run ineligible), so the per-item walk
    is the only thing that can write. The survivor is the base id."""
    members = groups if groups is not None else [_FOO]
    root, judge = _workspace(
        tmp_path, monkeypatch, members, {g.member_ids: _SAME for g in members}
    )
    _edit_config(root, replace=_WINDOW)
    _simulate_tty(monkeypatch)
    return root, judge


def _answers(monkeypatch: pytest.MonkeyPatch, *replies: str) -> None:
    remaining = iter(replies)
    monkeypatch.setattr("typer.prompt", lambda *a, **k: next(remaining))


def _edit(root: Path, concept_id: str) -> None:
    path = root / "bundle" / f"{concept_id}.md"
    path.write_text(
        path.read_text(encoding="utf-8") + "A hand edit.\n", encoding="utf-8"
    )


def test_normalise_shas_replaces_the_hex_token() -> None:
    assert normalise_shas("  committed as a1b2c3d.") == "  committed as <SHA>."


def test_normalise_shas_also_covers_the_revert_hint_in_the_same_sentence() -> None:
    line = "  committed as 6087722 -- `git revert 6087722` undoes it."
    assert normalise_shas(line) == (
        "  committed as <SHA> -- `git revert <SHA>` undoes it."
    )


def test_normalise_shas_makes_two_different_shas_equal() -> None:
    first = "committed as " + "a" * 40
    second = "committed as " + "0123456"
    assert normalise_shas(first) == normalise_shas(second)


def test_normalise_shas_leaves_other_words_untouched() -> None:
    text = "committed as nobody; decided as abcdef0 here; committed as 12"
    assert normalise_shas(text) == text


# --------------------------------------------------------------------------- #
# Identity, the per-item walk
# --------------------------------------------------------------------------- #


def test_identity_y_merges_and_resolves_the_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _walk_workspace(tmp_path, monkeypatch)
    before = _head(root)

    actual = _observe(root, ["curate", "--auto"], since=before, input="y\n")

    _settle("identity_y", actual)


def test_identity_s_writes_nothing_and_leaves_the_row_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _walk_workspace(tmp_path, monkeypatch)
    before = _head(root)

    actual = _observe(root, ["curate", "--auto"], since=before, input="s\n")

    _settle("identity_s", actual)


def test_identity_d_records_the_ruling_and_declines_the_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _walk_workspace(tmp_path, monkeypatch)
    before = _head(root)

    actual = _observe(root, ["curate", "--auto"], since=before, input="d\n")

    _settle("identity_d", actual)


# --------------------------------------------------------------------------- #
# Identity, the bulk paths
# --------------------------------------------------------------------------- #


def test_accept_recommended_writes_each_merge_through_the_per_merge_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _workspace(
        tmp_path,
        monkeypatch,
        [_FOO, _BAR],
        {_FOO.member_ids: _SAME, _BAR.member_ids: (Verdict.SAME, 0.60)},
    )
    _simulate_tty(monkeypatch)
    before = _head(root)

    actual = _observe(root, ["curate", "--auto"], since=before, input="y\n")

    _settle("accept_recommended", actual)


def test_auto_merge_makes_one_commit_per_run_not_per_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _workspace(
        tmp_path,
        monkeypatch,
        [_FOO, _BAR],
        {_FOO.member_ids: _SAME, _BAR.member_ids: _SAME},
    )
    before = _head(root)

    actual = _observe(root, ["curate", "--auto", "--auto-merge"], since=before)

    _settle("auto_merge", actual)


# --------------------------------------------------------------------------- #
# Identity, the commit phase's refusals
# --------------------------------------------------------------------------- #


def test_a_member_that_vanished_is_skipped_and_the_walk_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _walk_workspace(tmp_path, monkeypatch)

    def _forget_then_accept(*args: object, **kwargs: object) -> str:
        (root / "bundle" / "concepts" / "foo-2.md").unlink()
        return "y"

    monkeypatch.setattr("typer.prompt", _forget_then_accept)
    before = _head(root)

    actual = _observe(root, ["curate", "--auto"], since=before)

    _settle("member_vanished", actual)


def test_drift_exits_three_with_nothing_written_and_no_later_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _walk_workspace(tmp_path, monkeypatch, [_FOO, _BAR])

    def _edit_then_accept(*args: object, **kwargs: object) -> str:
        _edit(root, "concepts/foo")
        return "y"

    monkeypatch.setattr("typer.prompt", _edit_then_accept)
    before = _head(root)

    actual = _observe(root, ["curate", "--auto"], since=before)

    _settle("drift_exit_three", actual)


def _fail_second_call(target: str, error: Exception) -> Callable[..., Any]:
    """A wrapper over the real function that raises `error` on its second
    call, so the first merge lands and the second one fails."""
    module_path, name = target.rsplit(".", 1)
    module = __import__(module_path, fromlist=[name])
    real = getattr(module, name)
    calls = {"n": 0}

    def _wrapper(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:
            raise error
        return real(*args, **kwargs)

    return _wrapper


def test_a_failure_at_prepare_exits_one_after_the_first_merge_landed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _walk_workspace(tmp_path, monkeypatch, [_FOO, _BAR])
    monkeypatch.setattr(
        "openkos.application.lifecycle.prepare_one_merge",
        _fail_second_call(
            "openkos.application.lifecycle.prepare_one_merge", ValueError("no plan")
        ),
    )
    _answers(monkeypatch, "y", "y")
    before = _head(root)

    actual = _observe(root, ["curate", "--auto"], since=before)

    _settle("write_failure_at_prepare", actual)


def test_a_failure_at_the_merge_write_exits_one_after_the_first_merge_landed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _walk_workspace(tmp_path, monkeypatch, [_FOO, _BAR])
    monkeypatch.setattr(
        "openkos.application.merge_service.commit_merge",
        _fail_second_call(
            "openkos.application.merge_service.commit_merge", OSError("disk full")
        ),
    )
    _answers(monkeypatch, "y", "y")
    before = _head(root)

    actual = _observe(root, ["curate", "--auto"], since=before)

    _settle("write_failure_at_merge", actual)


def test_drift_is_reported_before_a_catalog_recompose_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both guards would fire; curate runs drift first, then the recompose,
    then read-drift -- the order `merge_concepts` does NOT keep."""
    root, _judge = _walk_workspace(tmp_path, monkeypatch)

    def _refuse(*args: object, **kwargs: object) -> object:
        raise catalog_delta.CatalogRecomposeError("the catalog moved")

    monkeypatch.setattr(
        "openkos.application.lifecycle.recompose_merge_catalog", _refuse
    )

    def _edit_then_accept(*args: object, **kwargs: object) -> str:
        _edit(root, "concepts/foo")
        return "y"

    monkeypatch.setattr("typer.prompt", _edit_then_accept)
    before = _head(root)

    actual = _observe(root, ["curate", "--auto"], since=before)

    _settle("guard_order_drift_first", actual)


# --------------------------------------------------------------------------- #
# Identity, the keep-distinct record and the refusals around the walk
# --------------------------------------------------------------------------- #


def test_a_keep_distinct_record_failure_is_fail_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _walk_workspace(tmp_path, monkeypatch)

    def _fail(*args: object, **kwargs: object) -> str:
        raise OSError("read-only filesystem")

    monkeypatch.setattr("openkos.cli.main._apply_identity_decision", _fail)
    _answers(monkeypatch, "d")
    before = _head(root)

    actual = _observe(root, ["curate", "--auto"], since=before)

    _settle("keep_distinct_record_failure", actual)


def test_a_write_walk_without_a_terminal_refuses_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _workspace(tmp_path, monkeypatch, [_FOO], {_FOO.member_ids: _SAME})
    before = _head(root)

    actual = _observe(root, ["curate", "--auto"], since=before)

    _settle("non_tty_refusal", actual)


def test_a_backend_that_goes_down_mid_batch_reports_partial_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _walk_workspace(tmp_path, monkeypatch, [_FOO, _BAR])
    down = _Judge(
        {_FOO.member_ids: _SAME, _BAR.member_ids: _SAME},
        failure=BackendUnavailable("connection refused"),
        stop_after=1,
    )
    monkeypatch.setattr("openkos.cli.curate.adjudicate_candidates", down)
    _answers(monkeypatch, "y")
    before = _head(root)

    actual = _observe(root, ["curate", "--auto"], since=before)

    _settle("partial_availability", actual)


# --------------------------------------------------------------------------- #
# `adjudicate --apply`: the other caller of the keep-distinct helper
# --------------------------------------------------------------------------- #


def test_adjudicate_apply_d_records_the_ruling_without_a_commit_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _workspace(tmp_path, monkeypatch, [_FOO], {_FOO.member_ids: _SAME})
    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(groups=(_FOO,), produced=1, retained=1),
    )
    monkeypatch.setattr(
        "openkos.cli.main.adjudicate_candidates",
        lambda candidates, **k: AdjudicationBatch(
            results=[
                AdjudicatedCandidate(
                    candidate=candidates[0],
                    verdict=Verdict.SAME,
                    confidence=0.95,
                    rationale="stub",
                )
            ]
        ),
    )
    # An open row for the pair, so the answer's effect on the queue is visible.
    layout = config.WorkspaceLayout(root)
    curate_queue.enqueue_identity(
        layout,
        [_FOO],
        [
            AdjudicatedCandidate(
                candidate=_FOO,
                verdict=Verdict.SAME,
                confidence=0.95,
                rationale="stub",
            )
        ],
        pinned={},
        commit_section=contextlib.nullcontext,
    )
    before = _head(root)

    actual = _observe(root, ["adjudicate", "--apply"], since=before, input="d\n")

    _settle("adjudicate_apply_d", actual)


# --------------------------------------------------------------------------- #
# Structure, the per-item walk (slice S0b)
# --------------------------------------------------------------------------- #

_FOO_BAR = ("concepts/foo", "concepts/bar")
_FOO_BAR2 = ("concepts/foo", "concepts/bar-2")
_FOO2_BAR = ("concepts/foo-2", "concepts/bar")


def _structure_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    plan: list[tuple[tuple[str, str], str]],
) -> Path:
    """A workspace whose Structure stage proposes `plan` -- (edge, relation
    type) pairs -- and whose other stages are empty. No model is reached."""
    root, _judge = _workspace(
        tmp_path,
        monkeypatch,
        [_FOO, _BAR],
        {_FOO.member_ids: _SAME, _BAR.member_ids: _SAME},
    )
    edges = [Edge(source_id=s, target_id=t) for (s, t), _ in plan]
    suggestions = [
        EdgeSuggestion(edge=edge, suggested_type=kind, rationale="stub rationale")
        for edge, (_, kind) in zip(edges, plan, strict=True)
    ]
    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(),
    )
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: edges)
    monkeypatch.setattr(
        "openkos.cli.curate.suggest_edge_types",
        lambda to_type, **k: EdgeSuggestionBatch(results=suggestions),
    )
    _simulate_tty(monkeypatch)
    return root


def test_structure_y_writes_the_relation_and_applies_the_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _structure_workspace(tmp_path, monkeypatch, [(_FOO_BAR, "references")])
    before = _head(root)

    actual = _observe(
        root, ["curate", "--auto", "--structure"], since=before, input="y\n"
    )

    _settle("structure_y", actual)


def test_structure_n_lists_the_decline_and_s_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _structure_workspace(
        tmp_path,
        monkeypatch,
        [(_FOO_BAR, "references"), (_FOO2_BAR, "references")],
    )
    _answers(monkeypatch, "n", "s")
    before = _head(root)

    actual = _observe(root, ["curate", "--auto", "--structure"], since=before)

    _settle("structure_n_and_s", actual)


def test_structure_accept_remaining_and_reversed_direction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--accept structure` on an asymmetric type: `r` relates the reversed
    edge, `a` accepts that item and every remaining one of the same type."""
    root = _structure_workspace(
        tmp_path,
        monkeypatch,
        [
            (_FOO_BAR, "depends_on"),
            (_FOO_BAR2, "depends_on"),
            (_FOO2_BAR, "depends_on"),
        ],
    )
    _answers(monkeypatch, "r", "a")
    before = _head(root)

    actual = _observe(root, ["curate", "--auto", "--accept", "structure"], since=before)

    _settle("structure_accept_remaining_and_reverse", actual)


def test_structure_endpoint_deleted_while_the_prompt_waited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _structure_workspace(tmp_path, monkeypatch, [(_FOO_BAR, "references")])

    def _forget_then_accept(*args: object, **kwargs: object) -> str:
        (root / "bundle" / "concepts" / "bar.md").unlink()
        return "y"

    monkeypatch.setattr("typer.prompt", _forget_then_accept)
    before = _head(root)

    actual = _observe(root, ["curate", "--auto", "--structure"], since=before)

    _settle("structure_endpoint_dropped_before_prepare", actual)


def test_structure_endpoint_deleted_between_prepare_and_the_commit_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _structure_workspace(tmp_path, monkeypatch, [(_FOO_BAR, "references")])
    real = __import__(
        "openkos.application.lifecycle", fromlist=["prepare_relate"]
    ).prepare_relate

    def _prepare_then_forget(*args: Any, **kwargs: Any) -> Any:
        prepared = real(*args, **kwargs)
        (root / "bundle" / "concepts" / "bar.md").unlink()
        return prepared

    monkeypatch.setattr(
        "openkos.application.lifecycle.prepare_relate", _prepare_then_forget
    )
    _answers(monkeypatch, "y")
    before = _head(root)

    actual = _observe(root, ["curate", "--auto", "--structure"], since=before)

    _settle("structure_endpoint_dropped_in_section", actual)


def test_structure_drift_exits_three_with_nothing_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _structure_workspace(
        tmp_path,
        monkeypatch,
        [(_FOO_BAR, "references"), (_FOO2_BAR, "references")],
    )

    # The relate plan is read AFTER the prompt, so the edit must land after
    # `prepare_relate` -- inside the window the drift guard exists for.
    real = __import__(
        "openkos.application.lifecycle", fromlist=["prepare_relate"]
    ).prepare_relate

    def _prepare_then_edit(*args: Any, **kwargs: Any) -> Any:
        prepared = real(*args, **kwargs)
        _edit(root, "concepts/foo")
        return prepared

    monkeypatch.setattr(
        "openkos.application.lifecycle.prepare_relate", _prepare_then_edit
    )
    _answers(monkeypatch, "y", "y")
    before = _head(root)

    actual = _observe(root, ["curate", "--auto", "--structure"], since=before)

    _settle("structure_drift_exit_three", actual)


def test_structure_failure_at_prepare_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _structure_workspace(tmp_path, monkeypatch, [(_FOO_BAR, "references")])

    def _fail(*args: object, **kwargs: object) -> object:
        raise ValueError("no plan")

    monkeypatch.setattr("openkos.application.lifecycle.prepare_relate", _fail)
    _answers(monkeypatch, "y")
    before = _head(root)

    actual = _observe(root, ["curate", "--auto", "--structure"], since=before)

    _settle("structure_write_failure_at_prepare", actual)


def test_structure_failure_at_the_relate_write_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _structure_workspace(tmp_path, monkeypatch, [(_FOO_BAR, "references")])

    def _fail(*args: object, **kwargs: object) -> object:
        raise OSError("disk full")

    monkeypatch.setattr("openkos.application.lifecycle.relate_core", _fail)
    _answers(monkeypatch, "y")
    before = _head(root)

    actual = _observe(root, ["curate", "--auto", "--structure"], since=before)

    _settle("structure_write_failure_at_relate", actual)


# --------------------------------------------------------------------------- #
# Metadata, the per-type walk (slice S0b)
# --------------------------------------------------------------------------- #


def _metadata_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tiers: dict[str, str],
) -> Path:
    """A workspace whose Metadata stage proposes `tiers` (type -> tier, every
    type currently `static`) and whose other stages are empty."""
    root, _judge = _workspace(tmp_path, monkeypatch, [_FOO], {_FOO.member_ids: _SAME})
    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(),
    )
    monkeypatch.setattr(
        "openkos.cli.curate._concept_type_names", lambda *a, **k: list(tiers)
    )
    monkeypatch.setattr(
        "openkos.cli.curate.suggest_volatility",
        lambda bundle_dir, **k: TierSuggestionBatch(
            results=[
                TierSuggestion(
                    type_name=name,
                    current_default="static",
                    suggested_tier=tier,
                    rationale="stub rationale",
                )
                for name, tier in tiers.items()
            ]
        ),
    )
    _simulate_tty(monkeypatch)
    return root


def test_metadata_y_writes_the_tier_and_applies_the_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _metadata_workspace(tmp_path, monkeypatch, {"Concept": "volatile"})
    before = _head(root)

    actual = _observe(root, ["curate", "--auto"], since=before, input="y\n")

    _settle("metadata_y", actual)
    assert "Concept: volatile" in (root / "openkos.yaml").read_text(encoding="utf-8")


def test_metadata_n_writes_nothing_and_a_accepts_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _metadata_workspace(
        tmp_path,
        monkeypatch,
        {"Concept": "volatile", "Entity": "slow", "Event": "volatile"},
    )
    _answers(monkeypatch, "n", "a")
    before = _head(root)

    actual = _observe(root, ["curate", "--auto"], since=before)

    _settle("metadata_n_then_a", actual)
    written = (root / "openkos.yaml").read_text(encoding="utf-8")
    assert "Concept:" not in written.split("type_tiers:")[-1]
    assert "Entity: slow" in written
    assert "Event: volatile" in written


def test_metadata_drift_exits_three_with_nothing_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _metadata_workspace(tmp_path, monkeypatch, {"Concept": "volatile"})

    real = __import__(
        "openkos.application.lifecycle", fromlist=["prepare_set_volatility"]
    ).prepare_set_volatility

    def _prepare_then_edit_config(*args: Any, **kwargs: Any) -> Any:
        prepared = real(*args, **kwargs)
        path = root / "openkos.yaml"
        path.write_text(
            path.read_text(encoding="utf-8") + "# hand edit\n", encoding="utf-8"
        )
        return prepared

    monkeypatch.setattr(
        "openkos.application.lifecycle.prepare_set_volatility",
        _prepare_then_edit_config,
    )
    _answers(monkeypatch, "y")
    before = _head(root)

    actual = _observe(root, ["curate", "--auto"], since=before)

    _settle("metadata_drift_exit_three", actual)


def test_metadata_refuses_when_the_config_has_a_dirty_entry_for_the_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _metadata_workspace(tmp_path, monkeypatch, {"Concept": "volatile"})
    path = root / "openkos.yaml"
    path.write_text(
        path.read_text(encoding="utf-8") + "type_tiers:\n  Concept: slow\n",
        encoding="utf-8",
    )
    before = _head(root)

    actual = _observe(root, ["curate", "--auto"], since=before, input="y\n")

    _settle("metadata_dirty_config_refusal", actual)


def test_every_recorded_scenario_has_a_test() -> None:
    if _RECORD:
        return
    recorded = set(json.loads(_GOLDENS.read_text(encoding="utf-8")))
    assert recorded == {
        "identity_y",
        "identity_s",
        "identity_d",
        "accept_recommended",
        "auto_merge",
        "member_vanished",
        "drift_exit_three",
        "write_failure_at_prepare",
        "write_failure_at_merge",
        "guard_order_drift_first",
        "keep_distinct_record_failure",
        "non_tty_refusal",
        "partial_availability",
        "adjudicate_apply_d",
        "structure_y",
        "structure_n_and_s",
        "structure_accept_remaining_and_reverse",
        "structure_endpoint_dropped_before_prepare",
        "structure_endpoint_dropped_in_section",
        "structure_drift_exit_three",
        "structure_write_failure_at_prepare",
        "structure_write_failure_at_relate",
        "metadata_y",
        "metadata_n_then_a",
        "metadata_drift_exit_three",
        "metadata_dirty_config_refusal",
    }
