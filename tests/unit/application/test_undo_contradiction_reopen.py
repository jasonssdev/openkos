"""Reopening a declined contradiction through `openkos.application.undo_service`
(`undo-findings` change, ADR-0067, ADR-0074): listed, previewed with a version
over the record's sidecar, and applied under one lock, called without the CLI.

Every record is written by `contradictions_service.apply_contradiction_decision`,
the writer a decline decision and `contradictions --decline` both use.
"""

import contextlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from openkos import config, lock
from openkos.application import contradictions_service, curate_decisions, lock_wait
from openkos.application import pending as application_pending
from openkos.application import undo_service as svc
from openkos.bundle import decisions as bundle_decisions
from openkos.lock import WorkspaceBusyError
from tests.unit.application.curation_support import (
    CommitRecorder,
    make_workspace,
    tree,
    write_concept,
)

A, B, C, D = "concepts/alpha", "concepts/bravo", "concepts/charlie", "concepts/delta"


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = make_workspace(tmp_path, monkeypatch)
    for concept_id in (A, B, C, D):
        write_concept(root, concept_id, title=concept_id.rsplit("/", 1)[1].title())
    return root


def _rule(
    root: Path,
    pair: tuple[str, str],
    merged: str | None = None,
    state: str = "declined",
    hour: int = 9,
) -> None:
    contradictions_service.apply_contradiction_decision(
        config.WorkspaceLayout(root),
        pair,
        merged,
        target_state=state,  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 10, 17, hour, tzinfo=UTC),
    )


def _ports(root: Path, recorder: CommitRecorder) -> curate_decisions.DecisionPorts:
    return curate_decisions.DecisionPorts(
        autocommit=recorder,
        commit_section=lock_wait.locked_commit_section(root, wait_seconds=0.0),
    )


def _preview(
    root: Path, first: str, second: str, merged: str | None = None
) -> svc.ContradictionReopenPlan:
    plan = svc.preview_reopen_contradiction(root, first, second, merged)
    assert isinstance(plan, svc.ContradictionReopenPlan), plan
    return plan


def _declined(root: Path, pair: tuple[str, str], merged: str | None = None) -> bool:
    return application_pending.is_contradiction_declined(
        config.WorkspaceLayout(root), pair, merged
    )


def _apply(
    root: Path, plan: svc.ContradictionReopenPlan, recorder: CommitRecorder
) -> object:
    return svc.apply_reopen_contradiction(
        root,
        plan.first_id,
        plan.second_id,
        plan.merged_absorbed_id,
        plan.content_digest,
        ports=_ports(root, recorder),
    )


# -- the list ----------------------------------------------------------------- #


def test_declined_contradictions_are_listed_newest_first(root: Path) -> None:
    _rule(root, (A, B), hour=9)
    _rule(root, (B, C), hour=11)
    _rule(root, (A, B), merged=C, hour=10)
    _rule(root, (A, C), state="open", hour=12)
    before = tree(root)

    listed = svc.declined_contradictions(root)

    assert [
        (r.first_id, r.second_id, r.merged_absorbed_id, r.decided_at) for r in listed
    ] == [
        (B, C, None, "2026-10-17T11:00:00+00:00"),
        (A, B, C, "2026-10-17T10:00:00+00:00"),
        (A, B, None, "2026-10-17T09:00:00+00:00"),
    ]
    assert tree(root) == before
    assert not (root / ".openkos" / "findings.db").exists()


def test_declines_recorded_at_the_same_time_are_ordered_by_pair(root: Path) -> None:
    for pair, merged in (
        ((B, C), None),
        ((A, B), C),
        ((A, B), None),
        ((A, D), None),
    ):
        _rule(root, pair, merged)

    assert [
        (r.first_id, r.second_id, r.merged_absorbed_id)
        for r in svc.declined_contradictions(root)
    ] == [(A, B, None), (A, B, C), (A, D, None), (B, C, None)]


def test_nothing_declined_lists_nothing(root: Path) -> None:
    assert svc.declined_contradictions(root) == ()


# -- preview ------------------------------------------------------------------ #


def test_a_declined_pair_previews_in_either_order_with_one_version(
    root: Path,
) -> None:
    _rule(root, (A, B))
    before = tree(root)

    first = _preview(root, B, A)
    second = _preview(root, A, B)

    assert (first.first_id, first.second_id, first.merged_absorbed_id) == (A, B, None)
    assert first == second
    assert tree(root) == before


def test_a_merged_body_decline_previews_with_its_discriminator(root: Path) -> None:
    _rule(root, (A, B), merged=C)

    plan = _preview(root, A, B, C)

    assert plan.merged_absorbed_id == C
    assert plan.content_digest != ""


@pytest.mark.parametrize(
    ("first", "second", "merged"),
    [
        (A, C, None),
        (A, B, C),
        (A, A, None),
        (A, "../escape", None),
        ("/abs", B, None),
        (A, B, "../escape"),
    ],
)
def test_no_declined_record_for_exactly_this_finding_is_refused(
    root: Path, first: str, second: str, merged: str | None
) -> None:
    _rule(root, (A, B))
    before = tree(root)

    assert svc.preview_reopen_contradiction(
        root, first, second, merged
    ) == svc.UndoRefusal("not_declined")
    assert tree(root) == before


