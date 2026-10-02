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
- `complementary-description` (#1223): two descriptions of ONE entity that
  state different, compatible facts (two roles of one person). Expected
  `consistent`. The field shape is a merged person whose two bodies read as
  "a participant in the discussion" and "the team member who will test the
  project"; the judge flagged it at 0.95 on difference alone.
- `identical-statement` (#1223): two bodies that assert the SAME limitation
  of one technique in different words. Expected `consistent`. The field
  judge's own rationale began "Both concepts state ..." and still returned
  `contradicts` at 0.95.
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
    # -- complementary-description (#1223) ------------------------------------
    ConceptDoc(
        "people/ana-ruiz-participant",
        "Ana Ruiz",
        "A participant in the conversation discussing knowledge graph "
        "implementation and system performance.",
        relations=(("people/ana-ruiz-tester", "related_to"),),
    ),
    ConceptDoc(
        "people/ana-ruiz-tester",
        "Ana Ruiz",
        "Miembro del equipo que se encargar\u00e1 de probar el proyecto.",
    ),
    ConceptDoc(
        "people/marco-silva-role",
        "Marco Silva",
        "Marco Silva leads the infrastructure team and owns the deployment pipeline.",
        relations=(("people/marco-silva-talk", "related_to"),),
    ),
    ConceptDoc(
        "people/marco-silva-talk",
        "Marco Silva",
        "Marco Silva gave the closing talk at the 2024 platform meetup, "
        "about incident reviews.",
    ),
    # -- identical-statement (#1223) ------------------------------------------
    ConceptDoc(
        "concepts/rag-limitaciones",
        "RAG y sus limitaciones",
        "Los sistemas RAG tienen limitaciones en la trazabilidad: la "
        "respuesta generada no siempre permite identificar qu\u00e9 "
        "fragmento recuperado sustenta cada afirmaci\u00f3n.",
        relations=(("concepts/trazabilidad-sistemas-informacion", "related_to"),),
    ),
    ConceptDoc(
        "concepts/trazabilidad-sistemas-informacion",
        "Trazabilidad en sistemas de informaci\u00f3n",
        "La trazabilidad en sistemas basados en RAG es limitada: no es "
        "posible determinar con certeza qu\u00e9 fragmento recuperado "
        "respalda cada afirmaci\u00f3n de la respuesta generada.",
    ),
    ConceptDoc(
        "concepts/batch-job-idempotency",
        "Batch Job Idempotency",
        "The nightly batch job is not idempotent: running it twice for the "
        "same date duplicates the output rows.",
        relations=(("concepts/nightly-export-caveats", "related_to"),),
    ),
    ConceptDoc(
        "concepts/nightly-export-caveats",
        "Nightly Export Caveats",
        "A caveat of the nightly export: re-running the batch job for a "
        "date that already ran produces duplicate output rows, so it must "
        "never be run twice for one date.",
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
    LabelledPair(
        "people/ana-ruiz-participant",
        "people/ana-ruiz-tester",
        "consistent",
        "complementary-description",
    ),
    LabelledPair(
        "people/marco-silva-role",
        "people/marco-silva-talk",
        "consistent",
        "complementary-description",
    ),
    LabelledPair(
        "concepts/rag-limitaciones",
        "concepts/trazabilidad-sistemas-informacion",
        "consistent",
        "identical-statement",
    ),
    LabelledPair(
        "concepts/batch-job-idempotency",
        "concepts/nightly-export-caveats",
        "consistent",
        "identical-statement",
    ),
)


# --------------------------------------------------------------------------- #
# Merged-content cases (#1223, second attempt)
# --------------------------------------------------------------------------- #
#
# The field failure (#1223) came from the MERGED-CONTENT path: a survivor
# that absorbed a duplicate, whose own ledger pairs `survivor_before` against
# `absorbed_snapshot`. That path builds its prompt with
# `_build_merge_messages` ("MERGE: A absorbed B", no relation line), a
# different user turn from the typed-edge `RELATION:` shape every pair above
# is judged through -- and `run_contradictions_eval._run_once` used to drop
# merged verdicts entirely. So the first #1223 attempt (the four
# compatible-statement pairs above, 0.00 FP over 15 runs) never exercised the
# path the issue was filed on.
#
# Every case is SYNTHETIC text written for this file. None of it comes from a
# private corpus.

MERGED_COMPLEMENTARY = "merged-complementary"
MERGED_IDENTICAL = "merged-identical"
MERGED_LONG = "merged-long"
MERGED_CONTRADICTION = "merged-contradiction"
MERGED_SCOPE_GUIDANCE = "merged-scope-guidance"
MERGED_NARROWER_USE = "merged-narrower-use"
MERGED_COMPATIBLE_PROBES = (
    MERGED_COMPLEMENTARY,
    MERGED_IDENTICAL,
    MERGED_LONG,
    MERGED_SCOPE_GUIDANCE,
    MERGED_NARROWER_USE,
)
"""Probe classes whose expected verdict is `consistent` (the FP classes)."""

MERGED_FIELD_SHAPE_PROBES = (MERGED_SCOPE_GUIDANCE, MERGED_NARROWER_USE)
"""The two classes added after the third #1223 occurrence (0.4.0 E2E): a
definitional sentence paired with (a) a sentence saying where task-specific
guidance BELONGS instead of in the defined thing (`merged-scope-guidance`), or
(b) a sentence naming a narrower use of the same thing
(`merged-narrower-use`). The first two #1223 attempts had no case of either
shape, so a 0-of-120 baseline there said nothing about them. Reported apart as
the pre-registered primary metric."""


@dataclass(frozen=True)
class MergedCase:
    """One survivor that absorbed one duplicate, judged via its ledger."""

    survivor_id: str
    survivor_title: str
    before_body: str
    absorbed_id: str
    absorbed_title: str
    absorbed_body: str
    expected: str
    probe: str
    prior_absorbed: tuple[tuple[str, str], ...] = ()
    """`(absorbed_id, body)` for earlier merges already stacked into the
    survivor's body as `## Merged content (<id>)` sections -- the "stacked
    bodies after a merge" shape. `_own_body_before_merge` cuts them off, and
    carrying them proves the cut holds on the judged prompt."""


def _long_notes(prefix: str, topics: tuple[str, ...], per_topic: int) -> str:
    """Deterministic long Spanish prose: many distinct, mutually compatible
    statements, each about its OWN named module so two sides built from
    disjoint `topics` never state different values for one property."""
    paragraphs: list[str] = []
    for index, topic in enumerate(topics):
        sentences = [
            f"El módulo {topic} de {prefix} registra sus eventos en un archivo "
            f"propio y rota ese archivo cada {7 + index} días.",
            f"El equipo que mantiene {topic} revisa sus alertas durante la "
            f"reunión semanal y anota los pendientes en la lista compartida.",
            f"La documentación de {topic} describe {per_topic} casos de uso "
            f"habituales, cada uno con su ejemplo de configuración.",
            f"Para trabajar con {topic} conviene tener instalada la versión "
            f"{2 + index % 3} del cliente de línea de comandos.",
            f"Los errores de {topic} se clasifican por gravedad y se "
            f"atienden en orden, empezando por los que bloquean a otros "
            f"equipos.",
            f"Cuando {topic} cambia de comportamiento, se avisa en el canal "
            f"del proyecto con un resumen de los cambios y su motivo.",
        ]
        paragraphs.append(" ".join(sentences))
    return "\n\n".join(paragraphs)


_TOPICS_BEFORE = (
    "Ingesta",
    "Catálogo",
    "Búsqueda",
    "Alertas",
    "Reportes",
    "Facturación",
    "Permisos",
    "Auditoría",
    "Exportación",
    "Notificaciones",
    "Sincronización",
    "Respaldo",
)
_TOPICS_ABSORBED = (
    "Importación",
    "Etiquetado",
    "Resumen",
    "Calendario",
    "Traducción",
    "Archivo",
    "Métricas",
    "Plantillas",
    "Versionado",
    "Mensajería",
    "Colaboración",
    "Recuperación",
)

MERGED_CASES: tuple[MergedCase, ...] = (
    # -- merged-complementary: two roles of ONE person, Spanish --------------
    MergedCase(
        "people/lucia-paredes",
        "Lucía Paredes",
        "Lucía Paredes participó en la discusión sobre el diseño del módulo "
        "de reportes y propuso guardar en memoria las consultas más "
        "frecuentes.",
        "people/lucia-paredes-2",
        "Lucía Paredes",
        "Lucía Paredes es la integrante del equipo que probará el módulo de "
        "reportes antes del lanzamiento.",
        "consistent",
        MERGED_COMPLEMENTARY,
    ),
    MergedCase(
        "people/tomas-herrera",
        "Tomás Herrera",
        "Tomás Herrera presentó en la reunión la propuesta para migrar la "
        "base de datos de inventario.",
        "people/tomas-herrera-2",
        "Tomás Herrera",
        "Tomás Herrera trabaja en el equipo de plataforma y atiende las "
        "guardias de los fines de semana.",
        "consistent",
        MERGED_COMPLEMENTARY,
    ),
    MergedCase(
        "concepts/servicio-notificaciones",
        "Servicio de notificaciones",
        "El servicio de notificaciones envía correos electrónicos y "
        "mensajes push a las personas suscritas.",
        "concepts/servicio-notificaciones-2",
        "Servicio de notificaciones",
        "El servicio de notificaciones se despliega en tres regiones y "
        "toma los mensajes pendientes de una cola.",
        "consistent",
        MERGED_COMPLEMENTARY,
    ),
    MergedCase(
        "people/priya-nair",
        "Priya Nair",
        "Priya Nair joined the review call and asked about the rollout "
        "schedule for the billing changes.",
        "people/priya-nair-2",
        "Priya Nair",
        "Priya Nair is the engineer who will run the load tests for the "
        "billing changes next month.",
        "consistent",
        MERGED_COMPLEMENTARY,
    ),
    # -- merged-identical: the SAME limitation, in different words -----------
    MergedCase(
        "concepts/recuperacion-documentos",
        "Recuperación de documentos",
        "Los sistemas de recuperación de documentos tienen una limitación: "
        "los resultados no siempre indican la fuente exacta de cada dato.",
        "concepts/recuperacion-documentos-2",
        "Recuperación de documentos",
        "Una limitación conocida de estos sistemas es que no se puede saber "
        "con certeza de qué fuente proviene cada dato del resultado.",
        "consistent",
        MERGED_IDENTICAL,
    ),
    MergedCase(
        "concepts/trabajo-nocturno",
        "Trabajo nocturno de exportación",
        "El trabajo nocturno de exportación no es idempotente: ejecutarlo "
        "dos veces para la misma fecha duplica las filas de salida.",
        "concepts/trabajo-nocturno-2",
        "Trabajo nocturno de exportación",
        "Si el trabajo de exportación se vuelve a ejecutar para una fecha "
        "que ya se procesó, las filas de salida quedan duplicadas.",
        "consistent",
        MERGED_IDENTICAL,
    ),
    # -- merged-long: bodies sized toward the 12288-token default window -----
    MergedCase(
        "concepts/plataforma-datos",
        "Plataforma de datos",
        "La plataforma de datos reúne varios módulos.\n\n"
        + _long_notes("la plataforma", _TOPICS_BEFORE, 4),
        "concepts/plataforma-datos-2",
        "Plataforma de datos",
        "La plataforma de datos también ofrece módulos de apoyo.\n\n"
        + _long_notes("la plataforma", _TOPICS_ABSORBED, 5),
        "consistent",
        MERGED_LONG,
        prior_absorbed=(
            (
                "concepts/plataforma-datos-0",
                "La plataforma de datos se mantiene en un repositorio único "
                "y se publica con una etiqueta de versión cada mes.",
            ),
        ),
    ),
    MergedCase(
        "concepts/plataforma-analitica",
        "Plataforma de analítica",
        "La plataforma de analítica reúne varios módulos.\n\n"
        + _long_notes("la analítica", _TOPICS_BEFORE * 2, 3),
        "concepts/plataforma-analitica-2",
        "Plataforma de analítica",
        "La plataforma de analítica también ofrece módulos de apoyo.\n\n"
        + _long_notes("la analítica", _TOPICS_ABSORBED * 2, 6),
        "consistent",
        MERGED_LONG,
    ),
    # -- merged-contradiction: real conflicts the merge path must keep -------
    MergedCase(
        "concepts/proyecto-aurora",
        "Proyecto Aurora",
        "El proyecto Aurora se lanzó en marzo de 2024 con tres módulos.",
        "concepts/proyecto-aurora-2",
        "Proyecto Aurora",
        "El proyecto Aurora se lanzó en septiembre de 2025; antes de esa "
        "fecha no existía ninguna versión pública.",
        "contradicts",
        MERGED_CONTRADICTION,
    ),
    MergedCase(
        "concepts/compresion-lecturas",
        "Compresión de lecturas",
        "La compresión redujo la latencia de lectura de forma notable y se "
        "considera un éxito del trimestre.",
        "concepts/compresion-lecturas-2",
        "Compresión de lecturas",
        "La compresión aumentó la latencia de lectura de forma notable y se "
        "considera un fracaso del trimestre.",
        "contradicts",
        MERGED_CONTRADICTION,
    ),
    # -- merged-scope-guidance: a definition vs "this belongs elsewhere" ------
    MergedCase(
        "concepts/claude-md",
        "CLAUDE.md",
        "CLAUDE.md is a Markdown file used in Claude Code to provide "
        "persistent memory and context for a project.",
        "concepts/claude-md-3",
        "CLAUDE.md",
        "A skill is loaded only when relevant, so long or task-specific "
        "guidance belongs in a skill rather than in CLAUDE.md.",
        "consistent",
        MERGED_SCOPE_GUIDANCE,
    ),
    MergedCase(
        "concepts/makefile",
        "Makefile",
        "A Makefile is a file that lists build targets and the commands "
        "that produce them.",
        "concepts/makefile-2",
        "Makefile",
        "Long shell logic belongs in a separate script that the Makefile "
        "calls, rather than inside the Makefile itself.",
        "consistent",
        MERGED_SCOPE_GUIDANCE,
    ),
    MergedCase(
        "concepts/archivo-env",
        "Archivo .env",
        "El archivo .env guarda las variables de entorno de un proyecto "
        "para el entorno local de desarrollo.",
        "concepts/archivo-env-2",
        "Archivo .env",
        "Los secretos de producción se guardan en un gestor de secretos, "
        "no en el archivo .env.",
        "consistent",
        MERGED_SCOPE_GUIDANCE,
    ),
    MergedCase(
        "concepts/readme",
        "README",
        "The README is the first file a visitor reads; it explains what the "
        "project does and how to install it.",
        "concepts/readme-2",
        "README",
        "Detailed API reference belongs on the documentation site, so the "
        "README only links to it.",
        "consistent",
        MERGED_SCOPE_GUIDANCE,
    ),
    MergedCase(
        "concepts/changelog",
        "Changelog",
        "Un changelog registra los cambios visibles de cada versión de un producto.",
        "concepts/changelog-3",
        "Changelog",
        "Las decisiones de diseño extensas se documentan en un registro de "
        "decisiones y no en el changelog.",
        "consistent",
        MERGED_SCOPE_GUIDANCE,
        prior_absorbed=(
            (
                "concepts/changelog-2",
                "El changelog se organiza por versión, con la más reciente "
                "al principio.",
            ),
        ),
    ),
    # -- merged-narrower-use: a definition vs a narrower use of the same thing
    MergedCase(
        "entities/claude-md",
        "CLAUDE.md",
        "A Markdown file used in Claude Code to provide persistent memory "
        "and context for a project.",
        "entities/claude-md-2",
        "CLAUDE.md",
        "A file where Claude saves solutions to problems.",
        "consistent",
        MERGED_NARROWER_USE,
    ),
    MergedCase(
        "concepts/cache",
        "Cache",
        "A cache stores recently computed results so that later requests "
        "are answered faster.",
        "concepts/cache-2",
        "Cache",
        "The cache is where the thumbnail service keeps its resized images.",
        "consistent",
        MERGED_NARROWER_USE,
    ),
    MergedCase(
        "concepts/bitacora",
        "Bitácora del sistema",
        "La bitácora del sistema registra eventos para facilitar el "
        "diagnóstico de fallos.",
        "concepts/bitacora-2",
        "Bitácora del sistema",
        "La bitácora es el lugar donde el servicio de pagos anota los "
        "reintentos fallidos.",
        "consistent",
        MERGED_NARROWER_USE,
    ),
    MergedCase(
        "concepts/cola-trabajo",
        "Cola de trabajo",
        "A work queue holds items until a worker is free to take them.",
        "concepts/cola-trabajo-2",
        "Cola de trabajo",
        "The queue is where the email service parks messages that are "
        "waiting to be sent.",
        "consistent",
        MERGED_NARROWER_USE,
    ),
    MergedCase(
        "concepts/tablero-kanban",
        "Tablero kanban",
        "Un tablero kanban muestra el estado de las tareas de un equipo en columnas.",
        "concepts/tablero-kanban-2",
        "Tablero kanban",
        "El tablero es donde el equipo de soporte coloca las solicitudes "
        "que están por atender.",
        "consistent",
        MERGED_NARROWER_USE,
    ),
    # -- merged-contradiction guards in the SAME two shapes ------------------
    MergedCase(
        "concepts/makefile-logic",
        "Makefile logic",
        "Long shell logic belongs in a separate script that the Makefile "
        "calls, never inside the Makefile itself.",
        "concepts/makefile-logic-2",
        "Makefile logic",
        "Long shell logic belongs inside the Makefile recipes; separate "
        "scripts are not used for it.",
        "contradicts",
        MERGED_CONTRADICTION,
    ),
    MergedCase(
        "concepts/cache-lectura",
        "Caché de lectura",
        "La caché es de solo lectura: el servicio de miniaturas nunca escribe en ella.",
        "concepts/cache-lectura-2",
        "Caché de lectura",
        "El servicio de miniaturas escribe en la caché las imágenes "
        "redimensionadas cada vez que procesa una.",
        "contradicts",
        MERGED_CONTRADICTION,
    ),
)
