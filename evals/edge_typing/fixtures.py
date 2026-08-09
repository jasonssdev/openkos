"""Labelled concept pairs for the `suggest_edge_types` harness (issue #508).

CONSTRUCTED, not adjudicated. Every document here was written so that the
rubric in `edge_typing._RELATION_RUBRIC` has exactly one defensible answer:
`part_of` is expected only where the text says a component sits inside a
whole, `related_to` only where the text connects two things without saying
how. That is a real limitation and the README states it -- an organic bundle
would carry ambiguity these pairs deliberately do not.

The point is not to certify accuracy on real material. It is to give a
prompt change something to move AGAINST: a fixed, deterministic set whose
answers are decidable from the rubric alone, so a regression shows up as a
label the rubric itself contradicts rather than as a matter of taste.

Coverage targets the four confusions the rubric names as its reason for
existing (`edge_typing.py`'s `_RELATION_RUBRIC` docstring): `part_of`
against `member_of`, `depends_on` against `part_of`, `caused_by` against
`produced_by`, and `references` against `related_to` -- plus the honest
abstention itself.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConceptDoc:
    """One concept document the harness materializes into a bundle."""

    concept_id: str
    title: str
    body: str


@dataclass(frozen=True)
class LabelledEdge:
    """One SOURCE -> TARGET pair with the type the rubric decides for it."""

    source_id: str
    target_id: str
    expected_type: str
    confusion: str
    """The rubric confusion this pair exists to probe, for the report."""


DOCS: tuple[ConceptDoc, ...] = (
    ConceptDoc(
        "concepts/retry-budget",
        "Retry Budget",
        "The retry budget is one component inside the request scheduler. It "
        "lives in the scheduler's own module and is constructed by the "
        "scheduler at startup; nothing outside the scheduler can hold one.",
    ),
    ConceptDoc(
        "concepts/request-scheduler",
        "Request Scheduler",
        "The request scheduler is a single subsystem that owns queueing, "
        "admission and retry. It is built from several internal components, "
        "one of which is the retry budget.",
    ),
    ConceptDoc(
        "concepts/nightly-backup-job",
        "Nightly Backup Job",
        "The nightly backup job is one of the scheduled maintenance jobs. It "
        "is listed alongside the log-rotation job and the index-vacuum job, "
        "and each of them is registered the same way.",
    ),
    ConceptDoc(
        "concepts/scheduled-maintenance-jobs",
        "Scheduled Maintenance Jobs",
        "Scheduled maintenance jobs is the collection of recurring jobs the "
        "operator can enable. Its members are alike in kind: each is a "
        "periodic task with a cron expression and a retention policy.",
    ),
    ConceptDoc(
        "concepts/report-renderer",
        "Report Renderer",
        "The report renderer needs a working template cache to do its job. "
        "The cache is not part of the renderer and is not built by it -- the "
        "renderer simply cannot produce output when the cache is absent.",
    ),
    ConceptDoc(
        "concepts/template-cache",
        "Template Cache",
        "The template cache is an independent service holding compiled "
        "templates. It is deployed on its own and has no knowledge of which "
        "components read from it.",
    ),
    ConceptDoc(
        "concepts/checkout-outage",
        "Checkout Outage",
        "The checkout outage was a two-hour period in which no order could "
        "be placed. It happened because the payment gateway migration "
        "removed a header the checkout service required.",
    ),
    ConceptDoc(
        "concepts/payment-gateway-migration",
        "Payment Gateway Migration",
        "The payment gateway migration moved traffic to a new provider. It "
        "dropped a legacy header during the cutover, which brought about the "
        "checkout outage the same afternoon.",
    ),
    ConceptDoc(
        "concepts/quarterly-risk-report",
        "Quarterly Risk Report",
        "The quarterly risk report is a document. It was written and "
        "published by the risk committee, which is the body that authors it "
        "each quarter.",
    ),
    ConceptDoc(
        "concepts/risk-committee",
        "Risk Committee",
        "The risk committee is a standing group of five people. Among other "
        "duties it authors the quarterly risk report.",
    ),
    ConceptDoc(
        "concepts/migration-runbook",
        "Migration Runbook",
        "The migration runbook is a set of operator steps. Step four "
        "explicitly cites the rollback policy by name and tells the reader "
        "to consult it before proceeding.",
    ),
    ConceptDoc(
        "concepts/rollback-policy",
        "Rollback Policy",
        "The rollback policy states when a change may be reverted and who "
        "may authorize it. It is maintained independently of any particular "
        "runbook.",
    ),
    ConceptDoc(
        "concepts/onboarding-checklist",
        "Onboarding Checklist",
        "The onboarding checklist covers the first week for a new engineer: "
        "accounts, environment, and a first small change.",
    ),
    ConceptDoc(
        "concepts/aortic-valve",
        "Aortic Valve",
        "The aortic valve forms the outflow gate of the heart. Anatomically "
        "it is situated within that organ and cannot be described apart from "
        "the chamber it seals.",
    ),
    ConceptDoc(
        "concepts/human-heart",
        "Human Heart",
        "The human heart is a single muscular organ. Its interior comprises "
        "chambers and valves, the aortic valve among the structures it "
        "contains.",
    ),
    ConceptDoc(
        "concepts/soprano-line",
        "Soprano Line",
        "The soprano line belongs to the choir's roster of voice parts. It "
        "is enrolled there on the same footing as alto, tenor and bass, each "
        "an entry of equal standing.",
    ),
    ConceptDoc(
        "concepts/voice-parts",
        "Voice Parts",
        "Voice parts is a roster whose entries are peers. Enrollment means "
        "being a part on that roster, and no entry contains any other.",
    ),
    ConceptDoc(
        "concepts/wal-segment",
        "WAL Segment",
        "A WAL segment is one file inside the write-ahead log. The log is a "
        "single ordered structure and the segment is a piece of it; a "
        "segment has no meaning outside its log.",
    ),
    ConceptDoc(
        "concepts/write-ahead-log",
        "Write-Ahead Log",
        "The write-ahead log is one durable structure guaranteeing ordering. "
        "Internally it is divided into segments, each a component of the "
        "same whole.",
    ),
    ConceptDoc(
        "concepts/eu-west-replica",
        "EU West Replica",
        "The EU West replica is one of the read replicas. It sits beside the "
        "US East and AP South replicas, and all of them are provisioned from "
        "the same template.",
    ),
    ConceptDoc(
        "concepts/read-replicas",
        "Read Replicas",
        "Read replicas is the set of standby copies serving read traffic. "
        "Every member is alike in kind: a follower with the same schema and "
        "its own region.",
    ),
    ConceptDoc(
        "concepts/search-api",
        "Search API",
        "The search API requires the ranking service to answer a query. The "
        "ranking service is deployed separately and is not contained in the "
        "API; without it the API returns an error.",
    ),
    ConceptDoc(
        "concepts/ranking-service",
        "Ranking Service",
        "The ranking service scores documents. It runs as its own process "
        "and does not know which callers depend on it.",
    ),
    ConceptDoc(
        "concepts/data-loss-incident",
        "Data Loss Incident",
        "The data loss incident destroyed six hours of writes. It occurred "
        "because the storage upgrade disabled fsync without anyone noticing.",
    ),
    ConceptDoc(
        "concepts/storage-upgrade",
        "Storage Upgrade",
        "The storage upgrade replaced the disk layer. It turned fsync off by "
        "default, which brought about the data loss incident that weekend.",
    ),
    ConceptDoc(
        "concepts/architecture-decision-record",
        "Architecture Decision Record",
        "The architecture decision record is a written document. It was "
        "authored and signed off by the platform team, which produces one "
        "for every significant decision.",
    ),
    ConceptDoc(
        "concepts/platform-team",
        "Platform Team",
        "The platform team is a group of engineers owning shared "
        "infrastructure. Among its outputs it authors architecture decision "
        "records.",
    ),
    ConceptDoc(
        "concepts/deploy-guide",
        "Deploy Guide",
        "The deploy guide walks an operator through a release. Its final "
        "section names the incident severity matrix explicitly and tells the "
        "reader to consult that document when a deploy goes wrong.",
    ),
    ConceptDoc(
        "concepts/incident-severity-matrix",
        "Incident Severity Matrix",
        "The incident severity matrix defines severity levels and the "
        "response each one requires. It stands on its own.",
    ),
    ConceptDoc(
        "concepts/hiring-loop",
        "Hiring Loop",
        "The hiring loop describes the interview stages a candidate goes "
        "through and who runs each one.",
    ),
    ConceptDoc(
        "concepts/documentation-style",
        "Documentation Style",
        "Documentation style covers tone, heading conventions and how much "
        "context a page should assume of its reader.",
    ),
    ConceptDoc(
        "concepts/cost-dashboard",
        "Cost Dashboard",
        "The cost dashboard shows monthly spend per service and highlights "
        "the largest movers week over week.",
    ),
    ConceptDoc(
        "concepts/oncall-rotation",
        "On-call Rotation",
        "The on-call rotation lists who carries the pager each week and how "
        "handover happens between shifts.",
    ),
    ConceptDoc(
        "concepts/incident-review-culture",
        "Incident Review Culture",
        "Incident review culture describes how the team talks about failure "
        "after the fact, and what it expects from a written review.",
    ),
)

EDGES: tuple[LabelledEdge, ...] = (
    LabelledEdge(
        "concepts/retry-budget",
        "concepts/request-scheduler",
        "part_of",
        "part_of vs member_of",
    ),
    LabelledEdge(
        "concepts/nightly-backup-job",
        "concepts/scheduled-maintenance-jobs",
        "member_of",
        "part_of vs member_of",
    ),
    LabelledEdge(
        "concepts/report-renderer",
        "concepts/template-cache",
        "depends_on",
        "depends_on vs part_of",
    ),
    LabelledEdge(
        "concepts/checkout-outage",
        "concepts/payment-gateway-migration",
        "caused_by",
        "caused_by vs produced_by",
    ),
    LabelledEdge(
        "concepts/quarterly-risk-report",
        "concepts/risk-committee",
        "produced_by",
        "caused_by vs produced_by",
    ),
    LabelledEdge(
        "concepts/migration-runbook",
        "concepts/rollback-policy",
        "references",
        "references vs related_to",
    ),
    LabelledEdge(
        "concepts/onboarding-checklist",
        "concepts/incident-review-culture",
        "related_to",
        "the honest abstention",
    ),
    LabelledEdge(
        "concepts/aortic-valve",
        "concepts/human-heart",
        "part_of",
        "part_of vs member_of (held out from the examples)",
    ),
    LabelledEdge(
        "concepts/soprano-line",
        "concepts/voice-parts",
        "member_of",
        "part_of vs member_of (held out from the examples)",
    ),
    LabelledEdge(
        "concepts/wal-segment",
        "concepts/write-ahead-log",
        "part_of",
        "part_of vs member_of",
    ),
    LabelledEdge(
        "concepts/eu-west-replica",
        "concepts/read-replicas",
        "member_of",
        "part_of vs member_of",
    ),
    LabelledEdge(
        "concepts/search-api",
        "concepts/ranking-service",
        "depends_on",
        "depends_on vs part_of",
    ),
    LabelledEdge(
        "concepts/data-loss-incident",
        "concepts/storage-upgrade",
        "caused_by",
        "caused_by vs produced_by",
    ),
    LabelledEdge(
        "concepts/architecture-decision-record",
        "concepts/platform-team",
        "produced_by",
        "caused_by vs produced_by",
    ),
    LabelledEdge(
        "concepts/deploy-guide",
        "concepts/incident-severity-matrix",
        "references",
        "references vs related_to",
    ),
    LabelledEdge(
        "concepts/hiring-loop",
        "concepts/documentation-style",
        "related_to",
        "the honest abstention",
    ),
    LabelledEdge(
        "concepts/cost-dashboard",
        "concepts/oncall-rotation",
        "related_to",
        "the honest abstention",
    ),
)
