"""`application.queue_refresh`: the model-free refresh of the decision queue from
persisted verdicts (#1391, design Decision 7).

Every test runs with the chat-client builder patched to fail: a refresh makes no
model call and resolves no backend client.
"""

import contextlib
import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from openkos import config, lock
from openkos.application import (
    backends,
    curate_decisions,
    queue_producers,
    queue_refresh,
)
from openkos.application import pending as application_pending
from openkos.application import suggest_relations_service as relations
from openkos.application import suggest_volatility_service as volatility
from openkos.application.lock_wait import locked_commit_section
from openkos.errors import OpenKosError
from openkos.graph.base import Edge
from openkos.resolution import adjudication, volatility_typing
from openkos.resolution import candidates as cand
from openkos.resolution.edge_typing import EdgeSuggestion
from openkos.resolution.volatility_typing import TierSuggestion
from openkos.state import adjudications as adjudication_store
from openkos.state import derived
from openkos.state import pending_queue as pq
from openkos.state import volatility_suggestions as volatility_store
from tests.unit.application.curation_support import make_workspace
from tests.unit.cli.commit_phase_support import write_doc

MODEL = "the-task-model"
MEMBERS = ("concepts/a", "concepts/b")
SRC, DST = "concepts/c", "concepts/d"


def _section() -> contextlib.AbstractContextManager[None]:
    return contextlib.nullcontext()


def _group(*members: str) -> cand.CandidateGroup:
    return cand.CandidateGroup(
        okf_type="Concept", member_ids=members, tier=cand.Tier.HIGH, trigger="alpha"
    )


def _finder(
    *groups: cand.CandidateGroup, produced: int | None = None
) -> Callable[..., cand.CandidateGroupReport]:
    def find(bundle_dir: Path, **kwargs: Any) -> cand.CandidateGroupReport:
        return cand.CandidateGroupReport(
            groups=groups,
            produced=len(groups) if produced is None else produced,
            retained=len(groups),
        )

    return find


