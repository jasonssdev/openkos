"""Matched source pairs for the collapse probe (issue #522).

Each pair carries the SAME facts twice and varies exactly ONE property, its
AXIS. Nothing else changes -- not the facts, not the subjects, not (within a
tolerance the probe checks) the length.

Three axes ship today:

- **meeting register** (`producto`, `versioning`): a record of a meeting
  against the same facts as flat statements. #522's original claim.
- **announced multiplicity** (`anuncio`): a meeting whose opening sentence
  enumerates the topics covered against one that does not. Both arms are
  meetings; only the announcement moves.
- **short lesson framing** (`lesson`): a short titled lesson whose title
  names an umbrella topic and whose opening sentence names the lesson,
  against the same three facts with neither. Nothing here is a meeting.

Each arm carries a ROLE as well as a label: `TREATMENT` is the arm its
hypothesis predicts will collapse, `FLOOR` is the arm that has to hold. The
roles are what the verdict logic reads, so a new axis needs no new harness.

One fixture is NOT a pair: `NEGATIVE_CONTROL`, a genuinely single-subject
source where returning one object is the CORRECT answer. See its own section
below -- it is read against `NEGATIVE_CONTROL_OBJECTS`, never against a
verdict.

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

`lesson` keeps that shape without being a meeting: an environment tool, a
committed choice about version pinning with its rationale, and a directory
convention. Three subjects, no participants -- it names no people at all, so
`STUB_TYPES` holds there a fortiori.

## The 1-4 KB band, and what no other fixture in this repository covers

`lesson` and `NEGATIVE_CONTROL` are the only fixtures here written to a
LENGTH target: roughly 1-4 KB, the size a course lesson file actually is.
Nothing else in `evals/` sits in that band on single-topic material.
`extraction_cap/` holds multi-subject expository prose at 7.6-17 KB, the
three pairs above are 600-800 B of meeting or flat statements, and
`decision_extraction/` runs on transcripts. So a proposed change to
`_drop_source_title_twins` (`concept.py`) could be measured against
multi-subject prose and against meeting material, and against nothing at all
of the shape whose floor the rule was written to protect --
`measure_single_object_rate.py` says exactly this under
`SINGLE_SUBJECT_UNMEASURED`.

What the band buys is the confound the other fixtures cannot remove. A short
source has less text to split, so an object count of one there is ambiguous
between "the framing collapsed it" and "there was not much to say"; a long
multi-subject source never reaches the case at all. Holding the length near
the reported shape is what makes the false-positive rate measured on it a
rate ABOUT that shape.

## The negative control, where one object is the right answer

`NEGATIVE_CONTROL` is a genuinely single-subject source: a title naming one
thing (a term, `Replica Lag`) and a body about that one thing. It is the
`mcp-launch` class `_drop_source_title_twins` names in its own docstring and
that `test_source_title_twin_kept_when_it_is_the_only_object` guards -- the
source whose only object restates the source title, kept because suppressing
it would emit `[]` for genuine content.

Every pair above reads one object as the DEFECT. Here one object is the
correct answer, and the failure to watch for is the opposite one: a change
that returns `[]` or drops the lone object. No pair can express that, because
a pair's floor arm is by construction multi-subject. So this fixture runs
UNPAIRED, exactly as the positive control does, and is reported in its own
section against `NEGATIVE_CONTROL_OBJECTS` rather than through a verdict.

It carries the same CONSTRUCTED limitation as everything else in this file,
plus one of its own: one source is one shape. A `[]` rate measured on it is
evidence about this document, not a false-positive rate for the rule.

## A control that cannot fail is not a control that passed

Written down because it already happened here. The first `NEGATIVE_CONTROL`
was a scheduled job, `Nightly Index Rebuild`, and it read as a clean pass on
its first live run: one object in 5 of 5, titled exactly like the source.
The object was a `Procedure` every time, and `_is_twin` exempts `Procedure`
BY TYPE (#413) -- so it would have survived with both of the rule's floors
deleted. The control was green on a case that could not go red.

Two things follow, and both are enforced rather than remembered. The fixture
is now a definition rather than a how-to, so the prompt's own tie-break does
not route it to the exempt type. And the harness stopped taking the type on
trust: `title_twin_runs` counts only DROPPABLE twins, `exempt_twin_runs`
counts the rest, and a run whose lone object is exempt is reported as
carrying no floor evidence instead of as a pass. The fixture makes the right
outcome likely; the harness makes the wrong one visible. Only the second is
a guarantee.

## Three subjects have to be separable, not merely three

The first `lesson` pair collapsed on BOTH arms (5 of 5 each, single-pass
`qwen3:8b`, seed 7) and the probe correctly reported `NO FLOOR`: the flat arm
afforded no floor, so the pair said nothing about its axis. The three
subjects were there -- an environment, a pinning decision, a test layout --
but each was three sentences of one connected setup narrative, and the
extractor read the narrative rather than its parts.

What changed is separability, not subject count: each paragraph now leads
with its own named artifact (`.venv`, the lockfile, the tests tree) and
carries its own consequence, so a reader dropping into any one of them meets
a self-contained subject. The arms grew from 1.1 KB to about 1.7 KB to pay
for that, which is inside `SHORT_SINGLE_TOPIC_BAND` and deliberately nowhere
near its ceiling.

The band is the fixture's whole point, so it is the last thing to spend. If
a floor arm cannot afford a floor inside 1-4 KB at a given model, the honest
report is `NO FLOOR` and a note that this size does not reach the question --
not a fourth subject, and not a longer document.

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
    # The `lesson` pair is the first written to a LENGTH target (1-4 KB) and
    # the first that is not a meeting on either arm. See the band section in
    # the module docstring for what that buys, and the separability section
    # for why each paragraph names its own artifact.
    PairedSource(
        pair_id="lesson",
        role=TREATMENT,
        arm="lesson",
        language="EN",
        source_title="Lesson 3: Setting Up a Python Project",
        text=(
            "In this lesson you set up a project the way the rest of the "
            "course expects it.\n\n"
            "A virtual environment is a directory holding one project's "
            "installed packages together with a link to the interpreter that "
            "made it. Installing into it changes nothing outside it, which is "
            "what lets two projects on one machine depend on different "
            "versions of the same library without either noticing the other. "
            "It is called .venv by convention and kept out of version "
            "control, so a fresh checkout never carries one. It holds no work "
            "of its own, only packages that can be fetched again, so deleting "
            "it and building it back is the shortest way out of an install "
            "that has gone wrong.\n\n"
            "The course pins exact versions in a lockfile rather than leaving "
            "ranges in the dependency list. A lockfile records what the "
            "resolver actually chose on the day it ran, including the "
            "packages nothing asked for directly, and it is committed next to "
            "the code. The reason is diagnostic: when something that worked "
            "stops working, the difference between two lockfiles names the "
            "package that moved, while a range like >=2.4 resolves to "
            "whatever exists at the moment it is read and leaves nothing "
            "behind to compare. Two people cloning a month apart get the same "
            "bytes.\n\n"
            "The tests tree mirrors the package tree file for file, so "
            "tests/parsing/test_tokens.py sits opposite "
            "src/parsing/tokens.py. The mirror is a reporting device more "
            "than a filing one: a module with nothing opposite it is an empty "
            "slot anybody can see, and one flat directory named by feature "
            "hides exactly that. A test file with no module opposite it is "
            "the same signal reversed, and usually means something was "
            "deleted without its test. Nothing checks the mirror, so it "
            "survives only while every new module arrives with its "
            "counterpart.\n"
        ),
    ),
    PairedSource(
        pair_id="lesson",
        role=FLOOR,
        arm="untitled",
        language="EN",
        source_title="Virtual environments, pinned versions, and test layout",
        text=(
            "A virtual environment is a directory holding one project's "
            "installed packages together with a link to the interpreter that "
            "made it. Installing into it changes nothing outside it, which is "
            "what lets two projects on one machine depend on different "
            "versions of the same library without either noticing the other. "
            "It is called .venv by convention and kept out of version "
            "control, so a fresh checkout never carries one. It holds no work "
            "of its own, only packages that can be fetched again, so deleting "
            "it and building it back is the shortest way out of an install "
            "that has gone wrong.\n\n"
            "Exact versions are pinned in a lockfile rather than left as "
            "ranges in the dependency list. A lockfile records what the "
            "resolver actually chose on the day it ran, including the "
            "packages nothing asked for directly, and it is committed next to "
            "the code. The reason is diagnostic: when something that worked "
            "stops working, the difference between two lockfiles names the "
            "package that moved, while a range like >=2.4 resolves to "
            "whatever exists at the moment it is read and leaves nothing "
            "behind to compare. Two people cloning a month apart get the same "
            "bytes.\n\n"
            "The tests tree mirrors the package tree file for file, so "
            "tests/parsing/test_tokens.py sits opposite "
            "src/parsing/tokens.py. The mirror is a reporting device more "
            "than a filing one: a module with nothing opposite it is an empty "
            "slot anybody can see, and one flat directory named by feature "
            "hides exactly that. A test file with no module opposite it is "
            "the same signal reversed, and usually means something was "
            "deleted without its test. Nothing checks the mirror, so it "
            "survives only while every new module arrives with its "
            "counterpart.\n"
        ),
    ),
)

NEGATIVE_CONTROL_OBJECTS = 1
"""The CORRECT object count for `NEGATIVE_CONTROL`.

