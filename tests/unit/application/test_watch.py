"""Unit tests for `application/watch.py` (MVP 4 unit 7.2, issue #1142, ADR-0038).

The watch job polls the configured inbox, admits a file only after its stat has
stayed unchanged for the quiet window, and imports it through `ingest_source`.
The inbox is external and read-only to the engine. Every test drives the real
`ingest_source` with a counting fake model, so "no model call" is observed.
"""

import dataclasses
import os
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from openkos import config
from openkos.application import budget, runner, watch
from openkos.application import ingest_service as svc
from openkos.application.lock_wait import CommitSection
from openkos.application.runtime import StopToken
from openkos.llm.base import Message
from openkos.state import jobs
from tests.unit.application.curation_support import make_workspace, tree

_T0 = datetime(2026, 9, 30, 15, 0, tzinfo=UTC)
_QUIET = 5


class _Time:
    def __init__(self) -> None:
        self.now = _T0

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class _Model:
    """A declining `LLMBackend` that counts calls and runs an optional hook."""

    def __init__(self) -> None:
        self.calls = 0
        self.hook: Callable[[], None] | None = None

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls += 1
        if self.hook is not None:
            self.hook()
        return '{"extract": false}'


class _Env:
    def __init__(self, root: Path, inbox: Path) -> None:
        self.root = root
        self.inbox = inbox
        self.time = _Time()
        self.model = _Model()
        self.commits: list[list[str]] = []
        self.stop = StopToken()
        self.on_commit: Callable[[], None] | None = None

    def ingest_ports(
        self, section: CommitSection, run_budget: budget.BudgetedRun
    ) -> svc.IngestPorts:
        def autocommit(r: Path, paths: Sequence[str], message: str) -> None:
            self.commits.append(list(paths))
            if self.on_commit is not None:
                self.on_commit()

        return svc.IngestPorts(
            chat_client=run_budget.wrap_chat_client(lambda cfg: self.model),
            autocommit=autocommit,
            after_commit=lambda layout, cfg: None,
            commit_section=section,
        )

    def ports(self) -> runner.RunnerPorts:
        return runner.RunnerPorts(
            refresh_derived=lambda root: None,
            commit_paths=lambda root, paths, message: None,
            paths_dirty=lambda root, paths: False,
            repo_root=lambda root: root,
            has_git_identity=lambda root: True,
            now=lambda: self.time.now,
            monotonic=lambda: 1000.0,
            sleep=lambda seconds: None,
            jitter=lambda low, high: high,
            wait_cap_seconds=0,
            watch=watch.WatchPorts(ingest_ports=self.ingest_ports),
        )

    def cfg(self, **kw: int) -> config.UnattendedConfig:
        return config.UnattendedConfig(inbox=self.inbox, quiet_seconds=_QUIET, **kw)

    def job(self, **kw: int) -> runner.JobResult | None:
        return watch.run_watch_job(
            self.root, unattended=self.cfg(**kw), stop=self.stop, ports=self.ports()
        )

    def settle(self) -> None:
        self.time.advance(_QUIET + 1)

    def drop(self, name: str, text: str = "Notes about self-control.\n") -> Path:
        path = self.inbox / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def raw(self) -> list[str]:
        return sorted(p.name for p in (self.root / "raw").iterdir())

    def observation(self, key: str) -> jobs.WatchObservation:
        conn = jobs.open_jobs(config.WorkspaceLayout(self.root).jobs_db_path)
        try:
            return {o.path: o for o in jobs.observations(conn)}[key]
        finally:
            conn.close()


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Env:
    root = make_workspace(tmp_path, monkeypatch)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    return _Env(root, inbox)


def _inbox_state(inbox: Path) -> dict[str, tuple[bytes | None, int]]:
    state: dict[str, tuple[bytes | None, int]] = {}
    for p in [*sorted(inbox.rglob("*")), inbox]:
        data = p.read_bytes() if p.is_file() else None
        state[str(p.relative_to(inbox))] = (data, p.stat().st_mtime_ns)
    return state


# --- settling ----------------------------------------------------------------


