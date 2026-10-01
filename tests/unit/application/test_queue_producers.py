"""`application.queue_producers`: the advisors' compute-and-enqueue path into the
pending-work queue (#1141, ADR-0037, unit 4.2).

The three properties every kind must hold: a re-run does not duplicate a row, a
changed input retires the old row as stale and inserts a new one, and an
incomplete run never retires an unseen row. Each is asserted over all five
producers through one table, so a new producer cannot skip them.
"""

import contextlib
import json
import sqlite3
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from openkos.application import queue_producers as qp
from openkos.application.contradictions_service import ContradictionsOutcome
from openkos.application.duplicates_service import DuplicatesReport
from openkos.application.revisions import RevisionOutcome, RevisionPlan, VectorCoverage
from openkos.application.suggest_relations_service import SuggestRelationsOutcome
from openkos.application.suggest_volatility_service import VolatilityOutcome
from openkos.bundle import decisions as bundle_decisions
from openkos.graph.base import Edge
from openkos.llm.base import BackendError
from openkos.model import okf
from openkos.resolution import candidates as cand
from openkos.resolution import contradiction as contra
from openkos.resolution import decision_revision as dr
from openkos.resolution.edge_typing import EdgeSuggestion
from openkos.resolution.volatility_typing import TierSuggestion, TierSuggestionBatch
from openkos.state import findings as findings_store
from openkos.state import pending_queue as pq
from openkos.state import revision_findings as rf

T0 = datetime(2026, 1, 1, tzinfo=UTC)
SENTINEL = "SENTINEL-claim-text-7f3a"


class _Section:
    def __init__(self) -> None:
        self.entered = 0

    def __call__(self) -> contextlib.AbstractContextManager[None]:
        @contextlib.contextmanager
        def cm() -> Iterator[None]:
            self.entered += 1
            yield

        return cm()


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    yield connection
    connection.close()


@pytest.fixture
def bundle_dir(tmp_path: Path) -> Path:
    path = tmp_path / "bundle"
    path.mkdir()
    return path


@pytest.fixture
def section() -> _Section:
    return _Section()


# -- One feed per kind --------------------------------------------------------
#
# A feed answers: given how many proposals the advisor produced (`items`), which
# version of its inputs it read (`version`), and whether the run was complete,
# call the kind's real `enqueue_*` and return the result.

Feed = Callable[..., qp.ProducerResult]

_PAIRS = (("concepts/a", "concepts/b"), ("concepts/c", "concepts/d"))


def _digest(version: int) -> Callable[[str], str | None]:
    return lambda ref: f"v{version}:{ref}"


def _feed_relation(
    conn: sqlite3.Connection,
    *,
    items: int,
    version: int,
    complete: bool,
    commit_section: Callable[[], contextlib.AbstractContextManager[None]],
    bundle_dir: Path,
    minutes: int,
) -> qp.ProducerResult:
    outcome = SuggestRelationsOutcome(
        status="completed",
        results=tuple(
            EdgeSuggestion(Edge(a, b), "works_at", "they work together")
            for a, b in _PAIRS[:items]
        ),
        total=items,
        deferred_by_bound=0 if complete else 1,
    )
    return qp.enqueue_relations(
        conn,
        outcome,
        current_digest=_digest(version),
        commit_section=commit_section,
        bundle_dir=bundle_dir,
        clock=lambda: T0 + timedelta(minutes=minutes),
    )


def _feed_volatility(
    conn: sqlite3.Connection,
    *,
    items: int,
    version: int,
    complete: bool,
    commit_section: Callable[[], contextlib.AbstractContextManager[None]],
    bundle_dir: Path,
    minutes: int,
) -> qp.ProducerResult:
    tiers = ("snapshot", "pointer")  # never the default, "timeless"
    outcome = VolatilityOutcome(
        batch=TierSuggestionBatch(
            results=[
                TierSuggestion(name, "timeless", tiers[version - 1], "why")
                for name in ("Person", "Place")[:items]
            ],
            deferred=0 if complete else 1,
        ),
        model="m",
    )
    return qp.enqueue_volatility(
        conn,
        outcome,
        commit_section=commit_section,
        bundle_dir=bundle_dir,
        clock=lambda: T0 + timedelta(minutes=minutes),
    )


