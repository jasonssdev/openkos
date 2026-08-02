"""Direct unit tests for `_reject_drifted_targets`' key contract (#325)
and its refusal-reporting contract (#319).

Every verb-level drift test in this package drives the guard end-to-end
through a CLI invocation; these are the only tests that call it directly,
because what they pin is not a verb's behavior but the guard's own
signature: `expected` is keyed by the ABSOLUTE `Path` objects the verb will
actually hand to `fsio.write_atomic` (or delete), never by a string rebuilt
from a concept-id. Before #325 callers interpolated keys (`f"bundle/
{canonical}.md"`) while the write went through `_resolve_concept_path` --
two independent constructions that merely happened to agree, and the known
divergences (a Windows drive-anchored id, an absolute `rel` out of an
unmerge ledger) failed closed only by accident of `read_bytes` raising.
The `Path`-keyed contract makes the wrong thing hard to express, and makes
the out-of-tree case an EXPLICIT refusal rather than an incidental one.

#319 added the reporting contract pinned below: the guard already
DISTINGUISHED three failure shapes internally (bytes differ, `OSError` on
read, `relative_to` raising) but flattened all three into one "changed on
disk" sentence and exit 1 -- so an operator whose target had VANISHED was
told to re-run (which refuses again on the same path), and an out-of-tree
key was blamed on an edit that never happened. The refusal now reports each
non-empty bucket separately, names each path by what the verb was about to
do to it (`deletes` marks the unlink targets), tailors the recovery advice
per bucket (or takes a verb-specific `remedy`), and exits 3 -- the one
failure code automation may treat as retryable when the message says so.
"""

from pathlib import Path

import pytest
import typer

from openkos import config
from openkos.cli import main


def _layout(root: Path) -> config.WorkspaceLayout:
    layout = config.WorkspaceLayout(root)
    layout.bundle_dir.mkdir(parents=True, exist_ok=True)
    return layout


def test_matching_resolved_path_keys_are_not_drift(tmp_path: Path) -> None:
    """The positive contract: keys built the way callers actually build them
    (`layout.bundle_dir / "x.md"`-style resolved paths, the SAME objects the
    write phase receives) compare clean and the guard returns without
    raising."""
    layout = _layout(tmp_path)
    target = layout.bundle_dir / "x.md"
    target.write_bytes(b"unchanged body\n")

    main._reject_drifted_targets(layout, {target: b"unchanged body\n"}, "test-verb")


