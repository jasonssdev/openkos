"""The `revisions` report renderer (#1014 piece (a), Phase B re-plan, Slice
P7a): a PURE function over `revisions.RevisionPlan`/`revisions.RevisionOutcome`
-shaped inputs, implementing design.md Decision 8 as revised by Decision B4
(the counts line, the no-candidates message, and the experimental stderr
notice's wording changed for Phase B; grouping, line shapes, and the
default/`--all` filter did not).

Kept in `application/` rather than a private `cli/main.py` renderer: it
takes only service-layer result types as input, no CLI/typer types, so it
is unit-testable without a CLI context -- the same reasoning ADR-0018
already applies to `application/revisions.py` itself (tasks.md, Slice P7a
preamble).

Scope: this module renders only the "counts line + groups" portion of
Decision 8's stdout sequence (items 3 and 5) plus the two empty-result
messages. The workspace-root line (item 1) and the served/judged summary
line (item 2) are printed directly by the `revisions` verb (Slice P7b),
since they need the workspace root and are not part of what P7a's task list
tests; the truncation notice (item 4) is `decision_revision.
revision_truncation_notice`, already a Phase A leaf function the verb calls
directly, unchanged.

`excluded` (the "unreadable relations" count) is not a field of `RevisionPlan`
-- it is `revisions.DecisionSet.bad_relations`, computed one step upstream by
`load_decisions` -- so it is threaded through as an explicit keyword
argument rather than read off `plan`."""

from collections import defaultdict
from typing import Final, Literal, cast

from openkos.application import revisions
from openkos.model import okf
from openkos.resolution import decision_revision
from openkos.resolution.decision_revision import DecisionDate, RevisionVerdict
from openkos.state.revision_findings import RevisionFinding

_NO_CANDIDATES_MESSAGE: Final = (
    "No candidate Decision pairs found (need two Decisions from different "
    "Sources with similar embeddings)."
)
"""Decision B4's revised wording -- NOT Phase A's "similar subjects" text,
since candidates are now blocked by embedding similarity, never by the
(dropped) subject pass."""

_NO_REVISIONS_MESSAGE: Final = "No decision revisions found."

_REMEDY_CLAUSE: Final = "Run 'openkos reindex' to include them."

_VERDICT_ORDER: Final[dict[str, int]] = {
    "reverses": 0,
    "refines": 1,
    "reaffirms": 2,
    "unrelated": 3,
}
"""Report order within a group (design.md Decision 8): REVERSES, REFINES,
REAFFIRMS, then UNRELATED (shown only under `--all`)."""

_VERB_BY_VERDICT: Final[dict[str, str]] = {
    "reverses": "reversed by",
    "refines": "refined by",
    "reaffirms": "reaffirmed by",
    "unrelated": "unrelated to",
}

_ACTIONABLE_VERDICTS: Final = frozenset({"reverses", "refines"})
"""The two verdicts eligible for the unquoted-but-still-shown carve-out
(design.md Decision 8's default filter) and for the quote detail lines."""

_DIRECTION_REASON_WORDING: Final[dict[str, str]] = {
    "missing": "no event_date",
    "multiple": "2+ distinct event_dates",
    "none-reached": "no Source reached",
    "equal": "same event_date",
}
"""The four exact `[direction unknown: <id>: <state>]` phrasings (design.md
Decision 8), keyed by `Direction.reason` -- every non-`"dated"` value
`pair_direction` can return."""


def _counts_line(*, missing: int, stale: int, excluded: int) -> str | None:
    """The counts line (design.md Decision B4): `"{missing} Decision(s)
    without an embedding; {stale} changed since the last reindex; {m}
    excluded (unreadable relations). Run 'openkos reindex' to include
    them."`, with every zero-valued clause omitted and the WHOLE line
    omitted (`None`) when all three are zero. The remedy clause is appended
    only when `missing + stale > 0` -- an `excluded`-only run has nothing a
    reindex would fix, so it is never printed."""
    clauses: list[str] = []
    if missing:
        clauses.append(f"{missing} Decision(s) without an embedding")
    if stale:
        clauses.append(f"{stale} changed since the last reindex")
    if excluded:
        clauses.append(f"{excluded} excluded (unreadable relations)")
    if not clauses:
        return None
    line = "; ".join(clauses) + "."
    if missing + stale > 0:
        line = f"{line} {_REMEDY_CLAUSE}"
    return line


