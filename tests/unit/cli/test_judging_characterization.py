"""Byte-identity characterization goldens for the judging spend path (the
`judging-application-service` change, slice S0a).

Recorded on the tree BEFORE the judging helpers move behind an application
service: each scenario pins stdout, stderr, the exit code, the commit subjects
made and the persisted verdict / suggestion rows. The scenarios cover the
output the existing characterization files leave unpinned: the Identity cost
line when a stored verdict is served, the model-disclosure lines, a declined
gate, a partial batch for Structure and Metadata, and the persist fail-open
and busy advisories of the adjudications and edge-suggestions stores.

Set `RECORD_JUDGING_GOLDENS=1` to re-record (only ever on an unchanged tree).
The recorded file is never edited by a later slice: a red golden means the
slice is wrong, not the golden.
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
from tests.unit.cli.commit_phase_support import hold_the_lock_from
from tests.unit.cli.golden_support import Goldens, normalise, normalise_shas
from tests.unit.cli.test_curate_auto_merge import (
    _BAR,
    _DIFFERENT,
    _FOO,
    _build,
    _edit_config,
    _git,
    _Judge,
    _prime_store,
    _simulate_tty,
)
from tests.unit.cli.test_curate_characterization import (
    _FOO2_BAR,
    _FOO_BAR,
    _answers,
    _head,
    _structure_workspace,
    _workspace,
)
from tests.unit.cli.test_suggest_relations import _init_workspace, _write_doc

runner = CliRunner()

_GOLDENS = Path(__file__).parent / "fixtures" / "judging_characterization_goldens.json"
_RECORD = os.environ.get("RECORD_JUDGING_GOLDENS") == "1"
_BOTH_DIFFERENT = {_FOO.member_ids: _DIFFERENT, _BAR.member_ids: _DIFFERENT}


@pytest.fixture(autouse=True)
def _pinned_environment(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pin everything a golden could read from the machine: the git identity
    through `GIT_CONFIG_COUNT` (`GIT_AUTHOR_*` is NOT read back by
    `git config`, so a CI runner with no global identity prints an
    `identity unset` line), an empty global config, no system config, no
    reachable model server, and a frozen progress clock."""
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


def _findings_rows(
    root: Path, table: str, columns: str, order: str = "1"
) -> list[list[Any]]:
    path = config.WorkspaceLayout(root).findings_db_path
    if not path.exists():
        return []
    conn = sqlite3.connect(path)
    try:
        return [
            list(row)
            for row in conn.execute(f"SELECT {columns} FROM {table} ORDER BY {order}")  # noqa: S608 -- fixed literals from this module
        ]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def _observe(
    root: Path,
    args: list[str],
    *,
    since: str,
    input: str | None = None,
) -> dict[str, Any]:
    result = runner.invoke(app, args, input=input)

    def _clean(text: str) -> str:
        return normalise_shas(normalise(text, root))

    return {
        "stdout": _clean(result.stdout),
        "stderr": _clean(result.stderr),
        "exit_code": result.exit_code,
        "commits": _git(root, "log", "--reverse", "--format=%s", f"{since}..HEAD")
        .strip()
        .splitlines(),
        "queue_rows": _findings_rows(
            root, "pending_items", "kind, status, resolution", "id"
        ),
        "adjudications": _findings_rows(root, "adjudications", "verdict, rationale"),
        "edge_suggestions": _findings_rows(
            root, "edge_suggestions", "pair_key, suggested_type"
        ),
    }


def _settle(scenario: str, actual: dict[str, Any]) -> None:
    """Record the scenario under `RECORD_JUDGING_GOLDENS=1`, else compare."""
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


def _identity_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, _Judge]:
    """Two groups, both judged different: `_FOO` has a servable stored verdict
    (an earlier run left it), `_BAR` is judged fresh. Nothing to walk, so the
    stream is the cost line, the disclosures and the persist."""
    root, judge = _workspace(tmp_path, monkeypatch, [_FOO, _BAR], _BOTH_DIFFERENT)
    _prime_store(root, _FOO)
    # A terminal, so the Identity stage judges instead of refusing the write walk.
    _simulate_tty(monkeypatch)
    return root, judge


# --------------------------------------------------------------------------- #
# Identity cost line, model disclosure, declined gate (S0a.2 - S0a.4)
# --------------------------------------------------------------------------- #


def test_identity_cost_line_reports_the_served_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, judge = _identity_workspace(tmp_path, monkeypatch)
    before = _head(root)

    actual = _observe(root, ["curate", "--auto"], since=before)

    _settle("identity_cost_line_served", actual)
    assert judge.asked == 1


