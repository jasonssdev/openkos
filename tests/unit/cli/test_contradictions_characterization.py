"""Byte-identity characterization tests for `openkos contradictions` (issue
#1168, findings slice), the `test_reindex_characterization.py` pattern.

The verb's orchestration moved into `application/contradictions_service.py`;
the CLI kept rendering, the exit-code mapping, the TTY progress hook and the
auto-commit. The existing `test_contradictions.py` assertions pin individual
substrings; this file additionally pins the COMPLETE `stdout` + `stderr` +
exit-code stream of a scenario matrix, plus the persisted state each path
leaves behind (the `findings.db` rows and the `bundle/.state` decision
sidecars, and the auto-commit's subject and files), against goldens recorded
on the tree BEFORE the move -- so the move cannot introduce or drop a stray
byte.

The workspace root and every decision timestamp are scrubbed (they differ per
run); everything else is compared verbatim. Every scenario whose verb
auto-commits pins the git identity, so the complete stderr does not depend on
the machine.
"""

import json
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from openkos.cli import main as cli_main
from openkos.cli.main import app
from openkos.llm.ollama import OllamaError, OllamaModelNotFound, OllamaUnavailable
from openkos.resolution.contradiction import (
    CandidatePlan,
    ContradictionBatch,
    Verdict,
    _CandidateSpec,
)
from openkos.state import derived, findings
from openkos.vcs import git as vcs_git
from tests.unit.cli.test_contradictions import (
    _break_os_walk,
    _CountingOllamaClient,
    _current_pair_digests,
    _found,
    _init_workspace,
    _persist_pair_finding,
    _two_candidate_partial_batch,
    _vacuous_plan,
    _verdict,
    _write_related_pair,
    _write_relation_doc,
    runner,
)

_GOLDENS_PATH = (
    Path(__file__).parent / "fixtures" / "contradictions_characterization_goldens.json"
)
_GOLDENS: dict[str, dict[str, Any]] = json.loads(
    _GOLDENS_PATH.read_text(encoding="utf-8")
)

_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T[\d:.]+\+00:00")


def _scrub(text: str, root: Path) -> str:
    for spelling in {str(root.resolve()), str(root)}:
        text = text.replace(spelling, "<ROOT>")
    return _TIMESTAMP.sub("<TS>", text)


def _findings_rows(root: Path) -> list[dict[str, Any]] | None:
    path = root / ".openkos" / "findings.db"
    if not path.exists():
        return None
    try:
        conn = derived.open_derived_connection(path)
        try:
            persisted = findings.open_findings(conn)
        finally:
            conn.close()
    except (OSError, sqlite3.Error) as exc:  # a deliberately corrupt store
        return [{"unreadable": type(exc).__name__}]
    return [
        {
            "pair_ids": list(pf.pair_ids),
            "merged_absorbed_id": pf.merged_absorbed_id,
            "verdict": pf.verdict,
            "confidence": pf.confidence,
            "rationale": pf.rationale,
            "conflicting_claims": list(pf.conflicting_claims),
            "input_digests": [[d.input_ref, d.digest] for d in pf.input_digests],
        }
        for pf in persisted
    ]


def _state_files(root: Path) -> dict[str, str]:
    state = root / "bundle" / ".state"
    if not state.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): _scrub(
            path.read_text(encoding="utf-8"), root
        )
        for path in sorted(state.rglob("*"))
        if path.is_file()
    }


def _last_commit(root: Path) -> dict[str, Any] | None:
    subject = vcs_git._run(["git", "log", "-1", "--format=%s"], cwd=root)
    files = vcs_git._run(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"], cwd=root
    )
    if subject.returncode != 0:
        return None
    return {
        "subject": subject.stdout.strip(),
        "files": sorted(line for line in files.stdout.splitlines() if line),
    }


