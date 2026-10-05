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
from typing import Any

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
        self.sha: str | None = None
        self.notices: list[str] = []
        self.stop = StopToken()
        self.on_commit: Callable[[], None] | None = None
        self.refreshes: list[Path] = []
        self.refresh_error: Exception | None = None

    def ingest_ports(
        self, section: CommitSection, run_budget: budget.BudgetedRun
    ) -> svc.IngestPorts:
        def autocommit(r: Path, paths: Sequence[str], message: str) -> str | None:
            self.commits.append(list(paths))
            if self.on_commit is not None:
                self.on_commit()
            return self.sha

        return svc.IngestPorts(
            chat_client=run_budget.wrap_chat_client(lambda cfg: self.model),
            autocommit=autocommit,
            after_commit=lambda layout, cfg: None,
            commit_section=section,
        )

    def ports(self) -> runner.RunnerPorts:
        return runner.RunnerPorts(
            refresh_derived=self.refresh_derived,
            commit_paths=lambda root, paths, message: None,
            paths_dirty=lambda root, paths: False,
            repo_root=lambda root: root,
            has_git_identity=lambda root: True,
            now=lambda: self.time.now,
            monotonic=lambda: 1000.0,
            sleep=lambda seconds: None,
            jitter=lambda low, high: high,
            wait_cap_seconds=0,
            watch=watch.WatchPorts(
                ingest_ports=self.ingest_ports, notify=self.notices.append
            ),
        )

    def refresh_derived(self, root: Path) -> object:
        self.refreshes.append(root)
        if self.refresh_error is not None:
            raise self.refresh_error
        return None

    def cfg(self, **kw: Any) -> config.UnattendedConfig:
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


def test_an_import_reports_its_commit_as_an_automatic_action(env: _Env) -> None:
    env.sha = "9c1d2e3"
    env.drop("a.md")
    env.job()
    env.settle()

    result = env.job()

    assert result is not None
    (action,) = result.actions
    assert action.sha == "9c1d2e3"
    assert action.undo == "git revert 9c1d2e3"
    assert "ingest a.md" in action.summary
    assert "sources/a" in action.concept_ids


def test_an_import_whose_commit_was_skipped_reports_no_action(env: _Env) -> None:
    env.sha = None  # not a repository, identity unset, or a failed commit
    env.drop("a.md")
    env.job()
    env.settle()

    result = env.job()

    assert result is not None
    assert result.actions == ()


def test_a_legacy_encoded_file_is_decoded_and_extracted(env: _Env) -> None:
    """A Mac Roman, CR-terminated export is text (#1224): the model is called,
    the raw copy keeps the original bytes, and the codec is announced."""
    original = "Decisi\u00f3n\rcoordinaci\u00f3n\r".encode("mac_roman")
    (env.inbox / "a.txt").write_bytes(original)
    env.job()
    env.settle()

    result = env.job()

    assert result is not None
    assert result.units_done == 1
    assert env.model.calls >= 1
    assert (env.root / "raw" / "a.txt").read_bytes() == original
    assert any("a.txt" in n and "mac_roman" in n for n in env.notices)


def test_a_binary_file_is_imported_with_a_visible_warning(env: _Env) -> None:
    """A text-extension source that ends as no-extractable-text is never
    silent in the unattended path (#1224): the warning names the file."""
    (env.inbox / "pic.txt").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
    env.job()
    env.settle()

    result = env.job()

    assert result is not None
    assert result.units_done == 1
    assert env.model.calls == 0
    assert any("pic.txt" in n and "no extractable text" in n for n in env.notices)


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


