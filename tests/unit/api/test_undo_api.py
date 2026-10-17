"""`openkos.api` and undoing a review choice (`review-undo` change, ADR-0067):
each undo reverses a choice applied through the decision surface itself, through
the public facade only, in a real git-backed workspace whose identity is
pinned. Previews and lists take no lock, build no client and write nothing."""

import dataclasses
import shutil
from pathlib import Path
from typing import cast, get_args

import pytest

from openkos import config, lock
from openkos.api import (
    Action,
    Applied,
    DecisionPreview,
    KeepDistinctPlan,
    KeptDistinct,
    NotUndoable,
    Refused,
    RelatePlan,
    Reopened,
    ReopenPreview,
    ReopenVersion,
    TierChanged,
    TierChangePreview,
    TierChangeVersion,
    TierPlan,
    UndoStale,
    Unrelated,
    UnrelatePreview,
    UnrelateVersion,
    VolatilityTier,
    Workspace,
    WorkspaceBusyError,
    apply_decision,
    apply_reopen,
    apply_tier_change,
    apply_unrelate,
    kept_distinct,
    list_decisions,
    preview_decision,
    preview_reopen,
    preview_tier_change,
    preview_unrelate,
    refresh_decision_queue,
)
from openkos.application import curate_decisions
from openkos.model import types
from openkos.state import pending_queue as pq
from tests.unit.api import support
from tests.unit.api.test_decisions_apply import _git, _head_files, _pin_identity
from tests.unit.api.test_unmerge_api import A, B, C, _workspace
from tests.unit.application.curation_support import make_workspace


def _keep_separate(root: Path, members: tuple[str, ...]) -> None:
    key = curate_decisions.identity_decision_key(members)
    preview = preview_decision(Workspace.open(root), key, Action.KEEP_DISTINCT)
    assert isinstance(preview, DecisionPreview), preview
    assert isinstance(preview.plan, KeepDistinctPlan)
    applied = apply_decision(
        Workspace.open(root), preview.version, Action.KEEP_DISTINCT
    )
    assert isinstance(applied, Applied), applied


