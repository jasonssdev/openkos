"""Undoing a reconcile through `openkos.application.undo_service`
(`undo-findings` change, ADR-0074): listed, previewed with a version over both
pages, and applied under one lock, called without the CLI.

Every resolution is written by `reconcile_service.reconcile_concepts`, the
transaction `reconcile` and a finding decision both use, so the undo is checked
against exactly what reconcile writes.
"""

import contextlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from openkos import config, lock
from openkos.application import curate_decisions, lock_wait, reconcile_service
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
_NOW = datetime(2026, 10, 17, 9, tzinfo=UTC)


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Three pages in the form the engine writes them (`okf.dump_frontmatter`),
    so an undo can be compared byte for byte with the page before."""
    root = make_workspace(tmp_path, monkeypatch)
    for concept_id in (A, B, C):
        name = concept_id.rsplit("/", 1)[1]
        page = write_concept(root, concept_id, title=name.title())
        page.write_text(
            okf.dump_frontmatter(
                {"status": "stable", "title": name.title(), "type": "Concept"},
                f"# {name.title()}\n\n{name} says so.\n",
            ),
            encoding="utf-8",
        )
    return root


def _reconcile(
    root: Path,
    first: str,
    second: str,
    *,
    winner: str | None = None,
    revision: str | None = None,
) -> None:
    reconcile_service.reconcile_concepts(
        root,
        first,
        second,
        winner=winner,
        revision=revision,
        auto=True,
        ports=reconcile_service.ReconcilePorts(autocommit=CommitRecorder()),
    )


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


def _page(root: Path, concept_id: str) -> Path:
    return okf.concept_path_for(concept_id, root / "bundle")


def _text(root: Path, concept_id: str) -> str:
    return _page(root, concept_id).read_text(encoding="utf-8")


def _status(root: Path, concept_id: str) -> object:
    metadata, _ = okf.load_frontmatter(_text(root, concept_id))
    return metadata.get("status")


def _ports(root: Path, recorder: CommitRecorder) -> curate_decisions.DecisionPorts:
    return curate_decisions.DecisionPorts(
        autocommit=recorder,
        commit_section=lock_wait.locked_commit_section(root, wait_seconds=0.0),
    )


def _preview(root: Path, first: str, second: str) -> svc.UnreconcilePlan:
    plan = svc.preview_unreconcile(root, first, second)
    assert isinstance(plan, svc.UnreconcilePlan), plan
    return plan


def _apply(root: Path, plan: svc.UnreconcilePlan, recorder: CommitRecorder) -> object:
    return svc.apply_unreconcile(
        root,
        plan.first_id,
        plan.second_id,
        plan.content_digest,
        ports=_ports(root, recorder),
    )


def _pages(root: Path) -> dict[str, bytes]:
    return {cid: _page(root, cid).read_bytes() for cid in (A, B, C)}


# -- the note's inverse --------------------------------------------------------- #

_NOTE_A = (
    "## Reconciliation\n<!-- okos:reconcile target=concepts/a role=reconciled -->\n"
    "Reconciled with [concepts/a](/concepts/a.md) on 2026-10-17 (both coexist).\n"
)
_NOTE_B = (
    "## Reconciliation\n<!-- okos:reconcile target=concepts/b role=superseded -->\n"
    "Superseded by [concepts/b](/concepts/b.md) as of 2026-10-17 (hidden).\n"
)


def test_the_last_note_is_removed_back_to_the_body_it_was_appended_to() -> None:
    body = "# T\n\nText.\n"

    assert reconcile_service.remove_reconciliation_note(
        body + "\n" + _NOTE_A, "concepts/a"
    ) == (body, True)


def test_a_note_followed_by_another_is_removed_and_the_other_kept() -> None:
    body = "# T\n\nText.\n\n" + _NOTE_A + "\n" + _NOTE_B

    assert reconcile_service.remove_reconciliation_note(body, "concepts/a") == (
        "# T\n\nText.\n\n" + _NOTE_B,
        True,
    )


def test_a_note_about_another_page_is_not_removed() -> None:
    body = "# T\n\nText.\n\n" + _NOTE_B

    assert reconcile_service.remove_reconciliation_note(body, "concepts/a") == (
        body,
        False,
    )


@pytest.mark.parametrize(
    "edited",
    [
        _NOTE_A.replace("## Reconciliation", "## Checked"),
        _NOTE_A.replace("Reconciled with", "\nReconciled with"),
        "Intro.\n" + _NOTE_A.split("\n", 1)[1],
        _NOTE_A.replace("## Reconciliation", "See ## Reconciliation"),
    ],
)
def test_a_note_edited_around_its_anchor_is_left_as_it_is(edited: str) -> None:
    body = "# T\n\nText.\n\n" + edited

    assert reconcile_service.remove_reconciliation_note(body, "concepts/a") == (
        body,
        False,
    )


def test_a_note_whose_sentence_was_reworded_is_still_removed() -> None:
    body = "# T\n\nText.\n\n" + _NOTE_A.replace("both coexist", "checked by me")

    assert reconcile_service.remove_reconciliation_note(body, "concepts/a") == (
        "# T\n\nText.\n",
        True,
    )


def test_a_counterpart_id_is_matched_literally() -> None:
    body = "# T\n\nText.\n\n" + _NOTE_A

    assert reconcile_service.remove_reconciliation_note(body, "concepts/.") == (
        body,
        False,
    )


# -- the list ----------------------------------------------------------------- #


def test_reconciled_pairs_are_listed_by_pair_with_their_resolution(
    root: Path,
) -> None:
    _reconcile(root, B, C, revision=C)
    _reconcile(root, B, A, winner=B)
    before = tree(root)

    listed = svc.reconciled_pairs(root)

    assert [
        (p.first_id, p.second_id, p.relation_type, p.holder_id, p.target_id)
        for p in listed
    ] == [
        (A, B, "supersedes", B, A),
        (B, C, "revises", C, B),
    ]
    assert tree(root) == before


def test_a_symmetric_pair_is_listed_once_without_a_holder(root: Path) -> None:
    _reconcile(root, A, B)

    assert [
        (p.first_id, p.second_id, p.relation_type, p.holder_id, p.target_id)
        for p in svc.reconciled_pairs(root)
    ] == [(A, B, "reconciled_with", None, None)]


def test_disagreeing_resolutions_and_edges_to_missing_pages_are_not_listed(
    root: Path,
) -> None:
    _reconcile(root, A, B, winner=A)
    _relate(root, B, "reconciled_with", A)
    write_concept(root, "concepts/charlie2", title="Charlie2")
    _relate(root, C, "revises", "concepts/charlie2")
    _page(root, "concepts/charlie2").unlink()

    assert svc.reconciled_pairs(root) == ()


def test_nothing_reconciled_lists_nothing(root: Path) -> None:
    _relate(root, A, "related_to", B)

    assert svc.reconciled_pairs(root) == ()


# -- preview ------------------------------------------------------------------ #


def test_a_symmetric_reconcile_previews_both_pages_in_either_order(
    root: Path,
) -> None:
    _reconcile(root, A, B)
    before = tree(root)

    plan = _preview(root, B, A)

    assert (plan.first_id, plan.second_id) == (A, B)
    assert (plan.relation_type, plan.holder_id, plan.target_id) == (
        "reconciled_with",
        None,
        None,
    )
    assert plan.withdraws_deprecation is False
    assert plan.pages_changed == (A, B)
    assert _preview(root, A, B) == plan
    assert tree(root) == before


def test_a_supersede_previews_its_holder_and_the_withdrawn_mark(root: Path) -> None:
    _reconcile(root, A, B, winner=B)

    plan = _preview(root, A, B)

    assert (plan.relation_type, plan.holder_id, plan.target_id) == (
        "supersedes",
        B,
        A,
    )
    assert plan.withdraws_deprecation is True
    assert plan.pages_changed == (A, B)


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (A, B),
        (A, "concepts/nowhere"),
        (A, A),
        ("../escape", B),
        (A, "/abs"),
    ],
)
def test_a_pair_with_no_resolution_is_refused(
    root: Path, first: str, second: str
) -> None:
    _relate(root, A, "related_to", B)
    before = tree(root)

    assert svc.preview_unreconcile(root, first, second) == svc.UndoRefusal(
        "not_reconciled"
    )
    assert tree(root) == before


def test_disagreeing_resolutions_are_refused(root: Path) -> None:
    _reconcile(root, A, B, winner=A)
    _relate(root, B, "reconciled_with", A)

    assert svc.preview_unreconcile(root, A, B) == svc.UndoRefusal("not_reconciled")


def test_the_version_moves_when_a_page_changes(root: Path) -> None:
    _reconcile(root, A, B)
    first = _preview(root, A, B)
    page = _page(root, B)
    page.write_text(page.read_text(encoding="utf-8") + "More.\n", encoding="utf-8")

    assert _preview(root, A, B).content_digest != first.content_digest


def test_the_version_moves_when_another_page_starts_superseding(root: Path) -> None:
    _reconcile(root, A, B, winner=A)
    first = _preview(root, A, B)
    before = _page(root, B).read_bytes()
    _relate(root, C, "supersedes", B)
    assert _page(root, B).read_bytes() == before

    second = _preview(root, A, B)

    assert second.withdraws_deprecation is False
    assert second.content_digest != first.content_digest


# -- apply -------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("winner", "revision"),
    [(None, None), (A, None), (B, None), (None, A), (None, B)],
)
def test_an_undo_puts_both_pages_back_as_they_were(
    root: Path, winner: str | None, revision: str | None
) -> None:
    before = _pages(root)
    _reconcile(root, A, B, winner=winner, revision=revision)
    plan = _preview(root, A, B)
    recorder = CommitRecorder()

    done = _apply(root, plan, recorder)

    assert done == svc.Unreconciled(
        first_id=A,
        second_id=B,
        relation_type=plan.relation_type,
        commit_sha="abc1234",
    )
    assert _pages(root) == before
    assert svc.reconciled_pairs(root) == ()
    assert recorder.calls == [
        (
            root,
            [f"bundle/{A}.md", f"bundle/{B}.md", "bundle/log.md"],
            f"openkos: unreconcile {A} <-> {B}",
        )
    ]
    log = (root / "bundle" / "log.md").read_text(encoding="utf-8")
    assert f"**Unreconcile**: Removed the {plan.relation_type!r} resolution" in log


def test_a_withdrawn_mark_reads_stable_on_a_page_that_had_no_status(
    root: Path,
) -> None:
    """The withdrawal is `unrelate`'s: it writes `status: stable`, the value the
    engine's own pages carry, so a page written without one gains it."""
    page = _page(root, B)
    page.write_text(
        page.read_text(encoding="utf-8").replace("status: stable\n", ""),
        encoding="utf-8",
    )
    _reconcile(root, A, B, winner=A)

    _apply(root, _preview(root, A, B), CommitRecorder())

    assert _status(root, B) == "stable"
    metadata, _ = okf.load_frontmatter(_text(root, B))
    assert "status_derived_from" not in metadata