Written as a named constant rather than a literal `1` because the same number
means the opposite thing eleven lines away: `COLLAPSE_SIZE` in the harness is
the defect, this is the right answer, and a reader who meets a bare `1` in
both places has no way to tell them apart."""

NEGATIVE_CONTROL = PairedSource(
    pair_id="negative-control",
    role=FLOOR,
    arm="single-subject",
    language="EN",
    source_title="Replica Lag",
    # Deliberately NOT in `SOURCES`: `pairs()` would reject a lone arm, and it
    # SHOULD -- an unpaired fixture read through the paired verdict logic is
    # the reading #522 warns is not a finding. The `role` field is inert here
    # for the same reason: nothing reads it, since `pairs()` never sees this
    # source. `FLOOR` is written because holding is this fixture's job.
    text=(
        "Replica lag is the delay between a write being acknowledged on the "
        "primary database and that same write becoming visible on a replica. "
        "It is counted in seconds of wall clock rather than in rows: a "
        "replica four seconds behind is serving the state the primary held "
        "four seconds ago, whatever number of writes happened in between. "
        "The figure is reported by the replica itself, which makes it a "
        "claim about the replica's own clock as much as about the data.\n\n"
        "The lag grows whenever a replica cannot apply changes as quickly as "
        "the primary produces them. A long transaction on the primary is the "
        "ordinary cause, because it arrives as a single unit and the replica "
        "works through it in one thread while everything behind it waits. "
        "Bulk deletes and index builds show the same shape for the same "
        "reason, and none of it is a failure state: a healthy system in the "
        "middle of a heavy import looks exactly like this.\n\n"
        "For a reader the consequence is that a query issued immediately "
        "after a write may not see it. The row exists on the primary and "
        "does not yet exist on the replica, so a page that saves a change "
        "and then re-reads it from a replica can show the value the person "
        "just replaced. The gap closes on its own, which is what makes it "
        "hard to see from a bug report.\n"
    ),
)
"""A genuinely single-subject source, where ONE object is the right answer.

