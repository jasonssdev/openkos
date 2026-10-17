"""`openkos.api` and who can see a page (`page-sensitivity` change, #1517): a
page's `sensitivity` is set in two phases, with the raise-only propagation
`openkos set-sensitivity` applies to a Source's descendants. Through the public
facade only, in a real git-backed workspace whose identity is pinned. A preview
takes no lock, builds no client and writes nothing."""

import dataclasses
from pathlib import Path

import pytest

from openkos import lock
from openkos.api import (
    PageSensitivity,
    SensitivityChanged,
    SensitivityPreview,
    SensitivityRefused,
    SensitivityStale,
    Workspace,
    WorkspaceBusyError,
    apply_sensitivity,
    preview_sensitivity,
)
from openkos.vcs import git as vcs_git
from tests.unit.api import support
from tests.unit.api.test_decisions_apply import _git, _head_files, _pin_identity
from tests.unit.application.curation_support import make_workspace
from tests.unit.cli.commit_phase_support import metadata_of, write_doc

PAGE = "concepts/garden"
SOURCE = "sources/notes"
CHILD = "concepts/from-notes"
OTHER = "concepts/elsewhere"


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _pin_identity(monkeypatch, tmp_path)
    ws = make_workspace(tmp_path, monkeypatch)
    write_doc(
        ws,
        PAGE,
        {"type": "Concept", "title": "The garden", "sensitivity": "private"},
        "Tomatoes.\n",
    )
    write_doc(
        ws,
        SOURCE,
        {"type": "Source", "title": "Notes", "sensitivity": "private"},
        "Raw notes.\n",
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
        "Derived.\n",
    )
    write_doc(
        ws,
        OTHER,
        {"type": "Concept", "title": "Elsewhere", "sensitivity": "public"},
        "Unrelated.\n",
    )
    vcs_git.commit_paths(ws, ["bundle"], "seed pages")
    support.forbid_model_clients(monkeypatch, "a sensitivity change")
    return ws


def _preview(root: Path, concept_id: str, level: PageSensitivity) -> SensitivityPreview:
    preview = preview_sensitivity(Workspace.open(root), concept_id, level)
    assert isinstance(preview, SensitivityPreview), preview
    return preview


def test_a_preview_names_the_page_and_the_direction_and_writes_nothing(
    root: Path,
) -> None:
    before = support.snapshot(root)
    with lock.workspace_lock(root):
        preview = _preview(root, PAGE, "confidential")
    assert preview.concept_id == PAGE
    assert preview.title == "The garden"
    assert preview.current == "private"
    assert preview.level == "confidential"
    assert preview.direction == "raise"
    assert preview.also_raised == ()
    assert preview.version.concept_id == PAGE
    assert preview.version.level == "confidential"
    assert support.snapshot(root) == before


def test_apply_writes_the_field_a_log_line_and_one_commit(root: Path) -> None:
    preview = _preview(root, PAGE, "confidential")
    changed = apply_sensitivity(Workspace.open(root), preview.version)
    assert isinstance(changed, SensitivityChanged), changed
    assert changed.concept_id == PAGE
    assert changed.level == "confidential"
    assert changed.previous == "private"
    assert changed.also_raised == ()
    assert changed.commit_sha == _git(root, "rev-parse", "--short", "HEAD")
    assert changed.committed_confidential is True
    assert metadata_of(root, PAGE)["sensitivity"] == "confidential"
    assert _head_files(root) == ["bundle/concepts/garden.md", "bundle/log.md"]
    assert _git(root, "log", "-1", "--format=%s") == (
        f"openkos: set-sensitivity {PAGE} -> confidential"
    )
    log = (root / "bundle" / "log.md").read_text(encoding="utf-8")
    assert f"**Set-sensitivity**: Set [{PAGE}](/{PAGE}.md) sensitivity to " in log
    # Only the frontmatter field moved: the body is byte-identical.
    text = (root / "bundle" / f"{PAGE}.md").read_text(encoding="utf-8")
    assert text.endswith("\nTomatoes.\n")


def test_a_lowering_is_previewed_and_applied_as_asked(root: Path) -> None:
    preview = _preview(root, PAGE, "public")
    assert preview.direction == "lower"
    changed = apply_sensitivity(Workspace.open(root), preview.version)
    assert isinstance(changed, SensitivityChanged)
    assert changed.previous == "private"
    assert changed.committed_confidential is False
    assert metadata_of(root, PAGE)["sensitivity"] == "public"


def test_raising_a_source_raises_its_descendants_with_it(root: Path) -> None:
    preview = _preview(root, SOURCE, "confidential")
    assert [
        (p.concept_id, p.title, p.before, p.after) for p in preview.also_raised
    ] == [(CHILD, "From the notes", "private", "confidential")]
    changed = apply_sensitivity(Workspace.open(root), preview.version)
    assert isinstance(changed, SensitivityChanged)
    assert changed.also_raised == (CHILD,)
    assert metadata_of(root, CHILD)["sensitivity"] == "confidential"
    assert metadata_of(root, OTHER)["sensitivity"] == "public"
    assert _head_files(root) == [
        "bundle/concepts/from-notes.md",
        "bundle/log.md",
        "bundle/sources/notes.md",
    ]


