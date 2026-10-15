"""The judging surface's quote half (the `judging-application-service` change,
S2b; ADR-0059): the binding, the token, the identity quote and its notices.

Every quote here runs against a client whose `chat` raises: a quote makes no
model call, so an unreachable backend must not matter.
"""

import contextlib
import dataclasses
import json
import sqlite3
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from openkos import config, lock
from openkos.application import judging
from openkos.application import pending as application_pending
from openkos.application import suggest_relations_service as relations
from openkos.application.runtime import StopToken
from openkos.graph import sqlite_graph
from openkos.graph.base import Edge
from openkos.llm.base import (
    BackendGenerationCapped,
    BackendHostLocality,
    BackendModelNotFound,
    BackendUnavailable,
    Message,
)
from openkos.model import okf
from openkos.resolution import edge_typing
from openkos.resolution.adjudication import (
    AdjudicatedCandidate,
    AdjudicationBatch,
    Verdict,
    rubric_digest,
)
from openkos.resolution.candidates import CandidateGroup, CandidateGroupReport, Tier
from openkos.state import adjudications as store
from openkos.state import derived, jobs
from openkos.state import edge_suggestions as edge_store
from tests.unit.application.curation_support import make_workspace

_RUBRIC = rubric_digest()
_NOW = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)


class _Client:
    """A chat client that must never be asked anything."""

    def __init__(self, *, is_local: bool = False) -> None:
        self.is_local = is_local
        self.chats = 0

    @property
    def locality(self) -> BackendHostLocality:
        return BackendHostLocality(is_local=self.is_local, display_host="localhost")

    def chat(self, messages: Sequence[Message]) -> str:
        self.chats += 1
        raise BackendUnavailable("the backend is down")


class _Fixture:
    def __init__(self, root: Path, groups: list[CandidateGroup]) -> None:
        self.root = root
        self.layout = config.WorkspaceLayout(root)
        self.groups = groups
        self.client = _Client()
        self.report = CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def ports(self, *, now: datetime = _NOW) -> judging.JudgingPorts:
        return judging.JudgingPorts(
            chat_client=lambda cfg, task: self.client,
            find_candidates_report=lambda bundle_dir, **_: self.report,
            now=lambda: now,
        )

    def quote(
        self,
        *,
        include_confidential: bool = False,
        include_uncertain: bool = False,
        now: datetime = _NOW,
    ) -> judging.JudgingQuote:
        return judging.quote_judging(
            self.root,
            "identity",
            include_confidential=include_confidential,
            include_uncertain=include_uncertain,
            ports=self.ports(now=now),
        )

    def store(
        self,
        group: CandidateGroup,
        *,
        verdict: str = "same",
        rubric: str | None = _RUBRIC,
        include_confidential: bool = False,
    ) -> None:
        current = application_pending.current_finding_digest(self.layout.bundle_dir)
        conn = derived.open_derived_connection(self.layout.findings_db_path)
        try:
            store.record_adjudications(
                conn,
                [
                    store.Adjudication(
                        member_ids=group.member_ids,
                        verdict=verdict,
                        confidence=0.8,
                        rationale="Judged.",
                        include_confidential=include_confidential,
                        input_digests=tuple(
                            store.InputDigest(input_ref=m, digest=current(m) or "")
                            for m in group.member_ids
                        ),
                        rubric_digest=rubric,
                    )
                ],
            )
        finally:
            conn.close()

    def edit(self, concept_id: str) -> None:
        path = self.layout.bundle_dir / f"{concept_id}.md"
        path.write_text(path.read_text(encoding="utf-8") + "\nEdited.\n", "utf-8")


def _write(root: Path, concept_id: str, *, sensitivity: str = "private") -> None:
    path = root / "bundle" / f"{concept_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        okf.dump_frontmatter(
            {"type": "Concept", "title": concept_id, "sensitivity": sensitivity},
            f"# {concept_id}\n\nBody.\n",
        ),
        encoding="utf-8",
    )


def _key(member_ids: tuple[str, ...]) -> str:
    return store.group_key_for(member_ids)


def _group(*members: str) -> CandidateGroup:
    return CandidateGroup(
        okf_type="Concept", member_ids=members, tier=Tier.HIGH, trigger="alpha"
    )


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Fixture:
    """Five two-member groups of readable private concepts."""
    root = make_workspace(tmp_path, monkeypatch)
    groups = [_group(f"concepts/g{n}-a", f"concepts/g{n}-b") for n in range(5)]
    for group in groups:
        for member in group.member_ids:
            _write(root, member)
    return _Fixture(root, groups)


# -- the binding and the token ---------------------------------------------- #


def _binding(**over: object) -> judging.QuoteBinding:
    base = judging.QuoteBinding(
        kind="identity",
        model="m",
        backend="ollama",
        is_local=True,
        effective_confidential=False,
        rubric_digest="r",
    )
    return dataclasses.replace(base, **over)  # type: ignore[arg-type]


def _token(**over: object) -> judging.QuoteToken:
    base = judging.QuoteToken(
        binding=_binding(),
        include_confidential=False,
        include_uncertain=False,
        work=(judging.WorkItem("a\nb", (("a", "1"), ("b", "2"))),),
        calls=1,
    )
    return dataclasses.replace(base, **over)  # type: ignore[arg-type]


def test_equal_tokens_share_a_digest() -> None:
    assert _token().digest() == _token().digest()
    assert _binding().digest() == _binding().digest()


@pytest.mark.parametrize(
    "override",
    [
        {"binding": _binding(model="other")},
        {"binding": _binding(backend="openai-compatible")},
        {"binding": _binding(is_local=False)},
        {"binding": _binding(effective_confidential=True)},
        {"binding": _binding(rubric_digest="other")},
        {"binding": _binding(rubric_digest=None)},
        {"include_confidential": True},
        {"include_uncertain": True},
        {"calls": 2},
        {"work": ()},
        {"work": (judging.WorkItem("a\nb", (("a", "1"), ("b", "3"))),)},
        {"work": (judging.WorkItem("c\nd", (("a", "1"), ("b", "2"))),)},
    ],
)
def test_the_digest_differs_per_field(override: dict[str, object]) -> None:
    assert _token(**override).digest() != _token().digest()


def test_the_binding_digest_covers_every_field() -> None:
    fields = {f.name for f in dataclasses.fields(judging.QuoteBinding)}
    for name, value in (
        ("kind", "volatility"),
        ("model", "x"),
        ("backend", "x"),
        ("is_local", False),
        ("effective_confidential", True),
        ("rubric_digest", "x"),
    ):
        assert name in fields
        assert _binding(**{name: value}).digest() != _binding().digest(), name


def test_no_field_of_the_token_holds_a_time() -> None:
    names = {
        f.name
        for cls in (judging.QuoteToken, judging.QuoteBinding, judging.WorkItem)
        for f in dataclasses.fields(cls)
    }
    assert not names & {"created_at", "issued_at", "expires_at", "ttl", "now", "time"}


@pytest.mark.parametrize(
    ("over", "expected"),
    [
        ({"model": "other"}, ("model",)),
        ({"backend": "openai-compatible"}, ("backend",)),
        ({"is_local": False}, ("locality",)),
        ({"effective_confidential": True}, ("confidential",)),
        ({"rubric_digest": "other"}, ("rubric",)),
    ],
)
def test_the_comparator_names_the_one_field_that_moved(
    over: dict[str, object], expected: tuple[str, ...]
) -> None:
    assert judging.changed_fields(_binding(), _binding(**over)) == expected


def test_the_comparator_names_every_field_that_moved() -> None:
    moved = judging.changed_fields(
        _binding(),
        _binding(
            model="x",
            backend="y",
            is_local=False,
            effective_confidential=True,
            rubric_digest="z",
        ),
    )
    assert moved == ("model", "backend", "locality", "confidential", "rubric")


def test_an_unchanged_binding_names_nothing_and_a_work_set_is_not_a_binding() -> None:
    assert judging.changed_fields(_binding(), _binding()) == ()
    other_work = _token(work=(), calls=0)
    assert judging.changed_fields(_token().binding, other_work.binding) == ()


def test_quote_stale_is_plain_data() -> None:
    stale = judging.QuoteStale(kind="identity", changed=("model", "rubric"))
    assert stale.changed == ("model", "rubric")


# -- the identity quote ------------------------------------------------------ #


