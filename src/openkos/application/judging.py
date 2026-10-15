"""Judging as an application service: the quote half (the
`judging-application-service` change, S2b; ADR-0059).

Judging is the model-backed step that turns an unjudged item into one a human
can decide. The surface is a quote, consent, run protocol: a caller asks for an
LLM-free `JudgingQuote`, the human consents to it by handing back its
`QuoteToken`, and a run (S2c) judges at most what was quoted. This module holds
the first half: the binding, the token, the identity quote and its notices. It
has no run yet.

The surface is plain data. It never prompts, prints, reads a terminal or raises
`typer.Exit`; everything a client renders reaches it as returned data. It is
OWNER-LOCAL (a quote carries work keys and the serve helpers read stored
rationales), so no disclosing adapter may import it.

A quote makes no model call and costs nothing. The one question that looks like
it needs the model -- which groups would make NO call, because no member is
readable or every member is confidential -- is answered by the leaf judge
itself, run over a recording stand-in that is handed no model: the rule lives
in `resolution/adjudication.py` alone and cannot drift from this module.
"""

import contextlib
import hashlib
import sqlite3
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

from openkos import config
from openkos.application import backends as application_backends
from openkos.application import identity_adjudication
from openkos.application import pending as application_pending
from openkos.application.lock_wait import CommitSection
from openkos.llm.base import LLMBackend, Message
from openkos.resolution import adjudication as leaf
from openkos.resolution.candidates import (
    CandidateGroup,
    CandidateGroupReport,
    find_candidates_report,
)
from openkos.state import adjudications as adjudications_store
from openkos.state import jobs

JudgingKind = Literal["identity", "relation_type", "volatility"]
"""What a quote is about. The quote carries its kind, so a later kind is
additive."""

ChangedField = Literal["model", "backend", "locality", "confidential", "rubric"]
"""The binding fields `QuoteStale` names when they moved."""

_NUL = "\x00"


class JudgingRefused(Exception):
    """A quote cannot be made at all: the root is not a workspace or its
    configuration cannot be read. Raised, never printed."""


@dataclass(frozen=True)
class QuoteBinding:
    """What consent was given to. A change of any field is `QuoteStale`."""

    kind: JudgingKind
    model: str
    """`config.resolve_task_model(cfg, task)`."""

    backend: str
    """`cfg.backend`."""

    is_local: bool
    """The client's own locality verdict (construction only, no call)."""

    effective_confidential: bool
    """`include_confidential` or the resolved local exemption."""

    rubric_digest: str | None
    """The judgment rubric's digest for a kind that has one, else `None`."""

    def digest(self) -> str:
        """Opaque sha256 hex of every field, sorted and NUL-separated."""
        return _digest(
            [
                f"kind={self.kind}",
                f"model={self.model}",
                f"backend={self.backend}",
                f"is_local={int(self.is_local)}",
                f"effective_confidential={int(self.effective_confidential)}",
                f"rubric_digest={self.rubric_digest}",
            ]
        )


def changed_fields(
    consented: QuoteBinding, current: QuoteBinding
) -> tuple[ChangedField, ...]:
    """Every field of `consented` that `current` no longer matches, in a fixed
    order. The work set is NOT part of the comparison: a moved work set is the
    run's ceiling to enforce, never a reason to refuse the whole run."""
    moved: list[ChangedField] = []
    if consented.model != current.model:
        moved.append("model")
    if consented.backend != current.backend:
        moved.append("backend")
    if consented.is_local != current.is_local:
        moved.append("locality")
    if consented.effective_confidential != current.effective_confidential:
        moved.append("confidential")
    if consented.rubric_digest != current.rubric_digest:
        moved.append("rubric")
    return tuple(moved)


@dataclass(frozen=True)
class QuoteStale:
    """A token whose binding no longer matches the current state. Returned,
    never raised, by a run before any client call (S2c)."""

    kind: JudgingKind
    changed: tuple[ChangedField, ...]


@dataclass(frozen=True)
class WorkItem:
    work_key: str
    """Identity: `adjudications_store.group_key_for(member_ids)`."""

    member_digests: tuple[tuple[str, str], ...]
    """Sorted `(concept id, sha256 of its current bytes)`; an unreadable member
    carries `""`."""


@dataclass(frozen=True)
class QuoteToken:
    """The context consent was given for. It holds no time: a token is stale
    only when a binding field moved, decided by resolved values and digests."""

    binding: QuoteBinding
    include_confidential: bool
    """The caller's request bit, re-resolved at run."""

    include_uncertain: bool
    work: tuple[WorkItem, ...]
    """The ceiling, in quote order."""

    calls: int
    """An upper bound on chat calls; a zero-call group is in `work` and adds
    nothing here."""

    def digest(self) -> str:
        """Opaque sha256 hex for transport, covering every field, sorted and
        NUL-separated. Nothing parses it back in this change."""
        parts = [
            f"binding={self.binding.digest()}",
            f"include_confidential={int(self.include_confidential)}",
            f"include_uncertain={int(self.include_uncertain)}",
            f"calls={self.calls}",
        ]
        parts += [
            f"work={item.work_key}"
            + _NUL
            + _NUL.join(f"{ref}:{digest}" for ref, digest in item.member_digests)
            for item in self.work
        ]
        return _digest(parts)


