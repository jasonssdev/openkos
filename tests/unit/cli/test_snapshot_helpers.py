"""Unit tests for the shared workspace-snapshot helpers (issue #281).

These helpers back every "a refused command wrote nothing" assertion in the
CLI test suite. Before #281 each CLI test module carried its own verbatim
copy, and every copy walked `root.rglob("*")` unfiltered -- so an assertion
whose stated contract is "the bundle was not written" was in fact asserting
byte-equality over git's private state too. Measured on a workspace after
`init` plus one `ingest`: 69 of the 82 compared entries lived under `.git/`.

That passed only because the refusal paths return before touching git. The
moment any refusal path gains a git read -- a cleanliness probe, a status
check, anything that refreshes the index -- git may rewrite `.git/index`
for reasons that depend on filesystem timestamp granularity (its "racily
clean" handling), and the test fails intermittently in a module unrelated
to whatever change introduced the probe. That is the exact shape of #236.

The helpers are tested directly, not only through their callers, because a
silent regression here weakens roughly 120 assertions (spread over about 260
call sites, since each assertion captures a before and an after) at once
without failing anything.
"""

import os
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from tests.unit.cli.conftest import (
    snapshot_bytes,
    snapshot_including_git,
    snapshot_with_mtime,
    snapshot_with_symlinks,
)

Snapshotter = Callable[[Path], Mapping[Path, object]]
"""`Mapping`, not `dict`. `dict` is INVARIANT in its value type, so none of
the three concrete return types (`Entry`, `MtimeEntry`) is assignable to
`dict[Path, object]` and the tuple below fails `mypy --strict`, which this
repo runs over `tests` as a required CI gate. `Mapping` is covariant in its
value type, so it accepts all three (#281 review, R1/R3 both CRITICAL)."""

EXCLUDING_SNAPSHOTTERS: tuple[Snapshotter, ...] = (
    snapshot_bytes,
    snapshot_with_mtime,
    snapshot_with_symlinks,
)
"""Every variant that applies the exclusion. Parametrized over rather than
looped over inside one test body: when the shared exclusion regresses, a
loop stops at the first failing variant and reports nothing about the other
two, which is exactly the blast-radius signal a maintainer needs here."""


def _workspace_with_git(root: Path) -> None:
    """Build a root mixing real bundle content with git directories at TWO
    depths, plus a decoy whose name merely STARTS with `.git`.

    The nested `bundle/vendored/.git` is what distinguishes "excluded
    anywhere in the path" from "excluded only at the root" -- without it the
    component-anywhere assertions below would pass against a root-only
    implementation, which is precisely the vacuous pin the #281 review
    caught (R1/R3/R4 all flagged it).
    """
    (root / "bundle").mkdir()
    (root / "bundle" / "note.md").write_text("content", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "index").write_bytes(b"\x00binary")
    (root / ".git" / "refs").mkdir()
    (root / ".git" / "refs" / "HEAD").write_text("ref", encoding="utf-8")
    (root / "bundle" / "vendored").mkdir()
    (root / "bundle" / "vendored" / ".git").mkdir()
    (root / "bundle" / "vendored" / ".git" / "index").write_bytes(b"\x00nested")
    (root / ".gitignore").write_text("ignored\n", encoding="utf-8")


@pytest.mark.parametrize(
    "snapshotter", EXCLUDING_SNAPSHOTTERS, ids=lambda f: f.__name__
)
class TestGitInternalsAreExcluded:
    def test_the_root_git_directory_is_omitted(
        self, snapshotter: Snapshotter, tmp_path: Path
    ) -> None:
        """The defect #281 reports: `.git/` entries are compared byte for
        byte by assertions that claim to be about the bundle."""
        _workspace_with_git(tmp_path)

        assert not [k for k in snapshotter(tmp_path) if k.parts[:-1] == (".git",)]

    def test_a_nested_git_directory_is_omitted_too(
        self, snapshotter: Snapshotter, tmp_path: Path
    ) -> None:
        """A snapshot root is not always the workspace root: `test_init.py`'s
        preflight test snapshots four sibling workspaces under one
        `tmp_path`, putting each repository at depth two. An exclusion that
        only checked the FIRST path component would readmit every git
        internal there while this file still reported green -- the vacuous
        pin the #281 review caught.

        Phrased as "nothing INSIDE a `.git`", not "no key mentions `.git`",
        because the node itself is deliberately kept -- see
        `test_the_git_directory_node_itself_is_kept`."""
        _workspace_with_git(tmp_path)

        assert not [k for k in snapshotter(tmp_path) if ".git" in k.parts[:-1]]

    def test_the_git_directory_node_itself_is_kept(
        self, snapshotter: Snapshotter, tmp_path: Path
    ) -> None:
        """Only the CONTENTS are excluded. The node's mere existence is
        product behavior, not ambient noise: `openkos init` creates the
        repository, so a refused `init` that created one anyway is a real
        defect, and excluding the node too would leave nothing in the suite
        able to see it (#281 review, R1/R3). A directory node carries no
        bytes and no timestamp, so keeping it reintroduces no flakiness."""
        _workspace_with_git(tmp_path)

        entries = snapshotter(tmp_path)

        assert entries[Path(".git")] is None
        assert entries[Path("bundle/vendored/.git")] is None

    def test_a_file_merely_prefixed_git_is_still_captured(
        self, snapshotter: Snapshotter, tmp_path: Path
    ) -> None:
        """`.gitignore` is ordinary workspace content. Excluding `.git` by
        prefix match rather than by exact path component would silently drop
        it -- and dropping content from a "wrote nothing" snapshot is the
        same class of defect as including git's internals, just inverted."""
        _workspace_with_git(tmp_path)

        assert Path(".gitignore") in snapshotter(tmp_path)


