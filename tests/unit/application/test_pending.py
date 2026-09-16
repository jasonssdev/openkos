"""Direct unit tests for `openkos.application.pending`: the four shared READ
predicates over `.openkos/findings.db` and `bundle/.state/decisions/**`
promoted from `cli/main.py` (and, for three of the four, deduplicated out of
a forked `cli/next_action.py` copy) for issue #995.

Mirrors `test_query_service.py`/`test_query_filing.py`'s posture: these
exercise the pure predicates directly against a real (tmp-path) workspace
and a real sqlite `findings.db`, never a CLI invocation. Fixtures write
decision sidecars and findings rows the same way `tests/unit/cli/
test_status.py`'s `_record_finding`/`_decline` helpers do, so a divergence
between this module's behaviour and what `status`/`contradictions` already
exercise would show up as a fixture mismatch, not just a passing test.
"""

from datetime import UTC, datetime
from pathlib import Path

from openkos import config
from openkos.application import pending
from openkos.bundle import decisions as bundle_decisions
from openkos.state import derived, findings


def _workspace(tmp_path: Path) -> config.WorkspaceLayout:
    config.write_config(tmp_path)
    return config.WorkspaceLayout(tmp_path)


def _record_finding(
    layout: config.WorkspaceLayout,
    *,
    pair_ids: tuple[str, str],
    merged_absorbed_id: str | None = None,
    verdict: str = "contradicts",
    confidence: float = 0.91,
) -> None:
    """Persist one contradiction finding directly via
    `state.findings.record_findings` -- mirrors `test_status.py`'s
    `_record_finding`: no CLI writer, no LLM call."""
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        findings.record_findings(
            conn,
            [
                findings.Finding(
                    pair_ids=pair_ids,
                    merged_absorbed_id=merged_absorbed_id,
                    verdict=verdict,
                    confidence=confidence,
                    rationale="Stub rationale.",
                    input_digests=(),
                )
            ],
        )
    finally:
        conn.close()


def _decide(
    layout: config.WorkspaceLayout,
    *,
    pair_ids: tuple[str, str],
    merged_absorbed_id: str | None = None,
    state: bundle_decisions.DecisionState,
) -> None:
    """Write one contradiction decision record directly via
    `bundle.decisions.write_decisions` -- mirrors `test_status.py`'s
    `_decline`, generalized to also cover the `open` (reopened) state."""
    bundle_decisions.write_decisions(
        pair_ids[0],
        layout.bundle_dir,
        records=[
            bundle_decisions.DecisionRecord(
                decision_key=bundle_decisions.decision_key_for(
                    pair_ids, merged_absorbed_id
                ),
                pair_ids=pair_ids,
                merged_absorbed_id=merged_absorbed_id,
                state=state,
                decided_at=datetime.now(UTC).isoformat(),
            )
        ],
    )


def _decide_identity(
    layout: config.WorkspaceLayout,
    *,
    member_ids: tuple[str, ...],
    state: bundle_decisions.DecisionState,
) -> None:
    """Write one identity decision record directly via
    `bundle.decisions.write_identity_decisions` (#797)."""
    sorted_members = tuple(sorted(member_ids))
    bundle_decisions.write_identity_decisions(
        sorted_members[0],
        layout.bundle_dir,
        records=[
            bundle_decisions.IdentityDecisionRecord(
                decision_key=bundle_decisions.identity_decision_key_for(sorted_members),
                member_ids=sorted_members,
                state=state,
                decided_at=datetime.now(UTC).isoformat(),
            )
        ],
    )


# --- persisted_findings ---


def test_persisted_findings_absent_db_answers_empty_and_creates_nothing(
    tmp_path: Path,
) -> None:
    """A workspace where the Contradictions stage has never persisted
    anything answers `()` rather than raising or opening a connection --
    and, because `path.exists()` is checked BEFORE
    `derived.open_derived_connection` (which would otherwise lazily create
    an empty file), `findings.db` must still be absent afterwards. This is
    the guard `config.WorkspaceLayout.findings_db_path`'s pure-derivation
    contract depends on."""
    layout = _workspace(tmp_path)

    result = pending.persisted_findings(layout)

    assert result == ()
    assert not layout.findings_db_path.exists()