def test_non_text_files_are_not_candidates_for_import(env: _Env) -> None:
    """The watch is a sweep of a folder, so it applies the allowlist that
    `ingest <dir>` applies (#1261): a `.docx` or extensionless file is neither
    observed, counted as waiting, nor imported."""
    env.drop("note.md")
    env.drop("UPPER.TXT")
    (env.inbox / "report.docx").write_bytes(b"PK\x03\x04binary")
    (env.inbox / "Makefile").write_text("all:\n", encoding="utf-8")
    env.job()
    env.settle()

    result = env.job()

    assert result is not None
    assert result.units_done == 2
    assert env.raw() == ["UPPER.TXT", "note.md"]
    conn = jobs.open_jobs(config.WorkspaceLayout(env.root).jobs_db_path)
    try:
        observed = {o.path for o in jobs.observations(conn)}
    finally:
        conn.close()
    assert observed == {"note.md", "UPPER.TXT"}


def test_the_waiting_count_excludes_non_text_files(env: _Env) -> None:
    env.drop("note.md")
    (env.inbox / "report.docx").write_bytes(b"PK\x03\x04binary")
    announced: list[str] = []
    ports = dataclasses.replace(env.ports(), announce=announced.append)

    watch.run_watch_job(env.root, unattended=env.cfg(), stop=env.stop, ports=ports)

    assert announced == ["1 file seen in the inbox; will import after 5s quiet"]


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


def test_a_source_changed_after_import_is_imported_as_a_new_version(
    env: _Env,
) -> None:
    path = env.drop("a.md")
    env.job()
    env.settle()
    env.job()
    raw_before = (env.root / "raw" / "a.md").read_bytes()
    path.write_text("A different save.\n", encoding="utf-8")
    env.job()
    env.settle()

    result = env.job()

    assert result is not None
    assert result.units_done == 1
    assert (env.root / "raw" / "a.md").read_bytes() == raw_before
    assert (env.root / "raw" / "a-2.md").read_text(encoding="utf-8") == (
        "A different save.\n"
    )
    assert env.observation("a.md").outcome == watch.IMPORTED
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


# --- derived indexes (#1260) -------------------------------------------------


def _settled_drop(env: _Env, *names: str) -> None:
    for name in names:
        env.drop(name)
    env.job()  # observe
    env.settle()


def test_imports_refresh_the_derived_indexes_once_per_job(env: _Env) -> None:
    _settled_drop(env, "a.md", "b.md")
    result = env.job()
    assert result is not None
    assert result.units_done == 2
    assert env.refreshes == [env.root]


def test_a_job_that_imports_nothing_refreshes_nothing(env: _Env) -> None:
    env.drop("a.md")
    env.job()
    assert env.refreshes == []


def test_a_contended_refresh_never_fails_the_import(env: _Env) -> None:
    from openkos.application.reindex_service import LockContention

    env.refresh_error = LockContention("busy")
    _settled_drop(env, "a.md")
    result = env.job()
    assert result is not None
    assert result.outcome == "completed"
    assert result.units_done == 1
    assert any("derived" in n for n in env.notices)


def test_a_refused_refresh_never_fails_the_import(env: _Env) -> None:
    from openkos.application.reindex_service import ReindexRefused

    env.refresh_error = ReindexRefused("no")
    _settled_drop(env, "a.md")
    result = env.job()
    assert result is not None
    assert result.outcome == "completed"
    assert any("derived" in n for n in env.notices)


def _forget_observations(env: _Env) -> None:
    """Simulate files ingested before the watch ever observed them (a manual
    `ingest`, or a lost `jobs.db`): every settled file is a candidate again."""
    conn = jobs.open_jobs(config.WorkspaceLayout(env.root).jobs_db_path)
    try:
        jobs.forget_observations(conn, [o.path for o in jobs.observations(conn)])
    finally:
        conn.close()


def test_unchanged_files_do_not_spend_the_source_budget(env: _Env) -> None:
    _settled_drop(env, "a.md", "b.md", "c.md")
    assert env.job() is not None
    _forget_observations(env)
    env.drop("z.md", "A brand new note.\n")
    env.job(max_sources_per_pass=2)  # starts the clocks
    env.settle()
    calls = env.model.calls

    result = env.job(max_sources_per_pass=2)

    assert result is not None
    assert (result.outcome, result.units_deferred) == ("completed", 0)
    assert "z.md" in env.raw()
    assert env.model.calls > calls  # only the new file reached the model
    assert env.observation("z.md").outcome == watch.IMPORTED


