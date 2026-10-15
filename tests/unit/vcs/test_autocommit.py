"""The print-free git commit core (`openkos.vcs.autocommit`) against REAL git
repositories: repository selection, commit scoping, and the config-edit plan.

Nothing here goes through the CLI. The core returns a `CommitOutcome` and
never prints; the CLI's own byte-identical output is pinned by
`tests/unit/cli/test_main_autocommit.py` and `test_config_commit_decision.py`.
"""

from collections.abc import Sequence
from pathlib import Path

import pytest

from openkos import config
from openkos.vcs import autocommit
from openkos.vcs import git as vcs_git
from tests.unit.vcs.conftest import _git, isolate_git_identity

_CONFIG = "backend: ollama\ntype_tiers:\n  Person: volatile\n  Concept: static\n"


def _identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    isolate_git_identity(
        monkeypatch,
        tmp_path_factory.mktemp("git-identity"),
        name="Tester",
        email="tester@example.invalid",
    )


def _repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    vcs_git.init_repo(path)
    return path


def _out(root: Path, *args: str) -> list[str]:
    result = vcs_git._run(["git", *args], cwd=root)
    assert result.returncode == 0, result.stderr
    return result.stdout.split()


def _head_files(root: Path) -> list[str]:
    return _out(root, "show", "--name-only", "--format=", "HEAD")


def _seed(root: Path, name: str = "seed.txt") -> None:
    (root / name).write_text("seed", encoding="utf-8")
    _git(["add", "--", name], root)
    _git(["commit", "-m", "seed"], root)


@pytest.fixture
def identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    _identity(monkeypatch, tmp_path_factory)


# -- Git repository selection ---------------------------------------------


def test_a_workspace_nested_in_an_outer_repository_commits_only_the_listed_paths(
    tmp_path: Path, identity: None
) -> None:
    outer = _repo(tmp_path / "outer")
    _seed(outer)
    workspace = outer / "ws"
    workspace.mkdir()
    (workspace / "a.md").write_text("a", encoding="utf-8")
    (workspace / "b.md").write_text("b", encoding="utf-8")
    (outer / "other.txt").write_text("other", encoding="utf-8")

    outcome = autocommit.commit_paths(workspace, ["a.md"], "openkos: nested")

    assert outcome.skipped is None
    assert outcome.sha == _out(outer, "rev-parse", "--short", "HEAD")[0]
    assert _head_files(outer) == ["ws/a.md"]
    assert set(_out(outer, "ls-files", "--others", "--exclude-standard")) == {
        "other.txt",
        "ws/b.md",
    }


def test_a_non_repository_is_skipped_and_gains_no_git_directory(
    tmp_path: Path, identity: None
) -> None:
    (tmp_path / "a.md").write_text("a", encoding="utf-8")

    outcome = autocommit.commit_paths(tmp_path, ["a.md"], "openkos: nowhere")

    assert outcome == autocommit.CommitOutcome(
        sha=None, skipped="not_a_repository", detail=None, includes_confidential=False
    )
    assert not (tmp_path / ".git").exists()


# -- Commit state ---------------------------------------------------------


def test_a_pre_staged_unrelated_file_is_neither_committed_nor_unstaged(
    tmp_path: Path, identity: None
) -> None:
    repo = _repo(tmp_path)
    _seed(repo)
    (repo / "mine.md").write_text("mine", encoding="utf-8")
    (repo / "staged.txt").write_text("staged", encoding="utf-8")
    (repo / "loose.txt").write_text("loose", encoding="utf-8")
    _git(["add", "--", "staged.txt"], repo)

    outcome = autocommit.commit_paths(repo, ["mine.md"], "openkos: scoped")

    assert outcome.sha is not None
    assert _head_files(repo) == ["mine.md"]
    # Only the user's own staged file is in the index: the unrelated loose
    # file was not staged on the way, which a blanket `git add -A` would do.
    assert _out(repo, "diff", "--cached", "--name-only") == ["staged.txt"]
    assert _out(repo, "ls-files", "--others", "--exclude-standard") == ["loose.txt"]


def test_an_unset_identity_is_skipped_without_a_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    repo = _repo(tmp_path)
    isolate_git_identity(monkeypatch, tmp_path_factory.mktemp("no-identity"))
    (repo / "a.md").write_text("a", encoding="utf-8")

    outcome = autocommit.commit_paths(repo, ["a.md"], "openkos: anon")

    assert outcome.skipped == "identity_unset"
    assert outcome.sha is None
    assert _out(repo, "ls-files", "--others", "--exclude-standard") == ["a.md"]