def _spec(a: str, b: str) -> contra._CandidateSpec:
    return contra._CandidateSpec(pair_ids=(a, b), relation_type="supports")


def _feed_contradiction(
    conn: sqlite3.Connection,
    *,
    items: int,
    version: int,
    complete: bool,
    commit_section: Callable[[], contextlib.AbstractContextManager[None]],
    bundle_dir: Path,
    minutes: int,
) -> qp.ProducerResult:
    specs = tuple(_spec(a, b) for a, b in _PAIRS[:items])
    verdicts = tuple(
        contra.ContradictionVerdict(
            pair_ids=spec.pair_ids,
            verdict=contra.Verdict.CONTRADICTS,
            confidence=0.9,
            rationale="they disagree",
            conflicting_claims=(SENTINEL,),
        )
        for spec in specs
    )
    outcome = _contradictions_outcome(specs, verdicts, complete=complete)
    return qp.enqueue_contradictions(
        conn,
        outcome,
        input_digests=lambda _root, spec: tuple(
            findings_store.InputDigest(ref, f"v{version}:{ref}")
            for ref in spec.pair_ids
        ),
        bundle_dir=bundle_dir,
        commit_section=commit_section,
        clock=lambda: T0 + timedelta(minutes=minutes),
    )


def _contradictions_outcome(
    specs: Sequence[contra._CandidateSpec],
    verdicts: Sequence[contra.ContradictionVerdict],
    *,
    complete: bool = True,
) -> ContradictionsOutcome:
    plan = contra.CandidatePlan(
        specs=tuple(specs), edge_total=len(specs), merged_total=0
    )
    return ContradictionsOutcome(
        model="m",
        plan=plan,
        judged_plan=plan,
        batch=contra.ContradictionBatch(results=list(verdicts)),
        verdicts=tuple(verdicts),
        displayed=tuple(verdicts),
        served_count=0,
        fresh_count=len(verdicts),
        vacuous_notice=None,
        candidate_notice=None,
        quarantine_notice=None,
        truncation_notice=None,
        zero_state=None,
        deferred_by_bound=0 if complete else 1,
    )


def _feed_identity(
    conn: sqlite3.Connection,
    *,
    items: int,
    version: int,
    complete: bool,
    commit_section: Callable[[], contextlib.AbstractContextManager[None]],
    bundle_dir: Path,
    minutes: int,
) -> qp.ProducerResult:
    groups = tuple(
        cand.CandidateGroup(
            okf_type="Person", member_ids=pair, tier=cand.Tier.HIGH, trigger="ada"
        )
        for pair in _PAIRS[:items]
    )
    report = DuplicatesReport(
        groups=groups,
        suppressed=0,
        truncation_notice=None if complete else "showing the first 50 groups",
    )
    return qp.enqueue_identity(
        conn,
        report,
        current_digest=_digest(version),
        commit_section=commit_section,
        bundle_dir=bundle_dir,
        clock=lambda: T0 + timedelta(minutes=minutes),
    )


def _revision_finding(a: str, b: str, version: int) -> rf.RevisionFinding:
    return rf.RevisionFinding(
        pair_ids=(a, b),
        verdict="reverses",
        confidence=0.9,
        rationale="the later decision replaces the earlier",
        quotes=("q1", "q2"),
        dates=("2025-01-01", "2026-01-01"),
        date_states=("explicit", "explicit"),
        include_confidential=False,
        prompt_version="p1",
        input_digests=(rf.InputDigest(a, f"v{version}:{a}"),),
    )


def _feed_revision(
    conn: sqlite3.Connection,
    *,
    items: int,
    version: int,
    complete: bool,
    commit_section: Callable[[], contextlib.AbstractContextManager[None]],
    bundle_dir: Path,
    minutes: int,
) -> qp.ProducerResult:
    return qp.enqueue_revisions(
        conn,
        tuple(_revision_finding(a, b, version) for a, b in _PAIRS[:items]),
        complete=complete,
        commit_section=commit_section,
        bundle_dir=bundle_dir,
        clock=lambda: T0 + timedelta(minutes=minutes),
    )


FEEDS: dict[str, Feed] = {
    "relation_type": _feed_relation,
    "volatility": _feed_volatility,
    "contradiction": _feed_contradiction,
    "identity": _feed_identity,
    "revision": _feed_revision,
}
ALL_KINDS = sorted(FEEDS)


