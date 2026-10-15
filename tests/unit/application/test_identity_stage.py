"""`application.identity_stage`: the daemon's identity stage split into a
model-free compute half and a commit half (#1391, design Decision 5).

`identity_report` walks the bundle and drops groups the model already judged
DIFFERENT; `enqueue_identity_report` carries a servable stored verdict into the
queue. Neither half builds a client.
"""

import contextlib
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from openkos import config
from openkos.application import backends, identity_stage
from openkos.application import pending as application_pending
from openkos.resolution import adjudication
from openkos.resolution import candidates as cand
from openkos.state import adjudications as store
from openkos.state import derived
from openkos.state import pending_queue as pq
from tests.unit.application.curation_support import make_workspace, write_concept


def _section(*args: Any, **kwargs: Any) -> Any:
    return contextlib.nullcontext()


def _group(*members: str) -> cand.CandidateGroup:
    return cand.CandidateGroup(
        okf_type="Concept", member_ids=members, tier=cand.Tier.HIGH, trigger="alpha"
    )


def _report_of(*groups: cand.CandidateGroup, produced: int | None = None) -> Any:
    def find(bundle_dir: Path, **kwargs: Any) -> cand.CandidateGroupReport:
        return cand.CandidateGroupReport(
            groups=groups,
            produced=len(groups) if produced is None else produced,
            retained=len(groups),
        )

    return find


@pytest.fixture
def no_client(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        pytest.fail("a client was built")

    monkeypatch.setattr(backends, "chat_client", fail)


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    ws = make_workspace(tmp_path, monkeypatch)
    for slug in ("a", "b", "c", "d"):
        write_concept(ws, f"concepts/{slug}", title="Alpha" if slug in "ab" else "Beta")
    return ws


def _record(
    layout: config.WorkspaceLayout, members: tuple[str, ...], verdict: str
) -> None:
    digest_of = application_pending.current_finding_digest(layout.bundle_dir)
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        store.record_adjudications(
            conn,
            [
                store.Adjudication(
                    member_ids=members,
                    verdict=verdict,
                    confidence=0.9,
                    rationale="Judged.",
                    include_confidential=False,
                    input_digests=tuple(
                        store.InputDigest(m, str(digest_of(m))) for m in members
                    ),
                    rubric_digest=adjudication.rubric_digest(),
                )
            ],
        )
    finally:
        conn.close()


def _identity_payloads(layout: config.WorkspaceLayout) -> list[dict[str, Any]]:
    import json

    conn = sqlite3.connect(layout.findings_db_path)
    try:
        return [
            json.loads(i.payload)
            for i in pq.all_items(conn)
            if i.kind == "identity" and i.status in ("pending", "claimed")
        ]
    finally:
        conn.close()


def test_identity_report_drops_a_judged_different_group(
    root: Path, no_client: None
) -> None:
    layout = config.WorkspaceLayout(root)
    different = _group("concepts/a", "concepts/b")
    kept = _group("concepts/c", "concepts/d")
    _record(layout, different.member_ids, "different")

    report = identity_stage.identity_report(
        root, layout, find_candidates_report=_report_of(different, kept)
    )

    assert [g.member_ids for g in report.groups] == [kept.member_ids]


def test_identity_report_keeps_a_group_judged_same(root: Path, no_client: None) -> None:
    layout = config.WorkspaceLayout(root)
    same = _group("concepts/a", "concepts/b")
    _record(layout, same.member_ids, "same")

    report = identity_stage.identity_report(
        root, layout, find_candidates_report=_report_of(same)
    )

    assert [g.member_ids for g in report.groups] == [same.member_ids]


def test_identity_report_preserves_the_truncation_notice(
    root: Path, no_client: None
) -> None:
    layout = config.WorkspaceLayout(root)
    group = _group("concepts/a", "concepts/b")

    report = identity_stage.identity_report(
        root, layout, find_candidates_report=_report_of(group, produced=110)
    )

    assert report.truncation_notice == "1 of 110 candidate group(s) shown (cap reached)"


def _enqueue(root: Path, report: Any, conn: sqlite3.Connection) -> Any:
    layout = config.WorkspaceLayout(root)
    return identity_stage.enqueue_identity_report(
        conn, layout, report, commit_section=_section
    )


def test_enqueue_carries_a_servable_stored_verdict_and_amends_an_unjudged_row(
    root: Path, no_client: None
) -> None:
    layout = config.WorkspaceLayout(root)
    group = _group("concepts/a", "concepts/b")
    find = _report_of(group)
    report = identity_stage.identity_report(root, layout, find_candidates_report=find)
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        pq.ensure_schema(conn)
        first = _enqueue(root, report, conn)
        assert (first.inserted, first.amended) == (1, 0)
        assert _identity_payloads(layout)[0].get("adjudication") is None

        _record(layout, group.member_ids, "same")
        second = _enqueue(root, report, conn)
    finally:
        conn.close()

    assert (second.inserted, second.amended) == (0, 1)
    (payload,) = _identity_payloads(layout)
    assert payload["adjudication"]["verdict"] == "same"


def test_a_second_enqueue_is_unchanged_and_leaves_kind_stats_identical(
    root: Path, no_client: None
) -> None:
    layout = config.WorkspaceLayout(root)
    group = _group("concepts/a", "concepts/b")
    _record(layout, group.member_ids, "same")
    report = identity_stage.identity_report(
        root, layout, find_candidates_report=_report_of(group)
    )
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        pq.ensure_schema(conn)
        _enqueue(root, report, conn)
        before = pq.kind_stats(conn)
        again = _enqueue(root, report, conn)
        after = pq.kind_stats(conn)
    finally:
        conn.close()

    assert (again.inserted, again.amended, again.replaced, again.unchanged) == (
        0,
        0,
        0,
        1,
    )
    assert after == before
