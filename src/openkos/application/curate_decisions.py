"""The curation decision surface: the apply half of a `curate` decision as
plain data (the `curate-decisions-as-data` change, ADR-0053).

This module is INTERNAL and OWNER-LOCAL. It is not a published API, and a
decision payload may derive from confidential concepts, so no `mcp/` or
`api/` adapter may import it (a boundary guard lands with the list). It is
a narrow synchronous service in the `merge_service` mould (ADR-0018): it
never prompts, never prints, never reads a TTY and never raises
`typer.Exit`. Every outcome is a returned typed result or a raised typed
refusal from `write_gate`; the adapter owns the wording, the exit code and
the commit disclosure.

Effects the service sequences but does not own arrive through
`DecisionPorts` (the auto-commit, the commit section, the warning sink) and
`DecisionObserver` (the lines that print INSIDE a commit section). Library
calls go through the module attribute (`duplicates_service.<name>`), never a
`from ... import`, so an existing patch of the target keeps working.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

from openkos import config
from openkos.application import commit_phase, duplicates_service
from openkos.application.lock_wait import CommitSection
from openkos.model import okf
from openkos.state import pending_queue as pq


class Action(StrEnum):
    """What a client may do with one open decision."""

    MERGE = "merge"
    KEEP_DISTINCT = "keep_distinct"
    SKIP = "skip"
    RELATE = "relate"
    RELATE_REVERSED = "relate_reversed"
    SET_TIER = "set_tier"


@dataclass(frozen=True)
class DecisionVersion:
    """The version a stateless caller commits against.

    `payload_digest` proves the queue row, its proposal and its recorded
    inputs are unchanged; `content_digest` proves the bytes the human saw are
    the bytes that would be written. `payload_digest` is `None` when no queue
    row backs the item, which the token path rejects. `decision_key` is the
    handle: a findings row id is never used, because the cache is derived."""

    decision_key: str
    payload_digest: str | None
    content_digest: str

    def token(self) -> str:
        """An opaque hex digest of the three components, for transport.
        Parsing a token back is deferred to the API stage."""
        parts = (
            self.decision_key,
            "-" if self.payload_digest is None else f"+{self.payload_digest}",
            self.content_digest,
        )
        return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Applied:
    """The action took effect. `commit_sha` is `None` when the auto-commit
    degraded (the write itself still landed)."""

    decision_key: str | None
    action: Action
    commit_sha: str | None


@dataclass(frozen=True)
class NotApplied:
    """The action was not taken, and that is not an error: the walk goes on.
    A record failure is fail-open and carries its error in `detail`."""

    action: Action
    reason: Literal[
        "skipped", "member_missing", "record_failed", "not_mergeable", "not_offered"
    ]
    detail: str | None = None


@dataclass(frozen=True)
class Stale:
    """The version token no longer matches; nothing was written."""

    decision_key: str
    reason: Literal[
        "content_changed", "row_resolved", "row_superseded", "row_inputs_changed"
    ]


ApplyOutcome = Applied | NotApplied | Stale


def _ignore_warning(message: str) -> None:
    """The default warning sink: drop it."""


def _write_identity_ruling(
    layout: config.WorkspaceLayout,
    member_ids: tuple[str, ...],
    on_warning: Callable[[str], None],
) -> str:
    """The default ruling writer: the shared service writer, reached through
    its module attribute so a patch of it still takes effect."""
    return duplicates_service.apply_identity_decision(
        layout, member_ids, target_state="declined", on_warning=on_warning
    )


@dataclass(frozen=True)
class DecisionPorts:
    """The effects the service sequences but does not own."""

    autocommit: Callable[[Path, Sequence[str], str], str | None]
    """Best-effort commit of the listed workspace-relative paths, returning the
    abbreviated sha or `None` on every degradation. Bound late by the adapter."""

    commit_section: CommitSection = commit_phase.unlocked_section
    """Entered around the commit phase only, never around a prompt (ADR-0036).
    The default holds nothing."""

    on_warning: Callable[[str], None] = _ignore_warning
    """Where a note a library reader returned is sent."""

    record_identity_ruling: Callable[
        [config.WorkspaceLayout, tuple[str, ...], Callable[[str], None]], str
    ] = _write_identity_ruling
    """Writes the keep-distinct ruling and returns its workspace-relative
    path. A port, not a direct call, so an adapter that already routes this
    write through its own seam keeps doing so."""


class DecisionObserver:
    """The lines that print INSIDE a commit section, before further work in
    the same sequence. Every method is a no-op; an adapter overrides the ones
    it renders. A result that ends the sequence is returned instead."""

    def merge_member_missing(self, survivor: str, absorbed: str) -> None:
        """A merge member vanished between the prompt and the section."""

    def relate_endpoint_missing(self, source: str, target: str) -> None:
        """A relation endpoint vanished between the prompt and the section."""

    def keep_distinct_unrecorded(self, member_ids: tuple[str, ...], error: str) -> None:
        """The keep-distinct ruling could not be recorded (fail-open)."""


def identity_decision_key(member_ids: Sequence[str]) -> str:
    """The stored queue key of an identity decision over `member_ids`."""
    return pq.stored_decision_key("identity", pq.identity_key(member_ids))


def skip_decision(decision_key: str) -> NotApplied:
    """Skip one decision: write nothing, record nothing. The queue row, if
    any, stays `pending` and the next run offers it again."""
    return NotApplied(Action.SKIP, "skipped")


def _all_present(layout: config.WorkspaceLayout, concept_ids: Sequence[str]) -> bool:
    return all(
        okf.concept_path_for(concept_id, layout.bundle_dir).exists()
        for concept_id in concept_ids
    )


def record_keep_distinct(
    root: Path,
    layout: config.WorkspaceLayout,
    member_ids: Sequence[str],
    *,
    ports: DecisionPorts,
) -> Applied | NotApplied:
    """Persist a human "keep distinct" ruling (#797) and commit it.

    No commit section and no presence check: `adjudicate --apply` calls this
    without either, and `commit_keep_distinct` adds both for `curate`. A write
    error is RETURNED as `NotApplied("record_failed")`, never raised: the
    decline already took effect (no merge happened), so the worst case is the
    group being offered again, and losing the operator's place to persist a
    suppression would be the larger harm. The commit subject keeps the member
    order the caller gave."""
    members = tuple(member_ids)
    try:
        rel_path = ports.record_identity_ruling(layout, members, ports.on_warning)
        commit_sha = ports.autocommit(
            root, [rel_path], f"openkos: keep distinct {'/'.join(members)}"
        )
    except (OSError, ValueError) as exc:
        return NotApplied(Action.KEEP_DISTINCT, "record_failed", str(exc))
    return Applied(identity_decision_key(members), Action.KEEP_DISTINCT, commit_sha)


def commit_keep_distinct(
    root: Path,
    layout: config.WorkspaceLayout,
    member_ids: Sequence[str],
    *,
    ports: DecisionPorts,
    observer: DecisionObserver,
) -> Applied | NotApplied:
    """`record_keep_distinct` inside one commit section, after a presence check.

    The prompt held no lock, so a member forgotten meanwhile has no ruling to
    record -- a row naming it would outlive the concept it is about. That case
    is a SILENT `NotApplied("member_missing")`: no observer call and nothing
    written. A record failure notifies the observer inside the section, then
    is returned."""
    members = tuple(member_ids)
    with ports.commit_section():
        if not _all_present(layout, members):
            return NotApplied(Action.KEEP_DISTINCT, "member_missing")
        outcome = record_keep_distinct(root, layout, members, ports=ports)
        if isinstance(outcome, NotApplied):
            observer.keep_distinct_unrecorded(members, outcome.detail or "")
        return outcome
