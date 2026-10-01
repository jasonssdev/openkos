"""The advisors' compute-and-enqueue path into the pending-work queue (#1141,
`mvp4-unattended-foundations` design Decision 4, ADR-0037).

Each advisor service already computes a typed result (`ContradictionsOutcome`,
`DuplicatesReport`, `SuggestRelationsOutcome`, `VolatilityOutcome`, and the
revision findings). This module turns that result into queue `Proposal`s and
publishes them through `state.pending_queue`; the unattended runner calls it
after the advisor's own compute. It is an adapter over those result types, not a
change to them, so every CLI verb is byte-identical: no verb enqueues here (the
curate stages that read and resolve rows are a later unit's). It never writes the
bundle -- only `.openkos/findings.db` -- and never resolves a row.

**Writes happen in a commit phase.** `publish` hands every upsert and the
retirement pass the `CommitSection` it was given; this module never takes the
workspace lock itself.

**Completeness is the producer's claim, made from the result.** A row is retired
as `stale` only when the advisor ran to completion over everything it could see:
a bounded run (`deferred_by_bound`), an aborted one (`failure`), a capped or
windowed candidate list, or a run whose inputs were unavailable cannot tell
"absent" from "not reached", so it retires nothing (`*_run_complete`). A
proposal the queue already holds a decline for is suppressed by the store, and
still counts as seen.

**Skips are deliberate.** A suggestion with no usable value (a fail-closed
degrade), an edge with an unreadable endpoint, and a volatility suggestion equal
to the type's current default are not proposals; none of them is a row, and in a
complete run a row that used to exist for one is retired.
"""

import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from openkos.application import duplicates_service, revisions
from openkos.application.contradictions_service import (
    ContradictionsOutcome,
    contradiction_spec_key,
)
from openkos.application.lock_wait import CommitSection
from openkos.application.suggest_relations_service import SuggestRelationsOutcome
from openkos.application.suggest_volatility_service import VolatilityOutcome
from openkos.resolution import adjudication
from openkos.resolution import contradiction as contradiction_engine
from openkos.state import pending_queue as pq
from openkos.state import revision_findings

PRODUCER_CONTRADICTIONS: Final = "contradictions/1"
PRODUCER_IDENTITY: Final = "duplicates/1"
PRODUCER_RELATIONS: Final = "suggest-relations/1"
PRODUCER_VOLATILITY: Final = "suggest-volatility/1"
PRODUCER_REVISIONS: Final = "revisions/1"

CurrentDigest = Callable[[str], str | None]
"""A concept id to the sha256 of its CURRENT bytes, or `None` when unreadable
(`application.pending.current_finding_digest`)."""

InputDigestsFor = Callable[[Path, Any], Iterable[Any]]
"""`(bundle_dir, candidate spec)` to `(input_ref, digest)` rows -- the
contradiction digest convention (`cli.curate.finding_input_digests`), passed in
so this module never imports the CLI."""


@dataclass(frozen=True)
class ProducerResult:
    """What one producer pass did, by outcome."""

    kind: str
    inserted: int
    unchanged: int
    replaced: int
    suppressed: int
    retired: int
    complete: bool


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _distinct(ids: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(ids))


def publish(
    conn: Any,
    kind: str,
    proposals: Sequence[pq.Proposal],
    *,
    complete: bool,
    commit_section: CommitSection,
    bundle_dir: Path,
    clock: Callable[[], datetime] | None = None,
) -> ProducerResult:
    """Upsert every proposal, then retire the kind's unseen rows when the run
    was `complete`. The one place the three queue properties are applied, so no
    kind can skip them."""
    clock_kwargs: dict[str, Any] = {} if clock is None else {"clock": clock}
    counts = dict.fromkeys(pq.UpsertOutcome, 0)
    for proposal in proposals:
        counts[
            pq.upsert_proposal(
                conn,
                proposal,
                commit_section=commit_section,
                bundle_dir=bundle_dir,
                **clock_kwargs,
            )
        ] += 1
    retired = pq.retire_unseen(
        conn,
        kind,
        {proposal.decision_key for proposal in proposals},
        complete=complete,
        commit_section=commit_section,
        **clock_kwargs,
    )
    return ProducerResult(
        kind=kind,
        inserted=counts[pq.UpsertOutcome.INSERTED],
        unchanged=counts[pq.UpsertOutcome.UNCHANGED],
        replaced=counts[pq.UpsertOutcome.REPLACED],
        suppressed=counts[pq.UpsertOutcome.SUPPRESSED],
        retired=retired,
        complete=complete,
    )


