"""`openkos pending`: the read-only view over the pending-work queue
(`.openkos/findings.db`) and the unattended job record (`.openkos/jobs.db`)
(#1141, pending-work "`openkos pending` Lists The Queue Read-Only").

The output goldens pin the whole stream. The verb takes no lock, makes no
model call, writes nothing, and never prints a row's payload (proposal text can
carry a person's words), only kind, target ids and the resolving command.
"""

import contextlib
import hashlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos.cli.main import _READ_ONLY_COMMANDS, app
from openkos.config import WorkspaceLayout
from openkos.graph import sqlite_graph
from openkos.resolution import candidates
from openkos.state import derived, jobs
from openkos.state import pending_queue as pq

runner = CliRunner()

_ABSENT = (
    "The pending-work queue has not been computed yet.\n"
    "Run `openkos daemon --once` to compute it.\n"
)


@contextlib.contextmanager
def _section() -> Iterator[None]:
    yield


@pytest.fixture
def workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned_git_identity: None
) -> WorkspaceLayout:
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0
    return WorkspaceLayout(tmp_path)


def _queue(layout: WorkspaceLayout) -> sqlite3.Connection:
    conn = derived.open_derived_connection(layout.findings_db_path)
    pq.ensure_schema(conn)
    return conn


def _enqueue(
    conn: sqlite3.Connection,
    layout: WorkspaceLayout,
    kind: str,
    targets: tuple[str, ...],
    *,
    payload: str = "{}",
) -> None:
    key = {
        "identity": lambda: pq.identity_key(targets),
        "relation_type": lambda: pq.relation_type_key(*targets[:2]),
        "contradiction": lambda: pq.contradiction_key((targets[0], targets[1]), None),
        "volatility": lambda: pq.volatility_key(targets[0]),
        "revision": lambda: pq.revision_key((targets[0], targets[1])),
        "watch_refusal": lambda: pq.watch_refusal_key(targets[0]),
    }[kind]()
    pq.upsert_proposal(
        conn,
        pq.Proposal(
            kind=kind,
            key_body=key,
            producer="test/1",
            payload=payload,
            targets=targets,
        ),
        commit_section=_section,
        bundle_dir=layout.bundle_dir,
    )


def _resolve(
    conn: sqlite3.Connection, item_id: int, status: str, resolution: str
) -> None:
    conn.execute(
        "UPDATE pending_items SET status=?, resolution=?, resolved_at='2026-01-02'"
        " WHERE id=?",
        (status, resolution, item_id),
    )
    conn.commit()


def _tree_digest(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
        and ".git/" not in p.as_posix()
        # A read-only open of a WAL database makes empty -wal/-shm sidecars.
        and not p.name.endswith(("-wal", "-shm"))
    }


def test_pending_is_classified_read_only() -> None:
    assert "pending" in _READ_ONLY_COMMANDS


def test_absent_queue_is_reported_absent_never_empty(
    workspace: WorkspaceLayout,
) -> None:
    result = runner.invoke(app, ["pending"])
    assert result.exit_code == 0
    assert result.output == _ABSENT
    assert "nothing" not in result.output.lower()


def test_findings_db_without_queue_table_is_absent_too(
    workspace: WorkspaceLayout,
) -> None:
    conn = derived.open_derived_connection(workspace.findings_db_path)
    conn.execute("CREATE TABLE unrelated (x INTEGER)")
    conn.commit()
    conn.close()
    result = runner.invoke(app, ["pending"])
    assert result.exit_code == 0
    assert result.output == _ABSENT


def test_absent_queue_with_stats_is_still_absent(workspace: WorkspaceLayout) -> None:
    result = runner.invoke(app, ["pending", "--stats"])
    assert result.exit_code == 0
    assert result.output == _ABSENT


def test_empty_queue_says_no_open_rows(workspace: WorkspaceLayout) -> None:
    _queue(workspace).close()
    result = runner.invoke(app, ["pending"])
    assert result.exit_code == 0
    assert result.output == "No open pending-work rows.\n"


