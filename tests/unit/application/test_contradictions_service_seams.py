"""Absence guards and service-level tests for the `contradictions`
application-service extraction (issue #1168), the `test_ingest_service_seams.py`
pattern.

The names that moved off `openkos.cli.main` are deliberately never aliased
back, so a stale `monkeypatch.setattr("openkos.cli.main.<name>", ...)` raises
`AttributeError` under pytest's default `raising=True` instead of silently
patching a name nothing reads. The service tests run it with an explicit root
while the process sits in an unrelated directory, which is what proves it never
reads the current directory, and they substitute every port so no model client,
no `typer` and no git is involved.
"""

import dataclasses
import inspect
import sqlite3
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from openkos import config
from openkos.application import contradictions_service as service
from openkos.bundle import decisions as bundle_decisions
from openkos.cli import main as cli_main
from openkos.graph.sqlite_graph import build_graph
from openkos.llm.base import (
    BackendError,
    BackendModelNotFound,
    BackendUnavailable,
    LLMBackend,
)
from openkos.resolution.contradiction import (
    CandidatePlan,
    ContradictionBatch,
    ContradictionVerdict,
    Verdict,
    _CandidateSpec,
)
from openkos.state import derived, findings
from tests.unit.cli.test_contradictions import _vacuous_plan
from tests.unit.conftest import LOCAL_BACKEND_LOCALITY


@pytest.mark.parametrize(
    "name",
    [
        "_partition_persisted_serves",
        "_contradiction_spec_key",
        "_open_findings_by_decision_key",
        "_apply_contradiction_decision",
        "_sorted_decision_pair",
    ],
)
def test_moved_contradiction_names_no_longer_live_on_cli_main(name: str) -> None:
    assert not hasattr(cli_main, name)


def test_the_moved_names_live_in_the_application_layer() -> None:
    assert callable(service.run_contradictions)
    assert callable(service.record_contradiction_decision)
    assert callable(service.list_declined)
    assert callable(service.contradiction_spec_key)
    assert callable(service.sorted_decision_pair)
    for refusal in (
        service.NotAWorkspace,
        service.WorkspaceUnreadable,
        service.BackendNotReachable,
        service.ModelNotInstalled,
        service.BackendFailed,
    ):
        assert issubclass(refusal, service.ContradictionsRefused)


def test_contradictions_verb_is_a_thin_adapter_that_delegates_to_the_service() -> None:
    verb = inspect.getsource(cli_main.contradictions)
    assert "contradictions_service.ContradictionsOptions(" in verb
    assert "bundle_decisions" not in verb
    assert "config.require_workspace(root)" not in verb
    report = inspect.getsource(cli_main._run_contradictions_report)
    assert "contradictions_service.run_contradictions(" in report
    assert "derived." not in report
    assert "findings." not in report
    assert "application_pending" not in report
    decision = inspect.getsource(cli_main._record_contradiction_decision)
    assert "contradictions_service.record_contradiction_decision(" in decision
    assert "bundle_decisions.read" not in decision
    assert "bundle_decisions.write" not in decision


# -- The service, without the CLI -------------------------------------------

_SPEC = _CandidateSpec(
    pair_ids=("concepts/a", "concepts/b"), relation_type="related_to"
)
_PLAN = CandidatePlan(specs=(_SPEC,), edge_total=1, merged_total=0)
_DIGESTS = (findings.InputDigest("concepts/a", "d" * 64),)


class _Client:
    locality = LOCAL_BACKEND_LOCALITY

    def chat(self, messages: object) -> str:
        raise AssertionError("the service must judge through `find_contradictions`")


