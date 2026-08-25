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
- `benefit-limitation` (#870): one concept describes what a technique is
  FOR, the other a limitation it has -- claims about DIFFERENT properties
  of one subject. Expected `consistent`. This is the class the 0.2.9 E2E
  reported judged `contradicts` at 0.95: "the first presents RAG as
  improving X while the second presents it as limited in Y" is most honest
  descriptions of any technique. The first pair mirrors the wild one
  exactly, down to the benefit body already integrating the limitation in
  its own prose ("Sin embargo, ...") -- the judge flagged a tension one
  member resolves internally. Per the fixture trap above, no body
  self-describes the pair as complementary or non-contradictory.
- `evaluative-contradiction` (#870): opposite claims about the SAME
  measured aspect of one technique, phrased evaluatively. Expected
  `contradicts`. The guard the new class needs: a benefit/limitation
  carve-out must not wash out real conflicts that arrive dressed as
  evaluations.
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
    # -- benefit-limitation (different aspects of one technique) --------------
    ConceptDoc(
        "concepts/generacion-aumentada-por-recuperacion",
        "Generación Aumentada por Recuperación (RAG)",
        "La generación aumentada por recuperación (RAG) es una técnica que "
        "mejora la extracción de decisiones a partir de reuniones: recupera "
        "los fragmentos relevantes del corpus y los entrega al modelo como "
        "contexto, lo que reduce las respuestas inventadas. Sin embargo, se "
        "discute su limitación en cuanto a la pérdida de trazabilidad del "
        "origen de cada afirmación una vez fusionado el contexto.",
        relations=(("concepts/trazabilidad-en-sistemas-rag", "related_to"),),
    ),
    ConceptDoc(
        "concepts/trazabilidad-en-sistemas-rag",
        "Trazabilidad en Sistemas RAG",
        "Los sistemas RAG presentan limitaciones de trazabilidad: cuando "
        "varios fragmentos recuperados se fusionan en un solo contexto, la "
        "respuesta final no conserva qué afirmación proviene de qué "
        "fragmento, y reconstruir esa procedencia exige instrumentación "
        "adicional fuera del propio sistema.",
    ),
    ConceptDoc(
        "concepts/caching-layer",
        "Caching Layer",
        "The caching layer cuts read latency by an order of magnitude: hot "
        "keys are served from memory without touching the primary store, "
        "and page loads that depend on them stop being IO-bound.",
        relations=(("concepts/cache-invalidation", "related_to"),),
    ),
    ConceptDoc(
        "concepts/cache-invalidation",
        "Cache Invalidation",
        "Cache invalidation is where the caching layer falls short: an "
        "entry can outlive the data it copies, so a write that succeeds "
        "against the primary store may keep being answered with the stale "
        "value until the entry expires or is explicitly evicted.",
    ),
    ConceptDoc(
        "concepts/microservices-autonomy",
        "Microservices Autonomy",
        "A microservice architecture lets each team deploy independently: "
        "one service can release, roll back, or scale without coordinating "
        "a shared release train, which shortens the path from commit to "
        "production.",
        relations=(("concepts/microservices-operational-load", "related_to"),),
    ),
    ConceptDoc(
        "concepts/microservices-operational-load",
        "Microservices Operational Load",
        "Operating a microservice architecture is expensive: every service "
        "needs its own deployment pipeline, monitoring, and on-call story, "
        "and a single user request may cross a dozen services, so "
        "debugging requires distributed tracing that a monolith never "
        "needed.",
    ),
    ConceptDoc(
        "concepts/secondary-indexes-reads",
        "Secondary Indexes for Reads",
        "Secondary indexes make selective reads fast: a query that filters "
        "on an indexed column stops scanning the table and resolves "
        "through the index in logarithmic time.",
        relations=(("concepts/index-write-amplification", "related_to"),),
    ),
    ConceptDoc(
        "concepts/index-write-amplification",
        "Index Write Amplification",
        "Each secondary index amplifies writes: every insert or update "
        "must also update every index that covers the touched columns, so "
        "a table with many indexes pays for them on every single write.",
    ),
    # -- evaluative-contradiction (same aspect, opposite claims) --------------
    ConceptDoc(
        "concepts/compression-benchmark-result",
        "Compression Benchmark Result",
        "Enabling response compression improved the API benchmark: average "
        "query latency dropped from 120ms to 80ms with compression on, "
        "measured on the same workload and hardware.",
        relations=(("concepts/compression-latency-review", "related_to"),),
    ),
    ConceptDoc(
        "concepts/compression-latency-review",
        "Compression Latency Review",
        "The review of the API benchmark found that enabling response "
        "compression hurt latency: average query latency rose from 80ms "
        "to 120ms with compression on, on the same workload and hardware.",
    ),
    ConceptDoc(
        "concepts/event-bus-rollout-outcome",
        "Event Bus Rollout Outcome",
        "After the event bus rollout, deployment failures fell by half "
        "over the second quarter compared with the first.",
        relations=(("concepts/event-bus-rollout-retrospective", "related_to"),),
    ),
    ConceptDoc(
        "concepts/event-bus-rollout-retrospective",
        "Event Bus Rollout Retrospective",
        "The retrospective recorded that deployment failures doubled over "
        "the second quarter following the event bus rollout, compared "
        "with the first.",
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
    LabelledPair(
        "concepts/generacion-aumentada-por-recuperacion",
        "concepts/trazabilidad-en-sistemas-rag",
        "consistent",
        "benefit-limitation",
    ),
    LabelledPair(
        "concepts/caching-layer",
        "concepts/cache-invalidation",
        "consistent",
        "benefit-limitation",
    ),
    LabelledPair(
        "concepts/microservices-autonomy",
        "concepts/microservices-operational-load",
        "consistent",
        "benefit-limitation",
    ),
    LabelledPair(
        "concepts/secondary-indexes-reads",
        "concepts/index-write-amplification",
        "consistent",
        "benefit-limitation",
    ),
    LabelledPair(
        "concepts/compression-benchmark-result",
        "concepts/compression-latency-review",
        "contradicts",
        "evaluative-contradiction",
    ),
    LabelledPair(
        "concepts/event-bus-rollout-outcome",
        "concepts/event-bus-rollout-retrospective",
        "contradicts",
        "evaluative-contradiction",
    ),
)
