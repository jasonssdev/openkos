"""Labelled candidate pairs for the entity-resolution adjudicator (#796).

Nine probe classes, chosen so the harness can see BOTH directions of the
change under test. A rule that makes identical Event titles read as a
recurring series buys its precision somewhere, and `event-same` is where it
would be paid for.

- `recurrence` -- two Events sharing one title that are two OCCURRENCES of a
  standing meeting. Expected `different`. This is the class #796 reports:
  the shipped judge answered `same` and its own rationale said "the event
  appears to be a continuation or follow-up of the same meeting".
- `event-same` -- two Events sharing one title that ARE one meeting recorded
  twice, from two sources. Expected `same`. The regime any Event-specific
  rule must not break. Without this class a treatment that answered
  `different` to every Event pair would score perfectly.
- `person-same` -- two `Person` documents with the identical name. Expected
  `same`. The control the issue names explicitly: title identity IS strong
  evidence for a Person, and the shipped judge already gets it right.
- `alias-same` -- one non-Event entity under two names. Expected `same`.
  The general recall the fix must not cost.
- `part-whole` -- a component and its whole. Expected `different`. The
  exclusion the shipped prompt already states, carried here so a rewrite
  cannot quietly drop it.
- `asym-recurrence` (#869) -- two occurrences of a standing meeting where
  only ONE member carries a date and an attendee list, and the detailed
  member ELABORATES the sparse member's stated purpose. Expected
  `different`. This is the shape the wild pair #869 reports: the rubric's
  own asymmetric branch ("if one carries such a signal and the other is
  silent, their subject matter must substantively overlap") was answered
  by ASSERTING overlap the members do not carry -- shared purpose read as
  shared substance, when the concrete specifics (agendas, next steps) are
  disjoint. The plain `recurrence` class does not cover it: its one
  asymmetric pair keeps both bodies short and topically disjoint, so
  nothing in it invites the richer-elaborates-poorer misreading.
- `asym-same` (#869) -- the same detail asymmetry, but the members ARE one
  meeting: the sparse body restates a concrete fact only that meeting
  could carry (the specific decision or deliverable the detailed body
  records), just without a date or attendee list of its own. Expected
  `same`. The guard the new class needs: without it, a rubric tweak that
  answered `different` whenever detail is asymmetric would score
  perfectly on `asym-recurrence` while costing every sparse-but-genuine
  duplicate.

- `aspect-of` (#910) -- `X` versus `«aspect» of X`: the part-whole
  exclusion the rubric already states, carried in the exact TITLE shape
  the wild run answered inconsistently (`same` to 'components of X',
  `different` to 'storage in X', against one anchor). Expected
  `different`. Three aspect nouns, three separate anchors, so the class
  measures the shape rather than one noun.
- `transitivity` (#910) -- one Project anchor against three Events that
  are three dated occurrences of one series, all six pairs expected
  `different`. The anchor is deliberately SHARED across its three pairs
  (see `_TRANSITIVITY_ANCHOR`); the runner additionally scores every
  C(4,3) verdict triangle for the 2-SAME-1-DIFFERENT pattern, which is
  the wild inconsistency itself: `same` twice and `different` once over
  three members cannot all be true at once.

Labels are CONSTRUCTED, not adjudicated: each pair is written to be
unambiguous under the rubric the prompt states, so a wrong verdict is a
rubric-consistency failure rather than a disputed judgment call. Read the
accuracy number that way.

The bodies are deliberately short. Adjudication sends every member's FULL
body, so a realistic transcript would make each call slow enough to change
what the harness can afford to run, and none of the signals under test
(a date, an attendee list, a named sprint) need length to be present.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class FixtureDoc:
    """One document to materialize into the probe bundle."""

    concept_id: str
    """Bundle-relative path without `.md` -- the identity adjudication uses."""
    okf_type: str
    title: str
    body: str
    type_alternative: str | None = None
    """The runner-up type the extractor would have recorded (#804), or
    `None` -- the common case. Set on the `transitivity` Events (#910) so
    the production cross-type bridge (`candidates._bridged_cross_type_
    pairs`) nominates each Event-anchor pair exactly as it did on the wild
    run: the harness never hand-builds a `CandidateGroup`, so a cross-type
    pair must earn its nomination the way a real bundle's would."""


@dataclass(frozen=True)
class LabelledPair:
    """Two documents the candidate tiers will group, and the verdict the
    rubric says they deserve."""

    left: FixtureDoc
    right: FixtureDoc
    probe: str
    """One of the five classes in the module docstring."""
    expected: str
    """`same`, `different`, or `uncertain` -- the `Verdict` value."""
    note: str
    """What makes the answer knowable from the two bodies alone. If this is
    empty the pair does not belong here: the model would be guessing, and a
    guess scored as a verdict is how a harness invents a result."""


# --------------------------------------------------------------------------- #
# recurrence -- identical Event titles, different occurrences
# --------------------------------------------------------------------------- #

_RECURRENCE: Final[tuple[LabelledPair, ...]] = (
    LabelledPair(
        left=FixtureDoc(
            "events/comite-evaluacion-coordinacion",
            "Event",
            "Comité de Evaluación (coordinación)",
            "Reunión de coordinación del comité. Se trabajó la narrativa "
            "visual del proyecto, la colaboración abierta con el grupo de "
            "documentación, y la revisión del compilador de conocimiento. "
            "Quedó pendiente definir quién redacta la guía de estilo.",
        ),
        right=FixtureDoc(
            "events/comite-evaluacion-coordinacion-2",
            "Event",
            "Comité de Evaluación (coordinación)",
            "ago 18, 2026. Reunión de coordinación del comité con Elena "
            "Varga, Rubén Castaño y Nadia Iqbal. Se discutió la metodología "
            "experimental cuantitativa, los límites de hardware disponibles "
            "y el estado del arte. Próximo paso: cerrar el protocolo de "
            "medición.",
        ),
        probe="recurrence",
        expected="different",
        note=(
            "A de-identified analogue of the pair #796 reports, matched on "
            "the properties that make the class hard rather than on its "
            "content: one body carries an abbreviated Spanish date and "
            "three named attendees the other never mentions, and the "
            "agendas do not overlap. The reported documents are a private "
            "meeting transcript naming real people, and eval fixtures are "
            "committed, published and quoted back inside stored model "
            "rationales -- so they carry invented names."
        ),
    ),
    LabelledPair(
        left=FixtureDoc(
            "events/weekly-design-review",
            "Event",
            "Weekly Design Review",
            "2026-03-04. Reviewed the navigation rework. Agreed to drop the "
            "sidebar in favour of a top bar. Attendees: Tom Becker, Priya "
            "Raman.",
        ),
        right=FixtureDoc(
            "events/weekly-design-review-2",
            "Event",
            "Weekly Design Review",
            "2026-03-11. Reviewed the onboarding flow. Agreed to cut the "
            "third welcome screen. Attendees: Tom Becker, Alice Nwosu.",
        ),
        probe="recurrence",
        expected="different",
        note=(
            "Dates one week apart, different subjects, different decisions, "
            "and only one attendee in common. The title names the series, "
            "not either meeting."
        ),
    ),
    LabelledPair(
        left=FixtureDoc(
            "events/sprint-retrospective",
            "Event",
            "Sprint Retrospective",
            "Retrospective for sprint 14. The team flagged that code review "
            "queues were the main source of delay and agreed to cap open "
            "reviews at three per person.",
        ),
        right=FixtureDoc(
            "events/sprint-retrospective-2",
            "Event",
            "Sprint Retrospective",
            "Retrospective for sprint 15. The review cap from last sprint "
            "held. The team raised flaky integration tests as the new "
            "bottleneck and agreed to quarantine them.",
        ),
        probe="recurrence",
        expected="different",
        note=(
            "Each body names its own sprint, and the second refers back to "
            "the first's decision as a previous one. A meeting cannot be a "
            "duplicate of the meeting it reports on."
        ),
    ),
)


# --------------------------------------------------------------------------- #
# event-same -- identical Event titles, one meeting recorded twice
# --------------------------------------------------------------------------- #

_EVENT_SAME: Final[tuple[LabelledPair, ...]] = (
    LabelledPair(
        left=FixtureDoc(
            "events/kickoff-plataforma-orion",
            "Event",
            "Kickoff Plataforma Orión",
            "12 de mayo de 2026. Kickoff de la Plataforma Orión con Lucía "
            "Ferrer y Diego Salas. Se acordó que el primer entregable es el "
            "conector de ingesta, con fecha al 30 de junio.",
        ),
        right=FixtureDoc(
            "events/kickoff-plataforma-orion-2",
            "Event",
            "Kickoff Plataforma Orión",
            "Notas del kickoff de Plataforma Orión, 12/05/2026. Presentes: "
            "Lucía Ferrer, Diego Salas. Primer entregable: conector de "
            "ingesta para el 30 de junio. Se mencionó además el riesgo de "
            "depender de un solo proveedor.",
        ),
        probe="event-same",
        expected="same",
        note=(
            "The same date written two ways, the same two attendees, and "
            "the same deliverable with the same deadline. Two sets of notes "
            "from one meeting; the second simply records one more remark."
        ),
    ),
    LabelledPair(
        left=FixtureDoc(
            "events/q3-budget-signoff",
            "Event",
            "Q3 Budget Sign-off",
            "2026-07-02. Finance signed off the Q3 budget at 1.4M, with the "
            "hiring line frozen until October. Present: Marta Ruiz, Sam "
            "Oyelaran.",
        ),
        right=FixtureDoc(
            "events/q3-budget-signoff-2",
            "Event",
            "Q3 Budget Sign-off",
            "Meeting on 2 July 2026 where the Q3 budget was approved at "
            "1.4M. Marta Ruiz and Sam Oyelaran attended. The hiring freeze "
            "runs to October.",
        ),
        probe="event-same",
        expected="same",
        note=(
            "Identical date, identical figure, identical freeze, identical "
            "attendees. Nothing in either body distinguishes an occurrence."
        ),
    ),
)


# --------------------------------------------------------------------------- #
# asym-recurrence -- one detailed member, one sparse member, two occurrences
# --------------------------------------------------------------------------- #

_ASYM_RECURRENCE: Final[tuple[LabelledPair, ...]] = (
    LabelledPair(
        left=FixtureDoc(
            "events/grupo-calidad-datos",
            "Event",
            "Grupo de Trabajo de Calidad de Datos",
            "Reunión del grupo de trabajo de calidad de datos. El propósito "
            "del grupo es mejorar la trazabilidad del pipeline de datos y "
            "reducir los errores de ingesta que llegan a producción. Se "
            "repasaron las prioridades del trimestre. Queda pendiente "
            "asignar un responsable del glosario de campos.",
        ),
        right=FixtureDoc(
            "events/grupo-calidad-datos-2",
            "Event",
            "Grupo de Trabajo de Calidad de Datos",
            "sep 3, 2026. Reunión del grupo de trabajo de calidad de datos "
            "con Irene Vallejo, Marco Sandoval y Petra Ilić. Se profundizó "
            "en la trazabilidad del pipeline: la metodología de validación "
            "por lotes, los retos con los esquemas heredados del sistema "
            "anterior y el estado actual de las reglas de limpieza. También "
            "se discutieron los errores de ingesta recientes y cómo "
            "clasificarlos. Acciones: documentar las reglas de validación "
            "por lotes y agendar la revisión del esquema heredado con el "
            "equipo de plataforma.",
        ),
        probe="asym-recurrence",
        expected="different",
        note=(
            "The wild #869 shape: the dated, attended body ELABORATES the "
            "sparse body's stated purpose (pipeline traceability, ingest "
            "errors), so shared purpose invites a same verdict -- but the "
            "concrete content is disjoint: the sparse body's pending item "
            "(assign a glossary owner) appears nowhere in the detailed "
            "body, whose action items (document batch validation rules, "
            "schedule the legacy-schema review) appear nowhere in the "
            "sparse one. Purpose is carried by the SERIES; these are two "
            "occurrences of it."
        ),
    ),
    LabelledPair(
        left=FixtureDoc(
            "events/platform-reliability-sync",
            "Event",
            "Platform Reliability Sync",
            "Reliability sync for the platform group. The sync exists to "
            "improve service reliability and bring the on-call load down. "
            "General discussion of where the pain is concentrated. Still "
            "open: choosing a postmortem template the whole group will use.",
        ),
        right=FixtureDoc(
            "events/platform-reliability-sync-2",
            "Event",
            "Platform Reliability Sync",
            "2026-05-14. Platform reliability sync with Dana Whitfield, "
            "Óscar Peña, and Li Wen. Went deep on the reliability push: the "
            "SLO review methodology, the alert-fatigue problem on the "
            "storage rotation, and what the on-call handoff is missing. "
            "Challenges raised: flaky synthetic checks and a noisy paging "
            "policy. Action items: tune the paging thresholds for the "
            "storage tier and pilot error budgets on two services.",
        ),
        probe="asym-recurrence",
        expected="different",
        note=(
            "Same asymmetry in English: the detailed body develops exactly "
            "the purpose the sparse body states (reliability, on-call "
            "load), which is the elaboration-reads-as-alignment trap. "
            "Knowable as different because the sparse body's one open item "
            "(pick a postmortem template) and the detailed body's action "
            "items (tune paging thresholds, pilot error budgets) are "
            "disjoint, and neither body mentions the other's."
        ),
    ),
    LabelledPair(
        left=FixtureDoc(
            "events/onboarding-working-group",
            "Event",
            "Onboarding Working Group",
            "Session of the onboarding working group. The group's purpose "
            "is to redesign the customer onboarding flow so new accounts "
            "reach first value sooner. High-level review of the funnel. "
            "Pending: pull the support tickets that mention onboarding.",
        ),
        right=FixtureDoc(
            "events/onboarding-working-group-2",
            "Event",
            "Onboarding Working Group",
            "22 June 2026. Onboarding working group with Priya Raman, "
            "Jonas Eklund, and Sofía Arrieta. Detailed pass over the "
            "redesign: the funnel-analysis methodology and which cohorts "
            "to segment by, the challenge of legacy accounts that predate "
            "the current signup flow, and early sketches of the first-run "
            "experience. Action items: prototype the welcome checklist and "
            "schedule two usability tests with recent signups.",
        ),
        probe="asym-recurrence",
        expected="different",
        note=(
            "The detailed member elaborates the sparse member's purpose "
            "(onboarding redesign, funnel) with methodology, a named "
            "challenge, and action items the sparse body never carries; "
            "the sparse body's pending item (pull support tickets) is "
            "absent from the detailed one. Two sessions of one working "
            "group, distinguishable only by their disjoint specifics."
        ),
    ),
)


# --------------------------------------------------------------------------- #
# asym-same -- one detailed member, one sparse member, ONE meeting
# --------------------------------------------------------------------------- #

_ASYM_SAME: Final[tuple[LabelledPair, ...]] = (
    LabelledPair(
        left=FixtureDoc(
            "events/sync-arquitectura",
            "Event",
            "Sync de Arquitectura",
            "Sync de arquitectura. Se decidió adoptar un bus de eventos "
            "para la mensajería interna en lugar de colas punto a punto, "
            "empezando por el servicio de notificaciones.",
        ),
        right=FixtureDoc(
            "events/sync-arquitectura-2",
            "Event",
            "Sync de Arquitectura",
            "9 de abril de 2026. Sync de arquitectura con Camila Reyes y "
            "Andrés Bolaño. Se evaluaron colas punto a punto frente a un "
            "bus de eventos para la mensajería interna, repasando costes "
            "operativos, garantías de entrega y la experiencia del equipo. "
            "Decisión: adoptar el bus de eventos, con el servicio de "
            "notificaciones como primer caso. Próximo paso: preparar la "
            "prueba de concepto del conector.",
        ),
        probe="asym-same",
        expected="same",
        note=(
            "The guard for the asymmetric class: the sparse body carries "
            "no date and no attendees, but it records the SAME specific "
            "decision the detailed body records -- event bus over "
            "point-to-point queues, notifications service first. A "
            "decision one occurrence records is exactly the signal the "
            "rubric names, restated by both members; a rule that reads "
            "detail asymmetry itself as distinctness would get this wrong."
        ),
    ),
    LabelledPair(
        left=FixtureDoc(
            "events/vendor-selection-review",
            "Event",
            "Vendor Selection Review",
            "Vendor selection review. Outcome: Nimbus was chosen for the "
            "data warehouse on a three-year contract.",
        ),
        right=FixtureDoc(
            "events/vendor-selection-review-2",
            "Event",
            "Vendor Selection Review",
            "2026-02-19. Vendor selection review with Marta Ruiz, Kofi "
            "Mensah, and Elif Demir. Compared the three shortlisted data "
            "warehouse vendors on pricing, migration support, and regional "
            "availability; walked through the reference calls. Decision: "
            "Nimbus, on a three-year contract. Next: legal review of the "
            "contract terms and a migration plan draft.",
        ),
        probe="asym-same",
        expected="same",
        note=(
            "One meeting, two records at very different levels of detail: "
            "the sparse body restates the unique outcome (Nimbus, data "
            "warehouse, three-year contract) that the detailed body "
            "records as its decision. Nothing in the sparse body is "
            "disjoint with the detailed one -- it is a strict summary."
        ),
    ),
)


# --------------------------------------------------------------------------- #
# person-same, alias-same, part-whole -- the surrounding regimes
# --------------------------------------------------------------------------- #

_CONTROLS: Final[tuple[LabelledPair, ...]] = (
    LabelledPair(
        left=FixtureDoc(
            "people/marta-ruiz",
            "Person",
            "Marta Ruiz",
            "Finance lead. Signs off quarterly budgets and owns the hiring plan.",
        ),
        right=FixtureDoc(
            "people/marta-ruiz-2",
            "Person",
            "Marta Ruiz",
            "Leads the finance team. Approved the Q3 budget and set the hiring freeze.",
        ),
        probe="person-same",
        expected="same",
        note=(
            "One name, one role, compatible facts. For a Person an "
            "identical name is strong evidence of one entity -- the control "
            "the issue names, and the answer an Event-specific rule must "
            "not disturb."
        ),
    ),
    LabelledPair(
        left=FixtureDoc(
            "concepts/model-context-protocol",
            "Concept",
            "Model Context Protocol",
            "An open protocol that lets a model reach tools and data "
            "sources through one uniform interface, so a client speaks to "
            "many servers the same way.",
        ),
        right=FixtureDoc(
            "concepts/protocolo-model-context",
            "Concept",
            "Protocolo Model Context",
            "Protocolo abierto que permite a un modelo alcanzar "
            "herramientas y fuentes de datos por una interfaz uniforme, de "
            "modo que un cliente habla con muchos servidores igual.",
        ),
        probe="alias-same",
        expected="same",
        note=(
            "One protocol named in two languages, with the same definition "
            "restated. The general recall an identity change must not cost "
            "-- and a shape this bilingual corpus actually produces."
        ),
    ),
    LabelledPair(
        left=FixtureDoc(
            "concepts/ingest-pipeline",
            "Concept",
            "Ingest Pipeline",
            "The stage that copies a source into the workspace, compiles it "
            "into objects, and updates the indexes.",
        ),
        right=FixtureDoc(
            "concepts/ingest-pipeline-scheduler",
            "Concept",
            "Ingest Pipeline Scheduler",
            "The component inside the ingest pipeline that decides the "
            "order sources are processed in and how many run at once.",
        ),
        probe="part-whole",
        expected="different",
        note=(
            "The second body says it is a component INSIDE the first. The "
            "part-whole exclusion the shipped prompt already states."
        ),
    ),
)


# --------------------------------------------------------------------------- #
# aspect-of (#910) -- X vs "«aspect» of X", the failing part-whole title shape
# --------------------------------------------------------------------------- #

_ASPECT_OF: Final[tuple[LabelledPair, ...]] = (
    LabelledPair(
        left=FixtureDoc(
            "concepts/atlas-data-platform",
            "Concept",
            "Atlas Data Platform",
            "The internal platform that ingests, stores, and serves the "
            "organization's datasets, spanning pipelines, storage tiers, "
            "and access control.",
        ),
        right=FixtureDoc(
            "concepts/components-of-the-atlas-data-platform",
            "Concept",
            "Components of the Atlas Data Platform",
            "An inventory of the parts the Atlas Data Platform is built "
            "from: the ingestion pipelines, the storage tiers, and the "
            "access-control layer.",
        ),
        probe="aspect-of",
        expected="different",
        note=(
            "The right body is an inventory OF the left's parts -- the "
            "part-whole exclusion the rubric already states, in the exact "
            "'components of X' title shape #910 reports the judge "
            "answering `same` to while answering `different` to 'storage "
            "in X' against the same anchor."
        ),
    ),
    LabelledPair(
        left=FixtureDoc(
            "concepts/meridian-archive-service",
            "Concept",
            "Meridian Archive Service",
            "The service that preserves published research bundles and "
            "answers retrieval requests against them.",
        ),
        right=FixtureDoc(
            "concepts/storage-in-the-meridian-archive-service",
            "Concept",
            "Storage in the Meridian Archive Service",
            "How the Meridian Archive Service lays out preserved bundles "
            "on disk: one content-addressed store per collection, with a "
            "manifest per bundle.",
        ),
        probe="aspect-of",
        expected="different",
        note=(
            "The right body describes one facet INSIDE the left -- the "
            "'storage in X' variant of the shape, the one #910's wild run "
            "got right; carried so the class measures the title SHAPE "
            "rather than one aspect noun."
        ),
    ),
    LabelledPair(
        left=FixtureDoc(
            "concepts/quorum-review-workflow",
            "Concept",
            "Quorum Review Workflow",
            "The end-to-end workflow a submitted change goes through: "
            "triage, reviewer assignment, verdict collection, and merge.",
        ),
        right=FixtureDoc(
            "concepts/governance-of-the-quorum-review-workflow",
            "Concept",
            "Governance of the Quorum Review Workflow",
            "Who may change the Quorum Review Workflow's rules, how rule "
            "changes are ratified, and where the rulings are recorded.",
        ),
        probe="aspect-of",
        expected="different",
        note=(
            "The right body is about the RULES OVER the left, not the "
            "workflow itself -- a third aspect noun, so a verdict pattern "
            "across the class separates the shape from any one noun."
        ),
    ),
)


# --------------------------------------------------------------------------- #
# transitivity (#910) -- one Project anchor against three Events that are
# themselves three occurrences of one standing meeting series
# --------------------------------------------------------------------------- #

_TRANSITIVITY_ANCHOR: Final[FixtureDoc] = FixtureDoc(
    "projects/evaluacion-de-decisiones",
    "Project",
    "Evaluación de Decisiones",
    "Iniciativa de investigación en curso sobre cómo el equipo registra y "
    "revisa sus decisiones. Abarca la metodología, las herramientas y los "
    "criterios de calidad; no es una reunión.",
)
"""The one document deliberately SHARED across the three cross-type pairs
below (#910): the wild inconsistency is one Project judged `same` as two of
three Events that were themselves judged mutually `different`, and three
independent anchors could not reproduce that shape. `documents()` dedupes
it; the ids stay unique per document, never per pair."""

_TRANSITIVITY_EVENTS: Final[tuple[FixtureDoc, ...]] = (
    FixtureDoc(
        "events/reunion-de-evaluacion-de-decisiones-1",
        "Event",
        "Reunión de Evaluación de Decisiones 1",
        "sep 2, 2026. Primera reunión del ciclo con Elena Varga y Rubén "
        "Castaño. Se revisó el criterio de registro de decisiones y quedó "
        "pendiente el formato de acta.",
        type_alternative="Project",
    ),
    FixtureDoc(
        "events/reunion-de-evaluacion-de-decisiones-2",
        "Event",
        "Reunión de Evaluación de Decisiones 2",
        "sep 9, 2026. Segunda reunión del ciclo con Nadia Iqbal. Se "
        "discutió la herramienta de seguimiento y los límites del tablero "
        "actual. Próximo paso: migrar el tablero.",
        type_alternative="Project",
    ),
    FixtureDoc(
        "events/reunion-de-evaluacion-de-decisiones-3",
        "Event",
        "Reunión de Evaluación de Decisiones 3",
        "sep 16, 2026. Tercera reunión del ciclo. Se ratificó el criterio "
        "de calidad y se cerró el formato de acta propuesto en la primera "
        "sesión.",
        type_alternative="Project",
    ),
)

TRANSITIVITY_MEMBERS: Final[tuple[str, ...]] = (
    _TRANSITIVITY_ANCHOR.concept_id,
    *(doc.concept_id for doc in _TRANSITIVITY_EVENTS),
)
"""The four ids whose C(4,3) verdict triangles the runner scores for
transitivity violations -- exported so the runner derives the triangles
from the fixtures rather than restating the ids."""


def _transitivity_pairs() -> tuple[LabelledPair, ...]:
    """All six pairs over the anchor and the three Events, every one
    expected `different`: an Event is one occurrence, the Project is the
    ongoing initiative (the distinction #910's own middle row states and
    the row below it abandons), and the Events are three dated occurrences
    of one series. The anchor-Event pairs are nominated by the production
    cross-type bridge (each Event's own `type_alternative`); the
    Event-Event pairs by the ordinary same-type LOW pass."""
    pairs: list[LabelledPair] = []
    for event in _TRANSITIVITY_EVENTS:
        pairs.append(
            LabelledPair(
                left=_TRANSITIVITY_ANCHOR,
                right=event,
                probe="transitivity",
                expected="different",
                note=(
                    "The Event body records one dated meeting; the Project "
                    "body says it is the ongoing initiative and states it "
                    "is not a meeting. #910's wild run answered `same` for "
                    "two of the three shapes like this one."
                ),
            )
        )
    for index, left in enumerate(_TRANSITIVITY_EVENTS):
        for right in _TRANSITIVITY_EVENTS[index + 1 :]:
            pairs.append(
                LabelledPair(
                    left=left,
                    right=right,
                    probe="transitivity",
                    expected="different",
                    note=(
                        "Two dated occurrences of one series -- distinct "
                        "dates, attendees, and outcomes, the recurrence "
                        "class's own shape. Carried inside this probe so "
                        "every leg of each verdict triangle is scored by "
                        "the same run."
                    ),
                )
            )
    return tuple(pairs)


_TRANSITIVITY: Final[tuple[LabelledPair, ...]] = _transitivity_pairs()


PAIRS: Final[tuple[LabelledPair, ...]] = (
    _RECURRENCE
    + _EVENT_SAME
    + _ASYM_RECURRENCE
    + _ASYM_SAME
    + _CONTROLS
    + _ASPECT_OF
    + _TRANSITIVITY
)

PROBES: Final[tuple[str, ...]] = (
    "recurrence",
    "event-same",
    "asym-recurrence",
    "asym-same",
    "person-same",
    "alias-same",
    "part-whole",
    "aspect-of",
    "transitivity",
)


def documents(
    pairs: tuple[LabelledPair, ...] = PAIRS,
) -> tuple[FixtureDoc, ...]:
    """Every DISTINCT fixture document of `pairs`, in pair order.

    `pairs` defaults to the full `PAIRS` set and exists for one caller:
    the runner's self-test, which feeds a deliberately conflicting pair
    set to prove the collision raise below actually fires -- an
    unexercised safety net is indistinguishable from a deleted one.

    A document deliberately shared between pairs (the `transitivity`
    anchor and its Events, #910) appears once: the SAME `FixtureDoc`
    object may recur across pairs, and recurrence of the identical
    document is intentional sharing. What still raises is one
    `concept_id` carried by two DIFFERENT documents -- that collision
    would silently merge two probes into one candidate group, and the
    harness would score a pair that does not exist."""
    seen: dict[str, FixtureDoc] = {}
    docs: list[FixtureDoc] = []
    for pair in pairs:
        for doc in (pair.left, pair.right):
            existing = seen.get(doc.concept_id)
            if existing is None:
                seen[doc.concept_id] = doc
                docs.append(doc)
            elif existing != doc:
                raise ValueError(
                    f"conflicting fixture documents share concept_id: {doc.concept_id}"
                )
    return tuple(docs)
