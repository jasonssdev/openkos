"""`openkos.api.preview_decision` and `apply_decision`: one decision previewed
without a write, then applied against the version the preview returned, with the
API's own local git commit.

Every test runs in a real workspace inside a real git repository whose identity
is pinned through `GIT_CONFIG_COUNT` (never `GIT_AUTHOR_*`, which does not satisfy
`git config`). The commit assertions read the values the commit carried: which
paths it held and what its subject said.
"""

import dataclasses
import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from openkos import config, lock
from openkos.api import (
    Action,
    Applied,
    DecisionPreview,
    DecisionVersion,
    KeepDistinctPlan,
    MergePlan,
    MergeStep,
    NotApplied,
    Refused,
    RelatePlan,
    Stale,
    TierPlan,
    Workspace,
    WorkspaceBusyError,
    apply_decision,
    list_decisions,
    preview_decision,
)
from openkos.application import curate_decisions, lock_wait
from openkos.state import derived
from openkos.state import pending_queue as pq
from openkos.vcs import git as vcs_git
from tests.unit.api import support
from tests.unit.application.curation_support import make_workspace, write_concept

PAIR = ("concepts/a", "concepts/b")
IDENTITY_KEY = curate_decisions.identity_decision_key(PAIR)
TIER_KEY = pq.stored_decision_key("volatility", pq.volatility_key("Person"))


def _git(root: Path, *args: str) -> str:
    result = vcs_git._run(["git", *args], cwd=root)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _head_files(root: Path) -> list[str]:
    return sorted(_git(root, "show", "--name-only", "--format=", "HEAD").split())


def _pin_identity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A fixed author, whatever the host's own git configuration says."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(tmp_path / "no-system-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "user.name")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "OpenKOS Test")
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "user.email")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", "test@openkos.invalid")


