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
    decided_at: str | None = None,
) -> None:
    """Write one contradiction decision record directly via
    `bundle.decisions.write_decisions` -- mirrors `test_status.py`'s
    `_decline`, generalized to also cover the `open` (reopened) state.

    `decided_at` defaults to "now", but a caller may pin an explicit value
    (R3-reopen-order-wall-clock, PR #996 review): `write_decisions`'s own
    contract is full-replace ("(Re)write `concept_id`'s ... decisions to
    hold EXACTLY `records`"), so which record survives a second call is
    decided by CALL ORDER, never by comparing the two records'
    `decided_at` values. Pinning an identical `decided_at` across two
    calls is how a test proves that -- if precedence were secretly decided
    by timestamp comparison instead, an identical stamp would make the
    two records indistinguishable and the outcome would be undefined."""
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
                decided_at=decided_at or datetime.now(UTC).isoformat(),
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


def _record_finding_with_digest(
    layout: config.WorkspaceLayout, *, input_ref: str, digest: str
) -> None:
    """Persist one finding whose SOLE `input_digests` row is
    `(input_ref, digest)` -- the fixture the two staleness-wiring tests
    below share, isolated from `_record_finding` because that helper
    always records `input_digests=()`, which can never go stale and so
    cannot exercise this wiring at all."""
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        findings.record_findings(
            conn,
            [
                findings.Finding(
                    pair_ids=("concept-a", "concept-b"),
                    merged_absorbed_id=None,
                    verdict="contradicts",
                    confidence=0.91,
                    rationale="Stub rationale.",
                    input_digests=(findings.InputDigest(input_ref, digest),),
                )
            ],
        )
    finally:
        conn.close()


def test_persisted_findings_wires_current_finding_digest_unchanged_is_not_stale(
    tmp_path: Path,
) -> None:
    """R3-stale-wiring-unproved (PR #996 review): nothing previously proved
    `current_finding_digest` is actually passed through as `open_findings`'s
    `current_digest=` callback -- staleness resolution could be entirely
    absent from `persisted_findings` and every existing test would still
    pass, because none of them recorded a real `input_digests` row against
    real bytes on disk. Here the recorded digest matches the CURRENT bytes
    of `concept-a.md`, so a correctly wired callback answers `stale=False`.
    Paired with the "changed" sibling below: together they are the only
    proof this wiring exists, because a `persisted_findings` that dropped
    the `current_digest=` argument entirely would ALSO make both read back
    `stale=False` (`state.findings.open_findings`'s own default)."""
    layout = _workspace(tmp_path)
    layout.bundle_dir.mkdir(parents=True, exist_ok=True)
    (layout.bundle_dir / "concept-a.md").write_bytes(b"original bytes")

    from openkos.state.vectorstore import content_hash

    _record_finding_with_digest(
        layout, input_ref="concept-a", digest=content_hash(b"original bytes")
    )

    result = pending.persisted_findings(layout)

    assert len(result) == 1
    assert result[0].stale is False


