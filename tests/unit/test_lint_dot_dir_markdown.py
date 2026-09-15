"""Unit tests for `lint.check_dot_dir_markdown`/`lint.scan_dot_dir_markdown`
(issue #984, the required companion to excluding dot-directories from the
bundle `*.md` walk).

`iter_bundle_markdown`'s exclusion makes the drop SILENT: a bundle author
who put a real concept under a dot-directory (by hand, or via a tool that
writes one) loses it from every count with no signal at all. This check
removes the "silently" -- it is a names-only walk over the SAME dot-directory
rule (mirrors `scan_markdown_under_state_dir`'s own rationale: `collect_docs`/
`_iter_docs` never descends into a dot-directory at all, so there is no
shared walk to fold this into).

`.state/` (`okf.STATE_DIRNAME`) is excluded from THIS check's own scope: it
already has its own, more specific `state-dir-markdown` finding
(`check_state_dir_contains_no_markdown`), and a stray `.md` file there must
produce exactly one finding, not two."""

import re
from pathlib import Path

from openkos import lint
from openkos.model import okf


def _paths_named_by(finding: lint.LintFinding) -> list[str]:
    """Every bundle-relative path a `dot-dir-markdown` detail names.

    The finding is per directory and names its example paths inline, so
    the identity test reads them back out of the prose. Matching only
    `.md` tokens keeps a detail that stopped naming paths failing the
    identity rather than passing on an empty set."""
    return re.findall(r"[\w./-]+\.md", finding.detail)


