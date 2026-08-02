"""CLI-test-specific fixtures, plus the shared workspace-snapshot helpers.

Two responsibilities, and the second one is imported by name from every CLI
test module that asserts a command wrote nothing: `snapshot_bytes`,
`snapshot_with_mtime`, `snapshot_with_symlinks` and `snapshot_including_git`
below. They live here rather than in a module of their own because this
package's established way to share a test helper is a direct import from a
conftest (`tests/unit/vcs/conftest.py`'s `isolate_git_identity` is imported
that way by six CLI modules). #281 replaced 18 verbatim per-module copies
with them.

`confirm_after` (#306) and `echo_after` (#313) share the same import
convention but are neither snapshot helpers nor wrote-nothing assertions:
both land an edit inside the window a mutating verb leaves open between its
Phase-A snapshot and its first Phase-B write, and only the modules covering
the drift guard import them. They differ only in which run they can reach --
`confirm_after` stubs `typer.confirm`, so it needs a prompt; `echo_after`
stubs `typer.echo`, which is what makes `--auto` and `review: false`
testable at all.

The offline-Ollama default that used to live here (#183) moved up to
`tests/unit/conftest.py` in #217. Two reasons, both discovered by measuring
rather than reading: it was INCOMPLETE (it stubbed `chat` and `embed` but not
`list_models`, so every test running `openkos init` still made a real HTTP
request), and it was SCOPED TOO NARROWLY (`tests/unit/vcs/conftest.py`'s
`tmp_git_repo` fixture and `tests/unit/retrieval/test_answer.py` also drive
`ingest --auto`, and sat outside this package's reach).

Nothing here shadows it any more. A same-named fixture in this file would
override the parent's for every test under `tests/unit/cli/**` -- which is
exactly how the incomplete stub kept winning -- so the seam default is
deliberately defined once, at the unit-suite root.
"""

import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import typer

_EXCLUDED_DIRS = (".git",)
"""Directories whose CONTENTS a workspace snapshot must not compare -- the
directory NODE itself is always kept; see `_walk` for why that matters.

`.git` is here because `openkos init` creates a git repository inside the
workspace root, so an unfiltered `rglob("*")` puts git's private state into
every "this command wrote nothing" assertion. Measured on a workspace after
`init` plus one `ingest`: 69 of the 82 entries lived under `.git/` (#281).

It passes today only because the refusal paths return before touching git --
a property of the current control flow, not of the assertion. Any refusal
path that later gains a git read may let git rewrite `.git/index` for
reasons that depend on filesystem timestamp granularity (its "racily clean"
handling), and the assertion then fails intermittently in a module unrelated
to the change that introduced the probe. That is #236's shape exactly.

Matched at ANY DEPTH, as an exact PATH COMPONENT:

- Any depth, not just the workspace root, because a snapshot root is not
  always the workspace: `test_init.py`'s preflight test snapshots four
  sibling workspaces under one `tmp_path`, so each repository sits at depth
  two. A root-only check would silently readmit every git internal there.
- Exact component, never a name prefix: `.gitignore` is ordinary workspace
  content and must stay in the snapshot.

The one snapshot that deliberately does NOT apply this is
`snapshot_including_git`; see its docstring for when git IS the measurement.
"""


def _walk(root: Path, *, apply_exclusions: bool = True) -> Iterator[Path]:
    """Yield every entry under `root`, so all four snapshot variants below
    share one definition of what a workspace snapshot covers.

    Excludes the CONTENTS of an `_EXCLUDED_DIRS` directory while KEEPING the
    directory node itself. That distinction is load-bearing, not tidiness:
    the contents are what shift under the assertion for reasons unrelated to
    the command, but the `.git` entry's mere EXISTENCE is product behavior --
    `openkos init` creates the repository (`cli/main.py`), so a refused
    `init` that created one anyway is a real defect, and dropping the node
    too would leave no assertion in the suite able to see it (#281 review,
    R1/R3). A directory node carries no bytes and no timestamp in any
    variant below, so keeping it costs exactly nothing in flakiness.

    `apply_exclusions=False` opts out entirely, for the single caller that
    needs git's contents in the comparison.
    """
    for path in root.rglob("*"):
        parts = path.relative_to(root).parts
        if apply_exclusions and any(part in _EXCLUDED_DIRS for part in parts[:-1]):
            continue
        yield path


