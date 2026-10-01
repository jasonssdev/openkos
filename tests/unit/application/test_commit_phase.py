"""`application.commit_phase`: the read-dependency guard a split verb's commit
phase re-validates beside its drift-checked write targets (ADR-0036).

The whole-verb lock used to make the documents a plan READ safe by excluding
every other writer. A commit phase that takes the lock only after the plan was
computed has no such exclusion, so a document whose bytes decided a
sensitivity level, a provenance list or a rewrite set must be re-validated
exactly like a write target.
"""

from pathlib import Path

import pytest

from openkos import config
from openkos.application import commit_phase
from tests.unit.application.curation_support import make_workspace, write_concept


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> config.WorkspaceLayout:
    root = make_workspace(tmp_path, monkeypatch)
    write_concept(root, "concepts/a", title="A")
    write_concept(root, "concepts/b", title="B")
    return config.WorkspaceLayout(root)


def _deps(layout: config.WorkspaceLayout) -> commit_phase.ReadDependencies:
    return commit_phase.capture_bundle_documents(layout.bundle_dir)


def test_an_untouched_bundle_has_no_read_drift(
    layout: config.WorkspaceLayout,
) -> None:
    deps = _deps(layout)

    # Precondition: the capture really holds the bundle's two concepts.
    assert {p.name for p in deps.present} == {"a.md", "b.md"}
    assert commit_phase.describe_read_drift(layout, deps, "merge") is None


def test_a_changed_dependency_is_named_and_refused(
    layout: config.WorkspaceLayout,
) -> None:
    deps = _deps(layout)
    target = layout.bundle_dir / "concepts" / "b.md"
    target.write_text(target.read_text(encoding="utf-8") + "\nedit\n", encoding="utf-8")

    message = commit_phase.describe_read_drift(layout, deps, "merge")

    assert message == (
        "openkos merge: refusing to write -- 1 read dependency(ies) changed on "
        "disk after this run computed its plan: concepts/b.md. Nothing was "
        "written. Re-run to recompute over the current bundle."
    )


def test_a_vanished_dependency_is_refused(layout: config.WorkspaceLayout) -> None:
    deps = _deps(layout)
    (layout.bundle_dir / "concepts" / "b.md").unlink()

    message = commit_phase.describe_read_drift(layout, deps, "forget")

    assert message is not None
    assert "1 read dependency(ies) vanished" in message
    assert "concepts/b.md" in message


def test_a_new_document_is_refused_because_it_has_no_baseline(
    layout: config.WorkspaceLayout,
) -> None:
    deps = _deps(layout)
    write_concept(layout.root, "concepts/c", title="C")

    message = commit_phase.describe_read_drift(layout, deps, "set-sensitivity")

    assert message is not None
    assert "1 new document(s) appeared: concepts/c.md" in message


def test_a_path_that_must_stay_absent_is_refused_when_it_appears(
    layout: config.WorkspaceLayout,
) -> None:
    ghost = layout.bundle_dir / "concepts" / "ghost.md"
    deps = commit_phase.ReadDependencies(absent=frozenset({ghost}))
    assert commit_phase.describe_read_drift(layout, deps, "unmerge") is None

    ghost.write_text("---\ntype: Concept\n---\n", encoding="utf-8")

    message = commit_phase.describe_read_drift(layout, deps, "unmerge")
    assert message is not None
    assert "1 path(s) that must not exist appeared: concepts/ghost.md" in message


def test_merging_two_captures_unions_their_sets(
    layout: config.WorkspaceLayout,
) -> None:
    deps = _deps(layout)
    ghost = layout.bundle_dir / "concepts" / "ghost.md"
    extra = commit_phase.ReadDependencies(absent=frozenset({ghost}))

    merged = deps.merged_with(extra)

    assert merged.present == deps.present
    assert merged.absent == {ghost}
    assert merged.documents == deps.documents


def test_without_exclude_every_document_is_a_dependency(
    layout: config.WorkspaceLayout,
) -> None:
    keep = layout.bundle_dir / "concepts" / "a.md"

    deps = commit_phase.capture_bundle_documents(
        layout.bundle_dir, exclude=frozenset({keep})
    )

    assert keep not in deps.present
    assert deps.documents is not None
    # Excluded paths (the verb's own write targets) are still KNOWN documents,
    # so they are never reported as new.
    assert keep in deps.documents


def test_the_unlocked_section_enters_and_exits_cleanly() -> None:
    with commit_phase.unlocked_section():
        pass