def test_the_source_budget_still_bounds_real_imports(env: _Env) -> None:
    _settled_drop(env, "a.md")
    env.job()
    _forget_observations(env)
    env.drop("y.md", "First new note.\n")
    env.drop("z.md", "Second new note.\n")
    env.job(max_sources_per_pass=1)
    env.settle()

    result = env.job(max_sources_per_pass=1)

    assert result is not None
    assert (result.outcome, result.detail_code) == (
        "budget_exhausted",
        "max_sources_per_pass",
    )
    assert result.units_deferred == 1


def test_a_job_of_only_unchanged_files_refreshes_nothing(env: _Env) -> None:
    _settled_drop(env, "a.md")
    assert env.job() is not None  # imports it
    _forget_observations(env)
    env.job()  # observes it again
    env.settle()
    refreshes = len(env.refreshes)

    result = env.job()

    assert result is not None
    assert len(env.refreshes) == refreshes


# --- the watch's lines name the file and carry the daemon's prefix (#1265) --------


_PREFIX = "openkos daemon: watch: "


def test_an_unchanged_file_is_reported_by_name_with_the_daemon_prefix(
    env: _Env,
) -> None:
    _settled_drop(env, "a.md")
    env.job()  # imports it
    _forget_observations(env)
    env.job()  # observes it again
    env.settle()
    env.notices.clear()

    env.job()

    assert env.notices == [
        f"{_PREFIX}'a.md' unchanged -- already imported; nothing to do."
    ]


def test_an_import_is_reported_by_name_and_outcome(env: _Env) -> None:
    _settled_drop(env, "a.md")
    env.notices.clear()

    env.job()

    assert len(env.notices) == 1
    assert env.notices[0].startswith(f"{_PREFIX}'a.md' imported -- ")


def test_a_new_version_is_reported_as_such(env: _Env) -> None:
    path = env.drop("a.md")
    env.job()
    env.settle()
    env.job()
    path.write_text("A different save.\n", encoding="utf-8")
    env.job()
    env.settle()
    env.notices.clear()

    env.job()

    assert any(
        n.startswith(f"{_PREFIX}'a.md' imported as a new version") for n in env.notices
    )


def test_every_watch_line_carries_the_daemon_prefix_and_no_ingest_hint(
    env: _Env,
) -> None:
    original = "Decisión\rcoordinación\r".encode("mac_roman")
    (env.inbox / "a.txt").write_bytes(original)
    env.job()
    env.settle()
    env.job()

    assert env.notices
    assert all(n.startswith(_PREFIX) for n in env.notices)
    assert not any("--re-extract" in n or "openkos ingest:" in n for n in env.notices)


# --- an unchanged file never meets the admission gate (#1265) ---------------------


def test_an_unchanged_file_larger_than_the_call_budget_is_not_refused(
    env: _Env,
) -> None:
    """Admission priced the file as if it would be extracted; the ingest then
    found it unchanged and spent nothing. A file that costs nothing must not be
    queued as `exceeds_budget` because of what it would cost if it did not."""
    _settled_drop(env, "a.md")
    assert env.job() is not None  # imported with a normal budget
    _forget_observations(env)
    env.job(max_calls_per_pass=1)  # observes it again
    env.settle()
    calls = env.model.calls

    result = env.job(max_calls_per_pass=1)

    assert result is not None
    assert (result.outcome, result.units_deferred) == ("completed", 0)
    assert env.observation("a.md").outcome == watch.IMPORTED
    assert env.model.calls == calls


def test_a_changed_file_over_the_call_budget_is_still_refused(env: _Env) -> None:
    _settled_drop(env, "a.md")
    assert env.job() is not None
    path = env.inbox / "a.md"
    path.write_text("A different save.\n", encoding="utf-8")
    env.job(max_calls_per_pass=1)
    env.settle()

    env.job(max_calls_per_pass=1)

    assert env.observation("a.md").outcome == watch.EXCEEDS_BUDGET