class _FindingView:
    """One finding, normalized to a single shape regardless of whether it
    came from `plan.served` (a persisted `RevisionFinding`, string verdict,
    ISO-string dates) or `outcome.results` (a freshly judged
    `RevisionVerdict`, enum verdict, `DecisionDate` objects) -- the renderer
    below reads only this shape, never branching on the source type again
    past construction."""

    __slots__ = (
        "confidence",
        "date_states",
        "dates",
        "direction",
        "malformed",
        "pair_ids",
        "quotes",
        "rationale",
        "served",
        "verdict",
    )

    def __init__(
        self,
        *,
        pair_ids: tuple[str, str],
        verdict: str,
        confidence: float,
        rationale: str,
        quotes: tuple[str | None, str | None],
        direction: decision_revision.Direction,
        dates: tuple[str | None, str | None],
        date_states: tuple[str, str],
        malformed: bool,
        served: bool,
    ) -> None:
        self.pair_ids = pair_ids
        self.verdict = verdict
        self.confidence = confidence
        self.rationale = rationale
        self.quotes = quotes
        self.direction = direction
        self.dates = dates
        self.date_states = date_states
        self.malformed = malformed
        self.served = served


def _view_from_finding(finding: RevisionFinding) -> _FindingView:
    """Reconstruct `Direction` from a persisted `RevisionFinding`'s stored
    `dates`/`date_states` via `pair_direction` -- the same single authority
    `RevisionVerdict.direction` delegates to (ADR-0025), so a served finding
    and a freshly judged one render identically."""
    date_a = DecisionDate(
        value=okf.parse_event_when(finding.dates[0]) if finding.dates[0] else None,
        state=finding.date_states[0],  # type: ignore[arg-type]
    )
    date_b = DecisionDate(
        value=okf.parse_event_when(finding.dates[1]) if finding.dates[1] else None,
        state=finding.date_states[1],  # type: ignore[arg-type]
    )
    direction = decision_revision.pair_direction(
        finding.pair_ids[0], date_a, finding.pair_ids[1], date_b
    )
    return _FindingView(
        pair_ids=finding.pair_ids,
        verdict=finding.verdict,
        confidence=finding.confidence,
        rationale=finding.rationale,
        quotes=finding.quotes,
        direction=direction,
        dates=finding.dates,
        date_states=finding.date_states,
        malformed=False,
        served=True,
    )


def _view_from_verdict(verdict: RevisionVerdict) -> _FindingView:
    dates: tuple[str | None, str | None] = (
        okf.format_event_when(verdict.dates[0].value)
        if verdict.dates[0].value is not None
        else None,
        okf.format_event_when(verdict.dates[1].value)
        if verdict.dates[1].value is not None
        else None,
    )
    return _FindingView(
        pair_ids=verdict.pair_ids,
        verdict=verdict.verdict.value,
        confidence=verdict.confidence,
        rationale=verdict.rationale,
        quotes=verdict.quotes,
        direction=verdict.direction,
        dates=dates,
        date_states=(verdict.dates[0].state, verdict.dates[1].state),
        malformed=verdict.malformed,
        served=False,
    )


def _group_key_and_partner(view: _FindingView) -> tuple[str, str]:
    """design.md Decision 8: a KNOWN direction groups under the earlier
    Decision, naming the later one; an UNKNOWN direction groups under
    `pair_id_0`, naming `pair_id_1`."""
    if view.direction.holder is not None:
        # `Direction`'s own contract: `earlier` is set together with
        # `holder`, never independently (`pair_direction`'s only two
        # `holder is not None` branches set both). `cast`, not `assert`
        # (S101, banned outside `tests/`) -- the same pattern the Phase A
        # leaf itself uses for an analogous invariant.
        return cast(str, view.direction.earlier), view.direction.holder
    return view.pair_ids[0], view.pair_ids[1]


def _has_unverified_quote(view: _FindingView) -> bool:
    return view.quotes[0] is None or view.quotes[1] is None