Entry = bytes | None | tuple[str, str]
"""What one entry maps to in every variant except `snapshot_with_mtime`:
its exact bytes, `None` for a directory, or `("symlink", target)`."""

MtimeEntry = tuple[bytes, int] | None | tuple[str, str]
"""`Entry` with `st_mtime_ns` alongside the bytes of a regular file."""


def _classify(path: Path) -> Entry:
    """Classify one entry without ever following a symlink.

    A symlink is checked FIRST because `Path.is_dir()`/`is_file()` follow it
    -- checking those first would either misread a symlink-to-dir as a plain
    directory or crash `read_bytes()` on a broken one. EVERY variant routes
    through this: these helpers back ~120 assertions across 19 modules, so a
    dangling symlink in a workspace must fail the caller's comparison with a
    readable diff, never raise `FileNotFoundError` out of this module with a
    traceback pointing at shared infrastructure (#281 review, R4).
    """
    if path.is_symlink():
        return ("symlink", str(path.readlink()))
    if path.is_dir():
        return None
    return path.read_bytes()


def _classify_with_mtime(path: Path) -> MtimeEntry:
    """`_classify`, plus `st_mtime_ns` for a regular file. Symlinks are still
    classified unfollowed and carry no timestamp: `stat()` follows the link,
    so timing a symlink would time its target."""
    classified = _classify(path)
    if isinstance(classified, bytes):
        return (classified, path.stat().st_mtime_ns)
    return classified


def snapshot_bytes(root: Path) -> dict[Path, Entry]:
    """Capture every entry under `root`, keyed by relative path.

    Files map to their exact bytes and directories map to `None`. Recording
    directory entries too (not only `is_file()`) means a refusal test also
    catches a stray directory created outside pre-flight -- including a
    `.git` directory a refusing command should never have created.
    """
    return {path.relative_to(root): _classify(path) for path in _walk(root)}


def snapshot_with_mtime(root: Path) -> dict[Path, MtimeEntry]:
    """Capture every entry's bytes AND `st_mtime_ns`, keyed by relative path.

    Stronger than `snapshot_bytes` on purpose: a decline (or refusal) must
    leave the bundle untouched at the FILESYSTEM level, not merely
    byte-identical after a write-then-restore, which a bytes-only snapshot
    cannot distinguish from never having been written.
    """
    return {path.relative_to(root): _classify_with_mtime(path) for path in _walk(root)}


def snapshot_with_symlinks(root: Path) -> dict[Path, Entry]:
    """Identical to `snapshot_bytes` today, and kept as a distinct name only
    where a module's assertions are ABOUT symlink handling.

    Before #281 this was the one per-module copy that classified symlinks
    unfollowed; `_classify` now gives every variant that property, so the
    two are the same function. `test_init.py` keeps this name because its
    workspaces deliberately contain symlink-to-dir, symlink-to-file and
    broken-symlink entries, and naming the behavior at the import makes that
    intent visible where it matters.
    """
    return {path.relative_to(root): _classify(path) for path in _walk(root)}


def snapshot_including_git(root: Path) -> dict[Path, Entry]:
    """The ONE snapshot that does NOT apply `_EXCLUDED_DIRS`.

    Every other variant excludes `.git` because there it is ambient noise
    that can shift under the assertion for reasons unrelated to the command
    under test. Here it is the MEASUREMENT: `test_init.py`'s preflight test
    initialises four workspaces under four different preflight outcomes and
    asserts they are byte-identical, and the repository `init` creates is
    part of what must not vary. The working-tree files are identical across
    all four by construction, so git internals are the only place an
    outcome-dependent regression could hide.

    That comparison is safe from the timestamp problem the exclusion exists
    for, and not by luck: its caller isolates git identity so `init` makes
    no commit, and `git init` alone is byte-identical across invocations.
    Removing this variant and letting that test use an excluding snapshot
    would leave the suite with no assertion that preflight outcome does not
    change git setup (#281 review, R3).
    """
    return {
        path.relative_to(root): _classify(path)
        for path in _walk(root, apply_exclusions=False)
    }


