"""Identity adjudication: serve, persist and reassemble (the
`judging-application-service` change, S1; ADR-0059).

These helpers answer one question for the identity judge -- which candidate
groups already hold a servable verdict in `.openkos/findings.db`, and how does
a fresh verdict get persisted -- and they are the part of the judging surface
that `adjudicate`, `curate`'s Identity stage and the judging service all share.
They lived in `cli/main.py` (#779, #838, #867, #1137, #1226); the bodies moved
here unchanged, so the CLI keeps thin delegators under the old names (the #1168
precedent) and every patched name still intercepts.

The one change of shape: this module speaks data, never to a terminal. The two
advisories a failed persist used to print reach the caller as fields of
`PersistResult`, and an unreadable store reaches it through an `on_warning`
callback. The CLI delegator renders them, byte for byte, to stderr.

`servable_verdict` is the one predicate for "this stored row answers this
group". The serve partition and (from S2a) the daemon's identity stage both ask
it, so the two can never disagree about what a servable verdict is.
"""

import contextlib
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from typing import Literal, NamedTuple

from openkos import config, lock
from openkos.application import curate_queue, queue_resolution
from openkos.application import pending as application_pending
from openkos.application.lock_wait import CommitSection
from openkos.resolution.adjudication import (
    AdjudicatedCandidate,
    Verdict,
    rubric_digest,
    withdraw_self_refuting_same,
)
from openkos.resolution.candidates import CandidateGroup
from openkos.state import adjudications as adjudications_store
from openkos.state import derived

type CurrentDigest = Callable[[str], str | None]
"""A member's current content digest, or `None` when it is unreadable."""

_Refusal = Literal["confidential", "rubric", "other"]


class AdjudicationServes(NamedTuple):
    """What `partition_adjudication_serves` answers (#779, widened by #838). A
    NamedTuple for `EdgeSuggestionServes`' exact reason: a widening positional
    tuple makes every unpack order-sensitive by convention alone, and
    `rubric_stale` is a count a caller could silently swap with nothing but a
    type checker noticing."""

    served: dict[str, AdjudicatedCandidate]
    to_judge: list[CandidateGroup]
    rubric_stale: int
    """How many groups re-judge with a persisted row predating the current
    judgment rubric (#838) -- the caller names the reason so the re-spend is
    announced, never a surprising `0 of N served`. Counted whether or not the
    group's members ALSO drifted: the rubric check runs before the costlier
    per-member hashing, and the claim the stderr line makes (the row predates
    the rubric) is true either way."""

    store_read: bool
    """Whether the store was actually READ (#809's fact, adopted here by #867
    when curate's Identity became this partition's second caller). An absent
    file and an unreadable one both serve nothing, but neither is a store that
    was read and held nothing for these groups -- and the split line renders
    only for the last of the three, on BOTH surfaces, so the two can never
    disagree the way #809 found the edge family's had."""


class PersistResult(NamedTuple):
    """What `persist_adjudications` reports instead of printing (the surface is
    plain data). At most one of `store_error` and `busy` is set."""

    persisted: int
    """Verdicts written to the store."""

    dropped: int
    """Results skipped because a member was unreadable or moved since it was
    judged (#1137): a row whose staleness can never be checked would serve
    forever."""

    store_error: str | None
    """The store write failed (`OSError` or `sqlite3.Error`); the text of the
    error. Nothing was persisted."""

    busy: str | None
    """The workspace lock was busy; the text of the error. Nothing was
    persisted."""


