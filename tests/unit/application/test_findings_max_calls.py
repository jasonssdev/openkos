"""The `max_calls` bound of the findings services (MVP 4 unit 5.2).

A budgeted (runner-started) run passes the calls it has left; `None` -- the
default, and the only value a CLI verb ever passes -- is unbounded and
byte-identical to the behaviour before the bound existed. A truncated stage
persists what it judged (the store serves it next pass) and REPORTS how many
units it deferred.
"""

import dataclasses
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from openkos import config
from openkos.application import contradictions_service as contradictions
from openkos.application import revisions as revisions_service
from openkos.application import suggest_relations_service as relations
from openkos.application import suggest_volatility_service as volatility
from openkos.graph.base import Edge
from openkos.llm.base import BackendUnavailable
from openkos.model import types
from openkos.resolution import volatility_typing
from openkos.resolution.contradiction import CandidatePlan, _CandidateSpec
from openkos.resolution.edge_typing import EdgeSuggestion, EdgeSuggestionBatch
from openkos.resolution.volatility_typing import TierSuggestionBatch
from tests.unit.application.test_contradictions_service_seams import (
    _Calls,
    _Observer,
    _ports,
    _run,
)
from tests.unit.application.test_contradictions_service_seams import (
    _workspace as _contradictions_workspace,
)
from tests.unit.application.test_findings_services_seams import (
    _relations_ports,
    _RelationsRecorder,
    _volatility_ports,
    _VolatilityRecorder,
    _workspace,
)
from tests.unit.resolution.test_volatility_typing import (
    _FakeLLM,
    _valid_reply,
    _write_doc,
)

# -- contradictions ---------------------------------------------------------


def _bounded(
    root: Path, ports: contradictions.ContradictionsPorts, max_calls: int
) -> contradictions.ContradictionsOutcome:
    return contradictions.run_contradictions(
        root,
        options=contradictions.ContradictionsOptions(max_calls=max_calls),
        ports=ports,
        observer=_Observer(),
    )


def _plan(n: int) -> CandidatePlan:
    specs = tuple(
        _CandidateSpec(
            pair_ids=(f"concepts/a{i}", f"concepts/b{i}"), relation_type="related_to"
        )
        for i in range(n)
    )
    return CandidatePlan(specs=specs, edge_total=n, merged_total=0)


def test_contradictions_max_calls_truncates_the_judged_plan_in_plan_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _contradictions_workspace(tmp_path, monkeypatch)
    calls = _Calls()
    plan = _plan(5)

    outcome = _bounded(root, _ports(calls, plan=plan), 2)

    judged = calls.find_kwargs["plan"]
    assert judged.specs == plan.specs[:2]
    assert (judged.edge_total, judged.merged_total) == (5, 0), "totals describe ALL"
    assert outcome.fresh_count == 2
    assert outcome.deferred_by_bound == 3
    assert outcome.plan is plan


def test_contradictions_a_bound_of_zero_judges_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _contradictions_workspace(tmp_path, monkeypatch)
    calls = _Calls()

    outcome = _bounded(root, _ports(calls, plan=_plan(3)), 0)

    assert calls.find_kwargs["plan"].specs == ()
    assert outcome.deferred_by_bound == 3
    assert outcome.zero_state is None, "an unjudged-by-budget run is not an empty graph"
    assert calls.persisted == []


