"""Unit tests for `application/repair.py`'s deprecated-status export pass
(deprecated-status-export, issue #1075, Phase 2): `plan_repair` composes
`okf.apply_deprecation_export` AFTER `okf.migrate_document` for every
concept document, producing at most ONE `DocumentRewrite` per document,
and `RepairPlan` reports exported/withdrawn/dropped-marker counts plus
blocked and skipped-withdrawal concept ids.

Mirrors `tests/unit/application/test_repair.py`'s fixtures (`_workspace`,
`_write_concept`)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from openkos import config, lifecycle
from openkos.application import repair as application_repair
from openkos.bundle import bundle
from openkos.model import okf


def _workspace(root: Path) -> config.WorkspaceLayout:
    config.write_config(root)
    layout = config.WorkspaceLayout(root)
    bundle.create(layout.bundle_dir, date(2026, 1, 1))
    return layout


def _write_concept(
    bundle_dir: Path,
    concept_id: str,
    metadata: dict[str, object],
    body: str = "Body.\n",
) -> Path:
    path = bundle_dir / f"{concept_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(okf.dump_frontmatter(metadata, body), encoding="utf-8")
    return path


def _rewrite_for(
    plan: application_repair.RepairPlan, concept_id: str
) -> application_repair.DocumentRewrite:
    return next(rw for rw in plan.document_rewrites if rw.concept_id == concept_id)


def test_repair_exports_a_hand_written_supersedes_edge(tmp_path: Path) -> None:
    """spec: 'repair exports a superseded concept the edge was hand-written
    for'."""
    layout = _workspace(tmp_path)
    _write_concept(
        layout.bundle_dir,
        "concepts/a",
        {
            "type": "Concept",
            "title": "A",
            "relations": [{"target": "concepts/b", "type": "supersedes"}],
        },
    )
    _write_concept(
        layout.bundle_dir,
        "concepts/b",
        {"type": "Concept", "title": "B", "status": "stable"},
    )

    plan = application_repair.plan_repair(layout.bundle_dir)

    assert isinstance(plan, application_repair.RepairPlan)
    assert plan.has_work
    rewrite = _rewrite_for(plan, "concepts/b")
    assert rewrite.changes.export is okf.ExportOutcome.EXPORT
    metadata, _ = okf.load_frontmatter(rewrite.text)
    assert metadata["status"] == "deprecated"
    assert metadata[okf.STATUS_DERIVED_FROM_KEY] == "supersedes"
    exported = sum(
        1
        for rw in plan.document_rewrites
        if rw.changes.export is okf.ExportOutcome.EXPORT
    )
    assert exported == 1


def test_repair_withdraws_an_export_whose_edge_was_removed_by_hand(
    tmp_path: Path,
) -> None:
    """spec: 'repair withdraws an export whose edge was removed by hand'."""
    layout = _workspace(tmp_path)
    _write_concept(
        layout.bundle_dir,
        "concepts/b",
        {
            "type": "Concept",
            "title": "B",
            "status": "deprecated",
            okf.STATUS_DERIVED_FROM_KEY: "supersedes",
        },
    )

    plan = application_repair.plan_repair(layout.bundle_dir)

    assert isinstance(plan, application_repair.RepairPlan)
    rewrite = _rewrite_for(plan, "concepts/b")
    assert rewrite.changes.export is okf.ExportOutcome.WITHDRAW
    metadata, _ = okf.load_frontmatter(rewrite.text)
    assert metadata["status"] == "stable"
    assert okf.STATUS_DERIVED_FROM_KEY not in metadata
    withdrawn = sum(
        1
        for rw in plan.document_rewrites
        if rw.changes.export is okf.ExportOutcome.WITHDRAW
    )
    assert withdrawn == 1


def test_repair_leaves_a_blocked_draft_alone_and_reports_it(tmp_path: Path) -> None:
    """spec: 'repair leaves a blocked draft alone and says so' -- bytes
    unchanged, no `DocumentRewrite` for it, named in `blocked_export_ids`."""
    layout = _workspace(tmp_path)
    _write_concept(
        layout.bundle_dir,
        "concepts/a",
        {
            "type": "Concept",
            "title": "A",
            "relations": [{"target": "concepts/b", "type": "supersedes"}],
        },
    )
    b_path = _write_concept(
        layout.bundle_dir,
        "concepts/b",
        {"type": "Concept", "title": "B", "status": "draft"},
    )
    original_bytes = b_path.read_bytes()

    plan = application_repair.plan_repair(layout.bundle_dir)

    assert isinstance(plan, application_repair.RepairPlan)
    assert not any(rw.concept_id == "concepts/b" for rw in plan.document_rewrites)
    assert plan.blocked_export_ids == ("concepts/b",)
    assert b_path.read_bytes() == original_bytes