def test_a_page_still_superseded_by_another_keeps_its_mark(root: Path) -> None:
    _reconcile(root, A, B, winner=A)
    _relate(root, C, "supersedes", B)
    plan = _preview(root, A, B)

    _apply(root, plan, CommitRecorder())

    assert plan.withdraws_deprecation is False
    assert _status(root, B) == "deprecated"
    metadata, _ = okf.load_frontmatter(_text(root, A))
    assert okf.decode_relations(metadata) == []


def test_an_edge_the_counterpart_holds_to_another_page_is_kept(root: Path) -> None:
    _reconcile(root, A, B, winner=A)
    _relate(root, B, "supersedes", C)

    _apply(root, _preview(root, A, B), CommitRecorder())

    metadata, _ = okf.load_frontmatter(_text(root, B))
    assert [(r.target, r.type) for r in okf.decode_relations(metadata)] == [
        (C, "supersedes")
    ]
    assert _status(root, C) == "deprecated"


def test_an_edited_note_is_kept_and_the_edge_still_removed(root: Path) -> None:
    _reconcile(root, A, B, revision=A)
    page = _page(root, B)
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "## Reconciliation", "## Checked by me"
        ),
        encoding="utf-8",
    )
    kept = _page(root, B).read_bytes()
    plan = _preview(root, A, B)
    recorder = CommitRecorder()

    _apply(root, plan, recorder)

    assert plan.pages_changed == (A,)
    assert _page(root, B).read_bytes() == kept
    assert "Revised by" not in _text(root, A)
    assert recorder.calls[0][1] == [f"bundle/{A}.md", "bundle/log.md"]


