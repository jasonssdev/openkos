"""`curate`'s Identity stage and the judging service on one fixture (the
`judging-application-service` change, S2c; design, "The equivalence fixture").

Both paths persist through `persist_adjudications`, so what they store and what
they close must be equal. They do NOT leave the queue the same at the instant
they return, and that is intended: `curate` inserts a row for a group that has
none and never upgrades an open one, while the service writes a verdict through
amend-only. The daemon is the producer of record for queue rows, so the property
that matters is that one daemon identity pass on each side converges.

Byte identity of `curate`'s own output is the S0a goldens' job, not this test's.
"""

import contextlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from openkos import config
from openkos.application import judging, queue_producers
from openkos.application import pending as application_pending
from openkos.application.identity_adjudication import persist_adjudications
from openkos.application.runtime import StopToken
from openkos.cli.main import app
from openkos.resolution.adjudication import AdjudicatedCandidate, Verdict
from openkos.resolution.candidates import CandidateGroup, CandidateGroupReport
from openkos.state import adjudications as store
from openkos.state import derived
from openkos.state import pending_queue as pq
from tests.unit.cli.test_curate_auto_merge import (
    _SAME,
    _build,
    _family,
    _Judge,
    _simulate_tty,
)
from tests.unit.cli.test_daemon import _run_identity_stage
from tests.unit.conftest import OfflineOllama

runner = CliRunner()

_G1, _G2, _G3, _G4 = (_family(name) for name in ("g1", "g2", "g3", "g4"))
_UNSURE = (Verdict.UNCERTAIN, 0.5)
_DIFFERENT = (Verdict.DIFFERENT, 0.95)
_VERDICTS = {
    _G1.member_ids: _SAME,
    _G2.member_ids: _DIFFERENT,
    _G3.member_ids: _UNSURE,
}


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9")


def _seed_unjudged_rows(root: Path, groups: list[CandidateGroup]) -> None:
    """An open `pending_evaluation` row per group, as a daemon pass leaves them."""
    layout = config.WorkspaceLayout(root)
    current = application_pending.current_finding_digest(layout.bundle_dir)
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        pq.ensure_schema(conn)
        for group in groups:
            proposal = queue_producers.identity_group_proposal(
                group, current_digest=current, result=None
            )
            assert proposal is not None
            pq.upsert_proposal(
                conn,
                proposal,
                commit_section=contextlib.nullcontext,
                bundle_dir=layout.bundle_dir,
            )
    finally:
        conn.close()


def _store_verdict(root: Path, group: CandidateGroup, verdict: Verdict) -> None:
    """A servable stored verdict under the current rubric and the bit a local
    client resolves, as an earlier `adjudicate` run leaves it."""
    result = persist_adjudications(
        config.WorkspaceLayout(root),
        [AdjudicatedCandidate(group, verdict, 0.95, "stored earlier")],
        include_confidential=True,
        judged_digests=None,
        commit_section=contextlib.nullcontext,
    )
    assert result.persisted == 1


def _adjudication_rows(root: Path) -> list[tuple[Any, ...]]:
    conn = derived.open_derived_connection(
        config.WorkspaceLayout(root).findings_db_path
    )
    try:
        return sorted(
            (
                row.member_ids,
                row.verdict,
                row.confidence,
                row.rationale,
                row.include_confidential,
                tuple(sorted((d.input_ref, d.digest) for d in row.input_digests)),
                row.rubric_digest,
            )
            for row in store.open_adjudications(conn)
        )
    finally:
        conn.close()


def _queue(root: Path) -> dict[tuple[str, ...], tuple[str, str | None]]:
    """Each identity row as (status, verdict), keyed by member set; ids and
    timestamps are ignored."""
    conn = derived.open_derived_connection(
        config.WorkspaceLayout(root).findings_db_path
    )
    try:
        return {
            tuple(sorted(item.targets)): (
                item.status,
                (json.loads(item.payload)["adjudication"] or {}).get("verdict"),
            )
            for item in pq.all_items(conn)
            if item.kind == "identity"
        }
    finally:
        conn.close()


def _members(group: CandidateGroup) -> tuple[str, ...]:
    return tuple(sorted(group.member_ids))


def _two_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, groups: list[CandidateGroup]
) -> tuple[Path, Path]:
    """One workspace seeded as the design says, then copied: curate runs in the
    first, the service in the second."""
    curate_root, _judge = _build(tmp_path, monkeypatch, groups, _VERDICTS)
    _seed_unjudged_rows(curate_root, [_G1, _G2, _G3])
    _store_verdict(curate_root, _G4, Verdict.SAME)
    service_root = tmp_path / "ws-service"
    shutil.copytree(curate_root, service_root, symlinks=True)
    return curate_root, service_root


def _patch_candidates(
    monkeypatch: pytest.MonkeyPatch, groups: list[CandidateGroup]
) -> None:
    report = CandidateGroupReport(
        groups=tuple(groups), produced=len(groups), retained=len(groups)
    )
    monkeypatch.setattr(
        "openkos.resolution.find_candidates_report", lambda *a, **k: report
    )


