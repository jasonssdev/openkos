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
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
import typer

from openkos.vcs import git as vcs_git

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


# --------------------------------------------------------------------- #
# Untracked-fixture auto-commit guard (issue #819)
# --------------------------------------------------------------------- #


def _is_absent_and_untracked(root: Path, rel_path: str) -> bool:
    """Whether `rel_path` is BOTH gone from disk and unknown to the index.

    That pair is exactly what makes `git add -- <rel_path>` fail: there is
    no file to stage and no tracked entry to record a deletion for. Asked
    of the filesystem and of git's INDEX rather than of git's error text,
    because that text is translated under a non-English locale and `_run`
    does not pin one -- a guard that matched on the English wording would
    simply stop guarding, silently, which is the failure class this whole
    fixture exists to prevent (issue #817, review R2).

    `git ls-files` answers with the path or with nothing, so "tracked" is
    read as machine-readable presence, never as prose. Reaching through
    `vcs_git._run` follows this suite's existing convention for asking git
    a one-off question from a fixture, and `_run` is deliberately the
    single subprocess seam the whole codebase goes through.

    A non-zero probe means the question could not be ANSWERED -- outside a
    repository, `git ls-files` exits non-zero with empty output, which is
    byte-identical to a confident "untracked". Returning False there is
    what keeps the guard from relabelling an unrelated failure as this
    story: an unclassifiable error is not this fixture's business, and the
    caller re-raises the original untouched (issue #817, review R2/R4)."""
    if (root / rel_path).exists():
        return False
    probe = vcs_git._run(["git", "ls-files", "--", rel_path], cwd=root)
    if probe.returncode != 0:
        return False
    return not probe.stdout.strip()


