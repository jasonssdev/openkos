"""Matched source pairs for the collapse probe (issue #522).

Each pair carries the SAME facts twice and varies exactly ONE property, its
AXIS. Nothing else changes -- not the facts, not the subjects, not (within a
tolerance the probe checks) the length.

Two axes ship today:

- **meeting register** (`producto`, `versioning`): a record of a meeting
  against the same facts as flat statements. #522's original claim.
- **announced multiplicity** (`anuncio`): a meeting whose opening sentence
  enumerates the topics covered against one that does not. Both arms are
  meetings; only the announcement moves.

Each arm carries a ROLE as well as a label: `TREATMENT` is the arm its
hypothesis predicts will collapse, `FLOOR` is the arm that has to hold. The
roles are what the verdict logic reads, so a new axis needs no new harness.

## Why pairs, rather than more meeting sources

The table in #522 compared meeting material against unrelated expository
fixtures and found the meeting sources collapsing to one object. That
comparison cannot separate framing from content: `small-04` and a meeting
transcript differ in register AND in topic AND in length, so three
explanations survive it.

A pair holds two of those fixed. If the meeting arm collapses and its flat
twin does not, the register is the mechanism -- which is exactly the probe
#522 asks for, made repeatable.

## The flat arm is the affordance floor, and that is what removes ground truth

There is no human count of "the right number of objects" here, and there
deliberately is none. What the flat arm establishes is that THIS TEXT
CONTAINS more than one distinct subject: the extractor itself found them, on
the same facts. So a meeting arm returning one object is a collapse against
evidence the probe generated, not against a number someone asserted. That is
the same one-directional move `decision_extraction/run_type_coverage.py`
makes with AMI's named-entity layer, with the floor produced here instead of
annotated.

The consequence is worth stating plainly: if BOTH arms collapse, this fixture
proves nothing about framing and the probe says so rather than reading the
meeting arm alone as a finding.

## The flat arm opens with a fact, never with a summary of the source

This cost a measurement, so it is written down. The `producto` flat arm first
opened "El onboarding y un par de temas pendientes del producto quedan en
este estado" -- an attempt to match the meeting arm's opening line for
length. It collapsed 5 of 5 runs to a single `Procedure` titled "Onboarding
Process", and the probe correctly reported `INVERTED`.

That was not flat prose collapsing. It was a sentence that names what the
whole document is about, which is an umbrella-topic frame the meeting arm
does not have -- so the pair had stopped varying only the register and the
verdict was unreadable.

Stripping the register means REMOVING the meeting sentence, not translating
it into a thesis statement. Where that leaves the arms too far apart under
`MAX_LENGTH_SKEW`, shorten the meeting arm's framing rather than give the
flat arm something to say: the meeting opening is the part that carries no
fact, so it is the part that can be cut without touching what the pair holds
fixed.

## What is written into every pair on purpose

Three distinct subjects, of the kinds a meeting actually produces: a proposal
about one thing, a committed choice about a different thing (with its
rationale), and outstanding work. They are about SEPARATE topics -- onboarding
verification, a Slack integration, and pending tasks -- so an extractor that
returns one object has genuinely merged unrelated subjects rather than
declined to split one.

CONSTRUCTED, not adjudicated, with the same limitation
`edge_typing/fixtures.py` states: these are written to make one defect
visible, not to certify behavior on organic material.

## The name

Not `fixtures.py`, though every sibling harness names its data that way. CI
runs `mypy .` over the whole repository, and the eval directories carry no
`__init__.py`, so two files named `fixtures.py` resolve to one duplicate
top-level module and typechecking stops before it checks anything. The unique
name is the smallest fix that leaves `edge_typing/` untouched.
"""

from __future__ import annotations

from dataclasses import dataclass

TREATMENT = "treatment"
"""The arm hypothesized to collapse."""

FLOOR = "floor"
"""The arm that establishes the text holds more than one subject."""


@dataclass(frozen=True)
class PairedSource:
    """One arm of one pair -- the unit the probe extracts from."""

    pair_id: str
    role: str
    """`TREATMENT` or `FLOOR` -- which side of the hypothesis this arm is."""
    arm: str
    """A descriptive label for what this arm IS (`meeting`, `flat`,
    `announced`, `unannounced`). Roles say which arm the hypothesis predicts
    will collapse; labels say what was actually written, and only the label
    survives into the report where a reader needs to know what varied."""
    language: str
    source_title: str
    """Passed to `extract_concept` as `source_title`. It carries framing too:
    a title naming a meeting is part of what the meeting arm IS, so the arms
    differ here on purpose and the probe never reuses one arm's title."""
    text: str