def _run(
    scenario: str, root: Path, args: list[str], *, with_commit: bool = False
) -> None:
    result = runner.invoke(app, args)
    actual: dict[str, Any] = {
        "exit_code": result.exit_code,
        "stdout": _scrub(result.stdout, root),
        "stderr": _scrub(result.stderr, root),
        "findings_rows": _findings_rows(root),
        "state_files": _state_files(root),
    }
    if with_commit:
        actual["last_commit"] = _last_commit(root)
    expected = _GOLDENS[scenario]
    for key in actual:
        assert actual[key] == expected[key], (scenario, key)
    assert set(expected) == set(actual), scenario


def _fake_client(monkeypatch: pytest.MonkeyPatch) -> None:
    _CountingOllamaClient.calls = []
    monkeypatch.setattr("openkos.cli.main.OllamaClient", _CountingOllamaClient)


def _find_raising(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    def _raise(*args: object, **kwargs: object) -> None:
        raise exc

    monkeypatch.setattr("openkos.cli.main.find_contradictions", _raise)


def _decline(*pair: str) -> None:
    result = runner.invoke(app, ["contradictions", "--decline", *pair])
    assert result.exit_code == 0, result.stderr


# -- refusals ---------------------------------------------------------------


def test_missing_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _run("missing_workspace", tmp_path, ["contradictions"])


def test_missing_workspace_declined_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _run("missing_workspace_declined", tmp_path, ["contradictions", "--declined"])


def test_missing_workspace_decline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _run(
        "missing_workspace_decline",
        tmp_path,
        ["contradictions", "--decline", "concepts/a", "concepts/b"],
    )


def test_malformed_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "openkos.yaml").write_text("model: [unclosed\n", encoding="utf-8")
    _run("malformed_config", tmp_path, ["contradictions"])