def reassemble_adjudications(
    candidates: list[CandidateGroup],
    served: dict[str, AdjudicatedCandidate],
    fresh: Sequence[AdjudicatedCandidate],
) -> list[AdjudicatedCandidate]:
    """Rebuild a run's verdicts in CANDIDATE order, merging what the store
    served with what the model just judged (#867) -- the adjudication twin of
    `_reassemble_edge_suggestions`, extracted from the `adjudicate` verb for the
    same #809 reason: `curate`'s Identity stage is a second caller, and a later
    fix to the merge would otherwise land in one copy and silently miss the
    other.

    A served verdict wins over a fresh one for the same key -- a tiebreak the
    callers' own partition makes unreachable, not a policy. Groups past a
    mid-batch failure (#441) appear in neither map and stay absent. Ordering
    follows `candidates`, so a served verdict and a fresh one are
    indistinguishable downstream."""
    fresh_by_key = {
        adjudications_store.group_key_for(result.candidate.member_ids): result
        for result in fresh
    }
    rebuilt: list[AdjudicatedCandidate] = []
    for group in candidates:
        key = adjudications_store.group_key_for(group.member_ids)
        found = served.get(key) or fresh_by_key.get(key)
        if found is not None:
            rebuilt.append(found)
    return rebuilt


def _serve(
    stored: adjudications_store.Adjudication,
    group: CandidateGroup,
    *,
    current_digest: CurrentDigest,
    rubric: str,
    include_confidential: bool | None,
) -> AdjudicatedCandidate | _Refusal:
    """The serve rule, returning the refusal's kind so the partition can count
    the rubric refusals without re-deciding anything."""
    if include_confidential is not None and (
        stored.include_confidential != include_confidential
    ):
        return "confidential"
    # #838: a row from a different (or unknown, pre-column) rubric is a verdict
    # this build would not produce; the operator who upgraded to a judgment fix
    # must not keep being served the exact verdict the fix exists to replace.
    # Checked BEFORE the member digests because it is the cheap comparison -- so
    # a group whose members also drifted still counts as rubric-stale, which
    # stays honest: the row does predate the rubric, whatever else is stale
    # about it.
    if stored.rubric_digest != rubric:
        return "rubric"
    # The stored ref SET must equal the group's current member set (#779
    # review, two lenses): a row carrying digests for fewer or different members
    # than the group now holds was computed over a DIFFERENT prompt, however
    # fresh each stored digest is.
    if {digest.input_ref for digest in stored.input_digests} != set(
        group.member_ids
    ) or any(
        current_digest(digest.input_ref) != digest.digest
        for digest in stored.input_digests
    ):
        return "other"
    try:
        verdict = Verdict(stored.verdict)
    except ValueError:
        return "other"
    # The SAME judgment path a fresh verdict goes through (#796). A row stored
    # before that withdrawal existed still carries the verdict its own rationale
    # argues against, and serving it would hand the operator a merge the engine
    # now refuses to propose. Re-deciding costs nothing here: the rule is pure,
    # reads only the rationale the row already carries, and is idempotent, so a
    # row written after the rule shipped passes through untouched.
    verdict, rationale = withdraw_self_refuting_same(verdict, stored.rationale)
    return AdjudicatedCandidate(
        candidate=group,
        verdict=verdict,
        confidence=stored.confidence,
        rationale=rationale,
    )


def servable_verdict(
    stored: adjudications_store.Adjudication,
    group: CandidateGroup,
    *,
    current_digest: CurrentDigest,
    rubric: str,
    include_confidential: bool | None,
) -> AdjudicatedCandidate | None:
    """The verdict `stored` serves for `group`, or `None` when it must re-judge.

    A row serves iff it matches the run's `include_confidential` bit (a verdict
    computed over a different member subset must never serve), was computed
    under THIS build's judgment `rubric` (#838), carries digests for exactly the
    group's member set, every stored digest equals the member's CURRENT content
    hash, and the stored verdict is in the enum. `include_confidential=None`
    means the bit is not compared: the one caller that passes it preserves the
    latest owner-local judgment rather than judging."""
    served = _serve(
        stored,
        group,
        current_digest=current_digest,
        rubric=rubric,
        include_confidential=include_confidential,
    )
    return None if isinstance(served, str) else served