SOURCES: tuple[PairedSource, ...] = (
    PairedSource(
        pair_id="producto",
        role=TREATMENT,
        arm="meeting",
        language="ES",
        source_title="Reunión con el equipo de producto",
        text=(
            "Nos reunimos hoy con el equipo de producto.\n\n"
            "Ana propuso mover el paso de verificación de correo al final "
            "del onboarding, después de que la persona ya haya creado su "
            "primer espacio de trabajo. Contó que la mitad de la gente "
            "abandona ahí porque llega antes de entender para qué sirve el "
            "producto.\n\n"
            "Decidimos posponer la integración con Slack hasta julio. La "
            "razón es que el equipo de infraestructura está con la migración "
            "de la base de datos hasta junio y no hay nadie que pueda "
            "sostener el webhook si se rompe.\n\n"
            "Cerramos con dos pendientes: Marta prepara el borrador del "
            "nuevo flujo de onboarding para el lunes, y Luis mide cuánta "
            "gente abandona hoy en el paso de verificación.\n"
        ),
    ),
    PairedSource(
        pair_id="producto",
        role=FLOOR,
        arm="flat",
        language="ES",
        source_title="Onboarding, Slack y trabajo pendiente",
        text=(
            "El paso de verificación de correo conviene moverlo al final del "
            "onboarding, después de que la persona ya haya creado su primer "
            "espacio de trabajo. La mitad de la gente abandona ahí porque "
            "llega antes de entender para qué sirve el producto.\n\n"
            "La integración con Slack queda pospuesta hasta julio. La razón "
            "es que el equipo de infraestructura está con la migración de la "
            "base de datos hasta junio y no hay nadie que pueda sostener el "
            "webhook si se rompe.\n\n"
            "Quedan dos trabajos pendientes. Marta prepara el borrador del "
            "nuevo flujo de onboarding para el lunes, y Luis mide cuánta "
            "gente abandona hoy en el paso de verificación.\n"
        ),
    ),
    PairedSource(
        pair_id="versioning",
        role=TREATMENT,
        arm="meeting",
        language="EN",
        source_title="Platform sync, 12 March",
        text=(
            "The platform team met this morning to go over the API "
            "versioning work and a couple of loose ends.\n\n"
            "Priya proposed putting the version in the URL path rather than "
            "in a header, because the header scheme is invisible in logs and "
            "every incident so far has started with someone unable to tell "
            "which version a failing client was on.\n\n"
            "We decided to keep the v1 endpoints alive until the end of the "
            "year. The reason is that two enterprise customers hold "
            "contracts naming v1 explicitly, and neither renews before "
            "November.\n\n"
            "We closed with two items: Dan writes the migration note for the "
            "docs site by Friday, and Priya measures how much v1 traffic is "
            "left.\n"
        ),
    ),
    PairedSource(
        pair_id="versioning",
        role=FLOOR,
        arm="flat",
        language="EN",
        source_title="API versioning and the v1 timeline",
        text=(
            "The API versioning work and a couple of loose ends on the "
            "platform stand as follows.\n\n"
            "The version belongs in the URL path rather than in a header, "
            "because the header scheme is invisible in logs and every "
            "incident so far has started with someone unable to tell which "
            "version a failing client was on.\n\n"
            "The v1 endpoints stay alive until the end of the year. The "
            "reason is that two enterprise customers hold contracts naming "
            "v1 explicitly, and neither renews before November.\n\n"
            "Two items remain. Dan writes the migration note for the docs "
            "site by Friday, and Priya measures how much v1 traffic is "
            "left.\n"
        ),
    ),
    # The `anuncio` pair tests the announced-multiplicity hypothesis on
    # content it was NOT generated from. See AXES below for why that matters.
    PairedSource(
        pair_id="anuncio",
        role=TREATMENT,
        arm="unannounced",
        language="ES",
        source_title="Reunión con el equipo de soporte",
        text=(
            "Nos juntamos con el equipo de soporte.\n\n"
            "Rocío propuso separar la cola de tickets por idioma. Los casos "
            "en portugués esperan el doble que los demás y se resuelven "
            "igual de rápido una vez que alguien los toma, así que la espera "
            "no viene de la dificultad sino de que nadie los reclama.\n\n"
            "Decidimos quedarnos con el proveedor de correo actual hasta "
            "diciembre. La razón es que el contrato tiene una penalidad por "
            "salida anticipada que cuesta más que el ahorro del primer año "
            "con cualquiera de los dos proveedores que miramos.\n\n"
            "Quedaron dos cosas por hacer. Iván arma el informe de tiempos "
            "de espera por idioma para el jueves, y Rocío pide a "
            "administración las cifras exactas de la penalidad.\n"
        ),
    ),
    PairedSource(
        pair_id="anuncio",
        role=FLOOR,
        arm="announced",
        language="ES",
        source_title="Reunión con el equipo de soporte",
        text=(
            "Nos juntamos con el equipo de soporte para hablar del tiempo de "
            "respuesta, del proveedor de correo y de lo que quedó "
            "pendiente.\n\n"
            "Rocío propuso separar la cola de tickets por idioma. Los casos "
            "en portugués esperan el doble que los demás y se resuelven "
            "igual de rápido una vez que alguien los toma, así que la espera "
            "no viene de la dificultad sino de que nadie los reclama.\n\n"
            "Decidimos quedarnos con el proveedor de correo actual hasta "
            "diciembre. La razón es que el contrato tiene una penalidad por "
            "salida anticipada que cuesta más que el ahorro del primer año "
            "con cualquiera de los dos proveedores que miramos.\n\n"
            "Quedaron dos cosas por hacer. Iván arma el informe de tiempos "
            "de espera por idioma para el jueves, y Rocío pide a "
            "administración las cifras exactas de la penalidad.\n"
        ),
    ),
)