@pytest.fixture(autouse=True)
def no_client(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        pytest.fail("a client was built")

    monkeypatch.setattr(backends, "chat_client", fail)
    monkeypatch.setattr(config, "resolve_task_model", lambda cfg, task: MODEL)


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    ws = make_workspace(tmp_path, monkeypatch)
    for cid in (*MEMBERS, SRC, DST):
        write_doc(ws, cid, {"type": "Concept", "title": cid}, f"Body of {cid}.\n")
    write_doc(
        ws,
        "people/ada",
        {"type": "Person", "title": "Ada", "sensitivity": "private"},
        "Body of Ada.\n",
    )
    return ws


def _record_identity(
    layout: config.WorkspaceLayout,
    verdict: str = "same",
    *,
    rubric_digest: str | None = None,
) -> None:
    digest_of = application_pending.current_finding_digest(layout.bundle_dir)
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        adjudication_store.record_adjudications(
            conn,
            [
                adjudication_store.Adjudication(
                    member_ids=MEMBERS,
                    verdict=verdict,
                    confidence=0.9,
                    rationale="Judged.",
                    include_confidential=False,
                    input_digests=tuple(
                        adjudication_store.InputDigest(m, str(digest_of(m)))
                        for m in MEMBERS
                    ),
                    rubric_digest=rubric_digest or adjudication.rubric_digest(),
                )
            ],
        )
    finally:
        conn.close()


def _record_relation(layout: config.WorkspaceLayout) -> None:
    relations.persist_edge_suggestions(
        layout,
        [EdgeSuggestion(Edge(SRC, DST), "works_at", "they work together")],
        include_confidential=False,
    )


def _record_volatility(
    layout: config.WorkspaceLayout,
    cfg: config.Config,
    *,
    tier: str = "static",
    model: str = MODEL,
) -> None:
    digest = next(
        p.prompt_digest
        for p in volatility_typing.plan_types(
            layout.bundle_dir,
            include_confidential=False,
            local_exemption=False,
            rationale_language=cfg.rationale_language,
            type_tiers=cfg.type_tiers,
        )
        if p.type_name == "Person"
    )
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        volatility_store.record_volatility_suggestions(
            conn,
            [
                volatility_store.PersistedVolatilitySuggestion(
                    type_name="Person",
                    model=model,
                    prompt_digest=digest,
                    suggested_tier=tier,
                    rationale="people change slowly",
                    input_refs=(),
                )
            ],
        )
    finally:
        conn.close()


def _record_all(root: Path) -> config.WorkspaceLayout:
    layout = config.WorkspaceLayout(root)
    _record_identity(layout)
    _record_relation(layout)
    _record_volatility(layout, config.read_config(root))
    return layout


def _refresh(
    root: Path,
    *groups: cand.CandidateGroup,
    section: Callable[[], contextlib.AbstractContextManager[None]] = _section,
    produced: int | None = None,
) -> queue_refresh.RefreshOutcome:
    return queue_refresh.refresh_decision_queue(
        root,
        commit_section=section,
        find_candidates_report=_finder(*groups, produced=produced),
    )


def _stats(layout: config.WorkspaceLayout) -> list[pq.KindStats]:
    conn = sqlite3.connect(layout.findings_db_path)
    try:
        return pq.kind_stats(conn)
    finally:
        conn.close()


def _rows(layout: config.WorkspaceLayout) -> list[Any]:
    conn = sqlite3.connect(layout.findings_db_path)
    try:
        return list(pq.all_items(conn))
    finally:
        conn.close()


def _open_by_kind(layout: config.WorkspaceLayout) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {}
    for item in curate_decisions.list_open_decisions(layout):
        out.setdefault(item.kind, []).append(item)
    return out


# -- S3.3 the refresh outcomes --------------------------------------------------


def test_judged_verdicts_of_all_three_kinds_become_listable_open_rows(
    root: Path,
) -> None:
    layout = _record_all(root)
    assert curate_decisions.list_open_decisions(layout) == ()

    outcome = _refresh(root, _group(*MEMBERS))

    open_rows = _open_by_kind(layout)
    assert sorted(open_rows) == ["identity", "relation_type", "volatility"]
    identity = open_rows["identity"][0]
    assert identity.targets == MEMBERS
    assert identity.payload["adjudication"]["verdict"] == "same"
    assert identity.evaluation == "judged_same"
    relation = open_rows["relation_type"][0]
    assert relation.targets == (SRC, DST)
    assert relation.payload["suggested_type"] == "works_at"
    volatile = open_rows["volatility"][0]
    assert volatile.payload["type_name"] == "Person"
    assert volatile.payload["suggested_tier"] == "static"
    assert [len(open_rows[k]) for k in sorted(open_rows)] == [1, 1, 1]
    assert (
        outcome.identity.inserted,
        outcome.relation_type.inserted,
        outcome.volatility.inserted,
    ) == (1, 1, 1)
    assert outcome.volatility_ambiguous == 0
    assert outcome.identity_truncation_notice is None


def test_a_second_refresh_over_unchanged_inputs_leaves_the_stats_identical(
    root: Path,
) -> None:
    layout = _record_all(root)
    _refresh(root, _group(*MEMBERS))
    first = _stats(layout)

    second_outcome = _refresh(root, _group(*MEMBERS))

    assert _stats(layout) == first
    assert {s.kind: (s.enqueued, s.stale, s.open) for s in first} == {
        "identity": (1, 0, 1),
        "relation_type": (1, 0, 1),
        "volatility": (1, 0, 1),
    }
    for result in (
        second_outcome.identity,
        second_outcome.relation_type,
        second_outcome.volatility,
    ):
        assert (result.inserted, result.replaced, result.retired) == (0, 0, 0)


def test_a_verdict_written_through_to_an_open_row_amends_it(root: Path) -> None:
    layout = config.WorkspaceLayout(root)
    _refresh(root, _group(*MEMBERS))  # an unjudged identity row
    before = _stats(layout)
    assert before[0].kind == "identity"

    _record_identity(layout)
    outcome = _refresh(root, _group(*MEMBERS))

    assert outcome.identity.amended == 1
    assert _stats(layout) == before
    rows = [r for r in _rows(layout) if r.kind == "identity"]
    assert len(rows) == 1
    assert json.loads(rows[0].payload)["adjudication"]["verdict"] == "same"


def test_a_changed_member_retires_the_old_row_as_stale_and_inserts_one(
    root: Path,
) -> None:
    layout = _record_all(root)
    _refresh(root, _group(*MEMBERS))
    write_doc(root, MEMBERS[0], {"type": "Concept", "title": "edited"}, "New body.\n")

    outcome = _refresh(root, _group(*MEMBERS))

    assert outcome.identity.replaced == 1
    identity = [r for r in _rows(layout) if r.kind == "identity"]
    assert sorted(r.status for r in identity) == ["pending", "stale"]
    stats = {s.kind: s for s in _stats(layout)}
    assert (stats["identity"].enqueued, stats["identity"].stale) == (2, 1)
    assert stats["identity"].open == 1


def test_a_complete_identity_pass_retires_an_absent_identity_row(root: Path) -> None:
    layout = _record_all(root)
    _refresh(root, _group(*MEMBERS))

    outcome = _refresh(root)  # the group is no longer reported

    assert outcome.identity.complete is True
    assert outcome.identity.retired == 1
    statuses = {r.status for r in _rows(layout) if r.kind == "identity"}
    assert statuses == {"stale"}


def _delete_store_rows(layout: config.WorkspaceLayout) -> None:
    conn = sqlite3.connect(layout.findings_db_path)
    try:
        for table in (
            "edge_suggestion_input_digests",
            "edge_suggestions",
            "volatility_suggestions",
        ):
            conn.execute(f"DELETE FROM {table}")  # noqa: S608 -- fixed names
        conn.commit()
    finally:
        conn.close()


def test_relation_and_volatility_rows_the_pass_did_not_see_stay_open(
    root: Path,
) -> None:
    layout = _record_all(root)
    _refresh(root, _group(*MEMBERS))
    _delete_store_rows(layout)

    outcome = _refresh(root, _group(*MEMBERS))

    open_rows = _open_by_kind(layout)
    assert len(open_rows["relation_type"]) == 1
    assert len(open_rows["volatility"]) == 1
    assert outcome.relation_type.complete is False
    assert outcome.volatility.complete is False
    assert (outcome.relation_type.retired, outcome.volatility.retired) == (0, 0)


def test_a_source_supersession_row_is_never_retired(root: Path) -> None:
    layout = config.WorkspaceLayout(root)
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        pq.ensure_schema(conn)
        queue_producers.enqueue_source_supersession(
            conn,
            source_id=SRC,
            previous_id=DST,
            reason="new_version",
            current_digest=application_pending.current_finding_digest(
                layout.bundle_dir
            ),
            bundle_dir=layout.bundle_dir,
            commit_section=_section,
        )
    finally:
        conn.close()

    _refresh(root)

    rows = [r for r in _rows(layout) if r.producer == "source-supersession/1"]
    assert [r.status for r in rows] == ["pending"]


def test_a_truncated_identity_report_retires_nothing_and_says_so(root: Path) -> None:
    layout = _record_all(root)
    _refresh(root, _group(*MEMBERS))

    outcome = _refresh(root, produced=500)  # truncated, and the group not reached

    assert outcome.identity.complete is False
    assert outcome.identity.retired == 0
    assert outcome.identity_truncation_notice
    assert len(_open_by_kind(layout)["identity"]) == 1


def test_non_servable_stored_verdicts_are_not_enqueued(root: Path) -> None:
    layout = config.WorkspaceLayout(root)
    cfg = config.read_config(root)
    _record_identity(layout, rubric_digest="an-older-rubric")
    _record_relation(layout)
    write_doc(root, DST, {"type": "Concept", "title": "moved"}, "Rewritten.\n")
    _record_volatility(layout, cfg, model="another-model")

    outcome = _refresh(root, _group(*MEMBERS))

    open_rows = _open_by_kind(layout)
    assert sorted(open_rows) == ["identity"]
    assert open_rows["identity"][0].payload["adjudication"] is None
    assert outcome.relation_type.inserted == 0
    assert outcome.volatility.inserted == 0


def test_an_absent_queue_with_nothing_to_publish_is_not_created(root: Path) -> None:
    layout = config.WorkspaceLayout(root)

    outcome = _refresh(root)

    assert not layout.findings_db_path.exists()
    for result in (outcome.identity, outcome.relation_type, outcome.volatility):
        assert (
            result.inserted,
            result.unchanged,
            result.replaced,
            result.suppressed,
            result.retired,
            result.amended,
        ) == (0, 0, 0, 0, 0, 0)
    assert (
        outcome.identity.complete,
        outcome.relation_type.complete,
        outcome.volatility.complete,
    ) == (True, False, False)


def _tree_digest(directory: Path) -> dict[str, str]:
    return {
        str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _store_dump(layout: config.WorkspaceLayout) -> dict[str, list[tuple[Any, ...]]]:
    conn = sqlite3.connect(layout.findings_db_path)
    try:
        return {
            table: conn.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608
            for table in (
                "adjudications",
                "adjudication_input_digests",
                "edge_suggestions",
                "edge_suggestion_input_digests",
                "volatility_suggestions",
            )
        }
    finally:
        conn.close()


def test_a_refresh_writes_only_the_queue(root: Path) -> None:
    layout = _record_all(root)
    bundle_before = _tree_digest(layout.bundle_dir)
    stores_before = _store_dump(layout)
    assert all(stores_before.values())

    _refresh(root, _group(*MEMBERS))

    assert _tree_digest(layout.bundle_dir) == bundle_before
    assert _store_dump(layout) == stores_before


def test_two_regimes_that_disagree_are_skipped_and_counted(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = config.WorkspaceLayout(root)
    cfg = config.read_config(root)
    write_doc(
        root,
        "people/hidden",
        {"type": "Person", "title": "Hidden", "sensitivity": "confidential"},
        "Body of Hidden.\n",
    )  # the confidential body is what makes the two regime prompts differ
    _record_identity(layout)  # so a queue exists to publish into
    excluded, included = (
        {
            p.type_name: p.prompt_digest
            for p in volatility_typing.plan_types(
                layout.bundle_dir,
                include_confidential=include,
                local_exemption=False,
                rationale_language=cfg.rationale_language,
                type_tiers=cfg.type_tiers,
            )
        }["Person"]
        for include in (False, True)
    )
    held = {
        ("Person", excluded): ("slow", "one"),
        ("Person", included): ("volatile", "two"),
    }

    def fake_cached_answers(
        layout: config.WorkspaceLayout, model: str, type_tiers: object = None
    ) -> object:
        def lookup(type_name: str, digest: str) -> TierSuggestion | None:
            answer = held.get((type_name, digest))
            if answer is None:
                return None
            return TierSuggestion(type_name, "", answer[0], answer[1])

        return lookup

    monkeypatch.setattr(volatility, "cached_answers", fake_cached_answers)

    outcome = _refresh(root, _group(*MEMBERS))

    assert excluded != included
    assert outcome.volatility_ambiguous == 1
    assert outcome.volatility.inserted == 0
    assert "volatility" not in _open_by_kind(layout)


def test_an_unreadable_workspace_is_refused_and_nothing_is_written(
    tmp_path: Path,
) -> None:
    empty = tmp_path / "not-a-workspace"
    empty.mkdir()

    with pytest.raises(queue_refresh.QueueRefreshRefused) as raised:
        _refresh(empty)

    assert isinstance(raised.value, OpenKosError)
    assert list(empty.iterdir()) == []


# -- S3.4 the lock discipline ---------------------------------------------------


class _RecordingSection:
    """A commit section that records, per entry, the queue writes made inside it,
    whether the queue already existed when it was entered, and can be made to
    fail busy on its Nth entry."""

    def __init__(self, layout: config.WorkspaceLayout, *, busy_on: int | None = None):
        self.layout = layout
        self.entries = 0
        self.depth = 0
        self.busy_on = busy_on
        self.queue_existed_at_entry: list[bool] = []
        self.writes: list[list[str]] = []

    def _queue_exists(self) -> bool:
        if not self.layout.findings_db_path.is_file():
            return False
        conn = sqlite3.connect(self.layout.findings_db_path)
        try:
            return pq.queue_exists(conn)
        finally:
            conn.close()

    def __call__(self) -> contextlib.AbstractContextManager[None]:
        self.entries += 1
        if self.busy_on == self.entries:
            raise lock.WorkspaceBusyError("busy")
        return self._enter()

    @contextlib.contextmanager
    def _enter(self) -> Iterator[None]:
        self.queue_existed_at_entry.append(self._queue_exists())
        self.writes.append([])
        self.depth += 1
        try:
            yield
        finally:
            self.depth -= 1


def _guard_writes(
    monkeypatch: pytest.MonkeyPatch, section: _RecordingSection
) -> list[int]:
    """Every queue write opens with `ensure_schema` inside its own transaction;
    record the section depth each one ran at."""
    depths: list[int] = []
    original = pq.ensure_schema

    def wrapped(conn: sqlite3.Connection) -> None:
        depths.append(section.depth)
        if section.writes:
            section.writes[-1].append("ensure_schema")
        original(conn)

    monkeypatch.setattr(pq, "ensure_schema", wrapped)
    return depths


def test_every_write_happens_inside_a_commit_section(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _record_all(root)
    section = _RecordingSection(layout)
    depths = _guard_writes(monkeypatch, section)

    _refresh(root, _group(*MEMBERS), section=section)

    assert depths, "the refresh wrote nothing"
    assert all(depth == 1 for depth in depths), depths
    # One section creates the schema, then one per upserted row (three kinds),
    # then one for the identity retirement (the only kind that can retire).
    assert section.entries == 1 + 3 + 1
    assert len(_rows(layout)) == 3


def test_the_compute_phase_holds_no_lock(root: Path) -> None:
    _record_all(root)
    acquired: list[bool] = []

    def find(bundle_dir: Path, **kwargs: Any) -> cand.CandidateGroupReport:
        # Another process taking the lock in the middle of compute is not blocked.
        with lock.workspace_lock(root):
            acquired.append(True)
        return cand.CandidateGroupReport(
            groups=(_group(*MEMBERS),), produced=1, retained=1
        )

    queue_refresh.refresh_decision_queue(
        root,
        commit_section=locked_commit_section(root, wait_seconds=0),
        find_candidates_report=find,
    )

    assert acquired == [True]


def test_a_busy_section_surfaces_unchanged_and_keeps_the_rows_already_written(
    root: Path,
) -> None:
    layout = _record_all(root)
    # Entries: 1 schema, 2 identity upsert, 3 identity retire, 4 relation upsert.
    section = _RecordingSection(layout, busy_on=4)

    with pytest.raises(lock.WorkspaceBusyError):
        _refresh(root, _group(*MEMBERS), section=section)

    assert [r.kind for r in _rows(layout)] == ["identity"]


def test_the_queue_schema_is_created_only_inside_the_first_section(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _record_all(root)
    section = _RecordingSection(layout)
    _guard_writes(monkeypatch, section)

    _refresh(root, _group(*MEMBERS), section=section)

    # The queue did not exist when the first section was entered, and that
    # section did nothing but create it; every later one found it in place.
    assert section.queue_existed_at_entry[0] is False
    assert all(section.queue_existed_at_entry[1:])
    assert section.writes[0] == ["ensure_schema"]