def test_persisted_findings_reads_a_persisted_row(tmp_path: Path) -> None:
    """A finding recorded via `record_findings` round-trips back through
    `persisted_findings` with its identity intact."""
    layout = _workspace(tmp_path)
    _record_finding(layout, pair_ids=("concept-a", "concept-b"))

    result = pending.persisted_findings(layout)

    assert len(result) == 1
    assert result[0].pair_ids == ("concept-a", "concept-b")


# --- is_contradiction_declined ---


def test_is_contradiction_declined_reads_declined_as_true(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    pair_ids = ("concept-a", "concept-b")
    _decide(layout, pair_ids=pair_ids, state="declined")

    assert pending.is_contradiction_declined(layout, pair_ids, None) is True


def test_is_contradiction_declined_reads_reopened_as_false(tmp_path: Path) -> None:
    """A record whose latest `state` is `open` (reopened, per the pending-
    work spec's "Declined Findings Are Hidden By Default" -- reopening is
    the operator's way back out) reads as NOT declined, even though a
    decision record exists under this key."""
    layout = _workspace(tmp_path)
    pair_ids = ("concept-a", "concept-b")
    _decide(layout, pair_ids=pair_ids, state="declined")
    _decide(layout, pair_ids=pair_ids, state="open")

    assert pending.is_contradiction_declined(layout, pair_ids, None) is False


def test_is_contradiction_declined_absent_record_answers_false(
    tmp_path: Path,
) -> None:
    layout = _workspace(tmp_path)

    assert (
        pending.is_contradiction_declined(layout, ("concept-a", "concept-b"), None)
        is False
    )


# --- is_group_kept_distinct ---


def test_is_group_kept_distinct_sorts_operator_supplied_ids_defensively(
    tmp_path: Path,
) -> None:
    """`is_group_kept_distinct` must sort `member_ids` itself: the decision
    key and the sidecar's owning concept id are both derived from the
    SORTED tuple, so an operator-supplied (unsorted) sequence must still
    match a record written from the sorted order."""
    layout = _workspace(tmp_path)
    sorted_members = ("concept-a", "concept-b", "concept-c")
    _decide_identity(layout, member_ids=sorted_members, state="declined")

    unsorted = ("concept-c", "concept-a", "concept-b")

    assert pending.is_group_kept_distinct(layout, unsorted) is True


def test_is_group_kept_distinct_reopened_answers_false(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    members = ("concept-a", "concept-b")
    _decide_identity(layout, member_ids=members, state="declined")
    _decide_identity(layout, member_ids=members, state="open")

    assert pending.is_group_kept_distinct(layout, members) is False


# --- current_finding_digest ---


def test_current_finding_digest_reads_current_bytes(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    layout.bundle_dir.mkdir(parents=True, exist_ok=True)
    (layout.bundle_dir / "concept-a.md").write_bytes(b"hello")

    digest = pending.current_finding_digest(layout.bundle_dir)

    from openkos.state.vectorstore import content_hash

    assert digest("concept-a") == content_hash(b"hello")


def test_current_finding_digest_missing_input_ref_answers_none(
    tmp_path: Path,
) -> None:
    """A concept id with nothing on disk (removed since the finding was
    recorded, or a merged-body candidate's synthetic ledger-snapshot label)
    answers `None` -- "cannot currently determine", never evidence of drift
    (`state.findings._is_stale`'s own contract: a `None` digest never marks
    a finding stale by itself)."""
    layout = _workspace(tmp_path)

    digest = pending.current_finding_digest(layout.bundle_dir)

    assert digest("nonexistent-concept") is None


def test_current_finding_digest_unreadable_input_ref_answers_none(
    tmp_path: Path,
) -> None:
    """A non-file `input_ref` (e.g. a directory sitting where a concept
    file would be) is unreadable the same OSError way a missing file is,
    and must answer `None` rather than raising."""
    layout = _workspace(tmp_path)
    layout.bundle_dir.mkdir(parents=True, exist_ok=True)
    (layout.bundle_dir / "concept-a.md").mkdir()

    digest = pending.current_finding_digest(layout.bundle_dir)

    assert digest("concept-a") is None