def test_a_page_changed_after_the_preview_is_stale(root: Path) -> None:
    _reconcile(root, A, B)
    plan = _preview(root, A, B)
    page = _page(root, A)
    page.write_text(page.read_text(encoding="utf-8") + "More.\n", encoding="utf-8")
    before = tree(root)
    recorder = CommitRecorder()

    assert _apply(root, plan, recorder) == svc.UndoStale()
    assert tree(root) == before
    assert recorder.calls == []


def test_an_altered_pair_is_stale(root: Path) -> None:
    _reconcile(root, A, B)
    _reconcile(root, A, C)
    plan = _preview(root, A, B)

    done = svc.apply_unreconcile(
        root, A, C, plan.content_digest, ports=_ports(root, CommitRecorder())
    )

    assert done == svc.UndoStale()
    assert len(svc.reconciled_pairs(root)) == 2


def test_a_pair_whose_page_has_the_same_bytes_is_still_another_pair(
    root: Path,
) -> None:
    _reconcile(root, A, B)
    _reconcile(root, A, C)
    _page(root, C).write_bytes(_page(root, B).read_bytes())
    plan = _preview(root, A, B)

    done = svc.apply_unreconcile(
        root, A, C, plan.content_digest, ports=_ports(root, CommitRecorder())
    )

    assert done == svc.UndoStale()


