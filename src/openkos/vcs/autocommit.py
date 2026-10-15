"""The print-free git auto-commit core.

The CLI's best-effort commit after a mutating verb and the Python API's commit
after applying a decision are the same operation, so the logic lives here once
and each adapter words its own warnings. Nothing in this module prints, prompts
or exits: every degradation is a field on the returned `CommitOutcome`.

Git is reached through `openkos.vcs.git` module attributes (never a bound
name), so a test that patches `vcs.git.<name>` intercepts this module too.
Pathspecs are workspace-relative and taken literally; staging and committing
are both scoped to the listed paths, never `-a`/`-A`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from openkos import config
from openkos.model import okf
from openkos.vcs import git

type Skipped = Literal[
    "not_a_repository", "identity_unset", "failed", "nothing_to_commit"
]

_CONFIG_REL_PATH = "openkos.yaml"


@dataclass(frozen=True)
class CommitOutcome:
    """What a commit attempt did. `sha` is the abbreviated new commit, `None`
    on every skip AND when a commit landed whose name could not be read back
    (`skipped` is then `None` too). `detail` carries the underlying reason for
    `skipped == "failed"`. `includes_confidential` is true only when a commit
    landed and one of its paths is explicitly marked confidential."""

    sha: str | None
    skipped: Skipped | None
    detail: str | None
    includes_confidential: bool


def paths_include_confidential(root: Path, paths: Sequence[str]) -> bool:
    """`True` iff any of the given (workspace-relative, POSIX) `paths` is a
    concept file whose frontmatter `sensitivity` equals the canonical top
    rank (`okf.SENSITIVITY_ORDER[-1]`, `"confidential"`).

    `blocks_llm_send` is a FAIL-CLOSED gate that also treats a missing, blank
    or unreadable `sensitivity` as confidential; this predicate is
    transparency, not a security gate, so it looks for an EXPLICIT
    `confidential` value only -- a source with no `sensitivity` field must
    never trigger a false "confidential committed" alarm.

    `bundle/index.md`/`bundle/log.md` (catalog files, never concept
    frontmatter), any path under `raw/` (source copies, not concept
    documents), and any path missing on disk (a staged deletion has nothing to
    read) are all skipped without raising."""
    reserved = {"bundle/index.md", "bundle/log.md"}
    for rel_path in paths:
        if rel_path in reserved or rel_path.startswith("raw/"):
            continue
        file_path = root / rel_path
        if not file_path.is_file():
            continue
        try:
            text = file_path.read_text(encoding="utf-8")
            metadata, _ = okf.load_frontmatter(text)
        except OSError, ValueError:
            continue
        if str(metadata.get("sensitivity", "")).strip() == okf.SENSITIVITY_ORDER[-1]:
            return True
    return False


def commit_paths(
    root: Path,
    paths: Sequence[str],
    message: str,
    *,
    has_confidential: Callable[
        [Path, Sequence[str]], bool
    ] = paths_include_confidential,
) -> CommitOutcome:
    """Best-effort commit of EXACTLY `paths` (workspace-relative, POSIX,
    literal): `git add -- <paths>` then `git commit -- <paths>`, so an
    unrelated file elsewhere -- unstaged, or already staged by the user -- is
    neither committed nor unstaged. A workspace nested in an outer repository
    commits into that repository, again only the listed paths.

    Never raises for a git degradation: not a repository, identity unset, or a
    `GitError`/`OSError` while staging or committing each come back as
    `skipped`. `has_confidential` is the disclosure predicate, injectable so
    an adapter can keep its own patch point."""
    repo = git.repo_root(root)
    if repo is None:
        return CommitOutcome(None, "not_a_repository", None, False)
    if not git.has_git_identity(root):
        return CommitOutcome(None, "identity_unset", None, False)
    try:
        sha = git.commit_paths(root, paths, message)
    except (git.GitError, OSError) as exc:
        return CommitOutcome(None, "failed", str(exc), False)
    return CommitOutcome(sha, None, None, has_confidential(root, paths))


@dataclass(frozen=True)
class ConfigCommitPlan:
    """How a config-rewriting verb's commit is made when `openkos.yaml`
    already carries uncommitted edits (#1330): `apply` is the verb's own
    change as a function of the file's bytes, applied to the COMMITTED version
    so the user's edits stay out of the commit."""

    apply: Callable[[bytes], bytes]


@dataclass(frozen=True)
class ConfigEditConflict:
    """The change cannot be applied cleanly to the committed `openkos.yaml`.
    Neutral on purpose: the adapter words the refusal."""

    concept_type: str


def plan_config_edit(
    root: Path,
    concept_type: str,
    tier: str,
    baseline: bytes,
) -> ConfigCommitPlan | ConfigEditConflict | None:
    """Before a config-rewriting verb writes (#1330): `None` when
    `openkos.yaml` is clean (or has no committed version, or there is no git),
    so the ordinary path-scoped commit is correct. Otherwise the file carries
    uncommitted edits that `git add -- openkos.yaml` would sweep into a commit
    titled for another change, so return a plan that commits the change
    applied to the committed version.

    Returns a `ConfigEditConflict` (nothing written) when the user's edit
    touches the very `type_tiers` entry being set, or the committed file is
    not one `set_type_tier` can edit. It reads git and nothing else."""
    try:
        if not git.paths_dirty(root, [_CONFIG_REL_PATH]):
            return None
        head = git.head_file_bytes(root, _CONFIG_REL_PATH)
    except git.GitError:
        return None
    if head is None:
        return None

    def apply(content: bytes) -> bytes:
        text = content.decode("utf-8")
        return config.set_type_tier(text, concept_type, tier).encode("utf-8")

    try:
        same_entry = config.type_tier_entry(
            head.decode("utf-8"), concept_type
        ) == config.type_tier_entry(baseline.decode("utf-8"), concept_type)
        apply(head)
    except ValueError:
        same_entry = False
    if not same_entry:
        return ConfigEditConflict(concept_type=concept_type)
    return ConfigCommitPlan(apply=apply)


def config_edit_pending(root: Path) -> bool:
    """`True` when `openkos.yaml` has something to commit. A git error counts
    as pending, so the commit itself reports the real condition."""
    try:
        return git.paths_dirty(root, [_CONFIG_REL_PATH])
    except git.GitError:
        return True


def commit_config_edit(
    root: Path, message: str, plan: ConfigCommitPlan | None
) -> CommitOutcome:
    """`commit_paths` for a verb that rewrote `openkos.yaml`.

    Skips the commit when it would hold nothing (#1330): re-applying the tier a
    config already holds leaves the tree clean, which is `nothing_to_commit`
    rather than a failure. With a `plan`, the commit holds only the verb's own
    change over the committed file (`commit_transformed_file`), leaving the
    user's uncommitted edits in the working tree."""
    if plan is None:
        if not config_edit_pending(root):
            return CommitOutcome(None, "nothing_to_commit", None, False)
        return commit_paths(root, [_CONFIG_REL_PATH], message)
    if not git.has_git_identity(root):
        return CommitOutcome(None, "identity_unset", None, False)
    try:
        sha = git.commit_transformed_file(root, _CONFIG_REL_PATH, plan.apply, message)
    except (git.GitError, OSError) as exc:
        if str(exc).endswith("nothing to commit"):
            return CommitOutcome(None, "nothing_to_commit", None, False)
        return CommitOutcome(None, "failed", str(exc), False)
    return CommitOutcome(sha, None, None, False)
