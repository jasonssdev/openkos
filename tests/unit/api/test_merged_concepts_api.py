"""`openkos.api.merged_concepts` (`review-undo` change, #1467): every combined
page is listed through the public facade, over merges made through the decision
surface itself, with no lock, no model client and no write."""

from pathlib import Path

import pytest

from openkos import lock
from openkos.api import (
    GroupSelection,
    MergedConcept,
    MergeRecord,
    Workspace,
    merge_history,
    merged_concepts,
)
from tests.unit.api import support
from tests.unit.api.test_unmerge_api import A, B, C, _apply, _workspace


def test_a_combined_group_is_listed_with_its_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch, (A, B, C))
    _apply(root, (A, B, C), GroupSelection(survivor_id=A, member_ids=(A, B, C)))
    before = support.snapshot(root)

    # The lock is held: a read that took it would raise busy.
    with lock.workspace_lock(root):
        listed = merged_concepts(Workspace.open(root))

    assert listed == (
        MergedConcept(concept_id=A, merges=merge_history(Workspace.open(root), A)),
    )
    assert [type(record) for record in listed[0].merges] == [MergeRecord] * 2
    assert [record.absorbed_id for record in listed[0].merges] == [C, B]
    assert support.snapshot(root) == before


def test_a_workspace_with_no_combine_lists_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch, (A, B))

    assert merged_concepts(Workspace.open(root)) == ()
