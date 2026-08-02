"""Direct unit tests for `_reject_drifted_targets`' key contract (#325).

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
    """A changed in-tree target refuses (exit 1) and the message names it by
    its workspace-relative POSIX path -- the guard derives that name from
    the key via `relative_to(layout.root)`, so the refusal reads exactly as
    it did under string keys and never leaks an absolute path for an
    in-tree target."""
    layout = _layout(tmp_path)
    target = layout.bundle_dir / "x.md"
    target.write_bytes(b"edited while the prompt waited\n")

    with pytest.raises(typer.Exit) as excinfo:
        main._reject_drifted_targets(
            layout, {target: b"what the plan was computed from\n"}, "test-verb"
        )

    assert excinfo.value.exit_code == 1
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

    assert excinfo.value.exit_code == 1
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
