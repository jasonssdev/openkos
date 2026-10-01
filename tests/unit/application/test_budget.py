"""Unit tests for `application/budget.py` (MVP 4 unit 5.2, issue #1140).

The budget counts CHAT calls (retries and re-asks included, embeddings never),
admits a unit of work by its ESTIMATE, sums the day's spend from `jobs.db`,
and is installed ONLY by the job runner: no CLI or MCP path may reach it.
"""

import ast
import threading
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import pytest

from openkos import config
from openkos.application import budget
from openkos.application import contradictions_service as service
from openkos.graph.sqlite_graph import build_graph
from openkos.llm.base import BackendUnavailable, LLMBackend, Message
from openkos.resolution.contradiction import (
    CandidatePlan,
    ContradictionBatch,
    ContradictionVerdict,
    Verdict,
    _CandidateSpec,
)
from openkos.state import derived, findings, jobs
from tests.unit.conftest import LOCAL_BACKEND_LOCALITY

_NOW = datetime(2026, 9, 30, 15, 0, tzinfo=UTC)
_REPO_SRC = Path(__file__).resolve().parents[3] / "src" / "openkos"


class _Inner:
    """A structural backend that records every call it receives."""

    locality = LOCAL_BACKEND_LOCALITY

    def __init__(self, *, fail_first: int = 0) -> None:
        self.chats: list[Sequence[Message]] = []
        self.embeds: list[Sequence[str]] = []
        self._fail_first = fail_first
        self.model = "inner-model"

    def chat(self, messages: Sequence[Message]) -> str:
        self.chats.append(messages)
        if len(self.chats) <= self._fail_first:
            raise BackendUnavailable("transient")
        return "reply"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.embeds.append(texts)
        return [[0.0] for _ in texts]


def _layout(tmp_path: Path) -> config.WorkspaceLayout:
    root = tmp_path / "ws"
    (root / ".openkos").mkdir(parents=True)
    return config.WorkspaceLayout(root)


def _record_job(layout: config.WorkspaceLayout, started_at: str, calls: int) -> None:
    conn = jobs.open_jobs(layout.openkos_dir / "jobs.db")
    try:
        job_id = jobs.start_job(conn, "watch", started_at)
        jobs.finish_job(
            conn,
            job_id,
            outcome="completed",
            ended_at=started_at,
            chat_calls=calls,
            units_done=1,
            units_deferred=0,
        )
    finally:
        conn.close()


def _unattended(**kw: Any) -> config.UnattendedConfig:
    return config.UnattendedConfig(**kw)


# -- CountingBackend --------------------------------------------------------


def test_every_chat_call_is_counted_including_retries_and_failed_calls() -> None:
    inner = _Inner(fail_first=1)
    counter = budget.CallCounter()
    client = budget.CountingBackend(inner, counter)

    with pytest.raises(BackendUnavailable):
        client.chat([{"role": "user", "content": "q"}])
    assert counter.count == 1, "a call that raised was still issued and spent"

    assert client.chat([{"role": "user", "content": "q"}]) == "reply"
    assert client.chat([{"role": "user", "content": "re-ask"}]) == "reply"
    assert counter.count == 3
    assert len(inner.chats) == 3, "the wrapper forwards every call, one for one"
    assert inner.chats[2] == [{"role": "user", "content": "re-ask"}]


def test_embeddings_are_forwarded_but_never_counted() -> None:
    inner = _Inner()
    counter = budget.CallCounter()
    client = budget.CountingBackend(inner, counter)

    assert client.embed(["a", "b"]) == [[0.0], [0.0]]
    assert inner.embeds == [["a", "b"]]
    assert counter.count == 0


def test_locality_and_other_attributes_are_forwarded() -> None:
    inner = _Inner()
    client = budget.CountingBackend(inner, budget.CallCounter())

    assert client.locality is LOCAL_BACKEND_LOCALITY
    assert client.model == "inner-model"


def test_the_counter_is_exact_under_concurrent_chat_calls() -> None:
    counter = budget.CallCounter()
    client = budget.CountingBackend(_Inner(), counter)

    def hammer() -> None:
        for _ in range(250):
            client.chat([])

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert counter.count == 2000