def test_a_quote_reports_served_and_to_judge_with_an_unreachable_backend(
    fx: _Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    fx.store(fx.groups[0])
    fx.store(fx.groups[1], verdict="different")

    quote = fx.quote()

    assert quote.kind == "identity"
    assert (quote.total, quote.served, quote.to_judge) == (5, 2, 3)
    assert quote.calls == 3
    assert quote.uncertain == 0
    assert fx.client.chats == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_a_quote_carries_no_eta() -> None:
    names = {f.name for f in dataclasses.fields(judging.JudgingQuote)}
    assert not names & {"eta", "eta_seconds", "duration", "seconds"}


def test_the_quote_binds_what_it_resolved(fx: _Fixture) -> None:
    quote = fx.quote()

    cfg = config.read_config(fx.root)
    assert quote.binding == judging.QuoteBinding(
        kind="identity",
        model=config.resolve_task_model(cfg, "adjudication"),
        backend=cfg.backend,
        is_local=False,
        effective_confidential=False,
        rubric_digest=_RUBRIC,
    )
    assert quote.token.binding == quote.binding


def test_an_edited_member_counts_in_to_judge_not_served(fx: _Fixture) -> None:
    fx.store(fx.groups[0])
    assert fx.quote().served == 1

    fx.edit(fx.groups[0].member_ids[0])

    after = fx.quote()
    assert (after.served, after.to_judge) == (0, 5)


def test_a_rubric_change_leaves_no_group_served(fx: _Fixture) -> None:
    for group in fx.groups[:2]:
        fx.store(group, rubric="a-rubric-this-build-does-not-have")

    quote = fx.quote()

    assert quote.served == 0
    assert quote.to_judge == 5
    assert judging.QuoteNotice("rubric_rejudge", count=2) in quote.notices


def test_a_stored_verdict_under_another_confidential_bit_is_not_served(
    fx: _Fixture,
) -> None:
    fx.store(fx.groups[0], include_confidential=True)

    plain = fx.quote(include_confidential=False)
    assert (plain.binding.effective_confidential, plain.served) == (False, 0)

    asked = fx.quote(include_confidential=True)
    assert (asked.binding.effective_confidential, asked.served) == (True, 1)

    fx.client.is_local = True
    exempt = fx.quote(include_confidential=False)
    assert (exempt.binding.effective_confidential, exempt.served) == (True, 1)


def test_kept_distinct_groups_are_dropped_before_the_quote(
    fx: _Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    dropped = fx.groups[2].member_ids
    monkeypatch.setattr(
        application_pending,
        "is_group_kept_distinct",
        lambda layout, member_ids, **_: tuple(member_ids) == dropped,
    )

    quote = fx.quote()

    assert (quote.total, quote.to_judge) == (4, 4)
    assert all(item.work_key != _key(dropped) for item in quote.token.work)


def test_the_work_set_is_the_ceiling_in_quote_order_with_member_digests(
    fx: _Fixture,
) -> None:
    fx.store(fx.groups[1])

    quote = fx.quote()

    assert [item.work_key for item in quote.token.work] == [
        _key(g.member_ids) for g in fx.groups if g is not fx.groups[1]
    ]
    current = application_pending.current_finding_digest(fx.layout.bundle_dir)
    first = quote.token.work[0]
    assert first.member_digests == tuple(
        (m, current(m)) for m in fx.groups[0].member_ids
    )
    assert quote.token.calls == quote.calls == len(quote.token.work)


def test_a_group_with_no_readable_member_adds_nothing_to_calls(
    fx: _Fixture,
) -> None:
    ghost = _group("concepts/ghost-a", "concepts/ghost-b")
    fx.groups.append(ghost)
    fx.report = CandidateGroupReport(groups=tuple(fx.groups), produced=6, retained=6)

    quote = fx.quote()

    assert quote.to_judge == 6
    assert quote.calls == 5
    assert quote.token.calls == 5
    ghost_item = quote.token.work[-1]
    assert ghost_item.work_key == _key(ghost.member_ids)
    assert ghost_item.member_digests == (
        ("concepts/ghost-a", ""),
        ("concepts/ghost-b", ""),
    )


def test_a_group_whose_members_are_all_confidential_adds_nothing_to_calls(
    fx: _Fixture,
) -> None:
    secret = _group("concepts/s-a", "concepts/s-b")
    for member in secret.member_ids:
        _write(fx.root, member, sensitivity="confidential")
    fx.groups.append(secret)
    fx.report = CandidateGroupReport(groups=tuple(fx.groups), produced=6, retained=6)
    fx.client.is_local = False

    blocked = fx.quote()
    assert (blocked.to_judge, blocked.calls) == (6, 5)

    fx.client.is_local = True
    exempt = fx.quote()
    assert (exempt.to_judge, exempt.calls) == (6, 6)


def test_an_unsupported_kind_is_refused() -> None:
    ports = judging.JudgingPorts(chat_client=lambda cfg, task: _Client())
    with pytest.raises(ValueError, match="volatility"):
        judging.quote_judging(Path(), "volatility", ports=ports)


def test_a_root_that_is_not_a_workspace_is_refused(tmp_path: Path) -> None:
    ports = judging.JudgingPorts(chat_client=lambda cfg, task: _Client())
    with pytest.raises(judging.JudgingRefused, match="refusing to quote"):
        judging.quote_judging(tmp_path, "identity", ports=ports)


def test_an_unreadable_config_is_refused(fx: _Fixture) -> None:
    fx.layout.config_path.write_text("model: [unterminated\n", encoding="utf-8")
    with pytest.raises(judging.JudgingRefused, match="failed while reading"):
        fx.quote()


def test_an_empty_candidate_set_quotes_zero(fx: _Fixture) -> None:
    fx.groups.clear()
    fx.report = CandidateGroupReport()

    quote = fx.quote()

    assert (quote.total, quote.served, quote.to_judge, quote.calls) == (0, 0, 0, 0)
    assert quote.token.work == ()


# -- judged-uncertain -------------------------------------------------------- #


def test_an_uncertain_item_is_not_re_priced_by_default(fx: _Fixture) -> None:
    fx.store(fx.groups[0], verdict="uncertain")

    quote = fx.quote()

    assert quote.uncertain == 1
    assert quote.served == 0
    assert quote.to_judge == 4
    assert quote.calls == 4
    assert _key(fx.groups[0].member_ids) not in [
        item.work_key for item in quote.token.work
    ]
    assert quote.token.include_uncertain is False


def test_opting_in_prices_the_uncertain_item_before_consent(fx: _Fixture) -> None:
    fx.store(fx.groups[0], verdict="uncertain")

    quote = fx.quote(include_uncertain=True)

    assert quote.uncertain == 1
    assert quote.to_judge == 5
    assert quote.calls == 5
    assert quote.token.include_uncertain is True
    assert quote.token.work[0].work_key == _key(fx.groups[0].member_ids)


def test_the_opt_in_is_bound_into_the_token(fx: _Fixture) -> None:
    fx.store(fx.groups[0], verdict="uncertain")
    default = fx.quote()
    opted = fx.quote(include_uncertain=True)
    assert default.token.digest() != opted.token.digest()


@pytest.mark.parametrize("opted_in", [False, True])
def test_an_edit_returns_an_uncertain_item_to_pending_judging(
    fx: _Fixture, opted_in: bool
) -> None:
    fx.store(fx.groups[0], verdict="uncertain")
    fx.edit(fx.groups[0].member_ids[0])

    quote = fx.quote(include_uncertain=opted_in)

    assert quote.uncertain == 0
    assert (quote.to_judge, quote.calls) == (5, 5)


def test_served_and_uncertain_and_fresh_account_for_every_group(
    fx: _Fixture,
) -> None:
    fx.store(fx.groups[0])
    fx.store(fx.groups[1], verdict="uncertain")

    quote = fx.quote()

    assert quote.total == quote.served + quote.uncertain + quote.to_judge == 5


# -- notices ----------------------------------------------------------------- #


def test_exactly_one_model_notice_names_the_model_and_backend(fx: _Fixture) -> None:
    quote = fx.quote()

    cfg = config.read_config(fx.root)
    model = [n for n in quote.notices if n.code == "model"]
    assert model == [
        judging.QuoteNotice(
            "model",
            model=config.resolve_task_model(cfg, "adjudication"),
            backend=cfg.backend,
        )
    ]


def test_a_capped_candidate_set_carries_the_truncation_notice(fx: _Fixture) -> None:
    fx.report = CandidateGroupReport(
        groups=tuple(fx.groups), produced=130, retained=100
    )
    assert judging.QuoteNotice("truncated", count=100, produced=130) in (
        fx.quote().notices
    )
    fx.report = CandidateGroupReport(groups=tuple(fx.groups), produced=5, retained=5)
    assert not [n for n in fx.quote().notices if n.code == "truncated"]


def _seed_job(
    fx: _Fixture | _RelFixture, *, started: datetime, ended: datetime | None = None
) -> None:
    conn = jobs.open_jobs(fx.layout.jobs_db_path)
    try:
        job_id = jobs.start_job(
            conn, "maintenance", started.strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        if ended is not None:
            conn.execute(
                "UPDATE jobs SET ended_at = ? WHERE id = ?",
                (ended.strftime("%Y-%m-%dT%H:%M:%SZ"), job_id),
            )
            conn.commit()
    finally:
        conn.close()


def _unattended(quote: judging.JudgingQuote) -> list[judging.QuoteNotice]:
    return [n for n in quote.notices if n.code == "unattended_active"]


def test_a_workspace_without_job_records_has_no_notice_and_gets_no_file(
    fx: _Fixture,
) -> None:
    assert not fx.layout.jobs_db_path.exists()

    quote = fx.quote()

    assert _unattended(quote) == []
    assert not fx.layout.jobs_db_path.exists()


def test_an_open_job_carries_the_notice(fx: _Fixture) -> None:
    _seed_job(fx, started=_NOW - timedelta(days=30))
    assert _unattended(fx.quote()) == [
        judging.QuoteNotice("unattended_active", state="active")
    ]


def test_a_job_started_inside_the_window_carries_the_notice(fx: _Fixture) -> None:
    window = config.read_config(fx.root).unattended.maintenance_interval_seconds
    _seed_job(
        fx,
        started=_NOW - timedelta(seconds=window - 10),
        ended=_NOW - timedelta(seconds=5),
    )
    assert _unattended(fx.quote()) == [
        judging.QuoteNotice("unattended_active", state="active")
    ]


def test_a_job_that_ended_long_ago_carries_no_notice(fx: _Fixture) -> None:
    window = config.read_config(fx.root).unattended.maintenance_interval_seconds
    _seed_job(
        fx,
        started=_NOW - timedelta(seconds=window + 60),
        ended=_NOW - timedelta(seconds=window + 30),
    )
    assert _unattended(fx.quote()) == []


def test_an_unreadable_job_record_yields_an_unknown_notice(fx: _Fixture) -> None:
    fx.layout.jobs_db_path.parent.mkdir(parents=True, exist_ok=True)
    fx.layout.jobs_db_path.write_bytes(b"this is not a database" * 50)

    assert _unattended(fx.quote()) == [
        judging.QuoteNotice("unattended_active", state="unknown")
    ]


def test_a_job_record_with_no_schema_carries_no_notice(fx: _Fixture) -> None:
    fx.layout.jobs_db_path.parent.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(fx.layout.jobs_db_path).close()

    assert _unattended(fx.quote()) == []


def test_a_job_with_an_unparseable_start_yields_an_unknown_notice(
    fx: _Fixture,
) -> None:
    _seed_job(fx, started=_NOW - timedelta(days=30), ended=_NOW)
    conn = jobs.open_jobs(fx.layout.jobs_db_path)
    try:
        conn.execute("UPDATE jobs SET started_at = 'yesterday-ish'")
        conn.commit()
    finally:
        conn.close()

    assert _unattended(fx.quote()) == [
        judging.QuoteNotice("unattended_active", state="unknown")
    ]


def test_the_unattended_notice_changes_neither_price_nor_token(fx: _Fixture) -> None:
    quiet = fx.quote()
    _seed_job(fx, started=_NOW)

    busy = fx.quote()

    assert _unattended(quiet) == []
    assert len(_unattended(busy)) == 1
    assert (busy.total, busy.served, busy.to_judge, busy.calls) == (
        quiet.total,
        quiet.served,
        quiet.to_judge,
        quiet.calls,
    )
    assert busy.token == quiet.token
    assert busy.token.digest() == quiet.token.digest()


# -- stability ---------------------------------------------------------------- #


def test_two_quotes_with_no_change_bind_the_same_context_and_work(
    fx: _Fixture,
) -> None:
    fx.store(fx.groups[0])
    first = fx.quote()
    later = fx.quote(now=_NOW + timedelta(days=400))

    assert first.token == later.token
    assert first.token.digest() == later.token.digest()
    assert first.binding == later.binding


def test_an_old_token_is_not_stale_by_age(fx: _Fixture) -> None:
    token = fx.quote().token
    much_later = fx.quote(now=_NOW + timedelta(days=4000))
    assert judging.changed_fields(token.binding, much_later.binding) == ()


def test_the_observer_defaults_to_no_ops() -> None:
    observer = judging.JudgingObserver()
    observer.progress(judging.JudgingProgress(1, 1, "k", "same"))
    observer.chunk_persisted(1)


def test_a_quote_creates_no_store(fx: _Fixture) -> None:
    fx.quote()
    assert not fx.layout.findings_db_path.exists()


# -- the run: support --------------------------------------------------------- #


def _reply(verdict: str = "same") -> str:
    return json.dumps({"verdict": verdict, "confidence": 0.9, "rationale": "Judged."})


class _Judge:
    """A scripted chat client: one reply (or raised error) per call, in order,
    `same` once the script runs out. `on_chat(n)` runs inside the n-th call, so a
    test can stop the run, edit a member or log an event from inside a call."""

    def __init__(
        self,
        script: Sequence[str | Exception] = (),
        *,
        is_local: bool = False,
        on_chat: Callable[[int], None] | None = None,
        log: list[str] | None = None,
        default_reply: str | None = None,
    ) -> None:
        self.default_reply = default_reply
        self.script = list(script)
        self.is_local = is_local
        self.on_chat = on_chat
        self.log = log if log is not None else []
        self.calls = 0

    @property
    def locality(self) -> BackendHostLocality:
        return BackendHostLocality(is_local=self.is_local, display_host="localhost")

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls += 1
        self.log.append("chat")
        if self.on_chat is not None:
            self.on_chat(self.calls)
        step = self.script[self.calls - 1] if self.calls <= len(self.script) else None
        if isinstance(step, Exception):
            raise step
        if step is not None:
            return step
        return self.default_reply if self.default_reply is not None else _reply()


class _Sections:
    """A commit-section port that records its entries and exits, and can be
    busy: `busy` refusals first, or every time with `always_busy`."""

    def __init__(
        self, log: list[str], *, busy: int = 0, always_busy: bool = False
    ) -> None:
        self.log = log
        self.busy = busy
        self.always_busy = always_busy
        self.attempts = 0

    @contextlib.contextmanager
    def __call__(self) -> Iterator[None]:
        self.attempts += 1
        if self.always_busy or self.attempts <= self.busy:
            raise lock.WorkspaceBusyError("held by another process")
        self.log.append("enter")
        try:
            yield
        finally:
            self.log.append("exit")


class _Recorder(judging.JudgingObserver):
    def __init__(self, *, fail_on_progress: int | None = None) -> None:
        self.fail_on_progress = fail_on_progress
        self.progress_events: list[judging.JudgingProgress] = []
        self.persisted: list[int] = []

    def progress(self, event: judging.JudgingProgress) -> None:
        self.progress_events.append(event)
        if event.index == self.fail_on_progress:
            raise RuntimeError("the client's callback failed")

    def chunk_persisted(self, persisted: int) -> None:
        self.persisted.append(persisted)


def _run(
    fx: _Fixture,
    token: judging.QuoteToken | None,
    *,
    judge: _Judge | None = None,
    stop: StopToken | None = None,
    observer: judging.JudgingObserver | None = None,
    chunk_size: int | None = None,
    sleeps: list[float] | None = None,
    **ports: object,
) -> judging.JudgingOutcome | judging.QuoteStale | judging.RunRefused:
    if judge is not None:
        fx.client = judge  # type: ignore[assignment]
    sleep = sleeps.append if sleeps is not None else (lambda seconds: None)
    return judging.run_judging(
        fx.root,
        token,
        stop=stop or StopToken(),
        ports=dataclasses.replace(fx.ports(), **{"sleep": sleep, **ports}),  # type: ignore[arg-type]
        observer=observer or judging.JudgingObserver(),
        chunk_size=chunk_size,
    )


def _outcome(result: object) -> judging.JudgingOutcome:
    assert isinstance(result, judging.JudgingOutcome), result
    return result


def _stored_keys(fx: _Fixture) -> set[str]:
    if not fx.layout.findings_db_path.exists():
        return set()
    conn = derived.open_derived_connection(fx.layout.findings_db_path)
    try:
        return {
            store.group_key_for(row.member_ids)
            for row in store.open_adjudications(conn)
        }
    finally:
        conn.close()


# -- the run: consent and staleness ------------------------------------------- #


def test_a_run_without_a_token_is_refused_before_anything_is_built(
    tmp_path: Path,
) -> None:
    built: list[object] = []

    def _never(cfg: object, task: object) -> object:
        built.append(task)
        raise AssertionError("a refused run builds no client")

    refused = judging.run_judging(
        tmp_path / "not-even-a-workspace",
        None,
        stop=StopToken(),
        ports=judging.JudgingPorts(chat_client=_never),  # type: ignore[arg-type]
    )

    assert refused == judging.RunRefused("no_quote_token")
    assert built == []
    assert not (tmp_path / "not-even-a-workspace").exists()


@pytest.mark.parametrize(
    ("field", "moved"),
    [
        ("model", {"model": "an-older-model"}),
        ("backend", {"backend": "openai-compatible"}),
        ("locality", {"is_local": True}),
        ("confidential", {"effective_confidential": True}),
        ("rubric", {"rubric_digest": "an-older-rubric"}),
    ],
)
def test_a_moved_binding_field_is_stale_and_spends_and_writes_nothing(
    fx: _Fixture, field: str, moved: dict[str, object]
) -> None:
    token = fx.quote().token
    stale = dataclasses.replace(
        token,
        binding=dataclasses.replace(token.binding, **moved),  # type: ignore[arg-type]
    )
    judge = _Judge()

    result = _run(fx, stale, judge=judge)

    assert isinstance(result, judging.QuoteStale)
    assert result.changed == (field,)
    assert judge.calls == 0
    assert not fx.layout.findings_db_path.exists()


def test_a_backend_that_moved_onto_this_machine_names_locality_and_confidential(
    fx: _Fixture,
) -> None:
    token = fx.quote().token

    result = _run(fx, token, judge=_Judge(is_local=True))

    assert result == judging.QuoteStale("identity", ("locality", "confidential"))


def test_a_moved_work_set_alone_is_not_stale(fx: _Fixture) -> None:
    token = fx.quote().token
    fx.store(fx.groups[0])

    result = _run(fx, token, judge=_Judge())

    assert isinstance(result, judging.JudgingOutcome)


def test_an_old_token_over_unchanged_state_is_not_stale(fx: _Fixture) -> None:
    token = fx.quote(now=_NOW - timedelta(days=400)).token

    assert isinstance(_run(fx, token, judge=_Judge()), judging.JudgingOutcome)


def test_a_root_that_is_not_a_workspace_is_refused_by_a_run(tmp_path: Path) -> None:
    token = _token()

    with pytest.raises(judging.JudgingRefused):
        judging.run_judging(
            tmp_path,
            token,
            stop=StopToken(),
            ports=judging.JudgingPorts(chat_client=lambda cfg, task: _Judge()),
        )


# -- the run: the binding ceiling --------------------------------------------- #


def test_a_run_judges_exactly_the_quoted_groups_and_persists_them(fx: _Fixture) -> None:
    token = fx.quote().token
    judge = _Judge()

    outcome = _outcome(_run(fx, token, judge=judge))

    assert outcome.status == "completed"
    assert (outcome.judged, outcome.calls, outcome.persisted) == (5, 5, 5)
    assert (outcome.deferred, outcome.unpersisted, outcome.not_in_quote) == (0, 0, ())
    assert judge.calls == 5
    assert _stored_keys(fx) == {_key(g.member_ids) for g in fx.groups}


def test_new_work_is_not_judged_and_is_named(fx: _Fixture) -> None:
    fx.report = CandidateGroupReport(
        groups=tuple(fx.groups[:3]), produced=3, retained=3
    )
    token = fx.quote().token
    fx.report = CandidateGroupReport(groups=tuple(fx.groups), produced=5, retained=5)
    judge = _Judge()

    outcome = _outcome(_run(fx, token, judge=judge))

    assert judge.calls == 3
    assert outcome.not_in_quote == (
        _key(fx.groups[3].member_ids),
        _key(fx.groups[4].member_ids),
    )
    assert outcome.settled_since_quote == 0
    assert _stored_keys(fx) == {_key(g.member_ids) for g in fx.groups[:3]}


def test_a_group_answered_elsewhere_is_settled_and_not_paid_for_again(
    fx: _Fixture,
) -> None:
    token = fx.quote().token
    fx.store(fx.groups[1])
    judge = _Judge()

    outcome = _outcome(_run(fx, token, judge=judge))

    assert (outcome.settled_since_quote, outcome.judged, judge.calls) == (1, 4, 4)
    assert outcome.not_in_quote == ()


def test_a_vanished_group_is_settled_not_judged(fx: _Fixture) -> None:
    token = fx.quote().token
    fx.report = CandidateGroupReport(
        groups=tuple(fx.groups[:2] + fx.groups[3:]), produced=4, retained=4
    )
    judge = _Judge()

    outcome = _outcome(_run(fx, token, judge=judge))

    assert (outcome.settled_since_quote, judge.calls) == (1, 4)


def test_a_group_ruled_kept_distinct_since_the_quote_is_settled(
    fx: _Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = fx.quote().token
    ruled = fx.groups[2].member_ids
    monkeypatch.setattr(
        application_pending,
        "is_group_kept_distinct",
        lambda layout, member_ids, **_: tuple(member_ids) == ruled,
    )
    judge = _Judge()

    outcome = _outcome(_run(fx, token, judge=judge))

    assert (outcome.settled_since_quote, judge.calls) == (1, 4)
    assert _key(ruled) not in _stored_keys(fx)


def test_a_member_edited_since_the_quote_is_no_longer_the_quoted_item(
    fx: _Fixture,
) -> None:
    token = fx.quote().token
    fx.edit(fx.groups[0].member_ids[0])
    judge = _Judge()

    outcome = _outcome(_run(fx, token, judge=judge))

    assert outcome.not_in_quote == (_key(fx.groups[0].member_ids),)
    assert outcome.settled_since_quote == 1
    assert judge.calls == 4


def test_a_default_token_never_judges_an_uncertain_item(fx: _Fixture) -> None:
    fx.store(fx.groups[0], verdict="uncertain")
    token = fx.quote().token
    judge = _Judge()

    outcome = _outcome(_run(fx, token, judge=judge))

    assert judge.calls == 4
    assert outcome.settled_since_quote == 0
    assert outcome.not_in_quote == ()


def test_an_opted_in_token_rejudges_an_uncertain_item_and_replaces_its_verdict(
    fx: _Fixture,
) -> None:
    fx.store(fx.groups[0], verdict="uncertain")
    token = fx.quote(include_uncertain=True).token

    outcome = _outcome(_run(fx, token, judge=_Judge()))

    assert outcome.judged == 5
    after = fx.quote()
    assert (after.served, after.uncertain, after.to_judge) == (5, 0, 0)


def test_a_group_that_makes_no_call_is_judged_free_and_never_persisted(
    fx: _Fixture,
) -> None:
    ghost = _group("concepts/ghost-a", "concepts/ghost-b")
    fx.report = CandidateGroupReport(groups=(*fx.groups, ghost), produced=6, retained=6)
    token = fx.quote().token
    assert (token.calls, len(token.work)) == (5, 6)
    judge = _Judge()

    outcome = _outcome(_run(fx, token, judge=judge))

    assert (outcome.judged, outcome.calls, judge.calls) == (6, 5, 5)
    assert (outcome.persisted, outcome.dropped) == (5, 1)


def test_a_call_past_the_ceiling_is_refused_before_it_is_made(fx: _Fixture) -> None:
    token = fx.quote().token
    judge = _Judge()

    def _greedy(groups: list[CandidateGroup], *, llm: object, **_: object) -> object:
        llm.chat([]), llm.chat([])  # type: ignore[attr-defined]
        return AdjudicationBatch(
            results=[AdjudicatedCandidate(groups[0], Verdict.SAME, 0.9, "r")]
        )

    with pytest.raises(AssertionError):
        _run(fx, token, judge=judge, adjudicate_candidates=_greedy)

    assert judge.calls == token.calls == 5


# -- the run: chunks, persistence and cancellation ---------------------------- #


def test_each_chunk_persists_before_the_next_chunk_is_judged(fx: _Fixture) -> None:
    log: list[str] = []
    token = fx.quote().token

    _run(fx, token, judge=_Judge(log=log), commit_section=_Sections(log))

    assert log == ["chat", "enter", "exit"] * 5


def test_a_larger_chunk_judges_its_groups_before_one_persist(fx: _Fixture) -> None:
    log: list[str] = []
    token = fx.quote().token

    outcome = _outcome(
        _run(
            fx,
            token,
            judge=_Judge(log=log),
            commit_section=_Sections(log),
            chunk_size=2,
        )
    )

    assert log == ["chat", "chat", "enter", "exit"] * 2 + ["chat", "enter", "exit"]
    assert outcome.persisted == 5


def test_a_chunk_size_below_one_is_refused(fx: _Fixture) -> None:
    with pytest.raises(ValueError, match="chunk_size"):
        _run(fx, fx.quote().token, chunk_size=0)


def test_a_stop_from_inside_a_call_lets_that_chunk_persist_and_defers_the_rest(
    fx: _Fixture,
) -> None:
    stop = StopToken()
    token = fx.quote().token
    judge = _Judge(on_chat=lambda n: stop.set() if n == 2 else None)

    outcome = _outcome(_run(fx, token, judge=judge, stop=stop))

    assert outcome.status == "stopped"
    assert (outcome.judged, outcome.persisted, outcome.deferred) == (2, 2, 3)
    assert judge.calls == 2
    assert _stored_keys(fx) == {_key(g.member_ids) for g in fx.groups[:2]}
    again = fx.quote()
    assert (again.served, again.to_judge) == (2, 3)


def test_a_stop_before_the_first_chunk_makes_no_call(fx: _Fixture) -> None:
    stop = StopToken()
    stop.set()
    judge = _Judge()

    outcome = _outcome(_run(fx, fx.quote().token, judge=judge, stop=stop))

    assert outcome.status == "stopped"
    assert (outcome.judged, outcome.calls, outcome.deferred) == (0, 0, 5)
    assert judge.calls == 0
    assert not fx.layout.findings_db_path.exists()


def test_a_stop_during_the_last_chunk_still_completes(fx: _Fixture) -> None:
    stop = StopToken()
    judge = _Judge(on_chat=lambda n: stop.set() if n == 5 else None)

    outcome = _outcome(_run(fx, fx.quote().token, judge=judge, stop=stop))

    assert (outcome.status, outcome.deferred, outcome.persisted) == ("completed", 0, 5)


# -- the run: what a persisted verdict does to the queue ---------------------- #


def _seed_rows(fx: _Fixture) -> None:
    """An open, unjudged identity row per group, as a daemon pass leaves them."""
    from openkos.application import queue_producers
    from openkos.state import pending_queue as pq

    current = application_pending.current_finding_digest(fx.layout.bundle_dir)
    conn = derived.open_derived_connection(fx.layout.findings_db_path)
    try:
        for group in fx.groups:
            proposal = queue_producers.identity_group_proposal(
                group, current_digest=current, result=None
            )
            assert proposal is not None
            pq.upsert_proposal(
                conn,
                proposal,
                commit_section=contextlib.nullcontext,
                bundle_dir=fx.layout.bundle_dir,
            )
    finally:
        conn.close()


def _queue(fx: _Fixture) -> dict[tuple[str, ...], tuple[str, object]]:
    from openkos.state import pending_queue as pq

    conn = derived.open_derived_connection(fx.layout.findings_db_path)
    try:
        return {
            tuple(sorted(item.targets)): (
                item.status,
                (json.loads(item.payload)["adjudication"] or {}).get("verdict"),
            )
            for item in pq.all_items(conn)
        }
    finally:
        conn.close()


def test_a_run_amends_open_rows_declines_different_and_never_merges(
    fx: _Fixture,
) -> None:
    _seed_rows(fx)
    before = {
        path.name: path.read_bytes()
        for path in sorted(fx.layout.bundle_dir.rglob("*.md"))
    }
    token = fx.quote().token
    judge = _Judge([_reply("same"), _reply("different"), _reply("uncertain")])

    outcome = _outcome(_run(fx, token, judge=judge))

    assert outcome.status == "completed"
    queue = _queue(fx)
    keys = [tuple(sorted(g.member_ids)) for g in fx.groups]
    assert queue[keys[0]] == ("pending", "same")
    assert queue[keys[1]][0] == "declined"
    assert queue[keys[2]] == ("pending", "uncertain")
    assert queue[keys[3]] == ("pending", "same")
    after = {
        path.name: path.read_bytes()
        for path in sorted(fx.layout.bundle_dir.rglob("*.md"))
    }
    assert after == before


def test_a_verdict_about_content_edited_during_the_call_is_dropped(
    fx: _Fixture,
) -> None:
    judge = _Judge(
        on_chat=lambda n: fx.edit(fx.groups[0].member_ids[0]) if n == 1 else None
    )

    outcome = _outcome(_run(fx, fx.quote().token, judge=judge))

    assert (outcome.judged, outcome.persisted, outcome.dropped) == (5, 4, 1)
    assert _key(fx.groups[0].member_ids) not in _stored_keys(fx)


# -- the run: progress and the observer --------------------------------------- #


def test_progress_events_carry_the_run_index_and_total(fx: _Fixture) -> None:
    fx.report = CandidateGroupReport(
        groups=tuple(fx.groups[:3]), produced=3, retained=3
    )
    token = fx.quote().token
    observer = _Recorder()

    outcome = _outcome(
        _run(
            fx,
            token,
            judge=_Judge([_reply("same"), _reply("different"), _reply("uncertain")]),
            observer=observer,
        )
    )

    assert [(e.index, e.total) for e in observer.progress_events] == [
        (1, 3),
        (2, 3),
        (3, 3),
    ]
    assert [e.work_key for e in observer.progress_events] == [
        _key(g.member_ids) for g in fx.groups[:3]
    ]
    assert [e.verdict for e in observer.progress_events] == [
        "same",
        "different",
        "uncertain",
    ]
    assert observer.persisted == [1, 2, 3]
    assert outcome.observer_failed is False


def test_progress_indices_are_run_wide_across_a_larger_chunk(fx: _Fixture) -> None:
    observer = _Recorder()

    _run(fx, fx.quote().token, judge=_Judge(), observer=observer, chunk_size=2)

    assert [(e.index, e.total) for e in observer.progress_events] == [
        (n, 5) for n in range(1, 6)
    ]
    assert observer.persisted == [2, 4, 5]


def test_a_progress_event_never_carries_the_rationale(fx: _Fixture) -> None:
    observer = _Recorder()

    _run(fx, fx.quote().token, judge=_Judge(), observer=observer)

    assert {f.name for f in dataclasses.fields(judging.JudgingProgress)} == {
        "index",
        "total",
        "work_key",
        "verdict",
    }
    assert len(observer.progress_events) == 5
    assert all("Judged." not in repr(e) for e in observer.progress_events)


@pytest.mark.parametrize("chunk_size", [1, 2])
def test_an_observer_that_raises_loses_no_paid_work(
    fx: _Fixture, chunk_size: int
) -> None:
    observer = _Recorder(fail_on_progress=2)

    outcome = _outcome(
        _run(
            fx,
            fx.quote().token,
            judge=_Judge(),
            observer=observer,
            chunk_size=chunk_size,
        )
    )

    assert outcome.observer_failed is True
    assert (outcome.status, outcome.judged, outcome.persisted) == ("completed", 5, 5)
    assert [e.index for e in observer.progress_events] == [1, 2]
    assert _stored_keys(fx) == {_key(g.member_ids) for g in fx.groups}


# -- the run: a busy workspace ------------------------------------------------ #


def test_a_transient_busy_workspace_is_absorbed_and_the_run_continues(
    fx: _Fixture,
) -> None:
    log: list[str] = []
    sections = _Sections(log, busy=1)
    sleeps: list[float] = []

    outcome = _outcome(
        _run(
            fx,
            fx.quote().token,
            judge=_Judge(log=log),
            commit_section=sections,
            sleeps=sleeps,
        )
    )

    assert (outcome.status, outcome.persisted, outcome.unpersisted) == (
        "completed",
        5,
        0,
    )
    assert sleeps == [1.0]
    assert sections.attempts == 6


def test_a_persistently_busy_workspace_stops_the_spend_and_says_so(
    fx: _Fixture,
) -> None:
    log: list[str] = []
    sections = _Sections(log, always_busy=True)
    sleeps: list[float] = []
    judge = _Judge(log=log)
    observer = _Recorder()

    outcome = _outcome(
        _run(
            fx,
            fx.quote().token,
            judge=judge,
            commit_section=sections,
            sleeps=sleeps,
            observer=observer,
        )
    )

    assert outcome.status == "unpersisted"
    assert outcome.unpersisted_cause == "workspace_busy"
    assert (outcome.judged, outcome.persisted, outcome.unpersisted) == (1, 0, 1)
    assert (outcome.deferred, outcome.calls) == (4, 1)
    assert judge.calls == 1
    assert sections.attempts == judging.PERSIST_ATTEMPTS == 3
    assert sleeps == [1.0, 2.0]
    assert observer.persisted == []
    assert not fx.layout.findings_db_path.exists()


def test_no_lock_is_held_while_the_run_waits_between_attempts(fx: _Fixture) -> None:
    log: list[str] = []
    sections = _Sections(log, busy=2)

    _run(
        fx,
        fx.quote().token,
        judge=_Judge(log=log),
        commit_section=sections,
        sleep=lambda seconds: log.append("sleep"),
    )

    assert log[:5] == ["chat", "sleep", "sleep", "enter", "exit"]


def test_a_store_error_is_reported_as_unpersisted_with_its_cause(
    fx: _Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(store, "record_adjudications", _boom)
    judge = _Judge()

    outcome = _outcome(_run(fx, fx.quote().token, judge=judge))

    assert outcome.status == "unpersisted"
    assert outcome.unpersisted_cause == "store_error"
    assert (outcome.unpersisted, outcome.persisted, outcome.deferred) == (1, 0, 4)
    assert judge.calls == 1


# -- the run: backend errors -------------------------------------------------- #


def test_a_failed_item_ends_the_run_with_its_prefix_kept(fx: _Fixture) -> None:
    capped = BackendGenerationCapped("the reply hit the cap")
    judge = _Judge([_reply(), _reply(), capped])

    outcome = _outcome(_run(fx, fx.quote().token, judge=judge))

    assert outcome.status == "failed"
    assert outcome.failure is capped
    assert outcome.failed_at == 3
    assert (outcome.judged, outcome.persisted, outcome.deferred) == (2, 2, 2)
    assert (outcome.calls, judge.calls) == (3, 3)
    assert _stored_keys(fx) == {_key(g.member_ids) for g in fx.groups[:2]}


def test_failed_at_is_the_run_position_not_the_chunk_position(fx: _Fixture) -> None:
    judge = _Judge([_reply(), _reply(), _reply(), BackendGenerationCapped("cap")])

    outcome = _outcome(_run(fx, fx.quote().token, judge=judge, chunk_size=2))

    assert outcome.failed_at == 4
    assert (outcome.judged, outcome.persisted, outcome.deferred) == (3, 3, 1)
    assert _stored_keys(fx) == {_key(g.member_ids) for g in fx.groups[:3]}


@pytest.mark.parametrize(
    "error", [BackendUnavailable("down"), BackendModelNotFound("no such model")]
)
def test_an_availability_error_propagates_after_the_prefix_is_persisted(
    fx: _Fixture, error: Exception
) -> None:
    observer = _Recorder()
    judge = _Judge([_reply(), _reply(), error])

    with pytest.raises(type(error)) as raised:
        _run(fx, fx.quote().token, judge=judge, observer=observer)

    assert raised.value is error
    assert _stored_keys(fx) == {_key(g.member_ids) for g in fx.groups[:2]}
    assert observer.persisted == [1, 2]


def test_an_availability_error_on_the_first_call_writes_nothing(fx: _Fixture) -> None:
    with pytest.raises(BackendUnavailable):
        _run(fx, fx.quote().token, judge=_Judge([BackendUnavailable("down")]))

    assert not fx.layout.findings_db_path.exists()


def test_a_busy_workspace_wins_over_a_backend_failure_in_the_same_chunk(
    fx: _Fixture,
) -> None:
    capped = BackendGenerationCapped("cap")
    judge = _Judge([_reply(), capped])

    outcome = _outcome(
        _run(
            fx,
            fx.quote().token,
            judge=judge,
            chunk_size=2,
            commit_section=_Sections([], always_busy=True),
        )
    )

    assert outcome.status == "unpersisted"
    assert outcome.failure is capped
    assert (outcome.failed_at, outcome.unpersisted, outcome.deferred) == (2, 1, 3)


# -- the run: locks, the daemon and silence ----------------------------------- #


def test_the_workspace_lock_is_free_during_every_model_call(fx: _Fixture) -> None:
    """Another process asking for the lock in the middle of a call is not
    refused: the run holds it only for a chunk's persist."""
    from openkos.application import lock_wait

    free_during_call: list[bool] = []

    def _probe(n: int) -> None:
        try:
            with lock.workspace_lock(fx.root):
                free_during_call.append(True)
        except lock.WorkspaceBusyError:
            free_during_call.append(False)

    outcome = _outcome(
        _run(
            fx,
            fx.quote().token,
            judge=_Judge(on_chat=_probe),
            commit_section=lock_wait.locked_commit_section(fx.root, wait_seconds=0),
        )
    )

    assert free_during_call == [True] * 5
    assert outcome.persisted == 5


def test_no_section_is_ever_open_while_a_chat_call_runs(fx: _Fixture) -> None:
    log: list[str] = []
    sections = _Sections(log)
    open_during_call: list[bool] = []

    def _record(n: int) -> None:
        open_during_call.append(log.count("enter") != log.count("exit"))

    _run(
        fx,
        fx.quote().token,
        judge=_Judge(log=log, on_chat=_record),
        commit_section=sections,
        chunk_size=2,
    )

    assert open_during_call == [False] * 5


def test_an_active_engine_does_not_block_a_run_and_its_records_are_untouched(
    fx: _Fixture,
) -> None:
    _seed_job(fx, started=_NOW - timedelta(days=30))
    before = fx.layout.jobs_db_path.read_bytes()
    token = fx.quote().token

    outcome = _outcome(_run(fx, token, judge=_Judge()))

    assert (outcome.status, outcome.persisted) == ("completed", 5)
    assert [n.code for n in fx.quote().notices].count("unattended_active") == 1
    assert fx.layout.jobs_db_path.read_bytes() == before


def test_a_run_writes_nothing_to_a_stream(
    fx: _Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(fx, fx.quote().token, judge=_Judge(), observer=_Recorder())

    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")


# -- relation_type: the fixture ------------------------------------------------ #


def _edge_reply(kind: str = "references") -> str:
    return json.dumps({"type": kind, "rationale": "Typed."})


def _edge_judge(*args: object, **kwargs: object) -> _Judge:
    """A scripted client that types an edge `references` once its script runs
    out, where `_Judge` would answer an identity verdict."""
    kwargs.setdefault("default_reply", _edge_reply())
    return _Judge(*args, **kwargs)  # type: ignore[arg-type]


class _RelFixture:
    """Five untyped edges between readable private concepts. The candidate walk
    is injected (a fixed, mutable list); the typing, the graph, the serve
    partition and the persist are the shipped ones."""

    def __init__(self, root: Path, edges: list[Edge]) -> None:
        self.root = root
        self.layout = config.WorkspaceLayout(root)
        self.edges = edges
        self.client: _Judge = _edge_judge()
        self.tasks: list[str | None] = []
        self.graphs = 0

    def _chat_client(self, cfg: config.Config, task: str | None) -> _Judge:
        self.tasks.append(task)
        return self.client

    def _build_graph(self, *args: object, **kwargs: object) -> object:
        self.graphs += 1
        return sqlite_graph.build_graph(*args, **kwargs)  # type: ignore[arg-type]

    def ports(self, **over: object) -> judging.JudgingPorts:
        return dataclasses.replace(
            judging.JudgingPorts(
                chat_client=self._chat_client,
                candidate_edges=lambda bundle_dir, **_: list(self.edges),
                open_proximity=lambda path: None,
                build_graph=self._build_graph,  # type: ignore[arg-type]
                now=lambda: _NOW,
                sleep=lambda seconds: None,
            ),
            **over,  # type: ignore[arg-type]
        )

    def quote(
        self, *, include_confidential: bool = False, **over: object
    ) -> judging.JudgingQuote:
        return judging.quote_judging(
            self.root,
            "relation_type",
            include_confidential=include_confidential,
            ports=self.ports(**over),
        )

    def seed(
        self,
        edge: Edge,
        *,
        kind: str = "references",
        include_confidential: bool = False,
    ) -> None:
        """Persist a servable suggestion for `edge`."""
        current = application_pending.current_finding_digest(self.layout.bundle_dir)
        conn = derived.open_derived_connection(self.layout.findings_db_path)
        try:
            edge_store.record_edge_suggestions(
                conn,
                [
                    edge_store.PersistedEdgeSuggestion(
                        source_id=edge.source_id,
                        target_id=edge.target_id,
                        suggested_type=kind,
                        rationale="Stored.",
                        include_confidential=include_confidential,
                        input_digests=tuple(
                            edge_store.InputDigest(
                                input_ref=ref, digest=current(ref) or ""
                            )
                            for ref in (edge.source_id, edge.target_id)
                        ),
                    )
                ],
            )
        finally:
            conn.close()

    def stored(self) -> dict[str, str]:
        """The persisted suggestions: directed key -> suggested type."""
        if not self.layout.findings_db_path.exists():
            return {}
        conn = derived.open_derived_connection(self.layout.findings_db_path)
        try:
            return {
                edge_store.pair_key_for(row.source_id, row.target_id): (
                    row.suggested_type
                )
                for row in edge_store.open_edge_suggestions(conn)
            }
        finally:
            conn.close()

    def edit(self, concept_id: str) -> None:
        path = self.layout.bundle_dir / f"{concept_id}.md"
        path.write_text(path.read_text(encoding="utf-8") + "\nEdited.\n", "utf-8")


def _edge_key(edge: Edge) -> str:
    return edge_store.pair_key_for(edge.source_id, edge.target_id)


@pytest.fixture
def rel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _RelFixture:
    root = make_workspace(tmp_path, monkeypatch)
    edges = [
        Edge(source_id=f"concepts/r{n}-a", target_id=f"concepts/r{n}-b")
        for n in range(5)
    ]
    for edge in edges:
        _write(root, edge.source_id)
        _write(root, edge.target_id)
    return _RelFixture(root, edges)


# -- relation_type: the quote -------------------------------------------------- #


def test_a_relation_quote_is_kind_tagged_and_splits_served_from_to_judge(
    rel: _RelFixture,
) -> None:
    rel.seed(rel.edges[0])
    rel.seed(rel.edges[1])

    quote = rel.quote()

    assert quote.kind == "relation_type"
    assert (quote.total, quote.served, quote.to_judge, quote.calls) == (5, 2, 3, 3)
    assert quote.uncertain == 0
    assert quote.token.calls == 3


def test_a_relation_quote_binds_the_edge_typing_model_and_no_rubric(
    rel: _RelFixture,
) -> None:
    (rel.root / "openkos.yaml").write_text(
        (rel.root / "openkos.yaml").read_text(encoding="utf-8")
        + "models:\n  edge_typing: edge-model\n  adjudication: judge-model\n",
        encoding="utf-8",
    )

    quote = rel.quote()

    assert quote.binding.kind == "relation_type"
    assert quote.binding.model == "edge-model"
    assert quote.binding.rubric_digest is None
    assert set(rel.tasks) == {"edge_typing"}
    [model_notice] = [n for n in quote.notices if n.code == "model"]
    assert model_notice.model == "edge-model"


def test_a_relation_quote_makes_no_model_call_and_writes_nothing(
    rel: _RelFixture,
) -> None:
    rel.client = _edge_judge([BackendUnavailable("the backend is down")])

    quote = rel.quote()

    assert quote.to_judge == 5
    assert rel.client.calls == 0
    assert not rel.layout.findings_db_path.exists()


def test_a_relation_quote_states_what_the_shipped_cost_question_states(
    rel: _RelFixture,
) -> None:
    """The counts are the relations service's own `CostQuote`: the same edges
    asked the same question price the same under either surface."""
    rel.seed(rel.edges[2])
    asked: list[relations.CostQuote] = []

    class _Decline(_NoRelationsObserver):
        def confirm_cost(self, quote: relations.CostQuote) -> bool:
            asked.append(quote)
            return False

    ports = rel.ports()
    relations.suggest_relations(
        rel.root,
        relations.SuggestRelationsRequest(),
        relations.SuggestRelationsPorts(
            chat_client=lambda cfg, task: rel.client,
            zero_state_message=lambda *_: "",
            autocommit=lambda *_: None,
            refresh_derived=lambda layout: None,
            resolve_local_exemption=lambda client, cfg: False,
            open_proximity=ports.open_proximity,
            candidate_edges=ports.candidate_edges,
        ),
        _Decline(),
    )

    quote = rel.quote()

    [cost] = asked
    assert (quote.total, quote.served, quote.to_judge) == (
        cost.total,
        cost.served,
        cost.to_type,
    )


class _NoRelationsObserver:
    """A relations observer that says nothing and answers no to the cost."""

    def walk_incomplete(self, bundle_dir: Path, **kwargs: object) -> None: ...

    def workspace_header(self, root: Path) -> None: ...

    def empty_window(self, edge_offset: int) -> None: ...

    def candidate_notices(self, truncation: object, quarantine: object) -> None: ...

    def no_candidates(self, message: str) -> None: ...

    def warn(self, message: str) -> None: ...

    def serve_split(self, served: int, total: int, fresh: int) -> None: ...

    def confirm_cost(self, quote: relations.CostQuote) -> bool:
        return False

    def edge_progress(self, index: int, count: int, suggestion: object) -> None: ...


def test_a_relation_work_item_is_the_directed_edge_with_its_endpoint_digests(
    rel: _RelFixture,
) -> None:
    rel.seed(rel.edges[0])
    current = application_pending.current_finding_digest(rel.layout.bundle_dir)

    quote = rel.quote()

    assert [item.work_key for item in quote.token.work] == [
        _edge_key(edge) for edge in rel.edges[1:]
    ]
    first = quote.token.work[0]
    assert first.member_digests == tuple(
        sorted(
            (ref, current(ref) or "")
            for ref in (rel.edges[1].source_id, rel.edges[1].target_id)
        )
    )


def test_the_two_directions_of_one_pair_are_two_questions(rel: _RelFixture) -> None:
    forward = rel.edges[0]
    backward = Edge(source_id=forward.target_id, target_id=forward.source_id)
    rel.edges[:] = [forward, backward]
    rel.seed(forward)

    quote = rel.quote()

    assert (quote.served, quote.to_judge) == (1, 1)
    assert [item.work_key for item in quote.token.work] == [_edge_key(backward)]


def test_an_edited_endpoint_returns_a_relation_to_judge(rel: _RelFixture) -> None:
    for edge in rel.edges:
        rel.seed(edge)
    assert rel.quote().to_judge == 0

    rel.edit(rel.edges[3].target_id)
    quote = rel.quote()

    assert (quote.served, quote.to_judge) == (4, 1)
    assert quote.token.work[0].work_key == _edge_key(rel.edges[3])


def test_a_suggestion_stored_under_another_confidential_bit_is_not_served(
    rel: _RelFixture,
) -> None:
    for edge in rel.edges:
        rel.seed(edge, include_confidential=False)

    exempt = rel.quote(include_confidential=True)

    assert (exempt.served, exempt.to_judge) == (0, 5)
    assert exempt.binding.effective_confidential is True
    assert exempt.token.include_confidential is True


def test_a_local_backend_makes_the_effective_confidential_bit_true(
    rel: _RelFixture,
) -> None:
    rel.client = _edge_judge(is_local=True)

    quote = rel.quote()

    assert quote.binding.is_local is True
    assert quote.binding.effective_confidential is True
    assert quote.token.include_confidential is False


def test_a_local_backend_serves_a_suggestion_stored_under_the_effective_bit(
    rel: _RelFixture,
) -> None:
    """A local backend turns the exemption on, so the effective bit is true and
    a suggestion stored under it is served even though the caller asked for
    nothing; one stored under the other bit is not."""
    rel.client = _edge_judge(is_local=True)
    rel.seed(rel.edges[0], include_confidential=True)
    rel.seed(rel.edges[1], include_confidential=False)

    quote = rel.quote()

    assert quote.binding.effective_confidential is True
    assert (quote.served, quote.to_judge) == (1, 4)
    assert _edge_key(rel.edges[0]) not in [i.work_key for i in quote.token.work]


def test_a_relation_quote_ignores_the_uncertain_opt_in(rel: _RelFixture) -> None:
    """Nothing here is judged-uncertain, so the flag prices nothing and is not
    bound into the token as though it did."""
    plain = rel.quote()
    opted = judging.quote_judging(
        rel.root, "relation_type", include_uncertain=True, ports=rel.ports()
    )

    assert (opted.calls, opted.to_judge, opted.uncertain) == (5, 5, 0)
    assert opted.token.include_uncertain is False
    assert opted.token.digest() == plain.token.digest()


def test_a_relation_quote_over_no_candidates_quotes_zero(rel: _RelFixture) -> None:
    rel.edges.clear()

    quote = rel.quote()

    assert (quote.total, quote.served, quote.to_judge, quote.calls) == (0, 0, 0, 0)
    assert quote.token.work == ()


def test_a_relation_quote_carries_the_models_notice_once_and_no_serve_notice(
    rel: _RelFixture,
) -> None:
    quote = rel.quote()

    assert [n.code for n in quote.notices] == ["model"]
    assert quote.notices[0].backend == "ollama"


def _report_graph(
    report: sqlite_graph.CandidateReport,
) -> Callable[..., sqlite_graph.SqliteGraphStore]:
    def build(*args: object, **kwargs: object) -> sqlite_graph.SqliteGraphStore:
        graph = sqlite_graph.build_graph(*args, **kwargs)  # type: ignore[arg-type]
        graph.candidate_report = report
        return graph

    return build


def test_a_capped_relation_walk_carries_the_truncation_notice_as_data(
    rel: _RelFixture,
) -> None:
    pairs = tuple(
        tuple(sorted((edge.source_id, edge.target_id))) for edge in rel.edges[:3]
    )
    report = sqlite_graph.CandidateReport(
        produced=3,
        retained=1,
        pairs=pairs,  # type: ignore[arg-type]
    )

    quote = rel.quote(build_graph=_report_graph(report))

    [notice] = [n for n in quote.notices if n.code == "truncated"]
    assert notice.text == "1 of 3 candidate edge(s) shown (cap reached)"
    assert not rel.layout.findings_db_path.exists()


def test_a_quarantined_relation_walk_carries_the_quarantine_notice_as_data(
    rel: _RelFixture,
) -> None:
    withheld = sqlite_graph.WithheldCandidate(
        pair=(rel.edges[0].source_id, rel.edges[0].target_id),
        source_ids=("sources/s1",),
    )
    report = sqlite_graph.CandidateReport(quarantine_withheld=(withheld,))

    quote = rel.quote(build_graph=_report_graph(report))

    [notice] = [n for n in quote.notices if n.code == "quarantined"]
    assert notice.text is not None
    assert "1 candidate edge(s) withheld" in notice.text
    assert "sources/s1" in notice.text


def test_a_relation_notice_never_changes_price_or_token(rel: _RelFixture) -> None:
    plain = rel.quote()
    report = sqlite_graph.CandidateReport(
        quarantine_withheld=(
            sqlite_graph.WithheldCandidate(
                pair=(rel.edges[0].source_id, rel.edges[0].target_id),
                source_ids=("sources/s1",),
            ),
        )
    )

    noisy = rel.quote(build_graph=_report_graph(report))

    assert (noisy.calls, noisy.to_judge) == (plain.calls, plain.to_judge)
    assert noisy.token.digest() == plain.token.digest()


# -- relation_type: the run: support ------------------------------------------- #


def _rel_run(
    rel: _RelFixture,
    token: judging.QuoteToken | None,
    *,
    judge: _Judge | None = None,
    stop: StopToken | None = None,
    observer: judging.JudgingObserver | None = None,
    chunk_size: int | None = None,
    sleeps: list[float] | None = None,
    **ports: object,
) -> judging.JudgingOutcome | judging.QuoteStale | judging.RunRefused:
    if judge is not None:
        rel.client = judge
    sleep = sleeps.append if sleeps is not None else (lambda seconds: None)
    return judging.run_judging(
        rel.root,
        token,
        stop=stop or StopToken(),
        ports=rel.ports(**{"sleep": sleep, **ports}),
        observer=observer or judging.JudgingObserver(),
        chunk_size=chunk_size,
    )


def _bundle_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(path): path.read_bytes()
        for path in sorted((root / "bundle").rglob("*"))
        if path.is_file()
    }


def _untyped(rel: _RelFixture, *indexes: int) -> list[str]:
    return [_edge_key(rel.edges[i]) for i in indexes]


# -- relation_type: the run: the binding ceiling ------------------------------- #


def test_a_relation_run_types_exactly_the_quoted_edges_and_persists_them(
    rel: _RelFixture,
) -> None:
    rel.seed(rel.edges[0])
    token = rel.quote().token
    bundle_before = _bundle_bytes(rel.root)
    judge = _edge_judge(default_reply=_edge_reply("supports"))

    outcome = _outcome(_rel_run(rel, token, judge=judge))

    assert (outcome.kind, outcome.status) == ("relation_type", "completed")
    assert (outcome.judged, outcome.calls, outcome.persisted) == (4, 4, 4)
    assert (outcome.unpersisted, outcome.dropped, outcome.deferred) == (0, 0, 0)
    assert outcome.not_in_quote == ()
    assert outcome.settled_since_quote == 0
    stored = rel.stored()
    assert stored[_edge_key(rel.edges[0])] == "references", "the served row is kept"
    assert {stored[key] for key in _untyped(rel, 1, 2, 3, 4)} == {"supports"}
    assert _bundle_bytes(rel.root) == bundle_before, "a run never writes the bundle"


def test_a_relation_run_prices_the_re_quote_after_it_as_all_served(
    rel: _RelFixture,
) -> None:
    _outcome(_rel_run(rel, rel.quote().token, judge=_edge_judge()))

    requote = rel.quote()

    assert (requote.served, requote.to_judge, requote.calls) == (5, 0, 0)


def test_a_relation_edge_added_since_the_quote_is_named_and_never_typed(
    rel: _RelFixture,
) -> None:
    token = rel.quote().token
    extra = Edge(source_id="concepts/x-a", target_id="concepts/x-b")
    _write(rel.root, extra.source_id)
    _write(rel.root, extra.target_id)
    rel.edges.append(extra)
    judge = _edge_judge()

    outcome = _outcome(_rel_run(rel, token, judge=judge))

    assert outcome.not_in_quote == (_edge_key(extra),)
    assert (outcome.judged, judge.calls) == (5, 5)
    assert _edge_key(extra) not in rel.stored()


def test_a_relation_edge_answered_elsewhere_is_settled_and_not_paid_for_again(
    rel: _RelFixture,
) -> None:
    token = rel.quote().token
    rel.seed(rel.edges[2])
    judge = _edge_judge()

    outcome = _outcome(_rel_run(rel, token, judge=judge))

    assert (outcome.settled_since_quote, outcome.judged, judge.calls) == (1, 4, 4)
    assert rel.stored()[_edge_key(rel.edges[2])] == "references"


def test_a_relation_edge_that_vanished_is_settled_not_typed(rel: _RelFixture) -> None:
    token = rel.quote().token
    del rel.edges[1]
    judge = _edge_judge()

    outcome = _outcome(_rel_run(rel, token, judge=judge))

    assert (outcome.settled_since_quote, outcome.judged, judge.calls) == (1, 4, 4)
    assert outcome.not_in_quote == ()


def test_a_relation_endpoint_edited_since_the_quote_is_no_longer_the_quoted_item(
    rel: _RelFixture,
) -> None:
    token = rel.quote().token
    rel.edit(rel.edges[1].target_id)
    judge = _edge_judge()

    outcome = _outcome(_rel_run(rel, token, judge=judge))

    assert outcome.not_in_quote == (_edge_key(rel.edges[1]),)
    assert (outcome.settled_since_quote, outcome.judged, judge.calls) == (1, 4, 4)


def test_a_relation_call_past_the_ceiling_is_refused_before_it_is_made(
    rel: _RelFixture,
) -> None:
    token = dataclasses.replace(rel.quote().token, calls=2)
    judge = _edge_judge()

    with pytest.raises(AssertionError, match="ceiling of 2"):
        _rel_run(rel, token, judge=judge, chunk_size=5)

    assert judge.calls == 2


def test_a_relation_run_without_a_token_is_refused_before_anything_is_built(
    rel: _RelFixture,
) -> None:
    outcome = _rel_run(rel, None)

    assert outcome == judging.RunRefused("no_quote_token")
    assert rel.tasks == []
    assert rel.graphs == 0


@pytest.mark.parametrize(
    ("yaml", "field"),
    [
        ("models:\n  edge_typing: another-model\n", "model"),
        ("backend: openai-compatible\nbase_url: http://x\n", "backend"),
    ],
)
def test_a_moved_relation_binding_is_stale_and_spends_and_writes_nothing(
    rel: _RelFixture, yaml: str, field: str
) -> None:
    token = rel.quote().token
    path = rel.root / "openkos.yaml"
    path.write_text(path.read_text(encoding="utf-8") + yaml, encoding="utf-8")
    judge = _edge_judge()

    result = _rel_run(rel, token, judge=judge)

    assert isinstance(result, judging.QuoteStale)
    assert result.kind == "relation_type"
    assert field in result.changed
    assert judge.calls == 0
    assert not rel.layout.findings_db_path.exists()


def test_a_relation_backend_that_moved_onto_this_machine_is_stale(
    rel: _RelFixture,
) -> None:
    token = rel.quote().token

    result = _rel_run(rel, token, judge=_edge_judge(is_local=True))

    assert isinstance(result, judging.QuoteStale)
    assert result.changed == ("locality", "confidential")


# -- relation_type: the run: chunks, persistence and cancellation -------------- #


def test_each_relation_chunk_persists_before_the_next_chunk_is_typed(
    rel: _RelFixture,
) -> None:
    log: list[str] = []

    outcome = _outcome(
        _rel_run(
            rel,
            rel.quote().token,
            judge=_edge_judge(log=log),
            commit_section=_Sections(log),
            chunk_size=2,
        )
    )

    assert log == [
        "chat", "chat", "enter", "exit",
        "chat", "chat", "enter", "exit",
        "chat", "enter", "exit",
    ]  # fmt: skip
    assert outcome.persisted == 5


def test_a_relation_run_is_chunked_by_the_measured_size_by_default(
    rel: _RelFixture,
) -> None:
    log: list[str] = []
    assert judging.RELATION_CHUNK_SIZE == 4

    _outcome(
        _rel_run(
            rel,
            rel.quote().token,
            judge=_edge_judge(log=log),
            commit_section=_Sections(log),
        )
    )

    assert log == ["chat"] * 4 + ["enter", "exit", "chat", "enter", "exit"]


def test_a_relation_chunk_size_below_one_is_refused(rel: _RelFixture) -> None:
    with pytest.raises(ValueError, match="chunk_size"):
        _rel_run(rel, rel.quote().token, chunk_size=0)


def test_a_stop_from_inside_a_relation_call_lets_that_chunk_persist_and_defers_the_rest(
    rel: _RelFixture,
) -> None:
    stop = StopToken()
    token = rel.quote().token

    outcome = _outcome(
        _rel_run(
            rel,
            token,
            judge=_edge_judge(on_chat=lambda n: stop.set() if n == 1 else None),
            stop=stop,
            chunk_size=2,
        )
    )

    assert outcome.status == "stopped"
    assert (outcome.judged, outcome.persisted, outcome.deferred) == (2, 2, 3)
    assert set(rel.stored()) == set(_untyped(rel, 0, 1))
    requote = rel.quote()
    assert (requote.served, requote.to_judge) == (2, 3)


def test_a_stop_before_the_first_relation_chunk_makes_no_call(
    rel: _RelFixture,
) -> None:
    stop = StopToken()
    stop.set()
    token = rel.quote().token
    builds_before = rel.graphs
    judge = _edge_judge()

    outcome = _outcome(_rel_run(rel, token, judge=judge, stop=stop))

    assert (outcome.status, outcome.judged, outcome.deferred) == ("stopped", 0, 5)
    assert judge.calls == 0
    assert not rel.layout.findings_db_path.exists()
    assert rel.graphs == builds_before + 1, "only the walk that partitions the work"


def test_a_stop_during_the_last_relation_chunk_still_completes(
    rel: _RelFixture,
) -> None:
    stop = StopToken()

    outcome = _outcome(
        _rel_run(
            rel,
            rel.quote().token,
            judge=_edge_judge(on_chat=lambda n: stop.set() if n == 5 else None),
            stop=stop,
        )
    )

    assert (outcome.status, outcome.deferred, outcome.persisted) == ("completed", 0, 5)


def test_a_relation_chunk_is_bounded_and_filtered_to_the_remaining_quoted_edges(
    rel: _RelFixture,
) -> None:
    """Each invocation of the service sees only the edges still to do and is
    bound to the chunk: a run over a bundle the quote did not cover never
    reaches the extra edge, and no invocation types more than its chunk."""
    token = rel.quote().token
    extra = Edge(source_id="concepts/x-a", target_id="concepts/x-b")
    _write(rel.root, extra.source_id)
    _write(rel.root, extra.target_id)
    rel.edges.insert(0, extra)
    seen: list[list[str]] = []

    def spy(to_type: list[Edge], **kwargs: object) -> edge_typing.EdgeSuggestionBatch:
        seen.append([_edge_key(edge) for edge in to_type])
        return edge_typing.suggest_edge_types(to_type, **kwargs)  # type: ignore[arg-type]

    _outcome(_rel_run(rel, token, chunk_size=2, suggest_edge_types=spy))

    assert seen == [_untyped(rel, 1, 2), _untyped(rel, 3, 4), _untyped(rel, 5)]
    assert _edge_key(extra) not in [key for chunk in seen for key in chunk]


def test_a_relation_stop_that_leaves_every_remaining_edge_settled_ends_the_run(
    rel: _RelFixture,
) -> None:
    """When the edges still to do stop being candidates mid-run, the walk finds
    nothing to type: they are settled, not deferred, and the run completes."""
    token = rel.quote().token

    def vanish(n: int) -> None:
        if n == 1:
            rel.edges[:] = rel.edges[:1]

    judge = _edge_judge(on_chat=vanish)

    outcome = _outcome(_rel_run(rel, token, judge=judge, chunk_size=1))

    assert outcome.status == "completed"
    assert (outcome.judged, outcome.persisted, outcome.deferred) == (1, 1, 0)
    assert outcome.settled_since_quote == 4


# -- relation_type: the run: what is stored ------------------------------------ #


def test_a_relation_reply_that_degrades_is_judged_but_never_persisted(
    rel: _RelFixture,
) -> None:
    judge = _edge_judge(["not json at all"], default_reply=_edge_reply())

    outcome = _outcome(_rel_run(rel, rel.quote().token, judge=judge))

    assert (outcome.judged, outcome.persisted, outcome.dropped) == (5, 4, 1)
    assert _edge_key(rel.edges[0]) not in rel.stored()
    assert outcome.judged == outcome.persisted + outcome.dropped + outcome.unpersisted


def test_a_relation_verdict_about_content_edited_during_the_call_is_dropped(
    rel: _RelFixture,
) -> None:
    token = rel.quote().token

    outcome = _outcome(
        _rel_run(
            rel,
            token,
            judge=_edge_judge(
                on_chat=lambda n: rel.edit(rel.edges[1].source_id) if n == 2 else None
            ),
        )
    )

    assert (outcome.judged, outcome.persisted, outcome.dropped) == (5, 4, 1)
    assert _edge_key(rel.edges[1]) not in rel.stored()


# -- relation_type: the run: progress and the observer -------------------------- #


def test_relation_progress_events_carry_the_run_index_total_edge_and_type(
    rel: _RelFixture,
) -> None:
    observer = _Recorder()
    rel.seed(rel.edges[0])

    _outcome(
        _rel_run(
            rel,
            rel.quote().token,
            judge=_edge_judge(default_reply=_edge_reply("supports")),
            observer=observer,
            chunk_size=3,
        )
    )

    assert [(e.index, e.total) for e in observer.progress_events] == [
        (1, 4), (2, 4), (3, 4), (4, 4),
    ]  # fmt: skip
    assert [e.work_key for e in observer.progress_events] == _untyped(rel, 1, 2, 3, 4)
    assert {e.verdict for e in observer.progress_events} == {"supports"}
    assert observer.persisted == [3, 4]


def test_a_relation_progress_event_never_carries_the_rationale(
    rel: _RelFixture,
) -> None:
    observer = _Recorder()

    _outcome(_rel_run(rel, rel.quote().token, judge=_edge_judge(), observer=observer))

    assert len(observer.progress_events) == 5
    for event in observer.progress_events:
        assert "Typed." not in repr(event)


@pytest.mark.parametrize("fail_on", [1, 3])
def test_a_relation_observer_that_raises_loses_no_paid_work(
    rel: _RelFixture, fail_on: int
) -> None:
    observer = _Recorder(fail_on_progress=fail_on)

    outcome = _outcome(
        _rel_run(rel, rel.quote().token, judge=_edge_judge(), observer=observer)
    )

    assert outcome.observer_failed is True
    assert (outcome.status, outcome.persisted) == ("completed", 5)
    assert len(rel.stored()) == 5
    assert len(observer.progress_events) == fail_on


# -- relation_type: the run: a busy workspace ----------------------------------- #


def test_a_transient_busy_relation_workspace_is_absorbed(rel: _RelFixture) -> None:
    log: list[str] = []
    sleeps: list[float] = []

    outcome = _outcome(
        _rel_run(
            rel,
            rel.quote().token,
            judge=_edge_judge(log=log),
            commit_section=_Sections(log, busy=1),
            sleeps=sleeps,
            chunk_size=5,
        )
    )

    assert (outcome.status, outcome.persisted, outcome.unpersisted) == (
        "completed",
        5,
        0,
    )
    assert sleeps == [1.0]


def test_a_persistently_busy_relation_workspace_stops_the_spend_and_says_so(
    rel: _RelFixture,
) -> None:
    log: list[str] = []
    sections = _Sections(log, always_busy=True)
    sleeps: list[float] = []
    judge = _edge_judge(log=log)
    observer = _Recorder()

    outcome = _outcome(
        _rel_run(
            rel,
            rel.quote().token,
            judge=judge,
            commit_section=sections,
            sleeps=sleeps,
            observer=observer,
            chunk_size=2,
        )
    )

    assert outcome.status == "unpersisted"
    assert outcome.unpersisted_cause == "workspace_busy"
    assert (outcome.judged, outcome.unpersisted, outcome.persisted) == (2, 2, 0)
    assert outcome.deferred == 3
    assert judge.calls == 2, "nothing further is spent once persistence fails"
    assert sleeps == [1.0, 2.0]
    assert sections.attempts == judging.PERSIST_ATTEMPTS
    assert observer.persisted == []
    assert "enter" not in log, "no lock is held between attempts"


def test_a_relation_store_error_is_reported_as_unpersisted_with_its_cause(
    rel: _RelFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(edge_store, "record_edge_suggestions", _boom)
    judge = _edge_judge()

    outcome = _outcome(_rel_run(rel, rel.quote().token, judge=judge, chunk_size=1))

    assert outcome.status == "unpersisted"
    assert outcome.unpersisted_cause == "store_error"
    assert (outcome.unpersisted, outcome.persisted, outcome.deferred) == (1, 0, 4)
    assert judge.calls == 1


# -- relation_type: the run: backend errors ------------------------------------- #


def test_a_failed_relation_ends_the_run_with_its_prefix_kept(rel: _RelFixture) -> None:
    judge = _edge_judge([_edge_reply(), BackendGenerationCapped("capped")])

    outcome = _outcome(_rel_run(rel, rel.quote().token, judge=judge, chunk_size=5))

    assert outcome.status == "failed"
    assert isinstance(outcome.failure, BackendGenerationCapped)
    assert (outcome.judged, outcome.persisted, outcome.failed_at) == (1, 1, 2)
    assert outcome.deferred == 3, "the failed edge was reached; three were not"
    assert set(rel.stored()) == set(_untyped(rel, 0))


def test_a_relation_failed_at_is_the_run_position_not_the_chunk_position(
    rel: _RelFixture,
) -> None:
    judge = _edge_judge(
        [_edge_reply(), _edge_reply(), BackendGenerationCapped("capped")]
    )

    outcome = _outcome(_rel_run(rel, rel.quote().token, judge=judge, chunk_size=2))

    assert (outcome.status, outcome.failed_at) == ("failed", 3)
    assert (outcome.judged, outcome.persisted, outcome.deferred) == (2, 2, 2)


@pytest.mark.parametrize(
    "error",
    [BackendUnavailable("down"), BackendModelNotFound("missing")],
)
def test_a_relation_availability_error_propagates_after_the_prefix_is_persisted(
    rel: _RelFixture, error: Exception
) -> None:
    observer = _Recorder()
    judge = _edge_judge([_edge_reply(), error])

    with pytest.raises(type(error)):
        _rel_run(rel, rel.quote().token, judge=judge, observer=observer, chunk_size=5)

    assert set(rel.stored()) == set(_untyped(rel, 0))
    assert observer.persisted == [1]


def test_a_relation_availability_error_on_the_first_call_writes_nothing(
    rel: _RelFixture,
) -> None:
    observer = _Recorder()

    with pytest.raises(BackendUnavailable):
        _rel_run(
            rel,
            rel.quote().token,
            judge=_edge_judge([BackendUnavailable("down")]),
            observer=observer,
        )

    assert not rel.layout.findings_db_path.exists()
    assert observer.persisted == []


def test_a_busy_relation_workspace_wins_over_a_backend_failure_in_one_chunk(
    rel: _RelFixture,
) -> None:
    log: list[str] = []

    outcome = _outcome(
        _rel_run(
            rel,
            rel.quote().token,
            judge=_edge_judge([_edge_reply(), BackendUnavailable("down")], log=log),
            commit_section=_Sections(log, always_busy=True),
            chunk_size=5,
        )
    )

    assert outcome.status == "unpersisted"
    assert outcome.unpersisted_cause == "workspace_busy"
    assert isinstance(outcome.failure, BackendUnavailable)
    assert (outcome.judged, outcome.unpersisted, outcome.failed_at) == (1, 1, 2)


# -- relation_type: the run: locks, the daemon and silence ---------------------- #


def test_the_workspace_lock_is_free_during_every_relation_call(
    rel: _RelFixture,
) -> None:
    from openkos.application import lock_wait

    free_during_call: list[bool] = []

    def _probe(n: int) -> None:
        try:
            with lock.workspace_lock(rel.root):
                free_during_call.append(True)
        except lock.WorkspaceBusyError:
            free_during_call.append(False)

    outcome = _outcome(
        _rel_run(
            rel,
            rel.quote().token,
            judge=_edge_judge(on_chat=_probe),
            commit_section=lock_wait.locked_commit_section(rel.root, wait_seconds=0),
            chunk_size=2,
        )
    )

    assert free_during_call == [True] * 5
    assert outcome.persisted == 5


def test_an_active_engine_does_not_block_a_relation_run(rel: _RelFixture) -> None:
    _seed_job(rel, started=_NOW - timedelta(days=30))
    before = rel.layout.jobs_db_path.read_bytes()

    outcome = _outcome(_rel_run(rel, rel.quote().token, judge=_edge_judge()))

    assert (outcome.status, outcome.persisted) == ("completed", 5)
    assert rel.layout.jobs_db_path.read_bytes() == before


def test_a_relation_run_writes_nothing_to_a_stream(
    rel: _RelFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    _outcome(_rel_run(rel, rel.quote().token, judge=_edge_judge()))

    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")
