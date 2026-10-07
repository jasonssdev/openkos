"""`openkos daemon [--once]` (MVP 4 unit 6.2, issues #1139-#1141).

The verb drives the runner (`application/runner.py`): it installs the signal
handlers that set the cooperative stop, configures the daemon log, wires the
git and advisor ports the runner leaves open, loops on the runner's schedule,
and exits 0 on stop. It is the one SELF-LOCKING command: it never holds the
workspace lock for its lifetime.
"""

import contextlib
import dataclasses
import io
import logging
import signal
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos import config, logsetup, userstate
from openkos.application import runner
from openkos.application.runtime import StopToken
from openkos.cli import daemon as daemon_module
from openkos.cli import observability
from openkos.cli.main import app
from openkos.resolution import adjudication
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


class _Clock:
    def __init__(self) -> None:
        self.now = _NOW

    def __call__(self) -> datetime:
        return self.now


class _DecliningModel:
    """A backend that extracts nothing and counts the calls it receives."""

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages: Sequence[object]) -> str:
        self.calls += 1
        return '{"extract": false}'


def _configure_inbox(root: Path, *, quiet_seconds: int = 5) -> Path:
    inbox = root.parent / "inbox"
    inbox.mkdir()
    cfg_path = root / "openkos.yaml"
    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8")
        + f"\nunattended:\n  inbox: {inbox}\n  quiet_seconds: {quiet_seconds}\n",
        encoding="utf-8",
    )
    return inbox


def _watching_ports(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock, model: _DecliningModel
) -> None:
    """The REAL production ports (so the watch wiring under test is the
    daemon's own), with only the clock, the derived refresh and the model
    replaced."""
    real = daemon_module.production_ports

    def ports(root: Path) -> runner.RunnerPorts:
        return dataclasses.replace(
            real(root),
            now=clock,
            refresh_derived=lambda r: None,
            advisor_stages=(),
        )

    monkeypatch.setattr(daemon_module, "production_ports", ports)
    monkeypatch.setattr(
        "openkos.cli.main._chat_client", lambda cfg, *, task=None: model
    )