@dataclass(frozen=True)
class QuoteNotice:
    code: Literal[
        "model", "truncated", "quarantined", "rubric_rejudge", "unattended_active"
    ]
    count: int | None = None
    """truncated: groups shown; rubric_rejudge: groups."""

    produced: int | None = None
    """truncated only: how many groups the corpus produced before the cap."""

    state: Literal["active", "unknown"] | None = None
    """unattended_active only."""

    model: str | None = None
    """model only: the resolved task model."""

    backend: str | None = None
    """model only: `cfg.backend`."""


@dataclass(frozen=True)
class JudgingQuote:
    kind: JudgingKind
    total: int
    """Candidate groups considered, kept-distinct ones already dropped."""

    served: int
    """Decided (SAME, DIFFERENT) verdicts served with no call."""

    uncertain: int
    """Servable UNCERTAIN verdicts. In `to_judge` only when opted in."""

    to_judge: int
    calls: int
    """An upper bound; the run never exceeds it."""

    binding: QuoteBinding
    notices: tuple[QuoteNotice, ...]
    token: QuoteToken


@dataclass(frozen=True)
class JudgingProgress:
    index: int
    total: int
    work_key: str
    verdict: str
    """`same`, `different` or `uncertain`; never the rationale."""


class JudgingObserver:
    """Receives a run's events (S2c). Every method is a no-op."""

    def progress(self, event: JudgingProgress) -> None:
        return None

    def chunk_persisted(self, persisted: int) -> None:
        return None


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class JudgingPorts:
    """The effects a quote and a run reach. Only the chat-client factory is
    required; the rest default to the real implementation."""

    chat_client: Callable[[config.Config, str | None], LLMBackend]
    resolve_local_exemption: Callable[
        [application_backends.HasLocality, config.Config], bool
    ] = application_backends.resolve_local_exemption
    commit_section: CommitSection = contextlib.nullcontext
    find_candidates_report: Callable[..., CandidateGroupReport] = find_candidates_report
    adjudicate_candidates: Callable[..., leaf.AdjudicationBatch] = (
        leaf.adjudicate_candidates
    )
    sleep: Callable[[float], None] = time.sleep
    """The retry backoff's wait (S2c); a quote never sleeps."""

    now: Callable[[], datetime] = _utc_now


def _digest(parts: Sequence[str]) -> str:
    return hashlib.sha256(_NUL.join(sorted(parts)).encode("utf-8")).hexdigest()


class _CallRecorder:
    """Stands in for the model when the quote asks the leaf which groups would
    make a call. It is handed to the leaf in place of a client, answers every
    prompt with an empty reply and counts them; no model is reached."""

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls += 1
        return ""


def _calling_groups(
    groups: Sequence[CandidateGroup],
    *,
    layout: config.WorkspaceLayout,
    ports: JudgingPorts,
    include_confidential: bool,
    local_exemption: bool,
) -> set[str]:
    """The work keys of the groups a run would spend a call on: the leaf's own
    determination, read off a recording stand-in. A group with no readable
    member (or only blocked ones) short-circuits in the leaf and calls nothing."""
    if not groups:
        return set()
    recorder = _CallRecorder()
    calling: set[str] = set()
    seen = 0

    def on_progress(index: int, total: int, result: leaf.AdjudicatedCandidate) -> None:
        nonlocal seen
        if recorder.calls > seen:
            calling.add(adjudications_store.group_key_for(result.candidate.member_ids))
            seen = recorder.calls

    ports.adjudicate_candidates(
        list(groups),
        bundle_dir=layout.bundle_dir,
        llm=cast(LLMBackend, recorder),
        include_confidential=include_confidential,
        local_exemption=local_exemption,
        on_progress=on_progress,
    )
    return calling


