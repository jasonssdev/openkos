"""The API's composition root for applying a decision.

The decision service sequences effects it does not own: the lock around its
commit phase and the local git commit of what it wrote. The CLI binds those
to its own printing helpers; this module binds them for a caller that has no
terminal. Nothing here prints: a commit that cannot be made is reported through
the caller's optional warning callback, in the API's own words, and the apply
still succeeds with no commit identity.

The commit core is shared with the CLI (`openkos.vcs.autocommit`); only the
wording and the wiring are separate. The workspace's repository is chosen by
`Workspace.root` alone, never by the process's working directory.
"""

import math
from collections.abc import Callable, Sequence
from pathlib import Path

from openkos.api._workspace import Workspace
from openkos.application import curate_decisions, lock_wait
from openkos.vcs.autocommit import (
    CommitOutcome,
    ConfigCommitPlan,
    ConfigEditConflict,
    commit_config_edit,
    commit_paths,
    plan_config_edit,
)

_NOT_COMMITTED = "the decision was applied but not committed: "


def validated_wait(wait_seconds: float) -> float:
    """`wait_seconds` when it lies in `[0, lock_wait.MAX_WAIT_SECONDS]`.

    Anything else, including a NaN, raises `ValueError` before a lock is
    touched or a byte is written."""
    if not 0 <= wait_seconds <= lock_wait.MAX_WAIT_SECONDS or math.isnan(wait_seconds):
        raise ValueError(
            f"wait_seconds must be between 0 and {lock_wait.MAX_WAIT_SECONDS}, "
            f"got {wait_seconds!r}"
        )
    return wait_seconds


def skipped_warning(outcome: CommitOutcome) -> str | None:
    """The API's words for a commit that was not made, or `None` when the
    commit landed or had nothing to hold."""
    if outcome.skipped == "not_a_repository":
        return _NOT_COMMITTED + "the workspace is not inside a git repository"
    if outcome.skipped == "identity_unset":
        return _NOT_COMMITTED + "git user.name and user.email are not set"
    if outcome.skipped == "failed":
        return _NOT_COMMITTED + f"the git commit did not complete ({outcome.detail})"
    return None


def _ignore(message: str) -> None:
    """The default warning sink: drop it."""


def decision_ports(
    workspace: Workspace,
    *,
    wait_seconds: float,
    on_warning: Callable[[str], None] | None,
) -> curate_decisions.DecisionPorts:
    """The effects `apply_decision` needs, bound to `workspace`.

    The lock is taken only inside the service's commit section, waiting at most
    `wait_seconds`. Reconciliation is deliberately not wired: the service's
    default leaves the previewed plan as it is, and the API never calls a model
    to apply a decision."""
    warn = on_warning if on_warning is not None else _ignore

    def report(outcome: CommitOutcome) -> str | None:
        warning = skipped_warning(outcome)
        if warning is not None:
            warn(warning)
        return outcome.sha

    def autocommit(root: Path, paths: Sequence[str], message: str) -> str | None:
        return report(commit_paths(root, paths, message))

    def plan_config_commit(
        root: Path, surface: str, concept_type: str, tier: str, baseline: bytes
    ) -> ConfigCommitPlan | curate_decisions.ConfigCommitRefusal | None:
        planned = plan_config_edit(root, concept_type, tier, baseline)
        if isinstance(planned, ConfigEditConflict):
            return curate_decisions.ConfigCommitRefusal(
                f"the {concept_type!r} tier cannot be committed on its own: "
                "openkos.yaml has uncommitted changes to that entry (or a "
                "shape this operation cannot edit in the committed version); "
                "commit or discard them, then try again"
            )
        return planned

    def autocommit_config_edit(
        root: Path, message: str, plan: curate_decisions.ConfigCommitPlan | None
    ) -> str | None:
        return report(
            commit_config_edit(
                root,
                message,
                plan if isinstance(plan, ConfigCommitPlan) else None,
            )
        )

    return curate_decisions.DecisionPorts(
        autocommit=autocommit,
        commit_section=lock_wait.locked_commit_section(
            workspace.root, wait_seconds=wait_seconds
        ),
        on_warning=warn,
        plan_config_commit=plan_config_commit,
        autocommit_config_edit=autocommit_config_edit,
    )