def test_an_unsettled_file_is_skipped(env: _Env) -> None:
    env.drop("a.md")

    assert env.job() is None  # first sight: the clock starts
    env.time.advance(_QUIET - 2)
    assert env.job() is None  # still inside the quiet window

    assert env.model.calls == 0
    assert env.raw() == []


def test_a_changing_file_restarts_the_quiet_window(env: _Env) -> None:
    path = env.drop("a.md")
    env.job()
    env.time.advance(_QUIET - 1)
    path.write_text("edited, longer text\n", encoding="utf-8")
    env.time.advance(2)  # past the ORIGINAL window, inside the new one

    assert env.job() is None
    assert env.raw() == []


def test_a_settled_file_is_imported_through_the_ingest_service(env: _Env) -> None:
    env.drop("a.md")
    env.job()
    env.settle()

    result = env.job()

    assert result is not None
    assert (result.kind, result.outcome) == ("watch", "completed")
    assert (result.units_done, result.units_deferred) == (1, 0)
    assert env.raw() == ["a.md"]
    assert env.model.calls >= 1
    assert result.chat_calls == env.model.calls
    assert env.commits
    assert "raw/a.md" in env.commits[0]
    assert env.observation("a.md").outcome == watch.IMPORTED


def test_dot_entries_symlinks_and_subdirectories(env: _Env) -> None:
    env.drop(".hidden.md")
    env.drop(".dir/inside.md")
    env.drop("sub/nested.md")
    (env.inbox / "link.md").symlink_to(env.inbox / "sub" / "nested.md")
    env.job()
    env.settle()

    result = env.job()

    assert result is not None
    assert result.units_done == 1
    assert env.raw() == ["nested.md"]


# --- a steady inbox costs nothing ---------------------------------------------


def test_a_steady_inbox_makes_no_model_call(env: _Env) -> None:
    env.drop("a.md")
    env.job()
    env.settle()
    assert env.job() is not None
    calls = env.model.calls

    for _ in range(3):
        env.settle()
        assert env.job() is None

    assert env.model.calls == calls


def test_a_touched_file_with_the_same_bytes_starts_no_ingest(env: _Env) -> None:
    path = env.drop("a.md")
    env.job()
    env.settle()
    env.job()
    calls = env.model.calls
    commits = len(env.commits)
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))

    env.job()  # sees the new mtime
    env.settle()
    assert env.job() is None

    assert (env.model.calls, len(env.commits)) == (calls, commits)
    assert env.observation("a.md").outcome == watch.IMPORTED


def test_an_unchanged_stat_is_not_read_again(env: _Env) -> None:
    env.drop("a.md")
    env.job()
    env.settle()
    env.job()
    hashed: list[Path] = []
    real = watch.WatchPorts(ingest_ports=env.ingest_ports)

    def counting_hash(path: Path) -> str:
        hashed.append(path)
        return real.hash_file(path)

    ports = env.ports()
    ports = dataclasses.replace(
        ports,
        watch=watch.WatchPorts(ingest_ports=env.ingest_ports, hash_file=counting_hash),
    )
    env.settle()
    watch.run_watch_job(env.root, unattended=env.cfg(), stop=env.stop, ports=ports)

    assert hashed == []


# --- the inbox is read-only to the engine --------------------------------------


def test_inbox_names_bytes_and_mtimes_are_unchanged_after_a_pass(env: _Env) -> None:
    env.drop("a.md")
    env.drop("sub/b.md", "Other notes.\n")
    before = _inbox_state(env.inbox)
    env.job()
    env.settle()

    result = env.job()

    assert result is not None
    assert result.units_done == 2
    assert env.raw() == ["a.md", "b.md"]
    assert _inbox_state(env.inbox) == before


def test_the_pass_writes_nothing_outside_raw_bundle_and_the_job_record(
    env: _Env,
) -> None:
    env.drop("a.md")
    before = tree(env.inbox.parent / "inbox")
    env.job()
    env.settle()
    env.job()

    assert tree(env.inbox.parent / "inbox") == before


# --- budget, stop, deferral -----------------------------------------------------