def test_repair_drops_an_invalid_marker(tmp_path: Path) -> None:
    """DROP-MARKER: an invalid marker value beside a human `draft` is
    removed alone, the human value stays."""
    layout = _workspace(tmp_path)
    _write_concept(
        layout.bundle_dir,
        "concepts/b",
        {
            "type": "Concept",
            "title": "B",
            "status": "draft",
            okf.STATUS_DERIVED_FROM_KEY: "supersedes",
        },
    )

    plan = application_repair.plan_repair(layout.bundle_dir)

    assert isinstance(plan, application_repair.RepairPlan)
    rewrite = _rewrite_for(plan, "concepts/b")
    assert rewrite.changes.export is okf.ExportOutcome.DROP_MARKER
    metadata, _ = okf.load_frontmatter(rewrite.text)
    assert metadata["status"] == "draft"
    assert okf.STATUS_DERIVED_FROM_KEY not in metadata


def test_repair_incomplete_walk_skips_and_reports_withdrawal(tmp_path: Path) -> None:
    """spec: 'Withdrawal Requires A Complete Edge Walk' -- a document with
    malformed `relations:` (parseable frontmatter, so `plan_repair` does not
    refuse the whole run over it) makes the edge walk incomplete; `b`'s
    stale export is KEPT, not withdrawn, and `b` is named in
    `skipped_withdrawal_ids`."""
    layout = _workspace(tmp_path)
    _write_concept(
        layout.bundle_dir,
        "concepts/b",
        {
            "type": "Concept",
            "title": "B",
            "status": "deprecated",
            okf.STATUS_DERIVED_FROM_KEY: "supersedes",
        },
    )
    broken_path = layout.bundle_dir / "concepts" / "broken.md"
    broken_path.write_text(
        "---\ntype: Concept\ntitle: Broken\nrelations: not-a-list\n---\nBody.\n",
        encoding="utf-8",
    )

    plan = application_repair.plan_repair(layout.bundle_dir)

    assert isinstance(plan, application_repair.RepairPlan)
    assert not any(rw.concept_id == "concepts/b" for rw in plan.document_rewrites)
    assert plan.skipped_withdrawal_ids == ("concepts/b",)


def test_deprecated_concept_ids_identical_before_and_after_repair(
    tmp_path: Path,
) -> None:
    """Regression (task 2.10, success criterion): `repair`'s export write
    must not change effective status -- `lifecycle.deprecated_concept_ids`
    returns the SAME set before and after `repair` runs over a bundle
    carrying a hand-written, unexported `supersedes` edge."""
    layout = _workspace(tmp_path)
    _write_concept(
        layout.bundle_dir,
        "concepts/a",
        {
            "type": "Concept",
            "title": "A",
            "relations": [{"target": "concepts/b", "type": "supersedes"}],
        },
    )
    _write_concept(
        layout.bundle_dir,
        "concepts/b",
        {"type": "Concept", "title": "B", "status": "stable"},
    )

    before = lifecycle.deprecated_concept_ids(layout.bundle_dir)

    plan = application_repair.plan_repair(layout.bundle_dir)
    assert isinstance(plan, application_repair.RepairPlan)
    application_repair.apply_repair(tmp_path, plan)

    after = lifecycle.deprecated_concept_ids(layout.bundle_dir)

    assert before == after == frozenset({"concepts/b"})


def test_repair_reports_nothing_to_migrate_summary_accounts_for_exports(
    tmp_path: Path,
) -> None:
    """A bundle with no v0.1 content, no ledger extraction, no flip, and NO
    export drift has `has_work` `False`."""
    layout = _workspace(tmp_path)
    _write_concept(
        layout.bundle_dir,
        "concepts/already-consistent",
        {
            "type": "Concept",
            "title": "Already consistent",
            "generated": {"by": "openkos/0.3.0", "at": "2026-07-14T09:00:00Z"},
            "status": "stable",
        },
    )

    plan = application_repair.plan_repair(layout.bundle_dir)

    assert isinstance(plan, application_repair.RepairPlan)
    assert not plan.has_work
    assert plan.document_rewrites == []
    assert plan.blocked_export_ids == ()
    assert plan.skipped_withdrawal_ids == ()
