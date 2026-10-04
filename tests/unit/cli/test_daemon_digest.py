"""The daemon's 'what changed' digest: each automatic commit of a pass, with the
command that undoes it, on one line (#1268, ADR-0044 deliverable 5)."""

import dataclasses
import re
import shlex
import subprocess
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos import logsetup, userstate
from openkos.application import digest, runner
from openkos.cli import daemon as daemon_module
from openkos.cli.main import app

cli = CliRunner()
_NOW = datetime(2026, 9, 30, 15, 0, tzinfo=UTC)
_ACTION_LINE = re.compile(r"^\s+(?P<sha>[0-9a-f]{7,40}) .* -- undo: (?P<undo>.+)$")


@pytest.fixture(autouse=True)
def _daemon_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned_git_identity: None
) -> Iterator[None]:
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


class _Clock:
    def __init__(self) -> None:
        self.now = _NOW

    def __call__(self) -> datetime:
        return self.now


class _DecliningModel:
    def chat(self, messages: Sequence[object]) -> str:
        return '{"extract": false}'


def _idle_ports() -> runner.RunnerPorts:
    return runner.RunnerPorts(
        refresh_derived=lambda r: None,
        commit_paths=lambda r, p, m: None,
        paths_dirty=lambda r, p: False,
        repo_root=lambda r: r,
        has_git_identity=lambda r: True,
        lint_counts=lambda layout: {},
        now=lambda: _NOW,
    )


def _watch_two_files(root: Path, monkeypatch: pytest.MonkeyPatch) -> _Clock:
    """The REAL production ports and the real VCS, with only the clock, the
    derived refresh and the model replaced; two files wait in the inbox."""
    inbox = root.parent / "inbox"
    inbox.mkdir()
    cfg_path = root / "openkos.yaml"
    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8")
        + f"\nunattended:\n  inbox: {inbox}\n  quiet_seconds: 5\n",
        encoding="utf-8",
    )
    for name in ("alpha.md", "beta.md"):
        (inbox / name).write_text(f"Notes in {name}.\n", encoding="utf-8")
    clock = _Clock()
    real = daemon_module.production_ports

    def ports(r: Path) -> runner.RunnerPorts:
        return dataclasses.replace(
            real(r), now=clock, refresh_derived=lambda _r: None, advisor_stages=()
        )

    monkeypatch.setattr(daemon_module, "production_ports", ports)
    monkeypatch.setattr(
        "openkos.cli.main._chat_client", lambda cfg, *, task=None: _DecliningModel()
    )
    return clock


def _vcs(root: Path, *args: str) -> str:
    done = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def _digest_lines(stdout: str) -> list[re.Match[str]]:
    return [m for line in stdout.splitlines() if (m := _ACTION_LINE.match(line))]


def test_a_pass_that_imported_files_ends_with_each_commit_and_its_undo(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _watch_two_files(root, monkeypatch)
    assert cli.invoke(app, ["daemon", "--once"]).exit_code == 0  # starts the clock
    clock.now += timedelta(seconds=6)
    head_before = _vcs(root, "rev-parse", "HEAD")

    result = cli.invoke(app, ["daemon", "--once"])

    assert result.exit_code == 0, result.output
    made = _vcs(root, "rev-list", "--reverse", f"{head_before}..HEAD").split()
    assert len(made) == 2
    lines = _digest_lines(result.stdout)
    assert [m["sha"] for m in lines] == [
        _vcs(root, "rev-parse", "--short", c) for c in reversed(made)
    ]
    assert all(m["undo"].startswith("git revert ") for m in lines)
    assert "what changed -- 2 automatic commits, newest first" in result.stdout
    assert "sources/alpha" in result.stdout
    # The digest closes the pass: it comes after the job report.
    assert result.stdout.index("watch: completed") < result.stdout.index("what changed")


def test_the_printed_undo_commands_restore_the_workspace_end_to_end(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _watch_two_files(root, monkeypatch)
    before = _vcs(root, "rev-parse", "HEAD")
    cli.invoke(app, ["daemon", "--once"])
    clock.now += timedelta(seconds=6)
    result = cli.invoke(app, ["daemon", "--once"])
    assert (root / "raw" / "alpha.md").exists()
    assert (root / "raw" / "beta.md").exists()

    for match in _digest_lines(result.stdout):  # newest first, as printed
        argv = shlex.split(match["undo"])
        assert argv[:2] == ["git", "revert"]
        _vcs(root, *argv[1:], "--no-edit")

    assert not (root / "raw" / "alpha.md").exists()
    assert not (root / "raw" / "beta.md").exists()
    assert not (root / "bundle" / "sources" / "alpha.md").exists()
    # Both reverts applied cleanly, newest first, and the tracked content is
    # byte-identical to before the pass (index.md and log.md included).
    assert _vcs(root, "diff", before, "--stat", "--", "raw", "bundle") == ""
    assert _vcs(root, "status", "--porcelain", "--", "raw", "bundle") == ""


def test_a_pass_with_no_automatic_commit_prints_no_digest(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(daemon_module, "production_ports", lambda r: _idle_ports())

    result = cli.invoke(app, ["daemon", "--once"])

    assert result.exit_code == 0, result.output
    assert "what changed" not in result.output


def _results(*shas_by_job: tuple[str, ...]) -> tuple[runner.JobResult, ...]:
    return tuple(
        runner.JobResult(
            "watch",
            "completed",
            actions=tuple(
                digest.record_action(s, [f"bundle/topics/{s}.md"], f"openkos: {s}")
                for s in shas
            ),
        )
        for shas in shas_by_job
    )


def test_every_action_of_every_job_is_listed(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Completeness: an action missing from the digest is a failure, whichever
    job made it (the B4 bar of the pre-registration reads exactly this)."""
    results = _results(("aaaaaaa", "bbbbbbb"), ("ccccccc",), ("ddddddd",))
    monkeypatch.setattr(daemon_module, "run_due_jobs", lambda *a, **k: results)
    monkeypatch.setattr(daemon_module, "production_ports", lambda r: _idle_ports())

    result = cli.invoke(app, ["daemon", "--once"])

    assert result.exit_code == 0, result.output
    order = [(m["sha"], m["undo"]) for m in _digest_lines(result.stdout)]
    assert order == [
        (sha, f"git revert {sha}")
        for sha in ("ddddddd", "ccccccc", "bbbbbbb", "aaaaaaa")
    ]
    assert "4 automatic commits, newest first" in result.stdout


def test_on_a_terminal_the_digest_is_a_separate_section_and_stays_greppable(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    results = _results(("aaaaaaa",))
    monkeypatch.setattr(daemon_module, "run_due_jobs", lambda *a, **k: results)
    monkeypatch.setattr(daemon_module, "production_ports", lambda r: _idle_ports())
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)

    result = cli.invoke(app, ["daemon", "--once"])

    assert "\n\nopenkos daemon: what changed -- 1 automatic commit\n" in result.stdout
    assert "\x1b[" not in result.stdout  # no ANSI, ever (ADR-0042)
    assert _digest_lines(result.stdout)[0]["undo"] == "git revert aaaaaaa"
