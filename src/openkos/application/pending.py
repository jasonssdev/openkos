"""Shared READ predicates over pending contradiction and identity findings
(issue #995, PR 1 of MVP 3's "prerequisite zero"): whether a proposal has
been persisted, whether a human already ruled on it, and whether that ruling
is still in force.

Promoted here from `cli/main.py`, where three of these four
(`current_finding_digest`, `is_contradiction_declined`,
`is_group_kept_distinct`) had a second, FORKED copy in `cli/next_action.py`.
That fork existed for a circular-import reason each forked docstring named
explicitly: `cli/main.py` imports `cli/next_action.py` to call
`next_action()`, so the reverse import would have been circular. Both
copies' docstrings promised "the two copies must stay behaviourally
identical" -- a promise nothing checked
(`tests/unit/application/test_layering.py::
test_shared_read_predicates_are_never_forked` is what makes it checkable
now). `persisted_findings` had no fork, but shares the same store and the
same caller relationship (it is `current_finding_digest`'s own caller), so
it is promoted alongside the other three rather than left as the one
copy still living in an adapter.

Moving all four here resolves the circular import for good, rather than
merely deduplicating two copies into one adapter-side location:
`application/` may not import `openkos.cli`
(`test_application_modules_never_import_cli_typer_or_rich`), so both
`cli/main.py -> application/pending.py` and
`cli/next_action.py -> application/pending.py` are legal, acyclic
directions -- unlike the `cli/next_action.py -> cli/main.py` direction the
fork was invented to avoid.

All four names are PUBLIC (no leading underscore): every module outside
`application/` is forbidden from reaching a private name on an
`application/*` module
(`test_application_private_names_are_never_consumed_across_the_boundary`,
issue #974), and both `cli/main.py` and `cli/next_action.py` are legitimate
callers across that boundary.

A signature divergence between the two forked `is_contradiction_declined`
copies is resolved here in favour of the `cli/main.py` shape:
`(layout, pair_ids, merged_absorbed_id)` rather than
`(layout, finding: findings.PersistedFinding)`. The `main.py` shape is the
more general one -- it is the only shape that can also serve the
operator-supplied `--decline`/`--reopen` pair, which is not a
`PersistedFinding`. `cli/next_action.py`'s call site now unpacks
`finding.pair_ids, finding.merged_absorbed_id` itself, exactly as
`cli/main.py`'s own call sites already did."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from openkos import config
from openkos.bundle import decisions as bundle_decisions
from openkos.model import okf
from openkos.state import derived, findings
from openkos.state.vectorstore import content_hash


def current_finding_digest(bundle_dir: Path) -> Callable[[str], str | None]:
    """A `state.findings.open_findings`-compatible `current_digest`
    callback: reads `input_ref` as a concept id and content-hashes its
    CURRENT bytes (design Decision 2), mirroring `cli.curate.
    _finding_input_digests`'s own read. An unreadable or non-file
    `input_ref` (a merged-body candidate's synthetic ledger-snapshot
    label, or a concept removed since the finding was recorded) answers
    `None` -- "cannot currently determine", never evidence of drift
    (`state.findings._is_stale`'s own contract)."""

    def _digest(input_ref: str) -> str | None:
        try:
            raw = okf.concept_path_for(input_ref, bundle_dir).read_bytes()
        except OSError:
            return None
        return content_hash(raw)

    return _digest


def persisted_findings(
    layout: config.WorkspaceLayout,
) -> tuple[findings.PersistedFinding, ...]:
    """Every persisted finding, with `stale` resolved against current
    bundle bytes -- the single read of `.openkos/findings.db` this module
    owns, shared by every adapter that needs the open/stale/declined
    predicate (`cli.main`'s `--declined` view, `status` (#598), and
    `cli.next_action`'s `open_contradictions`) so it is never reimplemented
    per caller.

    `path.exists()` is checked BEFORE `derived.open_derived_connection`,
    which would otherwise lazily create an empty file and break
    `config.WorkspaceLayout.findings_db_path`'s own pure-derivation
    contract ("this property never creates anything on disk by itself") --
    the same guard `vector_store_is_empty` uses for `vectors_db_path`. A
    workspace where the Contradictions stage has never persisted anything
    answers `()` rather than raising, and stays free of a stray
    `findings.db`."""
    if not layout.findings_db_path.exists():
        return ()
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        return findings.open_findings(
            conn, current_digest=current_finding_digest(layout.bundle_dir)
        )
    finally:
        conn.close()


def is_contradiction_declined(
    layout: config.WorkspaceLayout,
    pair_ids: tuple[str, str],
    merged_absorbed_id: str | None,
) -> bool:
    """`True` iff a decision record for `pair_ids`/`merged_absorbed_id`
    exists and its `state` is `declined` (pending-work spec: "Declined
    Findings Are Hidden By Default").

    `pair_ids` is NOT re-sorted here: a live `ContradictionVerdict`'s own
    `pair_ids` already arrives sorted, by `find_contradictions`'s own
    contract (`resolution.contradiction._candidate_pairs`'s
    `tuple(sorted(pair))` dedup key). This is also the shape the
    operator-supplied `--decline`/`--reopen` pair needs, which is NOT
    guaranteed sorted -- callers serving that pair must sort it themselves
    before calling in, the same way `cli.main`'s own `--decline`/`--reopen`
    call site already does."""
    key = bundle_decisions.decision_key_for(pair_ids, merged_absorbed_id)
    for record in bundle_decisions.read_decisions(pair_ids[0], layout.bundle_dir):
        if record.decision_key == key:
            return record.state == "declined"
    return False


def is_group_kept_distinct(
    layout: config.WorkspaceLayout, member_ids: Sequence[str]
) -> bool:
    """`True` iff a human has ruled this exact member set distinct and has
    not reopened it (#797) -- the identity twin of
    `is_contradiction_declined`.

    `CandidateGroup.member_ids` arrives sorted, but this is also reachable
    from operator-supplied ids, so it sorts defensively rather than
    trusting the caller."""
    members = tuple(sorted(member_ids))
    key = bundle_decisions.identity_decision_key_for(members)
    for record in bundle_decisions.read_identity_decisions(
        members[0], layout.bundle_dir
    ):
        if record.decision_key == key:
            return record.state == "declined"
    return False