def test_a_git_failure_is_reported_as_failed_with_its_detail(
    tmp_path: Path, identity: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)

    def boom(*_args: object) -> str:
        raise vcs_git.GitError("add exploded")

    monkeypatch.setattr(vcs_git, "commit_paths", boom)

    outcome = autocommit.commit_paths(repo, ["a.md"], "openkos: boom")

    assert outcome.skipped == "failed"
    assert outcome.detail == "add exploded"
    assert outcome.sha is None


def test_an_os_error_is_reported_as_failed_too(
    tmp_path: Path, identity: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)

    def boom(*_args: object) -> str:
        raise OSError("disk gone")

    monkeypatch.setattr(vcs_git, "commit_paths", boom)

    outcome = autocommit.commit_paths(repo, ["a.md"], "openkos: boom")

    assert (outcome.skipped, outcome.detail) == ("failed", "disk gone")


def test_a_commit_whose_sha_cannot_be_read_back_is_not_a_skip(
    tmp_path: Path, identity: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    monkeypatch.setattr(vcs_git, "commit_paths", lambda *_args: None)

    outcome = autocommit.commit_paths(repo, ["a.md"], "openkos: unnamed")

    assert outcome.sha is None
    assert outcome.skipped is None


# -- Confidential disclosure ----------------------------------------------


def _concept(root: Path, rel: str, sensitivity: str | None) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: Concept"]
    if sensitivity is not None:
        lines.append(f"sensitivity: {sensitivity}")
    path.write_text("\n".join([*lines, "---", "", "Body.", ""]), encoding="utf-8")


@pytest.mark.parametrize(
    ("sensitivity", "expected"),
    [("confidential", True), ("private", False), ("public", False), (None, False)],
)
def test_confidential_is_true_only_for_an_explicit_confidential(
    tmp_path: Path, sensitivity: str | None, expected: bool
) -> None:
    _concept(tmp_path, "bundle/c.md", sensitivity)

    assert autocommit.paths_include_confidential(tmp_path, ["bundle/c.md"]) is expected


def test_the_outcome_carries_confidential_only_for_a_committed_confidential_path(
    tmp_path: Path, identity: None
) -> None:
    repo = _repo(tmp_path)
    _seed(repo)
    _concept(repo, "bundle/secret.md", "confidential")
    _concept(repo, "bundle/open.md", "public")

    secret = autocommit.commit_paths(repo, ["bundle/secret.md"], "openkos: s")
    plain = autocommit.commit_paths(repo, ["bundle/open.md"], "openkos: p")

    assert (secret.includes_confidential, plain.includes_confidential) == (True, False)


def test_the_confidential_predicate_is_injectable(
    tmp_path: Path, identity: None
) -> None:
    repo = _repo(tmp_path)
    _seed(repo)
    (repo / "a.md").write_text("a", encoding="utf-8")
    seen: list[tuple[Path, list[str]]] = []

    def probe(root: Path, paths: Sequence[str]) -> bool:
        seen.append((root, list(paths)))
        return True

    outcome = autocommit.commit_paths(
        repo, ["a.md"], "openkos: injected", has_confidential=probe
    )

    assert outcome.includes_confidential is True
    assert seen == [(repo, ["a.md"])]


# -- Config edit plan -----------------------------------------------------


def _config_repo(path: Path) -> Path:
    repo = _repo(path)
    (repo / "openkos.yaml").write_text(_CONFIG, encoding="utf-8")
    _git(["add", "--", "openkos.yaml"], repo)
    _git(["commit", "-m", "config"], repo)
    return repo


def _baseline(repo: Path) -> bytes:
    return (repo / "openkos.yaml").read_bytes()


def test_plan_config_edit_is_none_on_a_clean_file(
    tmp_path: Path, identity: None
) -> None:
    repo = _config_repo(tmp_path)

    plan = autocommit.plan_config_edit(repo, "Person", "slow", _baseline(repo))

    assert plan is None


def test_plan_config_edit_is_none_outside_a_repository(tmp_path: Path) -> None:
    (tmp_path / "openkos.yaml").write_text(_CONFIG, encoding="utf-8")

    assert autocommit.plan_config_edit(tmp_path, "Person", "slow", b"") is None


def test_plan_config_edit_plans_over_an_unrelated_dirty_edit(
    tmp_path: Path, identity: None
) -> None:
    repo = _config_repo(tmp_path)
    (repo / "openkos.yaml").write_text(_CONFIG + "extra: 1\n", encoding="utf-8")

    plan = autocommit.plan_config_edit(repo, "Person", "slow", _baseline(repo))

    assert isinstance(plan, autocommit.ConfigCommitPlan)
    assert (
        plan.apply(_CONFIG.encode())
        == config.set_type_tier(_CONFIG, "Person", "slow").encode()
    )


def test_plan_config_edit_conflicts_on_an_edit_to_the_same_entry(
    tmp_path: Path, identity: None
) -> None:
    repo = _config_repo(tmp_path)
    (repo / "openkos.yaml").write_text(
        _CONFIG.replace("Person: volatile", "Person: static"), encoding="utf-8"
    )

    plan = autocommit.plan_config_edit(repo, "Person", "slow", _baseline(repo))

    assert plan == autocommit.ConfigEditConflict(concept_type="Person")


def test_a_conflict_writes_nothing(tmp_path: Path, identity: None) -> None:
    repo = _config_repo(tmp_path)
    edited = _CONFIG.replace("Person: volatile", "Person: static")
    (repo / "openkos.yaml").write_text(edited, encoding="utf-8")
    head_before = _out(repo, "rev-parse", "HEAD")

    autocommit.plan_config_edit(repo, "Person", "slow", _baseline(repo))

    assert _out(repo, "rev-parse", "HEAD") == head_before
    assert (repo / "openkos.yaml").read_text(encoding="utf-8") == edited


def test_user_edits_to_another_entry_stay_uncommitted(
    tmp_path: Path, identity: None
) -> None:
    repo = _config_repo(tmp_path)
    yaml = repo / "openkos.yaml"
    yaml.write_text(
        _CONFIG.replace("Concept: static", "Concept: slow"), encoding="utf-8"
    )
    plan = autocommit.plan_config_edit(repo, "Person", "slow", _baseline(repo))
    assert isinstance(plan, autocommit.ConfigCommitPlan)

    outcome = autocommit.commit_config_edit(repo, "openkos: person slow", plan)

    assert outcome.sha is not None
    assert _head_files(repo) == ["openkos.yaml"]
    committed = vcs_git.head_file_bytes(repo, "openkos.yaml")
    assert committed == config.set_type_tier(_CONFIG, "Person", "slow").encode()
    assert "Concept: slow" in yaml.read_text(encoding="utf-8")
    assert vcs_git.paths_dirty(repo, ["openkos.yaml"])


# -- commit_config_edit ---------------------------------------------------


def test_reapplying_the_current_tier_is_nothing_to_commit(
    tmp_path: Path, identity: None
) -> None:
    repo = _config_repo(tmp_path)
    head_before = _out(repo, "rev-parse", "HEAD")

    outcome = autocommit.commit_config_edit(repo, "openkos: same", None)

    assert outcome.skipped == "nothing_to_commit"
    assert outcome.sha is None
    assert _out(repo, "rev-parse", "HEAD") == head_before


def test_a_plan_that_changes_nothing_is_nothing_to_commit(
    tmp_path: Path, identity: None
) -> None:
    repo = _config_repo(tmp_path)
    plan = autocommit.ConfigCommitPlan(apply=lambda content: content)

    outcome = autocommit.commit_config_edit(repo, "openkos: noop", plan)

    assert outcome.skipped == "nothing_to_commit"
    assert outcome.sha is None


def test_a_dirty_config_without_a_plan_is_committed_by_path(
    tmp_path: Path, identity: None
) -> None:
    repo = _config_repo(tmp_path)
    (repo / "openkos.yaml").write_text(
        config.set_type_tier(_CONFIG, "Person", "slow"), encoding="utf-8"
    )

    outcome = autocommit.commit_config_edit(repo, "openkos: person slow", None)

    assert outcome.sha is not None
    assert _head_files(repo) == ["openkos.yaml"]


def test_commit_config_edit_with_a_plan_and_unset_identity_is_skipped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    repo = _config_repo(tmp_path)  # inherits the host identity for the seed commit
    isolate_git_identity(monkeypatch, tmp_path_factory.mktemp("no-identity"))
    plan = autocommit.ConfigCommitPlan(apply=lambda content: content + b"#\n")

    outcome = autocommit.commit_config_edit(repo, "openkos: anon", plan)

    assert outcome.skipped == "identity_unset"


def test_commit_config_edit_with_a_plan_reports_a_git_failure(
    tmp_path: Path, identity: None
) -> None:
    repo = _config_repo(tmp_path)

    def boom(content: bytes) -> bytes:
        raise ValueError("cannot edit")

    outcome = autocommit.commit_config_edit(
        repo, "openkos: broken", autocommit.ConfigCommitPlan(apply=boom)
    )

    assert outcome.skipped == "failed"
    assert outcome.detail == "cannot edit"
