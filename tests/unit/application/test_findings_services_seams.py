"""Absence guards and service-level tests for the `suggest-relations`,
`suggest-volatility` and `revisions` application-service extraction (issue
#1168), the `test_ingest_service_seams.py` pattern.

The names that moved off `openkos.cli.main` are deliberately never aliased
back, so a stale `monkeypatch.setattr("openkos.cli.main.<name>", ...)` raises
`AttributeError` under pytest's default `raising=True` instead of silently
patching a name nothing reads. The services are driven here with explicit
roots, fake ports and recording observers -- no CLI, no current directory.
"""

import inspect
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import pytest

from openkos import config
from openkos.application import revisions as revisions_service
from openkos.application import suggest_relations_service as relations
from openkos.application import suggest_volatility_service as volatility
from openkos.cli import main as cli_main
from openkos.graph.base import Edge
from openkos.llm.base import BackendModelNotFound, BackendUnavailable
from openkos.resolution.edge_typing import (
    LEAST_SPECIFIC_RELATION_TYPE,
    EdgeSuggestion,
    EdgeSuggestionBatch,
)
from openkos.resolution.volatility_typing import TierSuggestion, TierSuggestionBatch
from tests.unit.conftest import LOCAL_BACKEND_LOCALITY

_MOVED_OFF_CLI_MAIN = [
    "_echo_suggest_relations_batch_failure",
    "_echo_suggest_volatility_batch_failure",
    "_echo_revisions_batch_failure",
    "_run_suggest_relations_apply",
    "_REVISIONS_EXPERIMENTAL_NOTICE",
    "_REVISIONS_NO_VECTORS_MESSAGE",
    "_REVISIONS_MODEL_MISMATCH_MESSAGE",
]


@pytest.mark.parametrize("name", _MOVED_OFF_CLI_MAIN)
def test_moved_names_no_longer_live_on_cli_main(name: str) -> None:
    assert not hasattr(cli_main, name)


def test_the_moved_names_live_in_the_application_layer() -> None:
    assert callable(relations.suggest_relations)
    assert callable(relations.apply_relation_suggestions)
    assert callable(volatility.suggest_volatility_tiers)
    assert callable(revisions_service.run_revisions)
    assert issubclass(relations.DriftDetected, relations.SuggestionRefused)
    assert issubclass(
        revisions_service.ConfirmationUnavailable, revisions_service.RevisionsRefused
    )
    assert revisions_service.EXPERIMENTAL_NOTICE.startswith("openkos revisions:")


def test_shared_helpers_kept_on_cli_main_are_the_service_ones() -> None:
    """`curate` and the existing tests reach these through `cli.main`; they
    must stay the ONE definition, never a fork."""
    assert cli_main._suggestion_caveat is relations.suggestion_caveat
    assert cli_main.EdgeSuggestionServes is relations.EdgeSuggestionServes


@pytest.mark.parametrize(
    ("command", "service_call"),
    [
        (cli_main.suggest_relations_cmd, "relations_service.suggest_relations("),
        (
            cli_main.suggest_volatility_cmd,
            "volatility_service.suggest_volatility_tiers(",
        ),
        (cli_main.revisions, "revisions_service.run_revisions("),
    ],
)
def test_the_verbs_are_thin_adapters_that_delegate_to_the_service(
    command: Any, service_call: str
) -> None:
    source = inspect.getsource(command)
    assert service_call in source
    assert "config.require_workspace" not in source
    assert "config.read_config" not in source
    assert "sqlite3" not in source


# -- Fixtures ---------------------------------------------------------------


class _Llm:
    locality = LOCAL_BACKEND_LOCALITY


def _workspace(tmp_path: Path, extra_yaml: str = "") -> Path:
    (tmp_path / "bundle").mkdir()
    (tmp_path / "bundle" / "index.md").write_text("# Index\n", encoding="utf-8")
    (tmp_path / "bundle" / "log.md").write_text("# Log\n", encoding="utf-8")
    (tmp_path / "openkos.yaml").write_text(
        "model: llama3.1\n" + extra_yaml, encoding="utf-8"
    )
    return tmp_path


def _suggestion(
    source: str = "concepts/a",
    target: str = "concepts/b",
    kind: str | None = "references",
) -> EdgeSuggestion:
    return EdgeSuggestion(
        edge=Edge(source_id=source, target_id=target),
        suggested_type=kind,
        rationale="because",
    )


