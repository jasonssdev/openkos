"""Reopening a keep-separate ruling through `openkos.application.undo_service`
(`review-undo` change, ADR-0067): listed, previewed with a version over the
ruling's sidecar, and applied under one lock, called without the CLI.

Every ruling is written by `duplicates_service.apply_identity_decision`, the
writer a keep-separate decision and `duplicates --keep-distinct` both use.
"""

import contextlib
import functools
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from openkos import config, lock
from openkos.application import curate_decisions, duplicates_service, lock_wait
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

A, B, C = "concepts/alpha", "concepts/bravo", "concepts/charlie"


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = make_workspace(tmp_path, monkeypatch)
    for concept_id in (A, B, C):
        write_concept(root, concept_id, title=concept_id.rsplit("/", 1)[1].title())
    return root


def _rule(root: Path, members: tuple[str, ...], state: str = "declined") -> None:
    duplicates_service.apply_identity_decision(
        config.WorkspaceLayout(root),
        members,
        target_state=state,  # type: ignore[arg-type]
    )


def _fixed(at: datetime) -> datetime:
    return at


def _ports(
    root: Path, recorder: CommitRecorder, *, wait_seconds: float = 0.0
) -> curate_decisions.DecisionPorts:
    return curate_decisions.DecisionPorts(
        autocommit=recorder,
        commit_section=lock_wait.locked_commit_section(root, wait_seconds=wait_seconds),
    )


def _preview(root: Path, members: tuple[str, ...]) -> svc.ReopenPlan:
    plan = svc.preview_reopen(root, members)
    assert isinstance(plan, svc.ReopenPlan), plan
    return plan


def _kept(root: Path, members: tuple[str, ...]) -> bool:
    return application_pending.is_group_kept_distinct(
        config.WorkspaceLayout(root), members
    )


# -- the list ----------------------------------------------------------------- #


def test_kept_distinct_lists_declined_rulings_newest_first(root: Path) -> None:
    for hour, members in ((9, (A, B)), (10, (B, C))):
        at = datetime(2026, 10, 16, hour, tzinfo=UTC)
        duplicates_service.apply_identity_decision(
            config.WorkspaceLayout(root),
            members,
            target_state="declined",
            clock=functools.partial(_fixed, at),
        )
    _rule(root, (A, C), "open")
    before = tree(root)

    listed = svc.kept_distinct(root)

    assert [(r.member_ids, r.decided_at) for r in listed] == [
        ((B, C), "2026-10-16T10:00:00+00:00"),
        ((A, B), "2026-10-16T09:00:00+00:00"),
    ]
    assert tree(root) == before


def test_rulings_recorded_at_the_same_time_are_ordered_by_members(
    root: Path,
) -> None:
    for members in ((B, C), (A, C), (A, B)):
        duplicates_service.apply_identity_decision(
            config.WorkspaceLayout(root),
            members,
            target_state="declined",
            clock=lambda: datetime(2026, 10, 16, 9, tzinfo=UTC),
        )

    assert [r.member_ids for r in svc.kept_distinct(root)] == [(A, B), (A, C), (B, C)]


def test_a_hand_edited_unreadable_time_is_listed_last(root: Path) -> None:
    _rule(root, (B, C))
    _rule(root, (A, B))
    sidecar = bundle_decisions.decisions_path_for(A, root / "bundle")
    text = sidecar.read_text(encoding="utf-8")
    stamp = svc.kept_distinct(root)[0].decided_at
    sidecar.write_text(text.replace(stamp, "sometime"), encoding="utf-8")

    assert [(r.member_ids, r.decided_at) for r in svc.kept_distinct(root)][-1] == (
        (A, B),
        "sometime",
    )


def test_nothing_kept_distinct_lists_nothing(root: Path) -> None:
    assert svc.kept_distinct(root) == ()


# -- preview ------------------------------------------------------------------ #


def test_a_kept_pair_previews_in_either_order_with_one_version(root: Path) -> None:
    _rule(root, (A, B))
    before = tree(root)

    first = _preview(root, (B, A))
    second = _preview(root, (A, B))

    assert first.member_ids == (A, B)
    assert first == second
    assert tree(root) == before