def _is_displayed(view: _FindingView, *, show_all: bool) -> bool:
    """design.md Decision 8's default filter: `is_reportable_revision`
    results, plus any REVERSES/REFINES with an unverified quote (shown,
    flagged not actionable). `--all` shows everything, including UNRELATED,
    low-confidence, and malformed results."""
    if show_all:
        return True
    if view.malformed:
        return False
    if decision_revision.is_reportable_revision(
        view.verdict, view.confidence, view.quotes[0], view.quotes[1]
    ):
        return True
    return view.verdict in _ACTIONABLE_VERDICTS and _has_unverified_quote(view)


def _quote_or_placeholder(quote: str | None, concept_id: str) -> str:
    return quote if quote is not None else f"(no verbatim quote from {concept_id})"


def _render_finding(view: _FindingView) -> list[str]:
    group_key, partner = _group_key_and_partner(view)
    verb = _VERB_BY_VERDICT[view.verdict]
    tag = f"[{view.verdict.upper()}]"
    unquoted = view.verdict in _ACTIONABLE_VERDICTS and _has_unverified_quote(view)
    unquoted_suffix = " [not actionable: unquoted]" if unquoted else ""

    if view.direction.holder is not None:
        partner_date = view.dates[view.pair_ids.index(partner)]
        head = (
            f"{tag} {verb} {partner} on {partner_date} "
            f"(confidence {view.confidence:.2f}){unquoted_suffix}"
        )
    else:
        wording = _DIRECTION_REASON_WORDING[view.direction.reason]
        head = (
            f"{tag} with {partner} (confidence {view.confidence:.2f})"
            f" [direction unknown: {partner}: {wording}]{unquoted_suffix}"
        )

    lines = [f"  {head}"]
    if view.verdict in _ACTIONABLE_VERDICTS:
        if view.direction.holder is not None:
            earlier_id, later_id = group_key, partner
            earlier_quote = view.quotes[view.pair_ids.index(earlier_id)]
            later_quote = view.quotes[view.pair_ids.index(later_id)]
            lines.append(
                f"    earlier: {_quote_or_placeholder(earlier_quote, earlier_id)}"
            )
            lines.append(f"    later:   {_quote_or_placeholder(later_quote, later_id)}")
        else:
            id_0, id_1 = view.pair_ids
            lines.append(f"    {id_0}: {_quote_or_placeholder(view.quotes[0], id_0)}")
            lines.append(f"    {id_1}: {_quote_or_placeholder(view.quotes[1], id_1)}")
        lines.append(f"    rationale: {view.rationale}")
        lines.append("    next: openkos reconcile --from-findings")
    return lines


def _displayed_groups(
    views: list[_FindingView], *, show_all: bool
) -> list[tuple[str, list[_FindingView]]]:
    """The displayed findings as `(group key, findings)` in report order:
    groups sorted by key, then REVERSES, REFINES, REAFFIRMS, UNRELATED and
    confidence descending within a group. The human report and the `--json`
    array both read this, so they can never disagree on which findings exist
    or in what order."""
    groups: dict[str, list[_FindingView]] = defaultdict(list)
    for view in views:
        if _is_displayed(view, show_all=show_all):
            group_key, _ = _group_key_and_partner(view)
            groups[group_key].append(view)
    return [
        (
            group_key,
            sorted(
                groups[group_key],
                key=lambda view: (_VERDICT_ORDER[view.verdict], -view.confidence),
            ),
        )
        for group_key in sorted(groups)
    ]