def partition_adjudication_serves(
    layout: config.WorkspaceLayout,
    candidates: list[CandidateGroup],
    *,
    include_confidential: bool,
    on_warning: Callable[[str], None] | None,
    surface: str = "adjudicate",
) -> AdjudicationServes:
    """Split `candidates` into verdicts servable from `.openkos/findings.db` and
    the groups that still need a model call (#779) -- the adjudication twin of
    `_partition_persisted_serves`, same posture throughout.

    A group is SERVED iff `servable_verdict` serves its latest persisted row.
    Everything else re-judges, conservatively -- including a present-but-corrupt
    store, which degrades to one advisory through `on_warning` and a full fresh
    judge rather than crashing before any model spend (the #685 item-4 posture).

    `rubric_stale` counts the groups refused with a row predating the current
    rubric (mismatched or absent digest) -- whether or not their members also
    drifted, since the rubric gate runs first -- so the caller can name the
    reason: a rubric change re-judging every cached group would otherwise read
    as a surprising `0 of N served` with no stated cause (#838's
    announced-re-spend ruling).

    `on_warning=None` is curate's pricing probe (#867 review): the stage RUN
    rebuilds this partition minutes later and warns then, so an unreadable store
    costs one warning per curate run, not one per read. `surface` names the
    command the user actually ran, so a warning during a curate run is not
    attributed to the standalone verb."""
    if not layout.findings_db_path.exists():
        return AdjudicationServes({}, candidates, 0, store_read=False)
    try:
        conn = derived.open_derived_connection(layout.findings_db_path)
        try:
            persisted = adjudications_store.open_adjudications(conn)
        finally:
            conn.close()
    except (OSError, sqlite3.Error) as exc:
        if on_warning is not None:
            on_warning(
                f"openkos {surface}: warning -- failed to read persisted "
                f"adjudications ({exc}); judging every group fresh."
            )
        return AdjudicationServes({}, candidates, 0, store_read=False)
    latest: dict[str, adjudications_store.Adjudication] = {}
    for row in persisted:
        latest[adjudications_store.group_key_for(row.member_ids)] = row

    current_digest = application_pending.current_finding_digest(layout.bundle_dir)
    current_rubric = rubric_digest()
    served: dict[str, AdjudicatedCandidate] = {}
    to_judge: list[CandidateGroup] = []
    rubric_stale = 0
    for group in candidates:
        key = adjudications_store.group_key_for(group.member_ids)
        stored = latest.get(key)
        if stored is None:
            to_judge.append(group)
            continue
        outcome = _serve(
            stored,
            group,
            current_digest=current_digest,
            rubric=current_rubric,
            include_confidential=include_confidential,
        )
        if isinstance(outcome, str):
            if outcome == "rubric":
                rubric_stale += 1
            to_judge.append(group)
            continue
        served[key] = outcome
    return AdjudicationServes(served, to_judge, rubric_stale, store_read=True)


def servable_stored_verdicts(
    layout: config.WorkspaceLayout, groups: Sequence[CandidateGroup]
) -> list[AdjudicatedCandidate]:
    """The latest stored verdict of each group that is still servable, for the
    unattended pass to carry into the proposals it enqueues (Decision 6).

    Reads `.openkos/findings.db` read-only and asks `servable_verdict` -- the
    rule the serve partition asks -- with `include_confidential=None`: the pass
    is not judging, it preserves the latest owner-local judgment, and the queue
    payload is owner-local. It builds no client and calls no model. An absent or
    unreadable store carries nothing (a pass over it enqueues unjudged groups,
    as before), and nothing is ever created or written."""
    if not layout.findings_db_path.exists():
        return []
    try:
        conn = derived.open_derived_connection(layout.findings_db_path)
        try:
            persisted = adjudications_store.open_adjudications(conn)
        finally:
            conn.close()
    except OSError, sqlite3.Error:
        return []
    latest = {
        adjudications_store.group_key_for(row.member_ids): row for row in persisted
    }
    current_digest = application_pending.current_finding_digest(layout.bundle_dir)
    rubric = rubric_digest()
    carried: list[AdjudicatedCandidate] = []
    for group in groups:
        stored = latest.get(adjudications_store.group_key_for(group.member_ids))
        if stored is None:
            continue
        served = servable_verdict(
            stored,
            group,
            current_digest=current_digest,
            rubric=rubric,
            include_confidential=None,
        )
        if served is not None:
            carried.append(served)
    return carried