The `mcp-launch` class named in `_drop_source_title_twins`: a title naming
one thing and a body about that one thing, whose lone object is expected to
restate the title. That is the twin the rule would drop if its floor
(`concept.py`, `len(results) <= 1` and the all-twins case) were removed or
narrowed, and dropping it emits `[]` for genuine content.

The title travels through `source_title`, not as an H1 inside the text,
because `source_title` is precisely what the rule compares against -- putting
it in the body would test the model's reading of a heading instead of the
rule's input.

## Why it is a definition and not a how-to

This fixture was rewritten, and the reason belongs next to it. The first
version was titled `Nightly Index Rebuild` and described a scheduled job.
It held at one object in 5 of 5 live runs and the probe reported "no false
positive" -- but the lone object came back as a `Procedure` every time, and
`_is_twin` exempts `Procedure` BY TYPE (#413):

    result.type != _TWIN_EXEMPT_TYPE and _normalize_title(...) == ...

A `Procedure` is never a twin, whatever its title, so that object would have
survived with BOTH of the rule's floors deleted. The control was reporting a
clean bill of health on a case that could not have failed. It would have
stayed green through the exact change it exists to catch.

So the single subject here is a THING, not a set of steps: no procedure, no
numbered list, no imperative verb, nothing the prompt's own tie-break would
route to `Procedure` ("a page explaining what a tool is and how it works is
a Concept; a page of steps for installing that tool is a Procedure"). What
the type has to be is only this: NOT the exempt one. The harness no longer
takes that on trust -- `title_twin_runs` is type-aware, and a run whose lone
object is exempt is reported as carrying no floor evidence rather than as a
pass."""

AXES: dict[str, str] = {
    "producto": "meeting register",
    "versioning": "meeting register",
    "anuncio": "whether the opening sentence enumerates the source's topics",
    "lesson": (
        "short lesson framing: an umbrella-topic title plus an opening "
        "sentence naming the lesson"
    ),
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
    "lesson": (
        "a short lesson titled with an umbrella topic collapses to a single "
        "object echoing that title, even though its body covers three "
        "distinct sub-subjects"
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

This is a property of the fixtures, not a general rule about extraction. The
meeting pairs name people (Ana, Marta, Luis, Priya, Dan, Rocío, Iván) and
teams purely as participants in what happened; `lesson` and
`NEGATIVE_CONTROL` name no people at all, so the rule holds there a
fortiori. No arm anywhere in this file is ABOUT a person. The
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


SHORT_SINGLE_TOPIC_BAND: tuple[int, int] = (1024, 4096)
"""Inclusive byte band the short single-topic fixtures are written to.

Roughly 1-4 KB, the size a course lesson file actually is. It is checked
rather than assumed for the same reason `MAX_LENGTH_SKEW` is: the band is the
whole point of these fixtures, and an edit that drifts out of it turns a
measurement about the reported shape into a measurement about some other
shape, silently."""

SHORT_BAND_IDS: frozenset[str] = frozenset({"lesson"})
"""Which pairs are held to `SHORT_SINGLE_TOPIC_BAND`.

The three meeting/flat pairs are NOT: they were written before the band
existed, they sit at 600-800 B, and rewriting them to fit would change
fixtures a stored before/after comparison depends on. Adding an id here is
how a new pair opts in."""


def short_band_sources() -> tuple[PairedSource, ...]:
    """Every fixture held to `SHORT_SINGLE_TOPIC_BAND`, pairs and control."""
    return (
        *(s for s in SOURCES if s.pair_id in SHORT_BAND_IDS),
        NEGATIVE_CONTROL,
    )


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