def test_a_merged_body_decline_over_one_page_named_twice_is_reopened(
    root: Path,
) -> None:
    """A contradiction found inside a combined page names that page twice, with
    the absorbed page as the discriminator; its decline is reopened like any
    other."""
    _rule(root, (A, A), merged=B)
    plan = _preview(root, A, A, B)
    recorder = CommitRecorder()

    done = _apply(root, plan, recorder)

    assert done == svc.ContradictionReopened(
        first_id=A, second_id=A, merged_absorbed_id=B, commit_sha="abc1234"
    )
    assert not _declined(root, (A, A), B)
    assert svc.declined_contradictions(root) == ()


def test_only_the_typed_edge_decline_does_not_cover_the_merged_body_one(
    root: Path,
) -> None:
    _rule(root, (A, B), merged=C)

    assert svc.preview_reopen_contradiction(root, A, B, None) == svc.UndoRefusal(
        "not_declined"
    )


def test_a_reopened_record_is_refused(root: Path) -> None:
    _rule(root, (A, B))
    _rule(root, (A, B), state="open")

    assert svc.preview_reopen_contradiction(root, A, B, None) == svc.UndoRefusal(
        "not_declined"
    )


def test_the_version_moves_when_the_sidecar_is_rewritten(root: Path) -> None:
    _rule(root, (A, B))
    first = _preview(root, A, B)
    _rule(root, (A, C))

    assert _preview(root, A, B).content_digest != first.content_digest


def test_the_version_names_the_discriminator(root: Path) -> None:
    _rule(root, (A, B))
    _rule(root, (A, B), merged=C)

    assert _preview(root, A, B).content_digest != _preview(root, A, B, C).content_digest


# -- apply -------------------------------------------------------------------- #


def test_reopening_rewrites_the_record_open_and_commits_only_its_sidecar(
    root: Path,
) -> None:
    _rule(root, (A, B))
    _rule(root, (A, B), merged=C)
    plan = _preview(root, B, A)
    recorder = CommitRecorder()

    done = _apply(root, plan, recorder)

    assert done == svc.ContradictionReopened(
        first_id=A, second_id=B, merged_absorbed_id=None, commit_sha="abc1234"
    )
    assert not _declined(root, (A, B))
    assert _declined(root, (A, B), C)
    sidecar = bundle_decisions.decisions_path_for(A, root / "bundle")
    assert recorder.calls == [
        (
            root,
            [f"bundle/{sidecar.relative_to(root / 'bundle').as_posix()}"],
            f"openkos: reopen contradiction {A} <-> {B}",
        )
    ]


def test_a_merged_body_decline_is_reopened_alone(root: Path) -> None:
    _rule(root, (A, B))
    _rule(root, (A, B), merged=C)
    recorder = CommitRecorder()

    _apply(root, _preview(root, A, B, C), recorder)

    assert _declined(root, (A, B))
    assert not _declined(root, (A, B), C)
    assert recorder.calls[0][2] == f"openkos: reopen contradiction {A} <-> {B} ({C})"


def test_a_sidecar_changed_after_the_preview_is_stale(root: Path) -> None:
    _rule(root, (A, B))
    plan = _preview(root, A, B)
    _rule(root, (A, C))
    before = tree(root)
    recorder = CommitRecorder()

    assert _apply(root, plan, recorder) == svc.UndoStale()
    assert tree(root) == before
    assert recorder.calls == []


def test_an_altered_pair_is_stale(root: Path) -> None:
    _rule(root, (A, B))
    _rule(root, (A, C))
    plan = _preview(root, A, B)

    done = svc.apply_reopen_contradiction(
        root, A, C, None, plan.content_digest, ports=_ports(root, CommitRecorder())
    )

    assert done == svc.UndoStale()
    assert _declined(root, (A, C))


def test_a_record_reopened_after_the_preview_is_refused(root: Path) -> None:
    _rule(root, (A, B))
    plan = _preview(root, A, B)
    _rule(root, (A, B), state="open")
    recorder = CommitRecorder()

    assert _apply(root, plan, recorder) == svc.UndoRefusal("not_declined")
    assert recorder.calls == []


def test_a_held_lock_is_busy_and_writes_nothing(root: Path) -> None:
    _rule(root, (A, B))
    plan = _preview(root, A, B)
    before = tree(root)

    with lock.workspace_lock(root), pytest.raises(WorkspaceBusyError):
        _apply(root, plan, CommitRecorder())

    assert tree(root) == before


def test_the_plan_is_made_inside_the_section(root: Path) -> None:
    """A record reopened by another writer while the apply waits for the lock
    is seen by the apply: the plan is made after the lock is taken."""
    _rule(root, (A, B))
    plan = _preview(root, A, B)

    @contextlib.contextmanager
    def section() -> Iterator[None]:
        _rule(root, (A, B), state="open")
        yield

    ports = curate_decisions.DecisionPorts(
        autocommit=CommitRecorder(), commit_section=section
    )
    done = svc.apply_reopen_contradiction(
        root, A, B, None, plan.content_digest, ports=ports
    )

    assert done == svc.UndoRefusal("not_declined")