class _RelationsRecorder:
    """A `SuggestRelationsObserver` recording the order of what it was told."""

    def __init__(self, *, proceed: bool = True) -> None:
        self.events: list[str] = []
        self.proceed = proceed
        self.quote: relations.CostQuote | None = None

    def walk_incomplete(self, bundle_dir: Path, **kwargs: object) -> None:
        self.events.append("walk_incomplete")

    def workspace_header(self, root: Path) -> None:
        self.events.append("header")

    def empty_window(self, edge_offset: int) -> None:
        self.events.append(f"empty_window:{edge_offset}")

    def candidate_notices(self, truncation: object, quarantine: object) -> None:
        self.events.append("notices")

    def no_candidates(self, message: str) -> None:
        self.events.append(f"no_candidates:{message}")

    def warn(self, message: str) -> None:
        self.events.append(f"warn:{message}")

    def serve_split(self, served: int, total: int, fresh: int) -> None:
        self.events.append(f"split:{served}/{total}/{fresh}")

    def confirm_cost(self, quote: relations.CostQuote) -> bool:
        self.quote = quote
        self.events.append("confirm_cost")
        return self.proceed

    def edge_progress(self, index: int, count: int, suggestion: object) -> None:
        self.events.append(f"progress:{index}/{count}")


def _relations_ports(
    edges: list[Edge],
    typed: Any,
    *,
    tmp_calls: list[Any] | None = None,
) -> relations.SuggestRelationsPorts:
    def _suggest(to_type: Sequence[Edge], **kwargs: Any) -> EdgeSuggestionBatch:
        if tmp_calls is not None:
            tmp_calls.append(([e.source_id for e in to_type], kwargs))
        result: EdgeSuggestionBatch = typed(to_type, kwargs)
        return result

    return relations.SuggestRelationsPorts(
        chat_client=lambda cfg, task: _Llm(),  # type: ignore[arg-type, return-value]
        zero_state_message=lambda layout, store, missing: f"zero:{missing}",
        autocommit=lambda root, paths, message: None,
        refresh_derived=lambda layout: None,
        resolve_local_exemption=lambda client, cfg: False,
        open_proximity=lambda path: None,
        candidate_edges=lambda bundle_dir, **kwargs: edges,
        suggest_edge_types=_suggest,
    )


# -- suggest-relations ------------------------------------------------------


def test_relations_refuses_a_non_workspace_with_the_exact_text(tmp_path: Path) -> None:
    with pytest.raises(relations.NotAWorkspace) as caught:
        relations.suggest_relations(
            tmp_path,
            relations.SuggestRelationsRequest(),
            _relations_ports([], None),
            _RelationsRecorder(),
        )
    reason = config.require_workspace(tmp_path)
    assert caught.value.message == (
        f"openkos suggest-relations: refusing to run -- {reason}."
    )


def test_relations_reports_an_unreadable_config_with_the_exact_text(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    (root / "openkos.yaml").write_text("model: [unclosed\n", encoding="utf-8")
    with pytest.raises(relations.WorkspaceUnreadable) as caught:
        relations.suggest_relations(
            root,
            relations.SuggestRelationsRequest(),
            _relations_ports([], None),
            _RelationsRecorder(),
        )
    assert caught.value.message.startswith(
        "openkos suggest-relations: failed while reading the workspace -- "
    )


def test_relations_runs_against_the_explicit_root_not_the_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "ws").mkdir()
    root = _workspace(tmp_path / "ws")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    seen: list[Any] = []
    edge = Edge(source_id="concepts/a", target_id="concepts/b")

    def _typed(to_type: Sequence[Edge], kwargs: dict[str, Any]) -> EdgeSuggestionBatch:
        return EdgeSuggestionBatch(results=[_suggestion()])

    recorder = _RelationsRecorder()
    outcome = relations.suggest_relations(
        root,
        relations.SuggestRelationsRequest(skip_confirmation=True),
        _relations_ports([edge], _typed, tmp_calls=seen),
        recorder,
    )

    assert outcome.status == "completed"
    assert outcome.total == 1
    assert [r.suggested_type for r in outcome.results] == ["references"]
    assert seen[0][1]["bundle_dir"] == root / "bundle"
    assert recorder.events == ["walk_incomplete", "header", "notices"]


