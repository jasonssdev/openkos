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

import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

# The exact bytes to write to a fresh workspace's `.gitignore` (Slice 1,
# git-lifecycle): an openkos-specific header ignoring `.openkos/` (derived
# stores), followed VERBATIM by the standard toptal windows/linux/macos/
# python template. Copied byte-for-byte from the source-of-truth reference
# `openspec/changes/git-lifecycle/gitignore.reference` -- see
# `test_gitignore_template_matches_reference_file_verbatim` for the parity
# guard. None of the template's broad ignores (`build/`, `var/`, `lib/`,
# `dist/`, `db.sqlite3`, `*.log`) collide with openkos canonical paths
# (`bundle/**`, `raw/**`, `openkos.yaml`, `AGENTS.md`; the bundle uses
# `log.md`, not `.log`), so no canonical content is ever ignored.
_GITIGNORE_TEMPLATE = r"""# --- openkos workspace (derived artifacts) ---
# The engine's own cache / derived stores (fts.db, vectors.db, graph.db) live
# under .openkos/ and are always reconstructible from the canonical bundle via
# `openkos reindex`, so they are never committed.
.openkos/

# Created by https://www.toptal.com/developers/gitignore/api/windows,linux,macos,python
# Edit at https://www.toptal.com/developers/gitignore?templates=windows,linux,macos,python

### Linux ###
*~

# temporary files which can be created if a process still has a handle open of a deleted file
.fuse_hidden*

# KDE directory preferences
.directory

# Linux trash folder which might appear on any partition or disk
.Trash-*

# .nfs files are created when an open file is removed but is still being accessed
.nfs*

### macOS ###
# General
.DS_Store
.AppleDouble
.LSOverride

# Icon must end with two \r
Icon


# Thumbnails
._*

# Files that might appear in the root of a volume
.DocumentRevisions-V100
.fseventsd
.Spotlight-V100
.TemporaryItems
.Trashes
.VolumeIcon.icns
.com.apple.timemachine.donotpresent

# Directories potentially created on remote AFP share
.AppleDB
.AppleDesktop
Network Trash Folder
Temporary Items
.apdisk

### macOS Patch ###
# iCloud generated files
*.icloud

### Python ###
# Byte-compiled / optimized / DLL files
__pycache__/
*.py[cod]
*$py.class

# C extensions
*.so

# Distribution / packaging
.Python
build/
develop-eggs/
dist/
downloads/
eggs/
.eggs/
lib/
lib64/
parts/
sdist/
var/
wheels/
share/python-wheels/
*.egg-info/
.installed.cfg
*.egg
MANIFEST

# PyInstaller
#  Usually these files are written by a python script from a template
#  before PyInstaller builds the exe, so as to inject date/other infos into it.
*.manifest
*.spec

# Installer logs
pip-log.txt
pip-delete-this-directory.txt

# Unit test / coverage reports
htmlcov/
.tox/
.nox/
.coverage
.coverage.*
.cache
nosetests.xml
coverage.xml
*.cover
*.py,cover
.hypothesis/
.pytest_cache/
cover/

# Translations
*.mo
*.pot

# Django stuff:
*.log
local_settings.py
db.sqlite3
db.sqlite3-journal

# Flask stuff:
instance/
.webassets-cache

# Scrapy stuff:
.scrapy

# Sphinx documentation
docs/_build/

# PyBuilder
.pybuilder/
target/

# Jupyter Notebook
.ipynb_checkpoints

# IPython
profile_default/
ipython_config.py

# pyenv
#   For a library or package, you might want to ignore these files since the code is
#   intended to run in multiple environments; otherwise, check them in:
# .python-version

# pipenv
#   According to pypa/pipenv#598, it is recommended to include Pipfile.lock in version control.
#   However, in case of collaboration, if having platform-specific dependencies or dependencies
#   having no cross-platform support, pipenv may install dependencies that don't work, or not
#   install all needed dependencies.
#Pipfile.lock

# poetry
#   Similar to Pipfile.lock, it is generally recommended to include poetry.lock in version control.
#   This is especially recommended for binary packages to ensure reproducibility, and is more
#   commonly ignored for libraries.
#   https://python-poetry.org/docs/basic-usage/#commit-your-poetrylock-file-to-version-control
#poetry.lock

# pdm
#   Similar to Pipfile.lock, it is generally recommended to include pdm.lock in version control.
#pdm.lock
#   pdm stores project-wide configurations in .pdm.toml, but it is recommended to not include it
#   in version control.
#   https://pdm.fming.dev/#use-with-ide
.pdm.toml

# PEP 582; used by e.g. github.com/David-OConnor/pyflow and github.com/pdm-project/pdm
__pypackages__/

# Celery stuff
celerybeat-schedule
celerybeat.pid

# SageMath parsed files
*.sage.py

# Environments
.env
.venv
env/
venv/
ENV/
env.bak/
venv.bak/

# Spyder project settings
.spyderproject
.spyproject

# Rope project settings
.ropeproject

# mkdocs documentation
/site

# mypy
.mypy_cache/
.dmypy.json
dmypy.json

# Pyre type checker
.pyre/

# pytype static type analyzer
.pytype/

# Cython debug symbols
cython_debug/

# PyCharm
#  JetBrains specific template is maintained in a separate JetBrains.gitignore that can
#  be found at https://github.com/github/gitignore/blob/main/Global/JetBrains.gitignore
#  and can be added to the global gitignore or merged into this file.  For a more nuclear
#  option (not recommended) you can uncomment the following to ignore the entire idea folder.
#.idea/

### Python Patch ###
# Poetry local configuration file - https://python-poetry.org/docs/configuration/#local-configuration
poetry.toml

# ruff
.ruff_cache/

# LSP config files
pyrightconfig.json

### Windows ###
# Windows thumbnail cache files
Thumbs.db
Thumbs.db:encryptable
ehthumbs.db
ehthumbs_vista.db

# Dump file
*.stackdump

# Folder config file
[Dd]esktop.ini

# Recycle Bin used on file shares
$RECYCLE.BIN/

# Windows Installer files
*.cab
*.msi
*.msix
*.msm
*.msp

# Windows shortcuts
*.lnk

# End of https://www.toptal.com/developers/gitignore/api/windows,linux,macos,python
"""


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
    except UnicodeDecodeError as exc:
        # `text=True` decodes stdout/stderr as UTF-8 -- non-UTF-8 bytes in
        # git's output raise `UnicodeDecodeError`, a `ValueError` subclass,
        # NOT an `OSError`. Left unmapped, it would escape every caller's
        # `except (GitError, OSError)` handling (e.g. `init`'s git-setup
        # block) and crash as an uncaught traceback. Map it the same way as
        # every other `_run` failure mode: a typed `GitError`.
        raise GitError(
            f"{argv[0]} produced output that could not be decoded as UTF-8: {exc}"
        ) from exc


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
    """`True` iff the repository has EVER published anything to ANY remote
    -- direction-agnostic and fail-closed.

    This deliberately does NOT check whether the CURRENT `HEAD` is
    reachable from a remote branch (e.g. `git branch --remotes --contains
    HEAD`): that check is direction-dependent and goes stale the moment a
    caller makes ONE more local commit past the last push -- `HEAD` then
    points to a commit no remote has, so `--contains HEAD` flips back to
    "not published" even though an already-pushed ANCESTOR commit (and
    every file inside it) is still sitting on the remote, fully
    recoverable from there. `purge` rewriting history in that state would
    silently leave the caller's "erased" data intact on origin while
    reporting success.

    Instead: `True` iff the local repository has ANY `refs/remotes/*` ref
    at all. Once a single push has ever happened, this stays permanently
    `True` regardless of how far local `HEAD` advances afterward -- fail-
    closed by design, since `purge` cannot know (without a network round-
    trip this adapter deliberately does not perform) which SPECIFIC
    commits/blobs a remote still holds, only that at least one exists."""
    result = _run(["git", "for-each-ref", "--count=1", "refs/remotes/"], cwd=cwd)
    if result.returncode != 0:
        raise GitError(
            f"git for-each-ref --count=1 refs/remotes/ failed: {result.stderr.strip()}"
        )
    return result.stdout.strip() != ""