def test_lowering_a_source_never_lowers_its_descendants(root: Path) -> None:
    raised = apply_sensitivity(
        Workspace.open(root), _preview(root, SOURCE, "confidential").version
    )
    assert isinstance(raised, SensitivityChanged)
    preview = _preview(root, SOURCE, "private")
    assert preview.direction == "lower"
    assert preview.also_raised == ()
    lowered = apply_sensitivity(Workspace.open(root), preview.version)
    assert isinstance(lowered, SensitivityChanged)
    assert metadata_of(root, SOURCE)["sensitivity"] == "private"
    assert metadata_of(root, CHILD)["sensitivity"] == "confidential"


def test_a_level_already_set_is_refused_as_unchanged(root: Path) -> None:
    assert preview_sensitivity(
        Workspace.open(root), PAGE, "private"
    ) == SensitivityRefused("unchanged")


@pytest.mark.parametrize(
    "concept_id", ["concepts/missing", "../outside", "/etc/passwd", "index", ""]
)
def test_a_page_that_is_not_there_is_refused_as_not_found(
    root: Path, concept_id: str
) -> None:
    before = support.snapshot(root)
    assert preview_sensitivity(
        Workspace.open(root), concept_id, "confidential"
    ) == SensitivityRefused("not_found")
    assert support.snapshot(root) == before


def test_a_level_outside_the_three_raises_value_error(root: Path) -> None:
    with pytest.raises(ValueError, match="not a sensitivity level"):
        preview_sensitivity(Workspace.open(root), PAGE, "secret")  # type: ignore[arg-type]


def test_a_missing_field_reads_as_private_and_can_be_written_as_private(
    root: Path,
) -> None:
    write_doc(root, "concepts/bare", {"type": "Concept", "title": "Bare"}, "B.\n")
    preview = _preview(root, "concepts/bare", "private")
    assert preview.current == "private"
    assert preview.direction == "same"


def test_an_edited_page_is_stale_and_writes_nothing(root: Path) -> None:
    preview = _preview(root, PAGE, "confidential")
    page = root / "bundle" / f"{PAGE}.md"
    page.write_text(page.read_text(encoding="utf-8") + "More.\n", encoding="utf-8")
    before = support.snapshot(root)
    assert apply_sensitivity(Workspace.open(root), preview.version) == (
        SensitivityStale()
    )
    assert support.snapshot(root) == before


def test_a_new_descendant_after_the_preview_makes_a_source_raise_stale(
    root: Path,
) -> None:
    preview = _preview(root, SOURCE, "confidential")
    write_doc(
        root,
        "concepts/late",
        {
            "type": "Concept",
            "title": "Late",
            "sensitivity": "private",
            "provenance": [SOURCE],
        },
    )
    assert apply_sensitivity(Workspace.open(root), preview.version) == (
        SensitivityStale()
    )
    assert metadata_of(root, "concepts/late")["sensitivity"] == "private"


def test_an_altered_version_is_stale(root: Path) -> None:
    preview = _preview(root, PAGE, "confidential")
    for altered in (
        dataclasses.replace(preview.version, level="public"),
        dataclasses.replace(preview.version, content_digest="0" * 64),
    ):
        assert apply_sensitivity(Workspace.open(root), altered) == SensitivityStale()
    assert metadata_of(root, PAGE)["sensitivity"] == "private"


def test_a_level_set_meanwhile_is_refused_as_unchanged(root: Path) -> None:
    preview = _preview(root, PAGE, "confidential")
    again = _preview(root, PAGE, "confidential")
    assert isinstance(
        apply_sensitivity(Workspace.open(root), again.version), SensitivityChanged
    )
    assert apply_sensitivity(Workspace.open(root), preview.version) == (
        SensitivityRefused("unchanged")
    )


def test_a_busy_workspace_raises_and_writes_nothing(root: Path) -> None:
    preview = _preview(root, PAGE, "confidential")
    before = support.snapshot(root)
    with lock.workspace_lock(root), pytest.raises(WorkspaceBusyError):
        apply_sensitivity(Workspace.open(root), preview.version)
    assert support.snapshot(root) == before


def test_a_commit_that_cannot_be_made_is_reported_once(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_CONFIG_COUNT", "0")
    warnings: list[str] = []
    preview = _preview(root, PAGE, "confidential")
    changed = apply_sensitivity(
        Workspace.open(root), preview.version, on_warning=warnings.append
    )
    assert isinstance(changed, SensitivityChanged)
    assert changed.commit_sha is None
    assert len(warnings) == 1
    assert warnings[0].startswith(
        "the sensitivity change was applied but not committed"
    )
    assert metadata_of(root, PAGE)["sensitivity"] == "confidential"


def test_lowering_a_source_never_raises_a_descendant_below_the_new_level(
    root: Path,
) -> None:
    """A downgrade never cascades, even where combining would raise a
    descendant that sits below the new, lower level (ADR-0009)."""
    write_doc(
        root,
        SOURCE,
        {"type": "Source", "title": "Notes", "sensitivity": "confidential"},
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
    preview = _preview(root, SOURCE, "private")
    assert preview.direction == "lower"
    assert preview.also_raised == ()
    changed = apply_sensitivity(Workspace.open(root), preview.version)
    assert isinstance(changed, SensitivityChanged)
    assert changed.also_raised == ()
    assert metadata_of(root, CHILD)["sensitivity"] == "public"