@pytest.fixture
def kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`a` and `b` kept separate through a decision."""
    root = _workspace(tmp_path, monkeypatch, (A, B))
    _keep_separate(root, (A, B))
    return root


def _preview(root: Path, members: tuple[str, ...]) -> ReopenPreview:
    preview = preview_reopen(Workspace.open(root), members)
    assert isinstance(preview, ReopenPreview), preview
    return preview


# -- keep separate ------------------------------------------------------------- #


def test_a_kept_pair_is_listed_and_previewed_without_the_lock(kept: Path) -> None:
    before = support.snapshot(kept)

    with lock.workspace_lock(kept):
        listed = kept_distinct(Workspace.open(kept))
        preview = _preview(kept, (B, A))

    assert [(type(r), r.member_ids) for r in listed] == [(KeptDistinct, (A, B))]
    assert listed[0].decided_at
    assert preview.member_ids == (A, B)
    assert type(preview.version) is ReopenVersion
    assert preview.version.member_ids == (A, B)
    assert support.snapshot(kept) == before


def test_a_kept_pair_is_reopened_in_one_commit(kept: Path) -> None:
    preview = _preview(kept, (A, B))
    head = _git(kept, "rev-parse", "HEAD")

    reopened = apply_reopen(Workspace.open(kept), preview.version)

    assert isinstance(reopened, Reopened)
    assert reopened.member_ids == (A, B)
    assert reopened.commit_sha is not None
    assert _git(kept, "rev-parse", "HEAD~1") == head
    assert _git(kept, "log", "-1", "--format=%s") == f"openkos: reopen identity {A}/{B}"
    assert [path.split("/")[1] for path in _head_files(kept)] == [".state"]
    assert kept_distinct(Workspace.open(kept)) == ()


def test_a_pair_never_kept_separate_is_not_undoable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch, (A, B))

    assert preview_reopen(Workspace.open(root), (A, B)) == NotUndoable(
        "not_kept_distinct"
    )


def test_an_altered_version_is_stale_and_writes_nothing(kept: Path) -> None:
    preview = _preview(kept, (A, B))
    altered = dataclasses.replace(preview.version, content_digest="0" * 64)
    before = support.snapshot(kept)

    assert apply_reopen(Workspace.open(kept), altered) == UndoStale()
    assert support.snapshot(kept) == before


def test_a_replayed_version_is_not_undoable(kept: Path) -> None:
    preview = _preview(kept, (A, B))
    assert isinstance(apply_reopen(Workspace.open(kept), preview.version), Reopened)

    assert apply_reopen(Workspace.open(kept), preview.version) == NotUndoable(
        "not_kept_distinct"
    )


def test_a_held_lock_raises_busy_and_writes_nothing(kept: Path) -> None:
    preview = _preview(kept, (A, B))
    before = support.snapshot(kept)

    with lock.workspace_lock(kept), pytest.raises(WorkspaceBusyError):
        apply_reopen(Workspace.open(kept), preview.version)

    assert support.snapshot(kept) == before


def test_a_wait_outside_the_range_is_refused_before_anything_runs(kept: Path) -> None:
    preview = _preview(kept, (A, B))
    before = support.snapshot(kept)

    with pytest.raises(ValueError, match="wait_seconds"):
        apply_reopen(Workspace.open(kept), preview.version, wait_seconds=-1)

    assert support.snapshot(kept) == before


def test_a_commit_that_cannot_be_made_is_warned_once(kept: Path) -> None:
    preview = _preview(kept, (A, B))
    shutil.rmtree(kept / ".git")
    warnings: list[str] = []

    reopened = apply_reopen(
        Workspace.open(kept), preview.version, on_warning=warnings.append
    )

    assert isinstance(reopened, Reopened)
    assert reopened.commit_sha is None
    assert len(warnings) == 1
    assert warnings[0].startswith("the undo was applied but not committed")


def test_a_reopened_pair_is_offered_again_by_a_queue_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pin_identity(monkeypatch, tmp_path)
    root = make_workspace(tmp_path, monkeypatch)
    support.write_identity_groups(root)
    support.forbid_model_clients(monkeypatch, "an undo")
    refresh_decision_queue(Workspace.open(root))
    pair = ("concepts/g0-a", "concepts/g0-b")
    key = curate_decisions.identity_decision_key(pair)
    _keep_separate(root, pair)
    assert key not in {d.decision_key for d in list_decisions(Workspace.open(root))}

    apply_reopen(Workspace.open(root), _preview(root, pair).version)
    refresh_decision_queue(Workspace.open(root))

    assert key in {d.decision_key for d in list_decisions(Workspace.open(root))}


def test_a_group_member_set_is_reopened_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch, (A, B, C))
    _keep_separate(root, (A, B, C))

    assert preview_reopen(Workspace.open(root), (A, B)) == NotUndoable(
        "not_kept_distinct"
    )
    preview = _preview(root, (C, B, A))
    assert isinstance(apply_reopen(Workspace.open(root), preview.version), Reopened)
    assert kept_distinct(Workspace.open(root)) == ()


# -- a connection --------------------------------------------------------------- #


@pytest.fixture
def related(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`a` is a member of `b`, written by applying a relation-type decision."""
    root = _workspace(tmp_path, monkeypatch, (A, B))
    support.enqueue_relation(root, A, B, fresh=True)
    key = pq.stored_decision_key("relation_type", pq.relation_type_key(A, B))
    preview = preview_decision(Workspace.open(root), key, Action.RELATE)
    assert isinstance(preview, DecisionPreview), preview
    assert preview.plan == RelatePlan(A, B, "member_of")
    applied = apply_decision(Workspace.open(root), preview.version, Action.RELATE)
    assert isinstance(applied, Applied), applied
    return root


def _unrelate_preview(root: Path) -> UnrelatePreview:
    preview = preview_unrelate(Workspace.open(root), A, "member_of", B)
    assert isinstance(preview, UnrelatePreview), preview
    return preview