def test_identity_model_disclosure_names_a_keyed_stage_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _identity_workspace(tmp_path, monkeypatch)
    _edit_config(root, append="models:\n  adjudication: other-model:1b\n")
    config_text = (root / "openkos.yaml").read_text(encoding="utf-8")
    assert "adjudication: other-model:1b" in config_text
    before = _head(root)

    actual = _observe(root, ["curate", "--auto"], since=before)

    _settle("identity_model_disclosure", actual)


def test_declining_the_gate_with_real_probes_spends_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, judge = _identity_workspace(tmp_path, monkeypatch)
    before = _head(root)

    actual = _observe(root, ["curate"], since=before, input="n\n")

    _settle("identity_gate_declined", actual)
    assert judge.asked == 0


# --------------------------------------------------------------------------- #
# Partial batch for Structure and Metadata (S0a.5)
# --------------------------------------------------------------------------- #


def test_structure_partial_batch_reports_the_failure_after_the_first_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _structure_workspace(
        tmp_path,
        monkeypatch,
        [(_FOO_BAR, "references"), (_FOO2_BAR, "references")],
    )
    first = EdgeSuggestion(
        edge=Edge(source_id=_FOO_BAR[0], target_id=_FOO_BAR[1]),
        suggested_type="references",
        rationale="stub rationale",
    )
    monkeypatch.setattr(
        "openkos.cli.curate.suggest_edge_types",
        lambda to_type, **k: EdgeSuggestionBatch(
            results=[first],
            failure=BackendUnavailable("connection refused"),
            failed_index=2,
        ),
    )
    _answers(monkeypatch, "y")
    before = _head(root)

    actual = _observe(root, ["curate", "--auto", "--structure"], since=before)

    _settle("structure_partial_batch", actual)


def test_metadata_partial_batch_reports_the_failure_after_the_first_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _workspace(
        tmp_path, monkeypatch, [_FOO], {_FOO.member_ids: _DIFFERENT}
    )
    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(),
    )
    monkeypatch.setattr(
        "openkos.cli.curate._concept_type_names",
        lambda *a, **k: ["Concept", "Entity"],
    )
    monkeypatch.setattr(
        "openkos.cli.curate.suggest_volatility",
        lambda bundle_dir, **k: TierSuggestionBatch(
            results=[
                TierSuggestion(
                    type_name="Concept",
                    current_default="static",
                    suggested_tier="volatile",
                    rationale="stub rationale",
                )
            ],
            failure=BackendUnavailable("connection refused"),
        ),
    )
    _simulate_tty(monkeypatch)
    _answers(monkeypatch, "y")
    before = _head(root)

    actual = _observe(root, ["curate", "--auto"], since=before)

    _settle("metadata_partial_batch", actual)


# --------------------------------------------------------------------------- #
# Adjudications persist: fail-open and busy advisories (S0a.6)
# --------------------------------------------------------------------------- #


def _break_adjudication_store(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr("openkos.state.adjudications.record_adjudications", _boom)


def _adjudicate_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    before_returning: Callable[[], object] | None = None,
) -> Path:
    """A one-group workspace for the standalone verb, which stubs its own
    candidate finder and judge in `cli.main`."""
    root, _judge = _workspace(
        tmp_path, monkeypatch, [_FOO], {_FOO.member_ids: _DIFFERENT}
    )
    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(groups=(_FOO,), produced=1, retained=1),
    )

    def _judge_stub(candidates: list[CandidateGroup], **k: object) -> AdjudicationBatch:
        if before_returning is not None:
            before_returning()
        return AdjudicationBatch(
            results=[
                AdjudicatedCandidate(
                    candidate=candidates[0],
                    verdict=Verdict.DIFFERENT,
                    confidence=0.95,
                    rationale="stub",
                )
            ]
        )

    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _judge_stub)
    return root


def test_adjudicate_persist_failure_is_an_advisory_not_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _adjudicate_workspace(tmp_path, monkeypatch)
    _break_adjudication_store(monkeypatch)
    before = _head(root)

    actual = _observe(root, ["adjudicate"], since=before)

    _settle("adjudicate_persist_failure", actual)


def test_adjudicate_busy_workspace_costs_the_persist_an_advisory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    acquired: list[bool] = []
    with contextlib.ExitStack() as stack:
        root = _adjudicate_workspace(
            tmp_path,
            monkeypatch,
            before_returning=hold_the_lock_from(stack, tmp_path / "ws", acquired),
        )
        before = _head(root)

        actual = _observe(root, ["adjudicate"], since=before)

    assert acquired == [True]
    _settle("adjudicate_persist_busy", actual)


def test_curate_persist_failure_is_an_advisory_not_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _workspace(tmp_path, monkeypatch, [_FOO], _BOTH_DIFFERENT)
    _simulate_tty(monkeypatch)
    _break_adjudication_store(monkeypatch)
    before = _head(root)

    actual = _observe(root, ["curate", "--auto"], since=before)

    _settle("curate_persist_failure", actual)