def _rows(conn: sqlite3.Connection, kind: str) -> list[tuple[str, str, str]]:
    return conn.execute(
        "SELECT decision_key, status, last_seen_at FROM pending_items"
        " WHERE kind = ? ORDER BY id",
        (kind,),
    ).fetchall()


def _statuses(conn: sqlite3.Connection, kind: str) -> list[str]:
    return [row[1] for row in _rows(conn, kind)]


# -- The three required properties, over every kind -----------------------------


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_a_rerun_does_not_duplicate_a_row(
    kind: str, conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    feed = FEEDS[kind]
    first = feed(
        conn,
        items=2,
        version=1,
        complete=True,
        commit_section=section,
        bundle_dir=bundle_dir,
        minutes=0,
    )
    before = {row[0]: row[2] for row in _rows(conn, kind)}
    second = feed(
        conn,
        items=2,
        version=1,
        complete=True,
        commit_section=section,
        bundle_dir=bundle_dir,
        minutes=5,
    )
    rows = _rows(conn, kind)
    assert (first.inserted, first.unchanged) == (2, 0)
    assert (second.inserted, second.unchanged, second.replaced) == (0, 2, 0)
    assert len(rows) == 2
    assert [row[1] for row in rows] == ["pending", "pending"]
    # last-seen moved, on every row, and only that
    for key, last_seen in ((row[0], row[2]) for row in rows):
        assert last_seen == (T0 + timedelta(minutes=5)).isoformat()
        assert before[key] == T0.isoformat()


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_a_changed_input_retires_the_old_row_and_inserts_a_new_one(
    kind: str, conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    feed = FEEDS[kind]
    feed(
        conn,
        items=1,
        version=1,
        complete=True,
        commit_section=section,
        bundle_dir=bundle_dir,
        minutes=0,
    )
    result = feed(
        conn,
        items=1,
        version=2,
        complete=True,
        commit_section=section,
        bundle_dir=bundle_dir,
        minutes=5,
    )
    assert (result.inserted, result.replaced, result.unchanged) == (0, 1, 0)
    rows = _rows(conn, kind)
    assert [row[1] for row in rows] == ["stale", "pending"]
    # the same key for both generations: one open row per key
    assert rows[0][0] == rows[1][0]


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_an_incomplete_run_does_not_retire_unseen_rows(
    kind: str, conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    feed = FEEDS[kind]
    feed(
        conn,
        items=2,
        version=1,
        complete=True,
        commit_section=section,
        bundle_dir=bundle_dir,
        minutes=0,
    )
    # a bounded / aborted run reaches only the first proposal
    partial = feed(
        conn,
        items=1,
        version=1,
        complete=False,
        commit_section=section,
        bundle_dir=bundle_dir,
        minutes=5,
    )
    assert partial.retired == 0
    assert _statuses(conn, kind) == ["pending", "pending"]


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_a_complete_run_retires_a_proposal_that_no_longer_exists(
    kind: str, conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    feed = FEEDS[kind]
    feed(
        conn,
        items=2,
        version=1,
        complete=True,
        commit_section=section,
        bundle_dir=bundle_dir,
        minutes=0,
    )
    result = feed(
        conn,
        items=1,
        version=1,
        complete=True,
        commit_section=section,
        bundle_dir=bundle_dir,
        minutes=5,
    )
    assert result.retired == 1
    assert _statuses(conn, kind) == ["pending", "stale"]


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_a_run_never_touches_another_kinds_rows(
    kind: str, conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    for other in ALL_KINDS:
        FEEDS[other](
            conn,
            items=1,
            version=1,
            complete=True,
            commit_section=section,
            bundle_dir=bundle_dir,
            minutes=0,
        )
    # an empty complete run of ONE kind retires only that kind's row
    FEEDS[kind](
        conn,
        items=0,
        version=1,
        complete=True,
        commit_section=section,
        bundle_dir=bundle_dir,
        minutes=5,
    )
    for other in ALL_KINDS:
        assert _statuses(conn, other) == (["stale"] if other == kind else ["pending"])


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_every_queue_write_enters_the_commit_phase_and_never_resolves(
    kind: str, conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    FEEDS[kind](
        conn,
        items=2,
        version=1,
        complete=True,
        commit_section=section,
        bundle_dir=bundle_dir,
        minutes=0,
    )
    assert section.entered >= 3  # two upserts and the retirement pass
    statuses = {
        row[0] for row in conn.execute("SELECT status FROM pending_items").fetchall()
    }
    assert statuses == {"pending"}


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_producing_writes_nothing_under_the_bundle(
    kind: str, conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    FEEDS[kind](
        conn,
        items=2,
        version=1,
        complete=True,
        commit_section=section,
        bundle_dir=bundle_dir,
        minutes=0,
    )
    assert list(bundle_dir.rglob("*")) == []


# -- Per-kind behaviour -------------------------------------------------------


def test_contradiction_row_carries_the_claims_and_both_pair_ids(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    _feed_contradiction(
        conn,
        items=1,
        version=1,
        complete=True,
        commit_section=section,
        bundle_dir=bundle_dir,
        minutes=0,
    )
    (item,) = pq.open_items(conn, kind="contradiction")
    assert item.targets == ("concepts/a", "concepts/b")
    payload = json.loads(item.payload)
    assert payload["conflicting_claims"] == [SENTINEL]
    assert payload["merged_absorbed_id"] is None


def test_only_contradicts_verdicts_become_rows(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    specs = (_spec("concepts/a", "concepts/b"), _spec("concepts/c", "concepts/d"))
    verdicts = (
        contra.ContradictionVerdict(
            pair_ids=specs[0].pair_ids,
            verdict=contra.Verdict.CONSISTENT,
            confidence=0.9,
            rationale="fine",
            conflicting_claims=(),
        ),
        contra.ContradictionVerdict(
            pair_ids=specs[1].pair_ids,
            verdict=contra.Verdict.UNCERTAIN,
            confidence=0.2,
            rationale="unsure",
            conflicting_claims=(),
        ),
    )
    qp.enqueue_contradictions(
        conn,
        _contradictions_outcome(specs, verdicts),
        input_digests=lambda _r, _s: (),
        bundle_dir=bundle_dir,
        commit_section=section,
    )
    assert pq.open_items(conn) == []


def test_a_verdict_that_stops_contradicting_retires_its_row(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    spec = _spec("concepts/a", "concepts/b")
    verdict = contra.ContradictionVerdict(
        pair_ids=spec.pair_ids,
        verdict=contra.Verdict.CONTRADICTS,
        confidence=0.9,
        rationale="r",
        conflicting_claims=("c",),
    )
    kw = dict(
        input_digests=lambda _r, _s: (), bundle_dir=bundle_dir, commit_section=section
    )
    qp.enqueue_contradictions(conn, _contradictions_outcome([spec], [verdict]), **kw)  # type: ignore[arg-type]
    resolved = contra.ContradictionVerdict(
        pair_ids=spec.pair_ids,
        verdict=contra.Verdict.CONSISTENT,
        confidence=0.9,
        rationale="r",
        conflicting_claims=(),
    )
    qp.enqueue_contradictions(conn, _contradictions_outcome([spec], [resolved]), **kw)  # type: ignore[arg-type]
    assert _statuses(conn, "contradiction") == ["stale"]


def test_typed_edge_and_merged_body_contradictions_stay_distinct(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    entry = okf.MergeLedgerEntry(
        schema="v3",
        merged_at="2026-01-01T00:00:00Z",
        absorbed_id="concepts/b",
        absorbed_snapshot="absorbed bytes",
        survivor_before="survivor bytes",
        index_before="",
        log_before="",
        link_rewrites=[],
        sensitivity_before="",
        sensitivity_after="",
    )
    typed = _spec("concepts/a", "concepts/b")
    merged = contra._CandidateSpec(
        pair_ids=("concepts/a", "concepts/a"), relation_type=None, merge_entry=entry
    )
    verdicts = tuple(
        contra.ContradictionVerdict(
            pair_ids=spec.pair_ids,
            verdict=contra.Verdict.CONTRADICTS,
            confidence=0.9,
            rationale="r",
            conflicting_claims=("c",),
            merged_absorbed_id=("concepts/b" if spec.merge_entry else None),
        )
        for spec in (typed, merged)
    )
    qp.enqueue_contradictions(
        conn,
        _contradictions_outcome((typed, merged), verdicts),
        input_digests=lambda _r, _s: (),
        bundle_dir=bundle_dir,
        commit_section=section,
    )
    keys = {row[0] for row in _rows(conn, "contradiction")}
    assert len(keys) == 2


def test_a_declined_contradiction_is_not_enqueued(
    conn: sqlite3.Connection, section: _Section, tmp_path: Path
) -> None:
    bundle = tmp_path / "bundle"
    (bundle / "concepts").mkdir(parents=True)
    pair = ("concepts/a", "concepts/b")
    body = bundle_decisions.decision_key_for(pair, None)
    bundle_decisions.write_decisions(
        pair[0],
        bundle,
        records=[bundle_decisions.DecisionRecord(body, pair, None, "declined", "t")],
    )
    verdict = contra.ContradictionVerdict(
        pair_ids=pair,
        verdict=contra.Verdict.CONTRADICTS,
        confidence=0.9,
        rationale="r",
        conflicting_claims=("c",),
    )
    result = qp.enqueue_contradictions(
        conn,
        _contradictions_outcome([_spec(*pair)], [verdict]),
        input_digests=lambda _r, _s: (),
        bundle_dir=bundle,
        commit_section=section,
    )
    assert (result.suppressed, result.inserted) == (1, 0)
    assert pq.open_items(conn) == []


def test_volatility_equal_to_the_current_default_is_not_a_proposal(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    outcome = VolatilityOutcome(
        batch=TierSuggestionBatch(
            results=[
                TierSuggestion("Person", "timeless", "timeless", "same"),
                TierSuggestion("Place", "timeless", "snapshot", "differs"),
                TierSuggestion("Event", "timeless", None, "degraded"),
            ]
        ),
        model="m",
    )
    result = qp.enqueue_volatility(
        conn, outcome, commit_section=section, bundle_dir=bundle_dir
    )
    assert result.inserted == 1
    (item,) = pq.open_items(conn, kind="volatility")
    payload = json.loads(item.payload)
    assert (payload["type_name"], payload["suggested_tier"]) == ("Place", "snapshot")
    assert item.decision_key == "volatility:" + pq.volatility_key("Place")


def test_an_edge_without_a_suggested_type_or_endpoint_digest_is_skipped(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    outcome = SuggestRelationsOutcome(
        status="completed",
        results=(
            EdgeSuggestion(Edge("a", "b"), None, "degraded"),
            EdgeSuggestion(Edge("c", "d"), "works_at", "unreadable endpoint"),
            EdgeSuggestion(Edge("e", "f"), "works_at", "good"),
        ),
        total=3,
    )
    result = qp.enqueue_relations(
        conn,
        outcome,
        current_digest=lambda ref: None if ref == "d" else f"d:{ref}",
        commit_section=section,
        bundle_dir=bundle_dir,
    )
    assert result.inserted == 1
    (item,) = pq.open_items(conn, kind="relation_type")
    assert item.targets == ("e", "f")


def test_a_direction_corrected_suggestion_keeps_the_candidate_edge_as_its_key(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    suggestion = EdgeSuggestion(
        Edge("a", "b"), "works_at", "r", corrected_edge=Edge("b", "a", "works_at")
    )
    qp.enqueue_relations(
        conn,
        SuggestRelationsOutcome(status="completed", results=(suggestion,), total=1),
        current_digest=_digest(1),
        commit_section=section,
        bundle_dir=bundle_dir,
    )
    (item,) = pq.open_items(conn)
    assert item.decision_key == "relation_type:" + pq.relation_type_key("a", "b")
    payload = json.loads(item.payload)
    assert (payload["source_id"], payload["target_id"]) == ("a", "b")
    assert (payload["effective_source_id"], payload["effective_target_id"]) == (
        "b",
        "a",
    )


@pytest.mark.parametrize(
    "outcome",
    [
        SuggestRelationsOutcome(status="declined"),
        SuggestRelationsOutcome(status="empty_window"),
        SuggestRelationsOutcome(status="completed", failure=BackendError("down")),
        SuggestRelationsOutcome(status="completed", next_offset=10),
        SuggestRelationsOutcome(status="completed", truncation_notice="capped"),
    ],
    ids=["declined", "empty_window", "failed", "windowed", "truncated"],
)
def test_relation_runs_that_did_not_cover_everything_are_incomplete(
    outcome: SuggestRelationsOutcome,
) -> None:
    assert qp.relations_run_complete(outcome) is False


def test_a_relation_run_with_no_candidates_is_complete() -> None:
    assert qp.relations_run_complete(SuggestRelationsOutcome(status="no_candidates"))
    assert qp.relations_run_complete(SuggestRelationsOutcome(status="completed"))


def test_contradiction_run_completeness_reads_failure_bound_and_cap() -> None:
    spec = _spec("concepts/a", "concepts/b")
    ok = _contradictions_outcome([spec], [])
    assert qp.contradictions_run_complete(ok)
    bounded = _contradictions_outcome([spec], [], complete=False)
    assert not qp.contradictions_run_complete(bounded)
    import dataclasses

    failed = dataclasses.replace(
        ok, batch=contra.ContradictionBatch(results=[], failure=BackendError("x"))
    )
    assert not qp.contradictions_run_complete(failed)
    capped = dataclasses.replace(
        ok, plan=contra.CandidatePlan(specs=(spec,), edge_total=5, merged_total=0)
    )
    assert not qp.contradictions_run_complete(capped)


def test_volatility_run_completeness() -> None:
    assert qp.volatility_run_complete(
        VolatilityOutcome(batch=TierSuggestionBatch(results=[]), model="m")
    )
    assert not qp.volatility_run_complete(
        VolatilityOutcome(batch=TierSuggestionBatch(results=[], deferred=1), model="m")
    )
    assert not qp.volatility_run_complete(
        VolatilityOutcome(
            batch=TierSuggestionBatch(results=[], failure=BackendError("x")),
            model="m",
        )
    )


def _plan(*, total: int, candidates: int, store: str = "ok") -> RevisionPlan:
    return RevisionPlan(
        coverage=VectorCoverage(
            store=store,  # type: ignore[arg-type]
            vectors={},
            missing=frozenset(),
            stale=frozenset(),
        ),
        candidate_plan=dr.RevisionCandidatePlan(
            candidates=tuple(object() for _ in range(candidates)),  # type: ignore[misc]
            total=total,
            without_vector=0,
        ),
        served=(),
        to_judge=(),
    )


def test_revision_run_completeness() -> None:
    done = RevisionOutcome(results=())
    assert qp.revisions_run_complete(_plan(total=2, candidates=2), done)
    assert not qp.revisions_run_complete(_plan(total=3, candidates=2), done)
    assert not qp.revisions_run_complete(
        _plan(total=2, candidates=2, store="absent"), done
    )
    assert not qp.revisions_run_complete(
        _plan(total=2, candidates=2), RevisionOutcome(results=(), deferred_by_bound=1)
    )
    assert not qp.revisions_run_complete(
        _plan(total=2, candidates=2),
        RevisionOutcome(results=(), failure=BackendError("x"), failed_index=1),
    )


def test_identity_adjudication_verdict_rides_in_the_payload_and_changes_the_digest(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    from openkos.resolution import adjudication as adj

    group = cand.CandidateGroup(
        okf_type="Person",
        member_ids=("concepts/a", "concepts/b"),
        tier=cand.Tier.HIGH,
        trigger="ada",
    )
    report = DuplicatesReport(groups=(group,), suppressed=0, truncation_notice=None)
    kw = dict(current_digest=_digest(1), commit_section=section, bundle_dir=bundle_dir)
    qp.enqueue_identity(conn, report, **kw)  # type: ignore[arg-type]
    result = qp.enqueue_identity(
        conn,
        report,
        adjudicated=(adj.AdjudicatedCandidate(group, adj.Verdict.SAME, 0.9, SENTINEL),),
        **kw,  # type: ignore[arg-type]
    )
    assert result.replaced == 1
    (item,) = pq.open_items(conn, kind="identity")
    payload = json.loads(item.payload)
    assert payload["adjudication"] == {
        "verdict": "same",
        "confidence": 0.9,
        "rationale": SENTINEL,
    }
    assert item.targets == ("concepts/a", "concepts/b")
    assert item.decision_key == "identity:" + pq.identity_key(group.member_ids)


def test_an_adjudication_verdict_for_another_group_is_ignored(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    from openkos.resolution import adjudication as adj

    group = cand.CandidateGroup(
        okf_type="Person",
        member_ids=("concepts/a", "concepts/b"),
        tier=cand.Tier.HIGH,
        trigger="ada",
    )
    other = cand.CandidateGroup(
        okf_type="Person",
        member_ids=("concepts/x", "concepts/y"),
        tier=cand.Tier.HIGH,
        trigger="x",
    )
    qp.enqueue_identity(
        conn,
        DuplicatesReport(groups=(group,), suppressed=0, truncation_notice=None),
        adjudicated=(adj.AdjudicatedCandidate(other, adj.Verdict.SAME, 0.9, "r"),),
        current_digest=_digest(1),
        commit_section=section,
        bundle_dir=bundle_dir,
    )
    (item,) = pq.open_items(conn, kind="identity")
    assert json.loads(item.payload)["adjudication"] is None


def test_a_kept_distinct_identity_group_is_not_enqueued(
    conn: sqlite3.Connection, section: _Section, tmp_path: Path
) -> None:
    bundle = tmp_path / "bundle"
    (bundle / "concepts").mkdir(parents=True)
    members = ("concepts/a", "concepts/b")
    bundle_decisions.write_identity_decisions(
        members[0],
        bundle,
        records=[
            bundle_decisions.IdentityDecisionRecord(
                bundle_decisions.identity_decision_key_for(members),
                members,
                "declined",
                "t",
            )
        ],
    )
    group = cand.CandidateGroup(
        okf_type="Person", member_ids=members, tier=cand.Tier.HIGH, trigger="t"
    )
    result = qp.enqueue_identity(
        conn,
        DuplicatesReport(groups=(group,), suppressed=0, truncation_notice=None),
        current_digest=_digest(1),
        commit_section=section,
        bundle_dir=bundle,
    )
    assert (result.suppressed, result.inserted) == (1, 0)
    assert pq.open_items(conn) == []


def test_revision_row_carries_quotes_and_dates(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    _feed_revision(
        conn,
        items=1,
        version=1,
        complete=True,
        commit_section=section,
        bundle_dir=bundle_dir,
        minutes=0,
    )
    (item,) = pq.open_items(conn, kind="revision")
    payload = json.loads(item.payload)
    assert payload["verdict"] == "reverses"
    assert payload["quotes"] == ["q1", "q2"]
    assert item.decision_key == "revision:" + pq.revision_key(
        ("concepts/a", "concepts/b")
    )


def test_a_declined_merged_body_contradiction_is_found_when_the_absorbed_id_sorts_first(
    conn: sqlite3.Connection, section: _Section, tmp_path: Path
) -> None:
    """The row names its absorbed concept too, and that id can sort ahead of the
    sidecar's owner (`pair_ids[0]`); the decline must still be found."""
    bundle = tmp_path / "bundle"
    (bundle / "concepts").mkdir(parents=True)
    survivor, absorbed = "concepts/z", "concepts/a"
    pair = (survivor, survivor)
    body = bundle_decisions.decision_key_for(pair, absorbed)
    bundle_decisions.write_decisions(
        survivor,
        bundle,
        records=[
            bundle_decisions.DecisionRecord(body, pair, absorbed, "declined", "t")
        ],
    )
    entry = okf.MergeLedgerEntry(
        schema="v3",
        merged_at="2026-01-01T00:00:00Z",
        absorbed_id=absorbed,
        absorbed_snapshot="a",
        survivor_before="s",
        index_before="",
        log_before="",
        link_rewrites=[],
        sensitivity_before="",
        sensitivity_after="",
    )
    spec = contra._CandidateSpec(pair_ids=pair, relation_type=None, merge_entry=entry)
    verdict = contra.ContradictionVerdict(
        pair_ids=pair,
        verdict=contra.Verdict.CONTRADICTS,
        confidence=0.9,
        rationale="r",
        conflicting_claims=("c",),
        merged_absorbed_id=absorbed,
    )
    result = qp.enqueue_contradictions(
        conn,
        _contradictions_outcome([spec], [verdict]),
        input_digests=lambda _r, _s: (),
        bundle_dir=bundle,
        commit_section=section,
    )
    assert (result.suppressed, result.inserted) == (1, 0)