def test_a_connection_is_previewed_without_the_lock(related: Path) -> None:
    before = support.snapshot(related)

    with lock.workspace_lock(related):
        preview = _unrelate_preview(related)

    assert (preview.source_id, preview.relation_type, preview.target_id) == (
        A,
        "member_of",
        B,
    )
    assert preview.withdraws_deprecation is False
    assert type(preview.version) is UnrelateVersion
    assert (
        preview.version.source_id,
        preview.version.relation_type,
        preview.version.target_id,
    ) == (A, "member_of", B)
    assert support.snapshot(related) == before


def test_a_connection_made_through_a_decision_is_removed_in_one_commit(
    related: Path,
) -> None:
    preview = _unrelate_preview(related)
    head = _git(related, "rev-parse", "HEAD")

    unrelated = apply_unrelate(Workspace.open(related), preview.version)

    assert isinstance(unrelated, Unrelated)
    assert (unrelated.source_id, unrelated.relation_type, unrelated.target_id) == (
        A,
        "member_of",
        B,
    )
    assert unrelated.commit_sha is not None
    assert unrelated.committed_confidential is False
    assert _git(related, "rev-parse", "HEAD~1") == head
    assert _head_files(related) == ["bundle/concepts/a.md", "bundle/log.md"]
    assert preview_unrelate(Workspace.open(related), A, "member_of", B) == (
        NotUndoable("not_related")
    )


def test_an_absent_connection_is_not_undoable(related: Path) -> None:
    assert preview_unrelate(Workspace.open(related), B, "member_of", A) == (
        NotUndoable("not_related")
    )


def test_an_edited_page_is_stale_and_writes_nothing(related: Path) -> None:
    preview = _unrelate_preview(related)
    page = related / "bundle" / f"{A}.md"
    page.write_text(page.read_text(encoding="utf-8") + "\nEdited.\n", "utf-8")
    before = support.snapshot(related)

    assert apply_unrelate(Workspace.open(related), preview.version) == UndoStale()
    assert support.snapshot(related) == before


def test_a_confidential_page_reports_its_commit(related: Path) -> None:
    page = related / "bundle" / f"{A}.md"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "---\n", "---\nsensitivity: confidential\n", 1
        ),
        "utf-8",
    )
    _git(related, "commit", "-qam", "mark confidential")
    preview = _unrelate_preview(related)

    unrelated = apply_unrelate(Workspace.open(related), preview.version)

    assert isinstance(unrelated, Unrelated)
    assert unrelated.committed_confidential is True


def test_an_unrelate_on_a_held_lock_raises_busy(related: Path) -> None:
    preview = _unrelate_preview(related)
    before = support.snapshot(related)

    with lock.workspace_lock(related), pytest.raises(WorkspaceBusyError):
        apply_unrelate(Workspace.open(related), preview.version)

    assert support.snapshot(related) == before


# -- how quickly a type goes out of date ----------------------------------------- #


def _set_tier_through_a_decision(root: Path, suggested: str) -> TierPlan:
    support.enqueue_volatility(root, "Person", suggested)
    key = pq.stored_decision_key("volatility", pq.volatility_key("Person"))
    preview = preview_decision(Workspace.open(root), key, Action.SET_TIER)
    assert isinstance(preview, DecisionPreview), preview
    assert isinstance(preview.plan, TierPlan)
    applied = apply_decision(Workspace.open(root), preview.version, Action.SET_TIER)
    assert isinstance(applied, Applied), applied
    return preview.plan


@pytest.fixture
def plain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = _workspace(tmp_path, monkeypatch, (A, B))
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "settle the workspace", "--allow-empty")
    return root


def test_a_tier_decision_reports_the_entry_it_replaces(plain: Path) -> None:
    assert _set_tier_through_a_decision(plain, "volatile").configured_tier is None