def init_repo(cwd: Path) -> None:
    """Run `git init` in `cwd`, creating a new git repository.

    Callers (the `init` CLI verb) must only call this when `repo_root(cwd)`
    is `None` -- this function itself performs no such check, it just runs
    `git init` unconditionally. Raises `GitUnavailable` if `git` itself is
    absent from `PATH` (via `_run`'s `FileNotFoundError` mapping), or
    `GitError` if `git init` exits non-zero for any other reason."""
    result = _run(["git", "init"], cwd=cwd)
    if result.returncode != 0:
        raise GitError(f"git init failed: {result.stderr.strip()}")


def has_git_identity(cwd: Path) -> bool:
    """`True` iff BOTH `git config user.name` and `git config user.email`
    resolve to a non-empty value at `cwd` (local repo config, falling back
    to global/system config per git's own resolution order) -- `False` if
    either is unset/empty or the probe itself fails.

    A probe-only check: this never injects, writes, or falls back to any
    identity -- callers (the `init` CLI verb) skip the commit step entirely
    when this returns `False`, rather than using a bot identity."""
    name_result = _run(["git", "config", "user.name"], cwd=cwd)
    if name_result.returncode != 0 or not name_result.stdout.strip():
        return False
    email_result = _run(["git", "config", "user.email"], cwd=cwd)
    return email_result.returncode == 0 and bool(email_result.stdout.strip())


