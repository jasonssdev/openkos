"""Byte-identity characterization tests for `openkos duplicates` (issue #1168,
findings slice), the `test_reindex_characterization.py` pattern.

The verb's orchestration moved into `application/duplicates_service.py`; the
CLI kept rendering, the exit-code mapping and the auto-commit. The existing
`test_duplicates.py` assertions pin individual substrings; this file
additionally pins the COMPLETE `stdout` + `stderr` + exit-code stream of a
scenario matrix, plus the `bundle/.state` decision sidecars each path leaves
behind and the auto-commit's subject and files, against goldens recorded on
the tree BEFORE the move -- so the move cannot introduce or drop a stray
byte.

The workspace root and every decision timestamp are scrubbed (they differ per
run); everything else is compared verbatim. Every scenario whose verb
auto-commits pins the git identity, so the complete stderr does not depend on
the machine.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest

from openkos.cli.main import app
from openkos.resolution.candidates import CandidateGroup, CandidateGroupReport, Tier
from openkos.vcs import git as vcs_git
from tests.unit.cli.conftest import corrupt_identity_sidecar
from tests.unit.cli.test_duplicates import (
    _init_workspace,
    _two_event_group,
    _write_doc,
    runner,
)

_GOLDENS_PATH = (
    Path(__file__).parent / "fixtures" / "duplicates_characterization_goldens.json"
)
_GOLDENS: dict[str, dict[str, Any]] = json.loads(
    _GOLDENS_PATH.read_text(encoding="utf-8")
)

_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T[\d:.]+\+00:00")


def _scrub(text: str, root: Path) -> str:
    for spelling in {str(root.resolve()), str(root)}:
        text = text.replace(spelling, "<ROOT>")
    return _TIMESTAMP.sub("<TS>", text)


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


def _run(scenario: str, root: Path, args: list[str]) -> None:
    result = runner.invoke(app, args)
    actual: dict[str, Any] = {
        "exit_code": result.exit_code,
        "stdout": _scrub(result.stdout, root),
        "stderr": _scrub(result.stderr, root),
        "state_files": _state_files(root),
        "last_commit": _last_commit(root),
    }
    expected = _GOLDENS[scenario]
    for key in actual:
        assert actual[key] == expected[key], (scenario, key)
    assert set(expected) == set(actual), scenario


def _report(monkeypatch: pytest.MonkeyPatch, report: CandidateGroupReport) -> None:
    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", lambda *a, **k: report
    )


def _keep(*members: str) -> list[str]:
    args = ["duplicates"]
    for member in members:
        args += ["--keep-distinct", member]
    return args


def _rule(*members: str) -> None:
    result = runner.invoke(app, _keep(*members))
    assert result.exit_code == 0, result.stderr


def _group(*members: str, okf_type: str = "Event", trigger: str = "afg eval") -> Any:
    return CandidateGroup(
        okf_type=okf_type, member_ids=members, tier=Tier.HIGH, trigger=trigger
    )


# -- refusals ---------------------------------------------------------------


def test_missing_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _run("missing_workspace", tmp_path, ["duplicates"])


@pytest.mark.parametrize(
    ("scenario", "args"),
    [
        ("missing_workspace_keep_distinct", _keep("a/x", "a/y")),
        (
            "missing_workspace_reopen",
            ["duplicates", "--reopen", "a/x", "--reopen", "a/y"],
        ),
        ("missing_workspace_kept_distinct", ["duplicates", "--kept-distinct"]),
    ],
)
def test_missing_workspace_decision_verbs(
    scenario: str, args: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _run(scenario, tmp_path, args)


@pytest.mark.parametrize(
    ("scenario", "members"),
    [
        ("members_one", ("events/afg-eval",)),
        ("members_repeated", ("events/afg-eval", "events/afg-eval")),
        ("members_traversal", ("../../../pwned", "events/afg-eval")),
        ("members_absolute", ("/etc/passwd", "events/afg-eval")),
        ("members_blank_ignored", ("  ", "events/afg-eval")),
    ],
)
def test_keep_distinct_member_refusals(
    scenario: str,
    members: tuple[str, ...],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pinned_git_identity: None,
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _run(scenario, tmp_path, _keep(*members))


def test_reopen_member_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned_git_identity: None
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _run(
        "reopen_members_one",
        tmp_path,
        ["duplicates", "--reopen", "events/afg-eval"],
    )


def test_reopen_traversal_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned_git_identity: None
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _run(
        "reopen_members_traversal",
        tmp_path,
        ["duplicates", "--reopen", "../x", "--reopen", "events/a"],
    )


# -- the report --------------------------------------------------------------


def test_fresh_bundle_no_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _run("fresh_bundle", tmp_path, ["duplicates"])


def test_real_high_group(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Café Society")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="cafe   society")
    _run("real_high_group", tmp_path, ["duplicates"])


def test_real_low_group(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Stoicism")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Stoic Philosophy")
    _run("real_low_group", tmp_path, ["duplicates"])


def test_real_mixed_tiers_across_types(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Café Society")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="cafe society")
    _write_doc(tmp_path / "bundle" / "concepts" / "c.md", title="Stoicism")
    _write_doc(tmp_path / "bundle" / "concepts" / "d.md", title="Stoic Philosophy")
    _write_doc(
        tmp_path / "bundle" / "people" / "p.md", doc_type="Person", title="Ada Byron"
    )
    _write_doc(
        tmp_path / "bundle" / "people" / "q.md", doc_type="Person", title="ada  byron"
    )
    _write_doc(
        tmp_path / "bundle" / "concepts" / "mcp.md", title="Model Context Protocol"
    )
    _write_doc(tmp_path / "bundle" / "concepts" / "mcp2.md", title="MCP")
    _run("real_mixed_tiers", tmp_path, ["duplicates"])


def test_patched_tiers_and_tally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    groups = (
        CandidateGroup(
            okf_type="Concept", member_ids=("a", "b"), tier=Tier.HIGH, trigger="h"
        ),
        CandidateGroup(
            okf_type="Concept", member_ids=("c", "d"), tier=Tier.ACRONYM, trigger="ac"
        ),
        CandidateGroup(
            okf_type="Concept",
            member_ids=("e", "f", "g"),
            tier=Tier.LOW,
            trigger="0.875",
        ),
    )
    _report(monkeypatch, CandidateGroupReport(groups=groups, produced=3, retained=3))
    _run("patched_tiers_and_tally", tmp_path, ["duplicates"])


def test_truncation_notice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    group = CandidateGroup(
        okf_type="Concept", member_ids=("a", "b"), tier=Tier.HIGH, trigger="stub"
    )
    _report(
        monkeypatch,
        CandidateGroupReport(groups=(group,) * 50, produced=80, retained=50),
    )
    _run("truncation_notice", tmp_path, ["duplicates"])


def test_include_deprecated_default_and_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Stoicism")
    (tmp_path / "bundle" / "concepts" / "b.md").write_text(
        "---\ntype: Concept\ntitle: STOICISM\nstatus: deprecated\n---\n# STOICISM\n",
        encoding="utf-8",
    )
    _run("deprecated_default_excluded", tmp_path, ["duplicates"])
    _run("deprecated_included", tmp_path, ["duplicates", "--include-deprecated"])


# -- keep-distinct / reopen / kept-distinct ---------------------------------


def test_keep_distinct_writes_and_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned_git_identity: None
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _run("keep_distinct", tmp_path, _keep("events/afg-eval-2", "events/afg-eval"))


def test_keep_distinct_dedupes_and_canonicalizes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned_git_identity: None
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _run(
        "keep_distinct_canonicalized",
        tmp_path,
        _keep(" ./events//afg-eval ", "events/afg-eval", "events/zzz", "events/aaa"),
    )


def test_keep_distinct_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned_git_identity: None
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _rule("events/afg-eval", "events/afg-eval-2")
    _run("keep_distinct_twice", tmp_path, _keep("events/afg-eval", "events/afg-eval-2"))


def test_reopen_after_keep_distinct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned_git_identity: None
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _rule("events/afg-eval", "events/afg-eval-2")
    _run(
        "reopen",
        tmp_path,
        ["duplicates", "--reopen", "events/afg-eval-2", "--reopen", "events/afg-eval"],
    )


def test_reopen_with_no_prior_ruling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned_git_identity: None
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _run(
        "reopen_no_prior",
        tmp_path,
        ["duplicates", "--reopen", "events/x", "--reopen", "events/y"],
    )


def test_keep_distinct_wins_over_reopen_and_kept_distinct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned_git_identity: None
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _run(
        "keep_distinct_precedence",
        tmp_path,
        [
            *_keep("events/a", "events/b"),
            "--reopen",
            "events/c",
            "--reopen",
            "events/d",
            "--kept-distinct",
        ],
    )


def test_reopen_wins_over_kept_distinct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned_git_identity: None
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _run(
        "reopen_precedence",
        tmp_path,
        [
            "duplicates",
            "--reopen",
            "events/c",
            "--reopen",
            "events/d",
            "--kept-distinct",
        ],
    )


def test_ruled_group_is_hidden_and_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned_git_identity: None
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _report(monkeypatch, _two_event_group())
    _rule("events/afg-eval", "events/afg-eval-2")
    _run("ruled_group_hidden", tmp_path, ["duplicates"])


def test_ruled_group_hidden_beside_a_visible_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned_git_identity: None
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    groups = (
        _group("events/afg-eval", "events/afg-eval-2"),
        _group("events/other", "events/other-2", trigger="other"),
        _group("events/third", "events/third-2", trigger="third"),
    )
    _report(monkeypatch, CandidateGroupReport(groups=groups, produced=3, retained=3))
    _rule("events/afg-eval", "events/afg-eval-2")
    _rule("events/third", "events/third-2")
    _run("ruled_groups_hidden_beside_visible", tmp_path, ["duplicates"])


def test_reopened_group_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned_git_identity: None
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _report(monkeypatch, _two_event_group())
    _rule("events/afg-eval", "events/afg-eval-2")
    reopened = runner.invoke(
        app,
        ["duplicates", "--reopen", "events/afg-eval", "--reopen", "events/afg-eval-2"],
    )
    assert reopened.exit_code == 0, reopened.stderr
    _run("reopened_group_returns", tmp_path, ["duplicates"])


def test_malformed_row_warning_said_once_across_groups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned_git_identity: None
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    groups = (
        _group("events/afg-eval", "events/afg-eval-2"),
        _group("events/afg-eval", "events/afg-eval-3", trigger="second"),
        _group("events/afg-eval", "events/afg-eval-4", trigger="third"),
    )
    _report(monkeypatch, CandidateGroupReport(groups=groups, produced=3, retained=3))
    _rule("events/afg-eval", "events/afg-eval-2")
    corrupt_identity_sidecar(tmp_path / "bundle", "events/afg-eval")
    _run("malformed_row_warning_once", tmp_path, ["duplicates"])


def test_kept_distinct_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _run("kept_distinct_empty", tmp_path, ["duplicates", "--kept-distinct"])


def test_kept_distinct_populated_sorted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned_git_identity: None
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _rule("events/zulu", "events/yankee")
    _rule("events/afg-eval", "events/afg-eval-2")
    _rule("concepts/m", "concepts/n", "concepts/o")
    _rule("events/reopened-a", "events/reopened-b")
    reopened = runner.invoke(
        app,
        [
            "duplicates",
            "--reopen",
            "events/reopened-a",
            "--reopen",
            "events/reopened-b",
        ],
    )
    assert reopened.exit_code == 0, reopened.stderr
    _run("kept_distinct_populated", tmp_path, ["duplicates", "--kept-distinct"])


def test_kept_distinct_malformed_row_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned_git_identity: None
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _rule("events/afg-eval", "events/afg-eval-2")
    corrupt_identity_sidecar(tmp_path / "bundle", "events/afg-eval")
    _run("kept_distinct_malformed_row", tmp_path, ["duplicates", "--kept-distinct"])


def test_keep_distinct_over_a_malformed_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned_git_identity: None
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _rule("events/afg-eval", "events/afg-eval-2")
    corrupt_identity_sidecar(tmp_path / "bundle", "events/afg-eval")
    _run(
        "keep_distinct_over_malformed_row",
        tmp_path,
        _keep("events/afg-eval", "events/afg-eval-3"),
    )
