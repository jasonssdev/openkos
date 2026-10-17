"""Removing a typed relation through `openkos.application.undo_service`
(`review-undo` change, ADR-0067): previewed with a version over the pages it
rewrites, and applied under one lock, called without the CLI.

Every relation is written by `lifecycle.prepare_relate` and `relate_core`, the
writer a relation-type decision and `relate` both use.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from openkos import config, lock
from openkos.application import curate_decisions, lock_wait
from openkos.application import lifecycle as application_lifecycle
from openkos.application import undo_service as svc
from openkos.lock import WorkspaceBusyError
from openkos.model import okf
from tests.unit.application.curation_support import (
    CommitRecorder,
    make_workspace,
    tree,
    write_concept,
)

A, B, C = "concepts/alpha", "concepts/bravo", "concepts/charlie"
_NOW = datetime(2026, 10, 16, 9, tzinfo=UTC)


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = make_workspace(tmp_path, monkeypatch)
    for concept_id in (A, B, C):
        write_concept(root, concept_id, title=concept_id.rsplit("/", 1)[1].title())
    return root


def _relate(root: Path, source: str, rel_type: str, target: str) -> None:
    layout = config.WorkspaceLayout(root)
    source_path = okf.concept_path_for(source, layout.bundle_dir)
    target_path = okf.concept_path_for(target, layout.bundle_dir)
    log_path = layout.bundle_dir / "log.md"
    prepared = application_lifecycle.prepare_relate(
        source_path,
        log_path,
        source,
        target,
        rel_type,
        root,
        now=_NOW,
        target_path=target_path,
    )
    application_lifecycle.relate_core(
        source_path, log_path, prepared, target_path=target_path
    )


def _ports(root: Path, recorder: CommitRecorder) -> curate_decisions.DecisionPorts:
    return curate_decisions.DecisionPorts(
        autocommit=recorder,
        commit_section=lock_wait.locked_commit_section(root, wait_seconds=0.0),
    )


def _preview(root: Path, source: str, rel_type: str, target: str) -> svc.UnrelatePlan:
    plan = svc.preview_unrelate(root, source, rel_type, target)
    assert isinstance(plan, svc.UnrelatePlan), plan
    return plan


def _relations(root: Path, concept_id: str) -> list[tuple[str, str]]:
    path = okf.concept_path_for(concept_id, root / "bundle")
    metadata, _ = okf.load_frontmatter(path.read_text(encoding="utf-8"))
    return [(r.target, r.type) for r in okf.decode_relations(metadata)]


def _apply(root: Path, plan: svc.UnrelatePlan, recorder: CommitRecorder) -> object:
    return svc.apply_unrelate(
        root,
        plan.source_id,
        plan.relation_type,
        plan.target_id,
        plan.content_digest,
        ports=_ports(root, recorder),
    )


# -- preview ------------------------------------------------------------------ #


def test_a_relation_previews_its_edge_and_writes_nothing(root: Path) -> None:
    _relate(root, A, "part_of", B)
    before = tree(root)

    plan = _preview(root, A, "part_of", B)

    assert (plan.source_id, plan.relation_type, plan.target_id) == (A, "part_of", B)
    assert plan.withdraws_deprecation is False
    assert _preview(root, A, "part_of", B) == plan
    assert tree(root) == before


@pytest.mark.parametrize(
    ("source", "rel_type", "target"),
    [
        (A, "member_of", B),
        (B, "part_of", A),
        (A, "part_of", C),
        (A, "part_of", "concepts/nowhere"),
        ("concepts/nowhere", "part_of", B),
        (A, "part_of", A),
        (A, "  ", B),
        ("../escape", "part_of", B),
        (A, "part_of", "/abs"),
    ],
)
def test_no_such_relation_is_refused(
    root: Path, source: str, rel_type: str, target: str
) -> None:
    _relate(root, A, "part_of", B)
    before = tree(root)

    assert svc.preview_unrelate(root, source, rel_type, target) == svc.UndoRefusal(
        "not_related"
    )
    assert tree(root) == before


def test_a_hand_written_relation_to_the_page_itself_is_refused(root: Path) -> None:
    page = okf.concept_path_for(A, root / "bundle")
    metadata, body = okf.load_frontmatter(page.read_text(encoding="utf-8"))
    metadata[okf.RELATIONS_KEY] = okf.encode_relations(
        [okf.Relation(target=A, type="part_of")]
    )
    page.write_text(okf.dump_frontmatter(metadata, body), encoding="utf-8")

    assert svc.preview_unrelate(root, A, "part_of", A) == svc.UndoRefusal("not_related")


def test_the_type_is_read_as_unrelate_reads_it(root: Path) -> None:
    _relate(root, A, "part_of", B)

    assert _preview(root, A, " part_of ", B) == _preview(root, A, "part_of", B)


def test_the_version_moves_when_the_source_page_changes(root: Path) -> None:
    _relate(root, A, "part_of", B)
    first = _preview(root, A, "part_of", B)
    _relate(root, A, "references", C)

    assert _preview(root, A, "part_of", B).content_digest != first.content_digest


def test_the_version_names_the_edge(root: Path) -> None:
    _relate(root, A, "part_of", B)
    _relate(root, A, "references", B)

    assert (
        _preview(root, A, "part_of", B).content_digest
        != _preview(root, A, "references", B).content_digest
    )


def test_removing_a_supersedes_edge_withdraws_the_targets_export(root: Path) -> None:
    _relate(root, A, "supersedes", B)

    plan = _preview(root, A, "supersedes", B)

    assert plan.withdraws_deprecation is True


def test_the_version_moves_when_the_withdrawn_target_changes(root: Path) -> None:
    _relate(root, A, "supersedes", B)
    first = _preview(root, A, "supersedes", B)
    page = okf.concept_path_for(B, root / "bundle")
    page.write_text(page.read_text(encoding="utf-8") + "\nEdited.\n", "utf-8")

    assert _preview(root, A, "supersedes", B).content_digest != first.content_digest


def test_the_version_moves_when_the_withdrawal_no_longer_happens(root: Path) -> None:
    _relate(root, A, "supersedes", B)
    first = _preview(root, A, "supersedes", B)
    _relate(root, C, "supersedes", B)

    second = _preview(root, A, "supersedes", B)

    assert second.withdraws_deprecation is False
    assert second.content_digest != first.content_digest


# -- apply -------------------------------------------------------------------- #


def test_the_relation_is_removed_and_its_pages_committed(root: Path) -> None:
    before = tree(root)
    _relate(root, A, "part_of", B)
    plan = _preview(root, A, "part_of", B)
    recorder = CommitRecorder()

    done = _apply(root, plan, recorder)

    assert done == svc.Unrelated(A, "part_of", B, "abc1234")
    assert _relations(root, A) == []
    after = tree(root)
    # The frontmatter is re-serialised, so its keys may move; its content and
    # the body are what they were before the relation.
    assert okf.load_frontmatter(after[f"bundle/{A}.md"].decode("utf-8")) == (
        okf.load_frontmatter(before[f"bundle/{A}.md"].decode("utf-8"))
    )
    assert "**Unrelate**" in after["bundle/log.md"].decode("utf-8")
    assert recorder.calls == [
        (
            root,
            [f"bundle/{A}.md", "bundle/log.md"],
            f"openkos: unrelate {A} -> {B} (part_of)",
        )
    ]


def test_a_withdrawn_export_commits_the_target_too(root: Path) -> None:
    _relate(root, A, "supersedes", B)
    plan = _preview(root, A, "supersedes", B)
    recorder = CommitRecorder()

    _apply(root, plan, recorder)

    assert recorder.calls[0][1] == [f"bundle/{A}.md", f"bundle/{B}.md", "bundle/log.md"]


def test_a_source_changed_after_the_preview_is_stale(root: Path) -> None:
    _relate(root, A, "part_of", B)
    plan = _preview(root, A, "part_of", B)
    _relate(root, A, "references", C)
    before = tree(root)
    recorder = CommitRecorder()

    assert _apply(root, plan, recorder) == svc.UndoStale()
    assert tree(root) == before
    assert recorder.calls == []


def test_a_relation_removed_after_the_preview_is_refused(root: Path) -> None:
    _relate(root, A, "part_of", B)
    plan = _preview(root, A, "part_of", B)
    assert isinstance(_apply(root, plan, CommitRecorder()), svc.Unrelated)

    assert _apply(root, plan, CommitRecorder()) == svc.UndoRefusal("not_related")


def test_a_held_lock_is_busy_and_writes_nothing(root: Path) -> None:
    _relate(root, A, "part_of", B)
    plan = _preview(root, A, "part_of", B)
    before = tree(root)

    with lock.workspace_lock(root), pytest.raises(WorkspaceBusyError):
        _apply(root, plan, CommitRecorder())

    assert tree(root) == before