def has_reset_point(cwd: Path) -> bool:
    """`True` iff `HEAD` has at least one ancestor commit at `cwd` -- the
    necessary (not sufficient) condition for a `git reset --hard
    <commit>~1` remedy to name ANY real commit at all.

    `doctor`'s corrupted-ledger remediation (durable-derived-state slice
    1b) probes this before printing "reset and replay" as an available
    remedy: `_autocommit` (`cli/main.py::_autocommit`) is best-effort and
    silently no-ops with no git repo, no configured git identity, or any
    `GitError`/`OSError` -- so a workspace that never actually committed
    has no reset point at all, regardless of whether a `.git` directory
    exists. `False` on ANY probe failure (no repo, zero commits, exactly
    one commit with no parent, or the probe itself erroring) -- fail-
    closed, since a remedy this function cannot confirm must never be
    printed as available."""
    result = _run(["git", "rev-parse", "--verify", "--quiet", "HEAD~1"], cwd=cwd)
    return result.returncode == 0


def commit_paths(cwd: Path, rel_paths: Sequence[str], message: str) -> None:
    """Stage EXACTLY `rel_paths` (`git add -- <rel_paths>`, never `-A`/
    `-a`) and commit them with `message`.

    The `--` end-of-options guard keeps a leading-dash path from being
    re-parsed as a flag. Scoped staging is deliberate (design: `commit_paths`
    decision) -- in an existing host repo, a blanket `-A`/`-a` would sweep
    unrelated dirty content into openkos's own commit. Raises `GitError` if
    either the `add` or the `commit` step exits non-zero."""
    add_result = _run(["git", "add", "--", *rel_paths], cwd=cwd)
    if add_result.returncode != 0:
        raise GitError(f"git add failed: {add_result.stderr.strip()}")
    commit_result = _run(["git", "commit", "-m", message], cwd=cwd)
    if commit_result.returncode != 0:
        raise GitError(f"git commit failed: {commit_result.stderr.strip()}")


