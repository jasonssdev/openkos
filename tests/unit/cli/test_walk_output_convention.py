"""Interactive walks render one visually separated block per item (ADR-0042).

On a terminal each item is a block (header, rationale, prompt) set off by a
blank line, and a long rationale is wrapped under a hanging indent. Piped, the
walk prints exactly what it always did.
"""

from pathlib import Path

import pytest

from openkos.cli.main import app
from openkos.resolution.candidates import CandidateGroupReport
from openkos.resolution.volatility_typing import TierSuggestion, TierSuggestionBatch
from tests.unit.cli import test_adjudicate as adjudicate_tests
from tests.unit.cli import test_curate as curate_tests
from tests.unit.cli.conftest import seed_workspace_docs
from tests.unit.cli.test_adjudicate import runner

_LONG_RATIONALE = " ".join(["because"] * 40)


def _adjudicate_apply(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    *,
    tty: bool,
) -> str:
    adjudicate_tests._init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _, fake_find, fake_adjudicate = adjudicate_tests._seed_one_same_group(tmp_path)
    monkeypatch.setattr("openkos.cli.main.find_candidates_report", fake_find)
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", fake_adjudicate)
    if tty:
        adjudicate_tests._simulate_tty(monkeypatch)
    result = runner.invoke(app, ["adjudicate", "--apply"], input="n\n")
    assert result.exit_code == 0
    return result.stdout


def test_adjudicate_apply_item_block_is_set_off_on_a_terminal(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = _adjudicate_apply(tmp_path, tmp_path_factory, monkeypatch, tty=True)
    assert "\n\n  merge concepts/b into concepts/a" in "\n" + out


def test_adjudicate_apply_piped_has_no_separator_line(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = _adjudicate_apply(tmp_path, tmp_path_factory, monkeypatch, tty=False)
    assert "\n\n" not in "\n" + out.rstrip("\n")


def _metadata_walk(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> str:
    curate_tests._init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    curate_tests._write_doc(
        tmp_path / "bundle" / "concepts" / "a.md", title="Concept A"
    )
    curate_tests._reindexed_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(),
    )
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: [])
    monkeypatch.setattr(
        "openkos.cli.curate._concept_type_names", lambda *a, **k: ["Concept"]
    )
    monkeypatch.setattr(
        "openkos.cli.curate._contradiction_plan",
        lambda *a, **k: curate_tests._empty_plan(),
    )
    suggestion = TierSuggestion(
        type_name="Concept",
        current_default="static",
        suggested_tier="volatile",
        rationale=_LONG_RATIONALE,
    )
    monkeypatch.setattr(
        "openkos.cli.curate.suggest_volatility",
        lambda *a, **k: TierSuggestionBatch(results=[suggestion]),
    )
    curate_tests._simulate_tty(monkeypatch)
    result = runner.invoke(app, ["curate"], input="y\nn\n")
    assert result.exit_code == 0
    return result.stdout


def test_curate_metadata_item_is_a_block_with_a_wrapped_rationale(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = _metadata_walk(tmp_path, tmp_path_factory, monkeypatch)
    assert "\n\n[volatile] Concept\n  rationale: because" in "\n" + out
    rationale_lines = [
        line
        for line in out.splitlines()
        if "because" in line and "Set Concept" not in line
    ]
    assert len(rationale_lines) > 1
    assert all(len(line) <= 80 for line in rationale_lines)
    assert all(line.startswith("    ") for line in rationale_lines[1:])


def test_curate_structure_item_is_a_block_with_a_wrapped_rationale(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openkos.graph.base import Edge
    from openkos.resolution.edge_typing import EdgeSuggestion, EdgeSuggestionBatch

    curate_tests._init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    curate_tests._write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="A")
    curate_tests._write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="B")
    curate_tests._reindexed_workspace(tmp_path, monkeypatch)
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
                EdgeSuggestion(
                    edge=edge, suggested_type="references", rationale=_LONG_RATIONALE
                )
            ]
        ),
    )
    monkeypatch.setattr("openkos.cli.curate._concept_type_names", lambda *a, **k: [])
    monkeypatch.setattr(
        "openkos.cli.curate._contradiction_plan",
        lambda *a, **k: curate_tests._empty_plan(),
    )
    curate_tests._simulate_tty(monkeypatch)

    result = runner.invoke(app, ["curate"], input="y\nn\n")

    assert result.exit_code == 0
    assert "\n\n[references] concepts/a -> concepts/b\n  rationale: because" in (
        "\n" + result.stdout
    )
    rationale_lines = [
        line
        for line in result.stdout.splitlines()
        if "because" in line and "Relate" not in line
    ]
    assert len(rationale_lines) > 1
    assert all(len(line) <= 80 for line in rationale_lines)


def test_curate_identity_item_is_set_off_on_a_terminal(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    curate_tests._stub_later_stages_empty(monkeypatch)
    curate_tests._init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    curate_tests._write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="A")
    curate_tests._write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="B")
    seed_workspace_docs(tmp_path)
    curate_tests._reindexed_workspace(tmp_path, monkeypatch)
    curate_tests._seed_identity_pair(tmp_path, monkeypatch)
    curate_tests._simulate_tty(monkeypatch)

    result = runner.invoke(app, ["curate"], input="y\nn\n")

    assert result.exit_code == 0
    assert "\n\n  merge concepts/b into concepts/a" in "\n" + result.stdout
