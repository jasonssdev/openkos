"""Setting or restoring a type's tier through `openkos.application.undo_service`
(`review-undo` change, ADR-0067, design D6): previewed with a version over
`openkos.yaml`, and applied under one lock, called without the CLI."""

from pathlib import Path

import pytest

from openkos import config, lock
from openkos.application import curate_decisions, lock_wait
from openkos.application import undo_service as svc
from openkos.application.write_gate import Refused
from openkos.lock import WorkspaceBusyError
from tests.unit.application.curation_support import CommitRecorder, make_workspace

_BLOCK = "\n# Tiers, per type.\ntype_tiers:\n  Place: static  # places stay\n"


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = make_workspace(tmp_path, monkeypatch)
    path = config.WorkspaceLayout(root).config_path
    path.write_text(path.read_text(encoding="utf-8") + _BLOCK, encoding="utf-8")
    return root


def _yaml(root: Path) -> str:
    return config.WorkspaceLayout(root).config_path.read_text(encoding="utf-8")


def _preview(root: Path, type_name: str, tier: str | None) -> svc.TierChangePlan:
    plan = svc.preview_tier_change(root, type_name, tier)
    assert isinstance(plan, svc.TierChangePlan), plan
    return plan


class _Recorder:
    """The two config-commit ports, recording what they were asked."""

    def __init__(self, refusal: str | None = None) -> None:
        self.refusal = refusal
        self.planned: list[tuple[str, str, str | None]] = []
        self.committed: list[str] = []

    def plan(
        self,
        root: Path,
        surface: str,
        concept_type: str,
        tier: str | None,
        baseline: bytes,
    ) -> curate_decisions.ConfigCommitRefusal | None:
        self.planned.append((surface, concept_type, tier))
        if self.refusal is not None:
            return curate_decisions.ConfigCommitRefusal(self.refusal)
        return None

    def commit(
        self, root: Path, message: str, plan: curate_decisions.ConfigCommitPlan | None
    ) -> str | None:
        self.committed.append(message)
        return "abc1234"


def _ports(root: Path, recorder: _Recorder) -> curate_decisions.DecisionPorts:
    return curate_decisions.DecisionPorts(
        autocommit=CommitRecorder(),
        commit_section=lock_wait.locked_commit_section(root, wait_seconds=0.0),
        plan_config_commit=recorder.plan,
        autocommit_config_edit=recorder.commit,
    )


def _apply(root: Path, plan: svc.TierChangePlan, recorder: _Recorder) -> object:
    return svc.apply_tier_change(
        root,
        plan.type_name,
        plan.tier,
        plan.content_digest,
        ports=_ports(root, recorder),
    )


# -- preview ------------------------------------------------------------------ #


def test_a_set_previews_the_configured_tier_and_writes_nothing(root: Path) -> None:
    before = _yaml(root)

    plan = _preview(root, "Place", "slow")

    assert (plan.type_name, plan.tier, plan.configured_tier) == (
        "Place",
        "slow",
        "static",
    )
    assert _yaml(root) == before


def test_a_type_with_no_entry_is_configured_as_none(root: Path) -> None:
    assert _preview(root, "Person", "volatile").configured_tier is None


@pytest.mark.parametrize(("type_name", "tier"), [("Place", "static"), ("Person", None)])
def test_the_entry_it_already_has_is_refused(
    root: Path, type_name: str, tier: str | None
) -> None:
    assert svc.preview_tier_change(root, type_name, tier) == svc.UndoRefusal(
        "tier_unchanged"
    )


def test_an_unknown_type_is_refused(root: Path) -> None:
    assert svc.preview_tier_change(root, "Spaceship", "slow") == svc.UndoRefusal(
        "unknown_type"
    )


def test_an_unknown_tier_raises(root: Path) -> None:
    with pytest.raises(ValueError, match="tier"):
        svc.preview_tier_change(root, "Place", "sometimes")


def test_the_version_names_the_requested_tier_and_the_bytes(root: Path) -> None:
    slow = _preview(root, "Place", "slow")
    volatile = _preview(root, "Place", "volatile")
    cleared = _preview(root, "Place", None)
    path = config.WorkspaceLayout(root).config_path
    path.write_text(_yaml(root) + "# a comment\n", encoding="utf-8")

    assert (
        len({slow.content_digest, volatile.content_digest, cleared.content_digest}) == 3
    )
    assert _preview(root, "Place", "slow").content_digest != slow.content_digest


# -- apply -------------------------------------------------------------------- #


def test_clearing_removes_the_entry_and_keeps_every_other_byte(root: Path) -> None:
    before = _yaml(root)
    recorder = _Recorder()

    done = _apply(root, _preview(root, "Place", None), recorder)

    assert done == svc.TierChanged("Place", None, "abc1234")
    assert _yaml(root) == config.clear_type_tier(before, "Place")
    assert "Place:" not in _yaml(root)
    assert recorder.planned == [("api", "Place", None)]
    assert recorder.committed == ["openkos: clear-volatility Place"]


def test_setting_writes_the_entry(root: Path) -> None:
    recorder = _Recorder()

    done = _apply(root, _preview(root, "Person", "volatile"), recorder)

    assert done == svc.TierChanged("Person", "volatile", "abc1234")
    assert config.type_tier_entry(_yaml(root), "Person") == "volatile"
    assert recorder.committed == ["openkos: set-volatility Person -> volatile"]


def test_a_set_then_its_restore_gives_back_the_same_bytes(root: Path) -> None:
    before = _yaml(root)
    plan = _preview(root, "Person", "volatile")
    _apply(root, plan, _Recorder())

    _apply(root, _preview(root, "Person", plan.configured_tier), _Recorder())

    assert _yaml(root) == before


def test_a_configuration_changed_after_the_preview_is_stale(root: Path) -> None:
    plan = _preview(root, "Place", "slow")
    path = config.WorkspaceLayout(root).config_path
    path.write_text(_yaml(root) + "# edited\n", encoding="utf-8")
    before = _yaml(root)
    recorder = _Recorder()

    assert _apply(root, plan, recorder) == svc.UndoStale()
    assert _yaml(root) == before
    assert recorder.planned == []


def test_a_digest_echoed_for_another_type_is_stale(root: Path) -> None:
    plan = _preview(root, "Place", "slow")
    before = _yaml(root)

    done = svc.apply_tier_change(
        root, "Person", "slow", plan.content_digest, ports=_ports(root, _Recorder())
    )

    assert done == svc.UndoStale()
    assert _yaml(root) == before


def test_an_uncommitted_edit_to_the_entry_is_refused_with_the_adapters_text(
    root: Path,
) -> None:
    plan = _preview(root, "Place", "slow")
    before = _yaml(root)

    with pytest.raises(Refused, match="commit or discard"):
        _apply(root, plan, _Recorder(refusal="commit or discard them"))

    assert _yaml(root) == before


def test_a_held_lock_is_busy_and_writes_nothing(root: Path) -> None:
    plan = _preview(root, "Place", "slow")
    before = _yaml(root)

    with lock.workspace_lock(root), pytest.raises(WorkspaceBusyError):
        _apply(root, plan, _Recorder())

    assert _yaml(root) == before
