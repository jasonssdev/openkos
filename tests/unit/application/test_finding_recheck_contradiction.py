"""Re-checking one changed contradiction from the app (#1523): a quote for the
row's own pair, a run that judges that pair and nothing else, and what the run
does to the row (replace it, retire it, or leave it).

Every test runs the real planner over a real workspace in which `alpha` and
`beta` (and, in one test, `gamma` and `delta`) are joined by a typed edge, so
the candidate plan is the engine's own. The chat client is a scripted double; a
quote's client must never be asked anything.
"""

import contextlib
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from openkos import config, lock
from openkos.application import (
    curate_decisions,
    finding_inputs,
    judging,
    queue_producers,
)
from openkos.application import finding_recheck as recheck
from openkos.application.runtime import StopToken
from openkos.llm.base import BackendHostLocality, BackendUnavailable, Message
from openkos.resolution import contradiction
from openkos.state import derived
from openkos.state import pending_queue as pq
from tests.unit.application.curation_support import make_workspace

_A, _B, _C, _D = "concepts/alpha", "concepts/beta", "concepts/gamma", "concepts/delta"


def _write(root: Path, concept_id: str, body: str, related: str | None = None) -> None:
    path = root / "bundle" / f"{concept_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    relations = (
        f"relations:\n  - target: {related}\n    type: related_to\n" if related else ""
    )
    path.write_text(
        f"---\ntype: Concept\ntitle: {concept_id.rsplit('/', 1)[1]}\n"
        f"sensitivity: private\n{relations}---\n\n{body}\n",
        encoding="utf-8",
    )


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    ws = make_workspace(tmp_path, monkeypatch)
    _write(ws, _A, "The date is May.", related=_B)
    _write(ws, _B, "The date is May too.")
    return ws


class Judge:
    """A chat client that answers with `reply`, counts its calls and can run a
    hook inside the n-th one."""

    def __init__(
        self,
        reply: dict[str, object] | None = None,
        *,
        on_chat: Callable[[int], None] | None = None,
        error: Exception | None = None,
        forbidden: bool = False,
        local: bool = False,
    ) -> None:
        self.reply = reply if reply is not None else CONTRADICTS
        self.on_chat = on_chat
        self.error = error
        self.forbidden = forbidden
        self.local = local
        self.calls: list[str] = []

    @property
    def locality(self) -> BackendHostLocality:
        return BackendHostLocality(is_local=self.local, display_host="llm.example.test")

    def chat(self, messages: Sequence[Message]) -> str:
        assert not self.forbidden, "a quote called the model"
        self.calls.append(messages[-1]["content"])
        if self.on_chat is not None:
            self.on_chat(len(self.calls))
        if self.error is not None:
            raise self.error
        return json.dumps(self.reply)


CONTRADICTS: dict[str, object] = {
    "verdict": "contradicts",
    "confidence": 0.9,
    "rationale": "June against May.",
    "conflicting_claims": ["alpha says June", "beta says May"],
}
CONSISTENT: dict[str, object] = {
    "verdict": "consistent",
    "confidence": 0.9,
    "rationale": "Both say May.",
    "conflicting_claims": [],
}


def _ports(client: Judge, **overrides: Any) -> judging.JudgingPorts:
    return judging.JudgingPorts(chat_client=lambda cfg, task: client, **overrides)


def _layout(root: Path) -> config.WorkspaceLayout:
    return config.WorkspaceLayout(root)


def _spec(root: Path, pair: tuple[str, str]) -> contradiction._CandidateSpec:
    plan = contradiction.plan_candidates(_layout(root).bundle_dir)
    [spec] = [s for s in plan.specs if s.pair_ids == pair]
    return spec


def _enqueue(root: Path, pair: tuple[str, str]) -> str:
    """The open row the producers would have published for `pair`, as the pages
    stand now."""
    layout = _layout(root)
    pair = (min(pair), max(pair))  # a candidate's pair is sorted
    verdict = contradiction.ContradictionVerdict(
        pair_ids=pair,
        verdict=contradiction.Verdict.CONTRADICTS,
        confidence=0.8,
        rationale="May against June.",
        conflicting_claims=("the date is May", "the date is June"),
        merged_absorbed_id=None,
    )
    proposal = queue_producers.contradiction_proposal(
        verdict,
        _spec(root, pair),
        input_digests=finding_inputs.finding_input_digests,
        bundle_dir=layout.bundle_dir,
    )
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


def _quote(root: Path, key: str, client: Judge, **kwargs: Any) -> judging.JudgingQuote:
    return recheck.quote_finding(
        root,
        "contradiction",
        key,
        ports=_ports(client, **kwargs),
    )


