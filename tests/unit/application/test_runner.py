"""Unit tests for `application/runner.py` (MVP 4 unit 6.1, issues #1139-#1141).

The runner is the synchronous job core the daemon verb (6.2) drives. It
composes the incremental refresh, the lint counts and the advisor stages under
the budget, records exactly one outcome per job in `jobs.db`, and never performs
a consequential write: a maintenance pass changes nothing under `bundle/`.
"""

import ast
import contextlib
import dataclasses
import sqlite3
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from openkos import config, lock
from openkos.application import budget, queue_producers, runner
from openkos.application.consent import BooleanConfirmation, TypedChallengeConfirmation
from openkos.application.reindex_service import (
    BackendNotReachable,
    LockContention,
)
from openkos.application.runtime import SpendQuestion, StopToken
from openkos.state import jobs
from openkos.state import pending_queue as pq
from tests.unit.application.curation_support import make_workspace, tree, write_concept

_NOW = datetime(2026, 9, 30, 15, 0, tzinfo=UTC)
_SRC = Path(__file__).resolve().parents[3] / "src" / "openkos"


class _Clock:
    """A fake monotonic clock whose sleep advances it."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class _Git:
    """Records every commit the runner attempts; dirty paths are scriptable."""

    def __init__(self) -> None:
        self.dirty: set[str] = set()
        self.commits: list[list[str]] = []
        self.fail: Exception | None = None

    def paths_dirty(self, root: Path, paths: Sequence[str]) -> bool:
        return any(p in self.dirty for p in paths)

    def commit_paths(
        self, root: Path, paths: Sequence[str], message: str
    ) -> str | None:
        if self.fail is not None:
            raise self.fail
        self.commits.append(list(paths))
        self.dirty -= set(paths)
        return "abc1234"


def _ports(
    clock: _Clock,
    git: _Git,
    *,
    refresh: object = None,
    stages: Sequence[runner.AdvisorStage] = (),
) -> runner.RunnerPorts:
    return runner.RunnerPorts(
        refresh_derived=refresh or (lambda root: None),  # type: ignore[arg-type]
        commit_paths=git.commit_paths,
        paths_dirty=git.paths_dirty,
        repo_root=lambda root: root,
        has_git_identity=lambda root: True,
        advisor_stages=tuple(stages),
        lint_counts=lambda layout: {"orphans": 0},
        now=lambda: _NOW,
        monotonic=clock,
        sleep=clock.sleep,
        jitter=lambda low, high: high,
    )


def _unattended(**kw: Any) -> config.UnattendedConfig:
    return config.UnattendedConfig(**kw)


def _jobs(root: Path) -> tuple[jobs.JobRecord, ...]:
    conn = jobs.open_jobs(config.WorkspaceLayout(root).jobs_db_path)
    try:
        return tuple(reversed(jobs.recent_jobs(conn)))
    finally:
        conn.close()


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    ws = make_workspace(tmp_path, monkeypatch)
    write_concept(ws, "concepts/alpha", title="Alpha")
    return ws


def _enqueue_stage(name: str = "identity") -> runner.AdvisorStage:
    def run(ctx: runner.StageContext) -> runner.StageResult:
        proposal = pq.Proposal(
            kind="identity",
            key_body=pq.identity_key(["concepts/alpha", "concepts/beta"]),
            producer="duplicates/1",
            payload="{}",
            targets=("concepts/alpha", "concepts/beta"),
        )
        queue_producers.publish(
            ctx.queue(),
            "identity",
            [proposal],
            complete=True,
            commit_section=ctx.commit_section,
            bundle_dir=ctx.layout.bundle_dir,
        )
        return runner.StageResult()

    return runner.AdvisorStage(name=name, run=run, uses_model=False)


# --- the maintenance pass computes and enqueues only --------------------------


def test_maintenance_pass_changes_nothing_under_bundle_and_enqueues(
    root: Path,
) -> None:
    before = tree(root)
    result = runner.run_maintenance_job(
        root,
        unattended=_unattended(),
        stop=StopToken(),
        ports=_ports(_Clock(), _Git(), stages=[_enqueue_stage()]),
    )
    assert result.outcome == "completed"
    assert tree(root) == before
    layout = config.WorkspaceLayout(root)
    conn = sqlite3.connect(layout.findings_db_path)
    try:
        assert [i.decision_key for i in pq.open_items(conn)] == [
            "identity:" + pq.identity_key(["concepts/alpha", "concepts/beta"])
        ]
    finally:
        conn.close()
    (record,) = _jobs(root)
    assert (record.kind, record.outcome) == ("maintenance", "completed")
    assert record.units_done == 3  # refresh, lint, one advisor stage
    assert record.units_deferred == 0
    assert result.lint_counts == {"orphans": 0}


def test_stages_run_in_order_after_refresh_and_lint(root: Path) -> None:
    order: list[str] = []

    def stage(name: str) -> runner.AdvisorStage:
        def run(ctx: runner.StageContext) -> runner.StageResult:
            order.append(name)
            return runner.StageResult()

        return runner.AdvisorStage(name=name, run=run, uses_model=False)

    ports = _ports(
        _Clock(),
        _Git(),
        refresh=lambda r: order.append("refresh"),
        stages=[stage("a"), stage("b")],
    )

    def lint(layout: config.WorkspaceLayout) -> dict[str, int]:
        order.append("lint")
        return {}

    ports = dataclasses.replace(ports, lint_counts=lint)
    runner.run_maintenance_job(
        root, unattended=_unattended(), stop=StopToken(), ports=ports
    )
    assert order == ["refresh", "lint", "a", "b"]


# --- policy: never a consent flag, never an approval ---------------------------


def test_stage_policy_declines_every_write_confirmation_and_follows_budget(
    root: Path,
) -> None:
    seen: list[object] = []

    def run(ctx: runner.StageContext) -> runner.StageResult:
        seen.append(
            ctx.policy.answer(
                BooleanConfirmation(
                    prompt="Proceed?", bypass_flag="--yes", non_tty_refusal=None
                )
            )
        )
        seen.append(
            ctx.policy.answer(
                TypedChallengeConfirmation(
                    prompt="p",
                    expected="x",
                    supplying_flag="--f",
                    non_tty_refusal="n",
                    mismatch_abort="m",
                )
            )
        )
        seen.append(ctx.policy.answer(SpendQuestion(estimated_calls=3)))
        seen.append(ctx.policy.answer(SpendQuestion(estimated_calls=4)))
        return runner.StageResult()

    runner.run_maintenance_job(
        root,
        unattended=_unattended(max_calls_per_pass=3),
        stop=StopToken(),
        ports=_ports(
            _Clock(),
            _Git(),
            stages=[runner.AdvisorStage(name="s", run=run, uses_model=True)],
        ),
    )
    assert seen == ["declined", "declined", "approved", "declined"]


def test_runner_source_carries_no_consent_flag_or_bypass() -> None:
    tree_ = ast.parse((_SRC / "application" / "runner.py").read_text("utf-8"))
    names = {n.id for n in ast.walk(tree_) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree_) if isinstance(n, ast.Attribute)}
    keywords = {
        k.arg
        for n in ast.walk(tree_)
        if isinstance(n, ast.Call)
        for k in n.keywords
        if k.arg
    }
    forbidden = {"skip_confirmation", "include_confidential", "yes", "auto", "force"}
    assert not (names | keywords) & forbidden


# --- stop flag -----------------------------------------------------------------


def test_stop_set_before_a_burst_writes_nothing(root: Path) -> None:
    stop = StopToken()
    wrote: list[str] = []

    def run(ctx: runner.StageContext) -> runner.StageResult:
        stop.set()  # the signal lands after compute, before the commit phase
        with ctx.commit_section():
            wrote.append("burst")
        return runner.StageResult()

    before = tree(root)
    result = runner.run_maintenance_job(
        root,
        unattended=_unattended(),
        stop=stop,
        ports=_ports(
            _Clock(),
            _Git(),
            stages=[runner.AdvisorStage(name="s", run=run, uses_model=False)],
        ),
    )
    assert wrote == []
    assert result.outcome == "stopped"
    assert tree(root) == before
    assert _jobs(root)[0].outcome == "stopped"


def test_stop_between_units_defers_the_rest(root: Path) -> None:
    stop = StopToken()
    ran: list[str] = []

    def first(ctx: runner.StageContext) -> runner.StageResult:
        ran.append("first")
        stop.set()
        return runner.StageResult()

    def second(ctx: runner.StageContext) -> runner.StageResult:
        ran.append("second")
        return runner.StageResult()

    result = runner.run_maintenance_job(
        root,
        unattended=_unattended(),
        stop=stop,
        ports=_ports(
            _Clock(),
            _Git(),
            stages=[
                runner.AdvisorStage(name="first", run=first, uses_model=False),
                runner.AdvisorStage(name="second", run=second, uses_model=False),
            ],
        ),
    )
    assert ran == ["first"]
    assert (result.outcome, result.units_deferred) == ("stopped", 1)


# --- deadline ------------------------------------------------------------------


def test_deadline_defers_the_rest_and_records_timed_out(root: Path) -> None:
    clock = _Clock()
    ran: list[str] = []

    def slow(ctx: runner.StageContext) -> runner.StageResult:
        ran.append("slow")
        clock.now += 61  # the unit overruns the 60s deadline and is let finish
        return runner.StageResult()

    def never(ctx: runner.StageContext) -> runner.StageResult:
        ran.append("never")
        return runner.StageResult()

    result = runner.run_maintenance_job(
        root,
        unattended=_unattended(job_deadline_seconds=60),
        stop=StopToken(),
        ports=_ports(
            clock,
            _Git(),
            stages=[
                runner.AdvisorStage(name="slow", run=slow, uses_model=False),
                runner.AdvisorStage(name="never", run=never, uses_model=False),
            ],
        ),
    )
    assert ran == ["slow"]
    (record,) = _jobs(root)
    assert (record.outcome, record.units_deferred) == ("timed_out", 1)
    assert result.outcome == "timed_out"


# --- budget --------------------------------------------------------------------


def test_exhausted_budget_defers_model_stages_but_runs_free_ones(root: Path) -> None:
    ran: list[str] = []

    def stage(name: str, *, model: bool) -> runner.AdvisorStage:
        def run(ctx: runner.StageContext) -> runner.StageResult:
            ran.append(name)
            return runner.StageResult()

        return runner.AdvisorStage(name=name, run=run, uses_model=model)

    result = runner.run_maintenance_job(
        root,
        unattended=_unattended(max_calls_per_pass=0),
        stop=StopToken(),
        ports=_ports(
            _Clock(),
            _Git(),
            stages=[stage("model", model=True), stage("free", model=False)],
        ),
    )
    assert ran == ["free"]
    assert result.outcome == "budget_exhausted"
    assert result.detail_code == budget.PASS_LIMIT_KEY
    assert result.units_deferred == 1


def test_stage_bound_deferral_is_budget_exhausted_and_calls_are_recorded(
    root: Path,
) -> None:
    def run(ctx: runner.StageContext) -> runner.StageResult:
        client = ctx.budget.counting(_Inner())
        client.chat([])
        client.chat([])
        return runner.StageResult(deferred_by_bound=4)

    result = runner.run_maintenance_job(
        root,
        unattended=_unattended(),
        stop=StopToken(),
        ports=_ports(
            _Clock(),
            _Git(),
            stages=[runner.AdvisorStage(name="s", run=run, uses_model=True)],
        ),
    )
    (record,) = _jobs(root)
    assert (record.outcome, record.chat_calls, record.units_deferred) == (
        "budget_exhausted",
        2,
        4,
    )
    assert result.chat_calls == 2


class _Inner:
    locality = None

    def chat(self, messages: object) -> str:
        return "r"


# --- contention ----------------------------------------------------------------


def test_refresh_contention_backs_off_within_the_deadline_then_succeeds(
    root: Path,
) -> None:
    clock = _Clock()
    attempts: list[int] = []

    def refresh(r: Path) -> None:
        attempts.append(1)
        if len(attempts) < 3:
            raise LockContention("busy")

    result = runner.run_maintenance_job(
        root,
        unattended=_unattended(),
        stop=StopToken(),
        ports=_ports(clock, _Git(), refresh=refresh),
    )
    assert result.outcome == "completed"
    assert len(attempts) == 3
    assert len(clock.sleeps) == 2
    assert all(s > 0 for s in clock.sleeps)


def test_refresh_contention_past_the_deadline_is_busy(root: Path) -> None:
    clock = _Clock()

    def refresh(r: Path) -> None:
        raise LockContention("busy")

    result = runner.run_maintenance_job(
        root,
        unattended=_unattended(job_deadline_seconds=60),
        stop=StopToken(),
        ports=_ports(clock, _Git(), refresh=refresh, stages=[_enqueue_stage()]),
    )
    assert result.outcome == "busy"
    assert result.units_done == 0
    assert result.units_deferred == 3  # refresh, lint, the stage
    assert sum(clock.sleeps) <= 60 + 1


def test_commit_section_waits_for_a_held_lock_then_enters(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _Clock()
    calls: list[int] = []

    @contextlib.contextmanager
    def fake(r: Path) -> Iterator[Path]:
        calls.append(1)
        if len(calls) <= 2:
            raise lock.WorkspaceBusyError(lock.BUSY_REASON)
        yield r

    monkeypatch.setattr(lock, "workspace_lock", fake)
    result = runner.run_maintenance_job(
        root,
        unattended=_unattended(),
        stop=StopToken(),
        ports=_ports(clock, _Git(), stages=[_enqueue_stage()]),
    )
    assert result.outcome == "completed"
    assert len(calls) >= 3


def test_busy_commit_section_defers_the_unit(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Busy:
        def __enter__(self) -> Path:
            raise lock.WorkspaceBusyError(lock.BUSY_REASON)

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(lock, "workspace_lock", lambda r: _Busy())
    result = runner.run_maintenance_job(
        root,
        unattended=_unattended(job_deadline_seconds=60),
        stop=StopToken(),
        ports=_ports(_Clock(), _Git(), stages=[_enqueue_stage()]),
    )
    # refresh and lint completed, so the job is not `busy`: only the unit defers.
    assert result.outcome == "completed"
    assert (result.units_done, result.units_deferred) == (2, 1)


# --- refusals, failures, unreadable record ---------------------------------------


def test_a_refused_refresh_ends_the_job_refused_and_defers_the_rest(
    root: Path,
) -> None:
    def refresh(r: Path) -> None:
        raise BackendNotReachable("SENTINEL-document-text")

    result = runner.run_maintenance_job(
        root,
        unattended=_unattended(),
        stop=StopToken(),
        ports=_ports(_Clock(), _Git(), refresh=refresh, stages=[_enqueue_stage()]),
    )
    assert result.outcome == "refused"
    assert result.detail_code == "backend_not_reachable"
    assert result.units_deferred == 3
    (record,) = _jobs(root)
    assert "SENTINEL" not in repr(record)


def test_a_stage_exception_is_failed_with_a_class_code_only(root: Path) -> None:
    def run(ctx: runner.StageContext) -> runner.StageResult:
        raise RuntimeError("SENTINEL-model-output")

    result = runner.run_maintenance_job(
        root,
        unattended=_unattended(),
        stop=StopToken(),
        ports=_ports(
            _Clock(),
            _Git(),
            stages=[runner.AdvisorStage(name="s", run=run, uses_model=False)],
        ),
    )
    assert (result.outcome, result.detail_code) == ("failed", "runtime_error")
    (record,) = _jobs(root)
    assert "SENTINEL" not in repr(record)


def test_an_unreadable_job_record_makes_no_model_call(root: Path) -> None:
    layout = config.WorkspaceLayout(root)
    layout.openkos_dir.mkdir(exist_ok=True)
    layout.jobs_db_path.write_bytes(b"not a database" * 100)
    called: list[str] = []

    def run(ctx: runner.StageContext) -> runner.StageResult:
        called.append("ran")
        return runner.StageResult()

    result = runner.run_maintenance_job(
        root,
        unattended=_unattended(),
        stop=StopToken(),
        ports=_ports(
            _Clock(),
            _Git(),
            stages=[runner.AdvisorStage(name="s", run=run, uses_model=True)],
        ),
    )
    assert called == []
    assert (result.outcome, result.recorded) == ("refused", False)
    assert result.detail_code == "jobs_store_unreadable"


def test_a_record_that_cannot_be_written_still_ends_the_job(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(jobs, "finish_job", boom)
    result = runner.run_maintenance_job(
        root,
        unattended=_unattended(),
        stop=StopToken(),
        ports=_ports(_Clock(), _Git()),
    )
    assert result.outcome == "completed"
    assert result.recorded is False


# --- commit-failed retry ---------------------------------------------------------


def _seed_commit_failed(root: Path, paths: Sequence[str]) -> None:
    conn = jobs.open_jobs(config.WorkspaceLayout(root).jobs_db_path)
    try:
        job_id = jobs.start_job(conn, "watch", "2026-09-30T14:00:00Z")
        jobs.finish_job(
            conn,
            job_id,
            outcome="commit_failed",
            ended_at="2026-09-30T14:01:00Z",
            chat_calls=0,
            units_done=1,
            units_deferred=0,
        )
        jobs.record_uncommitted_paths(conn, job_id, paths)
    finally:
        conn.close()


def test_commit_failed_is_retried_first_and_clears_on_success(root: Path) -> None:
    _seed_commit_failed(root, ["bundle/a.md", "bundle/b.md"])
    git = _Git()
    git.dirty = {"bundle/a.md"}  # b.md was committed by hand since
    order: list[str] = []
    ports = _ports(_Clock(), git, refresh=lambda r: order.append("refresh"))
    orig = git.commit_paths

    def spying(root_: Path, paths: Sequence[str], message: str) -> str | None:
        order.append("commit")
        return orig(root_, paths, message)

    ports = dataclasses.replace(ports, commit_paths=spying)
    results = runner.run_due_jobs(
        root,
        unattended=_unattended(),
        stop=StopToken(),
        ports=ports,
        maintenance_due=True,
    )
    assert [r.kind for r in results] == ["commit-retry", "maintenance"]
    assert order == ["commit", "refresh"]
    assert git.commits == [["bundle/a.md"]]
    assert results[0].outcome == "completed"
    conn = jobs.open_jobs(config.WorkspaceLayout(root).jobs_db_path)
    try:
        assert jobs.uncommitted_paths(conn) == ()
    finally:
        conn.close()


def test_a_failing_retry_stays_recorded_and_is_commit_failed(root: Path) -> None:
    from openkos.vcs.git import GitError

    _seed_commit_failed(root, ["bundle/a.md"])
    git = _Git()
    git.dirty = {"bundle/a.md"}
    git.fail = GitError("index.lock held")
    (retry,) = runner.run_due_jobs(
        root,
        unattended=_unattended(),
        stop=StopToken(),
        ports=_ports(_Clock(), git),
        maintenance_due=False,
    )
    assert (retry.kind, retry.outcome) == ("commit-retry", "commit_failed")
    conn = jobs.open_jobs(config.WorkspaceLayout(root).jobs_db_path)
    try:
        assert jobs.uncommitted_paths(conn) == ("bundle/a.md",)
    finally:
        conn.close()


def test_no_recorded_paths_means_no_retry_job(root: Path) -> None:
    results = runner.run_due_jobs(
        root,
        unattended=_unattended(),
        stop=StopToken(),
        ports=_ports(_Clock(), _Git()),
        maintenance_due=False,
    )
    assert results == ()


def test_a_non_repository_retry_names_the_unfixable_cause(root: Path) -> None:
    _seed_commit_failed(root, ["bundle/a.md"])
    git = _Git()
    git.dirty = {"bundle/a.md"}
    ports = dataclasses.replace(_ports(_Clock(), git), repo_root=lambda r: None)
    (retry,) = runner.run_due_jobs(
        root,
        unattended=_unattended(),
        stop=StopToken(),
        ports=ports,
        maintenance_due=False,
    )
    assert (retry.outcome, retry.detail_code) == ("commit_failed", "not_a_repository")
    assert git.commits == []


def test_attempt_commit_records_paths_on_failure(root: Path) -> None:
    git = _Git()
    git.fail = RuntimeError("x")
    conn = jobs.open_jobs(config.WorkspaceLayout(root).jobs_db_path)
    try:
        job_id = jobs.start_job(conn, "watch", "2026-09-30T14:00:00Z")
        res = runner.attempt_commit(
            root, ["bundle/a.md"], "msg", ports=_ports(_Clock(), git)
        )
        assert isinstance(res, runner.CommitFailed)
        runner.record_commit_failure(conn, job_id, res)
        assert jobs.uncommitted_paths(conn) == ("bundle/a.md",)
        ok = runner.attempt_commit(
            root, ["bundle/b.md"], "msg", ports=_ports(_Clock(), _Git())
        )
        assert isinstance(ok, runner.Committed)
        assert ok.sha == "abc1234"
    finally:
        conn.close()


# --- layering ----------------------------------------------------------------------


def test_runner_never_imports_the_cli_a_terminal_or_async() -> None:
    tree_ = ast.parse((_SRC / "application" / "runner.py").read_text("utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree_):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not {n.split(".")[0] for n in imported} & {
        "typer",
        "rich",
        "asyncio",
        "subprocess",
    }
    assert not any(n.startswith("openkos.cli") for n in imported)
    assert not any(isinstance(n, ast.AsyncFunctionDef) for n in ast.walk(tree_))


# --- scheduling ----------------------------------------------------------------------


def test_maintenance_is_due_when_never_run_or_the_interval_elapsed(root: Path) -> None:
    cfg = _unattended(maintenance_interval_seconds=300)
    assert runner.maintenance_due(root, cfg, _NOW) is True
    conn = jobs.open_jobs(config.WorkspaceLayout(root).jobs_db_path)
    try:
        jobs.start_job(conn, "maintenance", "2026-09-30T14:58:00Z")
    finally:
        conn.close()
    assert runner.maintenance_due(root, cfg, _NOW) is False  # 120s ago
    later = datetime(2026, 9, 30, 15, 3, 1, tzinfo=UTC)
    assert runner.maintenance_due(root, cfg, later) is True  # 301s ago
