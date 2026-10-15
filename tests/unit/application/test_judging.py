"""The judging surface's quote half (the `judging-application-service` change,
S2b; ADR-0059): the binding, the token, the identity quote and its notices.

Every quote here runs against a client whose `chat` raises: a quote makes no
model call, so an unreachable backend must not matter.
"""

import dataclasses
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from openkos import config
from openkos.application import judging
from openkos.application import pending as application_pending
from openkos.llm.base import BackendHostLocality, BackendUnavailable, Message
from openkos.model import okf
from openkos.resolution.adjudication import rubric_digest
from openkos.resolution.candidates import CandidateGroup, CandidateGroupReport, Tier
from openkos.state import adjudications as store
from openkos.state import derived, jobs
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
    with pytest.raises(ValueError, match="relation_type"):
        judging.quote_judging(Path(), "relation_type", ports=ports)


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
    fx: _Fixture, *, started: datetime, ended: datetime | None = None
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