def _run(
    root: Path,
    token: judging.QuoteToken | None,
    client: Judge,
    *,
    stop: StopToken | None = None,
    **kwargs: Any,
) -> judging.JudgingOutcome | judging.QuoteStale:
    result = recheck.run_finding(
        root,
        token,
        stop=stop if stop is not None else StopToken(),
        ports=_ports(client, **kwargs),
    )
    assert not isinstance(result, judging.RunRefused), result
    return result


def _outcome(result: object) -> judging.JudgingOutcome:
    assert isinstance(result, judging.JudgingOutcome), result
    return result


def _changed(root: Path, key: str) -> str:
    """Make the row's pages move, so the row lists as changed."""
    _write(root, _B, "The date is June.")
    [item] = curate_decisions.list_open_decisions(_layout(root))
    assert item.decision_key == key
    assert item.fresh is False
    return key


@pytest.fixture
def key(root: Path) -> str:
    return _changed(root, _enqueue(root, (_A, _B)))


# -- a quote ------------------------------------------------------------------- #


def test_a_quote_prices_the_one_pair_and_makes_no_call(root: Path, key: str) -> None:
    client = Judge(forbidden=True)

    quote = _quote(root, key, client)

    assert quote.kind == "contradiction"
    assert (quote.total, quote.served, quote.to_judge, quote.calls) == (1, 0, 1, 1)
    assert quote.token.calls == 1
    [item] = quote.token.work
    assert item.work_key == key
    assert dict(item.member_digests).keys() >= {_A, _B}
    assert quote.binding.kind == "contradiction"
    assert quote.binding.model == config.resolve_task_model(
        config.read_config(root), "contradiction"
    )
    assert client.calls == []


def test_a_quote_for_a_row_that_is_not_open_has_nothing_to_judge(root: Path) -> None:
    quote = _quote(root, "contradiction:" + "0" * 16, Judge(forbidden=True))

    assert (quote.total, quote.to_judge, quote.calls) == (0, 0, 0)
    assert quote.token.work == ()


def test_a_quote_for_a_pair_the_search_no_longer_keeps_has_nothing_to_judge(
    root: Path, key: str
) -> None:
    _write(root, _A, "The date is May.")  # the typed edge is gone

    quote = _quote(root, key, Judge(forbidden=True))

    assert (quote.total, quote.to_judge, quote.calls) == (1, 0, 0)


@pytest.mark.parametrize("bad", ["", "identity:abc", "not-a-key"])
def test_a_quote_refuses_a_key_that_is_not_a_contradictions(
    root: Path, bad: str
) -> None:
    with pytest.raises(ValueError, match="decision_key"):
        _quote(root, bad, Judge(forbidden=True))


def test_a_confidential_page_is_judged_only_on_a_local_model(
    root: Path, key: str
) -> None:
    secret = root / "bundle" / f"{_B}.md"
    secret.write_text(
        secret.read_text(encoding="utf-8").replace(
            "sensitivity: private", "sensitivity: confidential"
        ),
        encoding="utf-8",
    )
    remote = _quote(root, key, Judge(forbidden=True))
    assert remote.to_judge == 0  # the search withholds it from a remote model
    client = Judge(local=True)
    quote = _quote(root, key, client)
    assert quote.to_judge == 1

    _run(root, quote.token, client)

    assert "The date is June." in client.calls[0]  # sent: nothing leaves this Mac


# -- a run --------------------------------------------------------------------- #


def test_a_run_judges_only_that_pair_and_makes_its_row_current(
    root: Path, key: str
) -> None:
    _write(root, _C, "The fee is 5.", related=_D)
    _write(root, _D, "The fee is 6.")
    other = _enqueue(root, (_C, _D))
    _write(root, _D, "The fee is 7.")  # the other row changed too
    client = Judge()
    quote = _quote(root, key, client)

    outcome = _outcome(_run(root, quote.token, client))

    assert outcome.kind == "contradiction"
    assert outcome.status == "completed"
    assert (outcome.judged, outcome.calls, outcome.persisted) == (1, 1, 1)
    assert len(client.calls) == 1
    assert "alpha" in client.calls[0]
    assert "gamma" not in client.calls[0]
    assert _status_of(root, key) == ["stale", "pending"]
    assert _status_of(root, other) == ["pending"]  # untouched, still changed
    listed = {
        i.decision_key: i for i in curate_decisions.list_open_decisions(_layout(root))
    }
    assert listed[key].fresh is True
    assert listed[other].fresh is False