@pytest.fixture(autouse=True)
def no_client(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    support.forbid_model_clients(monkeypatch, "a preview or an apply")


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A committed workspace holding concepts a, b and c (c links to b)."""
    _pin_identity(monkeypatch, tmp_path)
    ws = make_workspace(tmp_path, monkeypatch)
    write_concept(ws, "concepts/a", title="A")
    write_concept(ws, "concepts/b", title="B")
    write_concept(ws, "concepts/c", title="C", body="See [B](/concepts/b.md).")
    vcs_git.commit_paths(ws, ["bundle"], "seed concepts")
    return ws


def _row(root: Path, decision_key: str) -> tuple[str, str]:
    layout = config.WorkspaceLayout(root)
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        status, resolution = conn.execute(
            "SELECT status, resolution FROM pending_items WHERE decision_key = ?",
            (decision_key,),
        ).fetchone()
        return status, resolution
    finally:
        conn.close()


def _relation_key(source: str, target: str) -> str:
    return pq.stored_decision_key("relation_type", pq.relation_type_key(source, target))


def _preview(root: Path, key: str, action: Action) -> DecisionPreview:
    result = preview_decision(Workspace.open(root), key, action)
    assert isinstance(result, DecisionPreview), result
    return result


# The plan's `kind` is the action it plans, except that the reversed relation is
# still a relation: the discriminator names the plan's shape, not the direction.
_PLAN_KIND_OF_ACTION = {
    Action.MERGE: "merge",
    Action.KEEP_DISTINCT: "keep_distinct",
    Action.RELATE: "relate",
    Action.RELATE_REVERSED: "relate",
    Action.SET_TIER: "set_tier",
}


def _enqueue_merge(root: Path) -> None:
    support.enqueue_identity(root, PAIR, verdict="same", fresh=True)


# -- preview ------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("enqueue", "key", "action", "plan"),
    [
        (
            _enqueue_merge,
            IDENTITY_KEY,
            Action.MERGE,
            MergePlan(
                survivor_id="concepts/a",
                absorbed_id="concepts/b",
                survivor_criterion="id order -- equal body length",
                files_rewritten=1,
                cross_source_same_pair=False,
                cross_type_concern=None,
                steps=(
                    MergeStep(
                        survivor_id="concepts/a",
                        absorbed_id="concepts/b",
                        absorbed_type="Concept",
                        type_outcome="same_type",
                        files_rewritten=1,
                    ),
                ),
                survivor_type="Concept",
            ),
        ),
        (
            lambda r: support.enqueue_identity(r, PAIR, verdict="different"),
            IDENTITY_KEY,
            Action.KEEP_DISTINCT,
            KeepDistinctPlan(member_ids=PAIR),
        ),
        (
            lambda r: support.enqueue_relation(r, "concepts/a", "concepts/c"),
            _relation_key("concepts/a", "concepts/c"),
            Action.RELATE,
            RelatePlan("concepts/a", "concepts/c", "member_of"),
        ),
        (
            lambda r: support.enqueue_relation(r, "concepts/a", "concepts/c"),
            _relation_key("concepts/a", "concepts/c"),
            Action.RELATE_REVERSED,
            RelatePlan("concepts/c", "concepts/a", "member_of"),
        ),
        (
            lambda r: support.enqueue_volatility(r, "Person", "static"),
            TIER_KEY,
            Action.SET_TIER,
            TierPlan("Person", "static"),
        ),
    ],
    ids=["merge", "keep-distinct", "relate", "relate-reversed", "set-tier"],
)
def test_preview_returns_an_api_plan_and_writes_nothing(
    root: Path,
    enqueue: Callable[[Path], None],
    key: str,
    action: Action,
    plan: MergePlan | RelatePlan | TierPlan | KeepDistinctPlan,
) -> None:
    enqueue(root)
    before = support.snapshot(root)

    result = _preview(root, key, action)

    assert result.plan == plan
    assert result.plan.kind == _PLAN_KIND_OF_ACTION[action]
    assert result.decision_key == key
    assert result.action is action
    assert isinstance(result.version, DecisionVersion)
    assert result.version.decision_key == key
    assert support.snapshot(root) == before


def test_a_preview_carries_no_prepared_merge_anywhere(root: Path) -> None:
    _enqueue_merge(root)

    result = _preview(root, IDENTITY_KEY, Action.MERGE)

    names = {f.name for f in dataclasses.fields(result.plan)}
    assert names == {
        "kind",
        "survivor_id",
        "absorbed_id",
        "survivor_criterion",
        "files_rewritten",
        "cross_source_same_pair",
        "cross_type_concern",
        "steps",
        "kept_distinct_ids",
        "survivor_type",
    }
    assert not hasattr(result.plan, "prepared")
    assert [f.name for f in dataclasses.fields(result)] == [
        "decision_key",
        "action",
        "plan",
        "version",
    ]


def test_a_key_that_never_existed_previews_as_a_returned_unknown_stale(
    root: Path,
) -> None:
    result = preview_decision(Workspace.open(root), "identity:gone", Action.MERGE)

    assert result == Stale("identity:gone", "unknown_key")


def test_a_key_that_never_existed_applies_as_a_returned_unknown_stale(
    root: Path,
) -> None:
    _enqueue_merge(root)
    version = _preview(root, IDENTITY_KEY, Action.MERGE).version
    fabricated = DecisionVersion(
        "identity:gone", version.payload_digest, version.content_digest
    )
    before = support.snapshot(root)

    outcome = apply_decision(Workspace.open(root), fabricated, Action.MERGE)

    assert outcome == Stale("identity:gone", "unknown_key")
    assert support.snapshot(root) == before


def test_an_action_the_decision_does_not_offer_is_returned_not_raised(
    root: Path,
) -> None:
    support.enqueue_identity(root, PAIR)  # unjudged: the merge is withheld

    merge = preview_decision(Workspace.open(root), IDENTITY_KEY, Action.MERGE)
    tier = preview_decision(Workspace.open(root), IDENTITY_KEY, Action.SET_TIER)

    assert merge == NotApplied(Action.MERGE, "not_mergeable")
    assert tier == NotApplied(Action.SET_TIER, "not_offered")


def test_skip_has_no_plan_and_is_returned_as_not_applied(root: Path) -> None:
    _enqueue_merge(root)

    result = preview_decision(Workspace.open(root), IDENTITY_KEY, Action.SKIP)

    assert result == NotApplied(Action.SKIP, "skipped")


# -- apply --------------------------------------------------------------------- #


def test_apply_acts_on_one_decision_and_leaves_the_other_open(root: Path) -> None:
    _enqueue_merge(root)
    support.enqueue_volatility(root, "Person", "static")
    version = _preview(root, IDENTITY_KEY, Action.MERGE).version

    outcome = apply_decision(Workspace.open(root), version, Action.MERGE)

    assert isinstance(outcome, Applied)
    assert outcome.decision_key == IDENTITY_KEY
    assert outcome.action is Action.MERGE
    assert outcome.commit_sha == _git(root, "rev-parse", "--short", "HEAD")
    assert _row(root, IDENTITY_KEY) == ("applied", "as_proposed")
    assert [d.decision_key for d in list_decisions(Workspace.open(root))] == [TIER_KEY]
    assert not (root / "bundle" / "concepts" / "b.md").exists()


def test_the_merge_commit_holds_only_what_the_merge_wrote(root: Path) -> None:
    _enqueue_merge(root)
    (root / "unrelated.txt").write_text("staged by the user", encoding="utf-8")
    _git(root, "add", "--", "unrelated.txt")
    (root / "loose.txt").write_text("never staged", encoding="utf-8")
    version = _preview(root, IDENTITY_KEY, Action.MERGE).version

    outcome = apply_decision(Workspace.open(root), version, Action.MERGE)

    assert isinstance(outcome, Applied)
    assert outcome.commit_sha is not None
    files = _head_files(root)
    assert "bundle/concepts/b.md" in files
    assert "bundle/concepts/c.md" in files
    assert "unrelated.txt" not in files
    assert "loose.txt" not in files
    assert _git(root, "diff", "--cached", "--name-only") == "unrelated.txt"
    assert _git(root, "ls-files", "--others", "--exclude-standard") == "loose.txt"


def test_a_member_edited_after_the_preview_is_returned_stale(root: Path) -> None:
    _enqueue_merge(root)
    version = _preview(root, IDENTITY_KEY, Action.MERGE).version
    member = root / "bundle" / "concepts" / "a.md"
    member.write_text(
        member.read_text(encoding="utf-8") + "\nEdited later.\n", encoding="utf-8"
    )
    before = support.snapshot(root)

    outcome = apply_decision(Workspace.open(root), version, Action.MERGE)

    assert outcome == Stale(IDENTITY_KEY, "content_changed")
    assert support.snapshot(root) == before
    assert _row(root, IDENTITY_KEY)[0] == "pending"


def test_a_decision_resolved_since_the_preview_is_returned_stale(root: Path) -> None:
    _enqueue_merge(root)
    version = _preview(root, IDENTITY_KEY, Action.MERGE).version
    assert isinstance(
        apply_decision(Workspace.open(root), version, Action.MERGE), Applied
    )

    again = apply_decision(Workspace.open(root), version, Action.MERGE)

    assert again == Stale(IDENTITY_KEY, "row_resolved")


def test_applying_an_action_the_decision_withholds_is_returned_not_applied(
    root: Path,
) -> None:
    support.enqueue_identity(root, PAIR)
    version = DecisionVersion(IDENTITY_KEY, "x", "y")

    outcome = apply_decision(Workspace.open(root), version, Action.MERGE)

    assert outcome == NotApplied(Action.MERGE, "not_mergeable")


# -- the commit ---------------------------------------------------------------- #


def test_a_tier_decision_commits_only_the_tier_change(root: Path) -> None:
    support.enqueue_volatility(root, "Person", "static")
    config_path = root / "openkos.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "chat_timeout: 600", "chat_timeout: 601"
        ),
        encoding="utf-8",
    )
    version = _preview(root, TIER_KEY, Action.SET_TIER).version

    outcome = apply_decision(Workspace.open(root), version, Action.SET_TIER)

    assert isinstance(outcome, Applied)
    assert outcome.commit_sha is not None
    assert _head_files(root) == ["openkos.yaml"]
    committed = _git(root, "show", "HEAD:openkos.yaml")
    assert "Person: static" in committed
    assert "chat_timeout: 600" in committed
    assert "chat_timeout: 601" not in committed
    assert "chat_timeout: 601" in config_path.read_text(encoding="utf-8")
    assert _git(root, "log", "-1", "--format=%s") == (
        "openkos: set-volatility Person -> static"
    )


def test_an_uncommitted_edit_of_the_same_tier_entry_is_refused_in_api_words(
    root: Path,
) -> None:
    config_path = root / "openkos.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + "type_tiers:\n  Person: volatile\n",
        encoding="utf-8",
    )
    support.enqueue_volatility(root, "Person", "static", current_default="volatile")
    version = _preview(root, TIER_KEY, Action.SET_TIER).version
    before = support.snapshot(root)

    with pytest.raises(Refused) as caught:
        apply_decision(Workspace.open(root), version, Action.SET_TIER)

    assert str(caught.value) == (
        "the 'Person' tier cannot be committed on its own: openkos.yaml has "
        "uncommitted changes to that entry (or a shape this operation cannot "
        "edit in the committed version); commit or discard them, then try again"
    )
    assert support.snapshot(root) == before


def test_applying_the_tier_a_config_already_holds_commits_nothing_and_stays_quiet(
    root: Path,
) -> None:
    config_path = root / "openkos.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + "type_tiers:\n  Person: static\n",
        encoding="utf-8",
    )
    vcs_git.commit_paths(root, ["openkos.yaml"], "pin Person")
    support.enqueue_volatility(root, "Person", "static", current_default="static")
    head = _git(root, "rev-parse", "HEAD")
    warnings: list[str] = []
    version = _preview(root, TIER_KEY, Action.SET_TIER).version

    outcome = apply_decision(
        Workspace.open(root), version, Action.SET_TIER, on_warning=warnings.append
    )

    assert outcome == Applied(TIER_KEY, Action.SET_TIER, None)
    assert _git(root, "rev-parse", "HEAD") == head
    assert warnings == []


def _mark_confidential(root: Path, concept_id: str) -> None:
    path = root / "bundle" / f"{concept_id}.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "type: Concept\n", "type: Concept\nsensitivity: confidential\n", 1
        ),
        encoding="utf-8",
    )
    vcs_git.commit_paths(root, [f"bundle/{concept_id}.md"], "mark confidential")


def test_a_commit_holding_a_confidential_concept_says_so(root: Path) -> None:
    _mark_confidential(root, "concepts/a")
    _enqueue_merge(root)
    version = _preview(root, IDENTITY_KEY, Action.MERGE).version

    outcome = apply_decision(Workspace.open(root), version, Action.MERGE)

    assert isinstance(outcome, Applied)
    assert outcome.commit_sha is not None
    assert outcome.committed_confidential is True


def test_a_commit_holding_nothing_confidential_does_not_say_so(root: Path) -> None:
    _enqueue_merge(root)
    version = _preview(root, IDENTITY_KEY, Action.MERGE).version

    outcome = apply_decision(Workspace.open(root), version, Action.MERGE)

    assert isinstance(outcome, Applied)
    assert outcome.commit_sha is not None
    assert outcome.committed_confidential is False


def test_an_uncommitted_apply_never_claims_a_confidential_commit(root: Path) -> None:
    _mark_confidential(root, "concepts/a")
    _enqueue_merge(root)
    shutil.rmtree(root / ".git")
    version = _preview(root, IDENTITY_KEY, Action.MERGE).version

    outcome = apply_decision(Workspace.open(root), version, Action.MERGE)

    assert isinstance(outcome, Applied)
    assert outcome.commit_sha is None
    assert outcome.committed_confidential is False


def test_a_non_repository_still_applies_and_warns_exactly_once(root: Path) -> None:
    _enqueue_merge(root)
    shutil.rmtree(root / ".git")
    version = _preview(root, IDENTITY_KEY, Action.MERGE).version
    warnings: list[str] = []

    outcome = apply_decision(
        Workspace.open(root), version, Action.MERGE, on_warning=warnings.append
    )

    assert outcome == Applied(IDENTITY_KEY, Action.MERGE, None)
    assert warnings == [
        "the decision was applied but not committed: the workspace is not "
        "inside a git repository"
    ]
    assert not (root / "bundle" / "concepts" / "b.md").exists()
    assert not (root / ".git").exists()


def test_an_unset_git_identity_warns_in_api_words(
    root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _enqueue_merge(root)
    version = _preview(root, IDENTITY_KEY, Action.MERGE).version
    monkeypatch.setenv("GIT_CONFIG_COUNT", "0")
    warnings: list[str] = []

    outcome = apply_decision(
        Workspace.open(root), version, Action.MERGE, on_warning=warnings.append
    )

    assert outcome == Applied(IDENTITY_KEY, Action.MERGE, None)
    assert warnings == [
        "the decision was applied but not committed: git user.name and "
        "user.email are not set"
    ]


def test_a_skipped_commit_without_a_warning_callback_is_silent(root: Path) -> None:
    _enqueue_merge(root)
    shutil.rmtree(root / ".git")
    version = _preview(root, IDENTITY_KEY, Action.MERGE).version

    outcome = apply_decision(Workspace.open(root), version, Action.MERGE)

    assert outcome == Applied(IDENTITY_KEY, Action.MERGE, None)


def test_a_failed_commit_names_the_reason_and_still_applies(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enqueue_merge(root)
    version = _preview(root, IDENTITY_KEY, Action.MERGE).version

    def boom(*args: object, **kwargs: object) -> str:
        raise vcs_git.GitError("index.lock exists")

    monkeypatch.setattr(vcs_git, "commit_paths", boom)
    warnings: list[str] = []

    outcome = apply_decision(
        Workspace.open(root), version, Action.MERGE, on_warning=warnings.append
    )

    assert outcome == Applied(IDENTITY_KEY, Action.MERGE, None)
    assert warnings == [
        "the decision was applied but not committed: the git commit did not "
        "complete (index.lock exists)"
    ]


# -- repository selection ------------------------------------------------------ #


def test_the_commit_lands_in_the_workspace_repository_never_the_cwds(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enqueue_merge(root)
    version = _preview(root, IDENTITY_KEY, Action.MERGE).version
    other = tmp_path / "other-repository"
    other.mkdir()
    vcs_git.init_repo(other)
    (other / "mine.txt").write_text("not yours", encoding="utf-8")
    monkeypatch.chdir(other)
    workspace_head = _git(root, "rev-parse", "HEAD")

    outcome = apply_decision(Workspace.open(root), version, Action.MERGE)

    assert isinstance(outcome, Applied)
    assert outcome.commit_sha is not None
    assert _git(root, "rev-parse", "HEAD") != workspace_head
    assert _git(root, "rev-parse", "--short", "HEAD") == outcome.commit_sha
    assert vcs_git._run(["git", "rev-parse", "HEAD"], cwd=other).returncode != 0
    assert _git(other, "ls-files", "--others", "--exclude-standard") == "mine.txt"


# -- the lock ------------------------------------------------------------------ #


def test_a_held_lock_with_no_wait_raises_busy_and_writes_nothing(root: Path) -> None:
    _enqueue_merge(root)
    version = _preview(root, IDENTITY_KEY, Action.MERGE).version
    before = support.snapshot(root)

    with lock.workspace_lock(root), pytest.raises(WorkspaceBusyError):
        apply_decision(Workspace.open(root), version, Action.MERGE, wait_seconds=0)

    assert support.snapshot(root) == before
    assert _row(root, IDENTITY_KEY)[0] == "pending"


@pytest.mark.parametrize(
    "wait", [-1.0, lock_wait.MAX_WAIT_SECONDS + 1, float("nan"), float("inf")]
)
def test_a_wait_outside_the_documented_range_is_refused_before_anything_runs(
    root: Path, wait: float
) -> None:
    _enqueue_merge(root)
    version = _preview(root, IDENTITY_KEY, Action.MERGE).version
    before = support.snapshot(root)

    with pytest.raises(ValueError, match="wait_seconds"):
        apply_decision(Workspace.open(root), version, Action.MERGE, wait_seconds=wait)

    assert support.snapshot(root) == before


# -- preview and apply agree on the policy ------------------------------------- #


def test_preview_and_apply_both_ask_for_no_reconciliation(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enqueue_merge(root)
    seen: list[bool] = []
    real = curate_decisions.preview_merge

    def spy(*args: object, **kwargs: object) -> object:
        seen.append(bool(kwargs["no_reconcile"]))
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(curate_decisions, "preview_merge", spy)

    version = _preview(root, IDENTITY_KEY, Action.MERGE).version
    apply_decision(Workspace.open(root), version, Action.MERGE)

    # One build for the preview; the apply builds again and, under the token path,
    # re-previews inside the commit: every one asks for no reconciliation.
    assert len(seen) >= 2
    assert set(seen) == {True}


def test_the_stored_decision_payload_is_never_part_of_a_preview(root: Path) -> None:
    support.enqueue_relation(root, "concepts/a", "concepts/c", rationale="Because.")

    result = _preview(root, _relation_key("concepts/a", "concepts/c"), Action.RELATE)

    rendered = json.dumps(dataclasses.asdict(result), default=str)
    assert "confidence" not in rendered
    assert "Because." not in rendered
    assert '"payload"' not in rendered


def test_each_plan_type_carries_its_own_literal_kind() -> None:
    plans: dict[str, MergePlan | RelatePlan | TierPlan | KeepDistinctPlan] = {
        "merge": MergePlan("a", "b", "c", 0, False, None),
        "relate": RelatePlan("a", "b", "t"),
        "set_tier": TierPlan("Person", "static"),
        "keep_distinct": KeepDistinctPlan(("a", "b")),
    }

    assert {k: p.kind for k, p in plans.items()} == {
        "merge": "merge",
        "relate": "relate",
        "set_tier": "set_tier",
        "keep_distinct": "keep_distinct",
    }
