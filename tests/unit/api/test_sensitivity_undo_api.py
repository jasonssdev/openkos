"""Undoing a whole sensitivity change (#1706): the page the person changed and
the derived pages raised with it go back in one commit, each derived page to the
level its remaining sources require. Through the public facade only, in a real
git-backed workspace. The raised set is read from the `log.md` line the change
wrote; a change that carries no such record is not offered an undo."""

import dataclasses
from pathlib import Path

import pytest

from openkos import lock
from openkos.api import (
    SensitivityChanged,
    SensitivityNotUndoable,
    SensitivityStale,
    SensitivityUndone,
    SensitivityUndoPreview,
    Workspace,
    WorkspaceBusyError,
    apply_sensitivity,
    apply_undo_sensitivity,
    preview_sensitivity,
    preview_undo_sensitivity,
)
from openkos.vcs import git as vcs_git
from tests.unit.api import support
from tests.unit.api.test_decisions_apply import _git, _head_files, _pin_identity
from tests.unit.application.curation_support import make_workspace
from tests.unit.cli.commit_phase_support import metadata_of, write_doc

SOURCE = "sources/notes"
CHILD = "concepts/from-notes"
GRANDCHILD = "concepts/from-child"
OTHER_SOURCE = "sources/other"
PAGE = "concepts/garden"


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _pin_identity(monkeypatch, tmp_path)
    ws = make_workspace(tmp_path, monkeypatch)
    write_doc(
        ws, SOURCE, {"type": "Source", "title": "Notes", "sensitivity": "private"}
    )
    write_doc(
        ws,
        CHILD,
        {
            "type": "Concept",
            "title": "From the notes",
            "sensitivity": "private",
            "provenance": [SOURCE],
        },
    )
    write_doc(
        ws,
        GRANDCHILD,
        {
            "type": "Concept",
            "title": "From the child",
            "sensitivity": "private",
            "provenance": [CHILD],
        },
    )
    write_doc(
        ws, OTHER_SOURCE, {"type": "Source", "title": "Other", "sensitivity": "public"}
    )
    write_doc(
        ws, PAGE, {"type": "Concept", "title": "The garden", "sensitivity": "private"}
    )
    vcs_git.commit_paths(ws, ["bundle"], "seed pages")
    support.forbid_model_clients(monkeypatch, "an undo of a sensitivity change")
    return ws


def _change(root: Path, concept_id: str, level: str) -> SensitivityChanged:
    ws = Workspace.open(root)
    preview = preview_sensitivity(ws, concept_id, level)  # type: ignore[arg-type]
    assert not isinstance(preview, (SensitivityNotUndoable,)), preview
    changed = apply_sensitivity(ws, preview.version)  # type: ignore[union-attr]
    assert isinstance(changed, SensitivityChanged), changed
    return changed


def _undo_preview(root: Path, concept_id: str) -> SensitivityUndoPreview:
    preview = preview_undo_sensitivity(Workspace.open(root), concept_id)
    assert isinstance(preview, SensitivityUndoPreview), preview
    return preview


def _level(root: Path, concept_id: str) -> object:
    return metadata_of(root, concept_id)["sensitivity"]


def test_the_change_records_what_it_raised_in_the_log(root: Path) -> None:
    _change(root, SOURCE, "confidential")
    log = (root / "bundle" / "log.md").read_text(encoding="utf-8")
    line = next(x for x in log.splitlines() if "**Set-sensitivity**" in x)
    assert (
        f"Also raised (2): [{GRANDCHILD}](/{GRANDCHILD}.md) (was 'private'), " in line
    )
    assert f"[{CHILD}](/{CHILD}.md) (was 'private')." in line


def test_a_change_that_raised_nothing_writes_the_line_it_always_did(root: Path) -> None:
    _change(root, PAGE, "confidential")
    log = (root / "bundle" / "log.md").read_text(encoding="utf-8")
    line = next(x for x in log.splitlines() if "**Set-sensitivity**" in x)
    assert line.endswith("sensitivity to 'confidential' (was 'private').")


def test_the_preview_names_every_page_that_goes_back_and_writes_nothing(
    root: Path,
) -> None:
    _change(root, SOURCE, "confidential")
    before = support.snapshot(root)
    with lock.workspace_lock(root):
        preview = _undo_preview(root, SOURCE)
    assert preview.concept_id == SOURCE
    assert preview.title == "Notes"
    assert (preview.now, preview.restores) == ("confidential", "private")
    assert [(p.concept_id, p.title, p.now, p.after) for p in preview.pages] == [
        (GRANDCHILD, "From the child", "confidential", "private"),
        (CHILD, "From the notes", "confidential", "private"),
    ]
    assert support.snapshot(root) == before


