"""Fixture SCHEMA for the decision-revision-detector harness (#1014 piece
(a), sub-change 3), plus a tiny synthetic example set used only by
`run_decision_revisions_eval.py --self-test`.

**This is not the real fixture.** The real one -- hand-written, dated
meeting notes in a domain deliberately NOT AMI (see this harness's README)
-- lives in the sibling `revision_fixture_library.py`, in this same schema;
T3 is the owner adjudicating every `LabelledPair.contested` case there
BEFORE any scoring runs. The synthetic set below exists only to give `--self-test`
something to run the real pipeline over with zero network calls -- it is
deliberately small, hand-solvable, and not meant to measure anything about
the production judge.

Three record types, mirroring `evals/contradictions/contradiction_fixtures.py`'s
own `ConceptDoc`/`LabelledPair` split:

- `SourceDoc`: one raw meeting/email/note. Its `event_date` (or `None`) is
  the harness's stand-in for the service's real `bundle.provenance` +
  `okf.read_event_date` resolution (Phase B, S6) -- `resolve_decision_date`
  below aggregates a Decision's OWN `source_ids` over these.
- `DecisionDoc`: one Decision, `title`/`body` verbatim as the real subject
  pass and judge both read it, plus the `source_ids` its date resolves
  from.
- `LabelledPair`: one owner-adjudicated verdict over two Decision ids
  (design.md's contract): the drafter labels by construction and FLAGS
  every contested case (`contested`); contested calls are settled BEFORE
  scoring, never after (project memory).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from openkos.resolution.decision_revision import DecisionDate

RevisionExpectation = Literal["REVERSES", "REFINES", "REAFFIRMS", "UNRELATED"]
"""The fixture's own label vocabulary -- upper-case on purpose, to keep a
labelled pair's `expected_verdict` visually distinct from the judge's own
lower-case wire values (`RevisionVerdictValue.REVERSES.value ==
"reverses"`, ...) it is scored against."""


@dataclass(frozen=True)
class SourceDoc:
    """One raw source a Decision may cite as one of its `source_ids`.
    `event_date` is `None` when the source itself carries no resolvable
    date."""

    source_id: str
    event_date: date | None


@dataclass(frozen=True)
class DecisionDoc:
    """One Decision the fixture materializes."""

    concept_id: str
    title: str
    body: str
    source_ids: tuple[str, ...]
    """Which `SourceDoc.source_id`s this Decision cites -- empty when the
    Decision is deliberately undated (the "undated" hard case)."""


@dataclass(frozen=True)
class LabelledPair:
    """One owner-adjudicated pair."""

    decision_ids: tuple[str, str]
    expected_verdict: RevisionExpectation
    expected_later_id: str | None
    """Whichever of `decision_ids` the fixture's OWN dates make later --
    `None` when direction is not established (undated/equal/multi-date
    sides), so the harness's `pair_direction` call over resolved dates is
    expected to return no holder. This is a check on DATES only, never on
    the judge's verdict -- direction is never read from a model reply
    (ADR-0025, `decision_revision.py`'s own module docstring)."""
    contested: bool
    note: str
    hard_case: str | None = None
    """Which hard-case shape (design.md / the task's "hard cases on
    purpose" list) this pair exists to probe, e.g. `"paraphrase"`,
    `"same-subject-unrelated"`, `"reaffirm-different-words"`,
    `"undated"`. `None` for an ordinary pair."""


@dataclass(frozen=True)
class Fixture:
    """One full fixture: every source, every Decision, every labelled
    pair. T2's real fixture is loaded into this same shape."""

    sources: tuple[SourceDoc, ...]
    decisions: tuple[DecisionDoc, ...]
    pairs: tuple[LabelledPair, ...]


def resolve_decision_date(
    decision: DecisionDoc, sources_by_id: dict[str, SourceDoc]
) -> DecisionDate:
    """Aggregate `decision.source_ids`' own `event_date`s into one
    `DecisionDate` -- the harness's fixture-only stand-in for the
    service's real date resolution (Phase B, S6). Pure; reads only its
    two arguments.

    - No source id resolves to a dated source (`source_ids` empty, every
      referenced id missing from `sources_by_id`, or every resolving
      source's own `event_date` is `None`): `state="missing"`.
    - Every resolving source agrees on exactly one date: `state="dated"`,
      that date.
    - Two or more DISTINCT dates across the resolving sources:
      `state="multiple"`.

    `"none-reached"` (a Phase B graph-traversal state, per
    `decision_revision.DateState`'s own docstring) never arises here --
    this fixture has no notion of an unreached Decision."""
    dates = {
        sources_by_id[source_id].event_date
        for source_id in decision.source_ids
        if source_id in sources_by_id
        and sources_by_id[source_id].event_date is not None
    }
    if not dates:
        return DecisionDate(value=None, state="missing")
    if len(dates) > 1:
        return DecisionDate(value=None, state="multiple")
    return DecisionDate(value=next(iter(dates)), state="dated")


# ---------------------------------------------------------------------------
# The tiny synthetic set `--self-test` runs the real pipeline over.
# ---------------------------------------------------------------------------

_SOURCE_EARLY = SourceDoc("sources/2026-01-05-standup", date(2026, 1, 5))
_SOURCE_LATE = SourceDoc("sources/2026-02-09-standup", date(2026, 2, 9))

_SOURCES: tuple[SourceDoc, ...] = (_SOURCE_EARLY, _SOURCE_LATE)

_DECISIONS: tuple[DecisionDoc, ...] = (
    # -- REVERSES, plus one run scripted to confuse it with REFINES --------
    DecisionDoc(
        "decisions/billing-tool-v1",
        "Billing Tool Choice",
        "We will use Stripe as the billing tool for all new invoices.",
        (_SOURCE_EARLY.source_id,),
    ),
    DecisionDoc(
        "decisions/billing-tool-v2",
        "Billing Tool Change",
        "We will use Braintree as the billing tool, replacing Stripe for "
        "all new invoices.",
        (_SOURCE_LATE.source_id,),
    ),
    # -- REFINES -------------------------------------------------------------
    DecisionDoc(
        "decisions/release-cadence-v1",
        "Release Cadence",
        "Releases will ship every two weeks on Thursdays.",
        (_SOURCE_EARLY.source_id,),
    ),
    DecisionDoc(
        "decisions/release-cadence-v2",
        "Release Cadence Update",
        "Releases will ship every two weeks on Thursdays, except during "
        "the December code freeze.",
        (_SOURCE_LATE.source_id,),
    ),
    # -- REAFFIRMS, in different words --------------------------------------
    DecisionDoc(
        "decisions/standup-time-v1",
        "Standup Time",
        "Daily standup will be held at 9:30 in the morning.",
        (_SOURCE_EARLY.source_id,),
    ),
    DecisionDoc(
        "decisions/standup-time-v2",
        "Standup Schedule",
        "Standup stays at 9:30am every day.",
        (_SOURCE_LATE.source_id,),
    ),
    # -- UNRELATED, same lexical subject ("office") -------------------------
    DecisionDoc(
        "decisions/office-snacks",
        "Office Snacks Vendor",
        "We will order office snacks from Fresh Bites going forward.",
        (_SOURCE_EARLY.source_id,),
    ),
    DecisionDoc(
        "decisions/office-seating",
        "Office Seating Chart",
        "The office seating chart will be reorganized by team next month.",
        (_SOURCE_LATE.source_id,),
    ),
    # -- REFINES, deliberately LOW lexical overlap (the missed-candidate
    # / paraphrase hard case: the subject pass below returns two subjects
    # with no shared tokens for this pair, on purpose) ----------------------
    DecisionDoc(
        "decisions/oncall-rotation-v1",
        "On-Call Rotation",
        "The on-call rotation will run weekly, one engineer per week.",
        (_SOURCE_EARLY.source_id,),
    ),
    DecisionDoc(
        "decisions/oncall-rotation-v2",
        "Escalation Schedule",
        "The on-call rotation will run weekly, but escalate to the "
        "secondary after 15 minutes unacknowledged.",
        (_SOURCE_LATE.source_id,),
    ),
    # -- REFINES, undated second side (the no-direction hard case) ----------
    DecisionDoc(
        "decisions/parking-policy-v1",
        "Parking Policy",
        "Employees may park in Lot A on any weekday.",
        (_SOURCE_EARLY.source_id,),
    ),
    DecisionDoc(
        "decisions/parking-policy-v2",
        "Parking Policy Update",
        "Parking in Lot A is now also open on weekends.",
        (),
    ),
    # -- No labelled pair at all: exists only to exercise the subject
    # pass's malformed-reply degrade in isolation, with no candidate or
    # judge side effect (it can never pair with anything: a Decision with
    # no subject is excluded from candidate generation entirely).
    DecisionDoc(
        "decisions/orphan-note",
        "Orphan Note",
        "This Decision is never paired with anything in this fixture.",
        (_SOURCE_EARLY.source_id,),
    ),
)

_PAIRS: tuple[LabelledPair, ...] = (
    LabelledPair(
        ("decisions/billing-tool-v1", "decisions/billing-tool-v2"),
        "REVERSES",
        "decisions/billing-tool-v2",
        contested=False,
        note="Braintree replaces Stripe outright.",
    ),
    LabelledPair(
        ("decisions/release-cadence-v1", "decisions/release-cadence-v2"),
        "REFINES",
        "decisions/release-cadence-v2",
        contested=False,
        note="Same cadence, narrowed by a freeze window.",
    ),
    LabelledPair(
        ("decisions/standup-time-v1", "decisions/standup-time-v2"),
        "REAFFIRMS",
        "decisions/standup-time-v2",
        contested=False,
        note="Same choice, restated in different words.",
        hard_case="reaffirm-different-words",
    ),
    LabelledPair(
        ("decisions/office-snacks", "decisions/office-seating"),
        "UNRELATED",
        "decisions/office-seating",
        contested=False,
        note="Both mention the office; neither bears on the other's choice.",
        hard_case="same-subject-unrelated",
    ),
    LabelledPair(
        ("decisions/oncall-rotation-v1", "decisions/oncall-rotation-v2"),
        "REFINES",
        "decisions/oncall-rotation-v2",
        contested=False,
        note="Same rotation, refined with an escalation rule -- scripted "
        "with paraphrased subjects so the candidate stage misses it.",
        hard_case="paraphrase",
    ),
    LabelledPair(
        ("decisions/parking-policy-v1", "decisions/parking-policy-v2"),
        "REFINES",
        None,
        contested=False,
        note="Same policy, extended to weekends; the second side is "
        "deliberately undated.",
        hard_case="undated",
    ),
)


def load_fixture() -> Fixture:
    """T1's tiny synthetic fixture -- NOT AMI, NOT the real T2 fixture.
    See this module's docstring."""
    return Fixture(sources=_SOURCES, decisions=_DECISIONS, pairs=_PAIRS)