def paths_dirty(cwd: Path, rel_paths: Sequence[str]) -> bool:
    """`True` iff `git status --porcelain -- <rel_paths>` reports any
    untracked, unstaged, or staged change scoped to `rel_paths` (purge-
    transactional-cleanup #-final: the empty-diff guard for purge's
    post-rewrite auto-commit).

    Scoped exactly like `commit_paths`' own `git add -- <rel_paths>` (the
    `--` end-of-options guard, never a whole-tree `is_clean` probe): a dirty
    UNRELATED file elsewhere in the workspace must never make this return
    `True`, which would otherwise trigger a spurious auto-commit attempt.
    Raises `GitError` if the probe itself fails (e.g. `cwd` is not inside a
    git working tree) -- callers decide how to treat that failure; this
    function never swallows it."""
    result = _run(["git", "status", "--porcelain", "--", *rel_paths], cwd=cwd)
    if result.returncode != 0:
        raise GitError(f"git status failed: {result.stderr.strip()}")
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

    A `rel_path` containing the byte sequence `==>` is ALSO rejected.
    `get_paths_from_file` splits each line on `==>` ANYWHERE it occurs,
    BEFORE dispatching on the `literal:`/`glob:`/`regex:` prefix -- so a
    `rel_path` like `"target.md==>x"`, written as `literal:target.md==>x`,
    is parsed as a RENAME directive (`match="target.md"`, `repl="x"`), never
    as a filter/delete entry. With no other filter path present,
    `sanity_check_args` sets `inclusive=False`, which keeps EVERY file --
    the purge target is silently NEVER removed from history, while
    `git filter-repo` still exits 0. This is worse than a no-op: it reports
    success while doing nothing, for an operation that is irreversible.
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
        if "==>" in rel_path:
            raise ValueError(
                "expunge_paths: rel_path must not contain '==>' -- "
                "git-filter-repo's --paths-from-file treats it as a rename "
                "directive (match ==> repl), which would silently keep "
                f"the file instead of purging it: {rel_path!r}"
            )


def _validate_scrub_identities(scrub_identities: Sequence[str]) -> None:
    """Fail-closed validation of `expunge_paths`' `scrub_identities`, BEFORE
    any file is written or subprocess invoked.

    Each identity is written verbatim, one per line, to a sidecar temp file
    that the `--file-info-callback` snippet reads back at runtime (via
    `OPENKOS_SCRUB_IDS_FILE`). An identity containing a newline would split
    into TWO lines in that sidecar -- silently injecting an extra,
    caller-uncontrolled scrub target into the set the snippet drops lines
    for. Rejecting control characters and empty identities up front, before
    any subprocess or temp file is created, mirrors `_validate_rel_paths`'
    discipline exactly.
    """
    for identity in scrub_identities:
        if not identity or not identity.strip():
            raise ValueError(
                f"expunge_paths: scrub identity must not be empty/whitespace: "
                f"{identity!r}"
            )
        if "\n" in identity:
            raise ValueError(
                "expunge_paths: scrub identity must not contain a newline "
                f"character -- it would inject an extra scrub-set entry via "
                f"the sidecar identities file: {identity!r}"
            )
        if "\r" in identity:
            raise ValueError(
                "expunge_paths: scrub identity must not contain a carriage-"
                f"return character -- it would inject an extra scrub-set "
                f"entry via the sidecar identities file: {identity!r}"
            )
        if any(ord(char) < 0x20 for char in identity):
            raise ValueError(
                f"expunge_paths: scrub identity must not contain control "
                f"character(s): {identity!r}"
            )