def test_persisted_findings_wires_current_finding_digest_changed_is_stale(
    tmp_path: Path,
) -> None:
    """The sibling proof: identical setup, except `concept-a.md`'s bytes on
    disk have changed since the finding was recorded, so a correctly wired
    `current_digest` callback answers `stale=True`. This is the test the
    mutation proof below targets: removing `persisted_findings`'s
    `current_digest=current_finding_digest(...)` argument turns THIS test
    (and only this one) red, because the "unchanged" sibling above passes
    either way."""
    layout = _workspace(tmp_path)
    layout.bundle_dir.mkdir(parents=True, exist_ok=True)
    (layout.bundle_dir / "concept-a.md").write_bytes(b"original bytes")

    from openkos.state.vectorstore import content_hash

    _record_finding_with_digest(
        layout, input_ref="concept-a", digest=content_hash(b"original bytes")
    )
    (layout.bundle_dir / "concept-a.md").write_bytes(b"changed bytes")

    result = pending.persisted_findings(layout)

    assert len(result) == 1
    assert result[0].stale is True


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
    decision record exists under this key.

    R3-reopen-order-wall-clock (PR #996 review): both writes below share
    the SAME pinned `decided_at`, which is what proves the outcome does
    NOT rest on comparing the two records' timestamps. The real mechanism
    is `bundle.decisions.write_decisions`'s full-replace contract: its
    second call REPLACES `concept-a`'s entire sidecar with the "open"
    record, so `is_contradiction_declined` only ever reads back the one
    record still on disk -- call ORDER decides, never wall-clock value. If
    this test instead let `decided_at` default to `datetime.now(UTC)` for
    each call (as it did before this fix), it would still pass today, but
    for a reason indistinguishable from "the later timestamp wins" -- a
    resolution rule this store does not actually implement, and a coarse
    filesystem clock or two calls landing in the same tick could someday
    make that indistinguishable difference matter."""
    layout = _workspace(tmp_path)
    pair_ids = ("concept-a", "concept-b")
    pinned_decided_at = "2020-01-01T00:00:00+00:00"
    _decide(layout, pair_ids=pair_ids, state="declined", decided_at=pinned_decided_at)
    _decide(layout, pair_ids=pair_ids, state="open", decided_at=pinned_decided_at)

    assert pending.is_contradiction_declined(layout, pair_ids, None) is False


def test_is_contradiction_declined_absent_record_answers_false(
    tmp_path: Path,
) -> None:
    layout = _workspace(tmp_path)

    assert (
        pending.is_contradiction_declined(layout, ("concept-a", "concept-b"), None)
        is False
    )


def test_is_contradiction_declined_relies_on_pre_sorted_pair_ids(
    tmp_path: Path,
) -> None:
    """R2/R3-twin-sorting-contract (PR #996 review): `is_contradiction_declined`
    does NOT sort `pair_ids` itself (see `application.pending`'s module
    docstring, "Twin-sorting contract", for the full rationale). This pins
    that the dependency on a pre-sorted pair is real, not latent: a decision
    recorded under the SORTED pair is invisible to a lookup using the
    reversed order, because both `decision_key_for`'s digest AND the owning
    sidecar file (keyed on `pair_ids[0]`) are computed from `pair_ids`
    exactly as given. A future "helpful" defensive sort added here would be
    a deliberate, visible change to this test -- not a silent one."""
    layout = _workspace(tmp_path)
    sorted_ids = ("concept-a", "concept-b")
    _decide(layout, pair_ids=sorted_ids, state="declined")

    reversed_ids = ("concept-b", "concept-a")

    assert pending.is_contradiction_declined(layout, reversed_ids, None) is False


def test_is_contradiction_declined_merged_absorbed_id_participates_in_the_key(
    tmp_path: Path,
) -> None:
    """R3-merged-absorbed-key-unproved (PR #996 review): no prior test
    proved `merged_absorbed_id` actually participates in the decision key
    -- `decision_key_for`'s own docstring names it as the SOLE
    discriminator between a typed-edge candidate and a merged-body
    candidate sharing the same `pair_ids`. A decision recorded for
    `(pair, merged_absorbed_id="concept-x")` must not match a lookup for
    `(pair, merged_absorbed_id=None)` or a different absorbed id.

    Mutation proof target: dropping `merged_absorbed_id` from
    `decision_key_for`'s digest construction collapses all three lookups
    below to the SAME key, turning the first two assertions from `False`
    to `True`."""
    layout = _workspace(tmp_path)
    pair_ids = ("concept-a", "concept-b")
    _decide(layout, pair_ids=pair_ids, merged_absorbed_id="concept-x", state="declined")

    assert pending.is_contradiction_declined(layout, pair_ids, None) is False
    assert pending.is_contradiction_declined(layout, pair_ids, "concept-y") is False
    assert pending.is_contradiction_declined(layout, pair_ids, "concept-x") is True


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