def test_a_tier_decision_over_an_entry_reports_that_entry(plain: Path) -> None:
    yaml = plain / "openkos.yaml"
    yaml.write_text(
        yaml.read_text(encoding="utf-8") + "type_tiers:\n  Person: slow\n", "utf-8"
    )
    _git(plain, "commit", "-qam", "a tier")

    assert _set_tier_through_a_decision(plain, "volatile").configured_tier == "slow"


def _undo_tier(root: Path, plan: TierPlan) -> TierChanged:
    # `configured_tier` is the configuration's own text, so a caller narrows it
    # to a tier before passing it back.
    assert plan.configured_tier in (None, *get_args(VolatilityTier))
    restore = cast(VolatilityTier | None, plan.configured_tier)
    with lock.workspace_lock(root):
        preview = preview_tier_change(Workspace.open(root), "Person", restore)
    assert isinstance(preview, TierChangePreview), preview
    assert (preview.type_name, preview.tier) == ("Person", plan.configured_tier)
    assert preview.configured_tier == plan.tier
    assert type(preview.version) is TierChangeVersion
    changed = apply_tier_change(Workspace.open(root), preview.version)
    assert isinstance(changed, TierChanged), changed
    assert (changed.type_name, changed.tier) == ("Person", plan.configured_tier)
    assert changed.commit_sha is not None
    assert _head_files(root) == ["openkos.yaml"]
    return changed


def test_a_tier_with_no_earlier_entry_is_restored_by_removing_it(plain: Path) -> None:
    plan = _set_tier_through_a_decision(plain, "volatile")

    _undo_tier(plain, plan)

    yaml = (plain / "openkos.yaml").read_text(encoding="utf-8")
    assert config.type_tier_entry(yaml, "Person") is None
    assert _git(plain, "log", "-1", "--format=%s") == "openkos: clear-volatility Person"


def test_a_tier_over_an_entry_is_restored_byte_for_byte(plain: Path) -> None:
    yaml = plain / "openkos.yaml"
    yaml.write_text(
        yaml.read_text(encoding="utf-8") + "type_tiers:\n  Person: slow\n", "utf-8"
    )
    _git(plain, "commit", "-qam", "a tier")
    before = yaml.read_bytes()
    plan = _set_tier_through_a_decision(plain, "volatile")
    assert yaml.read_bytes() != before

    _undo_tier(plain, plan)

    assert yaml.read_bytes() == before
    assert _git(plain, "log", "-1", "--format=%s") == (
        "openkos: set-volatility Person -> slow"
    )


def test_a_tier_it_already_has_is_not_undoable(plain: Path) -> None:
    assert preview_tier_change(Workspace.open(plain), "Person", None) == NotUndoable(
        "tier_unchanged"
    )
    assert preview_tier_change(Workspace.open(plain), "Spaceship", "slow") == (
        NotUndoable("unknown_type")
    )


def test_a_tier_change_against_an_edited_config_is_stale(plain: Path) -> None:
    preview = preview_tier_change(Workspace.open(plain), "Person", "slow")
    assert isinstance(preview, TierChangePreview), preview
    yaml = plain / "openkos.yaml"
    yaml.write_text(yaml.read_text(encoding="utf-8") + "# edited\n", "utf-8")
    before = support.snapshot(plain)

    assert apply_tier_change(Workspace.open(plain), preview.version) == UndoStale()
    assert support.snapshot(plain) == before


def test_an_uncommitted_edit_to_the_same_entry_is_refused(plain: Path) -> None:
    yaml = plain / "openkos.yaml"
    yaml.write_text(
        yaml.read_text(encoding="utf-8") + "type_tiers:\n  Person: static\n", "utf-8"
    )
    preview = preview_tier_change(Workspace.open(plain), "Person", "slow")
    assert isinstance(preview, TierChangePreview), preview
    before = support.snapshot(plain)

    with pytest.raises(Refused, match="Person"):
        apply_tier_change(Workspace.open(plain), preview.version)

    assert support.snapshot(plain) == before


def test_the_tier_alias_names_exactly_the_engines_tiers() -> None:
    assert set(get_args(VolatilityTier)) == set(types.VOLATILITY_TIERS)