# The BODY of a `def file_info_callback(filename, mode, blob_id, value):`
# function, compiled and invoked by `git filter-repo --file-info-callback
# <this-file>` once per (non-deletion) file change. Deliberately a STATIC
# constant with ZERO subject-data interpolation: the purge-set identities to
# scrub reach it ONLY via the `OPENKOS_SCRUB_IDS_FILE` env var, read at call
# time from a sidecar temp file -- never written into this source string
# itself, so no id/title can ever be (mis)interpreted as Python source.
#
# `re` and `os` are already present in git-filter-repo's callback globals
# (see its `public_globals`), so no `import` statement is needed or allowed
# here (an `import` line would still work, but the snippet avoids depending
# on it to stay minimal).
#
# `_identity` below is a BYTES-only re-implementation of
# `openkos.bundle.index._link_identity`'s normalization rules -- it MUST stay
# in lockstep with that function (proven by the parity test in
# `tests/unit/vcs/test_scrub_snippet_parity.py`); this is intentionally
# duplicated code, not a shared import, because this snippet runs inside
# `git-filter-repo`'s own subprocess, which cannot import `openkos`.
_FILE_INFO_CALLBACK_SNIPPET = """\
if filename not in (b"bundle/index.md", b"bundle/log.md"):
    return (filename, mode, blob_id)
ids_path = os.environ.get("OPENKOS_SCRUB_IDS_FILE")
if not ids_path:
    return (filename, mode, blob_id)
with open(ids_path, "rb") as ids_handle:
    raw_ids = ids_handle.read()
scrub_ids = {line for line in raw_ids.split(b"\\n") if line}
if not scrub_ids:
    return (filename, mode, blob_id)

_bullet_markers = (b"* ", b"- ")
_link_re = re.compile(rb"\\[[^\\]]*\\]\\(([^)]+)\\)")
_anchor_re = re.compile(rb"\\(id: ([^)]+)\\)")
_scheme_re = re.compile(rb"\\A[A-Za-z][A-Za-z0-9+.-]*:")

def _identity(target):
    target = target.split(b"#", 1)[0].strip()
    if target.endswith(b'"') and b' "' in target:
        target = target.rsplit(b' "', 1)[0].strip()
    if not target:
        return None
    if _scheme_re.match(target):
        return None
    try:
        target.decode("utf-8")
    except UnicodeDecodeError:
        return None
    target = target.lstrip(b"/")
    parts = []
    for part in target.split(b"/"):
        if part in (b"", b"."):
            continue
        if part == b"..":
            if parts:
                parts.pop()
            else:
                return None
        else:
            parts.append(part)
    identity = b"/".join(parts)
    if identity.endswith(b".md"):
        identity = identity[: -len(b".md")]
    return identity

contents = value.get_contents_by_identifier(blob_id)
lines = contents.splitlines(keepends=True)
kept_lines = []
changed = False
is_log = filename == b"bundle/log.md"
for line in lines:
    stripped = line.lstrip()
    dropped = False
    if stripped.startswith(_bullet_markers):
        link_match = _link_re.search(stripped)
        if link_match is not None and _identity(link_match.group(1)) in scrub_ids:
            dropped = True
        if not dropped and is_log:
            anchor_match = _anchor_re.search(stripped)
            if anchor_match is not None and anchor_match.group(1) in scrub_ids:
                dropped = True
    if dropped:
        changed = True
        continue
    kept_lines.append(line)

if not changed:
    return (filename, mode, blob_id)
new_contents = b"".join(kept_lines)
new_blob_id = value.insert_file_with_contents(new_contents)
return (filename, mode, new_blob_id)
"""


