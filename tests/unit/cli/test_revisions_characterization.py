"""Byte-identity characterization tests for `openkos revisions` (issue #1168,
findings slice), the `test_reindex_characterization.py` pattern.

`revisions`' orchestration moved into `application/revisions.py`'s
`run_revisions`; the CLI kept rendering, the TTY cost gate and the exit-code
mapping. The existing `test_revisions.py` assertions pin individual
substrings; this file pins the COMPLETE `stdout` + `stderr` + exit code, and
the persisted `findings.db` rows, of every path of the verb against goldens
recorded on the tree BEFORE the move.
"""

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from openkos import config
from openkos.cli.main import app
from openkos.llm.ollama import OllamaError, OllamaModelNotFound, OllamaUnavailable
from openkos.state import derived, revision_findings
from tests.unit.cli.conftest import disable_local_exemption
from tests.unit.cli.golden_support import Goldens, normalise
from tests.unit.cli.test_revisions import (
    _init_workspace,
    _patch_llm,
    _RaisingLLM,
    _record_current_finding,
    _ScriptedLLM,
    _seed_decision_and_vector,
    _simulate_tty,
    _verdict_reply,
)

runner = CliRunner()

_GOLDENS = Goldens(
    Path(__file__).parent / "fixtures" / "revisions_characterization_goldens.json"
)


def _rows(tmp_path: Path) -> list[list[Any]]:
    layout = config.WorkspaceLayout(tmp_path)
    if not layout.findings_db_path.exists():
        return []
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        persisted = revision_findings.open_revision_findings(conn)
    finally:
        conn.close()
    return [
        [
            list(row.pair_ids),
            row.verdict,
            row.confidence,
            row.rationale,
            row.include_confidential,
        ]
        for row in persisted
    ]


def _run(
    scenario: str, tmp_path: Path, args: list[str], *, stdin: str | None = None
) -> None:
    result = runner.invoke(app, args, input=stdin)
    actual = {
        "exit_code": result.exit_code,
        "stdout": normalise(result.stdout, tmp_path),
        "stderr": normalise(result.stderr, tmp_path),
        "rows": _rows(tmp_path),
    }
    _GOLDENS.check(scenario, actual)


def _pair(tmp_path: Path, dim: int = 0) -> None:
    _seed_decision_and_vector(tmp_path, "decisions/a", dim, title="Adopt A")
    _seed_decision_and_vector(tmp_path, "decisions/b", dim, title="Adopt B")


def test_missing_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _run("missing_workspace", tmp_path, ["revisions"])


def test_malformed_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "openkos.yaml").write_text("model: [unclosed\n", encoding="utf-8")
    _run("malformed_config", tmp_path, ["revisions"])


def test_no_decisions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _ScriptedLLM())
    _run("no_decisions", tmp_path, ["revisions"])


def test_vector_store_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "bundle" / "decisions").mkdir(parents=True)
    (tmp_path / "bundle" / "decisions" / "a.md").write_text(
        "---\ntype: Decision\ntitle: A\nsensitivity: private\n---\nBody.\n",
        encoding="utf-8",
    )
    _patch_llm(monkeypatch, _ScriptedLLM())
    _run("vector_store_absent", tmp_path, ["revisions"])


def test_vector_store_model_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _seed_decision_and_vector(tmp_path, "decisions/a", 0, model="other-model")
    _patch_llm(monkeypatch, _ScriptedLLM())
    _run("vector_store_model_mismatch", tmp_path, ["revisions"])


def test_no_candidate_pairs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _seed_decision_and_vector(tmp_path, "decisions/a", 0)
    _seed_decision_and_vector(tmp_path, "decisions/b", 1)
    _patch_llm(monkeypatch, _ScriptedLLM())
    _run("no_candidate_pairs", tmp_path, ["revisions"])


def test_auto_run_reverses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    disable_local_exemption(tmp_path)
    _pair(tmp_path)
    _patch_llm(
        monkeypatch, _ScriptedLLM([_verdict_reply(verdict="reverses", confidence=0.9)])
    )
    _run("auto_run_reverses", tmp_path, ["revisions", "--auto"])