def _run_service(root: Path, groups: list[CandidateGroup]) -> judging.JudgingOutcome:
    report = CandidateGroupReport(
        groups=tuple(groups), produced=len(groups), retained=len(groups)
    )
    quoting = judging.JudgingPorts(
        chat_client=lambda cfg, task: OfflineOllama("m"),
        find_candidates_report=lambda *a, **k: report,
    )
    quote = judging.quote_judging(root, "identity", ports=quoting)
    assert (quote.served, quote.to_judge) == (1, 3)
    judged = judging.run_judging(
        root,
        quote.token,
        stop=StopToken(),
        ports=judging.JudgingPorts(
            chat_client=lambda cfg, task: OfflineOllama("m"),
            find_candidates_report=lambda *a, **k: report,
            adjudicate_candidates=_Judge(_VERDICTS),
        ),
    )
    assert isinstance(judged, judging.JudgingOutcome)
    return judged


def test_curate_and_the_service_agree_on_the_store_and_converge_through_the_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    groups = [_G1, _G2, _G3, _G4]
    curate_root, service_root = _two_copies(tmp_path, monkeypatch, groups)
    assert _adjudication_rows(curate_root) == _adjudication_rows(service_root)

    # curate: Identity judges the three, serves the fourth, and the walk skips.
    _simulate_tty(monkeypatch)
    # Every prompt is answered `n`: the Identity walk reads it as a skip, and the
    # other stages' `[y/N]` prompts accept nothing else (an unrecognized answer
    # would be asked again forever). Nothing is written to `bundle/`.
    monkeypatch.setattr("typer.prompt", lambda *a, **k: "n")
    monkeypatch.chdir(curate_root)
    result = runner.invoke(app, ["curate", "--auto"])
    assert result.exit_code == 0, result.stdout + result.stderr
    # the service, over the identical copy.
    outcome = _run_service(service_root, groups)
    assert (outcome.status, outcome.judged, outcome.persisted) == ("completed", 3, 3)

    # 1. The adjudications store: both persist through `persist_adjudications`.
    stored = _adjudication_rows(curate_root)
    assert stored == _adjudication_rows(service_root)
    assert {row[0]: row[1] for row in stored} == {
        _G1.member_ids: "same",
        _G2.member_ids: "different",
        _G3.member_ids: "uncertain",
        _G4.member_ids: "same",
    }

    # 2. The DIFFERENT resolution: g2's open row is declined on both sides.
    after_curate, after_service = _queue(curate_root), _queue(service_root)
    assert after_curate[_members(_G2)][0] == "declined"
    assert after_service[_members(_G2)][0] == "declined"

    # 3. Straight after the run the queues differ, by design.
    assert after_curate == {
        _members(_G1): ("pending", None),
        _members(_G2): ("declined", None),
        _members(_G3): ("pending", None),
        _members(_G4): ("pending", "same"),
    }
    assert after_service == {
        _members(_G1): ("pending", "same"),
        _members(_G2): ("declined", None),
        _members(_G3): ("pending", "uncertain"),
    }

    # ...and one daemon identity pass on each side makes them converge.
    _patch_candidates(monkeypatch, groups)
    _run_identity_stage(curate_root)
    _run_identity_stage(service_root)
    converged = {
        _members(_G1): ("pending", "same"),
        _members(_G2): ("declined", None),
        _members(_G3): ("pending", "uncertain"),
        _members(_G4): ("pending", "same"),
    }
    assert _queue(curate_root) == converged
    assert _queue(service_root) == converged


def test_a_run_and_a_daemon_pass_over_one_group_leave_one_open_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The app judges while the unattended engine runs: the daemon enqueues the
    group, the run writes its verdict through, the daemon passes again."""
    root, _judge = _build(tmp_path, monkeypatch, [_G1], {_G1.member_ids: _SAME})
    _patch_candidates(monkeypatch, [_G1])
    _run_identity_stage(root)
    assert _queue(root) == {_members(_G1): ("pending", None)}

    report = CandidateGroupReport(groups=(_G1,), produced=1, retained=1)
    ports = judging.JudgingPorts(
        chat_client=lambda cfg, task: OfflineOllama("m"),
        find_candidates_report=lambda *a, **k: report,
        adjudicate_candidates=_Judge({_G1.member_ids: _SAME}),
    )
    quote = judging.quote_judging(root, "identity", ports=ports)
    outcome = judging.run_judging(root, quote.token, stop=StopToken(), ports=ports)
    assert isinstance(outcome, judging.JudgingOutcome)
    assert _queue(root) == {_members(_G1): ("pending", "same")}

    _run_identity_stage(root)

    assert _queue(root) == {_members(_G1): ("pending", "same")}
    again = judging.quote_judging(root, "identity", ports=ports)
    assert (again.served, again.to_judge) == (1, 0)
    assert len(_adjudication_rows(root)) == 1
