"""Re-checking one changed newer decision from the app (#1523): the same quote
and run as a contradiction's, over the revisions detector.

The candidate plan reads a vector store the app never builds here, so the plan
is a port that answers with the candidates the test names; the judge, the
digests, the finding store and the queue are the real ones. The chat client is a
scripted double; a quote's client must never be asked anything.
"""

import contextlib
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from openkos import config
from openkos.application import curate_decisions, judging, queue_producers, revisions
from openkos.application import finding_recheck as recheck
from openkos.application.runtime import StopToken
from openkos.llm.base import BackendHostLocality, Message
from openkos.resolution import decision_revision
from openkos.state import derived, revision_findings
from openkos.state import pending_queue as pq
from tests.unit.application.curation_support import make_workspace

_A, _B, _C, _D = "decisions/a", "decisions/b", "decisions/c", "decisions/d"


def _write(root: Path, concept_id: str, body: str) -> None:
    path = root / "bundle" / f"{concept_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: Decision\ntitle: {concept_id}\nsensitivity: private\n---\n\n{body}\n",
        encoding="utf-8",
    )


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    ws = make_workspace(tmp_path, monkeypatch)
    _write(ws, _A, "Ship on May first.")
    _write(ws, _B, "Ship on June first.")
    return ws


REVERSES = {
    "verdict": "reverses",
    "confidence": 0.9,
    "rationale": "June overturns May.",
    "quote_first": "Ship on May first.",
    "quote_second": "Ship on June second.",
}
UNRELATED = {"verdict": "unrelated", "confidence": 0.9, "rationale": "Different."}


class Judge:
    """A chat client that answers with `reply` and counts its calls."""

    def __init__(
        self,
        reply: dict[str, object] | str | None = None,
        *,
        on_chat: Callable[[int], None] | None = None,
        forbidden: bool = False,
    ) -> None:
        self.reply = reply if reply is not None else REVERSES
        self.on_chat = on_chat
        self.forbidden = forbidden
        self.calls: list[str] = []

    @property
    def locality(self) -> BackendHostLocality:
        return BackendHostLocality(is_local=False, display_host="llm.example.test")

    def chat(self, messages: Sequence[Message]) -> str:
        assert not self.forbidden, "a quote called the model"
        self.calls.append(messages[-1]["content"])
        if self.on_chat is not None:
            self.on_chat(len(self.calls))
        if isinstance(self.reply, str):
            return self.reply
        return json.dumps(self.reply)


def _plan_of(*pairs: tuple[str, str]) -> Callable[..., revisions.RevisionPlan]:
    candidates = tuple(
        decision_revision.RevisionCandidate(pair_ids=pair, score=1.0) for pair in pairs
    )

    def plan(layout: config.WorkspaceLayout, *args: Any, **kwargs: Any) -> Any:
        return revisions.RevisionPlan(
            coverage=revisions.VectorCoverage(
                store="ok", vectors={}, missing=frozenset(), stale=frozenset()
            ),
            candidate_plan=decision_revision.RevisionCandidatePlan(
                candidates=candidates, total=len(candidates), without_vector=0
            ),
            served=(),
            to_judge=candidates,
        )

    return plan


def _ports(client: Judge, *pairs: tuple[str, str], **overrides: Any) -> Any:
    return judging.JudgingPorts(
        chat_client=lambda cfg, task: client,
        plan_revisions=_plan_of(*(pairs or ((_A, _B),))),
        **overrides,
    )


def _layout(root: Path) -> config.WorkspaceLayout:
    return config.WorkspaceLayout(root)


def _enqueue(root: Path, pair: tuple[str, str]) -> str:
    """The open row the producers would have published for `pair`, in the order
    given: a stored pair is not necessarily the plan's sorted one."""
    layout = _layout(root)
    finding = revision_findings.RevisionFinding(
        pair_ids=pair,
        verdict="reverses",
        confidence=0.8,
        rationale="May then June.",
        quotes=("Ship on May first.", "Ship on June first."),
        dates=(None, None),
        date_states=("none-reached", "none-reached"),
        include_confidential=False,
        prompt_version=decision_revision.JUDGE_PROMPT_VERSION,
        input_digests=revisions.current_input_digests(layout, pair),
    )
    [proposal] = queue_producers.revision_proposals([finding])
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        pq.ensure_schema(conn)
        pq.upsert_proposal(
            conn,
            proposal,
            commit_section=contextlib.nullcontext,
            bundle_dir=layout.bundle_dir,
        )
    finally:
        conn.close()
    return proposal.decision_key


