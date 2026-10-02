"""Synthetic sources for the decision-granularity harness (#1231 b and c).

Every text here is written for this file. None of it comes from a private
corpus, and no real person, company or project is named.

Each fixture shapes ONE failure the field report described and carries the
hand-written expectations the scorer reads:

- `topics`: one keyword group per distinct subject the source develops. A
  group matches when ANY of its alternatives occurs (accent-folded,
  case-folded) in a retained object's title, description or body. Used for
  recall, never for granularity.
- `expected_decisions`: how many separate choices the source records, or
  `None` when granularity is not what the fixture measures.
- `target_person`: a name the source states TWICE (it is the subject of two
  separate sentences), so a Person object for it is warranted, not a stub.
- `single_decision`: the over-splitting control -- one choice, so more than
  one Decision object is a defect.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Fixture:
    name: str
    title: str
    text: str
    topics: tuple[tuple[str, ...], ...]
    expected_decisions: int | None = None
    target_person: str | None = None
    single_decision: bool = False
    shape: str = ""
    """What failure shape this fixture reproduces, for the report."""


# Spanish export-style notes: five decisions under one "Decisiones" heading.
ES_FIVE_DECISIONS = Fixture(
    name="es-notes-5-decisions",
    title="Reunión de planificación del trimestre - Notas",
    shape="(b) five decisions under one heading, Spanish meeting notes",
    expected_decisions=5,
    topics=(
        ("cola de mensajes", "pierde mensajes", "servicio de pagos"),
        ("esquema de la api", "congela", "congelar"),
        ("soporte nocturno", "externaliz"),
        ("registro estructurado", "formato de registro"),
        ("panel interno", "renovación", "renovacion"),
    ),
    text=(
        "# Reunión de planificación del trimestre\n\n"
        "Invitados: Valeria Montes, Ignacio Duarte, Camila Ferreyra\n\n"
        "## Resumen\n\n"
        "El equipo repasó el estado del servicio de pagos, las quejas de "
        "los clientes de la API pública y la carga de las guardias, y "
        "acordó el plan de trabajo del trimestre.\n\n"
        "## Decisiones\n\n"
        "- Se migra el servicio de pagos a la cola de mensajes nueva antes "
        "de junio, porque la actual pierde mensajes bajo carga.\n"
        "- Se congela el esquema de la API pública hasta el cierre del "
        "trimestre para no romper a los clientes que ya integraron.\n"
        "- Se contrata a una persona más para el soporte nocturno en lugar "
        "de externalizarlo, por el costo de los incidentes repetidos.\n"
        "- Se adopta el formato de registro estructurado en todos los "
        "servicios, empezando por el de pagos.\n"
        "- Se pospone la renovación del panel interno al siguiente "
        "trimestre.\n\n"
        "## Próximos pasos\n\n"
        "- Valeria preparará el plan de migración de la cola.\n"
        "- Ignacio revisará los candidatos para soporte nocturno.\n"
        "- Camila definirá los campos del registro estructurado.\n"
    ),
)

# Gemini-notes layout (bold lead-ins), titled so it is NOT meeting-shaped:
# the general extraction path with the title shown to the model.
ES_GEMINI_NOTES = Fixture(
    name="es-gemini-notes-5-decisions",
    title="Seguimiento semanal - 2026/03/10 - Notas de Gemini",
    shape="(b) five bold-lead decisions, export-style layout, non-meeting title",
    expected_decisions=5,
    topics=(
        ("cola de mensajes", "pierde mensajes"),
        ("esquema de la api", "congel"),
        ("soporte nocturno", "externaliz"),
        ("registro estructurado", "formato de registro"),
        ("panel interno", "renovación", "renovacion"),
    ),
    text=(
        "Seguimiento semanal\n\n"
        "Invitados: Valeria Montes, Ignacio Duarte, Camila Ferreyra\n\n"
        "### Resumen\n\n"
        "El equipo revisó el estado del servicio de pagos, las quejas de "
        "los clientes de la API pública y la carga de las guardias. "
        "Valeria explicó que la cola actual se satura en los cierres de "
        "mes, e Ignacio presentó los costos de los incidentes nocturnos "
        "del último trimestre.\n\n"
        "### Decisiones\n\n"
        "**Migración de la cola:** se migra el servicio de pagos a la cola "
        "de mensajes nueva antes de junio, porque la actual pierde "
        "mensajes bajo carga.\n\n"
        "**Congelación de la API:** se congela el esquema de la API "
        "pública hasta el cierre del trimestre para no romper a los "
        "clientes que ya integraron.\n\n"
        "**Soporte nocturno:** se contrata a una persona más en lugar de "
        "externalizar el soporte nocturno, por el costo de los incidentes "
        "repetidos.\n\n"
        "**Registros:** se adopta el formato de registro estructurado en "
        "todos los servicios, empezando por el de pagos.\n\n"
        "**Panel interno:** se pospone la renovación del panel interno al "
        "siguiente trimestre.\n\n"
        "### Próximos pasos\n\n"
        "- Valeria preparará el plan de migración de la cola.\n"
        "- Ignacio revisará los candidatos para soporte nocturno.\n"
        "- Camila definirá los campos del registro estructurado.\n\n"
        "Actualizamos la sección Decisiones con tus comentarios. Danos tu "
        "opinión: Útil / Poco útil\n"
    ),
)

# English control: three bullet decisions. The field report says this shape
# already splits, so it guards against a treatment that makes it worse.
EN_REVIEW_THREE = Fixture(
    name="en-review-3-decisions",
    title="2026-02-03-architecture-review",
    shape="(b) control: three bullet decisions, English review notes",
    expected_decisions=3,
    topics=(
        ("read replica", "replica"),
        ("feature flag", "flags"),
        ("batch size", "nightly", "export"),
    ),
    text=(
        "# Architecture review, 3 February\n\n"
        "Notes from the review of the Orion data service. The service has "
        "grown from a single reporting job into the system most internal "
        "teams read from, so the review looked at where it hurts.\n\n"
        "## Context\n\n"
        "Reporting queries run against the same primary database as the "
        "write path. During the month-end close the queries slow the "
        "writes enough that two customers saw timeouts. Releases are "
        "currently all-or-nothing: a change goes to every customer at once, "
        "and the last two rollbacks took most of a day. The nightly export "
        "job moves rows in large batches, and when one batch fails the "
        "whole night is retried from the start.\n\n"
        "## Discussion\n\n"
        "The group compared adding capacity to the primary against moving "
        "reads elsewhere, and preferred moving reads because the load is "
        "read-heavy and bursty. On releases, the group agreed the rollbacks "
        "were slow mostly because there was no way to turn a single change "
        "off. For the export, a smaller batch means more round trips but "
        "much cheaper recovery.\n\n"
        "## Decisions\n\n"
        "- Orion reads move to a read replica so reporting queries stop "
        "competing with writes.\n"
        "- New behavior ships behind feature flags and is switched on one "
        "customer at a time.\n"
        "- The nightly export batch size drops to 500 rows so a failed "
        "batch is cheap to retry.\n\n"
        "## Open questions\n\n"
        "- Whether the replica needs its own alerting.\n"
        "- Who owns the flag cleanup once a rollout finishes.\n"
    ),
)

# English: decisions plus a new engineer named twice and attendees named
# once. Models issue part (c): the engineer is named in a decision AND in a
# staffing line, yet is not extracted as a Person.
EN_NEW_ENGINEER = Fixture(
    name="en-review-new-engineer",
    title="2026-02-10-architecture-review",
    shape="(c) a new engineer named twice, English review notes",
    expected_decisions=2,
    target_person="Rafael Okonkwo",
    topics=(
        ("queue", "retry"),
        ("rafael okonkwo",),
        ("ingestion",),
    ),
    text=(
        "# Architecture review, 10 February\n\n"
        "Attendees: Marta Lindqvist, Hugo Brandt, Selin Arslan\n\n"
        "Notes from the review of the Orion ingestion path.\n\n"
        "## Context\n\n"
        "The ingestion service accepts messages from about forty upstream "
        "producers. When a message fails to parse it is currently dropped "
        "after one attempt, and the producers only find out when a "
        "dashboard looks wrong. Two incidents last quarter came from "
        "messages lost this way. The team is also short-handed on the "
        "backend side while the ingestion work is under way.\n\n"
        "## Discussion\n\n"
        "The group agreed that losing messages silently is the larger risk "
        "and that parked messages must be easy to inspect and replay. "
        "Three retries was chosen because the failures seen so far were "
        "transient, and more attempts only delayed the alert.\n\n"
        "## Decisions\n\n"
        "- Rafael Okonkwo joins as the second backend engineer on the "
        "Orion team.\n"
        "- The ingestion service gets a dead-letter queue with three "
        "retries before a message is parked.\n\n"
        "## Staffing\n\n"
        "Rafael starts on Monday and will pair with Marta on the "
        "ingestion service during his first two weeks. Rafael Okonkwo "
        "comes from a payments team and has run queue-based systems "
        "before, which is why the dead-letter work is his first task.\n"
    ),
)

# Spanish meeting-titled variant: the participant-capture pass also fires.
ES_NEW_ENGINEER = Fixture(
    name="es-meeting-new-engineer",
    title="Reunión de arquitectura - 10 de febrero",
    shape="(c) a new engineer named twice, Spanish meeting notes",
    expected_decisions=2,
    target_person="Joaquín Beltrán",
    topics=(
        ("cola", "reintentos"),
        ("joaquín beltrán", "joaquin beltran"),
        ("ingesta", "ingestión", "ingestion"),
    ),
    text=(
        "# Reunión de arquitectura - 10 de febrero\n\n"
        "Asistentes: Marta Lindqvist, Hugo Brandt, Selin Arslan\n\n"
        "## Decisiones\n\n"
        "- Joaquín Beltrán se incorpora como segundo ingeniero de backend "
        "del equipo de Orion.\n"
        "- El servicio de ingesta tendrá una cola de mensajes fallidos con "
        "tres reintentos antes de apartar el mensaje.\n\n"
        "## Equipo\n\n"
        "Joaquín empieza el lunes y trabajará en pareja con Marta en el "
        "servicio de ingesta durante sus dos primeras semanas. Joaquín "
        "Beltrán viene de un equipo de pagos y ya ha operado sistemas "
        "basados en colas.\n"
    ),
)

# Over-splitting control: exactly one choice with its rationale.
EN_SINGLE_DECISION = Fixture(
    name="en-note-single-decision",
    title="2026-02-17-cache-note",
    shape="control: one decision, so one Decision object is correct",
    expected_decisions=1,
    single_decision=True,
    topics=(("cache", "ttl"),),
    text=(
        "# Cache note\n\n"
        "The team compared a five-minute and a one-hour time to live for "
        "the product catalog cache. Stale prices are costly, and the "
        "catalog rarely changes more than once an hour, so the team chose "
        "a five-minute time to live and will revisit it if the database "
        "load grows.\n"
    ),
)

FIXTURES: tuple[Fixture, ...] = (
    ES_FIVE_DECISIONS,
    ES_GEMINI_NOTES,
    EN_REVIEW_THREE,
    EN_NEW_ENGINEER,
    ES_NEW_ENGINEER,
    EN_SINGLE_DECISION,
)
