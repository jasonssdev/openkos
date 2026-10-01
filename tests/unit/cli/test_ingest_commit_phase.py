"""The `ingest` verb's commit-phase wiring (#1137, ADR-0036): the guard no longer
holds the workspace lock around the body, so extraction runs unlocked, a batch
never holds the lock between files, and only each file's commit phase takes it.
"""

import contextlib
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos import lock
from openkos.cli.main import app
from openkos.llm.base import Message
from tests.unit.cli.test_ingest import _init_workspace
from tests.unit.conftest import LOCAL_BACKEND_LOCALITY

runner = CliRunner()


class _HookedLLM:
    locality = LOCAL_BACKEND_LOCALITY

    def __init__(self, hook: Callable[[], None]) -> None:
        self.hook = hook
        self.calls = 0

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls += 1
        self.hook()
        return '{"extract": false}'


def _install(monkeypatch: pytest.MonkeyPatch, hook: Callable[[], None]) -> _HookedLLM:
    fake = _HookedLLM(hook)
    monkeypatch.setattr("openkos.cli.main.OllamaClient", lambda *a, **k: fake)
    return fake


def _is_free(root: Path) -> bool:
    try:
        with lock.workspace_lock(root):
            return True
    except lock.WorkspaceBusyError:
        return False


def test_a_single_ingest_extracts_without_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "notes.txt").write_text("Some raw notes.", encoding="utf-8")
    observed: list[bool] = []
    llm = _install(monkeypatch, lambda: observed.append(_is_free(tmp_path)))

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert llm.calls >= 1  # the list below is not vacuous
    assert observed == [True] * llm.calls
    assert (tmp_path / "bundle" / "sources" / "notes.md").is_file()
    assert _is_free(tmp_path)  # and nothing leaked the lock afterwards


def test_a_batch_never_holds_the_lock_between_or_during_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    notes = tmp_path / "notes"
    notes.mkdir()
    for name in ("a.txt", "b.txt", "c.txt"):
        (notes / name).write_text(f"Notes in {name}.", encoding="utf-8")
    observed: list[bool] = []
    llm = _install(monkeypatch, lambda: observed.append(_is_free(tmp_path)))

    result = runner.invoke(app, ["ingest", "notes", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert llm.calls >= 3  # at least one extraction per file
    assert observed == [True] * llm.calls
    for slug in ("a", "b", "c"):
        assert (tmp_path / "bundle" / "sources" / f"{slug}.md").is_file()


def test_a_busy_commit_phase_exits_3_and_a_batch_still_tries_every_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    notes = tmp_path / "notes"
    notes.mkdir()
    for name in ("a.txt", "b.txt"):
        (notes / name).write_text(f"Notes in {name}.", encoding="utf-8")
    before = {
        p.relative_to(tmp_path): p.read_bytes()
        for p in sorted(tmp_path.rglob("*"))
        if p.is_file() and ".openkos" not in p.parts and ".git" not in p.parts
    }
    holder = contextlib.ExitStack()
    taken = {"held": False}

    def _another_process_takes_the_lock() -> None:
        if not taken["held"]:
            taken["held"] = True
            holder.enter_context(lock.workspace_lock(tmp_path))

    llm = _install(monkeypatch, _another_process_takes_the_lock)
    try:
        result = runner.invoke(app, ["ingest", "notes", "--auto"])
    finally:
        holder.close()

    assert taken["held"]
    assert result.exit_code == 3
    assert llm.calls >= 2  # the second file was still extracted and attempted
    assert result.stderr.count("refusing to run --") == 2
    after = {
        p.relative_to(tmp_path): p.read_bytes()
        for p in sorted(tmp_path.rglob("*"))
        if p.is_file() and ".openkos" not in p.parts and ".git" not in p.parts
    }
    assert after == before
