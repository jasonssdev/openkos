"""A persisted DIFFERENT adjudication settles the identity row it answers
(#1226): `adjudicate` and `curate` persist verdicts through
`cli.main._persist_adjudications`, which closes the matching open row so
`openkos pending` stops offering a pair the model already judged distinct.
"""

import json
from pathlib import Path

import pytest

from openkos.cli import main
from openkos.resolution.adjudication import AdjudicatedCandidate, Verdict
from openkos.resolution.candidates import CandidateGroup, Tier
from tests.unit.application.test_queue_resolution import (
    _concept,
    _identity_row,
    _init,
    _items,
    _only,
    _seed,
)
from tests.unit.cli.conftest import pinned_git_identity as pinned_git_identity

_MEMBERS = ("concepts/a", "concepts/b")


def _result(verdict: Verdict) -> AdjudicatedCandidate:
    group = CandidateGroup(
        okf_type="Concept", member_ids=_MEMBERS, tier=Tier.HIGH, trigger="alpha"
    )
    return AdjudicatedCandidate(
        candidate=group, verdict=verdict, confidence=0.9, rationale="Judged."
    )


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = _init(tmp_path, monkeypatch)
    for member in _MEMBERS:
        _concept(root, member)
    _seed(root, _identity_row(_MEMBERS))
    return root


def _persist(root: Path, verdict: Verdict) -> None:
    from openkos import config

    main._persist_adjudications(
        config.WorkspaceLayout(root), [_result(verdict)], include_confidential=False
    )


def test_a_different_verdict_declines_the_open_identity_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned_git_identity: None
) -> None:
    root = _setup(tmp_path, monkeypatch)

    _persist(root, Verdict.DIFFERENT)

    row = _only(root)
    assert (row.status, row.resolution) == ("declined", "declined")


@pytest.mark.parametrize("verdict", [Verdict.SAME, Verdict.UNCERTAIN])
def test_a_verdict_other_than_different_leaves_the_row_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pinned_git_identity: None,
    verdict: Verdict,
) -> None:
    root = _setup(tmp_path, monkeypatch)

    _persist(root, verdict)

    assert [i.status for i in _items(root)] == ["pending"]


def test_a_different_verdict_never_touches_another_groups_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned_git_identity: None
) -> None:
    root = _setup(tmp_path, monkeypatch)
    other = ("concepts/c", "concepts/d")
    _concept(root, other[0])
    _concept(root, other[1])
    _seed(root, _identity_row(other))

    _persist(root, Verdict.DIFFERENT)

    statuses = {
        tuple(json.loads(i.payload)["member_ids"]): i.status for i in _items(root)
    }
    assert statuses == {_MEMBERS: "declined", other: "pending"}


def test_curate_does_not_enqueue_a_group_it_just_judged_different(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned_git_identity: None
) -> None:
    import contextlib

    from openkos import config
    from openkos.application import curate_queue

    root = _init(tmp_path, monkeypatch)
    for member in _MEMBERS:
        _concept(root, member)
    other = ("concepts/c", "concepts/d")
    _seed(root, _identity_row(other))  # the queue exists
    result = _result(Verdict.DIFFERENT)

    curate_queue.enqueue_identity(
        config.WorkspaceLayout(root),
        [result.candidate],
        [result],
        pinned={},
        commit_section=contextlib.nullcontext,
    )

    assert [tuple(i.targets) for i in _items(root)] == [other]
