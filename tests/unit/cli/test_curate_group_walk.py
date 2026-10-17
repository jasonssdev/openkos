"""`openkos curate`'s Identity group walk (#1438, ADR-0063).

A judged-SAME group of more than two members, and a cross-type pair, is walked
through the decision surface's group commit (`commit_merge_group_decision`), the
path the app uses: the person marks the members that are the same, says what a
cross-type thing is, and picks the member that keeps its name. No test reaches a
model: the candidate finder and the judge are stubs.
"""

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from openkos import config
from openkos.application import curate_decisions, curate_queue
from openkos.application.write_gate import DriftDetected, MergeGroupInterrupted
from openkos.cli.main import app
from openkos.model import okf
from openkos.resolution.adjudication import (
    AdjudicatedCandidate,
    AdjudicationBatch,
    Verdict,
)
from openkos.resolution.candidates import CandidateGroup, CandidateGroupReport, Tier
from openkos.state import derived
from openkos.state import pending_queue as pq
from tests.unit.cli.conftest import seed_workspace_docs
from tests.unit.cli.test_curate import (
    _init_apply_workspace,
    _lines,
    _reindexed_workspace,
    _simulate_tty,
    _stub_later_stages_empty,
)

runner = CliRunner()

_THREE = ("concepts/a", "concepts/b", "concepts/c")


def _doc(root: Path, concept_id: str, *, doc_type: str, title: str, body: str) -> None:
    path = root / "bundle" / f"{concept_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: {doc_type}\ntitle: {title}\nprovenance:\n  - sources/s1\n"
        f"---\n# {title}\n{body}\n",
        encoding="utf-8",
    )