def _items(root: Path) -> list[pq.PendingItem]:
    conn = derived.open_derived_connection(_layout(root).findings_db_path)
    try:
        return pq.all_items(conn)
    finally:
        conn.close()


def _status_of(root: Path, key: str) -> list[str]:
    return [i.status for i in _items(root) if i.decision_key == key]


def _quote(root: Path, key: str, client: Judge, *pairs: tuple[str, str]) -> Any:
    return recheck.quote_finding(root, "revision", key, ports=_ports(client, *pairs))


def _run(
    root: Path, token: judging.QuoteToken, client: Judge, *pairs: tuple[str, str]
) -> judging.JudgingOutcome:
    result = recheck.run_finding(
        root, token, stop=StopToken(), ports=_ports(client, *pairs)
    )
    assert isinstance(result, judging.JudgingOutcome), result
    return result


@pytest.fixture
def key(root: Path) -> str:
    key = _enqueue(root, (_A, _B))
    _write(root, _B, "Ship on June second.")  # the row's pages moved
    [item] = curate_decisions.list_open_decisions(_layout(root))
    assert item.decision_key == key
    assert item.fresh is False
    return key


def test_a_quote_prices_the_one_pair_and_makes_no_call(root: Path, key: str) -> None:
    client = Judge(forbidden=True)

    quote = _quote(root, key, client)

    assert quote.kind == "revision"
    assert (quote.total, quote.served, quote.to_judge, quote.calls) == (1, 0, 1, 1)
    [item] = quote.token.work
    assert item.work_key == key
    assert dict(item.member_digests).keys() >= {_A, _B}
    assert quote.binding.model == config.read_config(root).model
    assert client.calls == []


def test_a_quote_for_a_pair_the_plan_does_not_hold_has_nothing_to_judge(
    root: Path, key: str
) -> None:
    quote = _quote(root, key, Judge(forbidden=True), (_C, _D))

    assert (quote.total, quote.to_judge, quote.calls) == (1, 0, 0)


def test_a_quote_refuses_a_key_that_is_not_a_revisions(root: Path) -> None:
    with pytest.raises(ValueError, match="decision_key"):
        _quote(root, "contradiction:abc", Judge(forbidden=True))


def test_a_run_judges_only_that_pair_and_makes_its_row_current(
    root: Path, key: str
) -> None:
    _write(root, _C, "Pay 5.")
    _write(root, _D, "Pay 6.")
    other = _enqueue(root, (_C, _D))
    client = Judge()
    pairs = ((_A, _B), (_C, _D))
    quote = _quote(root, key, client, *pairs)

    outcome = _run(root, quote.token, client, *pairs)

    assert (outcome.kind, outcome.status) == ("revision", "completed")
    assert (outcome.judged, outcome.calls, outcome.persisted) == (1, 1, 1)
    assert len(client.calls) == 1
    assert _status_of(root, key) == ["stale", "pending"]
    assert _status_of(root, other) == ["pending"]
    listed = {
        i.decision_key: i for i in curate_decisions.list_open_decisions(_layout(root))
    }
    assert listed[key].fresh is True


def test_the_replacement_row_carries_the_new_verdict(root: Path, key: str) -> None:
    client = Judge(dict(REVERSES, rationale="Now June second."))
    quote = _quote(root, key, client)

    _run(root, quote.token, client)

    [row] = [i for i in _items(root) if i.decision_key == key and i.status == "pending"]
    payload = json.loads(row.payload)
    assert payload["rationale"] == "Now June second."
    assert payload["quotes"] == ["Ship on May first.", "Ship on June second."]


def test_a_run_that_finds_no_revision_retires_the_row(root: Path, key: str) -> None:
    client = Judge(UNRELATED)
    quote = _quote(root, key, client)

    outcome = _run(root, quote.token, client)

    assert (outcome.judged, outcome.persisted) == (1, 1)
    assert _status_of(root, key) == ["stale"]
    assert curate_decisions.list_open_decisions(_layout(root)) == ()