@pytest.mark.parametrize(
    ("scenario", "exc"),
    [
        ("raise_unavailable", OllamaUnavailable("connection refused")),
        ("raise_model_not_found", OllamaModelNotFound("no such model")),
        ("raise_generic", OllamaError("boom")),
    ],
)
def test_raise_path_ladder(
    scenario: str, exc: Exception, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _find_raising(monkeypatch, exc)
    _run(scenario, tmp_path, ["contradictions"])


# -- real pipeline: judging, serving, persisting ----------------------------


def test_judged_default_view(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_related_pair(tmp_path)
    seed_vectors_db(tmp_path)
    _fake_client(monkeypatch)
    _run("judged_default_view", tmp_path, ["contradictions"])


def test_judged_all_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_related_pair(tmp_path)
    seed_vectors_db(tmp_path)
    _fake_client(monkeypatch)
    _run("judged_all_flag", tmp_path, ["contradictions", "--all"])


def test_served_from_persisted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_related_pair(tmp_path)
    seed_vectors_db(tmp_path)
    _persist_pair_finding(tmp_path, input_digests=_current_pair_digests(tmp_path))
    _fake_client(monkeypatch)
    _run("served_from_persisted", tmp_path, ["contradictions"])
    assert _CountingOllamaClient.calls == []


def test_served_consistent_hidden(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_related_pair(tmp_path)
    seed_vectors_db(tmp_path)
    _persist_pair_finding(
        tmp_path,
        verdict="consistent",
        input_digests=_current_pair_digests(tmp_path),
    )
    _fake_client(monkeypatch)
    _run("served_consistent_hidden", tmp_path, ["contradictions"])


def test_served_consistent_all_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_related_pair(tmp_path)
    seed_vectors_db(tmp_path)
    _persist_pair_finding(
        tmp_path,
        verdict="consistent",
        confidence=0.4,
        input_digests=_current_pair_digests(tmp_path),
    )
    _fake_client(monkeypatch)
    _run("served_consistent_all_flag", tmp_path, ["contradictions", "--all"])


def test_stale_pair_rejudged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_related_pair(tmp_path)
    seed_vectors_db(tmp_path)
    _persist_pair_finding(
        tmp_path,
        input_digests=(findings.InputDigest("concepts/a", "0" * 64),),
    )
    _fake_client(monkeypatch)
    _run("stale_pair_rejudged", tmp_path, ["contradictions"])


def test_fresh_flag_rejudges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_related_pair(tmp_path)
    seed_vectors_db(tmp_path)
    _persist_pair_finding(tmp_path, input_digests=_current_pair_digests(tmp_path))
    _fake_client(monkeypatch)
    _run("fresh_flag_rejudges", tmp_path, ["contradictions", "--fresh"])


def test_unrecognized_stored_verdict_rejudged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_related_pair(tmp_path)
    seed_vectors_db(tmp_path)
    _persist_pair_finding(
        tmp_path,
        verdict="mystery",
        input_digests=_current_pair_digests(tmp_path),
    )
    _fake_client(monkeypatch)
    _run("unrecognized_stored_verdict", tmp_path, ["contradictions"])


def test_corrupt_findings_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_related_pair(tmp_path)
    seed_vectors_db(tmp_path)
    (tmp_path / ".openkos").mkdir(exist_ok=True)
    (tmp_path / ".openkos" / "findings.db").write_bytes(b"this is not sqlite" * 50)
    _fake_client(monkeypatch)
    _run("corrupt_findings_db", tmp_path, ["contradictions"])


def test_persist_failure_degrades(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    from openkos.cli import curate as curate_module

    _init_workspace(tmp_path, monkeypatch)
    _write_related_pair(tmp_path)
    seed_vectors_db(tmp_path)
    _fake_client(monkeypatch)

    def _broken(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(curate_module, "persist_findings", _broken)
    _run("persist_failure_degrades", tmp_path, ["contradictions"])


def test_declined_verdict_hidden(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
    pinned_git_identity: None,
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_related_pair(tmp_path)
    seed_vectors_db(tmp_path)
    _decline("concepts/a", "concepts/b")
    _fake_client(monkeypatch)
    _run("declined_verdict_hidden", tmp_path, ["contradictions", "--all"])


def _write_confidential_deprecated_pair(tmp_path: Path) -> None:
    _write_relation_doc(
        tmp_path / "bundle" / "concepts" / "a.md",
        title="Alpha",
        sensitivity_value="confidential",
        relations=[("concepts/b", "related_to")],
    )
    _write_relation_doc(
        tmp_path / "bundle" / "concepts" / "b.md", title="Beta", status="deprecated"
    )


def test_include_flags_restore_confidential_and_deprecated_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_confidential_deprecated_pair(tmp_path)
    seed_vectors_db(tmp_path)
    _fake_client(monkeypatch)
    _run(
        "include_flags_restore_pair",
        tmp_path,
        [
            "contradictions",
            "--include-confidential",
            "--include-deprecated",
            "--all",
        ],
    )


def test_default_excludes_confidential_and_deprecated_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_confidential_deprecated_pair(tmp_path)
    seed_vectors_db(tmp_path)
    _fake_client(monkeypatch)
    _run("default_excludes_pair", tmp_path, ["contradictions"])


def test_incomplete_walk_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_related_pair(tmp_path)
    seed_vectors_db(tmp_path)
    _fake_client(monkeypatch)
    _break_os_walk(monkeypatch)
    _run("incomplete_walk_warning", tmp_path, ["contradictions"])


# -- zero-candidate state messages -------------------------------------------


def test_zero_state_embeddings_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _fake_client(monkeypatch)
    _run("zero_state_embeddings_missing", tmp_path, ["contradictions"])


def test_zero_state_no_typed_edges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _fake_client(monkeypatch)
    _run("zero_state_no_typed_edges", tmp_path, ["contradictions"])


def test_zero_state_typed_edges_none_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_relation_doc(
        tmp_path / "bundle" / "concepts" / "a.md",
        title="Alpha",
        relations=[("concepts/b", "derived_from")],
    )
    _write_relation_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Beta")
    seed_vectors_db(tmp_path)
    _fake_client(monkeypatch)
    _run("zero_state_typed_edges_none", tmp_path, ["contradictions"])


def test_candidate_edge_truncation_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openkos.graph.proximity import ProximityPair

    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "bundle" / "concepts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "bundle" / "concepts" / "hub.md").write_text(
        "---\ntype: Concept\ntitle: Hub\n---\nBody.\n", encoding="utf-8"
    )
    pairs = []
    for index in range(1, 61):
        leaf_id = f"leaf{index:03d}"
        (tmp_path / "bundle" / "concepts" / f"{leaf_id}.md").write_text(
            f"---\ntype: Concept\ntitle: {leaf_id}\n---\nBody.\n", encoding="utf-8"
        )
        pairs.append(
            ProximityPair(
                source_id="concepts/hub",
                target_id=f"concepts/{leaf_id}",
                distance=index * 0.001,
            )
        )

    class _StubSource:
        def pairs(self, concept_ids: object) -> list[ProximityPair]:
            return pairs

        def close(self) -> None:
            return None

    monkeypatch.setattr(cli_main, "_open_proximity_or_degrade", lambda p: _StubSource())
    monkeypatch.setattr(cli_main, "find_contradictions", lambda *a, **k: _found([], 0))
    _run("candidate_edge_truncation", tmp_path, ["contradictions"])


def test_quarantine_notice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from openkos.graph.proximity import ProximityPair

    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "bundle" / "sources").mkdir(parents=True, exist_ok=True)
    (tmp_path / "bundle" / "sources" / "s.md").write_text(
        "---\ntype: Source\ntitle: s\nresource: raw/s.txt\n"
        "extraction_notice: judge-selection-empty\n---\nBody.\n",
        encoding="utf-8",
    )
    (tmp_path / "bundle" / "concepts").mkdir(parents=True, exist_ok=True)
    for slug, title in (("a", "A"), ("b", "B")):
        (tmp_path / "bundle" / "concepts" / f"{slug}.md").write_text(
            f"---\ntype: Concept\ntitle: {title}\nprovenance:\n"
            "  - sources/s\n---\nBody.\n",
            encoding="utf-8",
        )

    class _StubSource:
        def pairs(self, concept_ids: object) -> list[ProximityPair]:
            return [
                ProximityPair(
                    source_id="concepts/a", target_id="concepts/b", distance=0.1
                )
            ]

        def close(self) -> None:
            return None

    monkeypatch.setattr(cli_main, "_open_proximity_or_degrade", lambda p: _StubSource())
    monkeypatch.setattr(cli_main, "find_contradictions", lambda *a, **k: _found([], 0))
    _run("quarantine_notice", tmp_path, ["contradictions"])


# -- patched plan / batch shapes ---------------------------------------------


def test_vacuous_plan_no_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    plan = _vacuous_plan(2)
    monkeypatch.setattr("openkos.cli.main.plan_candidates", lambda *a, **k: plan)
    monkeypatch.setattr(
        "openkos.cli.main.find_contradictions", lambda *a, **k: _found([], 2)
    )
    _run("vacuous_plan_no_findings", tmp_path, ["contradictions"])


def test_vacuous_plan_with_merged_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    plan = _vacuous_plan(1)
    monkeypatch.setattr("openkos.cli.main.plan_candidates", lambda *a, **k: plan)
    monkeypatch.setattr(
        "openkos.cli.main.find_contradictions",
        lambda *a, **k: _found(
            [
                _verdict(
                    source="concepts/survivor0",
                    target="concepts/survivor0",
                    merged_absorbed_id="concepts/absorbed0",
                    confidence=0.91,
                    rationale="merged body disagrees",
                    conflicting_claims=("claim m1",),
                )
            ],
            1,
        ),
    )
    _run("vacuous_plan_with_merged_verdict", tmp_path, ["contradictions"])


def test_truncated_plan_uncertain_hidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    plan = CandidatePlan(
        specs=(
            _CandidateSpec(
                pair_ids=("concepts/a", "concepts/b"), relation_type="related_to"
            ),
        ),
        edge_total=201,
        merged_total=50,
    )
    monkeypatch.setattr("openkos.cli.main.plan_candidates", lambda *a, **k: plan)
    monkeypatch.setattr(
        "openkos.cli.main.find_contradictions",
        lambda *a, **k: _found(
            [_verdict(verdict=Verdict.UNCERTAIN, confidence=0.3, rationale="unsure")],
            1,
        ),
    )
    _run("truncated_plan_uncertain_hidden", tmp_path, ["contradictions"])


@pytest.mark.parametrize(
    ("scenario", "failure"),
    [
        ("partial_unavailable", OllamaUnavailable("connection refused")),
        ("partial_model_not_found", OllamaModelNotFound("no such model")),
        ("partial_generic", OllamaError("boom")),
    ],
)
def test_partial_batch(
    scenario: str,
    failure: OllamaError,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _two_candidate_partial_batch(monkeypatch, failure)
    _run(scenario, tmp_path, ["contradictions"])


def test_first_candidate_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    plan = CandidatePlan(
        specs=(
            _CandidateSpec(
                pair_ids=("concepts/a", "concepts/b"), relation_type="related_to"
            ),
        ),
        edge_total=1,
        merged_total=0,
    )
    monkeypatch.setattr("openkos.cli.main.plan_candidates", lambda *a, **k: plan)
    monkeypatch.setattr(
        "openkos.cli.main.find_contradictions",
        lambda *a, **k: (
            ContradictionBatch(
                results=[], failure=OllamaUnavailable("down"), failed_index=1
            ),
            1,
        ),
    )
    _run("first_candidate_failure", tmp_path, ["contradictions"])


# -- decline / reopen / declined view ----------------------------------------


def test_decline_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned_git_identity: None
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _run(
        "decline_typed",
        tmp_path,
        ["contradictions", "--decline", "concepts/b", "concepts/a"],
        with_commit=True,
    )


def test_decline_merged_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned_git_identity: None
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _run(
        "decline_merged_body",
        tmp_path,
        [
            "contradictions",
            "--decline",
            "concepts/a",
            "concepts/a",
            "--merged-absorbed-id",
            "concepts/z",
        ],
        with_commit=True,
    )


def test_decline_twice_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned_git_identity: None
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _decline("concepts/a", "concepts/b")
    _run(
        "decline_twice",
        tmp_path,
        ["contradictions", "--decline", "concepts/a", "concepts/b"],
        with_commit=True,
    )


def test_reopen_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned_git_identity: None
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _decline("concepts/a", "concepts/b")
    _run(
        "reopen_typed",
        tmp_path,
        ["contradictions", "--reopen", "concepts/b", "concepts/a"],
        with_commit=True,
    )


def test_reopen_merged_body_with_no_prior_decline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned_git_identity: None
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _run(
        "reopen_merged_body",
        tmp_path,
        [
            "contradictions",
            "--reopen",
            "concepts/a",
            "concepts/a",
            "--merged-absorbed-id",
            "concepts/z",
        ],
        with_commit=True,
    )


def test_declined_view_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _run("declined_view_empty", tmp_path, ["contradictions", "--declined"])


def test_declined_view_populated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned_git_identity: None
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_related_pair(tmp_path)
    _decline("concepts/a", "concepts/b")
    _decline("concepts/c", "concepts/d")
    result = runner.invoke(
        app,
        [
            "contradictions",
            "--decline",
            "concepts/a",
            "concepts/a",
            "--merged-absorbed-id",
            "concepts/z",
        ],
    )
    assert result.exit_code == 0, result.stderr
    _decline("concepts/e", "concepts/f")
    reopened = runner.invoke(
        app, ["contradictions", "--reopen", "concepts/e", "concepts/f"]
    )
    assert reopened.exit_code == 0, reopened.stderr
    # A fresh finding for a/b, a stale one for c/d, none for the merged one.
    _persist_pair_finding(tmp_path, input_digests=_current_pair_digests(tmp_path))
    conn = derived.open_derived_connection(tmp_path / ".openkos" / "findings.db")
    try:
        findings.record_findings(
            conn,
            [
                findings.Finding(
                    pair_ids=("concepts/c", "concepts/d"),
                    merged_absorbed_id=None,
                    verdict="uncertain",
                    confidence=0.25,
                    rationale="stale rationale",
                    conflicting_claims=(),
                    input_digests=(findings.InputDigest("concepts/a", "f" * 64),),
                )
            ],
        )
    finally:
        conn.close()
    _run("declined_view_populated", tmp_path, ["contradictions", "--declined"])