class _Observer:
    """A `ContradictionsObserver` that records what it was told, in order."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.unreadable: list[Exception] = []
        self.persist_errors: list[Exception] = []
        self.vacuous: list[str] = []
        self.exemptions: list[bool] = []

    def exemption_resolved(self, local_exemption: bool) -> None:
        self.events.append("exemption_resolved")
        self.exemptions.append(local_exemption)

    def vacuous_coverage(self, notice: str) -> None:
        self.events.append("vacuous_coverage")
        self.vacuous.append(notice)

    def persisted_findings_unreadable(self, error: Exception) -> None:
        self.events.append("persisted_findings_unreadable")
        self.unreadable.append(error)

    def progress_callback(self) -> None:
        self.events.append("progress_callback")
        return None

    def persist_failed(self, error: Exception) -> None:
        self.events.append("persist_failed")
        self.persist_errors.append(error)


def _verdict(
    *, verdict: Verdict = Verdict.CONTRADICTS, confidence: float = 0.9
) -> ContradictionVerdict:
    return ContradictionVerdict(
        pair_ids=("concepts/a", "concepts/b"),
        verdict=verdict,
        confidence=confidence,
        rationale="because",
        conflicting_claims=("c1",),
    )


class _Calls:
    def __init__(self) -> None:
        self.find_kwargs: dict[str, Any] = {}
        self.persisted: list[tuple[CandidatePlan, list[ContradictionVerdict]]] = []
        self.zero_state_args: list[tuple[Path, bool]] = []


def _ports(
    calls: _Calls,
    *,
    plan: CandidatePlan = _PLAN,
    find: Callable[..., tuple[ContradictionBatch, int]] | None = None,
    persist: Callable[..., None] | None = None,
    local_exemption: bool = False,
) -> service.ContradictionsPorts:
    def _find(bundle_dir: Path, **kwargs: Any) -> tuple[ContradictionBatch, int]:
        calls.find_kwargs = kwargs
        judged = [_verdict() for _ in kwargs["plan"].specs]
        return ContradictionBatch(results=judged), len(judged)

    def _persist(
        layout: config.WorkspaceLayout,
        plan: CandidatePlan,
        verdicts: Sequence[ContradictionVerdict],
    ) -> None:
        calls.persisted.append((plan, list(verdicts)))

    def _zero(layout: config.WorkspaceLayout, store: object, missing: bool) -> str:
        calls.zero_state_args.append((layout.root, missing))
        return "zero-state text"

    return service.ContradictionsPorts(
        chat_client=lambda cfg: _Client(),
        local_exemption=lambda client, cfg: local_exemption,
        open_proximity=lambda path: None,
        build_graph=build_graph,
        plan_candidates=lambda *a, **k: plan,
        find_contradictions=find or _find,
        persist_findings=persist or _persist,
        finding_input_digests=lambda bundle_dir, spec: _DIGESTS,
        zero_state_message=_zero,
    )


def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ws"
    (root / "bundle").mkdir(parents=True)
    (root / "bundle" / "index.md").write_text("# Index\n", encoding="utf-8")
    (root / "bundle" / "log.md").write_text("# Log\n", encoding="utf-8")
    (root / "openkos.yaml").write_text("model: llama3.1\n", encoding="utf-8")
    other = tmp_path / "unrelated-cwd"
    other.mkdir()
    monkeypatch.chdir(other)
    return root


def _run(
    root: Path,
    ports: service.ContradictionsPorts,
    observer: _Observer | None = None,
    **options: bool,
) -> service.ContradictionsOutcome:
    return service.run_contradictions(
        root,
        options=service.ContradictionsOptions(**options),
        ports=ports,
        observer=observer or _Observer(),
    )


def _persist_row(root: Path, *, verdict: str = "contradicts") -> None:
    conn = derived.open_derived_connection(root / ".openkos" / "findings.db")
    try:
        findings.record_findings(
            conn,
            [
                findings.Finding(
                    pair_ids=("concepts/a", "concepts/b"),
                    merged_absorbed_id=None,
                    verdict=verdict,
                    confidence=0.77,
                    rationale="stored rationale",
                    conflicting_claims=("stored claim",),
                    input_digests=_DIGESTS,
                )
            ],
        )
    finally:
        conn.close()


def test_a_non_workspace_root_is_refused_with_the_exact_text(tmp_path: Path) -> None:
    reason = config.require_workspace(tmp_path)
    expected = f"openkos contradictions: refusing to run -- {reason}."

    with pytest.raises(service.NotAWorkspace) as run:
        _run(tmp_path, _ports(_Calls()))
    with pytest.raises(service.NotAWorkspace) as ruling:
        service.record_contradiction_decision(
            tmp_path, ("a", "b"), None, target_state="declined"
        )
    with pytest.raises(service.NotAWorkspace) as listing:
        service.list_declined(tmp_path)

    assert run.value.message == expected
    assert ruling.value.message == expected
    assert listing.value.message == expected


def test_an_unreadable_config_is_refused_before_any_client_is_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    (root / "openkos.yaml").write_text("model: [unclosed\n", encoding="utf-8")
    ports = _ports(_Calls())

    def _no_client(cfg: config.Config) -> LLMBackend:
        raise AssertionError("the client must not be built for an unreadable config")

    with pytest.raises(service.WorkspaceUnreadable) as caught:
        _run(root, dataclasses.replace(ports, chat_client=_no_client))
    assert caught.value.message.startswith(
        "openkos contradictions: failed while reading the workspace -- "
    )


def test_a_fresh_run_judges_the_plan_persists_and_reports_the_split(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    calls = _Calls()
    observer = _Observer()

    outcome = _run(
        root,
        _ports(calls, local_exemption=True),
        observer,
        include_deprecated=True,
        include_confidential=True,
    )

    assert observer.events == ["exemption_resolved", "progress_callback"]
    assert observer.exemptions == [True]
    assert calls.find_kwargs["plan"] is _PLAN
    assert calls.find_kwargs["include_deprecated"] is True
    assert calls.find_kwargs["include_confidential"] is True
    assert calls.find_kwargs["local_exemption"] is True
    assert outcome.model == "llama3.1"
    assert (outcome.served_count, outcome.fresh_count) == (0, 1)
    assert [v.rationale for v in outcome.displayed] == ["because"]
    assert outcome.zero_state is None
    assert len(calls.persisted) == 1
    persisted_plan, persisted_verdicts = calls.persisted[0]
    assert persisted_plan is _PLAN
    assert persisted_verdicts == list(outcome.verdicts)


def test_a_digest_fresh_persisted_verdict_is_served_without_a_model_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    _persist_row(root)
    calls = _Calls()

    outcome = _run(root, _ports(calls))

    assert calls.find_kwargs["plan"].specs == ()
    assert calls.find_kwargs["plan"].edge_total == 1, (
        "totals describe the ORIGINAL plan"
    )
    assert (outcome.served_count, outcome.fresh_count) == (1, 0)
    (served,) = outcome.verdicts
    assert (served.rationale, served.confidence, served.conflicting_claims) == (
        "stored rationale",
        0.77,
        ("stored claim",),
    )
    assert calls.persisted == [], "a fully served run persists nothing"


def test_fresh_bypasses_the_store_entirely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    _persist_row(root)
    calls = _Calls()

    outcome = _run(root, _ports(calls), fresh=True)

    assert calls.find_kwargs["plan"] == _PLAN
    assert (outcome.served_count, outcome.fresh_count) == (0, 1)


@pytest.mark.parametrize(
    ("stored_verdict", "digests_match"),
    [("contradicts", False), ("mystery", True)],
)
def test_drifted_or_unrecognized_rows_re_judge(
    stored_verdict: str,
    digests_match: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    _persist_row(root, verdict=stored_verdict)
    calls = _Calls()
    ports = _ports(calls)
    if not digests_match:
        ports = dataclasses.replace(
            ports,
            finding_input_digests=lambda bundle_dir, spec: (
                findings.InputDigest("concepts/a", "e" * 64),
            ),
        )

    outcome = _run(root, ports)

    assert calls.find_kwargs["plan"] == _PLAN
    assert (outcome.served_count, outcome.fresh_count) == (0, 1)


def test_a_corrupt_findings_store_degrades_to_judging_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    (root / ".openkos").mkdir()
    (root / ".openkos" / "findings.db").write_bytes(b"not sqlite" * 50)
    calls = _Calls()
    observer = _Observer()

    outcome = _run(root, _ports(calls), observer)

    assert [type(e) for e in observer.unreadable] == [sqlite3.DatabaseError]
    assert observer.events[:3] == [
        "exemption_resolved",
        "persisted_findings_unreadable",
        "progress_callback",
    ]
    assert calls.find_kwargs["plan"] is _PLAN
    assert outcome.fresh_count == 1


def test_a_persist_failure_is_reported_and_the_verdicts_are_still_returned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    boom = OSError("disk full")

    def _broken(*args: object) -> None:
        raise boom

    observer = _Observer()
    outcome = _run(root, _ports(_Calls(), persist=_broken), observer)

    assert observer.persist_errors == [boom]
    assert [v.rationale for v in outcome.displayed] == ["because"]


def test_the_vacuous_advisory_is_raised_before_the_model_is_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    calls = _Calls()
    observer = _Observer()
    merged_only = _vacuous_plan(2)
    find_seen_events: list[list[str]] = []

    def _find(bundle_dir: Path, **kwargs: Any) -> tuple[ContradictionBatch, int]:
        find_seen_events.append(list(observer.events))
        return ContradictionBatch(results=[]), 0

    outcome = _run(root, _ports(calls, plan=merged_only, find=_find), observer)

    assert observer.vacuous == [outcome.vacuous_notice]
    assert outcome.vacuous_notice is not None
    assert "vacuous_coverage" in find_seen_events[0]


def test_a_clean_run_with_no_verdicts_ends_at_the_zero_state_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    calls = _Calls()
    empty = CandidatePlan(specs=(), edge_total=0, merged_total=0)

    def _find(bundle_dir: Path, **kwargs: Any) -> tuple[ContradictionBatch, int]:
        return ContradictionBatch(results=[]), 0

    outcome = _run(root, _ports(calls, plan=empty, find=_find))

    assert outcome.zero_state == "zero-state text"
    assert calls.zero_state_args == [(root, True)], "no proximity source -> missing"
    assert outcome.displayed == ()
    assert outcome.truncation_notice is None


def test_a_first_candidate_failure_is_not_reported_as_an_empty_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    failure = BackendUnavailable("down")

    def _find(bundle_dir: Path, **kwargs: Any) -> tuple[ContradictionBatch, int]:
        return ContradictionBatch(results=[], failure=failure, failed_index=1), 1

    calls = _Calls()
    outcome = _run(root, _ports(calls, find=_find))

    assert outcome.zero_state is None
    assert calls.zero_state_args == []
    assert outcome.batch.failure is failure


def test_low_confidence_and_declined_verdicts_are_hidden_unless_all_is_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    service.record_contradiction_decision(
        root, ("concepts/b", "concepts/a"), None, target_state="declined"
    )

    def _find(bundle_dir: Path, **kwargs: Any) -> tuple[ContradictionBatch, int]:
        return ContradictionBatch(
            results=[_verdict(verdict=Verdict.CONSISTENT, confidence=0.99)]
        ), 1

    hidden = _run(root, _ports(_Calls(), find=_find))
    everything = _run(root, _ports(_Calls(), find=_find), show_all=True)

    assert hidden.displayed == ()
    assert len(hidden.verdicts) == 1, "what was JUDGED still counts"
    assert everything.displayed == (), "a declined verdict stays hidden even with --all"


@pytest.mark.parametrize(
    ("raised", "refusal", "message"),
    [
        (
            BackendUnavailable("nobody home"),
            service.BackendNotReachable,
            "openkos contradictions: failed -- nobody home. Start it with "
            "`ollama serve`, then try again. Or run `openkos doctor` to "
            "diagnose the environment.",
        ),
        (
            BackendModelNotFound("gone"),
            service.ModelNotInstalled,
            "openkos contradictions: failed -- model 'llama3.1' is not "
            "installed. Pull it with `ollama pull llama3.1`, then try again.",
        ),
        (
            BackendError("boom"),
            service.BackendFailed,
            "openkos contradictions: failed -- boom.",
        ),
    ],
)
def test_the_ordered_backend_ladder_carries_the_exact_messages(
    raised: Exception,
    refusal: type[service.ContradictionsRefused],
    message: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(tmp_path, monkeypatch)

    def _find(bundle_dir: Path, **kwargs: Any) -> tuple[ContradictionBatch, int]:
        raise raised

    calls = _Calls()
    with pytest.raises(refusal) as caught:
        _run(root, _ports(calls, find=_find))

    assert type(caught.value) is refusal
    assert caught.value.message == message
    assert calls.persisted == []


def test_decisions_are_canonical_clocked_and_never_read_the_findings_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    stamp = datetime(2031, 4, 5, 6, 7, 8, tzinfo=UTC)

    ruling = service.record_contradiction_decision(
        root,
        ("concepts/b", "concepts/a"),
        "concepts/z",
        target_state="declined",
        clock=lambda: stamp,
    )

    assert ruling.pair == ("concepts/a", "concepts/b")
    assert ruling.merged_absorbed_id == "concepts/z"
    assert ruling.rel_path == "bundle/.state/decisions/concepts/a.decisions.okf"
    (record,) = bundle_decisions.read_decisions("concepts/a", root / "bundle")
    assert (record.pair_ids, record.merged_absorbed_id, record.state) == (
        ("concepts/a", "concepts/b"),
        "concepts/z",
        "declined",
    )
    assert record.decided_at == stamp.isoformat()
    assert not (root / ".openkos" / "findings.db").exists()


def test_the_declined_listing_joins_findings_and_marks_nothing_it_cannot_see(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    assert service.list_declined(root) == ()
    assert not (root / ".openkos" / "findings.db").exists(), (
        "an empty listing never opens the store"
    )
    service.record_contradiction_decision(
        root, ("concepts/a", "concepts/b"), None, target_state="declined"
    )
    service.record_contradiction_decision(
        root, ("concepts/c", "concepts/d"), None, target_state="declined"
    )
    service.record_contradiction_decision(
        root, ("concepts/e", "concepts/f"), None, target_state="open"
    )
    _persist_row(root)

    entries = service.list_declined(root)

    assert [e.record.pair_ids for e in entries] == sorted(
        [("concepts/a", "concepts/b"), ("concepts/c", "concepts/d")],
        key=lambda pair: bundle_decisions.decision_key_for(pair, None),
    )
    by_pair = {e.record.pair_ids: e.finding for e in entries}
    joined = by_pair[("concepts/a", "concepts/b")]
    assert joined is not None
    assert joined.rationale == "stored rationale"
    assert by_pair[("concepts/c", "concepts/d")] is None