def test_open_rows_are_grouped_by_kind_with_their_resolving_command(
    workspace: WorkspaceLayout,
) -> None:
    conn = _queue(workspace)
    _enqueue(conn, workspace, "contradiction", ("concepts/x", "concepts/y"))
    _enqueue(conn, workspace, "identity", ("concepts/a", "concepts/b"))
    _enqueue(conn, workspace, "identity", ("concepts/c", "concepts/d"))
    _resolve(conn, 3, "applied", "as_proposed")
    conn.close()
    result = runner.invoke(app, ["pending"])
    assert result.exit_code == 0
    assert result.output == (
        "Pending work: 2 open row(s).\n"
        "\n"
        "identity (1)\n"
        "  - concepts/a, concepts/b [pending]\n"
        "    resolve: openkos adjudicate --apply"
        " (y merges, s skips, d records keep-distinct)\n"
        "    or: openkos duplicates --keep-distinct concepts/a"
        " --keep-distinct concepts/b\n"
        "\n"
        "contradiction (1)\n"
        "  - concepts/x, concepts/y [pending]\n"
        "    resolve: openkos contradictions\n"
    )


def test_all_also_lists_resolved_declined_and_stale_rows(
    workspace: WorkspaceLayout,
) -> None:
    conn = _queue(workspace)
    _enqueue(conn, workspace, "identity", ("concepts/a", "concepts/b"))
    _enqueue(conn, workspace, "identity", ("concepts/c", "concepts/d"))
    _enqueue(conn, workspace, "identity", ("concepts/e", "concepts/f"))
    _enqueue(conn, workspace, "identity", ("concepts/g", "concepts/h"))
    _resolve(conn, 2, "applied", "as_proposed")
    _resolve(conn, 3, "declined", "declined")
    _resolve(conn, 4, "stale", "stale")
    conn.close()
    default = runner.invoke(app, ["pending"])
    assert "concepts/c" not in default.output
    result = runner.invoke(app, ["pending", "--all"])
    assert result.exit_code == 0
    assert result.output == (
        "Pending work: 1 open row(s).\n"
        "\n"
        "identity (4)\n"
        "  - concepts/a, concepts/b [pending]\n"
        "    resolve: openkos adjudicate --apply"
        " (y merges, s skips, d records keep-distinct)\n"
        "    or: openkos duplicates --keep-distinct concepts/a"
        " --keep-distinct concepts/b\n"
        "  - concepts/c, concepts/d [applied]\n"
        "  - concepts/e, concepts/f [declined]\n"
        "  - concepts/g, concepts/h [stale]\n"
    )


def test_all_on_a_queue_with_only_resolved_rows_is_not_called_empty(
    workspace: WorkspaceLayout,
) -> None:
    conn = _queue(workspace)
    _enqueue(conn, workspace, "volatility", ("person",))
    _resolve(conn, 1, "applied", "modified")
    conn.close()
    plain = runner.invoke(app, ["pending"])
    assert plain.output == "No open pending-work rows.\n"
    result = runner.invoke(app, ["pending", "--all"])
    assert result.output == (
        "Pending work: 0 open row(s).\n\nvolatility (1)\n  - person [applied]\n"
    )


def test_stats_reports_per_kind_counts_fraction_and_lifetime_caveat(
    workspace: WorkspaceLayout,
) -> None:
    conn = _queue(workspace)
    for i, pair in enumerate(("ab", "cd", "ef", "gh", "ij")):
        _enqueue(
            conn,
            workspace,
            "identity",
            (f"concepts/{pair[0]}", f"concepts/{pair[1]}"),
        )
        del i
    _enqueue(conn, workspace, "contradiction", ("concepts/x", "concepts/y"))
    _resolve(conn, 1, "applied", "as_proposed")
    _resolve(conn, 2, "applied", "as_proposed")
    _resolve(conn, 3, "declined", "declined")
    _resolve(conn, 4, "stale", "stale")
    conn.close()
    result = runner.invoke(app, ["pending", "--stats"])
    assert result.exit_code == 0
    assert result.output == (
        "Pending-work statistics. Counts cover only the queue's current lifetime:"
        " a rebuild or purge resets applied history.\n"
        "\n"
        "kind           enqueued  as proposed  modified  declined  stale  open"
        "  as proposed / resolved\n"
        "identity       5         2            0         1         1      1"
        "     2 of 3 (67%)\n"
        "contradiction  1         0            0         0         0      1"
        "     n/a\n"
    )


