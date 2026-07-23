"""`openkos.vcs.git`: the sole `subprocess` adapter over `git`/`git-filter-repo`.

`_run()` is the ONE place in the whole `openkos` codebase (production code
AND test fixtures) that calls `subprocess.run` -- it carries the sole
`# noqa: S603` suppression, justified because every argv passed to it
is a FIXED list: no `shell=True`, and no user-controlled data is ever
interpolated into argv itself. For `expunge_paths`, the one function that
needs to pass caller-supplied paths to `git`, those paths are written as
`literal:<path>` lines to a temp file consumed via `git filter-repo
--paths-from-file` instead -- byte-exact matching, no shell/glob/regex
re-interpretation, no argv interpolation.

This module provides only PROBES and the history-rewrite primitive. The
DECISION to call `expunge_paths` (rail ordering, typed confirmation) lives
in the `purge` CLI verb (PR2), not here -- but the probes are shared with
`doctor`, which is why this lives in `openkos.vcs`, not under `purge/`.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath


class GitError(Exception):
    """A `git`/`git-filter-repo` invocation exited non-zero."""


class GitUnavailable(GitError):
    """The invoked binary (`git` itself, or a `git` subcommand it dispatches
    to, e.g. `git-filter-repo`) was not found on `PATH`."""


class GitFinalizeError(GitError):
    """The history rewrite (`git filter-repo`) already SUCCEEDED, but the
    post-rewrite finalize step (`git reflog expire` and/or `git gc
    --prune=now`) FAILED afterward.

    This is deliberately a DISTINCT type from `GitError`: callers must be
    able to tell "the rewrite never happened" (plain `GitError`, safe to
    retry) apart from "the rewrite happened but purged data may STILL be
    recoverable via the un-expired reflog or unreachable-but-unpruned
    objects" (this exception) -- the two require completely different
    remediation.
    """


def _run(
    argv: Sequence[str], cwd: Path, env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a FIXED argv list under `cwd`, never via a shell.

    This is the ONLY subprocess call site in `openkos` -- every git
    operation, in both production code and test fixtures, goes through this
    one function instead of calling `subprocess` directly.

    Deliberate decision: no `timeout=` is passed to `subprocess.run`. A
    mis-calibrated timeout could kill an in-flight `git filter-repo` rewrite
    mid-write, which is the exact partial-failure catastrophe this module's
    `GitFinalizeError` exists to detect and report -- an artificially killed
    process is strictly worse than a slow one. Callers control cancellation
    at the process level (e.g. Ctrl-C / SIGINT) instead.
    """
    try:
        return subprocess.run(  # noqa: S603
            list(argv),
            cwd=cwd,
            env=dict(env) if env is not None else None,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitUnavailable(f"{argv[0]} not found on PATH") from exc
    except OSError as exc:
        # A present-but-broken binary (e.g. PermissionError from a TOCTOU
        # race after `shutil.which`, or ENOEXEC) must still map to a typed
        # error -- never let a raw OSError escape the adapter's contract.
        raise GitError(f"failed to invoke {argv[0]}: {exc}") from exc


def git_available() -> bool:
    """`True` iff a `git` binary is on `PATH`."""
    return shutil.which("git") is not None


def filter_repo_available() -> bool:
    """`True` iff `git-filter-repo` is invocable (as a `git` subcommand, via
    its standalone script/binary on `PATH`)."""
    return shutil.which("git-filter-repo") is not None


def repo_root(cwd: Path) -> Path | None:
    """The real, resolved git repository root containing `cwd`, or `None` if
    `cwd` is not inside any git working tree.

    A non-zero exit is `None` ONLY for the genuine "not inside a git repo"
    case (git's own "not a git repository" message). Any OTHER non-zero exit
    (broken config, corrupted repo, permission issues, ...) is a real error
    and must not be silently conflated with "not a repo" -- it is surfaced
    as `GitError` with git's stderr.
    """
    result = _run(["git", "rev-parse", "--show-toplevel"], cwd=cwd)
    if result.returncode != 0:
        if "not a git repository" in result.stderr.lower():
            return None
        raise GitError(f"git rev-parse --show-toplevel failed: {result.stderr.strip()}")
    return Path(result.stdout.strip()).resolve()


def is_clean(cwd: Path) -> bool:
    """`True` iff `git status --porcelain` reports no untracked, unstaged, or
    staged changes under `cwd`."""
    result = _run(["git", "status", "--porcelain"], cwd=cwd)
    if result.returncode != 0:
        raise GitError(f"git status failed: {result.stderr.strip()}")
    return result.stdout.strip() == ""


def has_published_commits(cwd: Path) -> bool:
    """`True` iff `HEAD` is reachable from any `refs/remotes/*` ref --
    fail-closed: ANY remote-tracking ref containing `HEAD` counts as
    published, regardless of the current branch's own configured upstream."""
    result = _run(["git", "branch", "--remotes", "--contains", "HEAD"], cwd=cwd)
    if result.returncode != 0:
        raise GitError(
            f"git branch --remotes --contains HEAD failed: {result.stderr.strip()}"
        )
    return result.stdout.strip() != ""


def _validate_rel_paths(rel_paths: Sequence[str]) -> None:
    """Fail-closed validation of `expunge_paths`' `rel_paths`, BEFORE any
    file is written or subprocess invoked.

    `git filter-repo --paths-from-file` parses its input file LINE BY LINE
    and re-dispatches on each line's prefix (`get_paths_from_file` in the
    vendored `git_filter_repo.py`). A `rel_path` containing a newline would
    therefore inject a SECOND directive of the attacker/caller's choosing
    (e.g. `glob:**`) into the same paths file -- which, combined with the
    always-passed `--invert-paths`, can escalate a single-path purge into a
    full-history mass deletion. Rejecting control characters and requiring
    workspace-relative literal paths (no absolute paths, no `..` segments)
    is defense in depth: normal `openkos` bundle/raw paths never need any of
    these.

    An EMPTY `rel_paths` list is also rejected: git-filter-repo's own
    `sanity_check_args` sets `inclusive=False` when there are no filter path
    changes, which performs a full `--force` history rewrite (reflog expire
    + gc) that filters NOTHING -- a destructive no-op purge.
    """
    if not rel_paths:
        raise ValueError(
            "expunge_paths: rel_paths must not be empty -- an empty path "
            "list makes git-filter-repo rewrite ALL of history while "
            "purging nothing (a destructive no-op filter)"
        )
    for rel_path in rel_paths:
        if not rel_path or not rel_path.strip():
            raise ValueError(
                f"expunge_paths: rel_path must not be empty/whitespace: {rel_path!r}"
            )
        if "\n" in rel_path:
            raise ValueError(
                "expunge_paths: rel_path must not contain a newline "
                f"character -- it would inject a second --paths-from-file "
                f"directive: {rel_path!r}"
            )
        if "\r" in rel_path:
            raise ValueError(
                "expunge_paths: rel_path must not contain a carriage-return "
                f"character -- it would inject a second --paths-from-file "
                f"directive: {rel_path!r}"
            )
        if any(ord(char) < 0x20 for char in rel_path):
            raise ValueError(
                f"expunge_paths: rel_path must not contain control character(s): "
                f"{rel_path!r}"
            )
        as_posix = PurePosixPath(rel_path)
        if rel_path.startswith("/") or as_posix.is_absolute():
            raise ValueError(
                f"expunge_paths: rel_path must be workspace-relative, not "
                f"absolute: {rel_path!r}"
            )
        if ".." in as_posix.parts:
            raise ValueError(
                f"expunge_paths: rel_path must not contain '..' segments: {rel_path!r}"
            )


def expunge_paths(cwd: Path, rel_paths: Sequence[str]) -> None:
    """Rewrite ALL git history under `cwd`, removing every path in
    `rel_paths` from every commit AND the working tree, via `git
    filter-repo`.

    Each path is validated (see `_validate_rel_paths`) and then written as a
    `literal:<path>` line to a temp file, passed via `--paths-from-file` --
    NEVER interpolated into argv -- so a path containing shell
    metacharacters or a leading `-` is still matched byte-exact, never
    re-parsed as a flag or shell token. Finalizes with `git reflog expire
    --expire=now --all` + `git gc --prune=now`, so purged blobs are
    unreachable AND pruned, not merely unreferenced.

    IRREVERSIBLE: no backup is taken. Callers MUST have already confirmed
    every fail-closed safety rail (git-root match, clean tree, no published
    commits, typed confirmation) before calling this -- this function
    performs no such checks itself.

    Raises `ValueError` for invalid `rel_paths` (no subprocess invoked,
    fail-closed), `GitError` if the rewrite itself fails (history NOT
    rewritten), or `GitFinalizeError` if the rewrite SUCCEEDED but the
    post-rewrite finalize step failed (history rewritten, purged data may
    still be recoverable -- see `GitFinalizeError`).
    """
    _validate_rel_paths(rel_paths)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as handle:
        for rel_path in rel_paths:
            handle.write(f"literal:{rel_path}\n")
        paths_file = Path(handle.name)

    try:
        result = _run(
            [
                "git",
                "filter-repo",
                "--force",
                "--invert-paths",
                "--paths-from-file",
                str(paths_file),
            ],
            cwd=cwd,
        )
        if result.returncode != 0:
            raise GitError(
                f"git filter-repo failed (exit {result.returncode}): "
                f"{result.stderr.strip()[-2000:]}"
            )
    finally:
        paths_file.unlink(missing_ok=True)

    _finalize(cwd)


_FINALIZE_REMEDIATION = (
    "The history rewrite completed, but pruning did not -- the purged data "
    "may STILL be recoverable via the reflog or unreachable-but-unpruned "
    "objects. To finish removing it, run:\n"
    "  git reflog expire --expire=now --all && git gc --prune=now"
)


def _finalize(cwd: Path) -> None:
    """Post-rewrite cleanup: expire the reflog and prune unreachable
    objects, so purged blobs are gone, not merely unreferenced.

    Only called AFTER `git filter-repo` has already succeeded, so any
    failure here is a PARTIAL-FAILURE window: history is already rewritten,
    but the purged blobs may still be recoverable until pruning completes.
    That state is raised as `GitFinalizeError`, never a plain `GitError`, so
    callers cannot mistake it for "the rewrite never happened".
    """
    expire = _run(["git", "reflog", "expire", "--expire=now", "--all"], cwd=cwd)
    if expire.returncode != 0:
        raise GitFinalizeError(
            f"git reflog expire failed after a successful rewrite: "
            f"{expire.stderr.strip()}\n{_FINALIZE_REMEDIATION}"
        )
    gc = _run(["git", "gc", "--prune=now"], cwd=cwd)
    if gc.returncode != 0:
        raise GitFinalizeError(
            f"git gc failed after a successful rewrite: "
            f"{gc.stderr.strip()}\n{_FINALIZE_REMEDIATION}"
        )
