"""`curate`'s Structure stage is opt-in (#1268, curate-command "The Structure
Stage Is Reviewed On Request").

The stage's suggestions are still computed elsewhere and kept as
`relation_type` pending rows; `curate` presents them only for `--structure`
(or `--accept structure`), and always says how many wait. Every test here is
model-free: the suggester, the candidate walk and the chat-client resolver are
counting wrappers, so a call that should not happen is an assertion, not a
spend.
"""

import contextlib
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos import config
from openkos.cli.main import app
from openkos.state import derived
from openkos.state import pending_queue as pq
from tests.unit.cli.test_curate import (
    _init_apply_workspace,
    _lines,
    _reindexed_workspace,
    _set_review,
    _simulate_tty,
    _two_edge_structure_queue,
    _write_doc,
)

runner = CliRunner()

_COMMAND = "openkos curate --structure"


class _Calls:
    """What a Structure stage that was NOT presented must never do."""

    def __init__(self) -> None:
        self.suggest = 0
        self.walk = 0
        self.chat = 0


def _count_structure_work(monkeypatch: pytest.MonkeyPatch) -> _Calls:
    """Wrap whatever the fakes currently are, so call AFTER installing them."""
    from openkos.cli import curate as curate_module

    calls = _Calls()
    real_walk = curate_module.candidate_edges  # type: ignore[attr-defined]
    real_suggest = curate_module.suggest_edge_types  # type: ignore[attr-defined]
    real_client = curate_module.application_backends.chat_client  # type: ignore[attr-defined]

    def walk(*a: object, **k: object) -> object:
        calls.walk += 1
        return real_walk(*a, **k)  # type: ignore[arg-type]

    def suggest(*a: object, **k: object) -> object:
        calls.suggest += 1
        return real_suggest(*a, **k)  # type: ignore[arg-type]

    class CountingBackend:
        """Delegates everything; counts the model calls (`chat`)."""

        def __init__(self, inner: object) -> None:
            self._inner = inner

        def chat(self, *a: object, **k: object) -> object:
            calls.chat += 1
            return self._inner.chat(*a, **k)  # type: ignore[attr-defined]

        def __getattr__(self, name: str) -> object:
            return getattr(self._inner, name)

    def client(*a: object, **k: object) -> object:
        return CountingBackend(real_client(*a, **k))

    monkeypatch.setattr("openkos.cli.curate.candidate_edges", walk)
    monkeypatch.setattr("openkos.cli.curate.suggest_edge_types", suggest)
    monkeypatch.setattr("openkos.cli.curate.application_backends.chat_client", client)
    return calls


def _workspace(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    for name in ("a", "b", "c"):
        _write_doc(tmp_path / "bundle" / "concepts" / f"{name}.md", title=name)
    _reindexed_workspace(tmp_path, monkeypatch)
    _two_edge_structure_queue(monkeypatch)
    _simulate_tty(monkeypatch)


@contextlib.contextmanager
def _no_lock() -> Iterator[None]:
    yield


def _seed_rows(root: Path, *suggested: str) -> None:
    """One open `relation_type` row per entry of `suggested` (a relation type)."""
    layout = config.WorkspaceLayout(root)
    conn = derived.open_derived_connection(layout.findings_db_path)
    pq.ensure_schema(conn)
    for index, kind in enumerate(suggested):
        source, target = f"concepts/s{index}", f"concepts/t{index}"
        pq.upsert_proposal(
            conn,
            pq.Proposal(
                kind="relation_type",
                key_body=pq.relation_type_key(source, target),
                producer="test/1",
                payload=json.dumps({"suggested_type": kind}),
                targets=(source, target),
            ),
            commit_section=_no_lock,
            bundle_dir=layout.bundle_dir,
        )
    conn.close()


def test_default_curate_neither_presents_nor_computes_structure(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _workspace(tmp_path, tmp_path_factory, monkeypatch)
    calls = _count_structure_work(monkeypatch)

    result = runner.invoke(app, ["curate"], input="")

    assert result.exit_code == 0, result.output
    assert "Relate concepts/a" not in result.output
    assert "untyped edge(s) ->" not in result.output
    assert (calls.suggest, calls.walk, calls.chat) == (0, 0, 0)


def test_the_summary_names_how_many_suggestions_wait_and_the_command(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _workspace(tmp_path, tmp_path_factory, monkeypatch)
    _seed_rows(tmp_path, "references", "related_to", "supersedes")

    result = runner.invoke(app, ["curate"], input="")

    assert (
        "Structure: not reviewed this run -- 2 relation suggestion(s) waiting; "
        f"review them with `{_COMMAND}`."
    ) in _lines(result.stdout)


def test_with_nothing_waiting_the_summary_still_names_the_opt_in(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _workspace(tmp_path, tmp_path_factory, monkeypatch)

    result = runner.invoke(app, ["curate"], input="")

    assert (
        "Structure: not reviewed this run -- no relation suggestions are waiting; "
        f"`{_COMMAND}` computes and reviews them."
    ) in _lines(result.stdout)


def test_structure_flag_presents_the_stage_exactly_as_before(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _workspace(tmp_path, tmp_path_factory, monkeypatch)
    calls = _count_structure_work(monkeypatch)

    result = runner.invoke(app, ["curate", "--structure"], input="y\ny\nn\n")

    assert result.exit_code == 0, result.output
    assert "2 untyped edge(s) -> 2 LLM call(s)" in result.output
    assert "Relate concepts/a" in result.output
    assert "Structure: applied 1, skipped 1." in _lines(result.stdout)
    assert calls.walk >= 1


def test_accept_structure_implies_the_opt_in(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _workspace(tmp_path, tmp_path_factory, monkeypatch)

    result = runner.invoke(app, ["curate", "--accept", "structure"], input="y\n")

    assert "Structure: applied 2, skipped 0." in _lines(result.stdout)


def test_review_false_does_not_opt_in(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`review: false` is standing consent to SAVE without asking; it is not a
    request to be shown the stage."""
    _workspace(tmp_path, tmp_path_factory, monkeypatch)
    _set_review(tmp_path, False)
    calls = _count_structure_work(monkeypatch)

    result = runner.invoke(app, ["curate"], input="y\n")

    assert "Structure: not reviewed this run" in result.stdout
    assert (calls.suggest, calls.walk, calls.chat) == (0, 0, 0)


def test_accept_metadata_alone_does_not_opt_structure_in(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _workspace(tmp_path, tmp_path_factory, monkeypatch)
    calls = _count_structure_work(monkeypatch)

    result = runner.invoke(app, ["curate", "--accept", "metadata"], input="y\n")

    assert "Structure: not reviewed this run" in result.stdout
    assert calls.suggest == 0