def _unattended_notice(
    layout: config.WorkspaceLayout, cfg: config.Config, now: datetime
) -> QuoteNotice | None:
    """The unattended engine's recent activity, from `.openkos/jobs.db` opened
    read-only (so the file is never created): an open job, or the newest job
    started within `maintenance_interval_seconds`. An absent or schema-less
    record yields no notice; an unreadable one yields `state="unknown"`, since
    an informational notice errs toward disclosure."""
    try:
        conn = jobs.open_jobs_read_only(layout.jobs_db_path)
    except jobs.JobsStoreUnreadableError:
        return QuoteNotice("unattended_active", state="unknown")
    if conn is None:
        return None
    try:
        newest = jobs.last_job(conn)
    except sqlite3.Error:
        return QuoteNotice("unattended_active", state="unknown")
    finally:
        conn.close()
    if newest is None:
        return None
    if newest.ended_at is None:
        return QuoteNotice("unattended_active", state="active")
    try:
        started = datetime.strptime(newest.started_at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=UTC
        )
    except ValueError:
        return QuoteNotice("unattended_active", state="unknown")
    window = timedelta(seconds=cfg.unattended.maintenance_interval_seconds)
    if now - started <= window:
        return QuoteNotice("unattended_active", state="active")
    return None


def _work_item(
    group: CandidateGroup, current_digest: Callable[[str], str | None]
) -> WorkItem:
    return WorkItem(
        work_key=adjudications_store.group_key_for(group.member_ids),
        member_digests=tuple(
            (member, current_digest(member) or "")
            for member in sorted(group.member_ids)
        ),
    )


def quote_judging(
    root: Path,
    kind: JudgingKind,
    *,
    include_confidential: bool = False,
    include_uncertain: bool = False,
    ports: JudgingPorts,
) -> JudgingQuote:
    """Price judging for `kind` at the workspace `root`, with no model call.

    Builds the client only to read its locality and the model tag, drops the
    groups a human already ruled distinct, partitions the rest under the
    effective confidential bit and reports what a run would judge and at most
    how many calls it would make. Judged-uncertain items are left out of
    `to_judge` and `calls` unless `include_uncertain`. Raises `JudgingRefused`
    when `root` is not a readable workspace and `ValueError` for a kind this
    stage does not quote yet."""
    if kind != "identity":
        raise ValueError(f"judging kind {kind!r} cannot be quoted yet")
    reason = config.require_workspace(root)
    if reason is not None:
        raise JudgingRefused(f"openkos judging: refusing to quote -- {reason}.")
    layout = config.WorkspaceLayout(root)
    try:
        cfg = config.read_config(root)
    except (OSError, ValueError) as exc:
        raise JudgingRefused(
            f"openkos judging: failed while reading the workspace -- {exc}."
        ) from exc

    # The literal task name, at the call: `test_chat_timeout_wiring` reads it
    # off the AST so a refactor cannot silently drop the `models:` override.
    client = cast(
        application_backends.HasLocality, ports.chat_client(cfg, "adjudication")
    )
    model = config.resolve_task_model(cfg, "adjudication")
    local_exemption = ports.resolve_local_exemption(client, cfg)
    effective = include_confidential or local_exemption
    binding = QuoteBinding(
        kind=kind,
        model=model,
        backend=cfg.backend,
        is_local=client.locality.is_local,
        effective_confidential=effective,
        rubric_digest=leaf.rubric_digest(),
    )

    report = ports.find_candidates_report(layout.bundle_dir)
    groups = [
        group
        for group in report.groups
        if not application_pending.is_group_kept_distinct(layout, group.member_ids)
    ]
    serves = identity_adjudication.partition_adjudication_serves(
        layout,
        groups,
        include_confidential=effective,
        on_warning=None,
        surface="judging",
    )
    decided = {
        key
        for key, verdict in serves.served.items()
        if verdict.verdict != leaf.Verdict.UNCERTAIN
    }
    uncertain = set(serves.served) - decided
    fresh = {adjudications_store.group_key_for(g.member_ids) for g in serves.to_judge}
    wanted = fresh | uncertain if include_uncertain else fresh
    needed = [
        group
        for group in groups
        if adjudications_store.group_key_for(group.member_ids) in wanted
    ]

    calling = _calling_groups(
        needed,
        layout=layout,
        ports=ports,
        include_confidential=include_confidential,
        local_exemption=local_exemption,
    )
    current_digest = application_pending.current_finding_digest(layout.bundle_dir)
    work = tuple(_work_item(group, current_digest) for group in needed)
    calls = len(calling)

    notices: list[QuoteNotice] = [
        QuoteNotice("model", model=model, backend=cfg.backend)
    ]
    if report.produced > report.retained:
        notices.append(
            QuoteNotice("truncated", count=report.retained, produced=report.produced)
        )
    if serves.rubric_stale:
        notices.append(QuoteNotice("rubric_rejudge", count=serves.rubric_stale))
    unattended = _unattended_notice(layout, cfg, ports.now())
    if unattended is not None:
        notices.append(unattended)

    token = QuoteToken(
        binding=binding,
        include_confidential=include_confidential,
        include_uncertain=include_uncertain,
        work=work,
        calls=calls,
    )
    return JudgingQuote(
        kind=kind,
        total=len(groups),
        served=len(decided),
        uncertain=len(uncertain),
        to_judge=len(needed),
        calls=calls,
        binding=binding,
        notices=tuple(notices),
        token=token,
    )