def test_auto_run_unrelated_hidden_then_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    disable_local_exemption(tmp_path)
    _pair(tmp_path)
    _patch_llm(monkeypatch, _ScriptedLLM([_verdict_reply()]))
    _run("auto_run_unrelated_hidden", tmp_path, ["revisions", "--auto"])
    _patch_llm(monkeypatch, _ScriptedLLM([_verdict_reply()]))
    _run(
        "fresh_all_unrelated_shown",
        tmp_path,
        ["revisions", "--auto", "--fresh", "--all"],
    )


def test_served_second_run_zero_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    disable_local_exemption(tmp_path)
    _pair(tmp_path)
    _record_current_finding(tmp_path, ("decisions/a", "decisions/b"))
    _patch_llm(monkeypatch, _ScriptedLLM())
    _run("served_no_gate", tmp_path, ["revisions", "--all"])


def test_partial_served_gate_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    disable_local_exemption(tmp_path)
    for name in ("a", "b", "c"):
        _seed_decision_and_vector(tmp_path, f"decisions/{name}", 0)
    _record_current_finding(tmp_path, ("decisions/a", "decisions/b"))
    _patch_llm(monkeypatch, _ScriptedLLM([_verdict_reply(), _verdict_reply()]))
    _run("partial_served_gate_counts", tmp_path, ["revisions", "--auto", "--all"])


def test_truncation_notice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _pair(tmp_path)
    _patch_llm(monkeypatch, _ScriptedLLM([_verdict_reply()]))
    monkeypatch.setattr(
        "openkos.cli.main.revision_truncation_notice", lambda plan: "FAKE NOTICE"
    )
    _run("truncation_notice", tmp_path, ["revisions", "--auto"])


def test_non_tty_without_auto_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _pair(tmp_path)
    fake = _patch_llm(monkeypatch, _ScriptedLLM([_verdict_reply()]))
    _run("non_tty_refuses", tmp_path, ["revisions"])
    assert fake.calls == []


def test_tty_decline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _pair(tmp_path)
    fake = _patch_llm(monkeypatch, _ScriptedLLM([_verdict_reply()]))
    _simulate_tty(monkeypatch)
    _run("tty_decline", tmp_path, ["revisions"], stdin="n\n")
    assert fake.calls == []


def test_tty_confirm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    disable_local_exemption(tmp_path)
    _pair(tmp_path)
    _patch_llm(
        monkeypatch, _ScriptedLLM([_verdict_reply(verdict="refines", confidence=0.8)])
    )
    _simulate_tty(monkeypatch)
    _run("tty_confirm", tmp_path, ["revisions"], stdin="y\n")


@pytest.mark.parametrize(
    ("scenario", "error"),
    [
        ("partial_generic", OllamaError("boom")),
        ("partial_unavailable", OllamaUnavailable("Ollama not reachable at x")),
        ("partial_model_not_found", OllamaModelNotFound("nope")),
    ],
)
def test_partial_batch(
    scenario: str,
    error: OllamaError,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    disable_local_exemption(tmp_path)
    _pair(tmp_path, 0)
    _seed_decision_and_vector(tmp_path, "decisions/c", 1)
    _seed_decision_and_vector(tmp_path, "decisions/d", 1)
    _patch_llm(
        monkeypatch,
        _RaisingLLM(
            [_verdict_reply(verdict="reverses", confidence=0.9)],
            error=error,
            error_at=2,
        ),
    )
    _run(scenario, tmp_path, ["revisions", "--auto"])


def test_first_call_fails_before_any_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _pair(tmp_path)
    _patch_llm(monkeypatch, _RaisingLLM([], error=OllamaError("down"), error_at=1))
    _run("first_call_fails", tmp_path, ["revisions", "--auto"])


def test_include_confidential(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    disable_local_exemption(tmp_path)
    _seed_decision_and_vector(
        tmp_path,
        "decisions/confidential",
        0,
        sensitivity="confidential",
        body="Confidential plan.",
    )
    _seed_decision_and_vector(tmp_path, "decisions/open", 0)
    _patch_llm(monkeypatch, _ScriptedLLM())
    _run("confidential_excluded", tmp_path, ["revisions", "--auto"])
    _patch_llm(monkeypatch, _ScriptedLLM([_verdict_reply(verdict="reaffirms")]))
    _run(
        "confidential_included",
        tmp_path,
        ["revisions", "--auto", "--include-confidential", "--all"],
    )