def test_stats_counts_applied_modified_apart_from_as_proposed(
    workspace: WorkspaceLayout,
) -> None:
    conn = _queue(workspace)
    _enqueue(conn, workspace, "relation_type", ("concepts/a", "concepts/b"))
    _enqueue(conn, workspace, "relation_type", ("concepts/c", "concepts/d"))
    _resolve(conn, 1, "applied", "as_proposed")
    _resolve(conn, 2, "applied", "modified")
    conn.close()
    result = runner.invoke(app, ["pending", "--stats"])
    assert "relation_type  2         1            1         0         0      0" in (
        result.output
    )
    assert "1 of 2 (50%)" in result.output


def test_unattended_outcomes_needing_attention_follow_the_queue(
    workspace: WorkspaceLayout,
) -> None:
    conn = _queue(workspace)
    _enqueue(conn, workspace, "identity", ("concepts/a", "concepts/b"))
    conn.close()
    jconn = jobs.open_jobs(workspace.openkos_dir / "jobs.db")
    ok = jobs.start_job(jconn, "maintenance", "2026-01-01T00:00:00+00:00")
    jobs.finish_job(
        jconn,
        ok,
        outcome="completed",
        ended_at="2026-01-01T00:01:00+00:00",
        chat_calls=1,
        units_done=1,
        units_deferred=0,
    )
    bad = jobs.start_job(jconn, "maintenance", "2026-01-02T00:00:00+00:00")
    jobs.finish_job(
        jconn,
        bad,
        outcome="budget_exhausted",
        ended_at="2026-01-02T00:01:00+00:00",
        chat_calls=4,
        units_done=2,
        units_deferred=3,
        detail_code="max_sources_per_pass",
    )
    jconn.close()
    result = runner.invoke(app, ["pending"])
    assert result.exit_code == 0
    assert result.output == (
        "Pending work: 1 open row(s).\n"
        "\n"
        "identity (1)\n"
        "  - concepts/a, concepts/b [pending]\n"
        "    resolve: openkos adjudicate --apply"
        " (y merges, s skips, d records keep-distinct)\n"
        "    or: openkos duplicates --keep-distinct concepts/a"
        " --keep-distinct concepts/b\n"
        "\n"
        "Needs attention (unattended jobs):\n"
        "  maintenance job 2: budget_exhausted (max_sources_per_pass),"
        " 3 deferred, 2026-01-02T00:01:00+00:00\n"
    )


def test_job_outcomes_show_even_when_the_queue_is_absent(
    workspace: WorkspaceLayout,
) -> None:
    jconn = jobs.open_jobs(workspace.openkos_dir / "jobs.db")
    job = jobs.start_job(jconn, "watch", "2026-01-02T00:00:00+00:00")
    jobs.finish_job(
        jconn,
        job,
        outcome="timed_out",
        ended_at="2026-01-02T00:01:00+00:00",
        chat_calls=0,
        units_done=0,
        units_deferred=1,
    )
    jconn.close()
    result = runner.invoke(app, ["pending"])
    assert result.output == (
        _ABSENT + "\nNeeds attention (unattended jobs):\n"
        "  watch job 1: timed_out, 1 deferred, 2026-01-02T00:01:00+00:00\n"
    )