def test_curate_busy_workspace_costs_the_persist_an_advisory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    acquired: list[bool] = []
    with contextlib.ExitStack() as stack:
        holder: list[Callable[[], object]] = []

        def _take_the_lock() -> None:
            holder[0]()

        judge = _Judge(_BOTH_DIFFERENT, before_returning=_take_the_lock)
        root, _ = _build(tmp_path, monkeypatch, [_FOO], _BOTH_DIFFERENT, judge=judge)
        holder.append(hold_the_lock_from(stack, root, acquired))
        _simulate_tty(monkeypatch)
        before = _head(root)

        actual = _observe(root, ["curate", "--auto"], since=before)

    assert acquired == [True]
    _settle("curate_persist_busy", actual)


# --------------------------------------------------------------------------- #
# Edge-suggestion persist: fail-open and busy advisories (S0a.7)
# --------------------------------------------------------------------------- #


def _break_edge_suggestion_store(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr("openkos.state.edge_suggestions.record_edge_suggestions", _boom)


def _suggest_relations_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    before_returning: Callable[[], object] | None = None,
) -> Path:
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="B")
    edge = Edge(source_id="concepts/a", target_id="concepts/b")
    monkeypatch.setattr(
        "openkos.cli.main.candidate_edges", lambda bundle_dir, **kwargs: [edge]
    )

    def _typed(edges: list[Edge], **kwargs: Any) -> EdgeSuggestionBatch:
        if before_returning is not None:
            before_returning()
        return EdgeSuggestionBatch(
            results=[
                EdgeSuggestion(
                    edge=e, suggested_type="references", rationale="stub rationale"
                )
                for e in edges
            ]
        )

    monkeypatch.setattr("openkos.cli.main.suggest_edge_types", _typed)
    return tmp_path


def test_suggest_relations_persist_failure_is_an_advisory_not_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _suggest_relations_workspace(tmp_path, monkeypatch)
    _break_edge_suggestion_store(monkeypatch)
    before = _git(root, "rev-parse", "HEAD").strip()

    actual = _observe(root, ["suggest-relations", "--auto"], since=before)

    _settle("suggest_relations_persist_failure", actual)


def test_suggest_relations_busy_workspace_costs_the_persist_an_advisory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    acquired: list[bool] = []
    with contextlib.ExitStack() as stack:
        root = _suggest_relations_workspace(
            tmp_path,
            monkeypatch,
            before_returning=hold_the_lock_from(stack, tmp_path, acquired),
        )
        before = _git(root, "rev-parse", "HEAD").strip()

        actual = _observe(root, ["suggest-relations", "--auto"], since=before)

    assert acquired == [True]
    _settle("suggest_relations_persist_busy", actual)


def test_structure_persist_failure_is_an_advisory_not_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _structure_workspace(tmp_path, monkeypatch, [(_FOO_BAR, "references")])
    _break_edge_suggestion_store(monkeypatch)
    _answers(monkeypatch, "n")
    before = _head(root)

    actual = _observe(root, ["curate", "--auto", "--structure"], since=before)

    _settle("structure_persist_failure", actual)


def test_structure_busy_workspace_costs_the_persist_an_advisory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    acquired: list[bool] = []
    with contextlib.ExitStack() as stack:
        root = _structure_workspace(tmp_path, monkeypatch, [(_FOO_BAR, "references")])
        suggestion = EdgeSuggestion(
            edge=Edge(source_id=_FOO_BAR[0], target_id=_FOO_BAR[1]),
            suggested_type="references",
            rationale="stub rationale",
        )
        take = hold_the_lock_from(stack, root, acquired)

        def _typed(to_type: object, **k: object) -> EdgeSuggestionBatch:
            take()
            return EdgeSuggestionBatch(results=[suggestion])

        monkeypatch.setattr("openkos.cli.curate.suggest_edge_types", _typed)
        _answers(monkeypatch, "n")
        before = _head(root)

        actual = _observe(root, ["curate", "--auto", "--structure"], since=before)

    assert acquired == [True]
    _settle("structure_persist_busy", actual)


# --------------------------------------------------------------------------- #
# the recorded set is exactly the tested set
# --------------------------------------------------------------------------- #


def test_every_recorded_scenario_has_a_test() -> None:
    if _RECORD:
        return
    recorded = set(json.loads(_GOLDENS.read_text(encoding="utf-8")))
    assert recorded == {
        "identity_cost_line_served",
        "identity_model_disclosure",
        "identity_gate_declined",
        "structure_partial_batch",
        "metadata_partial_batch",
        "adjudicate_persist_failure",
        "adjudicate_persist_busy",
        "curate_persist_failure",
        "curate_persist_busy",
        "suggest_relations_persist_failure",
        "suggest_relations_persist_busy",
        "structure_persist_failure",
        "structure_persist_busy",
    }