def _digest_rows(rows: Iterable[Any]) -> tuple[pq.InputDigest, ...]:
    return tuple(pq.InputDigest(row.input_ref, row.digest) for row in rows)


def _file_digests(
    current_digest: CurrentDigest, concept_ids: Iterable[str]
) -> tuple[pq.InputDigest, ...] | None:
    """One digest row per concept, or `None` when any is unreadable: a row whose
    inputs cannot be re-read could never be told stale."""
    rows: list[pq.InputDigest] = []
    for concept_id in concept_ids:
        digest = current_digest(concept_id)
        if digest is None:
            return None
        rows.append(pq.InputDigest(concept_id, digest))
    return tuple(rows)


# -- contradictions -----------------------------------------------------------


def contradictions_run_complete(outcome: ContradictionsOutcome) -> bool:
    """Complete when nothing stopped the loop, no `max_calls` bound left
    candidates unjudged, and the plan the run judged was not cut by the pair
    cap."""
    return (
        outcome.batch.failure is None
        and outcome.deferred_by_bound == 0
        and outcome.plan.total_count == len(outcome.plan.specs)
    )


def contradiction_proposals(
    outcome: ContradictionsOutcome, *, input_digests: InputDigestsFor, bundle_dir: Path
) -> list[pq.Proposal]:
    """One proposal per `CONTRADICTS` verdict (served or fresh). The key is the
    decline sidecar's own, so a typed-edge and a merged-body contradiction over
    one pair are two rows."""
    specs = {contradiction_spec_key(spec): spec for spec in outcome.plan.specs}
    return [
        contradiction_proposal(
            verdict,
            specs.get((verdict.pair_ids, verdict.merged_absorbed_id)),
            input_digests=input_digests,
            bundle_dir=bundle_dir,
        )
        for verdict in outcome.verdicts
        if verdict.verdict is contradiction_engine.Verdict.CONTRADICTS
    ]


def contradiction_proposal(
    verdict: contradiction_engine.ContradictionVerdict,
    spec: contradiction_engine._CandidateSpec | None,
    *,
    input_digests: InputDigestsFor,
    bundle_dir: Path,
) -> pq.Proposal:
    """The proposal for one `CONTRADICTS` verdict, with the digests of the
    candidate it was judged from (none when the spec is unknown)."""
    digests = () if spec is None else _digest_rows(input_digests(bundle_dir, spec))
    return pq.Proposal(
        kind="contradiction",
        key_body=pq.contradiction_key(verdict.pair_ids, verdict.merged_absorbed_id),
        producer=PRODUCER_CONTRADICTIONS,
        payload=_canonical(
            {
                "pair_ids": list(verdict.pair_ids),
                "merged_absorbed_id": verdict.merged_absorbed_id,
                "confidence": verdict.confidence,
                "rationale": verdict.rationale,
                "conflicting_claims": list(verdict.conflicting_claims),
            }
        ),
        # Sidecar owner first (`pair_ids[0]`), then the absorbed concept.
        targets=_distinct(
            (*verdict.pair_ids, *(filter(None, [verdict.merged_absorbed_id])))
        ),
        input_digests=digests,
        merged_absorbed_id=verdict.merged_absorbed_id,
    )


def enqueue_contradictions(
    conn: Any,
    outcome: ContradictionsOutcome,
    *,
    input_digests: InputDigestsFor,
    bundle_dir: Path,
    commit_section: CommitSection,
    clock: Callable[[], datetime] | None = None,
) -> ProducerResult:
    return publish(
        conn,
        "contradiction",
        contradiction_proposals(
            outcome, input_digests=input_digests, bundle_dir=bundle_dir
        ),
        complete=contradictions_run_complete(outcome),
        commit_section=commit_section,
        bundle_dir=bundle_dir,
        clock=clock,
    )


# -- identity (duplicate groups, optionally adjudicated) ------------------------