def test_the_undo_puts_back_the_page_and_the_raised_pages_in_one_commit(
    root: Path,
) -> None:
    _change(root, SOURCE, "confidential")
    undone = apply_undo_sensitivity(
        Workspace.open(root), _undo_preview(root, SOURCE).version
    )
    assert isinstance(undone, SensitivityUndone), undone
    assert undone.concept_id == SOURCE
    assert undone.level == "private"
    assert undone.restored == (GRANDCHILD, CHILD)
    assert undone.kept == ()
    assert undone.commit_sha == _git(root, "rev-parse", "--short", "HEAD")
    assert (_level(root, SOURCE), _level(root, CHILD), _level(root, GRANDCHILD)) == (
        "private",
        "private",
        "private",
    )
    assert _head_files(root) == [
        "bundle/concepts/from-child.md",
        "bundle/concepts/from-notes.md",
        "bundle/log.md",
        "bundle/sources/notes.md",
    ]
    assert _git(root, "log", "-1", "--format=%s") == (
        f"openkos: undo set-sensitivity {SOURCE} -> private"
    )


def test_a_derived_page_never_goes_below_what_another_source_requires(
    root: Path,
) -> None:
    """The high-water mark: after the change, a derived page came to cite a
    second, confidential source. Putting the first source back must leave the
    page confidential, not at the level it had before."""
    _change(root, SOURCE, "confidential")
    write_doc(
        root,
        OTHER_SOURCE,
        {"type": "Source", "title": "Other", "sensitivity": "confidential"},
    )
    write_doc(
        root,
        CHILD,
        {
            "type": "Concept",
            "title": "From the notes",
            "sensitivity": "confidential",
            "provenance": [SOURCE, OTHER_SOURCE],
        },
    )
    preview = _undo_preview(root, SOURCE)
    by_id = {p.concept_id: p for p in preview.pages}
    assert by_id[CHILD].after == "confidential"
    assert by_id[GRANDCHILD].after == "confidential"
    undone = apply_undo_sensitivity(Workspace.open(root), preview.version)
    assert isinstance(undone, SensitivityUndone), undone
    # The page that cites the kept one is held with it.
    assert undone.kept == (GRANDCHILD, CHILD)
    assert undone.restored == ()
    assert _level(root, SOURCE) == "private"
    assert _level(root, CHILD) == "confidential"
    assert _level(root, GRANDCHILD) == "confidential"


def test_a_derived_page_with_a_citation_that_does_not_resolve_is_not_lowered(
    root: Path,
) -> None:
    _change(root, SOURCE, "confidential")
    write_doc(
        root,
        CHILD,
        {
            "type": "Concept",
            "title": "From the notes",
            "sensitivity": "confidential",
            "provenance": [SOURCE, "sources/gone"],
        },
    )
    preview = _undo_preview(root, SOURCE)
    assert {p.concept_id: p.after for p in preview.pages}[CHILD] == "confidential"
    undone = apply_undo_sensitivity(Workspace.open(root), preview.version)
    assert isinstance(undone, SensitivityUndone)
    assert _level(root, CHILD) == "confidential"


@pytest.mark.parametrize("touched", [SOURCE, CHILD])
def test_a_page_changed_since_is_refused_honestly_and_nothing_is_written(
    root: Path, touched: str
) -> None:
    _change(root, SOURCE, "confidential")
    write_doc(
        root,
        touched,
        {
            "type": "Concept" if touched == CHILD else "Source",
            "title": "x",
            "sensitivity": "public",
            **({"provenance": [SOURCE]} if touched == CHILD else {}),
        },
    )
    before = support.snapshot(root)
    assert preview_undo_sensitivity(Workspace.open(root), SOURCE) == (
        SensitivityNotUndoable("changed_since")
    )
    assert support.snapshot(root) == before


def test_a_change_with_no_record_of_what_it_raised_is_not_offered_an_undo(
    root: Path,
) -> None:
    _change(root, PAGE, "confidential")
    assert preview_undo_sensitivity(Workspace.open(root), PAGE) == (
        SensitivityNotUndoable("no_record")
    )
    assert preview_undo_sensitivity(Workspace.open(root), OTHER_SOURCE) == (
        SensitivityNotUndoable("no_record")
    )


def test_a_change_from_before_the_record_existed_is_not_offered_an_undo(
    root: Path,
) -> None:
    _change(root, SOURCE, "confidential")
    log = root / "bundle" / "log.md"
    text = log.read_text(encoding="utf-8")
    start = text.index(" Also raised (")
    end = text.index("\n", start)
    log.write_text(text[:start] + text[end:], encoding="utf-8")
    assert preview_undo_sensitivity(Workspace.open(root), SOURCE) == (
        SensitivityNotUndoable("no_record")
    )


def test_a_change_undone_once_cannot_be_undone_again(root: Path) -> None:
    _change(root, SOURCE, "confidential")
    undone = apply_undo_sensitivity(
        Workspace.open(root), _undo_preview(root, SOURCE).version
    )
    assert isinstance(undone, SensitivityUndone)
    assert preview_undo_sensitivity(Workspace.open(root), SOURCE) == (
        SensitivityNotUndoable("no_record")
    )


def test_only_the_latest_change_to_a_page_is_offered(root: Path) -> None:
    _change(root, SOURCE, "confidential")
    _change(root, SOURCE, "private")  # a lowering: nothing raised, no record
    assert preview_undo_sensitivity(Workspace.open(root), SOURCE) == (
        SensitivityNotUndoable("no_record")
    )