def test_an_unreadable_job_record_is_noted_and_does_not_hide_the_queue(
    workspace: WorkspaceLayout,
) -> None:
    conn = _queue(workspace)
    _enqueue(conn, workspace, "revision", ("concepts/a", "concepts/b"))
    conn.close()
    (workspace.openkos_dir / "jobs.db").write_bytes(b"not a database" * 100)
    result = runner.invoke(app, ["pending"])
    assert result.exit_code == 0
    assert "concepts/a, concepts/b [pending]" in result.output
    assert "job record" in result.output
    assert "unreadable" in result.output


def test_listing_prints_no_payload_text(workspace: WorkspaceLayout) -> None:
    sentinel = "SENTINEL-PAYLOAD-7f3a9c"
    conn = _queue(workspace)
    _enqueue(
        conn,
        workspace,
        "contradiction",
        ("concepts/a", "concepts/b"),
        payload=f'{{"rationale":"{sentinel}"}}',
    )
    conn.close()
    for args in (["pending"], ["pending", "--all"], ["pending", "--stats"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 0
        assert sentinel not in result.output


def test_pending_writes_nothing_and_leaves_the_claim_alone(
    workspace: WorkspaceLayout,
) -> None:
    conn = _queue(workspace)
    _enqueue(conn, workspace, "identity", ("concepts/a", "concepts/b"))
    conn.close()
    before = _tree_digest(workspace.root)
    for args in (["pending"], ["pending", "--all"], ["pending", "--stats"]):
        assert runner.invoke(app, args).exit_code == 0
    assert _tree_digest(workspace.root) == before
    check = sqlite3.connect(workspace.findings_db_path)
    assert check.execute("SELECT status FROM pending_items").fetchall() == [
        ("pending",)
    ]
    check.close()
    assert not (workspace.openkos_dir / "jobs.db").exists()


def test_outside_a_workspace_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["pending"])
    assert result.exit_code == 1
    assert "openkos pending: refusing to run" in result.output


def test_an_unreadable_queue_file_refuses_without_claiming_empty(
    workspace: WorkspaceLayout,
) -> None:
    workspace.openkos_dir.mkdir(exist_ok=True)
    workspace.findings_db_path.write_bytes(b"not a database" * 100)
    result = runner.invoke(app, ["pending"])
    assert result.exit_code == 1
    assert "not available" in result.output
    assert "nothing" not in result.output.lower()


def test_a_volatility_row_names_its_concept_type_not_an_empty_subject(
    workspace: WorkspaceLayout,
) -> None:
    conn = _queue(workspace)
    pq.upsert_proposal(
        conn,
        pq.Proposal(
            kind="volatility",
            key_body=pq.volatility_key("Person"),
            producer="test/1",
            payload='{"type_name": "Person", "suggested_tier": "slow"}',
            targets=(),
        ),
        commit_section=_section,
        bundle_dir=workspace.bundle_dir,
    )
    conn.close()

    result = runner.invoke(app, ["pending"])

    assert result.output == (
        "Pending work: 1 open row(s).\n"
        "\n"
        "volatility (1)\n"
        "  - type Person [pending]\n"
        "    resolve: openkos curate\n"
    )


def test_a_watch_refusal_row_names_the_refused_file_in_its_resolve_hint(
    workspace: WorkspaceLayout,
) -> None:
    inbox = workspace.root.parent / "my inbox"
    inbox.mkdir()
    (workspace.root / "openkos.yaml").write_text(
        (workspace.root / "openkos.yaml").read_text(encoding="utf-8")
        + f"\nunattended:\n  inbox: '{inbox}'\n",
        encoding="utf-8",
    )
    conn = _queue(workspace)
    _enqueue(
        conn,
        workspace,
        "watch_refusal",
        ("sources/big",),
        payload='{"inbox_path": "big.md", "reason": "x"}',
    )
    conn.close()

    result = runner.invoke(app, ["pending"])

    assert (
        f"    resolve: openkos ingest '{inbox.resolve() / 'big.md'}'\n" in result.output
    )
    assert "<" not in result.output


def test_a_row_with_an_unreadable_payload_keeps_a_generic_hint(
    workspace: WorkspaceLayout,
) -> None:
    conn = _queue(workspace)
    _enqueue(conn, workspace, "watch_refusal", ("sources/big",), payload="not json")
    conn.close()

    result = runner.invoke(app, ["pending"])

    assert "    resolve: openkos ingest <the refused file>\n" in result.output


def test_an_identity_row_judged_same_points_at_the_merge_walk(
    workspace: WorkspaceLayout,
) -> None:
    conn = _queue(workspace)
    _enqueue(
        conn,
        workspace,
        "identity",
        ("concepts/a", "concepts/b"),
        payload='{"adjudication": {"verdict": "same"}}',
    )
    conn.close()

    result = runner.invoke(app, ["pending"])

    assert "    resolve: openkos adjudicate --apply\n" in result.output


def test_a_row_with_no_target_and_no_type_still_renders_a_subject(
    workspace: WorkspaceLayout,
) -> None:
    conn = _queue(workspace)
    _enqueue(conn, workspace, "identity", ())
    conn.close()

    result = runner.invoke(app, ["pending"])

    assert "  - (no subject) [pending]\n" in result.output
    assert "    resolve: openkos duplicates --keep-distinct\n" in result.output


def test_a_larger_identity_group_points_at_the_merge_commands_not_a_prompt(
    workspace: WorkspaceLayout,
) -> None:
    """`adjudicate --apply` merges only 2-member groups; for a larger one it
    prints the exact pairwise `merge` lines, so the hint says that instead of
    promising a y/s/d prompt (#1265)."""
    conn = _queue(workspace)
    _enqueue(conn, workspace, "identity", ("concepts/a", "concepts/b", "concepts/c"))
    conn.close()

    result = runner.invoke(app, ["pending"])

    assert (
        "    resolve: openkos adjudicate --apply"
        " (prints the pairwise `openkos merge` commands)\n"
        "    or: openkos duplicates --keep-distinct concepts/a"
        " --keep-distinct concepts/b --keep-distinct concepts/c\n"
    ) in result.output


def test_an_identity_kind_at_the_candidate_cap_says_more_may_exist(
    workspace: WorkspaceLayout,
) -> None:
    """The advisor keeps at most `_MAX_CANDIDATE_GROUPS` groups per run, so a
    kind listed at exactly the cap may be hiding more; `pending` says so and
    names the verb that reports the total (#1265)."""
    cap = candidates._MAX_CANDIDATE_GROUPS
    conn = _queue(workspace)
    for n in range(cap):
        _enqueue(conn, workspace, "identity", (f"concepts/a{n}", f"concepts/b{n}"))
    conn.close()

    result = runner.invoke(app, ["pending"])

    assert f"identity ({cap})\n" in result.output
    assert (
        f"  note: {cap} is the per-run candidate cap, so more may exist;"
        " `openkos duplicates` reports the total.\n"
    ) in result.output


def test_an_identity_kind_under_the_candidate_cap_carries_no_cap_note(
    workspace: WorkspaceLayout,
) -> None:
    cap = candidates._MAX_CANDIDATE_GROUPS
    conn = _queue(workspace)
    for n in range(cap - 1):
        _enqueue(conn, workspace, "identity", (f"concepts/a{n}", f"concepts/b{n}"))
    conn.close()

    result = runner.invoke(app, ["pending"])

    assert "candidate cap" not in result.output


def test_a_relation_type_kind_at_its_cap_names_the_verb_that_reports_the_total(
    workspace: WorkspaceLayout,
) -> None:
    cap = sqlite_graph._MAX_CANDIDATE_EDGES
    conn = _queue(workspace)
    for n in range(cap):
        _enqueue(conn, workspace, "relation_type", (f"concepts/a{n}", f"concepts/b{n}"))
    conn.close()

    result = runner.invoke(app, ["pending"])

    assert "`openkos suggest-relations` reports the total." in result.output
