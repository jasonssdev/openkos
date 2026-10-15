"""`openkos.api.list_decisions`: open decisions as API-owned values with typed
details. The stored payload mapping and the adjudicator's confidence never
cross the facade."""

import dataclasses
from pathlib import Path

import pytest

from openkos import lock
from openkos.api import (
    Action,
    Decision,
    IdentityDetail,
    RelationDetail,
    TierDetail,
    WithheldAction,
    Workspace,
    list_decisions,
)
from tests.unit.api import support
from tests.unit.application.curation_support import make_workspace
from tests.unit.cli.commit_phase_support import write_doc

PAIR = ("concepts/a", "concepts/b")


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    ws = make_workspace(tmp_path, monkeypatch)
    for concept_id in (*PAIR, "concepts/c"):
        write_doc(ws, concept_id, {"type": "Concept", "title": concept_id}, "Body.\n")
    return ws


@pytest.fixture(autouse=True)
def no_client(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    support.forbid_model_clients(monkeypatch, "listing")


def test_an_absent_queue_lists_nothing(root: Path) -> None:
    assert list_decisions(Workspace.open(root)) == ()


def test_a_judged_identity_decision_carries_a_typed_identity_detail(root: Path) -> None:
    support.enqueue_identity(root, PAIR, verdict="same", rationale="Same thing.")

    (decision,) = list_decisions(Workspace.open(root))

    assert isinstance(decision, Decision)
    assert decision.kind == "identity"
    assert decision.targets == PAIR
    assert decision.decision_key.startswith("identity:")
    assert decision.evaluation == "judged_same"
    assert decision.fresh is not None
    assert Action.MERGE in decision.actions
    assert decision.withheld == ()
    assert decision.resolves_via == "openkos adjudicate --apply"
    assert decision.detail == IdentityDetail(
        member_ids=PAIR,
        okf_type="Concept",
        tier="high",
        verdict="same",
        rationale="Same thing.",
    )


def test_an_unjudged_identity_decision_has_no_verdict_and_withholds_merge(
    root: Path,
) -> None:
    support.enqueue_identity(root, PAIR)

    (decision,) = list_decisions(Workspace.open(root))

    assert decision.evaluation == "pending_evaluation"
    assert Action.MERGE not in decision.actions
    assert decision.withheld == (WithheldAction(Action.MERGE, "pending_evaluation"),)
    assert isinstance(decision.detail, IdentityDetail)
    assert (decision.detail.verdict, decision.detail.rationale) == (None, None)


def test_a_relation_decision_carries_a_typed_relation_detail(root: Path) -> None:
    support.enqueue_relation(root, "concepts/a", "concepts/c", rationale="Because.")

    (decision,) = list_decisions(Workspace.open(root))

    assert decision.kind == "relation_type"
    assert decision.evaluation == "not_applicable"
    assert decision.actions == (Action.RELATE, Action.RELATE_REVERSED, Action.SKIP)
    assert decision.detail == RelationDetail(
        source_id="concepts/a",
        target_id="concepts/c",
        effective_source_id="concepts/a",
        effective_target_id="concepts/c",
        suggested_type="member_of",
        rationale="Because.",
    )


def test_a_volatility_decision_carries_a_typed_tier_detail(root: Path) -> None:
    support.enqueue_volatility(root, "Person", "static")

    (decision,) = list_decisions(Workspace.open(root))

    assert decision.kind == "volatility"
    assert decision.actions == (Action.SET_TIER, Action.SKIP)
    assert decision.detail == TierDetail(
        type_name="Person",
        current_default="slow",
        suggested_tier="static",
        rationale="Names do not change.",
    )


@pytest.mark.parametrize(
    "enqueue",
    [
        lambda root: support.enqueue_volatility(root, "Person", "daily"),
        lambda root: support.enqueue_relation(
            root, "concepts/a", "concepts/c", suggested=""
        ),
        lambda root: support.enqueue_identity(root, PAIR, tier="bogus"),
    ],
    ids=["volatility", "relation", "identity"],
)
def test_a_malformed_payload_is_still_listed_with_no_detail(
    root: Path, enqueue: object
) -> None:
    enqueue(root)  # type: ignore[operator]

    (decision,) = list_decisions(Workspace.open(root))

    assert decision.detail is None
    assert decision.decision_key


def test_listing_preserves_the_service_order_and_every_row(root: Path) -> None:
    support.enqueue_volatility(root)
    support.enqueue_identity(root, PAIR)
    support.enqueue_relation(root, "concepts/a", "concepts/c")

    kinds = [d.kind for d in list_decisions(Workspace.open(root))]

    assert kinds == ["identity", "relation_type", "volatility"]


def test_no_payload_mapping_or_confidence_is_exposed(root: Path) -> None:
    support.enqueue_identity(root, PAIR, verdict="same")
    (decision,) = list_decisions(Workspace.open(root))

    names = {f.name for f in dataclasses.fields(Decision)}
    detail_names = {f.name for f in dataclasses.fields(IdentityDetail)}
    assert "payload" not in names
    assert "payload_digest" not in names
    assert not any("confidence" in n for n in names | detail_names)
    assert "0.91" not in repr(decision)


def test_listing_takes_no_lock_and_writes_nothing(root: Path) -> None:
    support.enqueue_identity(root, PAIR, verdict="same")
    support.enqueue_volatility(root)
    before = support.snapshot(root)

    with lock.workspace_lock(root):
        decisions = list_decisions(Workspace.open(root))

    assert len(decisions) == 2
    assert support.snapshot(root) == before