def test_a_page_edited_during_the_call_publishes_nothing(root: Path, key: str) -> None:
    client = Judge(on_chat=lambda n: _write(root, _B, "Ship on July first."))
    quote = _quote(root, key, client)

    outcome = _run(root, quote.token, client)

    assert (outcome.judged, outcome.persisted, outcome.dropped) == (1, 0, 1)
    assert _status_of(root, key) == ["pending"]


def test_a_pair_edited_after_the_quote_is_settled_not_judged(
    root: Path, key: str
) -> None:
    client = Judge()
    quote = _quote(root, key, client)
    _write(root, _B, "Ship on August first.")

    outcome = _run(root, quote.token, client)

    assert (outcome.judged, outcome.calls, outcome.settled_since_quote) == (0, 0, 1)
    assert client.calls == []


def test_a_row_whose_pair_is_stored_in_another_order_is_still_found(
    root: Path,
) -> None:
    key = _enqueue(root, (_B, _A))

    quote = _quote(root, key, Judge(forbidden=True))

    assert quote.to_judge == 1


def test_the_quote_asks_for_every_candidate_not_only_the_unserved(
    root: Path, key: str
) -> None:
    seen: dict[str, Any] = {}
    plan = _plan_of((_A, _B))

    def recording(layout: config.WorkspaceLayout, *args: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return plan(layout, *args, **kwargs)

    ports = judging.JudgingPorts(
        chat_client=lambda cfg, task: Judge(forbidden=True), plan_revisions=recording
    )
    recheck.quote_finding(root, "revision", key, ports=ports)

    assert seen["fresh"] is True  # a served, now-changed finding is judged again


def test_a_plan_without_usable_vectors_quotes_nothing(root: Path, key: str) -> None:
    def absent(layout: config.WorkspaceLayout, *args: Any, **kwargs: Any) -> Any:
        return revisions.RevisionPlan(
            coverage=revisions.VectorCoverage(
                store="absent", vectors={}, missing=frozenset(), stale=frozenset()
            ),
            candidate_plan=decision_revision.RevisionCandidatePlan(
                candidates=(
                    decision_revision.RevisionCandidate(pair_ids=(_A, _B), score=1.0),
                ),
                total=1,
                without_vector=0,
            ),
            served=(),
            to_judge=(),
        )

    ports = judging.JudgingPorts(
        chat_client=lambda cfg, task: Judge(forbidden=True), plan_revisions=absent
    )
    quote = recheck.quote_finding(root, "revision", key, ports=ports)

    assert (quote.total, quote.to_judge) == (1, 0)


def test_a_reply_that_cannot_be_read_is_dropped_and_the_row_kept(
    root: Path, key: str
) -> None:
    client = Judge()
    quote = _quote(root, key, client)
    client.reply = "this is not a verdict"

    outcome = _run(root, quote.token, client)

    assert (outcome.judged, outcome.persisted, outcome.dropped) == (1, 0, 1)
    assert _status_of(root, key) == ["pending"]


def test_another_pairs_finding_never_replaces_this_pairs_row(
    root: Path, key: str
) -> None:
    _write(root, _C, "Pay 5.")
    _write(root, _D, "Pay 6.")
    layout = _layout(root)
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        revision_findings.record_revision_findings(
            conn,
            [
                revision_findings.RevisionFinding(
                    pair_ids=(_C, _D),
                    verdict="reverses",
                    confidence=0.9,
                    rationale="Pay changed.",
                    quotes=("Pay 5.", "Pay 6."),
                    dates=(None, None),
                    date_states=("none-reached", "none-reached"),
                    include_confidential=False,
                    prompt_version=decision_revision.JUDGE_PROMPT_VERSION,
                    input_digests=revisions.current_input_digests(layout, (_C, _D)),
                )
            ],
        )
    finally:
        conn.close()
    client = Judge(UNRELATED)
    quote = _quote(root, key, client)

    _run(root, quote.token, client)

    assert _status_of(root, key) == ["stale"]
    assert [i.decision_key for i in _items(root) if i.status == "pending"] == []