def test_the_replacement_row_carries_the_new_verdict(root: Path, key: str) -> None:
    client = Judge(dict(CONTRADICTS, rationale="Now June against May."))
    quote = _quote(root, key, client)

    _run(root, quote.token, client)

    [row] = [i for i in _items(root) if i.decision_key == key and i.status == "pending"]
    payload = json.loads(row.payload)
    assert payload["rationale"] == "Now June against May."
    assert payload["conflicting_claims"] == ["alpha says June", "beta says May"]


def test_a_run_that_no_longer_finds_a_disagreement_retires_the_row(
    root: Path, key: str
) -> None:
    client = Judge(CONSISTENT)
    quote = _quote(root, key, client)

    outcome = _outcome(_run(root, quote.token, client))

    assert (outcome.judged, outcome.persisted) == (1, 1)
    assert _status_of(root, key) == ["stale"]
    assert curate_decisions.list_open_decisions(_layout(root)) == ()


def test_a_page_edited_during_the_call_publishes_nothing(root: Path, key: str) -> None:
    client = Judge(on_chat=lambda n: _write(root, _B, "The date is July."))
    quote = _quote(root, key, client)

    outcome = _outcome(_run(root, quote.token, client))

    assert outcome.judged == 1
    assert (outcome.persisted, outcome.dropped) == (0, 1)
    assert _status_of(root, key) == ["pending"]  # still the changed row


def test_a_pair_edited_after_the_quote_is_settled_not_judged(
    root: Path, key: str
) -> None:
    client = Judge()
    quote = _quote(root, key, client)
    _write(root, _B, "The date is August.")

    outcome = _outcome(_run(root, quote.token, client))

    assert (outcome.judged, outcome.calls, outcome.settled_since_quote) == (0, 0, 1)
    assert client.calls == []


def test_a_moved_binding_is_stale_before_any_call(root: Path, key: str) -> None:
    client = Judge()
    quote = _quote(root, key, client)
    cfg_text = (root / "openkos.yaml").read_text(encoding="utf-8")
    (root / "openkos.yaml").write_text(
        cfg_text + "\nmodels:\n  contradiction: another-model:1b\n", encoding="utf-8"
    )

    result = _run(root, quote.token, client)

    assert isinstance(result, judging.QuoteStale)
    assert "model" in result.changed
    assert client.calls == []


def test_a_row_resolved_after_the_quote_is_not_judged(root: Path, key: str) -> None:
    client = Judge()
    quote = _quote(root, key, client)
    conn = derived.open_derived_connection(_layout(root).findings_db_path)
    try:
        pq.retire_open_by_key(conn, key, commit_section=contextlib.nullcontext)
    finally:
        conn.close()

    outcome = _outcome(_run(root, quote.token, client))

    assert (outcome.judged, outcome.calls, outcome.settled_since_quote) == (0, 0, 1)


def test_an_unreachable_backend_is_raised_and_the_row_is_unchanged(
    root: Path, key: str
) -> None:
    client = Judge(error=BackendUnavailable("down"))
    quote = _quote(root, key, client)

    with pytest.raises(BackendUnavailable):
        _run(root, quote.token, client)

    assert _status_of(root, key) == ["pending"]


class _Busy:
    """A commit section whose workspace never frees up."""

    def __call__(self) -> _Busy:
        return self

    def __enter__(self) -> None:
        raise lock.WorkspaceBusyError("busy")

    def __exit__(self, *exc: object) -> None:
        return None


def test_a_busy_workspace_ends_the_run_unpersisted(root: Path, key: str) -> None:
    client = Judge()
    quote = _quote(root, key, client)
    slept: list[float] = []

    outcome = _outcome(
        _run(root, quote.token, client, commit_section=_Busy(), sleep=slept.append)
    )

    assert outcome.status == "unpersisted"
    assert outcome.unpersisted_cause == "workspace_busy"
    assert outcome.unpersisted == 1
    assert slept == list(judging.PERSIST_BACKOFF_SECONDS)
    assert _status_of(root, key) == ["pending"]


def test_a_stop_before_the_pair_leaves_it_deferred(root: Path, key: str) -> None:
    client = Judge()
    quote = _quote(root, key, client)
    stop = StopToken()
    stop.set()

    outcome = _outcome(_run(root, quote.token, client, stop=stop))

    assert (outcome.status, outcome.judged, outcome.deferred) == ("stopped", 0, 1)
    assert client.calls == []


def test_a_missing_token_is_refused(root: Path) -> None:
    result = recheck.run_finding(
        root, None, stop=StopToken(), ports=_ports(Judge(forbidden=True))
    )

    assert isinstance(result, judging.RunRefused)