AXES: dict[str, str] = {
    "producto": "meeting register",
    "versioning": "meeting register",
    "anuncio": "whether the opening sentence enumerates the source's topics",
}
"""What each pair varies. Printed with the verdict, because "the treatment arm
collapsed" means nothing without it."""

HYPOTHESES: dict[str, str] = {
    "producto": "a source that records a meeting collapses to the meeting (#522)",
    "versioning": "a source that records a meeting collapses to the meeting (#522)",
    "anuncio": (
        "a source that does NOT announce its own topics collapses to one "
        "object, regardless of register"
    ),
}
"""What a treatment-only collapse would support, per pair.

`anuncio` deliberately carries content the hypothesis was NOT generated from.
The announced-multiplicity idea came out of `producto`, where shortening the
meeting arm's opening -- a change made only to satisfy `MAX_LENGTH_SKEW` --
took it from 2 collapses in 10 runs to 10 in 10. Re-running that same text
would re-fit the hypothesis to the observation that produced it. A support
queue, two new subjects and a different decision are what make `anuncio` a
test instead."""

STUB_TYPES: frozenset[str] = frozenset({"Person", "Organization"})
"""Types that, ON THESE FIXTURES, the prompt's own rule already forbids.

This is a property of the fixtures, not a general rule about extraction. Every
pair here names people (Ana, Marta, Luis, Priya, Dan, Rocío, Iván) and teams
purely as participants in what happened; no arm is ABOUT any of them. The
anti-enumeration paragraph in `concept.py` covers exactly that case by name:

    a person, place, or organization merely mentioned or named in passing is
    NOT a standalone object [...] extract the Event and the Decisions, not
    five Person stubs.

So a `Person` or `Organization` object here is the model contradicting an
instruction it was given, which needs no adjudication to score. It exists
because `qwen3:14b` emitted Ana, Marta and Luis as objects while every
collapse verdict in the same report read `NOT REPRODUCED` -- the probe was
measuring one failure mode and blind to its opposite.

A fixture genuinely about a person would have to override this."""

MAX_LENGTH_SKEW = 0.15
"""How far the two arms of a pair may differ in length before the pair stops
measuring framing.

Length is the one confound a pair cannot hold fixed by construction: the
meeting register costs characters ("We met this morning to go over...") that
the flat arm has to spend on something else or not at all. If one arm is much
longer, a difference in object count is as easily explained by having more
text to split as by the framing, and the probe would be reporting the
confound it exists to remove. 15% is the widest gap the current pairs need;
it is checked, not assumed."""


def length_skew(pair: tuple[PairedSource, PairedSource]) -> float:
    """`|a - b| / max(a, b)` over the two arms' text lengths."""
    first, second = (len(arm.text) for arm in pair)
    return abs(first - second) / max(first, second)


def pairs() -> dict[str, tuple[PairedSource, PairedSource]]:
    """`{pair_id: (treatment arm, floor arm)}`, in `SOURCES` order.

    Raises if a pair is incomplete or duplicated, because a half pair silently
    dropped would leave the probe reporting a treatment arm's object count
    with no floor to read it against -- which is precisely the reading #522
    warns is not a finding. Raises too when a pair has no declared axis: a
    verdict that cannot name what varied is not readable."""
    by_id: dict[str, dict[str, PairedSource]] = {}
    for source in SOURCES:
        arms = by_id.setdefault(source.pair_id, {})
        if source.role in arms:
            raise ValueError(f"duplicate {source.role!r} arm for {source.pair_id!r}")
        arms[source.role] = source

    out: dict[str, tuple[PairedSource, PairedSource]] = {}
    for pair_id, arms in by_id.items():
        missing = {TREATMENT, FLOOR} - set(arms)
        if missing:
            raise ValueError(f"{pair_id!r} is missing arm(s): {sorted(missing)}")
        if pair_id not in AXES or pair_id not in HYPOTHESES:
            raise ValueError(f"{pair_id!r} has no declared axis or hypothesis")
        out[pair_id] = (arms[TREATMENT], arms[FLOOR])
    return out
