"""`query` takes no lock to answer, and `--save` takes it only to file.

The retrieval, the model call, the duplicate scan, the preview and the
confirmation all run lock-free; the lock is held for the commit phase alone,
which re-validates the sensitivity of every cited concept the insight's level
was folded from (workspace-lock: "A Plain `query` Takes No Lock").
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos import lock
from openkos.cli.main import app
from openkos.retrieval.answer import AnswerResult, Citation
from tests.unit.cli.conftest import confirm_after, snapshot_with_mtime
from tests.unit.cli.test_query_save import (
    _changed_under_bundle,
    _fake_matched_answer,
    _init_workspace,
    _write_concept,
)

runner = CliRunner()

_INSIGHT = "bundle/insights/stoicism-teaches-the-dichotomy-of-control.md"
_CONCEPT = "bundle/concepts/stoicism.md"


def _stub_answer(monkeypatch: pytest.MonkeyPatch, result: AnswerResult) -> None:
    monkeypatch.setattr(
        "openkos.application.query.answer", lambda *args, **kwargs: result
    )


def _workspace_with_a_cited_concept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path / "bundle", "concepts", "stoicism", title="Stoicism")
    citation = Citation(concept_id="concepts/stoicism", title="Stoicism")
    _stub_answer(monkeypatch, _fake_matched_answer(citations=[citation]))


def _simulate_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


def _raise_cited_concept_to_confidential(tmp_path: Path) -> None:
    path = tmp_path / _CONCEPT
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "sensitivity: private", "sensitivity: confidential"
        ),
        encoding="utf-8",
    )


def test_a_plain_query_answers_while_the_lock_is_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace_with_a_cited_concept(tmp_path, monkeypatch)

    with lock.workspace_lock(tmp_path):
        result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0, result.output
    assert "Stoicism teaches the dichotomy of control." in result.stdout
    assert "refusing to run" not in result.stderr


def test_the_lock_is_not_held_while_the_save_waits_at_its_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The window a human spends reading the preview must not starve a daemon."""
    _workspace_with_a_cited_concept(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    acquired_during_prompt: list[bool] = []

    def _probe() -> None:
        try:
            with lock.workspace_lock(tmp_path):
                acquired_during_prompt.append(True)
        except lock.WorkspaceBusyError:
            acquired_during_prompt.append(False)

    confirm_after(monkeypatch, _probe)

    result = runner.invoke(app, ["query", "what is stoicism?", "--save"])

    assert result.exit_code == 0, result.output
    assert acquired_during_prompt == [True]


def test_a_save_refuses_retry_safe_when_the_lock_is_held_at_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace_with_a_cited_concept(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    holder = lock.workspace_lock(tmp_path)
    held: list[object] = []

    def _take_the_lock_during_the_prompt() -> None:
        held.append(holder.__enter__())

    confirm_after(monkeypatch, _take_the_lock_during_the_prompt)
    before = snapshot_with_mtime(tmp_path)
    try:
        result = runner.invoke(app, ["query", "what is stoicism?", "--save"])
    finally:
        holder.__exit__(None, None, None)

    assert held, "the prompt stub never ran"
    assert result.exit_code == 3
    assert "openkos query: refusing to run" in result.stderr
    assert not (tmp_path / _INSIGHT).exists()
    assert _changed_under_bundle(before, snapshot_with_mtime(tmp_path)) == set()


def test_query_accepts_wait(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _workspace_with_a_cited_concept(tmp_path, monkeypatch)

    result = runner.invoke(app, ["query", "what is stoicism?", "--wait", "1"])

    assert result.exit_code == 0, result.output


@pytest.mark.usefixtures("pinned_git_identity")
def test_an_unchanged_cited_concept_lets_the_save_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control for the sentinel below: same flow, nothing raised."""
    _workspace_with_a_cited_concept(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    confirm_after(monkeypatch, lambda: None)

    result = runner.invoke(app, ["query", "what is stoicism?", "--save"])

    assert result.exit_code == 0, result.output
    insight = (tmp_path / _INSIGHT).read_text(encoding="utf-8")
    assert "sensitivity: private" in insight


@pytest.mark.usefixtures("pinned_git_identity")
def test_a_cited_concept_raised_to_confidential_between_phases_refuses_the_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SENTINEL. The insight's level was folded as `private` from a cited
    concept that was `private`; another process raises that concept to
    `confidential` while the human reads the preview. Filing now would write
    a `private` synthesis of confidential content.

    Asserts the carried value, not only the verdict: the concept keeps its
    raised level, no insight exists, and the catalog is byte-identical.
    """
    _workspace_with_a_cited_concept(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    confirm_after(monkeypatch, lambda: _raise_cited_concept_to_confidential(tmp_path))
    index_before = (tmp_path / "bundle" / "index.md").read_bytes()
    log_before = (tmp_path / "bundle" / "log.md").read_bytes()

    result = runner.invoke(app, ["query", "what is stoicism?", "--save"])

    assert result.exit_code == 3
    assert "concepts/stoicism" in result.stderr
    assert "Nothing was written." in result.stderr
    assert not (tmp_path / _INSIGHT).exists()
    assert (tmp_path / "bundle" / "index.md").read_bytes() == index_before
    assert (tmp_path / "bundle" / "log.md").read_bytes() == log_before
    assert "sensitivity: confidential" in (tmp_path / _CONCEPT).read_text(
        encoding="utf-8"
    )


def test_the_sentinel_cannot_pass_by_refusing_for_another_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal is the read-dependency guard's, not the drift guard's:
    the catalog files are untouched, so only the cited concept can have
    moved."""
    _workspace_with_a_cited_concept(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    confirm_after(monkeypatch, lambda: _raise_cited_concept_to_confidential(tmp_path))

    result = runner.invoke(app, ["query", "what is stoicism?", "--save"])

    assert result.exit_code == 3
    assert "index.md" not in result.stderr
    assert "log.md" not in result.stderr
