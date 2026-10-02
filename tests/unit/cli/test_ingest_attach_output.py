"""What `ingest` says when a candidate attaches to an existing concept instead
of forking it (attach-at-ingest, #1268): the proposed-changes line, the import
summary, the stderr note and the per-file batch suffix."""

from pathlib import Path

import pytest

from openkos.cli.main import app
from tests.unit.cli.test_ingest import (  # noqa: F401 -- autouse fixture
    _concept_reply,
    _default_llm,
    _init_workspace,
    _patch_llm,
    runner,
)


def _two_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply(title="Stoic Practice"))
    (tmp_path / "notes-a.txt").write_text("Notes from source A.", encoding="utf-8")
    (tmp_path / "notes-b.txt").write_text("Notes from source B.", encoding="utf-8")
    assert runner.invoke(app, ["ingest", "notes-a.txt", "--auto"]).exit_code == 0


def test_the_preview_marks_an_attach_as_a_revision_not_a_new_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _two_sources(tmp_path, monkeypatch)

    result = runner.invoke(app, ["ingest", "notes-b.txt", "--auto"])

    assert result.exit_code == 0
    assert "~ bundle/concepts/stoic-practice.md (revised" in result.stdout
    assert "+ bundle/concepts/stoic-practice.md" not in result.stdout
    assert "stoic-practice-2" not in result.stdout


def test_the_summary_names_each_revised_concept_and_its_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _two_sources(tmp_path, monkeypatch)

    result = runner.invoke(app, ["ingest", "notes-b.txt", "--auto"])

    assert "1 existing object revised" in result.stdout
    assert "revised concepts/stoic-practice (now version 2)" in result.stdout
    assert "Source only" not in result.stdout


def test_the_attach_is_noted_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _two_sources(tmp_path, monkeypatch)

    result = runner.invoke(app, ["ingest", "notes-b.txt", "--auto"])

    assert "revising it instead of writing a copy" in result.stderr


def test_a_plain_first_ingest_prints_no_attach_wording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply(title="Stoic Practice"))
    (tmp_path / "notes-a.txt").write_text("Notes from source A.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes-a.txt", "--auto"])

    assert "revised" not in result.stdout
    assert "+ bundle/concepts/stoic-practice.md" in result.stdout


def test_a_batch_outcome_line_names_the_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _two_sources(tmp_path, monkeypatch)
    batch = tmp_path / "batch"
    batch.mkdir()
    (batch / "notes-b.txt").write_text("Notes from source B.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "batch", "--auto"])

    assert result.exit_code == 0
    assert "notes-b.txt -- ingested (revised 1 existing object)" in result.stdout