def confirm_after(monkeypatch: pytest.MonkeyPatch, edit: Callable[[], object]) -> None:
    """Replace `typer.confirm` with a stub that runs `edit` and then answers
    yes, so a test can mutate the workspace INSIDE the window a mutating
    verb leaves open between its Phase-A snapshot and its first Phase-B
    write (#306).

    Patching the prompt rather than `fsio.write_atomic` is deliberate: the
    prompt is where that window is actually widest -- an operator reading a
    preview is a human-scale pause, which is exactly when a second terminal
    lands an edit -- and it needs no timing, no threads, and no assumption
    about how many writes a verb performs. `typer.confirm` is patched on the
    `typer` module itself because `cli/main.py` calls it as `typer.confirm`
    (the precedent is `test_forget.py`'s two confirm stubs).

    The stub swallows the call's arguments, `abort=True` included: returning
    `True` is what "the operator said yes" means to every call site here, so
    a verb that refuses after this stub ran refused for its OWN reason, not
    because the prompt declined.

    `edit` returns `object` rather than `None` so a caller can pass a bare
    `lambda: path.write_text(...)` (which returns the character count) or a
    bound `path.unlink` without either one needing a wrapper; the return
    value is discarded.
    """

    def _confirm(*args: object, **kwargs: object) -> bool:
        edit()
        return True

    monkeypatch.setattr(typer, "confirm", _confirm)


def echo_after(
    monkeypatch: pytest.MonkeyPatch, edit: Callable[[], object], *, trigger: str
) -> None:
    """`confirm_after`'s counterpart for the UNPROMPTED path (#313 review, R3).

    `confirm_after` can only widen the window a verb leaves open when a
    prompt actually runs, so it cannot reach `--auto` or `review: false` --
    and those are the runs most likely to race a second writer, because
    nothing pauses for a human. This stub instead patches `typer.echo` and
    fires `edit` ONCE, on the first line containing `trigger`, then keeps
    delegating to the real `typer.echo` untouched.

    Pass the LAST line of a verb's preview as `trigger`: every verb here
    prints its full preview at the end of Phase A and writes nothing until
    Phase B, so that line sits inside the same window the prompt would have
    held open. Choosing an earlier line still lands in the window; choosing
    a line a verb prints AFTER its first write does not, and the test would
    then be asserting nothing -- which is why `trigger` is required rather
    than defaulted.

    Firing once matters: a verb may echo its trigger line again on the
    success path, and re-running `edit` there would corrupt the very
    workspace the test is about to assert on.
    """
    real_echo = typer.echo
    fired = False

    def _echo(message: object = "", *args: Any, **kwargs: Any) -> None:
        nonlocal fired
        if not fired and trigger in str(message):
            fired = True
            edit()
        real_echo(message, *args, **kwargs)

    monkeypatch.setattr(typer, "echo", _echo)


@pytest.fixture
def seed_vectors_db() -> Callable[[Path], None]:
    """Return a callable that writes a `.openkos/vectors.db` holding one
    `vector_meta` row, so the bundle counts as embeddings PRESENT.

    Issue #183's state 3 keys on "absent OR empty", not merely absent -- a
    zero-byte file must NOT read as present. `init` never creates this file,
    so embeddings-missing is the default for a bare `_init_workspace` call
    unless a test opts out via this fixture.

    A factory rather than a plain fixture: seeding is opt-in per test (most
    CLI tests want the embeddings-missing default), and the callable form
    keeps the existing `seed_vectors_db(tmp_path)` call shape from the three
    module-level helpers it replaces (#197).
    """

    def _seed(workspace_root: Path) -> None:
        openkos_dir = workspace_root / ".openkos"
        openkos_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(openkos_dir / "vectors.db"))
        try:
            conn.execute(
                "CREATE TABLE vector_meta (concept_id TEXT PRIMARY KEY, "
                "content_hash TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO vector_meta (concept_id, content_hash) "
                "VALUES ('stub', 'hash')"
            )
            conn.commit()
        finally:
            conn.close()

    return _seed