@pytest.mark.parametrize(
    "members",
    [
        (A, C),
        (A,),
        (A, A),
        (A, "../escape"),
        (A, "/abs"),
        (A, B, C),
    ],
)
def test_no_declined_ruling_over_exactly_these_members_is_refused(
    root: Path, members: tuple[str, ...]
) -> None:
    _rule(root, (A, B))
    before = tree(root)

    assert svc.preview_reopen(root, members) == svc.UndoRefusal("not_kept_distinct")
    assert tree(root) == before


def test_a_reopened_ruling_is_refused(root: Path) -> None:
    _rule(root, (A, B))
    _rule(root, (A, B), "open")

    assert svc.preview_reopen(root, (A, B)) == svc.UndoRefusal("not_kept_distinct")


def test_the_version_moves_when_the_sidecar_is_rewritten(root: Path) -> None:
    _rule(root, (A, B))
    first = _preview(root, (A, B))
    _rule(root, (A, C))

    assert _preview(root, (A, B)).content_digest != first.content_digest


# -- apply -------------------------------------------------------------------- #


def test_reopening_rewrites_the_ruling_open_and_commits_only_its_sidecar(
    root: Path,
) -> None:
    _rule(root, (A, B))
    plan = _preview(root, (A, B))
    recorder = CommitRecorder()

    done = svc.apply_reopen(
        root, plan.member_ids, plan.content_digest, ports=_ports(root, recorder)
    )

    assert done == svc.Reopened(member_ids=(A, B), commit_sha="abc1234")
    assert not _kept(root, (A, B))
    assert svc.kept_distinct(root) == ()
    sidecar = bundle_decisions.decisions_path_for(A, root / "bundle")
    assert recorder.calls == [
        (
            root,
            [f"bundle/{sidecar.relative_to(root / 'bundle').as_posix()}"],
            f"openkos: reopen identity {A}/{B}",
        )
    ]


def test_a_sidecar_changed_after_the_preview_is_stale(root: Path) -> None:
    _rule(root, (A, B))
    plan = _preview(root, (A, B))
    _rule(root, (A, C))
    before = tree(root)
    recorder = CommitRecorder()

    done = svc.apply_reopen(
        root, plan.member_ids, plan.content_digest, ports=_ports(root, recorder)
    )

    assert done == svc.UndoStale()
    assert tree(root) == before
    assert recorder.calls == []


def test_altered_members_are_stale(root: Path) -> None:
    _rule(root, (A, B))
    _rule(root, (A, C))
    plan = _preview(root, (A, B))

    done = svc.apply_reopen(
        root, (A, C), plan.content_digest, ports=_ports(root, CommitRecorder())
    )

    assert done == svc.UndoStale()
    assert _kept(root, (A, C))


def test_a_ruling_reopened_after_the_preview_is_refused(root: Path) -> None:
    _rule(root, (A, B))
    plan = _preview(root, (A, B))
    _rule(root, (A, B), "open")
    recorder = CommitRecorder()

    done = svc.apply_reopen(
        root, plan.member_ids, plan.content_digest, ports=_ports(root, recorder)
    )

    assert done == svc.UndoRefusal("not_kept_distinct")
    assert recorder.calls == []


def test_a_held_lock_is_busy_and_writes_nothing(root: Path) -> None:
    _rule(root, (A, B))
    plan = _preview(root, (A, B))
    before = tree(root)

    with lock.workspace_lock(root), pytest.raises(WorkspaceBusyError):
        svc.apply_reopen(
            root,
            plan.member_ids,
            plan.content_digest,
            ports=_ports(root, CommitRecorder()),
        )

    assert tree(root) == before


def test_the_plan_is_made_inside_the_section(root: Path) -> None:
    """A ruling reopened by another writer while the apply waits for the lock
    is seen by the apply: the plan is made after the lock is taken."""
    _rule(root, (A, B))
    plan = _preview(root, (A, B))

    @contextlib.contextmanager
    def section() -> Iterator[None]:
        _rule(root, (A, B), "open")
        yield

    ports = curate_decisions.DecisionPorts(
        autocommit=CommitRecorder(), commit_section=section
    )
    done = svc.apply_reopen(root, plan.member_ids, plan.content_digest, ports=ports)

    assert done == svc.UndoRefusal("not_kept_distinct")