def expunge_paths(
    cwd: Path,
    rel_paths: Sequence[str],
    *,
    scrub_identities: Sequence[str] | None = None,
) -> None:
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

    When `scrub_identities` is a non-empty sequence, this SAME single `git
    filter-repo` pass ALSO content-scrubs `bundle/index.md` and
    `bundle/log.md` across every historical commit: each identity is
    validated (see `_validate_scrub_identities`, fail-closed, BEFORE any
    subprocess), then written one-per-line to a sidecar temp file whose path
    is passed via the `OPENKOS_SCRUB_IDS_FILE` env var to a `--file-info-
    callback <snippet-file>` invocation of the STATIC
    `_FILE_INFO_CALLBACK_SNIPPET` -- no id/title is ever interpolated into
    the snippet source itself or into argv. When `scrub_identities` is
    `None`/empty, no `--file-info-callback` argv is added at all: behavior is
    byte-identical to calling this function without that parameter.

    IRREVERSIBLE: no backup is taken. Callers MUST have already confirmed
    every fail-closed safety rail (git-root match, clean tree, no published
    commits, typed confirmation) before calling this -- this function
    performs no such checks itself.

    Raises `ValueError` for invalid `rel_paths`/`scrub_identities` (no
    subprocess invoked, fail-closed), `GitError` if the rewrite itself fails
    (history NOT rewritten), or `GitFinalizeError` if the rewrite SUCCEEDED
    but the post-rewrite finalize step failed (history rewritten, purged
    data may still be recoverable -- see `GitFinalizeError`).
    """
    _validate_rel_paths(rel_paths)
    if scrub_identities:
        _validate_scrub_identities(scrub_identities)

    # Every temp file's `Path` is assigned to its tracking variable
    # IMMEDIATELY after `NamedTemporaryFile` creates it -- BEFORE its write
    # loop runs -- so the `finally` below can always unlink it, even if that
    # very write loop raises (e.g. `OSError: ENOSPC`/`EIO`/quota) partway
    # through. Assigning only AFTER a completed write loop (the pre-fix
    # shape) would leave a file created-but-unassigned, and therefore
    # un-unlinked, exactly when its write fails -- a permanent on-disk leak
    # of its content. This matters most for `sidecar_file`, which holds the
    # PLAINTEXT purge-set identities (the sensitive data being erased), but
    # applies identically to `paths_file` (purged paths) and `snippet_file`
    # (non-sensitive, kept symmetric for the same guarantee).
    paths_file: Path | None = None
    snippet_file: Path | None = None
    sidecar_file: Path | None = None
    try:
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False, encoding="utf-8"
            ) as handle:
                paths_file = Path(handle.name)
                for rel_path in rel_paths:
                    handle.write(f"literal:{rel_path}\n")

            argv = [
                "git",
                "filter-repo",
                "--force",
                "--invert-paths",
                "--paths-from-file",
                str(paths_file),
            ]
            env: Mapping[str, str] | None = None
            if scrub_identities:
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".py", delete=False, encoding="utf-8"
                ) as snippet_handle:
                    snippet_file = Path(snippet_handle.name)
                    snippet_handle.write(_FILE_INFO_CALLBACK_SNIPPET)
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".txt", delete=False, encoding="utf-8"
                ) as sidecar_handle:
                    sidecar_file = Path(sidecar_handle.name)
                    for identity in scrub_identities:
                        sidecar_handle.write(f"{identity}\n")
                env = {**os.environ, "OPENKOS_SCRUB_IDS_FILE": str(sidecar_file)}
                argv = [*argv, "--file-info-callback", str(snippet_file)]
        except OSError as exc:
            # Setup only -- no subprocess has run yet, so this is the safe
            # "the rewrite did NOT happen" case: a plain `GitError`, never
            # `GitFinalizeError` (which implies the rewrite already
            # succeeded). Without this mapping, a raw `OSError` would
            # propagate past the CLI's `GitError`/`GitFinalizeError` handling
            # and lose the "history not rewritten" messaging.
            raise GitError(
                f"expunge_paths: failed to prepare a temp file for git "
                f"filter-repo (history NOT rewritten): {exc}"
            ) from exc

        result = _run(argv, cwd=cwd, env=env)
        if result.returncode != 0:
            raise GitError(
                f"git filter-repo failed (exit {result.returncode}): "
                f"{result.stderr.strip()[-2000:]}"
            )
    finally:
        if paths_file is not None:
            paths_file.unlink(missing_ok=True)
        if snippet_file is not None:
            snippet_file.unlink(missing_ok=True)
        if sidecar_file is not None:
            sidecar_file.unlink(missing_ok=True)

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
