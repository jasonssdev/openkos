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
from pathlib import Path


class GitError(Exception):
    """A `git`/`git-filter-repo` invocation exited non-zero."""


class GitUnavailable(GitError):
    """The invoked binary (`git` itself, or a `git` subcommand it dispatches
    to, e.g. `git-filter-repo`) was not found on `PATH`."""


def _run(
    argv: Sequence[str], cwd: Path, env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a FIXED argv list under `cwd`, never via a shell.

    This is the ONLY subprocess call site in `openkos` -- every git
    operation, in both production code and test fixtures, goes through this
    one function instead of calling `subprocess` directly.
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


def git_available() -> bool:
    """`True` iff a `git` binary is on `PATH`."""
    return shutil.which("git") is not None


def filter_repo_available() -> bool:
    """`True` iff `git-filter-repo` is invocable (as a `git` subcommand, via
    its standalone script/binary on `PATH`)."""
    return shutil.which("git-filter-repo") is not None


def repo_root(cwd: Path) -> Path | None:
    """The real, resolved git repository root containing `cwd`, or `None` if
    `cwd` is not inside any git working tree."""
    result = _run(["git", "rev-parse", "--show-toplevel"], cwd=cwd)
    if result.returncode != 0:
        return None
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


def expunge_paths(cwd: Path, rel_paths: Sequence[str]) -> None:
    """Rewrite ALL git history under `cwd`, removing every path in
    `rel_paths` from every commit AND the working tree, via `git
    filter-repo`.

    Each path is written as a `literal:<path>` line to a temp file, passed
    via `--paths-from-file` -- NEVER interpolated into argv -- so a path
    containing shell metacharacters or a leading `-` is still matched
    byte-exact, never re-parsed as a flag or shell token. Finalizes with
    `git reflog expire --expire=now --all` + `git gc --prune=now`, so purged
    blobs are unreachable AND pruned, not merely unreferenced.

    IRREVERSIBLE: no backup is taken. Callers MUST have already confirmed
    every fail-closed safety rail (git-root match, clean tree, no published
    commits, typed confirmation) before calling this -- this function
    performs no such checks itself.
    """
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


def _finalize(cwd: Path) -> None:
    """Post-rewrite cleanup: expire the reflog and prune unreachable
    objects, so purged blobs are gone, not merely unreferenced."""
    expire = _run(["git", "reflog", "expire", "--expire=now", "--all"], cwd=cwd)
    if expire.returncode != 0:
        raise GitError(f"git reflog expire failed: {expire.stderr.strip()}")
    gc = _run(["git", "gc", "--prune=now"], cwd=cwd)
    if gc.returncode != 0:
        raise GitError(f"git gc failed: {gc.stderr.strip()}")