def persist_adjudications(
    layout: config.WorkspaceLayout,
    results: Sequence[AdjudicatedCandidate],
    *,
    include_confidential: bool,
    judged_digests: Mapping[str, str | None] | None,
    commit_section: CommitSection,
    write_through: bool = False,
) -> PersistResult:
    """Persist freshly judged adjudication verdicts (#779), fail-open: a failed
    persist is reported in the result, never raised -- the same #684 posture
    curate's findings persist takes. A result any of whose members has no
    current digest (unreadable -- including the no-readable-member UNCERTAIN
    short-circuit) is skipped: a row whose staleness can never be checked would
    serve forever.

    `judged_digests` is each member's content digest as it stood BEFORE the
    judging call (#1137): the judging call holds no workspace lock, so a member
    edited, raised or forgotten meanwhile has a different digest now, and its
    verdict is dropped rather than stored against content nobody judged. Without
    it the digests are read here, at persist time.

    The persist is a commit phase: it enters `commit_section`, and a busy
    workspace is reported in `PersistResult.busy`. A persisted DIFFERENT verdict
    answers the pair's open identity row (#1226), under the same lock as the
    persist so no forget can interleave; best-effort, the queue is a cache.

    `write_through` (the judging run) also writes each persisted SAME or UNCERTAIN
    verdict through to the group's OPEN identity row, in the same section and
    amend-only (`curate_queue.set_identity_verdicts`): a group with no open row
    gets its row, verdict included, from the next producer pass. `curate` keeps
    `False` and its missing-only enqueue."""
    if not results:
        return PersistResult(0, 0, None, None)
    try:
        with commit_section():
            current_digest = application_pending.current_finding_digest(
                layout.bundle_dir
            )
            # #838: every fresh verdict records the rubric it was computed under,
            # so the serve gate can refuse it after a judgment fix ships.
            # Computed once -- it is constant within a build.
            current_rubric = rubric_digest()
            batch: list[adjudications_store.Adjudication] = []
            kept: list[AdjudicatedCandidate] = []
            for result in results:
                digests: list[adjudications_store.InputDigest] = []
                for member_id in result.candidate.member_ids:
                    digest = current_digest(member_id)
                    if digest is None or (
                        judged_digests is not None
                        and judged_digests.get(member_id) != digest
                    ):
                        break
                    digests.append(
                        adjudications_store.InputDigest(
                            input_ref=member_id, digest=digest
                        )
                    )
                else:
                    kept.append(result)
                    batch.append(
                        adjudications_store.Adjudication(
                            member_ids=tuple(result.candidate.member_ids),
                            verdict=result.verdict.value,
                            confidence=result.confidence,
                            rationale=result.rationale,
                            include_confidential=include_confidential,
                            input_digests=tuple(digests),
                            rubric_digest=current_rubric,
                        )
                    )
            dropped = len(results) - len(batch)
            if not batch:
                return PersistResult(0, dropped, None, None)
            try:
                conn = derived.open_derived_connection(layout.findings_db_path)
                try:
                    adjudications_store.record_adjudications(conn, batch)
                finally:
                    conn.close()
                # #1226: a persisted DIFFERENT verdict answers the pair's open
                # identity row.
                for stored in batch:
                    if stored.verdict == Verdict.DIFFERENT.value:
                        queue_resolution.resolve_judged_different(
                            layout.root, member_ids=stored.member_ids
                        )
                if write_through:
                    # Already inside the one section: the lock is not
                    # re-entrant, so the amendment enters a no-op of its own.
                    curate_queue.set_identity_verdicts(
                        layout,
                        kept,
                        pinned=judged_digests or {},
                        commit_section=contextlib.nullcontext,
                    )
            except (OSError, sqlite3.Error) as exc:
                return PersistResult(0, dropped, str(exc), None)
            return PersistResult(len(batch), dropped, None, None)
    except lock.WorkspaceBusyError as exc:
        return PersistResult(0, 0, None, str(exc))