def test_deferred_files_are_picked_up_on_the_next_job(env: _Env) -> None:
    env.drop("a.md", "First notes.\n")
    env.drop("b.md", "Second notes.\n")
    env.job(max_sources_per_pass=1)
    env.settle()

    first = env.job(max_sources_per_pass=1)
    assert first is not None
    assert (first.outcome, first.detail_code) == (
        "budget_exhausted",
        "max_sources_per_pass",
    )
    assert (first.units_done, first.units_deferred) == (1, 1)
    assert env.raw() == ["a.md"]

    second = env.job(max_sources_per_pass=1)
    assert second is not None
    assert (second.outcome, second.units_done, second.units_deferred) == (
        "completed",
        1,
        0,
    )
    assert env.raw() == ["a.md", "b.md"]


def test_a_source_that_never_fits_the_pass_budget_is_recorded_not_run(
    env: _Env,
) -> None:
    env.drop("a.md")
    env.job(max_calls_per_pass=1)
    env.settle()

    result = env.job(max_calls_per_pass=1)

    assert result is not None
    assert env.model.calls == 0
    assert env.raw() == []
    assert env.observation("a.md").outcome == watch.EXCEEDS_BUDGET
    env.settle()
    assert env.job(max_calls_per_pass=1) is None  # not retried every poll


def test_a_stop_between_files_defers_the_rest(env: _Env) -> None:
    env.drop("a.md", "First notes.\n")
    env.drop("b.md", "Second notes.\n")
    env.job()
    env.settle()
    env.on_commit = lambda: env.stop.set()

    result = env.job()

    assert result is not None
    assert (result.outcome, result.units_done, result.units_deferred) == (
        "stopped",
        1,
        1,
    )
    assert env.raw() == ["a.md"]


def test_a_stop_during_extraction_writes_nothing_and_defers(env: _Env) -> None:
    env.drop("a.md")
    env.job()
    env.settle()
    env.model.hook = lambda: env.stop.set()

    result = env.job()

    assert result is not None
    assert (result.outcome, result.units_done, result.units_deferred) == (
        "stopped",
        0,
        1,
    )
    assert env.raw() == []


# --- the pre-commit re-hash ------------------------------------------------------


def test_a_file_changed_between_settle_and_commit_is_deferred(env: _Env) -> None:
    path = env.drop("a.md")
    env.job()
    env.settle()
    before = tree(env.root)

    def rewrite() -> None:
        path.write_text("saved again!\n", encoding="utf-8")

    env.model.hook = rewrite

    result = env.job()

    assert result is not None
    assert (result.outcome, result.units_done, result.units_deferred) == (
        "completed",
        0,
        1,
    )
    assert env.raw() == []
    assert env.commits == []
    assert tree(env.root) == before
    # Deferred, not terminal: once the new bytes settle the file is imported.
    env.model.hook = None
    env.job()
    env.settle()
    again = env.job()
    assert again is not None
    assert again.units_done == 1
    assert env.raw() == ["a.md"]


# --- changed after import ---------------------------------------------------------


def test_a_source_changed_after_import_is_refused_once_without_a_model_call(
    env: _Env,
) -> None:
    path = env.drop("a.md")
    env.job()
    env.settle()
    env.job()
    calls = env.model.calls
    raw_before = (env.root / "raw" / "a.md").read_bytes()
    path.write_text("A different save.\n", encoding="utf-8")
    env.job()
    env.settle()

    result = env.job()

    assert result is not None
    assert result.units_done == 1
    assert env.model.calls == calls
    assert (env.root / "raw" / "a.md").read_bytes() == raw_before
    assert env.observation("a.md").outcome == watch.REFUSED
    env.settle()
    assert env.job() is None


# --- the runner's extension point ---------------------------------------------------


def test_run_due_jobs_runs_the_watch_before_maintenance(env: _Env) -> None:
    env.drop("a.md")
    env.job()
    env.settle()

    results = runner.run_due_jobs(
        env.root,
        unattended=env.cfg(),
        stop=env.stop,
        ports=env.ports(),
        maintenance_due=True,
    )

    assert [r.kind for r in results] == ["watch", "maintenance"]
    assert env.raw() == ["a.md"]


def test_the_watch_is_off_without_an_inbox(env: _Env) -> None:
    env.drop("a.md")

    result = watch.run_watch_job(
        env.root,
        unattended=config.UnattendedConfig(quiet_seconds=_QUIET),
        stop=env.stop,
        ports=env.ports(),
    )

    assert result is None