def test_a_reconcile_undone_after_the_preview_is_refused(root: Path) -> None:
    _reconcile(root, A, B)
    plan = _preview(root, A, B)
    assert isinstance(_apply(root, plan, CommitRecorder()), svc.Unreconciled)
    recorder = CommitRecorder()

    assert _apply(root, plan, recorder) == svc.UndoRefusal("not_reconciled")
    assert recorder.calls == []


def test_a_held_lock_is_busy_and_writes_nothing(root: Path) -> None:
    _reconcile(root, A, B)
    plan = _preview(root, A, B)
    before = tree(root)

    with lock.workspace_lock(root), pytest.raises(WorkspaceBusyError):
        _apply(root, plan, CommitRecorder())

    assert tree(root) == before


def test_the_plan_is_made_inside_the_section(root: Path) -> None:
    """A page edited by another writer while the apply waits for the lock is
    seen by the apply: the plan is made after the lock is taken."""
    _reconcile(root, A, B)
    plan = _preview(root, A, B)

    @contextlib.contextmanager
    def section() -> Iterator[None]:
        page = _page(root, A)
        page.write_text(page.read_text(encoding="utf-8") + "More.\n", "utf-8")
        yield

    ports = curate_decisions.DecisionPorts(
        autocommit=CommitRecorder(), commit_section=section
    )

    assert (
        svc.apply_unreconcile(root, A, B, plan.content_digest, ports=ports)
        == svc.UndoStale()
    )
