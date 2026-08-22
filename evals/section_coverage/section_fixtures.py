"""The two sources #793 compares, verbatim from the 0.2.8 E2E workspace.

Neither is invented. Both are the exact bytes that produced the outcome the
issue reports, copied from `~/openkos-e2e-028/raw/`, so a verdict measured
here is a verdict about the reported run rather than about a reconstruction
of it.

## Why these two and not a constructed pair

The other probes in `evals/` build matched pairs that vary one property.
This one does not, because the property under test is not a property of the
SOURCE at all -- it is whether a per-section coverage SIGNAL can tell a
section that produced an object from one that did not. The ground truth for
that lives in the extraction output of a given run, not in the fixture.

So what these two fixtures supply is the two regimes the signal has to work
in, both drawn from the same reported run:

- `helios-overview` is the source that LOST two of its four sections.
  Three objects came out of it: one Concept and two Person. `## Storage`
  and `## Components` produced nothing at all.
- `kickoff` is its neighbour in the same bundle and the issue's own
  counter-example -- comparable size (631 B against 533 B), and it produced
  eight objects, including a Decision quoting its datastore line verbatim.

The second is the floor. A signal that flags sections on `kickoff` as
freely as on `helios-overview` is not reading coverage, it is reading
markdown.

## The four sections of `helios-overview`, and what each is for

| section | reported outcome | what it tests |
| --- | --- | --- |
| the `#` title section | one Concept | the signal must stay QUIET |
| `## Storage` | nothing | the signal must FIRE |
| `## Components` | nothing | the signal must FIRE |
| `## Ownership` | two Person | the signal must stay QUIET |

A heading opens a section whatever its level, so the `#` title owns the
paragraph beneath it rather than that paragraph being a nameless preamble.
That is not a naming convenience: the paragraph is where the one Concept
came from, so it has to be a section the signal can report on, and giving
it the title's own heading is what lets a reader find it.

Two sections that must fire and two that must not, inside ONE source, is
what keeps the measurement from being vacuous. A run where all four fire
proves the signal cannot discriminate; a run where none fire proves it is
blind on the very source the issue was filed about. Both are reported as
verdicts in their own right rather than read as a pass.

The `## Ownership` row is the one worth watching. It produced two objects,
so it must come back covered -- but a `Person` object's body is written
about the person, and the source line is `Technical lead: Marta Ruiz.
Product: Tom Becker.` If no Person body reproduces that line, the section
reads as uncovered while having produced two objects, and the signal
over-fires on a CORRECT extraction. That is a design failure of the signal,
not of the model, and it is the specific thing this probe exists to catch
before any of it ships.
"""

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class Fixture:
    """One source, with the section outcomes the reported run observed.

    `must_fire` and `must_stay_quiet` name sections by their heading line
    exactly as it appears in `text`, `#` markers included, so a name can be
    grepped straight out of the fixture. They record what the E2E run
    reported, so they are
    EVIDENCE ABOUT THAT RUN, not ground truth for every run -- extraction
    is stochastic and #793 says so itself. The probe re-derives each run's
    own outcome from that run's objects and uses these only to say whether
    the reported case reproduced.
    """

    name: str
    title: str
    text: str
    must_fire: tuple[str, ...]
    must_stay_quiet: tuple[str, ...]
    reported_objects: tuple[str, ...] = ()
    """The objects the reported run produced, as `type: title`.

    The other half of the same adjudicated outcome `must_fire` records, and
    the half an ABLATION needs: `must_fire` says which sections produced
    nothing, this says what the run produced instead. The probe's `--ablate`
    mode keeps only these objects out of a stored healthy run and scores the
    remainder, so the reported failure is reconstructed from the model's own
    real texts rather than from section bodies handed back to themselves.

    `type: title` rather than the object texts, because this repo does not
    hold the 0.2.8 run's texts -- only which objects came out of it. Empty
    for a fixture whose reported run nobody itemised, and `--ablate` then
    says so rather than ablating against an empty set, which would delete
    every object and report a total loss as a finding.
    """


PREAMBLE: Final = "(preamble)"
"""How a section of body text that precedes the FIRST heading is named.

Neither fixture here has one -- both open with their `#` title, so every
line belongs to a heading-owned section. It is defined anyway because the
signal has to name such a section if a real source carries one, and a
sentinel invented at report time would differ from the one production uses.
"""


HELIOS_OVERVIEW: Final = Fixture(
    name="helios-overview",
    title="Helios Data Platform (HDP) — Overview",
    text="""# Helios Data Platform (HDP) — Overview

The Helios Data Platform, usually shortened to HDP, is the ingestion and
query layer used by the internal analytics team.

## Storage
HDP standardized on MySQL 8 as its primary datastore. The decision was
driven by the operations team's existing MySQL tooling.

## Components
- Ingest workers: pull source records and normalize them.
- Query API: serves the analytics dashboards.
- Redis cache: sits in front of the query API.

## Ownership
Technical lead: Marta Ruiz. Product: Tom Becker.
""",
    must_fire=("## Storage", "## Components"),
    must_stay_quiet=("# Helios Data Platform (HDP) — Overview", "## Ownership"),
    # The three the 0.2.8 run produced: one Concept from the title section
    # and two Person from `## Ownership`. Nothing from `## Storage` or
    # `## Components`, which is `must_fire` said from the object side.
    reported_objects=(
        "Concept: Helios Data Platform",
        "Person: Marta Ruiz",
        "Person: Tom Becker",
    ),
)


KICKOFF: Final = Fixture(
    name="kickoff",
    title="Project Helios — Kickoff Meeting",
    text="""# Project Helios — Kickoff Meeting
Date: 2026-01-12
Attendees: Marta Ruiz, Tom Becker, Priya Nair

## Context
Helios is our internal ingestion platform. It replaces the nightly batch
loader that has been failing roughly twice a month since October.

## Decisions
- Primary datastore will be PostgreSQL 16. Marta argued for it over MySQL
  because we already run Postgres in the billing service.
- Tom set the delivery deadline at 2026-03-15 for the first internal release.
- Priya owns the schema migration plan.

## Open questions
- Do we need a caching layer at all for v1?
- Who runs the on-call rotation once Helios is live?
""",
    # The floor source. The reported run produced eight objects from it and
    # the issue names no lost section, so nothing here is expected to fire.
    # It is listed as must-stay-quiet in full rather than left unannotated,
    # because a signal that fires here is over-firing and the probe should
    # say so in the same table as everything else.
    #
    # No `reported_objects`: the issue records that eight objects came out of
    # this source, not which eight, and an ablation needs the identities. It
    # is also the arm where an ablation would answer nothing -- there is no
    # lost section here to reconstruct.
    must_fire=(),
    must_stay_quiet=(
        "# Project Helios — Kickoff Meeting",
        "## Context",
        "## Decisions",
        "## Open questions",
    ),
)


def build_fixtures() -> tuple[Fixture, ...]:
    """The treatment source first, its floor second -- report order."""
    return (HELIOS_OVERVIEW, KICKOFF)
