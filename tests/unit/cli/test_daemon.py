"""`openkos daemon [--once]` (MVP 4 unit 6.2, issues #1139-#1141).

The verb drives the runner (`application/runner.py`): it installs the signal
handlers that set the cooperative stop, configures the daemon log, wires the
git and advisor ports the runner leaves open, loops on the runner's schedule,
and exits 0 on stop. It is the one SELF-LOCKING command: it never holds the
workspace lock for its lifetime.
"""

import contextlib
import dataclasses
import logging
import signal
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos import config, logsetup, userstate
from openkos.application import runner
from openkos.application.runtime import StopToken
from openkos.cli import daemon as daemon_module
from openkos.cli.main import app
from openkos.resolution import candidates as cand
from openkos.resolution.volatility_typing import TierSuggestion, TierSuggestionBatch
from openkos.state import jobs
from openkos.state import pending_queue as pq
from tests.unit.cli.commit_phase_support import lock_is_free, write_doc

cli = CliRunner()
_NOW = datetime(2026, 9, 30, 15, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _daemon_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned_git_identity: None
) -> Iterator[None]:
    """The log goes to a temp directory, never the real per-user one, and no
    handler outlives the test."""
    logsetup.reset_logging()
    monkeypatch.setattr(userstate, "log_dir", lambda *a, **k: tmp_path / "state-logs")
    yield
    logsetup.reset_logging()


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.chdir(ws)
    assert cli.invoke(app, ["init"]).exit_code == 0
    return ws


class _FailFastStop(StopToken):
    """A stop token whose idle wait fails the test instead of hanging it: a
    daemon that reaches its idle wait without a stop request is a defect in the
    test's premise (the signal never set the flag)."""

    def wait(self, timeout: float, **kwargs: object) -> bool:
        raise AssertionError("the daemon idled; the signal did not set the stop flag")


def _serve_with_token(monkeypatch: pytest.MonkeyPatch, token: StopToken) -> None:
    real_serve = daemon_module.serve

    def serve(*args: object, **kwargs: object) -> int:
        return real_serve(*args, **{**kwargs, "stop": token})  # type: ignore[arg-type]

    monkeypatch.setattr(daemon_module, "serve", serve)


class _Git:
    """Scriptable git effects: dirty paths and every commit the runner makes."""

    def __init__(self) -> None:
        self.dirty: set[str] = set()
        self.commits: list[list[str]] = []

    def paths_dirty(self, root: Path, paths: Sequence[str]) -> bool:
        return any(p in self.dirty for p in paths)

    def commit_paths(
        self, root: Path, paths: Sequence[str], message: str
    ) -> str | None:
        self.commits.append(list(paths))
        self.dirty -= set(paths)
        return "abc1234"


def _fake_ports(
    git: _Git, stages: Sequence[runner.AdvisorStage] = ()
) -> runner.RunnerPorts:
    return runner.RunnerPorts(
        refresh_derived=lambda root: None,
        commit_paths=git.commit_paths,
        paths_dirty=git.paths_dirty,
        repo_root=lambda root: root,
        has_git_identity=lambda root: True,
        advisor_stages=tuple(stages),
        lint_counts=lambda layout: {},
        now=lambda: _NOW,
    )


def _install_ports(monkeypatch: pytest.MonkeyPatch, ports: runner.RunnerPorts) -> None:
    monkeypatch.setattr(daemon_module, "production_ports", lambda root: ports)


def _job_rows(root: Path) -> list[jobs.JobRecord]:
    conn = jobs.open_jobs(config.WorkspaceLayout(root).jobs_db_path)
    try:
        return list(reversed(jobs.recent_jobs(conn)))
    finally:
        conn.close()


def _bundle_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted((root / "bundle").rglob("*"))
        if p.is_file()
    }


def _stage(name: str, seen: list[str]) -> runner.AdvisorStage:
    def run(ctx: runner.StageContext) -> runner.StageResult:
        seen.append(name)
        return runner.StageResult()

    return runner.AdvisorStage(name=name, run=run, uses_model=False)


# --- classification ----------------------------------------------------------


def test_daemon_is_registered_and_self_locking() -> None:
    from openkos.cli.main import _SELF_LOCKING_COMMANDS

    assert "daemon" in _SELF_LOCKING_COMMANDS


def test_daemon_refuses_outside_a_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    elsewhere = tmp_path / "plain"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    result = cli.invoke(app, ["daemon", "--once"])

    assert result.exit_code == 1
    assert result.stderr.startswith("openkos daemon: refusing to run -- ")