def _build(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    docs: dict[str, str],
    *,
    okf_type: str = "Concept",
    member_types: tuple[str, ...] | None = None,
) -> CandidateGroup:
    """A workspace holding `docs` (Concept ID -> OKF type) and a pending-work
    queue, one judged-SAME candidate group over all of them, and a terminal."""
    _stub_later_stages_empty(monkeypatch)
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    for concept_id, doc_type in docs.items():
        name = concept_id.rsplit("/", 1)[-1].upper()
        _doc(tmp_path, concept_id, doc_type=doc_type, title=name, body=f"About {name}.")
    seed_workspace_docs(tmp_path)
    _reindexed_workspace(tmp_path, monkeypatch)
    # The queue a producer pass (the daemon, the app's refresh) creates: the
    # group decision commits against the group's open row.
    conn = derived.open_derived_connection(
        config.WorkspaceLayout(tmp_path).findings_db_path
    )
    try:
        pq.ensure_schema(conn)
    finally:
        conn.close()
    group = CandidateGroup(
        okf_type=okf_type,
        member_ids=tuple(sorted(docs)),
        tier=Tier.HIGH,
        trigger="stub",
        **({} if member_types is None else {"member_types": member_types}),
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
    return group


def _three(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> CandidateGroup:
    return _build(
        tmp_path,
        tmp_path_factory,
        monkeypatch,
        dict.fromkeys(_THREE, "Concept"),
    )


def _exists(root: Path, concept_id: str) -> bool:
    return (root / "bundle" / f"{concept_id}.md").is_file()


def _spy(monkeypatch: pytest.MonkeyPatch, name: str) -> list[Any]:
    """Record every call to `curate_decisions.<name>` and its outcome."""
    calls: list[Any] = []
    real = getattr(curate_decisions, name)

    def _wrapped(*args: Any, **kwargs: Any) -> Any:
        outcome = real(*args, **kwargs)
        calls.append((args, kwargs, outcome))
        return outcome

    monkeypatch.setattr(curate_decisions, name, _wrapped)
    return calls


def test_two_marked_of_three_merge_through_the_group_commit(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _three(tmp_path, tmp_path_factory, monkeypatch)
    commits = _spy(monkeypatch, "commit_merge_group_decision")

    # cost gate, mark 1 and 2, name from 1, consent.
    result = runner.invoke(app, ["curate"], input="y\n1 2\n1\ny\n")

    assert result.exit_code == 0, result.output
    out = result.stdout
    assert "  1. concepts/a -- A (Concept)" in _lines(out)
    assert "Which of these are the same?" in out
    assert "Which one keeps its name?" in out
    assert "    merge concepts/b (Concept) into concepts/a" in _lines(out)
    assert "  kept distinct: concepts/c" in _lines(out)
    assert "Combine 2 member(s) into concepts/a? [y]es / [s]kip" in out
    assert "skipped (N>2, merge manually)" not in out
    assert len(commits) == 1
    _args, kwargs, outcome = commits[0]
    assert kwargs["expected"].selection == curate_decisions.GroupSelection(
        "concepts/a", ("concepts/a", "concepts/b")
    )
    assert isinstance(outcome, curate_decisions.Applied)
    assert outcome.merged == (("concepts/a", "concepts/b"),)
    assert outcome.kept_distinct == (("concepts/a", "concepts/c"),)
    assert _exists(tmp_path, "concepts/a")
    assert not _exists(tmp_path, "concepts/b")
    assert _exists(tmp_path, "concepts/c")
    assert "Identity: applied 1, skipped 0." in _lines(out)


def test_the_survivor_is_the_one_the_person_names(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _three(tmp_path, tmp_path_factory, monkeypatch)

    result = runner.invoke(app, ["curate"], input="y\na\n3\ny\n")

    assert result.exit_code == 0, result.output
    assert _exists(tmp_path, "concepts/c")
    assert not _exists(tmp_path, "concepts/a")
    assert not _exists(tmp_path, "concepts/b")
    assert "Combine 3 member(s) into concepts/c?" in result.stdout
    assert "kept distinct:" not in result.stdout


def test_distinct_keeps_the_whole_group_separate(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _three(tmp_path, tmp_path_factory, monkeypatch)
    kept = _spy(monkeypatch, "commit_keep_distinct")
    commits = _spy(monkeypatch, "commit_merge_group_decision")

    result = runner.invoke(app, ["curate"], input="y\nd\n")

    assert result.exit_code == 0, result.output
    assert commits == []
    assert [tuple(call[0][2]) for call in kept] == [_THREE]
    assert all(_exists(tmp_path, m) for m in _THREE)
    assert "  declined: concepts/a, concepts/b, concepts/c" in _lines(result.stdout)


@pytest.mark.parametrize("answers", ["y\n\n", "y\n  \n", "y\n1 2\n\n", "y\n1 2\n1\n\n"])
def test_enter_at_any_question_writes_nothing(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    answers: str,
) -> None:
    _three(tmp_path, tmp_path_factory, monkeypatch)
    kept = _spy(monkeypatch, "commit_keep_distinct")
    commits = _spy(monkeypatch, "commit_merge_group_decision")

    result = runner.invoke(app, ["curate"], input=answers)

    assert result.exit_code == 0, result.output
    assert commits == []
    assert kept == []
    assert all(_exists(tmp_path, m) for m in _THREE)
    assert "Identity: applied 0, skipped 1." in _lines(result.stdout)


@pytest.mark.parametrize("bad", ["1", "1 1", "4 1", "x", "0 2"])
def test_a_marking_that_names_fewer_than_two_members_is_asked_again(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    bad: str,
) -> None:
    _three(tmp_path, tmp_path_factory, monkeypatch)

    result = runner.invoke(app, ["curate"], input=f"y\n{bad}\ns\n")

    assert result.exit_code == 0, result.output
    assert f"Unrecognized answer '{bad}' -- expected" in result.stdout
    assert all(_exists(tmp_path, m) for m in _THREE)


def test_a_name_outside_the_marked_members_is_asked_again(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _three(tmp_path, tmp_path_factory, monkeypatch)

    result = runner.invoke(app, ["curate"], input="y\n1 2\n3\n2\ny\n")

    assert result.exit_code == 0, result.output
    assert "Unrecognized answer '3' -- expected" in result.stdout
    assert _exists(tmp_path, "concepts/b")
    assert not _exists(tmp_path, "concepts/a")


def test_a_cross_type_pair_asks_what_it_is_and_that_member_survives(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _build(
        tmp_path,
        tmp_path_factory,
        monkeypatch,
        {"events/m1": "Event", "projects/m1": "Project"},
        okf_type="Event+Project",
        member_types=("Event", "Project"),
    )
    commits = _spy(monkeypatch, "commit_merge_group_decision")

    # cost gate, "It is an Event", consent. No marking and no name question.
    result = runner.invoke(app, ["curate"], input="y\n1\ny\n")

    assert result.exit_code == 0, result.output
    out = result.stdout
    assert "Which of these are the same?" not in out
    assert "What is M1? 1) Event  2) Project" in out
    assert "Which one keeps its name?" not in out
    assert "note: cross-type SAME" in out
    assert "skipped (cross-type SAME" not in out
    assert len(commits) == 1
    assert _exists(tmp_path, "events/m1")
    assert not _exists(tmp_path, "projects/m1")
    metadata, _ = okf.load_frontmatter(
        (tmp_path / "bundle" / "events" / "m1.md").read_text(encoding="utf-8")
    )
    assert metadata[okf.TYPE_ALTERNATIVE_KEY] == "Project"
    assert "-- its type is recorded as type_alternative" in out


def test_two_members_of_the_chosen_type_ask_which_keeps_its_name(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _build(
        tmp_path,
        tmp_path_factory,
        monkeypatch,
        {"events/x": "Event", "projects/x": "Project", "projects/y": "Project"},
        okf_type="Event+Project",
        member_types=("Event", "Project", "Project"),
    )

    # cost gate, all, "It is a Project", name from projects/y (3), consent.
    result = runner.invoke(app, ["curate"], input="y\na\n2\n3\ny\n")

    assert result.exit_code == 0, result.output
    assert "Which one keeps its name? 2 or 3" in result.stdout
    assert _exists(tmp_path, "projects/y")
    assert not _exists(tmp_path, "projects/x")
    assert not _exists(tmp_path, "events/x")


def test_the_engine_never_offers_a_member_of_another_type_as_the_name(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _build(
        tmp_path,
        tmp_path_factory,
        monkeypatch,
        {"events/x": "Event", "projects/x": "Project", "projects/y": "Project"},
        okf_type="Event+Project",
        member_types=("Event", "Project", "Project"),
    )

    result = runner.invoke(app, ["curate"], input="y\na\n2\n1\ns\n")

    assert result.exit_code == 0, result.output
    assert "Unrecognized answer '1' -- expected" in result.stdout


def test_a_row_that_does_not_offer_the_group_keeps_the_skip_line(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _three(tmp_path, tmp_path_factory, monkeypatch)
    monkeypatch.setattr(curate_queue, "enqueue_identity", lambda *a, **k: None)

    result = runner.invoke(app, ["curate"], input="y\n")

    assert result.exit_code == 0, result.output
    assert "skipped (N>2, merge manually)" in result.stdout
    assert "openkos merge concepts/a concepts/b" in result.stdout
    assert "Which of these are the same?" not in result.stdout
    assert all(_exists(tmp_path, m) for m in _THREE)


def test_a_drift_at_commit_exits_three(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _three(tmp_path, tmp_path_factory, monkeypatch)

    def _drift(*_a: Any, **_k: Any) -> Any:
        raise DriftDetected("openkos curate: concepts/b changed since the preview")

    monkeypatch.setattr(curate_decisions, "commit_merge_group_decision", _drift)

    result = runner.invoke(app, ["curate"], input="y\na\n1\ny\n")

    assert result.exit_code == 3
    assert "concepts/b changed since the preview" in result.stderr


def test_a_group_cut_short_names_what_it_kept_and_exits_one(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _three(tmp_path, tmp_path_factory, monkeypatch)

    def _interrupted(*_a: Any, **_k: Any) -> Any:
        raise MergeGroupInterrupted(
            (("concepts/a", "concepts/b"),), "abc1234", "concepts/c", "disk full"
        )

    monkeypatch.setattr(curate_decisions, "commit_merge_group_decision", _interrupted)

    result = runner.invoke(app, ["curate"], input="y\na\n1\ny\n")

    assert result.exit_code == 1
    assert (
        "openkos curate: Identity: stopped combining into concepts/a after "
        "merging concepts/b -- failed while merging concepts/c (disk full); "
        "the merges written are kept, each reversible with `openkos unmerge`."
    ) in result.stderr


def test_a_stale_commit_is_counted_as_skipped(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _three(tmp_path, tmp_path_factory, monkeypatch)
    monkeypatch.setattr(
        curate_decisions,
        "commit_merge_group_decision",
        lambda *a, **k: curate_decisions.Stale("k", "content_changed"),
    )

    result = runner.invoke(app, ["curate"], input="y\na\n1\ny\n")

    assert result.exit_code == 0, result.output
    assert (
        "openkos curate: Identity: skipped concepts/a, concepts/b, concepts/c "
        "-- not combined (content_changed)."
    ) in result.stderr
    assert "Identity: applied 0, skipped 1." in _lines(result.stdout)


def test_two_names_for_one_survivor_are_asked_again(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _three(tmp_path, tmp_path_factory, monkeypatch)

    result = runner.invoke(app, ["curate"], input="y\n1 2\n1 2\n2\ny\n")

    assert result.exit_code == 0, result.output
    assert "Unrecognized answer '1 2' -- expected" in result.stdout
    assert _exists(tmp_path, "concepts/b")
    assert not _exists(tmp_path, "concepts/a")


def test_without_a_queue_the_walk_creates_one_and_asks(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The group decision commits against its open row, so a group the walk
    must ask about creates the queue a producer pass has not (#1489) instead of
    printing the manual `openkos merge` commands."""
    _three(tmp_path, tmp_path_factory, monkeypatch)
    findings = config.WorkspaceLayout(tmp_path).findings_db_path
    findings.unlink()

    result = runner.invoke(app, ["curate"], input="y\ns\n")

    assert result.exit_code == 0, result.output
    assert "Which of these are the same?" in result.stdout
    assert "skipped (N>2, merge manually)" not in result.stdout
    assert curate_queue.queue_present(config.WorkspaceLayout(tmp_path))


def test_without_a_queue_a_pair_that_needs_no_walk_creates_none(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The absent-queue contract holds for every group the walk is not
    needed for: a same-type pair merges without a queue row."""
    _build(
        tmp_path,
        tmp_path_factory,
        monkeypatch,
        {"concepts/a": "Concept", "concepts/b": "Concept"},
    )
    config.WorkspaceLayout(tmp_path).findings_db_path.unlink()

    result = runner.invoke(app, ["curate"], input="y\nn\n")

    assert result.exit_code == 0, result.output
    assert not curate_queue.queue_present(config.WorkspaceLayout(tmp_path))
