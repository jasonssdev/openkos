"""Constructed fixture pairs for the contradiction-judge harness (#558).

Every document is written so its labelled pair has exactly ONE defensible
verdict -- read accuracy as rubric-consistency, not field accuracy (the same
philosophy as `evals/edge_typing/fixtures.py`, and the same warning: an
organic bundle carries ambiguity these pairs deliberately do not).

Four classes, keyed by `LabelledPair.probe`:

- `factual-contradiction`: two incompatible assertions about the SAME
  subject and the SAME property (a date, a number, a status, a cause).
  Expected `contradicts` -- these are the true positives the fix must keep.
- `antonym`: two concepts DEFINED in opposition to each other --
  complementary types in one taxonomy. Their definitions are opposite by
  design; they make no incompatible claim about any shared fact. Expected
  `consistent`. This is the class issue #558 is about: the field run judged
  two of these `contradicts` at confidence 1.00. The bodies deliberately do
  NOT self-describe as "complementary" or "the opposite strategy" -- the
  first fixture draft did, and the disarming phrase handed the judge the
  verdict (baseline antonym FP rate 0.07); organic corpora define each side
  in opposition without that meta-commentary, which is the case that failed
  in the field.
- `plain-consistent`: related concepts with no opposition at all, the
  everyday case. Expected `consistent`.
- `definitional-contradiction`: a real same-subject/same-property conflict
  WRAPPED in definitional prose, so a judge that learns "definitional
  language means consistent" from the antonym rule gets caught. Expected
  `contradicts`.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ConceptDoc:
    """One minimal OKF concept document the harness materializes."""

    concept_id: str
    title: str
    body: str
    relations: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    """`(target_id, relation_type)` frontmatter rows -- these create the
    typed edges `find_contradictions` seeds its candidate pairs from."""


@dataclass(frozen=True)
class LabelledPair:
    """One candidate pair with the verdict the fixture construction fixes."""

    source_id: str
    target_id: str
    expected: str
    """`"contradicts"` or `"consistent"` -- the one defensible verdict."""
    probe: str
    """Which failure class this pair exists to probe (see module docstring)."""


DOCS: tuple[ConceptDoc, ...] = (
    # -- factual-contradiction ------------------------------------------------
    ConceptDoc(
        "concepts/okp-standard-history",
        "OKP Standard History",
        "The Open Knowledge Protocol standard was first published in 2019 by "
        "the consortium's working group. That first publication already "
        "included the bundle format and the concept schema.",
        relations=(("concepts/okp-standard-overview", "related_to"),),
    ),
    ConceptDoc(
        "concepts/okp-standard-overview",
        "OKP Standard Overview",
        "The Open Knowledge Protocol standard was first published in 2021. "
        "Before 2021 no version of the standard existed in public form; "
        "earlier drafts circulated privately inside the consortium only.",
    ),
    ConceptDoc(
        "concepts/free-tier-limits",
        "Free Tier Limits",
        "The free tier allows up to 5 projects per account. This limit is "
        "enforced at project creation time and has not changed since launch.",
        relations=(("concepts/free-tier-billing", "related_to"),),
    ),
    ConceptDoc(
        "concepts/free-tier-billing",
        "Free Tier Billing",
        "Billing for the free tier is simple because the free tier allows "
        "at most 3 projects per account; the fourth project creation always "
        "requires a paid plan.",
    ),
    ConceptDoc(
        "concepts/legacy-exporter-status",
        "Legacy Exporter Status",
        "The legacy CSV exporter was removed in version 4.0. Any pipeline "
        "still calling it fails at startup with a removed-feature error "
        "after upgrading.",
        relations=(("concepts/exporter-migration", "related_to"),),
    ),
    ConceptDoc(
        "concepts/exporter-migration",
        "Exporter Migration",
        "Migration to the new exporter is optional: the legacy CSV exporter "
        "remains available and fully supported in version 4.0, and there is "
        "no announced date for its removal.",
    ),
    ConceptDoc(
        "events/march-outage-cause",
        "March Outage Cause",
        "The March outage was caused by a DNS misconfiguration pushed during "
        "a routine zone update. Rolling back the zone file restored service.",
        relations=(("events/march-outage-review", "related_to"),),
    ),
    ConceptDoc(
        "events/march-outage-review",
        "March Outage Review",
        "The post-incident review concluded the March outage was caused by "
        "an expired TLS certificate on the API gateway. DNS was investigated "
        "and explicitly ruled out as a contributing factor.",
    ),
    # -- antonym (complementary types in one taxonomy) ------------------------
    ConceptDoc(
        "concepts/personalized-recommendation",
        "Personalized Recommendation",
        "Personalized recommendation tailors suggestions to an individual "
        "user, using that user's own interaction history. The quality of "
        "its output depends on how much history the user has accumulated.",
        relations=(("concepts/non-personalized-recommendation", "related_to"),),
    ),
    ConceptDoc(
        "concepts/non-personalized-recommendation",
        "Non-Personalized Recommendation",
        "Non-personalized recommendation suggests the same items to every "
        "user -- popularity charts, editorial picks -- using no individual "
        "history at all. It works identically for a first-time anonymous "
        "visitor and a long-time account holder.",
    ),
    ConceptDoc(
        "concepts/synchronous-replication",
        "Synchronous Replication",
        "Synchronous replication acknowledges a write only after every "
        "replica has confirmed it, trading latency for zero data loss on "
        "failover.",
        relations=(("concepts/asynchronous-replication", "related_to"),),
    ),
    ConceptDoc(
        "concepts/asynchronous-replication",
        "Asynchronous Replication",
        "Asynchronous replication acknowledges a write as soon as the "
        "primary has it, trading a bounded replication lag for low "
        "latency. A failover can lose the writes still in flight to the "
        "replicas.",
    ),
    ConceptDoc(
        "concepts/allowlist-filtering",
        "Allowlist Filtering",
        "Allowlist filtering denies everything by default and admits only "
        "the entries explicitly listed. Nothing runs unless someone "
        "approved it first.",
        relations=(("concepts/denylist-filtering", "related_to"),),
    ),
    ConceptDoc(
        "concepts/denylist-filtering",
        "Denylist Filtering",
        "Denylist filtering admits everything by default and blocks only "
        "the entries explicitly listed. Anything not yet on the list runs "
        "without review.",
    ),
    ConceptDoc(
        "concepts/optimistic-locking",
        "Optimistic Locking",
        "Optimistic locking lets every transaction proceed without taking "
        "locks and validates at commit time, aborting on conflict. It "
        "performs best when conflicts are rare.",
        relations=(("concepts/pessimistic-locking", "related_to"),),
    ),
    ConceptDoc(
        "concepts/pessimistic-locking",
        "Pessimistic Locking",
        "Pessimistic locking takes locks up front so a conflicting "
        "transaction waits instead of aborting. It performs best when "
        "conflicts are common.",
    ),
    ConceptDoc(
        "concepts/supervised-learning",
        "Supervised Learning",
        "Supervised learning trains on labelled examples: each training "
        "input carries the answer the model should produce. Classification "
        "and regression are its canonical tasks.",
        relations=(("concepts/unsupervised-learning", "related_to"),),
    ),
    ConceptDoc(
        "concepts/unsupervised-learning",
        "Unsupervised Learning",
        "Unsupervised learning trains on unlabelled data, discovering "
        "structure -- clusters, densities, embeddings -- without target "
        "answers ever being provided.",
    ),
    # -- plain-consistent -----------------------------------------------------
    ConceptDoc(
        "concepts/retry-budget",
        "Retry Budget",
        "The retry budget bounds how many retries the request scheduler may "
        "issue in a sliding window, protecting downstream services from "
        "retry storms.",
        relations=(("concepts/request-scheduler", "part_of"),),
    ),
    ConceptDoc(
        "concepts/request-scheduler",
        "Request Scheduler",
        "The request scheduler owns dispatch order, deadlines, and the "
        "retry budget for outbound requests. It is constructed once at "
        "process startup.",
    ),
    ConceptDoc(
        "concepts/bundle-format",
        "Bundle Format",
        "A bundle is a directory of markdown concept documents with YAML "
        "frontmatter. Every derived index can be rebuilt from the bundle "
        "alone.",
        relations=(("concepts/concept-document", "related_to"),),
    ),
    ConceptDoc(
        "concepts/concept-document",
        "Concept Document",
        "A concept document is one markdown file inside a bundle: YAML "
        "frontmatter carrying type, title, and relations, followed by a "
        "prose body.",
    ),
    # -- definitional-contradiction -------------------------------------------
    ConceptDoc(
        "concepts/client-default-timeout",
        "Client Default Timeout",
        "By definition of the client's contract, requests have no timeout "
        "unless the caller sets one: the default is to wait indefinitely "
        "for a response.",
        relations=(("concepts/client-timeout-behavior", "related_to"),),
    ),
    ConceptDoc(
        "concepts/client-timeout-behavior",
        "Client Timeout Behavior",
        "The client's contract defines a default timeout of 30 seconds: any "
        "request with no explicit timeout set is aborted after 30 seconds "
        "with a timeout error.",
    ),
)


PAIRS: tuple[LabelledPair, ...] = (
    LabelledPair(
        "concepts/okp-standard-history",
        "concepts/okp-standard-overview",
        "contradicts",
        "factual-contradiction",
    ),
    LabelledPair(
        "concepts/free-tier-limits",
        "concepts/free-tier-billing",
        "contradicts",
        "factual-contradiction",
    ),
    LabelledPair(
        "concepts/legacy-exporter-status",
        "concepts/exporter-migration",
        "contradicts",
        "factual-contradiction",
    ),
    LabelledPair(
        "events/march-outage-cause",
        "events/march-outage-review",
        "contradicts",
        "factual-contradiction",
    ),
    LabelledPair(
        "concepts/personalized-recommendation",
        "concepts/non-personalized-recommendation",
        "consistent",
        "antonym",
    ),
    LabelledPair(
        "concepts/synchronous-replication",
        "concepts/asynchronous-replication",
        "consistent",
        "antonym",
    ),
    LabelledPair(
        "concepts/allowlist-filtering",
        "concepts/denylist-filtering",
        "consistent",
        "antonym",
    ),
    LabelledPair(
        "concepts/optimistic-locking",
        "concepts/pessimistic-locking",
        "consistent",
        "antonym",
    ),
    LabelledPair(
        "concepts/supervised-learning",
        "concepts/unsupervised-learning",
        "consistent",
        "antonym",
    ),
    LabelledPair(
        "concepts/retry-budget",
        "concepts/request-scheduler",
        "consistent",
        "plain-consistent",
    ),
    LabelledPair(
        "concepts/bundle-format",
        "concepts/concept-document",
        "consistent",
        "plain-consistent",
    ),
    LabelledPair(
        "concepts/client-default-timeout",
        "concepts/client-timeout-behavior",
        "contradicts",
        "definitional-contradiction",
    ),
)
