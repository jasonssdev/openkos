"""Labelled candidate pairs for the auto-merge-safe-class measurement
harness (#1054, `design.md` "Fixture").

Ten probe classes -- five expected `different` (the negatives a false
auto-merge would delete a concept over) and five expected `same` (the
positives the mode exists to clear). Every class the owner's eligible
class admits (design D3: 2-member `SAME` groups, one shared OKF `type`, no
`cross_type_concern`) is represented; `transitivity` is excluded by
construction because it is cross-type.

- `week-apart` -- **the owner's named hard negative** (B4): one series
  title, disjoint provenance, dates seven days apart, different attendees
  and decisions. Scored on its own (never folded into `recurrence`) so R3
  can check it by probe name.
- `recurrence`, `asym-recurrence` (including `grupo-calidad-datos`),
  `event-same`, `asym-same` -- imported from
  `evals/adjudication/adjudication_fixtures.py` by reference, not copied:
  the labels these classes carry are the ones #796/#869 already measured,
  and importing keeps them from drifting out of step with that harness.
- `namesake-person` -- two different people sharing one name, disjoint
  affiliations: title identity is strong evidence for a Person (the
  `person-same` control), but it is not proof, and a namesake pair must
  never auto-merge.
- `aspect-or-part` -- imported `part-whole` and `aspect-of` pairs
  (same-type only, where D3 admits them), relabelled onto one probe: the
  part-whole exclusion the adjudication rubric already states.
- `reingest-dup` -- the re-ingest accumulation case (#772): one source
  compiled twice, SHARED `provenance`. The class the mode exists to clear
  most directly.
- `person-same`, `alias-same` -- new pairs (not the adjudication module's,
  which carry only one pair each and this design's minimums ask for two):
  identical name for a Person, and one entity under two names.

Labels are CONSTRUCTED, not adjudicated -- a wrong verdict is a
rubric-consistency failure, read that way (mirrors
`adjudication_fixtures.py`'s own discipline). Bodies are short: adjudication
sends every member's full body, so length changes what the harness can
afford to run, and none of the signals here need length to be legible.

No private corpus content: every document below is invented and
de-identified.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

_ADJUDICATION_DIR = Path(__file__).resolve().parents[1] / "adjudication"
if str(_ADJUDICATION_DIR) not in sys.path:
    # Appended after any path the importing runner already inserted at 0,
    # mirroring `run_adjudication_eval.py`'s own append-not-insert
    # discipline -- this module must never shadow a same-named module
    # closer to whatever entry point imports it.
    sys.path.append(str(_ADJUDICATION_DIR))

from adjudication_fixtures import PAIRS as _ADJUDICATION_PAIRS  # noqa: E402


@dataclass(frozen=True)
class FixtureDoc:
    """One document to materialize into the probe bundle."""

    concept_id: str
    """Bundle-relative path without `.md` -- the identity resolution uses."""
    okf_type: str
    title: str
    body: str
    provenance: tuple[str, ...] = ()
    """`provenance:` frontmatter targets (design D3, "every document
    carries ... a provenance: list so the cross-source flag is
    computable"). Never set directly by a pair constructor below except
    `reingest-dup`, whose two members must share ONE entry (one source
    compiled twice) -- every other pair gets a synthetic per-document
    default from `_doc` (one document, one source), which is the correct
    shape for a genuinely distinct document."""


@dataclass(frozen=True)
class LabelledPair:
    """Two documents the candidate tiers will group, and the verdict the
    eligible-class rule says they deserve."""

    left: FixtureDoc
    right: FixtureDoc
    probe: str
    """One of the ten classes in the module docstring."""
    expected: str
    """`same` or `different` -- this harness never labels `uncertain`: an
    ambiguous pair would not belong in a fixture meant to prove a
    ZERO-false-merge bar, on either side of it."""
    note: str
    """What makes the answer knowable from the two bodies alone."""


def _doc(
    concept_id: str,
    okf_type: str,
    title: str,
    body: str,
    *,
    provenance: tuple[str, ...] | None = None,
) -> FixtureDoc:
    """One document, defaulting `provenance` to a single entry unique to
    `concept_id` -- correct for every class here except `reingest-dup`,
    which passes an explicit SHARED entry for both members."""
    return FixtureDoc(
        concept_id=concept_id,
        okf_type=okf_type,
        title=title,
        body=body,
        provenance=provenance
        if provenance is not None
        else (f"raw/{concept_id.replace('/', '-')}-source.md",),
    )


def _import(
    probe_source: str, *, rename_to: str | None = None
) -> tuple[LabelledPair, ...]:
    """Every `adjudication_fixtures.PAIRS` entry labelled `probe_source`,
    converted to this module's `LabelledPair`/`FixtureDoc` and relabelled
    to `rename_to` (defaulting to `probe_source` unchanged). Filters the
    PUBLIC `PAIRS` tuple by `.probe` rather than reaching for a private
    per-class tuple, so a rename or a reshuffle on that side cannot leave
    this import silently empty."""
    renamed = rename_to or probe_source
    converted = []
    for pair in _ADJUDICATION_PAIRS:
        if pair.probe != probe_source:
            continue
        converted.append(
            LabelledPair(
                left=_doc(
                    pair.left.concept_id,
                    pair.left.okf_type,
                    pair.left.title,
                    pair.left.body,
                ),
                right=_doc(
                    pair.right.concept_id,
                    pair.right.okf_type,
                    pair.right.title,
                    pair.right.body,
                ),
                probe=renamed,
                expected=pair.expected,
                note=pair.note,
            )
        )
    return tuple(converted)


# --------------------------------------------------------------------------- #
# week-apart -- the owner's named hard negative (B4)
# --------------------------------------------------------------------------- #

_WEEK_APART: Final[tuple[LabelledPair, ...]] = (
    LabelledPair(
        left=_doc(
            "events/quarterly-product-sync-1",
            "Event",
            "Quarterly Product Sync",
            "2026-04-07. Reviewed the pricing page redesign. Agreed to drop "
            "the annual-plan toggle from the first release. Attendees: Nora "
            "Fenwick, Ines Okafor.",
        ),
        right=_doc(
            "events/quarterly-product-sync-2",
            "Event",
            "Quarterly Product Sync",
            "2026-04-14. Reviewed the trial-extension request flow. Agreed "
            "to cap self-serve extensions at 14 days. Attendees: Nora "
            "Fenwick, Marcus Boyle.",
        ),
        probe="week-apart",
        expected="different",
        note=(
            "One series title, dates seven days apart, disjoint agendas and "
            "decisions, only one attendee shared. The owner's named negative "
            "(B4), verbatim shape."
        ),
    ),
    LabelledPair(
        left=_doc(
            "events/facilities-ops-review-1",
            "Event",
            "Facilities Ops Review",
            "2026-06-02. Walked the HVAC maintenance backlog. Agreed to "
            "prioritize the third-floor unit. Attendees: Petra Halvorsen, "
            "Yusuf Demirci.",
        ),
        right=_doc(
            "events/facilities-ops-review-2",
            "Event",
            "Facilities Ops Review",
            "2026-06-09. Walked the badge-access renewal queue. Agreed to "
            "batch renewals monthly instead of on request. Attendees: Petra "
            "Halvorsen, Greta Lindqvist.",
        ),
        probe="week-apart",
        expected="different",
        note=(
            "Same series title, one week apart, disjoint subject matter and "
            "decisions, only one attendee shared."
        ),
    ),
    LabelledPair(
        left=_doc(
            "events/client-checkin-1",
            "Event",
            "Client Check-in",
            "2026-08-11. Client raised concerns about onboarding speed. "
            "Agreed to send a revised timeline by Friday. Attendees: Renata "
            "Sousa, Tobias Klein.",
        ),
        right=_doc(
            "events/client-checkin-2",
            "Event",
            "Client Check-in",
            "2026-08-18. Client asked about the reporting export format. "
            "Agreed to ship a CSV option next sprint. Attendees: Renata "
            "Sousa, Wei Lam.",
        ),
        probe="week-apart",
        expected="different",
        note=(
            "Same series title, one week apart, disjoint topics and "
            "commitments, only one attendee shared. Three independent "
            "instances of the same hard-negative shape."
        ),
    ),
)


# --------------------------------------------------------------------------- #
# namesake-person -- two different people, one name, disjoint affiliations
# --------------------------------------------------------------------------- #

_NAMESAKE_PERSON: Final[tuple[LabelledPair, ...]] = (
    LabelledPair(
        left=_doc(
            "people/alex-kim-platform-engineer",
            "Person",
            "Alex Kim",
            "Platform engineer on the storage team at Meridian Systems. "
            "Owns the object-store migration.",
        ),
        right=_doc(
            "people/alex-kim-pastry-chef",
            "Person",
            "Alex Kim",
            "Head pastry chef at the Rosewood Bakery. Runs the seasonal "
            "menu and trains new bakers.",
        ),
        probe="namesake-person",
        expected="different",
        note=(
            "One name, two disjoint affiliations and roles with no overlap "
            "-- a namesake, not a duplicate. `person-same`'s identity "
            "signal (identical name) must not be sufficient on its own."
        ),
    ),
    LabelledPair(
        left=_doc(
            "people/sam-rivera-attorney",
            "Person",
            "Sam Rivera",
            "Immigration attorney at a small practice in Denver. Handles "
            "family-sponsorship cases.",
        ),
        right=_doc(
            "people/sam-rivera-guitarist",
            "Person",
            "Sam Rivera",
            "Touring guitarist for a jazz trio based in Lisbon. Releases "
            "one album every two years.",
        ),
        probe="namesake-person",
        expected="different",
        note=(
            "Same name, disjoint professions and locations, nothing "
            "connecting the two beyond the name itself."
        ),
    ),
)


# --------------------------------------------------------------------------- #
# aspect-or-part -- imported part-whole and aspect-of pairs, one probe
# --------------------------------------------------------------------------- #

_ASPECT_OR_PART: Final[tuple[LabelledPair, ...]] = _import(
    "part-whole", rename_to="aspect-or-part"
) + _import("aspect-of", rename_to="aspect-or-part")


# --------------------------------------------------------------------------- #
# reingest-dup -- one source compiled twice, shared provenance (#772)
# --------------------------------------------------------------------------- #

_REINGEST_DUP: Final[tuple[LabelledPair, ...]] = (
    LabelledPair(
        left=_doc(
            "concepts/deployment-checklist-a",
            "Concept",
            "Deployment Checklist",
            "Run migrations, flip the feature flag, then warm the cache "
            "before routing traffic.",
            provenance=("raw/deploy-notes.md",),
        ),
        right=_doc(
            "concepts/deployment-checklist-b",
            "Concept",
            "Deployment Checklist",
            "Run migrations first, flip the feature flag, and warm the "
            "cache before traffic is routed.",
            provenance=("raw/deploy-notes.md",),
        ),
        probe="reingest-dup",
        expected="same",
        note=(
            "The same source compiled twice: identical steps in the same "
            "order, worded slightly differently, sharing one provenance "
            "entry -- the re-ingest accumulation case (#772)."
        ),
    ),
    LabelledPair(
        left=_doc(
            "concepts/q1-okrs-a",
            "Concept",
            "Q1 OKRs",
            "Objective: grow activation rate. Key results: onboarding "
            "completion to 70%, time-to-first-value under 2 days.",
            provenance=("raw/q1-planning.md",),
        ),
        right=_doc(
            "concepts/q1-okrs-b",
            "Concept",
            "Q1 OKRs",
            "Objective: grow activation. KRs: 70% onboarding completion, "
            "time to first value under two days.",
            provenance=("raw/q1-planning.md",),
        ),
        probe="reingest-dup",
        expected="same",
        note="One planning document, compiled twice, one shared provenance entry.",
    ),
    LabelledPair(
        left=_doc(
            "concepts/incident-runbook-a",
            "Concept",
            "Incident Runbook",
            "Page the on-call engineer, open an incident channel, and post "
            "a status update within 15 minutes.",
            provenance=("raw/runbook-source.md",),
        ),
        right=_doc(
            "concepts/incident-runbook-b",
            "Concept",
            "Incident Runbook",
            "Page on-call, open an incident channel, post a status update "
            "inside 15 minutes.",
            provenance=("raw/runbook-source.md",),
        ),
        probe="reingest-dup",
        expected="same",
        note="Same runbook, two compilations, one shared provenance entry.",
    ),
)


# --------------------------------------------------------------------------- #
# imported classes -- recurrence, asym-recurrence, event-same, asym-same
# --------------------------------------------------------------------------- #

_RECURRENCE: Final[tuple[LabelledPair, ...]] = _import("recurrence")
_ASYM_RECURRENCE: Final[tuple[LabelledPair, ...]] = _import("asym-recurrence")
"""Includes `grupo-calidad-datos` (#869's wild shape, judged `same` 13 of 15
runs at 0.95 confidence today), imported verbatim by concept id."""
_EVENT_SAME: Final[tuple[LabelledPair, ...]] = _import("event-same")
_ASYM_SAME: Final[tuple[LabelledPair, ...]] = _import("asym-same")


# --------------------------------------------------------------------------- #
# person-same, alias-same -- new pairs (design.md's minimum of 2 each
# exceeds the single pair adjudication_fixtures.py carries per class)
# --------------------------------------------------------------------------- #

_PERSON_SAME: Final[tuple[LabelledPair, ...]] = (
    LabelledPair(
        left=_doc(
            "people/jordan-blake-a",
            "Person",
            "Jordan Blake",
            "Product designer. Leads the design system and the onboarding "
            "flow redesign.",
        ),
        right=_doc(
            "people/jordan-blake-b",
            "Person",
            "Jordan Blake",
            "Designs the onboarding flow and maintains the shared component library.",
        ),
        probe="person-same",
        expected="same",
        note=(
            "One name, one role, compatible facts -- for a Person, an "
            "identical name is strong evidence of one entity."
        ),
    ),
    LabelledPair(
        left=_doc(
            "people/taylor-morgan-a",
            "Person",
            "Taylor Morgan",
            "Backend engineer on the billing team. Owns the invoicing service.",
        ),
        right=_doc(
            "people/taylor-morgan-b",
            "Person",
            "Taylor Morgan",
            "Works on billing, specifically the invoicing service and its retry logic.",
        ),
        probe="person-same",
        expected="same",
        note="Same name, same team, compatible and overlapping facts.",
    ),
)

_ALIAS_SAME: Final[tuple[LabelledPair, ...]] = (
    LabelledPair(
        left=_doc(
            "concepts/task-queue",
            "Concept",
            "Task Queue",
            "The component that holds pending background jobs until a "
            "worker is free to run them.",
        ),
        right=_doc(
            "concepts/background-task-queue",
            "Concept",
            "Background Task Queue",
            "Holds background jobs waiting for a free worker, releasing "
            "them in submission order.",
        ),
        probe="alias-same",
        expected="same",
        note=(
            "One component under a short name and a more qualified one -- "
            "the same definition restated. Titles must share enough tokens "
            "to reach `find_candidates`' LOW-tier near-match at all (design "
            "D3): a wholly unrelated alias, however true to life, would "
            "never be NOMINATED as a candidate in production, so it has no "
            "place in a fixture meant to prove structural eligibility."
        ),
    ),
    LabelledPair(
        left=_doc(
            "concepts/rate-limiter",
            "Concept",
            "Rate Limiter",
            "Rejects or delays requests once a client exceeds its allowed "
            "call rate over a rolling window.",
        ),
        right=_doc(
            "concepts/api-rate-limiter",
            "Concept",
            "API Rate Limiter",
            "Delays or rejects calls once a client goes over its allotted "
            "rate for the current window.",
        ),
        probe="alias-same",
        expected="same",
        note="One mechanism under a short name and a qualified one, same behavior restated.",
    ),
)


PAIRS: Final[tuple[LabelledPair, ...]] = (
    _WEEK_APART
    + _RECURRENCE
    + _ASYM_RECURRENCE
    + _NAMESAKE_PERSON
    + _ASPECT_OR_PART
    + _REINGEST_DUP
    + _EVENT_SAME
    + _PERSON_SAME
    + _ALIAS_SAME
    + _ASYM_SAME
)

PROBES: Final[tuple[str, ...]] = (
    "week-apart",
    "recurrence",
    "asym-recurrence",
    "namesake-person",
    "aspect-or-part",
    "reingest-dup",
    "event-same",
    "person-same",
    "alias-same",
    "asym-same",
)

NEGATIVE_PROBES: Final[tuple[str, ...]] = (
    "week-apart",
    "recurrence",
    "asym-recurrence",
    "namesake-person",
    "aspect-or-part",
)
POSITIVE_PROBES: Final[tuple[str, ...]] = (
    "reingest-dup",
    "event-same",
    "person-same",
    "alias-same",
    "asym-same",
)


def documents(pairs: tuple[LabelledPair, ...] = PAIRS) -> tuple[FixtureDoc, ...]:
    """Every DISTINCT fixture document of `pairs`, in pair order. Mirrors
    `adjudication_fixtures.documents`: a document deliberately shared
    between pairs is fine (none are, here -- this class is 2-member-group
    only), but two DIFFERENT documents sharing a `concept_id` raises,
    since that collision would silently merge two probes into one
    candidate group."""
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
