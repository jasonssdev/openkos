"""The report verbs follow the TTY-gated output convention (ADR-0042).

`status`, `lint`, `duplicates`, `contradictions`, `revisions`,
`suggest-relations` and `suggest-volatility` already separate their sections
with blank lines in every mode, so what the convention adds on a terminal is
wrapped prose under a hanging indent, advisory lines on stderr prefixed
`note:` / `warning:`, and `suggest-relations --apply` items set off as blocks.
Piped, every one of them prints exactly what it always did.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos.cli.main import app
from openkos.graph.base import Edge
from openkos.resolution.edge_typing import EdgeSuggestion, EdgeSuggestionBatch
from openkos.resolution.volatility_typing import TierSuggestion, TierSuggestionBatch
from tests.unit.cli import test_contradictions as contradictions_tests
from tests.unit.cli import test_duplicates as duplicates_tests
from tests.unit.cli import test_lint as lint_tests
from tests.unit.cli import test_revisions as revisions_tests
from tests.unit.cli import test_status as status_tests
from tests.unit.cli import test_suggest_relations as relations_tests
from tests.unit.cli import test_suggest_volatility as volatility_tests
from tests.unit.cli.conftest import (
    commit_pending_fixture_docs,
    disable_local_exemption,
)

runner = CliRunner()

_LONG = " ".join(["because"] * 40)


@pytest.fixture(autouse=True)
def _identity(pinned_git_identity: None) -> None:
    """A git identity for every scenario, `init` included."""


def _tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


def _assert_wrapped(text: str, marker: str, *, hanging: str) -> None:
    """The first line holding `marker` starts a block that wraps to 80 columns
    and continues under `hanging`."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if marker in line)
    block = [lines[start]]
    for line in lines[start + 1 :]:
        if not line.startswith(hanging + " ") and not line.startswith(hanging):
            break
        if (
            line.strip() == ""
            or line.lstrip() != line.lstrip(" ")
            or line.startswith(hanging[:-1] + "-")
        ):
            break
        block.append(line)
    assert len(block) > 1, text
    assert all(len(line) <= 80 for line in block), block


# -- status ------------------------------------------------------------------


def _status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, tty: bool) -> str:
    status_tests._init_workspace(tmp_path, monkeypatch)
    if tty:
        _tty(monkeypatch)
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    return result.stdout


def test_status_wraps_a_long_line_on_a_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _status(tmp_path, monkeypatch, tty=True)
    _assert_wrapped(out, "Similar-title", hanging="    ")
    assert "\n\nNeeds attention:\n" in out


def test_status_piped_keeps_the_long_line_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _status(tmp_path, monkeypatch, tty=False)
    assert (
        "  Similar-title candidates are not counted here -- run "
        "`openkos duplicates` for the full scan.\n"
    ).replace("--", "—") in out


# -- lint --------------------------------------------------------------------


def _lint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, tty: bool) -> str:
    lint_tests._init_workspace(tmp_path, monkeypatch)
    concepts = tmp_path / "bundle" / "concepts"
    concepts.mkdir()
    ghost = "ghost" * 12
    (concepts / "stoicism.md").write_text(
        "---\ntype: Concept\ntitle: Stoicism\n"
        f"relations:\n  - target: concepts/{ghost}\n    type: relates-to\n"
        "---\nBody.\n",
        encoding="utf-8",
    )
    index = tmp_path / "bundle" / "index.md"
    index.write_text(
        index.read_text(encoding="utf-8")
        + "\n# Concepts\n\n* [Stoicism](/concepts/stoicism.md) - test fixture.\n",
        encoding="utf-8",
    )
    if tty:
        _tty(monkeypatch)
    result = runner.invoke(app, ["lint"])
    assert result.exit_code == 0
    return result.stdout


def test_lint_wraps_a_long_finding_on_a_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _lint(tmp_path, monkeypatch, tty=True)
    lines = out.splitlines()
    start = next(i for i, line in enumerate(lines) if "concepts/stoicism:" in line)
    assert lines[start + 1].startswith("    ")
    assert all(len(line) <= 80 for line in lines[start : start + 2])