class TestExclusionKeepsRealContent:
    def test_real_content_survives_the_exclusion(self, tmp_path: Path) -> None:
        _workspace_with_git(tmp_path)

        assert snapshot_bytes(tmp_path)[Path("bundle/note.md")] == b"content"

    def test_a_git_write_does_not_change_the_snapshot(self, tmp_path: Path) -> None:
        """The regression this whole issue exists to prevent: a snapshot
        taken before a git-internal write must equal one taken after."""
        _workspace_with_git(tmp_path)
        before = snapshot_bytes(tmp_path)

        (tmp_path / ".git" / "index").write_bytes(b"\x00rewritten by a status probe")
        (tmp_path / ".git" / "ORIG_HEAD").write_text("deadbeef", encoding="utf-8")

        assert snapshot_bytes(tmp_path) == before


class TestEachVariantKeepsItsOwnStrength:
    def test_bytes_variant_maps_directories_to_none(self, tmp_path: Path) -> None:
        _workspace_with_git(tmp_path)

        assert snapshot_bytes(tmp_path)[Path("bundle")] is None

    def test_mtime_variant_detects_a_write_then_restore(self, tmp_path: Path) -> None:
        """Why the mtime variant exists at all: a command that writes a file
        and then restores its exact bytes left the bundle touched at the
        filesystem level, and the bytes-only snapshot cannot see that.

        The new mtime is FORCED with `os.utime` rather than obtained by
        writing twice and hoping the clock moved. Linux stamps inode times
        from the kernel's coarse clock, whose resolution is one timer tick
        (commonly 1-4 ms), and CI runs on Linux only -- two back-to-back
        writes of a 7-byte file land inside a single tick, leaving
        `st_mtime_ns` identical and this assertion red for reasons that have
        nothing to do with the helper. That is the very class of
        environment-dependent intermittency this module exists to retire, so
        it must not be reintroduced here (#281 review, R1/R3/R4).
        """
        _workspace_with_git(tmp_path)
        target = tmp_path / "bundle" / "note.md"
        before = snapshot_with_mtime(tmp_path)

        target.write_text("scratch", encoding="utf-8")
        target.write_text("content", encoding="utf-8")
        stamp = target.stat().st_mtime_ns + 1_000_000_000
        os.utime(target, ns=(stamp, stamp))

        assert snapshot_bytes(tmp_path)[Path("bundle/note.md")] == b"content"
        assert snapshot_with_mtime(tmp_path) != before

    def test_including_git_variant_keeps_the_git_directory(
        self, tmp_path: Path
    ) -> None:
        """The deliberate opt-out. `test_init.py`'s preflight test compares
        four freshly initialised workspaces, where the repository `init`
        creates is the measurement rather than ambient noise -- excluding it
        there would leave the suite with no assertion that preflight outcome
        does not change git setup."""
        _workspace_with_git(tmp_path)

        entries = snapshot_including_git(tmp_path)

        assert entries[Path(".git/index")] == b"\x00binary"
        assert entries[Path("bundle/vendored/.git/index")] == b"\x00nested"

    @pytest.mark.skipif(
        os.name != "posix", reason="symlink creation without privilege requires POSIX"
    )
    def test_symlink_variant_records_the_target_without_following_it(
        self, tmp_path: Path
    ) -> None:
        """A symlink is classified before `is_dir()`/`is_file()` are consulted,
        because those follow it: a symlink to a directory would otherwise read
        as a plain directory, and a BROKEN symlink would crash `read_bytes()`."""
        _workspace_with_git(tmp_path)
        (tmp_path / "dangling").symlink_to(tmp_path / "nowhere")
        (tmp_path / "to-dir").symlink_to(tmp_path / "bundle")

        entries = snapshot_with_symlinks(tmp_path)

        assert entries[Path("dangling")] == ("symlink", str(tmp_path / "nowhere"))
        assert entries[Path("to-dir")] == ("symlink", str(tmp_path / "bundle"))