def test_relations_declining_the_cost_gate_types_nothing_and_quotes_the_spend(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    seen: list[Any] = []
    edges = [Edge("concepts/a", "concepts/b"), Edge("concepts/c", "concepts/d")]
    recorder = _RelationsRecorder(proceed=False)

    outcome = relations.suggest_relations(
        root,
        relations.SuggestRelationsRequest(),
        _relations_ports(
            edges, lambda *a: EdgeSuggestionBatch(results=[]), tmp_calls=seen
        ),
        recorder,
    )

    assert outcome.status == "declined"
    assert seen == []
    assert recorder.quote == relations.CostQuote(total=2, served=0, to_type=2)


def test_relations_zero_candidates_ask_the_state_message_port(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    recorder = _RelationsRecorder()
    outcome = relations.suggest_relations(
        root,
        relations.SuggestRelationsRequest(),
        _relations_ports([], None),
        recorder,
    )
    assert outcome.status == "no_candidates"
    assert "no_candidates:zero:True" in recorder.events


def test_relations_an_offset_past_the_set_is_an_empty_window(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    recorder = _RelationsRecorder()
    outcome = relations.suggest_relations(
        root,
        relations.SuggestRelationsRequest(edge_offset=7),
        _relations_ports([], None),
        recorder,
    )
    assert outcome.status == "empty_window"
    assert "empty_window:7" in recorder.events


def test_relations_backend_raise_path_is_a_typed_refusal_with_the_exact_text(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    edge = Edge("concepts/a", "concepts/b")

    def _unavailable(to_type: Sequence[Edge], kwargs: dict[str, Any]) -> None:
        raise BackendUnavailable("nobody home")

    with pytest.raises(relations.BackendNotReachable) as caught:
        relations.suggest_relations(
            root,
            relations.SuggestRelationsRequest(skip_confirmation=True),
            _relations_ports([edge], _unavailable),
            _RelationsRecorder(),
        )
    assert caught.value.message == (
        "openkos suggest-relations: failed -- nobody home. Start it with "
        "`ollama serve`, then try again. Or run `openkos doctor` to diagnose "
        "the environment."
    )

    def _missing(to_type: Sequence[Edge], kwargs: dict[str, Any]) -> None:
        raise BackendModelNotFound("nope")

    with pytest.raises(relations.ModelNotInstalled) as missing:
        relations.suggest_relations(
            root,
            relations.SuggestRelationsRequest(skip_confirmation=True),
            _relations_ports([edge], _missing),
            _RelationsRecorder(),
        )
    assert missing.value.message == (
        "openkos suggest-relations: failed -- model 'llama3.1' is not installed. "
        "Pull it with `ollama pull llama3.1`, then try again."
    )


def test_relations_a_partial_batch_is_carried_on_the_outcome_not_raised(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    failure = BackendUnavailable("gone")
    batch = EdgeSuggestionBatch(
        results=[_suggestion()], failure=failure, failed_index=2
    )
    outcome = relations.suggest_relations(
        root,
        relations.SuggestRelationsRequest(skip_confirmation=True),
        _relations_ports(
            [Edge("concepts/a", "concepts/b"), Edge("concepts/c", "concepts/d")],
            lambda *a: batch,
        ),
        _RelationsRecorder(),
    )
    assert outcome.failure is failure
    assert len(outcome.results) == 1
    assert relations.relations_batch_failure_message(
        batch, total=outcome.total, model=outcome.model
    ) == (
        "openkos suggest-relations: failed after suggesting 1 of 2 untyped "
        "edge(s) -- gone. Start it with `ollama serve`, then try again. Or "
        "run `openkos doctor` to diagnose the environment."
    )


def test_the_caveat_classes_are_disjoint() -> None:
    from openkos.model.relations import ASYMMETRIC_RELATION_TYPES

    assert LEAST_SPECIFIC_RELATION_TYPE not in ASYMMETRIC_RELATION_TYPES
    assert relations.suggestion_caveat("references") == ""


class _ApplyRecorder:
    def __init__(self, answers: list[bool]) -> None:
        self.answers = answers
        self.events: list[str] = []
        self.summary_seen: relations.ApplyOutcome | None = None

    def degraded(self, edge: Edge) -> None:
        self.events.append(f"degraded:{edge.source_id}")

    def preview(
        self, edge: Edge, suggested_type: str, caveat: str, rationale: str
    ) -> None:
        self.events.append(f"preview:{suggested_type}")

    def confirm_relate(
        self, edge: Edge, suggested_type: str, caveat: str
    ) -> Literal["yes", "no", "skip"]:
        self.events.append("confirm")
        return "yes" if self.answers.pop(0) else "no"

    def already_present(self) -> None:
        self.events.append("already_present")

    def summary(self, outcome: relations.ApplyOutcome) -> None:
        self.events.append("summary")
        self.summary_seen = outcome


def _apply_workspace(tmp_path: Path) -> Path:
    root = _workspace(tmp_path)
    concepts = root / "bundle" / "concepts"
    concepts.mkdir()
    for name in ("a", "b"):
        (concepts / f"{name}.md").write_text(
            f"---\ntype: Concept\ntitle: {name.upper()}\n---\n# {name.upper()}\n",
            encoding="utf-8",
        )
    return root


def test_apply_commits_through_the_port_and_refreshes_once_after_the_summary(
    tmp_path: Path,
) -> None:
    root = _apply_workspace(tmp_path)
    commits: list[tuple[Path, list[str], str]] = []
    refreshed: list[str] = []
    observer = _ApplyRecorder([True])
    ports = relations.SuggestRelationsPorts(
        chat_client=lambda cfg, task: _Llm(),  # type: ignore[arg-type, return-value]
        zero_state_message=lambda *a: "",
        autocommit=lambda r, paths, message: commits.append((r, list(paths), message)),
        refresh_derived=lambda layout: refreshed.append(observer.events[-1]),
    )

    outcome = relations.apply_relation_suggestions(
        root, [_suggestion(), _suggestion(kind=None)], ports, observer
    )

    assert (outcome.applied, outcome.skipped, outcome.declined) == (1, 1, ())
    assert commits == [
        (
            root,
            ["bundle/concepts/a.md", "bundle/log.md"],
            "openkos: relate concepts/a -> concepts/b (references)",
        )
    ]
    # The summary is rendered BEFORE the derived stores are refreshed.
    assert refreshed == ["summary"]
    assert "type: references" in (root / "bundle" / "concepts" / "a.md").read_text(
        encoding="utf-8"
    )


def test_apply_a_fully_declined_walk_refreshes_nothing(tmp_path: Path) -> None:
    root = _apply_workspace(tmp_path)
    refreshed: list[str] = []
    ports = relations.SuggestRelationsPorts(
        chat_client=lambda cfg, task: _Llm(),  # type: ignore[arg-type, return-value]
        zero_state_message=lambda *a: "",
        autocommit=lambda *a: pytest.fail("declined items are never committed"),
        refresh_derived=lambda layout: refreshed.append("refresh"),
    )
    outcome = relations.apply_relation_suggestions(
        root, [_suggestion()], ports, _ApplyRecorder([False])
    )
    assert outcome.declined == ("concepts/a -> concepts/b [references]",)
    assert refreshed == []


def test_apply_drift_between_prompt_and_write_is_a_typed_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _apply_workspace(tmp_path)
    from openkos.application import lifecycle

    real = lifecycle.prepare_relate

    def _prepare_then_drift(*args: Any, **kwargs: Any) -> Any:
        prepared = real(*args, **kwargs)
        (root / "bundle" / "concepts" / "a.md").write_text(
            "drifted\n", encoding="utf-8"
        )
        return prepared

    monkeypatch.setattr(lifecycle, "prepare_relate", _prepare_then_drift)
    ports = relations.SuggestRelationsPorts(
        chat_client=lambda cfg, task: _Llm(),  # type: ignore[arg-type, return-value]
        zero_state_message=lambda *a: "",
        autocommit=lambda *a: pytest.fail("a refused write is never committed"),
        refresh_derived=lambda layout: None,
    )
    with pytest.raises(relations.DriftDetected) as caught:
        relations.apply_relation_suggestions(
            root, [_suggestion()], ports, _ApplyRecorder([True])
        )
    assert "refusing to write" in caught.value.message
    assert "bundle/concepts/a.md" in caught.value.message


# -- suggest-volatility -----------------------------------------------------


class _VolatilityRecorder:
    def __init__(self) -> None:
        self.events: list[str] = []

    def walk_incomplete(self, bundle_dir: Path, **kwargs: object) -> None:
        self.events.append("walk_incomplete")

    def progress_callback(self) -> None:
        self.events.append("progress_callback")
        return None


def _volatility_ports(batch: Any) -> volatility.VolatilityPorts:
    def _suggest(bundle_dir: Path, **kwargs: Any) -> TierSuggestionBatch:
        if isinstance(batch, Exception):
            raise batch
        result: TierSuggestionBatch = batch
        return result

    return volatility.VolatilityPorts(
        chat_client=lambda cfg, task: _Llm(),  # type: ignore[arg-type, return-value]
        resolve_local_exemption=lambda client, cfg: False,
        suggest_volatility=_suggest,
    )


def test_volatility_reads_the_explicit_root_and_carries_the_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "ws").mkdir()
    root = _workspace(tmp_path / "ws")
    monkeypatch.chdir(tmp_path)
    tier = TierSuggestion(
        type_name="Person", current_default="slow", suggested_tier="slow", rationale="r"
    )
    recorder = _VolatilityRecorder()
    outcome = volatility.suggest_volatility_tiers(
        root,
        volatility.VolatilityRequest(),
        _volatility_ports(TierSuggestionBatch(results=[tier])),
        recorder,
    )
    assert outcome.results == (tier,)
    assert outcome.failure is None
    assert outcome.model == "llama3.1"
    assert recorder.events == ["walk_incomplete", "progress_callback"]


def test_volatility_refusals_and_raise_path_carry_the_exact_text(
    tmp_path: Path,
) -> None:
    with pytest.raises(relations.NotAWorkspace) as caught:
        volatility.suggest_volatility_tiers(
            tmp_path,
            volatility.VolatilityRequest(),
            _volatility_ports(None),
            _VolatilityRecorder(),
        )
    assert caught.value.message.startswith(
        "openkos suggest-volatility: refusing to run -- "
    )

    (tmp_path / "ws").mkdir()
    root = _workspace(tmp_path / "ws")
    with pytest.raises(relations.BackendNotReachable) as unreachable:
        volatility.suggest_volatility_tiers(
            root,
            volatility.VolatilityRequest(),
            _volatility_ports(BackendUnavailable("down")),
            _VolatilityRecorder(),
        )
    assert unreachable.value.message == (
        "openkos suggest-volatility: failed -- down. Start it with `ollama "
        "serve`, then try again. Or run `openkos doctor` to diagnose the "
        "environment."
    )


# -- revisions ---------------------------------------------------------------


class _RevisionsRecorder:
    def __init__(
        self, answer: revisions_service.ConfirmationAnswer = "proceed"
    ) -> None:
        self.events: list[str] = []
        self.answer = answer

    def started(self) -> None:
        self.events.append("started")

    def truncation_notice(self, notice: str) -> None:
        self.events.append(f"truncation:{notice}")

    def cost_gate(self, plan: Any) -> None:
        self.events.append("cost_gate")

    def confirm_judging(self) -> revisions_service.ConfirmationAnswer:
        self.events.append("confirm")
        return self.answer

    def progress_callback(self) -> None:
        return None


def _fake_plan(store: str, *, to_judge: int) -> Any:
    return SimpleNamespace(
        coverage=SimpleNamespace(store=store),
        candidate_plan=SimpleNamespace(candidates=()),
        served=(),
        to_judge=tuple(object() for _ in range(to_judge)),
    )


def _revisions_ports(notice: str | None = None) -> revisions_service.RevisionsPorts:
    return revisions_service.RevisionsPorts(
        chat_client=lambda cfg, task: _Llm(),  # type: ignore[arg-type, return-value]
        resolve_local_exemption=lambda client, cfg: False,
        truncation_notice=lambda plan: notice,
    )


def _patch_revisions(
    monkeypatch: pytest.MonkeyPatch, plan: Any, judged: list[str]
) -> None:
    monkeypatch.setattr(
        revisions_service,
        "load_decisions",
        lambda layout, **kwargs: revisions_service.DecisionSet(
            decisions=(revisions_service.Decision("decisions/a", frozenset()),),
            bad_relations=0,
        ),
    )
    monkeypatch.setattr(revisions_service, "plan_revisions", lambda *a, **k: plan)

    def _judge(layout: object, plan: object, **kwargs: object) -> Any:
        judged.append("judged")
        return revisions_service.RevisionOutcome(results=())

    monkeypatch.setattr(revisions_service, "judge_revisions", _judge)


def test_revisions_refuses_a_non_workspace_with_the_exact_text(tmp_path: Path) -> None:
    with pytest.raises(revisions_service.NotAWorkspace) as caught:
        revisions_service.run_revisions(
            tmp_path,
            revisions_service.RevisionsRequest(),
            _revisions_ports(),
            _RevisionsRecorder(),
        )
    reason = config.require_workspace(tmp_path)
    assert caught.value.message == f"openkos revisions: refusing to run -- {reason}."


def test_revisions_states_the_notice_then_the_gate_then_judges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    judged: list[str] = []
    _patch_revisions(monkeypatch, _fake_plan("ok", to_judge=2), judged)
    recorder = _RevisionsRecorder()

    run = revisions_service.run_revisions(
        root,
        revisions_service.RevisionsRequest(),
        _revisions_ports("CAPPED"),
        recorder,
    )

    assert recorder.events == ["started", "truncation:CAPPED", "cost_gate", "confirm"]
    assert run.status == "completed"
    assert run.report is not None
    assert judged == ["judged"]


def test_revisions_the_gate_is_stated_even_under_auto_but_never_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    _patch_revisions(monkeypatch, _fake_plan("ok", to_judge=1), [])
    recorder = _RevisionsRecorder()
    revisions_service.run_revisions(
        root,
        revisions_service.RevisionsRequest(skip_confirmation=True),
        _revisions_ports(),
        recorder,
    )
    assert recorder.events == ["started", "cost_gate"]


def test_revisions_a_gate_with_nothing_to_judge_states_and_asks_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    _patch_revisions(monkeypatch, _fake_plan("ok", to_judge=0), [])
    recorder = _RevisionsRecorder()
    run = revisions_service.run_revisions(
        root, revisions_service.RevisionsRequest(), _revisions_ports(), recorder
    )
    assert run.status == "completed"
    assert recorder.events == ["started"]


def test_revisions_declined_judges_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    judged: list[str] = []
    _patch_revisions(monkeypatch, _fake_plan("ok", to_judge=1), judged)
    run = revisions_service.run_revisions(
        root,
        revisions_service.RevisionsRequest(),
        _revisions_ports(),
        _RevisionsRecorder("declined"),
    )
    assert run.status == "declined"
    assert judged == []


def test_revisions_an_unaskable_gate_is_a_typed_refusal_with_the_exact_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    judged: list[str] = []
    _patch_revisions(monkeypatch, _fake_plan("ok", to_judge=1), judged)
    with pytest.raises(revisions_service.ConfirmationUnavailable) as caught:
        revisions_service.run_revisions(
            root,
            revisions_service.RevisionsRequest(),
            _revisions_ports(),
            _RevisionsRecorder("unavailable"),
        )
    assert caught.value.message == (
        "openkos revisions: refusing to spend model calls without "
        "confirmation -- stdin is not a TTY; re-run with --auto."
    )
    assert judged == []


@pytest.mark.parametrize(
    ("store", "status"),
    [("absent", "vectors_absent"), ("model-mismatch", "model_mismatch")],
)
def test_revisions_a_degraded_vector_store_reaches_neither_gate_nor_judge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store: str, status: str
) -> None:
    root = _workspace(tmp_path)
    judged: list[str] = []
    _patch_revisions(monkeypatch, _fake_plan(store, to_judge=3), judged)
    recorder = _RevisionsRecorder()
    run = revisions_service.run_revisions(
        root, revisions_service.RevisionsRequest(), _revisions_ports("X"), recorder
    )
    assert run.status == status
    assert recorder.events == ["started"]
    assert judged == []


def test_revisions_batch_failure_message_keeps_the_three_tiers(
    tmp_path: Path,
) -> None:
    def _outcome(failure: Exception) -> Any:
        return revisions_service.RevisionOutcome(results=(), failure=failure)  # type: ignore[arg-type]

    assert revisions_service.revisions_batch_failure_message(
        _outcome(BackendModelNotFound("x")), total=3, model="m"
    ) == (
        "openkos revisions: failed after judging 0 of 3 planned pair(s) -- "
        "model 'm' is not installed. Pull it with `ollama pull m`, then try again."
    )


def test_revisions_batch_failure_message_words_the_configured_backend(
    tmp_path: Path,
) -> None:
    import dataclasses

    config.write_config(tmp_path)
    cfg = dataclasses.replace(
        config.read_config(tmp_path),
        backend="openai-compatible",
        base_url="http://127.0.0.1:1/v1",
        model="m",
    )

    def _outcome(failure: Exception) -> Any:
        return revisions_service.RevisionOutcome(results=(), failure=failure)  # type: ignore[arg-type]

    unavailable = revisions_service.revisions_batch_failure_message(
        _outcome(BackendUnavailable("gone")), total=3, model="m", cfg=cfg
    )
    assert "Start your OpenAI-compatible server at 127.0.0.1:1" in unavailable
    assert "ollama" not in unavailable
    missing = revisions_service.revisions_batch_failure_message(
        _outcome(BackendModelNotFound("x")), total=3, model="m", cfg=cfg
    )
    assert "Make sure your OpenAI-compatible server serves 'm'" in missing
    assert "ollama" not in missing