def identity_proposals(
    report: duplicates_service.DuplicatesReport,
    *,
    current_digest: CurrentDigest,
    adjudicated: Sequence[adjudication.AdjudicatedCandidate] = (),
) -> list[pq.Proposal]:
    """One proposal per candidate group the report still offers. An adjudication
    result for the same member set rides in the payload (and so in the digest: a
    changed verdict replaces the row)."""
    verdict_by_members = {
        tuple(sorted(result.candidate.member_ids)): result for result in adjudicated
    }
    proposals: list[pq.Proposal] = []
    for group in report.groups:
        proposal = identity_group_proposal(
            group,
            current_digest=current_digest,
            result=verdict_by_members.get(tuple(sorted(group.member_ids))),
        )
        if proposal is not None:
            proposals.append(proposal)
    return proposals


def identity_group_proposal(
    group: Any,
    *,
    current_digest: CurrentDigest,
    result: adjudication.AdjudicatedCandidate | None = None,
) -> pq.Proposal | None:
    """The proposal for one candidate group, or `None` when a member cannot be
    re-read (a row whose inputs cannot be digested could never be told stale)."""
    digests = _file_digests(current_digest, group.member_ids)
    if digests is None:
        return None
    return pq.Proposal(
        kind="identity",
        key_body=pq.identity_key(group.member_ids),
        producer=PRODUCER_IDENTITY,
        payload=_canonical(
            {
                "member_ids": list(group.member_ids),
                "member_types": list(group.member_types),
                "okf_type": group.okf_type,
                "tier": group.tier.value,
                "trigger": group.trigger,
                "adjudication": None
                if result is None
                else {
                    "verdict": result.verdict.value,
                    "confidence": result.confidence,
                    "rationale": result.rationale,
                },
            }
        ),
        targets=tuple(group.member_ids),
        input_digests=digests,
    )


def enqueue_identity(
    conn: Any,
    report: duplicates_service.DuplicatesReport,
    *,
    current_digest: CurrentDigest,
    bundle_dir: Path,
    commit_section: CommitSection,
    adjudicated: Sequence[adjudication.AdjudicatedCandidate] = (),
    clock: Callable[[], datetime] | None = None,
) -> ProducerResult:
    """A report whose groups were capped carries a truncation notice and so is
    incomplete."""
    return publish(
        conn,
        "identity",
        identity_proposals(
            report, current_digest=current_digest, adjudicated=adjudicated
        ),
        complete=report.truncation_notice is None,
        commit_section=commit_section,
        bundle_dir=bundle_dir,
        clock=clock,
    )


# -- relation types -------------------------------------------------------------


def relations_run_complete(outcome: SuggestRelationsOutcome) -> bool:
    """Complete only when the run typed every candidate it was shown: not
    declined, not an empty offset window, no failure, no bound, no capped or
    windowed candidate list."""
    return (
        outcome.status in ("completed", "no_candidates")
        and outcome.failure is None
        and outcome.deferred_by_bound == 0
        and outcome.next_offset is None
        and outcome.truncation_notice is None
    )


def relation_proposals(
    outcome: SuggestRelationsOutcome, *, current_digest: CurrentDigest
) -> list[pq.Proposal]:
    """One proposal per typed untyped-edge. The row is keyed on the CANDIDATE
    edge (`suggestion.edge`), never on a direction-corrected one; the corrected
    direction is carried in the payload."""
    proposals: list[pq.Proposal] = []
    for suggestion in outcome.results:
        proposal = relation_suggestion_proposal(
            suggestion, current_digest=current_digest
        )
        if proposal is not None:
            proposals.append(proposal)
    return proposals


def relation_suggestion_proposal(
    suggestion: Any, *, current_digest: CurrentDigest
) -> pq.Proposal | None:
    """The proposal for one typed untyped-edge suggestion, or `None` for a
    degraded one or an endpoint that cannot be re-read."""
    if suggestion.suggested_type is None:
        return None
    edge = suggestion.edge
    digests = _file_digests(current_digest, (edge.source_id, edge.target_id))
    if digests is None:
        return None
    effective = suggestion.corrected_edge or edge
    return pq.Proposal(
        kind="relation_type",
        key_body=pq.relation_type_key(edge.source_id, edge.target_id),
        producer=PRODUCER_RELATIONS,
        payload=_canonical(
            {
                "source_id": edge.source_id,
                "target_id": edge.target_id,
                "effective_source_id": effective.source_id,
                "effective_target_id": effective.target_id,
                "suggested_type": suggestion.suggested_type,
                "rationale": suggestion.rationale,
            }
        ),
        targets=(edge.source_id, edge.target_id),
        input_digests=digests,
    )