def test_no_dot_directory_yields_no_findings(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert lint.check_dot_dir_markdown(bundle_dir) == []
    assert lint.scan_dot_dir_markdown(bundle_dir) == []


def test_markdown_under_a_dot_directory_is_flagged(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    dot_dir = bundle_dir / ".obsidian"
    dot_dir.mkdir(parents=True)
    stray_path = dot_dir / "workspace.md"
    stray_path.write_text("Not a concept.\n", encoding="utf-8")

    findings = lint.check_dot_dir_markdown(bundle_dir)

    assert len(findings) == 1
    (finding,) = findings
    assert finding.kind == "dot-dir-markdown"
    # The finding is about the DIRECTORY -- that is the decision a human
    # makes here, and makes once.
    assert finding.path == ".obsidian"
    assert "Knowledge Object" in finding.detail
    # The offending file is still named, so the report stays actionable.
    assert ".obsidian/workspace.md" in finding.detail


def test_markdown_under_a_nested_dot_directory_is_flagged(tmp_path: Path) -> None:
    """The rule is about ANY dot-directory component, at any depth -- not a
    named list (#984's own "what about `.vscode/`" question)."""
    bundle_dir = tmp_path / "bundle"
    nested_dir = bundle_dir / "concepts" / ".private"
    nested_dir.mkdir(parents=True)
    (nested_dir / "x.md").write_text("Body.\n", encoding="utf-8")

    findings = lint.check_dot_dir_markdown(bundle_dir)

    assert [finding.path for finding in findings] == ["concepts/.private"]
    assert "concepts/.private/x.md" in findings[0].detail


def test_same_named_dot_directories_at_different_depths_do_not_collide(
    tmp_path: Path,
) -> None:
    """Two directories that merely SHARE a name are two findings (#984).

    Keyed on the bare component name, `.obsidian` and
    `concepts/.obsidian` collapse into one finding whose `path` names no
    real location -- and the example cap can then hide that more than one
    directory is involved. This check is the only disclosure of an
    otherwise silent exclusion, so a merged or misnamed line weakens
    exactly the safety net it exists to provide."""
    bundle_dir = tmp_path / "bundle"
    (bundle_dir / ".obsidian").mkdir(parents=True)
    (bundle_dir / "concepts" / ".obsidian").mkdir(parents=True)
    (bundle_dir / ".obsidian" / "a.md").write_text("A.\n", encoding="utf-8")
    (bundle_dir / "concepts" / ".obsidian" / "b.md").write_text(
        "B.\n", encoding="utf-8"
    )

    findings = lint.check_dot_dir_markdown(bundle_dir)

    assert [finding.path for finding in findings] == [
        ".obsidian",
        "concepts/.obsidian",
    ]
    # Each finding is spelled as a real bundle-relative directory.
    for finding in findings:
        assert (bundle_dir / finding.path).is_dir()


def test_one_finding_per_dot_directory_not_one_per_file(tmp_path: Path) -> None:
    """Many files in one dot-directory collapse to ONE finding, and the
    paths it names are sorted (#984).

    An editor pointed at `bundle/` grows dot-directories that fill with
    markdown -- Obsidian's `.obsidian/.trash/` holds every note the user
    ever deleted -- so one line per file turns a health report into a wall
    on every run, repeating a single fact once per file."""
    bundle_dir = tmp_path / "bundle"
    dot_dir = bundle_dir / ".obsidian"
    dot_dir.mkdir(parents=True)
    (dot_dir / "b.md").write_text("B.\n", encoding="utf-8")
    (dot_dir / "a.md").write_text("A.\n", encoding="utf-8")

    findings = lint.check_dot_dir_markdown(bundle_dir)

    assert [finding.path for finding in findings] == [".obsidian"]
    assert "2 `.md` files" in findings[0].detail
    assert findings[0].detail.index(".obsidian/a.md") < findings[0].detail.index(
        ".obsidian/b.md"
    )


def test_two_dot_directories_yield_one_finding_each(tmp_path: Path) -> None:
    """Collapsing is per top-level dot-directory, not per bundle: two
    unrelated tools must not be merged into one undiagnosable line."""
    bundle_dir = tmp_path / "bundle"
    (bundle_dir / ".obsidian").mkdir(parents=True)
    (bundle_dir / ".vscode").mkdir()
    (bundle_dir / ".obsidian" / "a.md").write_text("A.\n", encoding="utf-8")
    (bundle_dir / ".vscode" / "b.md").write_text("B.\n", encoding="utf-8")

    findings = lint.check_dot_dir_markdown(bundle_dir)

    assert [finding.path for finding in findings] == [".obsidian", ".vscode"]


def test_a_flood_of_files_names_examples_then_a_count(tmp_path: Path) -> None:
    """Past `_DOT_DIR_EXAMPLES` the finding stops listing and counts
    (#984). The cap is what keeps a real `.obsidian/.trash/` -- hundreds of
    deleted notes -- from being the whole `lint` report, while still
    naming enough paths to recognise WHAT the directory holds."""
    bundle_dir = tmp_path / "bundle"
    trash = bundle_dir / ".obsidian" / ".trash"
    trash.mkdir(parents=True)
    for index in range(10):
        (trash / f"note-{index:02d}.md").write_text("Body.\n", encoding="utf-8")

    findings = lint.check_dot_dir_markdown(bundle_dir)

    assert len(findings) == 1
    detail = findings[0].detail
    assert "10 `.md` files" in detail
    assert detail.count(".obsidian/.trash/note-") == lint._DOT_DIR_EXAMPLES
    assert f"and {10 - lint._DOT_DIR_EXAMPLES} more" in detail


def test_markdown_outside_any_dot_directory_is_not_flagged(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    (bundle_dir / "concepts").mkdir(parents=True)
    (bundle_dir / "concepts" / "ordinary.md").write_text(
        "---\ntype: Concept\ntitle: Ordinary\n---\nBody.\n", encoding="utf-8"
    )

    assert lint.check_dot_dir_markdown(bundle_dir) == []


def test_state_dir_markdown_is_excluded_from_this_check(tmp_path: Path) -> None:
    """`.state/` keeps its own, more specific `state-dir-markdown` finding --
    a stray `.md` file there must produce exactly ONE finding total (the
    state-dir one), never two for the same file."""
    bundle_dir = tmp_path / "bundle"
    state_dir = bundle_dir / okf.STATE_DIRNAME
    state_dir.mkdir(parents=True)
    (state_dir / "x.md").write_text("Body.\n", encoding="utf-8")

    assert lint.check_dot_dir_markdown(bundle_dir) == []

    state_dir_findings = lint.check_state_dir_contains_no_markdown(bundle_dir)
    dot_dir_findings = lint.check_dot_dir_markdown(bundle_dir)
    assert len(state_dir_findings) + len(dot_dir_findings) == 1


def test_scan_reports_exactly_what_the_walk_drops(tmp_path: Path) -> None:
    """The check is DEFINED against the walk, not against a second copy of
    its rule (#984).

    `scan_dot_dir_markdown` exists to name what `okf.iter_bundle_markdown`
    drops, so the two have to answer with one rule. Stating the rule twice
    -- once in the walk, once in the check -- is the failure this pins: the
    two agree on the day they are written and drift the moment either side
    is widened, and the direction of the drift is the bad one. A walk that
    excludes MORE than the check reports drops files with no signal at all,
    which is precisely the silence this check was added to remove.

    So it is asserted as an identity over a mixed bundle rather than as a
    list of expected paths: everything `rglob` finds and the walk does not
    yield is reported here, minus `.state/`, which keeps its own more
    specific finding."""
    bundle_dir = tmp_path / "bundle"
    (bundle_dir / "concepts").mkdir(parents=True)
    (bundle_dir / ".obsidian" / "nested").mkdir(parents=True)
    (bundle_dir / "concepts" / ".private").mkdir()
    (bundle_dir / okf.STATE_DIRNAME).mkdir()

    for rel in (
        "index.md",
        "log.md",
        "concepts/stoicism.md",
        ".hidden.md",
        ".obsidian/workspace.md",
        ".obsidian/nested/deep.md",
        "concepts/.private/draft.md",
        f"{okf.STATE_DIRNAME}/stray.md",
    ):
        (bundle_dir / rel).write_text("body\n", encoding="utf-8")

    on_disk = set(bundle_dir.rglob("*.md"))
    walked = set(okf.iter_bundle_markdown(bundle_dir))
    state_dir = bundle_dir / okf.STATE_DIRNAME
    dropped_outside_state = {
        path for path in on_disk - walked if state_dir not in path.parents
    }

    assert set(lint.scan_dot_dir_markdown(bundle_dir)) == dropped_outside_state

    # And the identity has to hold for what `lint` RENDERS, not only for
    # the helper: `cli.main`'s `lint()` calls `check_dot_dir_markdown`, so
    # a guard aimed only at the scan would be pinning the copy nobody
    # runs. Every dropped path reaches a finding.
    reported = {
        bundle_dir / path
        for finding in lint.check_dot_dir_markdown(bundle_dir)
        for path in _paths_named_by(finding)
    }
    assert reported == dropped_outside_state

    # Non-vacuous: the walk really does drop something here, and really
    # does keep the dot-FILE at the bundle root.
    assert dropped_outside_state
    assert bundle_dir / ".hidden.md" in walked