def test_a_missing_page_is_not_found(root: Path) -> None:
    for concept_id in ("concepts/missing", "../outside", ""):
        assert preview_undo_sensitivity(Workspace.open(root), concept_id) == (
            SensitivityNotUndoable("not_found")
        )


def test_a_page_edited_after_the_preview_makes_the_apply_stale(root: Path) -> None:
    _change(root, SOURCE, "confidential")
    preview = _undo_preview(root, SOURCE)
    page = root / "bundle" / f"{GRANDCHILD}.md"
    page.write_text(page.read_text(encoding="utf-8") + "More.\n", encoding="utf-8")
    before = support.snapshot(root)
    assert apply_undo_sensitivity(Workspace.open(root), preview.version) == (
        SensitivityStale()
    )
    assert support.snapshot(root) == before


def test_an_altered_version_is_stale(root: Path) -> None:
    _change(root, SOURCE, "confidential")
    version = _undo_preview(root, SOURCE).version
    altered = dataclasses.replace(version, content_digest="0" * 64)
    assert apply_undo_sensitivity(Workspace.open(root), altered) == SensitivityStale()
    assert _level(root, SOURCE) == "confidential"


def test_a_busy_workspace_raises_and_writes_nothing(root: Path) -> None:
    _change(root, SOURCE, "confidential")
    version = _undo_preview(root, SOURCE).version
    before = support.snapshot(root)
    with lock.workspace_lock(root), pytest.raises(WorkspaceBusyError):
        apply_undo_sensitivity(Workspace.open(root), version)
    assert support.snapshot(root) == before


def test_an_undo_of_a_change_that_landed_confidential_is_flagged_for_the_commit(
    root: Path,
) -> None:
    write_doc(
        root, SOURCE, {"type": "Source", "title": "Notes", "sensitivity": "private"}
    )
    _change(root, SOURCE, "confidential")
    undone = apply_undo_sensitivity(
        Workspace.open(root), _undo_preview(root, SOURCE).version
    )
    assert isinstance(undone, SensitivityUndone)
    # Nothing in the commit is confidential once undone.
    assert undone.committed_confidential is False


def test_an_undo_never_raises_a_page_above_where_it_stands(root: Path) -> None:
    """A page already under-classified for some other reason is not raised by an
    undo: the undo only puts back."""
    write_doc(
        root, SOURCE, {"type": "Source", "title": "Notes", "sensitivity": "public"}
    )
    write_doc(
        root,
        CHILD,
        {
            "type": "Concept",
            "title": "From the notes",
            "sensitivity": "public",
            "provenance": [SOURCE],
        },
    )
    write_doc(
        root, GRANDCHILD, {"type": "Concept", "title": "G", "sensitivity": "public"}
    )
    vcs_git.commit_paths(root, ["bundle"], "adjust")
    _change(root, SOURCE, "private")
    write_doc(
        root,
        OTHER_SOURCE,
        {"type": "Source", "title": "Other", "sensitivity": "confidential"},
    )
    write_doc(
        root,
        CHILD,
        {
            "type": "Concept",
            "title": "From the notes",
            "sensitivity": "private",
            "provenance": [SOURCE, OTHER_SOURCE],
        },
    )
    undone = apply_undo_sensitivity(
        Workspace.open(root), _undo_preview(root, SOURCE).version
    )
    assert isinstance(undone, SensitivityUndone)
    assert _level(root, CHILD) == "private"


@pytest.mark.parametrize(
    "rewrite",
    [
        # What a scrub of a purged id leaves: the id, the link or the item gone.
        lambda line: line.replace(f"/{CHILD}.md", "/[purged].md"),
        lambda line: line.replace(f"[{CHILD}]", "[purged]"),
        lambda line: line.replace(
            f"[{CHILD}](/{CHILD}.md) (was 'private')", "[purged]"
        ),
        lambda line: line.replace(f", [{CHILD}](/{CHILD}.md) (was 'private')", ""),
        lambda line: line[: line.index(" Also raised (")] + " Also raised (1): .",
        lambda line: line.replace(" (was 'private').", " (was [purged])."),
        lambda line: line.replace("sources/notes", "[purged]"),
        lambda line: line + " Extra.",
    ],
)
def test_a_line_that_was_rewritten_is_not_read_and_is_not_undoable(
    root: Path, rewrite: object
) -> None:
    """A purge or any other rewrite of the log line leaves something that no
    longer parses as the record: the undo fails closed with `no_record`."""
    _change(root, SOURCE, "confidential")
    log = root / "bundle" / "log.md"
    lines = log.read_text(encoding="utf-8").splitlines()
    index = next(i for i, x in enumerate(lines) if "**Set-sensitivity**" in x)
    rewritten = rewrite(lines[index])  # type: ignore[operator]
    assert rewritten != lines[index]
    lines[index] = rewritten
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = preview_undo_sensitivity(Workspace.open(root), SOURCE)
    assert result == SensitivityNotUndoable("no_record"), rewritten