# --- --once ------------------------------------------------------------------


def test_once_runs_due_jobs_in_order_and_exits_zero(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git = _Git()
    git.dirty.add("bundle/concepts/a.md")
    layout = config.WorkspaceLayout(root)
    conn = jobs.open_jobs(layout.jobs_db_path)
    try:
        earlier = jobs.start_job(conn, "maintenance", "2026-09-01T00:00:00Z")
        jobs.finish_job(
            conn,
            earlier,
            outcome="completed",
            ended_at="2026-09-01T00:01:00Z",
            chat_calls=0,
            units_done=2,
            units_deferred=0,
            detail_code=None,
        )
        jobs.record_uncommitted_paths(conn, earlier, ["bundle/concepts/a.md"])
    finally:
        conn.close()
    seen: list[str] = []
    _install_ports(monkeypatch, _fake_ports(git, [_stage("only", seen)]))

    result = cli.invoke(app, ["daemon", "--once"])

    assert result.exit_code == 0, result.output
    rows = _job_rows(root)
    assert [(r.kind, r.outcome) for r in rows] == [
        ("maintenance", "completed"),
        ("commit-retry", "completed"),
        ("maintenance", "completed"),
    ]
    assert git.commits == [["bundle/concepts/a.md"]]
    assert seen == ["only"]
    assert "commit-retry: completed" in result.stdout
    assert result.stdout.index("commit-retry") < result.stdout.index(
        "maintenance: completed"
    )


def test_once_does_not_run_maintenance_that_is_not_due(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    _install_ports(monkeypatch, _fake_ports(_Git(), [_stage("s", seen)]))
    assert cli.invoke(app, ["daemon", "--once"]).exit_code == 0
    assert cli.invoke(app, ["daemon", "--once"]).exit_code == 0

    assert seen == ["s"]  # the second run found maintenance not yet due
    assert [r.kind for r in _job_rows(root)] == ["maintenance"]


def test_once_logs_job_start_and_outcome_to_the_daemon_log(
    root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_ports(monkeypatch, _fake_ports(_Git()))
    assert cli.invoke(app, ["daemon", "--once"]).exit_code == 0
    logsetup.reset_logging()

    text = (tmp_path / "state-logs").glob("*.log")
    contents = "".join(p.read_text(encoding="utf-8") for p in text)
    assert "maintenance job 1 started" in contents
    assert "maintenance job 1 ended: completed" in contents
    assert not list((root / "bundle").rglob("*.log"))


# --- signals -----------------------------------------------------------------


def test_sigterm_during_a_write_burst_completes_the_burst_then_stops(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran: list[str] = []
    burst: list[str] = []
    marker = root / ".openkos" / "burst.txt"

    def writer(ctx: runner.StageContext) -> runner.StageResult:
        ran.append("writer")
        with ctx.commit_section():
            marker.write_text("first", encoding="utf-8")
            burst.append("first")
            signal.raise_signal(signal.SIGTERM)  # arrives INSIDE the burst
            marker.write_text("first+second", encoding="utf-8")
            burst.append("second")
        return runner.StageResult()

    before = signal.getsignal(signal.SIGTERM)
    _serve_with_token(monkeypatch, _FailFastStop())
    seen: list[str] = []
    _install_ports(
        monkeypatch,
        _fake_ports(
            _Git(),
            [
                runner.AdvisorStage(name="writer", run=writer, uses_model=False),
                _stage("after", seen),
            ],
        ),
    )

    result = cli.invoke(app, ["daemon"])

    assert result.exit_code == 0, result.output
    assert burst == ["first", "second"]  # no partial write
    assert marker.read_text(encoding="utf-8") == "first+second"
    assert ran == ["writer"]  # nothing new started after the signal
    assert seen == []
    (row,) = _job_rows(root)
    assert (row.kind, row.outcome) == ("maintenance", "stopped")
    assert row.units_deferred >= 1
    assert signal.getsignal(signal.SIGTERM) is before  # handler restored


def test_sigint_also_stops_and_a_second_signal_is_harmless(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def stage(ctx: runner.StageContext) -> runner.StageResult:
        signal.raise_signal(signal.SIGINT)
        signal.raise_signal(signal.SIGTERM)  # a second signal while stopping
        return runner.StageResult()

    before = signal.getsignal(signal.SIGINT)
    _serve_with_token(monkeypatch, _FailFastStop())
    _install_ports(
        monkeypatch,
        _fake_ports(
            _Git(), [runner.AdvisorStage(name="s", run=stage, uses_model=False)]
        ),
    )

    result = cli.invoke(app, ["daemon"])

    assert result.exit_code == 0, result.output
    assert signal.getsignal(signal.SIGINT) is before


def test_the_signal_handler_sets_the_stop_token_without_taking_a_lock(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The handler must be a lock-free `StopToken.set()` (runtime.py): logging
    or `Event.set()` from a handler can deadlock the interrupted thread."""
    token = _FailFastStop()

    def stage(ctx: runner.StageContext) -> runner.StageResult:
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        handler(signal.SIGTERM, None)
        return runner.StageResult()

    _serve_with_token(monkeypatch, token)
    _install_ports(
        monkeypatch,
        _fake_ports(
            _Git(), [runner.AdvisorStage(name="s", run=stage, uses_model=False)]
        ),
    )

    assert cli.invoke(app, ["daemon"]).exit_code == 0
    assert token.is_set()


# --- the idle daemon holds no lock -------------------------------------------


class _IdleProbe(StopToken):
    """A stop token that says when the loop has reached its idle wait."""

    def __init__(self) -> None:
        super().__init__()
        self.idle = threading.Event()

    def wait(self, timeout: float, **kwargs: object) -> bool:
        self.idle.set()
        return super().wait(timeout, **kwargs)  # type: ignore[arg-type]


def test_an_idle_daemon_does_not_hold_the_workspace_lock(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = _IdleProbe()
    exit_code: list[int] = []
    ports = _fake_ports(_Git())

    thread = threading.Thread(
        target=lambda: exit_code.append(
            daemon_module.serve(
                root, once=False, ports=ports, stop=probe, install_signals=False
            )
        )
    )
    thread.start()
    try:
        assert probe.idle.wait(timeout=10), "the daemon never reached its idle wait"
        assert lock_is_free(root)
        with contextlib.ExitStack() as stack:
            from openkos import lock

            stack.enter_context(lock.workspace_lock(root))  # a person's verb
            assert probe.idle.is_set()
    finally:
        probe.set()
        thread.join(timeout=10)
    assert not thread.is_alive()
    assert exit_code == [0]


def test_the_loop_waits_with_the_stop_token_not_a_blocking_sleep(
    root: Path,
) -> None:
    waits: list[float] = []

    class _Recorder(StopToken):
        def wait(self, timeout: float, **kwargs: object) -> bool:
            waits.append(timeout)
            self.set()
            return True

    code = daemon_module.serve(
        root,
        once=False,
        ports=_fake_ports(_Git()),
        stop=_Recorder(),
        install_signals=False,
    )

    assert code == 0
    assert len(waits) == 1
    assert 0 < waits[0] <= 10


# --- the production advisor stages -------------------------------------------


def test_production_stages_enqueue_rows_and_write_nothing_under_bundle(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_doc(root, "concepts/a", {"type": "Concept", "title": "Alpha"})
    write_doc(root, "concepts/b", {"type": "Concept", "title": "Beta"})
    group = cand.CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/a", "concepts/b"),
        tier=cand.Tier.HIGH,
        trigger="alpha",
    )
    monkeypatch.setattr(
        "openkos.resolution.find_candidates_report",
        lambda bundle_dir, **kw: cand.CandidateGroupReport(
            groups=(group,), produced=1, retained=1
        ),
    )
    from openkos.graph.base import Edge
    from openkos.resolution.edge_typing import EdgeSuggestion, EdgeSuggestionBatch

    edge = Edge(source_id="concepts/a", target_id="concepts/b")
    monkeypatch.setattr(
        "openkos.resolution.edge_typing.candidate_edges",
        lambda bundle_dir, **kw: [edge],
    )
    monkeypatch.setattr(
        "openkos.resolution.edge_typing.suggest_edge_types",
        lambda edges, **kw: EdgeSuggestionBatch(
            results=[
                EdgeSuggestion(
                    edge=edge,
                    suggested_type="references",
                    rationale="r",
                    corrected_edge=None,
                )
            ]
        ),
    )
    monkeypatch.setattr(
        "openkos.resolution.volatility_typing.suggest_volatility",
        lambda bundle_dir, **kw: TierSuggestionBatch(
            results=[
                TierSuggestion(
                    type_name="Concept",
                    current_default="slow",
                    suggested_tier="fast",
                    rationale="r",
                )
            ]
        ),
    )
    before = _bundle_bytes(root)

    result = cli.invoke(app, ["daemon", "--once"])

    assert result.exit_code == 0, result.output
    assert _bundle_bytes(root) == before
    layout = config.WorkspaceLayout(root)
    conn = sqlite3.connect(layout.findings_db_path)
    try:
        kinds = sorted({i.kind for i in pq.open_items(conn)})
    finally:
        conn.close()
    assert kinds == ["identity", "relation_type", "volatility"]
    (row,) = _job_rows(root)
    assert (row.kind, row.outcome) == ("maintenance", "completed")
    assert row.units_done == 7  # refresh, lint counts, five advisor stages
    assert row.chat_calls == 0  # the patched leaves never touched the client


def test_production_stage_names_are_the_five_advisors_in_curate_order() -> None:
    names = [s.name for s in daemon_module.production_stages()]
    assert names == [
        "identity",
        "suggest-relations",
        "suggest-volatility",
        "contradictions",
        "revisions",
    ]
    assert [s.name for s in daemon_module.production_stages() if not s.uses_model] == [
        "identity"
    ]


def test_pending_hint_is_true_after_the_daemon_computes_the_queue(
    root: Path,
) -> None:
    absent = cli.invoke(app, ["pending"])
    assert "Run `openkos daemon --once` to compute it." in absent.stdout

    assert cli.invoke(app, ["daemon", "--once"]).exit_code == 0

    computed = cli.invoke(app, ["pending"])
    assert computed.exit_code == 0
    assert "has not been computed yet" not in computed.stdout


# --- the budget --------------------------------------------------------------


def test_budget_exhaustion_is_recorded_and_surfaced(root: Path) -> None:
    cfg_path = root / "openkos.yaml"
    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8")
        + "\nunattended:\n  max_calls_per_pass: 0\n",
        encoding="utf-8",
    )

    result = cli.invoke(app, ["daemon", "--once"])

    assert result.exit_code == 0, result.output
    (row,) = _job_rows(root)
    assert (row.kind, row.outcome) == ("maintenance", "budget_exhausted")
    assert row.detail_code == "max_calls_per_pass"
    assert row.chat_calls == 0
    assert "maintenance: budget_exhausted (max_calls_per_pass)" in result.stdout
    # The advisor that needs no model still ran; the model-using ones did not.
    assert row.units_done == 3
    assert row.units_deferred == 4


def test_model_stages_receive_the_live_budget_remainder(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_path = root / "openkos.yaml"
    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8")
        + "\nunattended:\n  max_calls_per_pass: 7\n",
        encoding="utf-8",
    )
    bounds: dict[str, int | None] = {}

    def spy(name: str):  # type: ignore[no-untyped-def]
        def run(ctx: runner.StageContext) -> runner.StageResult:
            bounds[name] = ctx.budget.remaining
            return runner.StageResult()

        return run

    stages = [
        dataclasses.replace(s, run=spy(s.name))
        for s in daemon_module.production_stages()
    ]
    _install_ports(monkeypatch, _fake_ports(_Git(), stages))
    assert cli.invoke(app, ["daemon", "--once"]).exit_code == 0
    assert set(bounds.values()) == {7}


def test_the_stage_bound_is_the_budget_remainder(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`max_calls` handed to each service is `ctx.budget.remaining`."""
    cfg_path = root / "openkos.yaml"
    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8")
        + "\nunattended:\n  max_calls_per_pass: 5\n",
        encoding="utf-8",
    )
    seen: dict[str, int | None] = {}

    from openkos.application import (
        contradictions_service,
        revisions,
        suggest_relations_service,
        suggest_volatility_service,
    )

    def capture(name: str, real):  # type: ignore[no-untyped-def]
        def wrapper(*args, **kwargs):  # type: ignore[no-untyped-def]
            for arg in (*args, *kwargs.values()):
                if hasattr(arg, "max_calls"):
                    seen[name] = arg.max_calls
            raise RuntimeError("stop here")

        return wrapper

    monkeypatch.setattr(
        contradictions_service,
        "run_contradictions",
        capture("contradictions", None),
    )
    monkeypatch.setattr(
        suggest_relations_service, "suggest_relations", capture("relations", None)
    )
    monkeypatch.setattr(
        suggest_volatility_service,
        "suggest_volatility_tiers",
        capture("volatility", None),
    )
    monkeypatch.setattr(revisions, "run_revisions", capture("revisions", None))

    for stage in daemon_module.production_stages():
        if not stage.uses_model:
            continue
        layout = config.WorkspaceLayout(root)
        from openkos.application import budget as budget_module
        from openkos.application.lock_wait import locked_commit_section
        from openkos.application.runtime import UnattendedPolicy

        run_budget = budget_module.start_budgeted_run(
            layout, config.read_config(root).unattended, _NOW
        )
        ctx = runner.StageContext(
            root=root,
            layout=layout,
            budget=run_budget,
            policy=UnattendedPolicy(),
            commit_section=locked_commit_section(root, wait_seconds=0),
            queue=lambda: (_ for _ in ()).throw(AssertionError("no queue")),
        )
        with pytest.raises(RuntimeError, match="stop here"):
            stage.run(ctx)

    assert seen == {
        "relations": 5,
        "volatility": 5,
        "contradictions": 5,
        "revisions": 5,
    }


# --- logging never carries content -------------------------------------------


def test_the_daemon_log_never_carries_document_text(
    root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sentinel = "SENTINEL-BODY-5d1e7c-do-not-log"
    write_doc(root, "concepts/a", {"type": "Concept", "title": "Alpha"}, sentinel)
    assert cli.invoke(app, ["daemon", "--once"]).exit_code == 0
    logsetup.reset_logging()

    logs = "".join(
        p.read_text(encoding="utf-8") for p in (tmp_path / "state-logs").glob("*.log")
    )
    assert logs  # the run did log
    assert sentinel not in logs


def test_serve_configures_daemon_logging_for_the_workspace(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured: list[Path | None] = []
    real = logsetup.configure_logging

    def spy(mode: str, *, root: Path | None = None):  # type: ignore[no-untyped-def]
        configured.append(root if mode == "daemon" else None)
        return real(mode, root=root)  # type: ignore[arg-type]

    monkeypatch.setattr(logsetup, "configure_logging", spy)
    _install_ports(monkeypatch, _fake_ports(_Git()))
    assert cli.invoke(app, ["daemon", "--once"]).exit_code == 0

    assert [r for r in configured if r is not None] == [root]
    assert logging.getLogger("openkos").propagate  # restored after the run


def test_a_backend_failure_inside_a_stage_is_recorded_as_failed_after_enqueueing(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openkos.graph.base import Edge
    from openkos.llm.base import BackendUnavailable
    from openkos.resolution.edge_typing import EdgeSuggestion, EdgeSuggestionBatch

    first = Edge(source_id="concepts/a", target_id="concepts/b")
    second = Edge(source_id="concepts/b", target_id="concepts/c")
    for name in "abc":
        write_doc(root, f"concepts/{name}", {"type": "Concept", "title": name})
    monkeypatch.setattr(
        "openkos.resolution.edge_typing.candidate_edges",
        lambda bundle_dir, **kw: [first, second],
    )
    monkeypatch.setattr(
        "openkos.resolution.edge_typing.suggest_edge_types",
        lambda edges, **kw: EdgeSuggestionBatch(
            results=[
                EdgeSuggestion(
                    edge=first,
                    suggested_type="references",
                    rationale="r",
                    corrected_edge=None,
                )
            ],
            failure=BackendUnavailable("down"),
            failed_index=2,
        ),
    )

    result = cli.invoke(app, ["daemon", "--once"])

    assert result.exit_code == 0, result.output
    (row,) = _job_rows(root)
    assert (row.outcome, row.detail_code) == ("failed", "backend_unavailable")
    conn = sqlite3.connect(config.WorkspaceLayout(root).findings_db_path)
    try:
        # The completed prefix was still enqueued before the failure surfaced.
        assert [i.kind for i in pq.open_items(conn)] == ["relation_type"]
    finally:
        conn.close()


def test_a_configured_inbox_says_the_watcher_is_not_enabled(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inbox = root.parent / "inbox"
    inbox.mkdir()
    cfg_path = root / "openkos.yaml"
    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8") + f"\nunattended:\n  inbox: {inbox}\n",
        encoding="utf-8",
    )
    _install_ports(monkeypatch, _fake_ports(_Git()))

    result = cli.invoke(app, ["daemon", "--once"])

    assert result.exit_code == 0, result.output
    assert "inbox watcher is not enabled" in result.stderr