def revisions_report(
    plan: revisions.RevisionPlan,
    outcome: revisions.RevisionOutcome,
    *,
    excluded: int = 0,
    show_all: bool = False,
) -> str:
    """Render the `revisions` report: the counts line (with the
    `openkos reindex` remedy), grouped findings (served plus freshly
    judged, normalized through `_FindingView`), and the two empty-result
    messages (design.md Decision 8/B4).

    `excluded` is `revisions.DecisionSet.bad_relations`, threaded in by the
    caller (Slice P7b) since it is not a field of `RevisionPlan`."""
    if not plan.candidate_plan.candidates:
        return _NO_CANDIDATES_MESSAGE

    views = [_view_from_finding(finding) for finding in plan.served] + [
        _view_from_verdict(verdict) for verdict in outcome.results
    ]
    if not views:
        return _NO_REVISIONS_MESSAGE

    lines: list[str] = []
    counts = _counts_line(
        missing=len(plan.coverage.missing),
        stale=len(plan.coverage.stale),
        excluded=excluded,
    )
    if counts is not None:
        lines.append(counts)
        lines.append("")

    for group_key, ordered in _displayed_groups(views, show_all=show_all):
        lines.append(group_key)
        for view in ordered:
            lines.extend(_render_finding(view))
        lines.append("")

    if not lines:
        return _NO_REVISIONS_MESSAGE
    return "\n".join(lines).rstrip("\n") + "\n"


# -- The `--json` machine object (#1739) -------------------------------------

JSON_SCHEMA_VERSION: Final = 1
"""The `revisions --json` contract version. Bump on a breaking change to a
field name, type or meaning; adding a field is not breaking."""

JsonStatus = Literal[
    "completed", "no_decisions", "vectors_absent", "model_mismatch", "declined"
]


def _relation(view: _FindingView) -> str | None:
    """The relation `reconcile --from-findings` would offer for this finding:
    only a DIRECTED `reverses`/`refines` has one. Never a relation written."""
    if view.direction.holder is None:
        return None
    return decision_revision.RELATION_FOR_VERDICT.get(
        decision_revision.RevisionVerdictValue(view.verdict)
    )


def _json_entry(view: _FindingView) -> dict[str, object]:
    direction = view.direction
    return {
        "decisions": [
            {
                "id": view.pair_ids[index],
                "event_date": view.dates[index],
                "date_state": view.date_states[index],
                "quote": view.quotes[index],
            }
            for index in (0, 1)
        ],
        "verdict": view.verdict,
        "relation": _relation(view),
        "untyped_change": view.verdict in _ACTIONABLE_VERDICTS
        and direction.holder is None,
        "direction": {
            "source": "event_dates" if direction.holder is not None else "none",
            "earlier": direction.earlier,
            "later": direction.holder,
            "reason": direction.reason,
        },
        "confidence": view.confidence,
        "rationale": view.rationale,
        "served": view.served,
        "malformed": view.malformed,
        "actionable": not view.malformed
        and decision_revision.is_actionable_revision(
            view.verdict, view.confidence, view.quotes[0], view.quotes[1]
        ),
    }


def revisions_json_report(
    plan: revisions.RevisionPlan,
    outcome: revisions.RevisionOutcome,
    *,
    openkos_version: str,
    excluded: int = 0,
    show_all: bool = False,
) -> dict[str, object]:
    """The `revisions --json` object for a judged run: the same findings, in
    the same order, as `revisions_report` lists (`show_all` widens both), plus
    run counts that `show_all` never changes. Pure and I/O-free."""
    views = [_view_from_finding(finding) for finding in plan.served] + [
        _view_from_verdict(verdict) for verdict in outcome.results
    ]
    return {
        "schema_version": JSON_SCHEMA_VERSION,
        "openkos_version": openkos_version,
        "status": "completed",
        "partial": outcome.failure is not None,
        "counts": {
            "candidate_pairs": len(plan.candidate_plan.candidates),
            "served": len(plan.served),
            "judged": len(outcome.results),
            "malformed": sum(1 for view in views if view.malformed),
            "without_embedding": len(plan.coverage.missing),
            "changed_since_reindex": len(plan.coverage.stale),
            "excluded_unreadable_relations": excluded,
        },
        "revisions": [
            _json_entry(view)
            for _, ordered in _displayed_groups(views, show_all=show_all)
            for view in ordered
        ],
    }


def revisions_json_status(
    status: JsonStatus, *, openkos_version: str
) -> dict[str, object]:
    """The `revisions --json` object for a run that never judged (no
    Decisions, vectors absent, model mismatch, declined): valid JSON with
    `counts` null and no revisions, so a pipe never reads an empty stdout."""
    return {
        "schema_version": JSON_SCHEMA_VERSION,
        "openkos_version": openkos_version,
        "status": status,
        "partial": False,
        "counts": None,
        "revisions": [],
    }