def test_contradictions_the_bound_counts_only_what_would_be_judged_not_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Served verdicts cost nothing, so a bound of 1 over a plan of one served
    and two unserved candidates judges ONE of the two, not zero."""
    from tests.unit.application.test_contradictions_service_seams import (
        _DIGESTS,
        _persist_row,
    )

    root = _contradictions_workspace(tmp_path, monkeypatch)
    served_spec = _CandidateSpec(
        pair_ids=("concepts/a", "concepts/b"), relation_type="related_to"
    )
    plan = CandidatePlan(
        specs=(served_spec, *_plan(2).specs), edge_total=3, merged_total=0
    )
    _persist_row(root)
    calls = _Calls()
    ports = dataclasses.replace(
        _ports(calls, plan=plan),
        finding_input_digests=lambda bundle_dir, spec: _DIGESTS,
    )

    outcome = _bounded(root, ports, 1)

    assert calls.find_kwargs["plan"].specs == (_plan(2).specs[0],)
    assert outcome.served_count == 1
    assert outcome.deferred_by_bound == 1


def test_contradictions_without_a_bound_nothing_is_deferred(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _contradictions_workspace(tmp_path, monkeypatch)
    calls = _Calls()
    plan = _plan(4)

    outcome = _run(root, _ports(calls, plan=plan))

    assert calls.find_kwargs["plan"] is plan
    assert outcome.deferred_by_bound == 0


def test_a_negative_bound_is_refused_rather_than_sliced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _contradictions_workspace(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="max_calls"):
        _bounded(root, _ports(_Calls(), plan=_plan(3)), -1)


# -- suggest-relations ------------------------------------------------------


def _edges(n: int) -> list[Edge]:
    return [Edge(f"concepts/s{i}", f"concepts/t{i}") for i in range(n)]


def _typed(to_type: Sequence[Edge], kwargs: dict[str, Any]) -> EdgeSuggestionBatch:
    return EdgeSuggestionBatch(
        results=[
            EdgeSuggestion(edge=e, suggested_type="references", rationale="r")
            for e in to_type
        ]
    )


def test_relations_max_calls_types_only_the_first_edges_and_reports_the_rest(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    seen: list[Any] = []

    outcome = relations.suggest_relations(
        root,
        relations.SuggestRelationsRequest(skip_confirmation=True, max_calls=2),
        _relations_ports(_edges(5), _typed, tmp_calls=seen),
        _RelationsRecorder(),
    )

    assert seen[0][0] == ["concepts/s0", "concepts/s1"]
    assert [r.edge.source_id for r in outcome.results] == ["concepts/s0", "concepts/s1"]
    assert outcome.total == 5
    assert outcome.deferred_by_bound == 3


def test_relations_without_a_bound_all_edges_are_typed(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    seen: list[Any] = []

    outcome = relations.suggest_relations(
        root,
        relations.SuggestRelationsRequest(skip_confirmation=True),
        _relations_ports(_edges(5), _typed, tmp_calls=seen),
        _RelationsRecorder(),
    )

    assert len(seen[0][0]) == 5
    assert outcome.deferred_by_bound == 0


def test_relations_a_bound_of_zero_types_nothing(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    seen: list[Any] = []

    outcome = relations.suggest_relations(
        root,
        relations.SuggestRelationsRequest(skip_confirmation=True, max_calls=0),
        _relations_ports(_edges(3), _typed, tmp_calls=seen),
        _RelationsRecorder(),
    )

    assert seen[0][0] == [], "zero edges means zero llm.chat calls by construction"
    assert outcome.deferred_by_bound == 3


def test_relations_negative_bound_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="max_calls"):
        relations.suggest_relations(
            _workspace(tmp_path),
            relations.SuggestRelationsRequest(skip_confirmation=True, max_calls=-1),
            _relations_ports(_edges(2), _typed),
            _RelationsRecorder(),
        )


# -- suggest-volatility -----------------------------------------------------


def test_volatility_forwards_the_bound_to_the_leaf_only_when_one_is_set(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    seen: list[dict[str, Any]] = []

    def _suggest(bundle_dir: Path, **kwargs: Any) -> TierSuggestionBatch:
        seen.append(kwargs)
        return TierSuggestionBatch(results=[], deferred=4)

    ports = dataclasses.replace(_volatility_ports(None), suggest_volatility=_suggest)

    unbounded = volatility.suggest_volatility_tiers(
        root, volatility.VolatilityRequest(), ports, _VolatilityRecorder()
    )
    bounded = volatility.suggest_volatility_tiers(
        root, volatility.VolatilityRequest(max_calls=3), ports, _VolatilityRecorder()
    )

    assert "max_calls" not in seen[0], "CLI calls are byte-identical"
    assert seen[1]["max_calls"] == 3
    assert unbounded.deferred_by_bound == 4, "carried from the batch, whatever it is"
    assert bounded.deferred_by_bound == 4


def test_volatility_negative_bound_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="max_calls"):
        volatility.suggest_volatility_tiers(
            _workspace(tmp_path),
            volatility.VolatilityRequest(max_calls=-1),
            _volatility_ports(TierSuggestionBatch(results=[])),
            _VolatilityRecorder(),
        )


def _three_types(bundle: Path) -> None:
    _write_doc(bundle / "a.md", doc_type="Person", title="A")
    _write_doc(bundle / "b.md", doc_type="Project", title="B")
    _write_doc(bundle / "c.md", doc_type="Organization", title="C")


def test_volatility_leaf_stops_after_max_calls_and_counts_the_deferred_types(
    tmp_path: Path,
) -> None:
    _three_types(tmp_path)
    llm = _FakeLLM(replies=[_valid_reply(), _valid_reply()])

    batch = volatility_typing.suggest_volatility(tmp_path, llm=llm, max_calls=2)

    assert [s.type_name for s in batch.results] == ["Organization", "Person"]
    assert len(llm.calls) == 2
    assert batch.deferred == 1
    assert batch.failure is None


def test_volatility_leaf_without_a_bound_is_unchanged(tmp_path: Path) -> None:
    _three_types(tmp_path)
    llm = _FakeLLM(replies=[_valid_reply()] * 3)

    batch = volatility_typing.suggest_volatility(tmp_path, llm=llm)

    assert len(batch.results) == 3
    assert batch.deferred == 0


def test_volatility_leaf_a_bound_of_zero_makes_no_call(tmp_path: Path) -> None:
    _three_types(tmp_path)
    llm = _FakeLLM()

    batch = volatility_typing.suggest_volatility(tmp_path, llm=llm, max_calls=0)

    assert llm.calls == []
    assert batch.results == []
    assert batch.deferred == 3


def test_volatility_leaf_a_failed_call_still_spends_the_bound(tmp_path: Path) -> None:
    """A raised chat was an issued call: it counts against the bound, and the
    failure still comes back in the batch."""
    _three_types(tmp_path)
    llm = _FakeLLM(error=BackendUnavailable("down"), error_at=1)

    batch = volatility_typing.suggest_volatility(tmp_path, llm=llm, max_calls=2)

    assert len(llm.calls) == 1
    assert isinstance(batch.failure, BackendUnavailable)
    assert batch.failed_index == 1


def test_volatility_leaf_a_filtered_type_is_not_a_deferred_unit(
    tmp_path: Path,
) -> None:
    """A type whose sampled docs are all blocked never reaches a chat call, so
    deferring it would claim work that was never there."""
    _write_doc(tmp_path / "a.md", doc_type="Person", title="A")
    _write_doc(
        tmp_path / "b.md",
        doc_type="Project",
        title="B",
        sensitivity_value="confidential",
    )
    llm = _FakeLLM(replies=[])

    batch = volatility_typing.suggest_volatility(tmp_path, llm=llm, max_calls=0)

    assert batch.deferred == 1, "only Person was ever going to be asked"


def test_volatility_leaf_tiers_are_the_packaged_defaults_when_bounded(
    tmp_path: Path,
) -> None:
    _write_doc(tmp_path / "a.md", doc_type="Person", title="A")
    llm = _FakeLLM(replies=[_valid_reply("slow", "r")])

    (suggestion,) = volatility_typing.suggest_volatility(
        tmp_path, llm=llm, max_calls=1
    ).results

    assert suggestion.current_default == types.TYPE_TO_DEFAULT_VOLATILITY["Person"]


# -- revisions: the request carries the bound to `judge_revisions` ----------


def test_the_revisions_request_forwards_its_bound_to_judge_revisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.unit.application.test_findings_services_seams import (
        _fake_plan,
        _revisions_ports,
        _RevisionsRecorder,
    )

    root = _workspace(tmp_path)
    forwarded: list[Any] = []
    monkeypatch.setattr(
        revisions_service,
        "load_decisions",
        lambda layout, **kwargs: revisions_service.DecisionSet(
            decisions=(revisions_service.Decision("decisions/a", frozenset()),),
            bad_relations=0,
        ),
    )
    monkeypatch.setattr(
        revisions_service,
        "plan_revisions",
        lambda *a, **k: _fake_plan("ok", to_judge=1),
    )

    def _judge(layout: object, plan: object, **kwargs: Any) -> Any:
        forwarded.append(kwargs.get("max_calls", "absent"))
        return revisions_service.RevisionOutcome(results=())

    monkeypatch.setattr(revisions_service, "judge_revisions", _judge)

    for request in (
        revisions_service.RevisionsRequest(skip_confirmation=True, max_calls=7),
        revisions_service.RevisionsRequest(skip_confirmation=True),
    ):
        revisions_service.run_revisions(
            root, request, _revisions_ports(), _RevisionsRecorder()
        )

    assert forwarded == [7, None]


def test_revisions_negative_bound_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="max_calls"):
        revisions_service.judge_revisions(
            config.WorkspaceLayout(tmp_path),
            revisions_service.RevisionPlan(
                coverage=revisions_service.VectorCoverage(
                    store="ok", vectors={}, missing=frozenset(), stale=frozenset()
                ),
                candidate_plan=None,  # type: ignore[arg-type]
                served=(),
                to_judge=(),
            ),
            llm=_FakeLLM(),
            effective_confidential=False,
            max_calls=-1,
        )