def test_two_wrapped_clients_share_one_counter_per_run(tmp_path: Path) -> None:
    run = budget.start_budgeted_run(_layout(tmp_path), _unattended(), _NOW)
    first = run.counting(_Inner())
    second = run.counting(_Inner())

    first.chat([])
    second.chat([])
    second.chat([])

    assert run.spent == 3


def test_wrap_chat_client_wraps_a_port_of_either_arity(tmp_path: Path) -> None:
    run = budget.start_budgeted_run(_layout(tmp_path), _unattended(), _NOW)
    inner = _Inner()
    one_arg = run.wrap_chat_client(lambda cfg: inner)
    two_arg = run.wrap_chat_client(lambda cfg, task: inner)

    one_arg(object()).chat([])
    two_arg(object(), "edge_typing").chat([])

    assert run.spent == 2


# -- The daily sum from jobs.db ---------------------------------------------


def test_an_absent_job_record_means_zero_spent_and_is_not_created(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)

    run = budget.start_budgeted_run(
        layout, _unattended(max_calls_per_pass=100, max_calls_per_day=50), _NOW
    )

    assert run.spent_today_at_start == 0
    assert run.remaining == 50
    assert not (layout.openkos_dir / "jobs.db").exists(), "a read, never a create"


def test_two_passes_share_one_days_budget(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _record_job(layout, "2026-09-30T09:00:00Z", 40)

    run = budget.start_budgeted_run(
        layout, _unattended(max_calls_per_pass=100, max_calls_per_day=50), _NOW
    )

    assert run.spent_today_at_start == 40
    assert run.remaining == 10, "the pass limit is 100 but the day leaves 10"
    assert run.admit(10).admitted
    assert not run.admit(11).admitted


def test_only_jobs_started_on_the_local_day_count(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _record_job(layout, "2026-09-29T23:59:59Z", 77)  # yesterday (UTC)
    _record_job(layout, "2026-09-30T00:00:00Z", 5)
    _record_job(layout, "2026-09-30T12:00:00Z", 6)

    run = budget.start_budgeted_run(layout, _unattended(), _NOW)

    assert run.spent_today_at_start == 11


def test_the_day_boundary_is_local_midnight_not_utc_midnight(tmp_path: Path) -> None:
    plus_five = timezone(timedelta(hours=5))
    now = datetime(2026, 9, 30, 1, 0, tzinfo=plus_five)  # 2026-09-29T20:00Z
    assert budget.local_day_start_utc(now) == "2026-09-29T19:00:00Z"
    layout = _layout(tmp_path)
    _record_job(layout, "2026-09-29T18:59:59Z", 9)  # 23:59:59 local, yesterday
    _record_job(layout, "2026-09-29T19:00:00Z", 4)  # 00:00:00 local, today

    run = budget.start_budgeted_run(layout, _unattended(), now)

    assert run.spent_today_at_start == 4


def test_a_naive_clock_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone"):
        budget.local_day_start_utc(
            datetime(2026, 9, 30, 1, 0)  # noqa: DTZ001 -- naive on purpose
        )


def test_an_unreadable_job_record_fails_closed_before_any_client_exists(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)
    record = layout.openkos_dir / "jobs.db"
    record.write_bytes(b"this is not a sqlite database" * 50)
    before = record.read_bytes()
    inner = _Inner()

    with pytest.raises(budget.SpendRecordUnreadable) as caught:
        budget.start_budgeted_run(layout, _unattended(), _NOW)

    assert inner.chats == [], "no model call was possible: no run object exists"
    assert str(record) in str(caught.value)
    assert "delete" in str(caught.value)
    assert isinstance(caught.value, budget.BudgetRefused)
    assert record.read_bytes() == before, "never repaired or deleted for the user"


# -- Admission by estimate --------------------------------------------------


def test_a_source_over_the_pass_budget_is_refused_and_flagged_unrunnable(
    tmp_path: Path,
) -> None:
    run = budget.start_budgeted_run(
        _layout(tmp_path), _unattended(max_calls_per_pass=20), _NOW
    )

    admission = run.admit(25)

    assert not admission.admitted
    assert admission.limit == "max_calls_per_pass"
    assert admission.never_fits is True, "it can never run unattended"


def test_a_source_that_fits_the_pass_but_not_what_remains_is_deferred_not_refused(
    tmp_path: Path,
) -> None:
    run = budget.start_budgeted_run(
        _layout(tmp_path), _unattended(max_calls_per_pass=20), _NOW
    )
    inner = run.counting(_Inner())
    for _ in range(15):
        inner.chat([])

    admission = run.admit(10)

    assert not admission.admitted
    assert admission.limit == "max_calls_per_pass"
    assert admission.never_fits is False, "a later pass may run it"
    assert run.admit(5).admitted


def test_the_day_limit_names_itself_when_it_is_the_binding_one(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)
    _record_job(layout, "2026-09-30T09:00:00Z", 45)
    run = budget.start_budgeted_run(
        layout, _unattended(max_calls_per_pass=100, max_calls_per_day=50), _NOW
    )

    admission = run.admit(6)

    assert not admission.admitted
    assert admission.limit == "max_calls_per_day"
    assert admission.never_fits is False


def test_admission_is_by_estimate_so_a_zero_estimate_unit_is_always_admitted(
    tmp_path: Path,
) -> None:
    run = budget.start_budgeted_run(
        _layout(tmp_path), _unattended(max_calls_per_pass=0), _NOW
    )

    assert run.remaining == 0
    assert run.admit(0).admitted
    assert not run.admit(1).admitted


def test_remaining_never_goes_negative_after_an_overrun(tmp_path: Path) -> None:
    run = budget.start_budgeted_run(
        _layout(tmp_path), _unattended(max_calls_per_pass=2), _NOW
    )
    client = run.counting(_Inner())
    for _ in range(5):  # retry slack overruns the estimate
        client.chat([])

    assert run.spent == 5, "accounting is by the calls actually made"
    assert run.remaining == 0


# -- A truncated advisor stage resumes with zero repeated calls --------------


def _specs(n: int) -> tuple[_CandidateSpec, ...]:
    return tuple(
        _CandidateSpec(
            pair_ids=(f"concepts/a{i}", f"concepts/b{i}"), relation_type="related_to"
        )
        for i in range(n)
    )


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / "bundle").mkdir(parents=True)
    (root / "bundle" / "index.md").write_text("# Index\n", encoding="utf-8")
    (root / "bundle" / "log.md").write_text("# Log\n", encoding="utf-8")
    (root / "openkos.yaml").write_text("model: llama3.1\n", encoding="utf-8")
    return root


class _Observer:
    def exemption_resolved(self, local_exemption: bool) -> None: ...
    def vacuous_coverage(self, notice: str) -> None: ...
    def persisted_findings_unreadable(self, error: Exception) -> None: ...
    def progress_callback(self) -> None:
        return None

    def persist_failed(self, error: Exception) -> None:
        raise AssertionError(f"persist failed: {error}")


def test_a_truncated_contradictions_stage_resumes_with_zero_repeated_calls(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    run = budget.start_budgeted_run(
        config.WorkspaceLayout(root), _unattended(max_calls_per_pass=100), _NOW
    )
    plan = CandidatePlan(specs=_specs(5), edge_total=5, merged_total=0)
    judged_pairs: list[tuple[str, str]] = []
    digests = (findings.InputDigest("concepts/x", "d" * 64),)

    def find(bundle_dir: Path, **kwargs: object) -> tuple[ContradictionBatch, int]:
        llm = cast(LLMBackend, kwargs["llm"])
        judged_plan: CandidatePlan = kwargs["plan"]  # type: ignore[assignment]
        verdicts = []
        for spec in judged_plan.specs:
            llm.chat([])  # one model call per pair
            judged_pairs.append(spec.pair_ids)
            verdicts.append(
                ContradictionVerdict(
                    pair_ids=spec.pair_ids,
                    verdict=Verdict.CONTRADICTS,
                    confidence=0.9,
                    rationale="r",
                    conflicting_claims=("c",),
                )
            )
        return ContradictionBatch(results=verdicts), len(verdicts)

    def persist(
        layout: config.WorkspaceLayout,
        plan: CandidatePlan,
        verdicts: Sequence[ContradictionVerdict],
    ) -> None:
        """`curate`'s write path with the digest function pinned, so the
        serve-side check (same pinned function) sees the rows as fresh."""
        conn = derived.open_derived_connection(layout.findings_db_path)
        try:
            findings.record_findings(
                conn,
                [
                    findings.Finding(
                        pair_ids=v.pair_ids,
                        merged_absorbed_id=v.merged_absorbed_id,
                        verdict=v.verdict.value,
                        confidence=v.confidence,
                        rationale=v.rationale,
                        conflicting_claims=v.conflicting_claims,
                        input_digests=digests,
                    )
                    for v in verdicts
                ],
            )
        finally:
            conn.close()

    inner = _Inner()
    ports = service.ContradictionsPorts(
        chat_client=run.wrap_chat_client(lambda cfg: inner),
        local_exemption=lambda client, cfg: False,
        open_proximity=lambda path: None,
        build_graph=build_graph,
        plan_candidates=lambda *a, **k: plan,
        find_contradictions=find,
        persist_findings=persist,
        finding_input_digests=lambda bundle_dir, spec: digests,
        zero_state_message=lambda layout, store, missing: "zero",
    )

    def stage(max_calls: int | None) -> service.ContradictionsOutcome:
        return service.run_contradictions(
            root,
            options=service.ContradictionsOptions(max_calls=max_calls),
            ports=ports,
            observer=_Observer(),
        )

    first = stage(3)
    assert len(inner.chats) == 3
    assert run.spent == 3
    assert first.fresh_count == 3
    assert first.deferred_by_bound == 2, "truncated, not silently dropped"
    first_batch = list(judged_pairs)
    assert first_batch == [s.pair_ids for s in plan.specs[:3]], "deterministic order"

    second = stage(None)
    assert len(inner.chats) == 5, "exactly the two unjudged pairs cost a call"
    assert judged_pairs[3:] == [s.pair_ids for s in plan.specs[3:]]
    assert set(judged_pairs[3:]).isdisjoint(first_batch), "zero repeated calls"
    assert (second.served_count, second.fresh_count) == (3, 2)
    assert second.deferred_by_bound == 0

    third = stage(None)
    assert len(inner.chats) == 5, "a fully served stage makes no call"
    assert third.served_count == 5


# -- Runner-only installation -----------------------------------------------


def _modules_under(*parts: str) -> list[Path]:
    return sorted((_REPO_SRC.joinpath(*parts)).rglob("*.py"))


def _binds_budget(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and node.module.endswith("application.budget"):
                return True
            if node.module == "openkos.application" and any(
                alias.name == "budget" for alias in node.names
            ):
                return True
        elif isinstance(node, ast.Import):
            if any(alias.name.endswith("application.budget") for alias in node.names):
                return True
        elif (
            isinstance(node, ast.Name)
            and node.id
            in {
                "CountingBackend",
                "start_budgeted_run",
            }
        ) or (
            isinstance(node, ast.Attribute)
            and node.attr
            in {
                "CountingBackend",
                "start_budgeted_run",
            }
        ):
            return True
    return False


@pytest.mark.parametrize("surface", ["cli", "mcp"])
def test_no_cli_or_mcp_module_can_install_the_counting_wrapper(surface: str) -> None:
    modules = _modules_under(surface)
    assert modules, f"{surface}/ must be scanned, not vacuously empty"
    offenders = [str(p.relative_to(_REPO_SRC)) for p in modules if _binds_budget(p)]
    assert offenders == []


def test_the_detector_would_catch_an_installation(tmp_path: Path) -> None:
    """The guard above is only worth its green if it can go red."""
    bad = tmp_path / "bad.py"
    bad.write_text(
        "from openkos.application.budget import start_budgeted_run\n", "utf-8"
    )
    worse = tmp_path / "worse.py"
    worse.write_text("from openkos.application import budget\n", "utf-8")
    sneaky = tmp_path / "sneaky.py"
    sneaky.write_text("x = something.CountingBackend(client)\n", "utf-8")
    clean = tmp_path / "clean.py"
    clean.write_text("from openkos.application import backends\n", "utf-8")

    assert _binds_budget(bad)
    assert _binds_budget(worse)
    assert _binds_budget(sneaky)
    assert not _binds_budget(clean)