def test_lint_piped_keeps_the_long_finding_on_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _lint(tmp_path, monkeypatch, tty=False)
    assert any(
        line.startswith("  concepts/stoicism:") and "ghost" * 12 in line
        for line in out.splitlines()
    )


# -- duplicates --------------------------------------------------------------


def _duplicates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, tty: bool) -> Any:
    duplicates_tests._init_workspace(tmp_path, monkeypatch)
    report = duplicates_tests._two_event_group()
    notice = "capped " + _LONG
    monkeypatch.setattr(
        "openkos.application.duplicates_service.candidate_group_truncation_notice",
        lambda report: notice,
    )
    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", lambda *a, **k: report
    )
    if tty:
        _tty(monkeypatch)
    result = runner.invoke(app, ["duplicates"])
    assert result.exit_code == 0
    return result


def test_duplicates_wraps_the_legend_and_prefixes_the_notice_on_a_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _duplicates(tmp_path, monkeypatch, tty=True)
    _assert_wrapped(result.stdout, "ACRONYM", hanging="  ")
    assert result.stderr.startswith("note: capped ")
    assert all(len(line) <= 80 for line in result.stderr.splitlines())


def test_duplicates_piped_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _duplicates(tmp_path, monkeypatch, tty=False)
    assert any(
        line.startswith("Legend: ") and line.endswith("identical titles).")
        for line in result.stdout.splitlines()
    )
    assert result.stderr == "capped " + _LONG + "\n"


# -- contradictions ----------------------------------------------------------


def _contradictions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
    *,
    tty: bool,
) -> Any:
    contradictions_tests._init_workspace(tmp_path, monkeypatch)
    contradictions_tests._write_related_pair(tmp_path)
    seed_vectors_db(tmp_path)
    monkeypatch.setattr(
        "openkos.cli.main.find_contradictions",
        lambda *a, **k: contradictions_tests._found(
            [
                contradictions_tests._verdict(
                    rationale=_LONG, conflicting_claims=(_LONG, "short")
                )
            ],
            1,
        ),
    )
    contradictions_tests._CountingOllamaClient.calls = []
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient", contradictions_tests._CountingOllamaClient
    )

    def _broken(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("openkos.cli.curate.persist_findings", _broken)
    if tty:
        _tty(monkeypatch)
    result = runner.invoke(app, ["contradictions"])
    assert result.exit_code == 0, result.stderr
    return result


def test_contradictions_wraps_prose_and_prefixes_the_warning_on_a_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    result = _contradictions(tmp_path, monkeypatch, seed_vectors_db, tty=True)
    _assert_wrapped(result.stdout, "  - because", hanging="    ")
    _assert_wrapped(result.stdout, "  rationale:", hanging="    ")
    assert result.stderr.startswith("warning: failed to persist findings")
    assert all(len(line) <= 80 for line in result.stderr.splitlines())


def test_contradictions_piped_is_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    result = _contradictions(tmp_path, monkeypatch, seed_vectors_db, tty=False)
    assert f"  rationale: {_LONG}\n" in result.stdout
    assert result.stderr.startswith(
        "openkos contradictions: warning -- failed to persist findings"
    )


# -- suggest-volatility ------------------------------------------------------


def _volatility(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, tty: bool) -> str:
    volatility_tests._init_workspace(tmp_path, monkeypatch)
    batch = TierSuggestionBatch(
        results=[
            TierSuggestion(
                type_name="Person",
                current_default="slow",
                suggested_tier="slow",
                rationale=_LONG,
            )
        ]
    )
    monkeypatch.setattr("openkos.cli.main.suggest_volatility", lambda *a, **k: batch)
    if tty:
        _tty(monkeypatch)
    result = runner.invoke(app, ["suggest-volatility"])
    assert result.exit_code == 0, result.stderr
    return result.stdout


def test_suggest_volatility_wraps_the_rationale_on_a_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _volatility(tmp_path, monkeypatch, tty=True)
    _assert_wrapped(out, "because", hanging="    ")


def test_suggest_volatility_piped_keeps_the_rationale_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _volatility(tmp_path, monkeypatch, tty=False)
    assert f"  rationale: {_LONG}\n" in out


# -- suggest-relations -------------------------------------------------------


def _relations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
    *,
    tty: bool,
    stdin: str | None = None,
) -> Any:
    relations_tests._init_workspace(tmp_path, monkeypatch)
    relations_tests._write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="A")
    relations_tests._write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="B")
    commit_pending_fixture_docs()
    edges = [Edge("concepts/a", "concepts/b"), Edge("concepts/b", "concepts/a")]
    monkeypatch.setattr(
        "openkos.cli.main.candidate_edges", lambda bundle_dir, **kwargs: edges
    )
    batch = EdgeSuggestionBatch(
        results=[
            EdgeSuggestion(edge=edge, suggested_type="references", rationale=_LONG)
            for edge in edges
        ]
    )
    monkeypatch.setattr("openkos.cli.main.suggest_edge_types", lambda *a, **k: batch)
    if tty:
        _tty(monkeypatch)
    return runner.invoke(app, ["suggest-relations", *args], input=stdin)


