"""`openkos.api.status` and `openkos.api.pending_report`: lock-free, model-free,
write-free reads whose results hold counts, strings and API values only."""

import dataclasses
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

import pytest

from openkos import config, lock
from openkos.api import (
    ActivityEntry,
    JobSummary,
    KindCounts,
    PendingReport,
    PendingRow,
    QueueUnavailableError,
    Workspace,
    WorkspaceStatus,
    pending_report,
    status,
)
from tests.unit.api import support
from tests.unit.application.curation_support import make_workspace
from tests.unit.cli.commit_phase_support import write_doc


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return make_workspace(tmp_path, monkeypatch)


@pytest.fixture(autouse=True)
def no_client(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    support.forbid_model_clients(monkeypatch, "a read")


def _plain(value: object) -> bool:
    """Whether `value` holds only counts, strings and API-owned values."""
    if value is None or isinstance(value, str | int | bool | Path):
        return True
    if isinstance(value, tuple):
        return all(_plain(v) for v in value)
    if isinstance(value, Mapping):
        return all(_plain(k) and _plain(v) for k, v in value.items())
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return type(value).__module__.startswith("openkos.api") and all(
            _plain(getattr(value, f.name)) for f in dataclasses.fields(value)
        )
    return False


# -- status ------------------------------------------------------------------ #


def test_status_of_a_fresh_workspace(root: Path) -> None:
    result = status(Workspace.open(root))

    assert isinstance(result, WorkspaceStatus)
    assert result.root == root
    assert (result.sources, result.concepts) == (0, 0)
    assert dict(result.concepts_by_type) == {}
    assert result.conformance_findings == ()
    assert result.queue == "absent"
    assert dict(result.open_decisions_by_kind) == {}
    assert result.last_job is None
    assert result.jobs_unreadable is False
    assert result.vectors_missing is True
    assert result.edges is None
    assert result.stale_indexes == ()
    assert result.warnings == ()
    assert result.health == dataclasses.replace(result.health)  # a HealthCounts
    assert result.health.dangling == 0
    assert isinstance(result.recent_activity, tuple)
    assert all(isinstance(e, ActivityEntry) for e in result.recent_activity)


def test_status_counts_concepts_by_type_and_maps_findings_to_counts(
    root: Path,
) -> None:
    write_doc(root, "people/ada", {"type": "Person", "title": "Ada"}, "Ada.\n")
    write_doc(root, "people/bob", {"type": "Person", "title": "Bob"}, "Bob.\n")
    write_doc(
        root,
        "concepts/x",
        {"type": "Concept", "title": "X"},
        "Links to [gone](/concepts/missing.md).\n",
    )

    result = status(Workspace.open(root))

    assert result.concepts == 3
    assert dict(result.concepts_by_type) == {"Person": 2, "Concept": 1}
    assert result.health.dangling == 1
    assert isinstance(result.concepts_by_type, MappingProxyType)


def test_status_reports_the_queue_and_open_decisions_by_kind(root: Path) -> None:
    support.enqueue_identity(root, ("concepts/a", "concepts/b"))
    support.enqueue_volatility(root)

    result = status(Workspace.open(root))

    assert result.queue == "present"
    assert dict(result.open_decisions_by_kind) == {"identity": 1, "volatility": 1}


def test_status_summarises_the_last_job_and_whether_it_needs_attention(
    root: Path,
) -> None:
    support.record_failed_job(root)

    result = status(Workspace.open(root))

    assert result.last_job == JobSummary(
        kind="maintenance",
        started_at="2026-10-14T10:00:00Z",
        ended_at="2026-10-14T10:01:00Z",
        outcome="failed",
        detail_code="boom",
        units_deferred=3,
        needs_attention=True,
    )


def test_a_database_that_holds_no_queue_reads_as_an_absent_queue(root: Path) -> None:
    layout = config.WorkspaceLayout(root)
    layout.openkos_dir.mkdir(exist_ok=True)
    sqlite3.connect(layout.findings_db_path).close()  # a database that is no queue

    assert status(Workspace.open(root)).queue == "absent"


def test_status_takes_no_lock_builds_no_client_and_writes_nothing(root: Path) -> None:
    support.enqueue_identity(root, ("concepts/a", "concepts/b"))
    support.record_failed_job(root)
    before = support.snapshot(root)

    with lock.workspace_lock(root):  # another writer holds the lock
        result = status(Workspace.open(root))

    assert isinstance(result, WorkspaceStatus)
    assert support.snapshot(root) == before


def test_a_status_result_holds_only_counts_strings_and_api_values(root: Path) -> None:
    support.enqueue_identity(root, ("concepts/a", "concepts/b"))
    support.record_failed_job(root)

    assert _plain(status(Workspace.open(root)))


# -- pending report ---------------------------------------------------------- #


def test_an_absent_queue_is_not_an_empty_queue(root: Path) -> None:
    absent = pending_report(Workspace.open(root))

    layout = config.WorkspaceLayout(root)
    support.enqueue_identity(root, ("concepts/a", "concepts/b"))
    conn = sqlite3.connect(layout.findings_db_path)
    conn.execute("DELETE FROM pending_items")
    conn.commit()
    conn.close()
    empty = pending_report(Workspace.open(root))

    assert isinstance(absent, PendingReport)
    assert absent.queue_computed is False
    assert absent.open_rows == ()
    assert absent.stats == ()
    assert empty.queue_computed is True
    assert empty.open_rows == ()


def test_the_report_lists_open_rows_with_subject_and_resolving_command(
    root: Path,
) -> None:
    support.enqueue_identity(
        root, ("concepts/a", "concepts/b"), verdict="same", rationale="Same."
    )
    support.enqueue_volatility(root, "Person")

    report = pending_report(Workspace.open(root))

    rows = {row.kind: row for row in report.open_rows}
    assert set(rows) == {"identity", "volatility"}
    identity = rows["identity"]
    assert isinstance(identity, PendingRow)
    assert identity.targets == ("concepts/a", "concepts/b")
    assert identity.subject == "concepts/a, concepts/b"
    assert identity.resolving_command == "openkos adjudicate --apply"
    assert identity.decision_key.startswith("identity:")
    assert rows["volatility"].subject == "type Person"
    assert rows["volatility"].targets == ()


def test_a_resolved_row_is_not_listed_as_open(root: Path) -> None:
    support.enqueue_identity(root, ("concepts/a", "concepts/b"))
    support.enqueue_identity(root, ("concepts/c", "concepts/d"))
    conn = sqlite3.connect(config.WorkspaceLayout(root).findings_db_path)
    conn.execute(
        "UPDATE pending_items SET status = 'declined' "
        "WHERE decision_key = (SELECT MAX(decision_key) FROM pending_items)"
    )
    conn.commit()
    conn.close()

    report = pending_report(Workspace.open(root))

    assert len(report.open_rows) == 1
    assert sum(s.enqueued for s in report.stats) == 2


def test_the_report_carries_lifetime_counts_per_kind(root: Path) -> None:
    support.enqueue_identity(root, ("concepts/a", "concepts/b"))

    report = pending_report(Workspace.open(root))

    by_kind = {s.kind: s for s in report.stats}
    assert isinstance(by_kind["identity"], KindCounts)
    assert (by_kind["identity"].enqueued, by_kind["identity"].open) == (1, 1)
    assert "volatility" not in by_kind  # no row of that kind was ever enqueued


def test_the_report_lists_attention_jobs_as_summaries(root: Path) -> None:
    support.record_failed_job(root)

    report = pending_report(Workspace.open(root))

    assert [(j.kind, j.outcome, j.needs_attention) for j in report.attention] == [
        ("maintenance", "failed", True)
    ]
    assert report.jobs_unreadable is False


def test_an_unreadable_job_record_sets_the_flag_without_raising(root: Path) -> None:
    layout = config.WorkspaceLayout(root)
    layout.openkos_dir.mkdir(exist_ok=True)
    (layout.openkos_dir / "jobs.db").write_bytes(b"not a database" * 30)

    report = pending_report(Workspace.open(root))

    assert report.jobs_unreadable is True


def test_an_unreadable_queue_raises_the_public_unavailable_error(root: Path) -> None:
    layout = config.WorkspaceLayout(root)
    layout.openkos_dir.mkdir(exist_ok=True)
    layout.findings_db_path.write_bytes(b"this is not a sqlite database" * 20)

    with pytest.raises(QueueUnavailableError):
        pending_report(Workspace.open(root))


def test_the_report_is_lock_free_model_free_and_write_free(root: Path) -> None:
    support.enqueue_identity(root, ("concepts/a", "concepts/b"))
    support.record_failed_job(root)
    before = support.snapshot(root)

    with lock.workspace_lock(root):
        report = pending_report(Workspace.open(root))

    assert report.queue_computed is True
    assert support.snapshot(root) == before
    assert _plain(report)