def enqueue_relations(
    conn: Any,
    outcome: SuggestRelationsOutcome,
    *,
    current_digest: CurrentDigest,
    bundle_dir: Path,
    commit_section: CommitSection,
    clock: Callable[[], datetime] | None = None,
) -> ProducerResult:
    return publish(
        conn,
        "relation_type",
        relation_proposals(outcome, current_digest=current_digest),
        complete=relations_run_complete(outcome),
        commit_section=commit_section,
        bundle_dir=bundle_dir,
        clock=clock,
    )


# -- volatility -----------------------------------------------------------------


def volatility_run_complete(outcome: VolatilityOutcome) -> bool:
    return outcome.failure is None and outcome.deferred_by_bound == 0


def volatility_proposals(outcome: VolatilityOutcome) -> list[pq.Proposal]:
    """One proposal per type whose suggested tier differs from its current
    default. A type is not a concept, so the row has no targets and no input
    digests: the default it was computed against is part of the payload."""
    proposals: list[pq.Proposal] = []
    for suggestion in outcome.results:
        proposal = volatility_suggestion_proposal(suggestion)
        if proposal is not None:
            proposals.append(proposal)
    return proposals


def volatility_suggestion_proposal(suggestion: Any) -> pq.Proposal | None:
    """The proposal for one type's suggested tier, or `None` for a degraded
    suggestion or one equal to the type's current default."""
    if (
        suggestion.suggested_tier is None
        or suggestion.suggested_tier == suggestion.current_default
    ):
        return None
    return pq.Proposal(
        kind="volatility",
        key_body=pq.volatility_key(suggestion.type_name),
        producer=PRODUCER_VOLATILITY,
        payload=_canonical(
            {
                "type_name": suggestion.type_name,
                "current_default": suggestion.current_default,
                "suggested_tier": suggestion.suggested_tier,
                "rationale": suggestion.rationale,
            }
        ),
        targets=(),
    )


def enqueue_volatility(
    conn: Any,
    outcome: VolatilityOutcome,
    *,
    bundle_dir: Path,
    commit_section: CommitSection,
    clock: Callable[[], datetime] | None = None,
) -> ProducerResult:
    return publish(
        conn,
        "volatility",
        volatility_proposals(outcome),
        complete=volatility_run_complete(outcome),
        commit_section=commit_section,
        bundle_dir=bundle_dir,
        clock=clock,
    )


# -- revisions ------------------------------------------------------------------


def revisions_run_complete(
    plan: revisions.RevisionPlan, outcome: revisions.RevisionOutcome
) -> bool:
    """Complete when the vector store was usable, every Decision had a current
    vector, the candidate list was not capped, and judging neither failed nor
    hit a bound."""
    coverage = plan.coverage
    return (
        coverage.store == "ok"
        and not coverage.missing
        and not coverage.stale
        and plan.candidate_plan.total == len(plan.candidate_plan.candidates)
        and outcome.failure is None
        and outcome.deferred_by_bound == 0
    )


def revision_proposals(
    findings: Iterable[revision_findings.RevisionFinding],
) -> list[pq.Proposal]:
    """One proposal per actionable, fresh revision finding
    (`revisions.actionable_revision_findings`)."""
    proposals: list[pq.Proposal] = []
    for finding in findings:
        proposals.append(
            pq.Proposal(
                kind="revision",
                key_body=pq.revision_key(finding.pair_ids),
                producer=PRODUCER_REVISIONS,
                payload=_canonical(
                    {
                        "pair_ids": list(finding.pair_ids),
                        "verdict": finding.verdict,
                        "confidence": finding.confidence,
                        "rationale": finding.rationale,
                        "quotes": list(finding.quotes),
                        "dates": list(finding.dates),
                        "date_states": list(finding.date_states),
                    }
                ),
                targets=tuple(finding.pair_ids),
                input_digests=_digest_rows(finding.input_digests),
            )
        )
    return proposals


def enqueue_revisions(
    conn: Any,
    findings: Iterable[revision_findings.RevisionFinding],
    *,
    complete: bool,
    bundle_dir: Path,
    commit_section: CommitSection,
    clock: Callable[[], datetime] | None = None,
) -> ProducerResult:
    """`complete` comes from `revisions_run_complete(plan, outcome)`."""
    return publish(
        conn,
        "revision",
        revision_proposals(findings),
        complete=complete,
        commit_section=commit_section,
        bundle_dir=bundle_dir,
        clock=clock,
    )