def test_a_daemon_with_an_inbox_imports_a_settled_file(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inbox = _configure_inbox(root)
    (inbox / "note.md").write_text("Notes about self-control.\n", encoding="utf-8")
    clock, model = _Clock(), _DecliningModel()
    _watching_ports(monkeypatch, clock, model)

    first = cli.invoke(app, ["daemon", "--once"])
    assert first.exit_code == 0, first.output
    assert not (root / "raw" / "note.md").exists()  # first sight starts the clock
    clock.now += timedelta(seconds=6)
    second = cli.invoke(app, ["daemon", "--once"])

    assert second.exit_code == 0, second.output
    assert "not enabled" not in second.output
    assert "daemon: watch: completed" in second.output
    assert (root / "raw" / "note.md").read_bytes() == b"Notes about self-control.\n"
    assert model.calls >= 1
    watch_rows = [r for r in _job_rows(root) if r.kind == "watch"]
    assert [(r.outcome, r.units_done) for r in watch_rows] == [("completed", 1)]
    assert watch_rows[0].chat_calls == model.calls  # counted by the budgeted run


def test_a_daemon_without_an_inbox_runs_no_watch(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock, model = _Clock(), _DecliningModel()
    _watching_ports(monkeypatch, clock, model)

    result = cli.invoke(app, ["daemon", "--once"])
    clock.now += timedelta(seconds=6)
    cli.invoke(app, ["daemon", "--once"])

    assert result.exit_code == 0, result.output
    assert "inbox" not in result.output
    assert [r.kind for r in _job_rows(root) if r.kind == "watch"] == []
    assert model.calls == 0


# --- feedback while it works (#1225) -------------------------------------------


def _tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


def test_a_file_that_has_not_settled_is_announced_with_the_quiet_window(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first run only STARTS the clock (observation-based settling, ADR-0038);
    it must say so instead of finishing silently."""
    inbox = _configure_inbox(root, quiet_seconds=30)
    (inbox / "note.md").write_text("Notes about self-control.\n", encoding="utf-8")
    _watching_ports(monkeypatch, _Clock(), _DecliningModel())
    _tty(monkeypatch)

    result = cli.invoke(app, ["daemon", "--once"])

    assert result.exit_code == 0, result.output
    assert (
        "openkos daemon: 1 file seen in the inbox; will import after 30s quiet"
        in result.output
    )
    assert not (root / "raw" / "note.md").exists()


def test_the_settling_notice_is_silent_when_stderr_is_not_a_tty(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inbox = _configure_inbox(root)
    (inbox / "note.md").write_text("Notes about self-control.\n", encoding="utf-8")
    _watching_ports(monkeypatch, _Clock(), _DecliningModel())

    result = cli.invoke(app, ["daemon", "--once"])

    assert "file seen" not in result.output


def test_a_maintenance_pass_names_each_stage_as_it_starts(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    stages = [_stage("alpha", seen), _stage("beta", seen)]
    _install_ports(monkeypatch, _fake_ports(_Git(), stages))
    _tty(monkeypatch)

    result = cli.invoke(app, ["daemon", "--once"])

    assert result.exit_code == 0, result.output
    assert "openkos daemon: maintenance: alpha (1/2)..." in result.output
    assert "openkos daemon: maintenance: beta (2/2)..." in result.output
    assert result.output.index("alpha (1/2)") < result.output.index("beta (2/2)")


def test_the_production_observers_report_per_item_progress_on_a_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Tty:
        def isatty(self) -> bool:
            return True

        def write(self, text: str) -> int:
            return len(text)

        def flush(self) -> None:
            return None

    monkeypatch.setattr("sys.stderr", _Tty())
    observers = (
        daemon_module._DaemonReindexObserver(),
        daemon_module._DaemonContradictionsObserver(),
        daemon_module._DaemonVolatilityObserver(),
        daemon_module._DaemonRevisionsObserver(),
    )

    assert all(o.progress_callback() is not None for o in observers)
    monkeypatch.setattr("sys.stderr", io.StringIO())  # not a TTY
    assert all(o.progress_callback() is None for o in observers)


def test_the_daemon_log_records_an_end_line_for_a_once_run(
    root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_ports(monkeypatch, _fake_ports(_Git()))
    assert cli.invoke(app, ["daemon", "--once"]).exit_code == 0
    logsetup.reset_logging()

    contents = "".join(
        p.read_text(encoding="utf-8") for p in (tmp_path / "state-logs").glob("*.log")
    )
    assert "daemon started (once=True)" in contents
    assert "daemon finished (once=True)" in contents


def test_the_announcer_drops_a_repeat_until_it_is_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines: list[str] = []
    monkeypatch.setattr(
        observability,
        "stage_notice",
        lambda verb, message: lines.append(message),
    )
    announce = daemon_module._Announcer()

    announce("1 file seen")
    announce("1 file seen")
    announce("2 files seen")
    announce.reset()
    announce("2 files seen")

    assert lines == ["1 file seen", "2 files seen", "2 files seen"]


def test_the_relations_observer_reports_per_edge_progress_on_a_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[str] = []

    class _Tty:
        def isatty(self) -> bool:
            return True

        def write(self, text: str) -> int:
            writes.append(text)
            return len(text)

        def flush(self) -> None:
            return None

    monkeypatch.setattr("sys.stderr", _Tty())
    observer = daemon_module._DaemonRelationsObserver()

    observer.edge_progress(1, 2, object())
    observer.edge_progress(2, 2, object())

    assert "".join(writes).count("untyped edge") == 2
    monkeypatch.setattr("sys.stderr", io.StringIO())
    daemon_module._DaemonRelationsObserver().edge_progress(1, 1, object())


def _identity_rows(root: Path) -> list[tuple[str, str]]:
    conn = sqlite3.connect(config.WorkspaceLayout(root).findings_db_path)
    try:
        return [
            (i.status, i.resolution or "")
            for i in pq.all_items(conn)
            if i.kind == "identity"
        ]
    finally:
        conn.close()


def _judge_pair(root: Path, verdict: str) -> None:
    from openkos.application import pending as application_pending
    from openkos.state import adjudications as store
    from openkos.state import derived

    layout = config.WorkspaceLayout(root)
    digest_of = application_pending.current_finding_digest(layout.bundle_dir)
    members = ("concepts/a", "concepts/b")
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


def test_daemon_retires_an_identity_row_once_the_group_is_judged_different(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_doc(root, "concepts/a", {"type": "Concept", "title": "Alpha"})
    write_doc(root, "concepts/b", {"type": "Concept", "title": "Alpha"})
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
    assert cli.invoke(app, ["daemon", "--once"]).exit_code == 0
    assert _identity_rows(root) == [("pending", "")]

    _judge_pair(root, "different")
    # Maintenance is not due again right after a run; forget that it ran.
    config.WorkspaceLayout(root).jobs_db_path.unlink()
    assert cli.invoke(app, ["daemon", "--once"]).exit_code == 0

    assert _identity_rows(root) == [("stale", "stale")]


# --- the optional native wake (#1213) ----------------------------------------


class _WakeNotifier:
    def __init__(self) -> None:
        self.started: Path | None = None
        self.closed = False
        self.consumed = 0
        self.pending = True

    def start(self, inbox: Path) -> None:
        self.started = inbox

    def consume(self) -> bool:
        self.consumed += 1
        pending, self.pending = self.pending, False
        return pending

    def close(self) -> None:
        self.closed = True


class _CountingStop(StopToken):
    """Records each idle wait's kwargs, runs its `wake`, and stops the loop
    after `limit` waits."""

    def __init__(self, limit: int) -> None:
        super().__init__()
        self.limit = limit
        self.waits: list[dict[str, object]] = []

    def wait(self, timeout: float, **kwargs: object) -> bool:
        self.waits.append({"timeout": timeout, **kwargs})
        wake = kwargs.get("wake")
        if callable(wake):
            wake()
        if len(self.waits) >= self.limit:
            self.set()
        return self.is_set()


def _native_workspace(root: Path) -> Path:
    inbox = _configure_inbox(root)
    cfg_path = root / "openkos.yaml"
    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8") + "  watch_backend: native\n",
        encoding="utf-8",
    )
    return inbox


def _serve(root: Path, stop: StopToken | None, *, once: bool = False) -> int:
    return daemon_module.serve(
        root,
        once=once,
        ports=_fake_ports(_Git()),
        stop=stop,
        install_signals=False,
    )


def test_native_backend_wakes_the_idle_wait_and_is_closed(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inbox = _native_workspace(root)
    notifier = _WakeNotifier()
    monkeypatch.setattr(
        "openkos.application.watch_notify._default_factory", lambda: notifier
    )
    stop = _CountingStop(limit=1)

    assert _serve(root, stop) == 0

    assert notifier.started == inbox.resolve()
    assert callable(stop.waits[0]["wake"])
    assert notifier.consumed >= 1
    assert notifier.closed


def test_native_without_the_extra_warns_and_polls(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _native_workspace(root)

    def missing() -> object:
        raise ImportError("watchdog")

    monkeypatch.setattr("openkos.application.watch_notify._default_factory", missing)
    stop = _CountingStop(limit=1)

    assert _serve(root, stop) == 0

    assert "openkos[watch]" in capsys.readouterr().err
    assert "wake" not in stop.waits[0]


def test_poll_backend_never_passes_a_wake(root: Path) -> None:
    _configure_inbox(root)
    stop = _CountingStop(limit=1)

    _serve(root, stop)

    assert "wake" not in stop.waits[0]


def test_once_never_starts_a_notifier(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _native_workspace(root)
    monkeypatch.setattr(
        "openkos.application.watch_notify._default_factory",
        lambda: pytest.fail("a --once run built a notifier"),
    )

    assert _serve(root, None, once=True) == 0


def test_a_native_wake_is_coalesced_before_the_next_pass(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _native_workspace(root)
    notifier = _WakeNotifier()
    monkeypatch.setattr(
        "openkos.application.watch_notify._default_factory", lambda: notifier
    )
    stop = _CountingStop(limit=2)

    assert _serve(root, stop) == 0

    assert stop.waits[1]["timeout"] == daemon_module.WAKE_COALESCE_SECONDS
    assert "wake" not in stop.waits[1]


# --- the candidate cap is disclosed, not swallowed (#1265) -------------------

_CAP_NOTICE = "note: 50 of 110 candidate group(s) shown (cap reached)"


def _stage_ctx(root: Path, notify: list[str]) -> runner.StageContext:
    from openkos.application import budget as budget_module
    from openkos.application.lock_wait import locked_commit_section
    from openkos.application.runtime import UnattendedPolicy

    layout = config.WorkspaceLayout(root)
    return runner.StageContext(
        root=root,
        layout=layout,
        budget=budget_module.start_budgeted_run(
            layout, config.read_config(root).unattended, _NOW
        ),
        policy=UnattendedPolicy(),
        commit_section=locked_commit_section(root, wait_seconds=0),
        queue=lambda: None,  # type: ignore[arg-type, return-value]
        notify=notify.append,
    )


def test_the_identity_stage_discloses_a_capped_candidate_list(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openkos.application import duplicates_service, queue_producers

    monkeypatch.setattr(
        duplicates_service,
        "report_duplicates",
        lambda *a, **k: duplicates_service.DuplicatesReport((), 0, _CAP_NOTICE),
    )
    monkeypatch.setattr(queue_producers, "enqueue_identity", lambda *a, **k: None)
    seen: list[str] = []

    daemon_module._identity_stage(_stage_ctx(root, seen))

    assert seen == [f"identity: {_CAP_NOTICE}"]


def test_the_identity_stage_is_silent_when_the_cap_did_not_bind(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openkos.application import duplicates_service, queue_producers

    monkeypatch.setattr(
        duplicates_service,
        "report_duplicates",
        lambda *a, **k: duplicates_service.DuplicatesReport((), 0, None),
    )
    monkeypatch.setattr(queue_producers, "enqueue_identity", lambda *a, **k: None)
    seen: list[str] = []

    daemon_module._identity_stage(_stage_ctx(root, seen))

    assert seen == []


def test_the_relations_and_revisions_observers_disclose_their_caps() -> None:
    seen: list[str] = []
    relations = daemon_module._DaemonRelationsObserver(seen.append)
    revisions_observer = daemon_module._DaemonRevisionsObserver(seen.append)

    relations.candidate_notices("note: 50 of 80 candidate edge(s) shown", None)
    relations.candidate_notices(None, None)
    revisions_observer.truncation_notice("note: 50 of 70 pair(s) shown")

    assert seen == [
        "suggest-relations: note: 50 of 80 candidate edge(s) shown",
        "revisions: note: 50 of 70 pair(s) shown",
    ]


def test_the_contradictions_stage_discloses_a_capped_pair_list(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import types

    from openkos.application import contradictions_service, queue_producers

    outcome = types.SimpleNamespace(
        truncation_notice="note: 50 of 90 pair(s) shown",
        batch=types.SimpleNamespace(failure=None),
        deferred_by_bound=0,
    )
    monkeypatch.setattr(
        contradictions_service, "run_contradictions", lambda *a, **k: outcome
    )
    monkeypatch.setattr(queue_producers, "enqueue_contradictions", lambda *a, **k: None)
    seen: list[str] = []

    daemon_module._contradictions_stage(_stage_ctx(root, seen))

    assert seen == ["contradictions: note: 50 of 90 pair(s) shown"]


def test_the_runner_hands_a_stage_the_announce_port(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lines: list[str] = []

    def stage(ctx: runner.StageContext) -> runner.StageResult:
        ctx.notify("a cap bound")
        return runner.StageResult()

    ports = dataclasses.replace(
        _fake_ports(_Git(), [runner.AdvisorStage("x", stage, uses_model=False)]),
        announce=lines.append,
    )
    runner.run_due_jobs(
        root,
        unattended=config.read_config(root).unattended,
        stop=StopToken(),
        ports=ports,
        maintenance_due=True,
    )

    assert "a cap bound" in lines


def test_the_relations_and_revisions_stages_wire_their_observers_to_notify(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openkos.application import revisions
    from openkos.application import suggest_relations_service as relations_service

    def fake_relations(
        root_arg: Path, request: object, ports: object, observer: Any
    ) -> None:
        observer.candidate_notices("relations cap", None)
        raise RuntimeError("stop here")

    def fake_revisions(
        root_arg: Path, request: object, ports: object, observer: Any
    ) -> None:
        observer.truncation_notice("revisions cap")
        raise RuntimeError("stop here")

    monkeypatch.setattr(relations_service, "suggest_relations", fake_relations)
    monkeypatch.setattr(revisions, "run_revisions", fake_revisions)
    seen: list[str] = []

    for stage in (daemon_module._relations_stage, daemon_module._revisions_stage):
        with pytest.raises(RuntimeError, match="stop here"):
            stage(_stage_ctx(root, seen))

    assert seen == ["suggest-relations: relations cap", "revisions: revisions cap"]


# --- the daemon never auto-merges (#1298, ADR-0049) -----------------------------


def _git_log(root: Path) -> list[str]:
    import subprocess

    return subprocess.run(
        ["git", "log", "--format=%H %s"],  # noqa: S607
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()


def test_the_maintenance_identity_stage_never_merges_an_in_class_pair(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An in-class base/-2 pair a judge would call `same` at 1.0 is exactly what
    `curate --auto-merge` merges. The daemon is the unattended engine (ADR-0037):
    it may only enqueue. Nothing is deleted, no merge commit is made, and the
    only rows it leaves are identity rows. Every judging entry point is patched
    to answer `same` at 1.0 and to fail the test if the daemon reaches it for a
    write, so a stage that started acting on a verdict could not pass quietly."""
    from openkos.application import auto_merge

    write_doc(root, "concepts/foo", {"type": "Concept", "title": "Foo"})
    write_doc(root, "concepts/foo-2", {"type": "Concept", "title": "Foo"})
    group = cand.CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/foo", "concepts/foo-2"),
        tier=cand.Tier.HIGH,
        trigger="key",
        member_types=("Concept", "Concept"),
    )
    assert auto_merge.in_structural_class(group)
    monkeypatch.setattr(
        "openkos.resolution.find_candidates_report",
        lambda bundle_dir, **kw: cand.CandidateGroupReport(
            groups=(group,), produced=1, retained=1
        ),
    )

    def _same_at_one(candidates: Sequence[cand.CandidateGroup], **kw: object) -> Any:
        return adjudication.AdjudicationBatch(
            results=[
                adjudication.AdjudicatedCandidate(
                    candidate=g,
                    verdict=adjudication.Verdict.SAME,
                    confidence=1.0,
                    rationale="stub",
                )
                for g in candidates
            ]
        )

    monkeypatch.setattr(adjudication, "adjudicate_candidates", _same_at_one)
    wrote: list[str] = []
    for name in ("plan_auto_merges", "apply_auto_merges"):
        monkeypatch.setattr(
            auto_merge,
            name,
            lambda *a, _n=name, **k: wrote.append(_n),
        )
    before = _bundle_bytes(root)
    log_before = _git_log(root)

    result = cli.invoke(app, ["daemon", "--once"])

    assert result.exit_code == 0, result.output
    assert wrote == []
    assert _bundle_bytes(root) == before
    assert (root / "bundle" / "concepts" / "foo-2.md").exists()
    assert _git_log(root) == log_before
    conn = sqlite3.connect(config.WorkspaceLayout(root).findings_db_path)
    try:
        items = pq.open_items(conn)
        assert {i.kind for i in items} == {"identity"}
        assert len(items) == 1  # the in-class pair, enqueued and nothing else
    finally:
        conn.close()


def _imported_modules(path: Path) -> set[str]:
    import ast

    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = "." * node.level + (node.module or "")
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names)
    return names


@pytest.mark.parametrize(
    "module", ["openkos/cli/daemon.py", "openkos/application/runner.py"]
)
def test_the_unattended_engine_does_not_import_the_auto_merge_pass(
    module: str,
) -> None:
    source = Path(__file__).resolve().parents[3] / "src" / module
    imported = _imported_modules(source)

    assert imported  # the parse saw this module's imports
    assert not {name for name in imported if name.endswith("auto_merge")}, (
        f"{module} imports the auto-merge pass"
    )


# -- #1331: the daemon import renders what an attended ingest tells the operator --


def _staged_with_report(**report_fields: Any) -> Any:
    from openkos.application import ingest as application_ingest
    from openkos.extraction import concept as concept_mod

    return application_ingest.StagedDerivedObjects(
        plans=(),
        skip_reason=None,
        notices=(),
        report=concept_mod.ExtractionReport(**report_fields),
        drops=(),
        lost_in_staging=0,
    )


def test_watch_ports_name_the_cap_truncation_and_what_was_discarded() -> None:
    ports = daemon_module.watch_ports()
    staged = _staged_with_report(
        produced=22, retained=20, discarded_titles=("Alpha", "Beta")
    )

    lines = ports.staged_notices(staged)

    assert lines == [
        "20 of 22 extracted object(s) kept (cap reached); discarded: Alpha, Beta"
    ]


def test_watch_ports_carry_the_unevidenced_advisory_with_its_titles() -> None:
    ports = daemon_module.watch_ports()
    staged = _staged_with_report(
        produced=1, retained=1, unevidenced_titles=("Agentic Systems",)
    )

    (line,) = ports.staged_notices(staged)

    assert "carry no line quoted from the source" in line
    assert "Agentic Systems" in line


def test_watch_ports_have_nothing_to_say_for_a_clean_extraction() -> None:
    ports = daemon_module.watch_ports()

    assert ports.staged_notices(_staged_with_report(produced=3, retained=3)) == []


def test_watch_ports_summarise_torn_classifications_per_import() -> None:
    from openkos.application import ingest_service as svc

    ports = daemon_module.watch_ports()
    outcome = svc.IngestWritten(
        regenerated=False,
        extraction_degraded=False,
        derived_count=3,
        alternative_pairs=(("Concept", "Entity"), ("Concept", "Entity")),
    )

    assert ports.outcome_notices(outcome) == [
        "2 of 3 derived object(s) recorded a type_alternative on the document "
        "(all: Concept/Entity)."
    ]


def test_the_extraction_phase_hook_is_silent_off_a_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.stderr", io.StringIO())

    assert daemon_module.watch_ports().phase_hook("a.md") is None


def test_the_extraction_phase_hook_names_the_file_on_a_tty(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stderr.isatty", lambda: True, raising=False)
    hook = daemon_module.watch_ports().phase_hook("a.md")
    assert hook is not None

    hook("extracting window 2/5")

    assert (
        "openkos daemon: extracting window 2/5 ('a.md')..." in capsys.readouterr().err
    )


# -- #1332: an unchanged bundle costs the volatility stage nothing ------------------


class _CountingModel:
    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages: Sequence[Any]) -> str:
        self.calls += 1
        return '{"tier": "slow", "rationale": "changes occasionally"}'


@contextlib.contextmanager
def _volatility_ctx(root: Path) -> Iterator[runner.StageContext]:
    from openkos.application import budget as budget_module
    from openkos.application.lock_wait import locked_commit_section
    from openkos.application.runtime import UnattendedPolicy
    from openkos.state import derived

    layout = config.WorkspaceLayout(root)
    conn = derived.open_derived_connection(layout.findings_db_path)
    pq.ensure_schema(conn)
    try:
        yield runner.StageContext(
            root=root,
            layout=layout,
            budget=budget_module.start_budgeted_run(
                layout, config.read_config(root).unattended, _NOW
            ),
            policy=UnattendedPolicy(),
            commit_section=locked_commit_section(root, wait_seconds=0),
            queue=lambda: conn,
        )
    finally:
        conn.close()


def test_the_volatility_stage_does_not_re_ask_an_unchanged_bundle(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_doc(
        root, "concepts/a", {"type": "Concept", "title": "A", "sensitivity": "private"}
    )
    write_doc(
        root, "events/b", {"type": "Event", "title": "B", "sensitivity": "private"}
    )
    model = _CountingModel()
    monkeypatch.setattr("openkos.cli.main._chat_client", lambda cfg, task=None: model)
    monkeypatch.setattr(
        "openkos.cli.main._resolve_local_exemption", lambda client, cfg: True
    )
    stage = next(
        s for s in daemon_module.production_stages() if s.name == "suggest-volatility"
    )

    with _volatility_ctx(root) as ctx:
        stage.run(ctx)
    first_pass_calls = model.calls
    with _volatility_ctx(root) as ctx:
        stage.run(ctx)

    assert first_pass_calls == 2
    assert model.calls == first_pass_calls


def test_the_volatility_stage_still_queues_what_it_served(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A served answer must stay in the published set: a complete run retires
    every row it does not republish."""
    write_doc(
        root,
        "projects/p",
        {"type": "Project", "title": "P", "sensitivity": "private"},
    )
    model = _CountingModel()  # "slow" differs from Project's default
    monkeypatch.setattr("openkos.cli.main._chat_client", lambda cfg, task=None: model)
    monkeypatch.setattr(
        "openkos.cli.main._resolve_local_exemption", lambda client, cfg: True
    )
    stage = next(
        s for s in daemon_module.production_stages() if s.name == "suggest-volatility"
    )

    for _ in range(2):
        with _volatility_ctx(root) as ctx:
            stage.run(ctx)

    conn = sqlite3.connect(config.WorkspaceLayout(root).findings_db_path)
    try:
        kinds = [i.kind for i in pq.open_items(conn)]
    finally:
        conn.close()
    assert model.calls == 1
    assert kinds == ["volatility"]


# -- #1332: a tier the user already applied is never re-proposed ------------------


def _apply_event_slow(root: Path) -> None:
    cfg_path = root / "openkos.yaml"
    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8") + "\ntype_tiers:\n  Event: slow\n",
        encoding="utf-8",
    )


def test_the_volatility_stage_does_not_propose_the_tier_already_applied(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Event's registry default is `static`; the workspace maps it to `slow`
    and the model answers `slow`: nothing differs from the EFFECTIVE tier, so
    no pending row exists."""
    _apply_event_slow(root)
    write_doc(
        root, "events/b", {"type": "Event", "title": "B", "sensitivity": "private"}
    )
    model = _CountingModel()  # always answers "slow"
    monkeypatch.setattr("openkos.cli.main._chat_client", lambda cfg, task=None: model)
    monkeypatch.setattr(
        "openkos.cli.main._resolve_local_exemption", lambda client, cfg: True
    )
    stage = next(
        s for s in daemon_module.production_stages() if s.name == "suggest-volatility"
    )

    with _volatility_ctx(root) as ctx:
        stage.run(ctx)

    conn = sqlite3.connect(config.WorkspaceLayout(root).findings_db_path)
    try:
        kinds = [i.kind for i in pq.open_items(conn)]
    finally:
        conn.close()
    assert model.calls == 1
    assert kinds == []


def test_changing_type_tiers_re_asks_the_type(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The effective tier is part of the prompt, so the cached answer to the
    old question is not served for the new one."""
    write_doc(
        root, "events/b", {"type": "Event", "title": "B", "sensitivity": "private"}
    )
    model = _CountingModel()
    monkeypatch.setattr("openkos.cli.main._chat_client", lambda cfg, task=None: model)
    monkeypatch.setattr(
        "openkos.cli.main._resolve_local_exemption", lambda client, cfg: True
    )
    stage = next(
        s for s in daemon_module.production_stages() if s.name == "suggest-volatility"
    )
    with _volatility_ctx(root) as ctx:
        stage.run(ctx)
    _apply_event_slow(root)

    with _volatility_ctx(root) as ctx:
        stage.run(ctx)

    assert model.calls == 2


# -- #1334: the daemon removes the logs of workspaces that are gone -----------------


def test_daemon_start_removes_a_stale_log_group_and_says_so(
    root: Path, tmp_path: Path
) -> None:
    import os

    from openkos.lock import workspace_digest

    gone = tmp_path / "long-gone-workspace"
    digest = workspace_digest(gone)
    log_dir = tmp_path / "state-logs"
    log_dir.mkdir(exist_ok=True)
    stale = log_dir / f"{digest}.log"
    stale.write_text("old\n", encoding="utf-8")
    record = log_dir / f"{digest}.workspace"
    record.write_text(os.path.realpath(gone), encoding="utf-8")
    legacy = log_dir / ("c" * 64 + ".log")
    legacy.write_text("unattributable\n", encoding="utf-8")

    result = cli.invoke(app, ["daemon", "--once"])

    assert result.exit_code == 0, result.output
    assert not stale.exists()
    assert not record.exists()
    assert legacy.exists()
    assert f"openkos daemon: removed log '{digest}.log'" in result.stderr
    assert str(gone) in result.stderr


def test_daemon_start_records_its_workspace_beside_its_log(
    root: Path, tmp_path: Path
) -> None:
    import os

    from openkos.lock import workspace_digest

    assert cli.invoke(app, ["daemon", "--once"]).exit_code == 0

    record = tmp_path / "state-logs" / f"{workspace_digest(root)}.workspace"
    assert record.read_text(encoding="utf-8") == os.path.realpath(root)


def test_daemon_start_leaves_a_live_workspaces_log_alone(
    root: Path, tmp_path: Path
) -> None:
    import os

    from openkos.lock import workspace_digest

    other = tmp_path / "other-workspace"
    (other / "bundle").mkdir(parents=True)
    (other / "bundle" / "index.md").write_text("x", encoding="utf-8")
    (other / "bundle" / "log.md").write_text("x", encoding="utf-8")
    digest = workspace_digest(other)
    log_dir = tmp_path / "state-logs"
    log_dir.mkdir(exist_ok=True)
    (log_dir / f"{digest}.log").write_text("live\n", encoding="utf-8")
    (log_dir / f"{digest}.workspace").write_text(
        os.path.realpath(other), encoding="utf-8"
    )

    result = cli.invoke(app, ["daemon", "--once"])

    assert result.exit_code == 0, result.output
    assert (log_dir / f"{digest}.log").exists()
    assert "removed log" not in result.stderr