def test_suggest_relations_report_wraps_the_rationale_on_a_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _relations(tmp_path, monkeypatch, ["--auto"], tty=True)
    assert result.exit_code == 0, result.stderr
    _assert_wrapped(result.stdout, "because", hanging="    ")


def test_suggest_relations_apply_item_is_a_block_on_a_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _relations(
        tmp_path, monkeypatch, ["--auto", "--apply"], tty=True, stdin="n\nn\n"
    )
    assert result.exit_code == 0, result.stderr
    assert "\n\n[references] concepts/b -> concepts/a\n  rationale: because" in (
        "\n" + result.stdout
    )
    _assert_wrapped(result.stdout, "because", hanging="    ")


def test_suggest_relations_piped_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _relations(tmp_path, monkeypatch, ["--auto"], tty=False)
    assert result.exit_code == 0, result.stderr
    assert f"  rationale: {_LONG}\n" in result.stdout


# -- revisions ---------------------------------------------------------------


def _revisions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, tty: bool) -> Any:
    revisions_tests._init_workspace(tmp_path, monkeypatch)
    disable_local_exemption(tmp_path)
    revisions_tests._seed_decision_and_vector(tmp_path, "decisions/a", 0, title="A")
    revisions_tests._seed_decision_and_vector(tmp_path, "decisions/b", 0, title="B")
    revisions_tests._patch_llm(
        monkeypatch,
        revisions_tests._ScriptedLLM(
            [revisions_tests._verdict_reply(verdict="reverses", confidence=0.9)]
        ),
    )
    monkeypatch.setattr(
        "openkos.cli.main.revision_truncation_notice", lambda plan: "capped " + _LONG
    )
    if tty:
        _tty(monkeypatch)
    result = runner.invoke(app, ["revisions", "--auto"])
    assert result.exit_code == 0, result.stderr
    return result


def test_revisions_prefixes_its_notices_on_a_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _revisions(tmp_path, monkeypatch, tty=True)
    lines = result.stderr.splitlines()
    assert lines[0].startswith("note: experimental -- ")
    assert any(line.startswith("note: capped ") for line in lines)
    notice_block = [
        line
        for line in lines
        if line.startswith(("note:", "  ")) and "Proceed" not in line
    ]
    assert all(len(line) <= 80 for line in notice_block)
    assert all(len(line) <= 80 for line in result.stdout.splitlines())


def test_revisions_piped_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _revisions(tmp_path, monkeypatch, tty=False)
    assert result.stderr.startswith("openkos revisions: experimental -- ")
    assert "capped " + _LONG + "\n" in result.stderr