def test_a_drifted_target_is_named_workspace_relative(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A changed in-tree target refuses (exit 3, #319) and the message names
    it by its workspace-relative POSIX path -- the guard derives that name
    from the key via `relative_to(layout.root)`, so the refusal reads
    exactly as it did under string keys and never leaks an absolute path
    for an in-tree target."""
    layout = _layout(tmp_path)
    target = layout.bundle_dir / "x.md"
    target.write_bytes(b"edited while the prompt waited\n")

    with pytest.raises(typer.Exit) as excinfo:
        main._reject_drifted_targets(
            layout, {target: b"what the plan was computed from\n"}, "test-verb"
        )

    assert excinfo.value.exit_code == 3
    err = capsys.readouterr().err
    assert "refusing to write --" in err
    assert "plan: bundle/x.md. Nothing was written" in err


def test_an_out_of_tree_absolute_key_is_refused_even_when_readable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A key outside `layout.root` is drift BY DEFINITION, before any byte
    is read: a write target the workspace does not contain is one the
    operator was never shown, so blessing it because its bytes happen to
    match would be the same silent-authority failure the guard exists to
    close. The file here exists AND matches its snapshot exactly -- the
    refusal must come from the path, not the comparison -- and the message
    names the raw path, since no workspace-relative spelling of it exists.
    Before #325 this case failed closed only when the accidental
    `layout.root / rel` join produced an unreadable path; now it is refused
    explicitly and deterministically."""
    workspace = tmp_path / "workspace"
    layout = _layout(workspace)
    outside = tmp_path / "elsewhere.md"
    outside.write_bytes(b"matching bytes\n")

    with pytest.raises(typer.Exit) as excinfo:
        main._reject_drifted_targets(
            layout, {outside: b"matching bytes\n"}, "test-verb"
        )

    assert excinfo.value.exit_code == 3
    err = capsys.readouterr().err
    assert "refusing to write --" in err
    assert str(outside) in err


def test_refusal_message_entries_are_sorted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Determinism pin: with several drifted targets the message lists them
    sorted, regardless of `expected`'s insertion order -- the guard sorts
    its collected entries, so two runs over the same drift always produce
    the same quotable refusal."""
    layout = _layout(tmp_path)
    first = layout.bundle_dir / "a.md"
    second = layout.bundle_dir / "b.md"
    for path in (second, first):
        path.write_bytes(b"edited\n")

    with pytest.raises(typer.Exit):
        main._reject_drifted_targets(
            layout, {second: b"planned\n", first: b"planned\n"}, "test-verb"
        )

    err = capsys.readouterr().err
    assert err.index("bundle/a.md") < err.index("bundle/b.md")


# -- #319: three buckets, delete labeling, per-bucket remedy, exit 3 ---------


def test_a_changed_only_refusal_keeps_the_rerun_remedy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The changed bucket is the one a plain re-run actually fixes -- the
    recompute picks up the current state -- so its default remedy stays the
    re-run advice, and no other bucket's clause or advice appears for it."""
    layout = _layout(tmp_path)
    target = layout.bundle_dir / "x.md"
    target.write_bytes(b"edited\n")

    with pytest.raises(typer.Exit) as excinfo:
        main._reject_drifted_targets(layout, {target: b"planned\n"}, "test-verb")

    assert excinfo.value.exit_code == 3
    err = capsys.readouterr().err
    assert "1 write target(s) changed on disk" in err
    assert "re-run to recompute over the current bundle." in err.lower()
    assert "vanished" not in err
    assert "outside the workspace" not in err
    # No delete targets were declared, so the delete half of the footer
    # must not appear -- claiming "nothing was deleted" for a verb that
    # deletes nothing would be noise at best and misleading at worst.
    assert "nothing was deleted" not in err


def test_a_vanished_target_gets_the_restore_first_remedy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """#319 bucket 2: a target that VANISHED (deleted/unreadable) refuses
    again on every plain re-run -- the read keeps failing -- so telling the
    operator to re-run, as the flattened message did, sent them in a
    circle. The vanished bucket must be reported as vanished, not as
    "changed on disk", and the advice must say the path has to be restored
    first -- and ONLY that (R4 wave 4): the old "or confirm the deletion is
    intended" clause was a dead end, because for `forget`/`purge` delete
    targets a re-run fails in Phase A (the file cannot be resolved) before
    any confirmation could matter."""
    layout = _layout(tmp_path)
    target = layout.bundle_dir / "gone.md"
    # Never created on disk: the guard's read raises, which is the same
    # observation as deleted-during-the-prompt.

    with pytest.raises(typer.Exit) as excinfo:
        main._reject_drifted_targets(layout, {target: b"planned\n"}, "test-verb")

    assert excinfo.value.exit_code == 3
    err = capsys.readouterr().err
    assert "refusing to write --" in err
    assert "1 write target(s) vanished" in err
    assert "bundle/gone.md" in err
    assert "restore them first" in err
    assert "confirm the deletion" not in err
    assert "changed on disk" not in err
    # The changed-only advice alone would be a lie here: a plain re-run
    # refuses again on the same path.
    assert "re-run to recompute over the current bundle" not in err.lower()


def test_an_out_of_tree_target_is_reported_as_deterministic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """#319 bucket 3 (and the wave-2 R4 WARNING): an out-of-tree key is
    produced by the same inputs every run -- nothing "changed on disk after
    this run computed its plan", and no re-run can clear it. The refusal
    must say so instead of naming a cause that never happened."""
    workspace = tmp_path / "workspace"
    layout = _layout(workspace)
    outside = tmp_path / "elsewhere.md"
    outside.write_bytes(b"matching bytes\n")

    with pytest.raises(typer.Exit) as excinfo:
        main._reject_drifted_targets(
            layout, {outside: b"matching bytes\n"}, "test-verb"
        )

    assert excinfo.value.exit_code == 3
    err = capsys.readouterr().err
    assert "outside the workspace" in err
    assert "deterministic" in err
    assert "changed on disk" not in err
    assert "re-run to recompute over the current bundle" not in err.lower()


def test_all_three_buckets_are_reported_distinctly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One refusal can carry all three buckets at once; each non-empty
    bucket gets its own clause naming its own paths, and the combined
    remedy carries the vanished restore-first advice AND the out-of-tree
    determinism note alongside the changed bucket's clause."""
    workspace = tmp_path / "workspace"
    layout = _layout(workspace)
    changed = layout.bundle_dir / "changed.md"
    changed.write_bytes(b"edited\n")
    vanished = layout.bundle_dir / "vanished.md"
    outside = tmp_path / "elsewhere.md"
    outside.write_bytes(b"whatever\n")

    with pytest.raises(typer.Exit) as excinfo:
        main._reject_drifted_targets(
            layout,
            {changed: b"planned\n", vanished: b"planned\n", outside: b"planned\n"},
            "test-verb",
        )

    assert excinfo.value.exit_code == 3
    err = capsys.readouterr().err
    assert "1 write target(s) changed on disk" in err
    assert "bundle/changed.md" in err
    assert "1 write target(s) vanished" in err
    assert "bundle/vanished.md" in err
    assert "outside the workspace" in err
    assert str(outside) in err
    assert "restore" in err
    assert "deterministic" in err


def test_delete_targets_are_labeled_and_the_footer_says_nothing_was_deleted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """#319: a path in `deletes` was about to be UNLINKED, not written, and
    the message must say which is which -- "refusing to write" on a delete
    told the operator the wrong thing was about to happen. When any delete
    target exists the footer also extends to "nothing was deleted", so the
    fail-closed claim covers both halves of the plan."""
    layout = _layout(tmp_path)
    written = layout.bundle_dir / "written.md"
    written.write_bytes(b"edited\n")
    doomed = layout.bundle_dir / "doomed.md"
    doomed.write_bytes(b"also edited\n")

    with pytest.raises(typer.Exit) as excinfo:
        main._reject_drifted_targets(
            layout,
            {written: b"planned\n", doomed: b"planned\n"},
            "test-verb",
            deletes=frozenset({doomed}),
        )

    assert excinfo.value.exit_code == 3
    err = capsys.readouterr().err
    assert "1 write target(s) changed on disk" in err
    assert "bundle/written.md" in err
    assert "1 delete target(s) changed on disk" in err
    assert "bundle/doomed.md" in err
    assert "Nothing was written, nothing was deleted." in err


def test_a_custom_remedy_replaces_the_default_advice(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """#319/#328: a verb whose re-run is NOT a safe recovery (unmerge) must
    be able to substitute its own recovery sentence; when it does, it
    replaces the DEFAULT re-run advice -- the changed bucket's -- because
    two contradictory recovery sentences would be worse than the flattened
    message this redesign removes. It does NOT suppress the vanished/
    out-of-tree advisory sentences (R3+R4 wave 5, pinned in the next test);
    here those buckets are empty, so the custom remedy stands alone."""
    layout = _layout(tmp_path)
    target = layout.bundle_dir / "x.md"
    target.write_bytes(b"edited\n")

    with pytest.raises(typer.Exit) as excinfo:
        main._reject_drifted_targets(
            layout,
            {target: b"planned\n"},
            "test-verb",
            remedy="Copy your edit somewhere safe first.",
        )

    assert excinfo.value.exit_code == 3
    err = capsys.readouterr().err
    assert "Copy your edit somewhere safe first." in err
    assert "re-run to recompute over the current bundle" not in err.lower()
    # Empty buckets contribute no advisory: the composition rule appends
    # them only when they have paths to speak about.
    assert "vanished" not in err
    assert "deterministic" not in err


def test_a_custom_remedy_still_carries_the_vanished_advisory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """R3+R4 wave 5: the vanished (and out-of-tree) advisory sentences are
    facts about the refusal, not recovery advice a verb can meaningfully
    substitute -- under wholesale replacement, unmerge's "copy your edit
    somewhere safe" remedy talked about copying an edit that, for a
    VANISHED target, does not exist, and dropped the one instruction
    (restore the path) without which no re-run can ever proceed. A custom
    remedy therefore replaces only the default re-run advice; each
    non-empty advisory bucket's sentence is appended after it."""
    layout = _layout(tmp_path)
    vanished = layout.bundle_dir / "gone.md"
    # Never created: the guard's read raises.

    with pytest.raises(typer.Exit) as excinfo:
        main._reject_drifted_targets(
            layout,
            {vanished: b"planned\n"},
            "test-verb",
            remedy="Copy your edit somewhere safe first.",
        )

    assert excinfo.value.exit_code == 3
    err = capsys.readouterr().err
    assert "Copy your edit somewhere safe first." in err
    assert "restore them first" in err
    # Composition order: the remedy in effect first, advisories after it.
    assert err.index("Copy your edit somewhere safe first.") < err.index(
        "restore them first"
    )
    # The DEFAULT advice is what the custom remedy replaces -- it must not
    # leak back in alongside it.
    assert "re-run to recompute over the current bundle" not in err.lower()


def test_the_refusal_never_reads_like_phase_bs_failure_sentence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """#234's rule, re-pinned across the new bucket combinations: this
    message reports a run that never started writing, Phase B's "No path
    was written." reports one that failed after it began -- a bug report
    quoting either must remain attributable to exactly one of them."""
    layout = _layout(tmp_path)
    changed = layout.bundle_dir / "changed.md"
    changed.write_bytes(b"edited\n")
    vanished = layout.bundle_dir / "vanished.md"

    with pytest.raises(typer.Exit):
        main._reject_drifted_targets(
            layout,
            {changed: b"planned\n", vanished: b"planned\n"},
            "test-verb",
            deletes=frozenset({vanished}),
        )

    err = capsys.readouterr().err
    assert "No path was written." not in err
    assert "Nothing was written" in err