@pytest.fixture(autouse=True)
def _fail_loudly_on_an_untracked_fixture_degrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Turn the ONE auto-commit degradation a fixture can cause by accident
    into a test failure (issue #817, item 1).

    `_write_doc` writes concept documents straight to disk, so they stay
    untracked. Identity's merge then DELETES the absorbed one, and the
    auto-commit's `git add -- bundle/concepts/b.md` fails because that path
    is now neither on disk nor in the index -- silently, because
    `_autocommit` degrades to a stderr WARNING and no test asserted a
    commit had happened. 169 tests across ten modules in this package were
    running against that state, which is a workspace no real session ever
    has: every prior mutating verb has already auto-committed its own
    writes.

    A fixture that quietly puts the code under test on its degradation path
    proves less than it appears to, so this makes the state impossible to
    reach without saying so, for every test under this package including
    ones not yet written (issue #819).

    It is narrow on purpose. A deliberately degraded run -- identity unset,
    a monkeypatched failure, a workspace that is not a repository -- either
    returns before `commit_paths` or raises for a different reason, and
    `_is_absent_and_untracked` reports False for all of those, so the
    original `GitError` propagates untouched and those tests keep working."""
    real = vcs_git.commit_paths

    def guarded(cwd: Path, rel_paths: Sequence[str], message: str) -> str | None:
        try:
            return real(cwd, rel_paths, message)
        except vcs_git.GitError:
            never_tracked = [
                rel for rel in rel_paths if _is_absent_and_untracked(cwd, rel)
            ]
            if not never_tracked:
                raise
            raise AssertionError(
                "auto-commit degraded because a fixture document was never "
                f"tracked before it was deleted: {never_tracked}. Call "
                "`commit_pending_fixture_docs()` from the helper that wrote "
                "it, so this test exercises the commit path a real session "
                "takes."
            ) from None

    monkeypatch.setattr(vcs_git, "commit_paths", guarded)


def commit_pending_fixture_docs() -> None:
    """Commit whatever a module's own document-writing helper just wrote
    (issue #819).

    Called at the bottom of each module's own `_write_concept`/`_write_doc`
    helper, which is what let 169 affected tests be fixed by editing the
    handful of helpers they share instead of the tests themselves. It takes
    no arguments on purpose: those helpers disagree about whether they
    receive a workspace root or a full document path, and every one of them
    runs after `_init_workspace` has already `chdir`-ed into the workspace,
    so `Path.cwd()` is the one thing they all share.

    It commits the whole pending `bundle/` rather than the single document,
    because these helpers typically write a concept AND hand-author its
    `index.md` bullet, and a real session has both committed.

    It COMMITS rather than merely staging. Staging alone would keep a later
    `git add` from failing on a deleted document, but `commit_paths` runs a
    bare `git commit`, which takes the whole INDEX -- so a fixture document
    left staged would be swept into the verb's own auto-commit and change
    what that commit contains.

    Silent when there is no repository or no identity: in both of those the
    auto-commit under test skips before it can degrade, so there is nothing
    to restore and nothing the guard could catch.

    It also REFUSES to act outside an openkos workspace. Resolving the
    repository from `Path.cwd()` is what lets it take no arguments, and the
    cost of that is a call site that runs before its module has `chdir`-ed
    -- where the current directory is the checkout this suite is running
    from, and a commit here would land fixture noise in a developer's real
    working tree. `openkos.yaml` is the workspace marker `require_workspace`
    itself checks, and this repository has none at its root, so the check
    separates the two cases exactly (issue #819, review R3)."""
    cwd = Path.cwd()
    if not (cwd / "openkos.yaml").is_file():
        return
    root = vcs_git.repo_root(cwd)
    if root is None or not vcs_git.has_git_identity(root):
        return
    seed_workspace_docs(root)


def seed_workspace_docs(root: Path) -> None:
    """Commit every fixture document written under `bundle/` so far.

    The bulk sibling of the per-module `_seed_commit` helpers (issue #817,
    item 1), for the tests that write their documents through a helper
    rather than naming each path. Same reason, same precondition: in a real
    workspace every document a merge is about to absorb was already
    committed by the verb that wrote it, so a merge deleting an UNTRACKED
    file is a state no session reaches.

    Names the `bundle` directory rather than enumerating documents, so a
    test that adds one later is covered without touching this; still a
    scoped `git add -- bundle`, never `-A`/`-a`, so the repository rule
    `test_no_blanket_add_flags_anywhere` guards is unaffected.

    A no-op when nothing is pending, because `git commit` over an empty
    index is an ERROR, not a success. The emptiness question goes to
    `vcs_git.paths_dirty`, the same scoped porcelain check `commit_paths`
    itself is paired with elsewhere -- machine-readable, and so unaffected
    by locale or human-facing wording (issue #817, review R2)."""
    if not vcs_git.paths_dirty(root, ["bundle"]):
        return
    vcs_git.commit_paths(root, ["bundle"], "seed fixture documents")


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


def changed_paths(
    before: Mapping[Path, object], after: Mapping[Path, object]
) -> set[Path]:
    """The relative paths whose snapshot entry differs between `before` and
    `after` -- the drift suites' one definition of "what did this run touch"
    (#327; previously copy-pasted as an inline set comprehension across the
    #313 drift suites).

    Feed it `snapshot_with_mtime` captures, not `snapshot_bytes` ones. The
    guard's contract is that a refused run left every non-drifted file
    untouched at the FILESYSTEM level, and a bytes-only snapshot cannot see
    the one violation that matters most here: a write-then-restore puts the
    original bytes back, so it compares equal to never having been written.
    `st_mtime_ns` is what makes that rewrite count as a change -- which is
    also why this helper compares entries with plain `!=` and adds no
    tolerance: any observable difference, content or timestamp, is a touch.

    `before.keys() | after.keys()` (not either alone) is load-bearing too:
    a FILE that only exists on one side -- created or deleted during the
    run -- must be reported, and `.get()`'s `None` default makes it compare
    unequal to any file entry. Directories are the known blind spot the
    idiom always had: a directory maps to `None` on the side it exists,
    which equals the `None` default on the side it does not, so a bare
    directory create/delete is invisible here (the snapshot equality
    asserts, which compare whole dicts, still see it).
    """
    return {k for k in before.keys() | after.keys() if before.get(k) != after.get(k)}


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


class EchoAfterLatch:
    """The handle `echo_after` returns: `fired` flips the moment the trigger
    line matches and `edit` runs.

    Exposed because a dead stub used to be indistinguishable from a live one
    (#327): when a verb's preview wording changes, the trigger stops
    matching, the edit never fires, the run SUCCEEDS, and the test fails on
    its exit-code assert -- pointing the reader at the drift guard when the
    defect is the stub's stale trigger. `assert hook.fired` right after the
    run makes the never-fired stub name itself instead.
    """

    def __init__(self) -> None:
        self.fired = False


def echo_after(
    monkeypatch: pytest.MonkeyPatch, edit: Callable[[], object], *, trigger: str
) -> EchoAfterLatch:
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

    Returns an `EchoAfterLatch` so every caller can `assert hook.fired`
    after the run (#327) -- see the class docstring for why a silent
    never-fired stub is the failure mode worth naming.
    """
    real_echo = typer.echo
    latch = EchoAfterLatch()

    def _echo(message: object = "", *args: Any, **kwargs: Any) -> None:
        if not latch.fired and trigger in str(message):
            latch.fired = True
            edit()
        real_echo(message, *args, **kwargs)

    monkeypatch.setattr(typer, "echo", _echo)
    return latch


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


def disable_local_exemption(workspace_root: Path) -> None:
    """Set `confidential_local_exemption: false` in `workspace_root`'s
    `openkos.yaml` -- the documented way to restore the pre-#240 blanket
    `confidential` gate.

    Needed by every CLI test that asserts a confidential concept is EXCLUDED
    from an `llm.chat` payload. Since issue #240 those tests are otherwise
    asserting the wrong thing: the suite's Ollama stand-ins report a local
    backend (`tests/unit/conftest.py::LOCAL_BACKEND_LOCALITY`), which is
    exactly the case where the exemption applies and the concept is
    legitimately included. Flipping the key restores the condition each of
    those tests was written to exercise, and does it through the same public
    switch a user has, rather than by reaching into the predicate."""
    config_path = workspace_root / "openkos.yaml"
    text = config_path.read_text(encoding="utf-8")
    assert "confidential_local_exemption: true" in text, (
        "the packaged template no longer declares 'confidential_local_exemption: true'"
    )
    config_path.write_text(
        text.replace(
            "confidential_local_exemption: true",
            "confidential_local_exemption: false",
        ),
        encoding="utf-8",
    )
