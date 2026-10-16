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

Judging needs a chat client the API builds itself when the caller supplies
none. The concrete client classes are bound here, in this module's own globals,
and handed to the shared resolver (`application.backends`) at call time, the
pattern the CLI and the MCP server use; a test seam that patches the names
below therefore intercepts every client the API builds.
"""

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from openkos import config
from openkos.api._workspace import Workspace
from openkos.application import backends, curate_decisions, judging, lock_wait
from openkos.llm.base import LLMBackend
from openkos.llm.ollama import OllamaClient
from openkos.llm.openai_compatible import OpenAICompatibleClient
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


@dataclass
class CommitNotes:
    """What the commit ports learned that `Applied` has no slot for in flight:
    set by the ports while an apply runs, read by the caller afterwards."""

    confidential: bool = False
    """A commit landed and holds a concept explicitly marked confidential."""


def _ignore(message: str) -> None:
    """The default warning sink: drop it."""


def decision_ports(
    workspace: Workspace,
    *,
    wait_seconds: float,
    on_warning: Callable[[str], None] | None,
    notes: CommitNotes | None = None,
) -> curate_decisions.DecisionPorts:
    """The effects `apply_decision` needs, bound to `workspace`.

    The lock is taken only inside the service's commit section, waiting at most
    `wait_seconds`. Reconciliation is deliberately not wired: the service's
    default leaves the previewed plan as it is, and the API never calls a model
    to apply a decision."""
    warn = on_warning if on_warning is not None else _ignore

    def report(outcome: CommitOutcome) -> str | None:
        if notes is not None and outcome.includes_confidential:
            notes.confidential = True
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


def _backend_factories() -> backends.BackendFactories:
    """This module's own concrete client classes, read at call time.

    Holding class references (never calls) keeps the construction guard
    unconditional, and reading the globals when called keeps a patch of
    `OllamaClient` or `OpenAICompatibleClient` here intercepting every client
    the API builds."""
    return backends.BackendFactories(
        ollama=OllamaClient, openai_compatible=OpenAICompatibleClient
    )


def default_chat_client(cfg: config.Config, task: str | None) -> LLMBackend:
    """The chat client for `task` under `cfg`, as the CLI would build it.

    `cfg` is the configuration the judging service has just read, never a
    cached one, so a change of backend or model made while a caller holds a
    `Workspace` is observed. The API key, for the `openai-compatible` backend,
    is read from `OPENKOS_OPENAI_API_KEY` by the shared resolver and from
    nowhere else."""
    return backends.chat_client(cfg, factories=_backend_factories(), task=task)


def judging_ports(
    workspace: Workspace,
    *,
    chat_client: Callable[[str], LLMBackend] | None,
    wait_seconds: float,
) -> judging.JudgingPorts:
    """The effects judging reaches, bound to `workspace`.

    With no override the default client is built from the freshly read
    configuration; an override (task name to client) replaces it and no default
    client is built. The lock is taken only inside a persist section, waiting at
    most `wait_seconds`."""
    section = lock_wait.locked_commit_section(workspace.root, wait_seconds=wait_seconds)
    if chat_client is None:
        return judging.JudgingPorts(
            chat_client=default_chat_client, commit_section=section
        )
    override = chat_client
    # Judging always names the task it needs a client for.
    return judging.JudgingPorts(
        chat_client=lambda cfg, task: override(cast(str, task)),
        commit_section=section,
    )
