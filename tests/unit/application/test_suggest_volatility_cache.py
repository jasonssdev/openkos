"""`suggest_volatility_tiers` with the answered-question cache (#1332).

An unchanged bundle must cost nothing: the second pass over the same bundle
serves every type from `findings.db` and asks the model nothing, and what it
serves is what it computed -- no-change answers included."""

import sqlite3
from collections.abc import Sequence
from contextlib import nullcontext
from pathlib import Path

import pytest

from openkos import config
from openkos.application import suggest_volatility_service as svc
from openkos.llm.base import Message
from openkos.state import derived
from openkos.state import volatility_suggestions as store
from tests.unit.application.curation_support import make_workspace
from tests.unit.cli.commit_phase_support import write_doc


class _Model:
    def __init__(self, replies: Sequence[str]) -> None:
        self.replies = list(replies)
        self.calls = 0

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls += 1
        return self.replies.pop(0)


class _Observer:
    def walk_incomplete(self, *a: object, **k: object) -> None:
        return None

    def progress_callback(self) -> None:
        return None


def _reply(tier: str) -> str:
    return f'{{"tier": "{tier}", "rationale": "because {tier}"}}'


def _ports(model: _Model, **kw: object) -> svc.VolatilityPorts:
    return svc.VolatilityPorts(
        chat_client=lambda cfg, task: model,
        resolve_local_exemption=lambda client, cfg: True,
        commit_section=lambda: nullcontext(),
        **kw,  # type: ignore[arg-type]
    )


def _bundle(root: Path) -> None:
    for cid, kind in (("concepts/a", "Concept"), ("events/b", "Event")):
        write_doc(
            root,
            cid,
            {"type": kind, "title": cid, "sensitivity": "private"},
            f"Body of {cid}.\n",
        )


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    workspace = make_workspace(tmp_path, monkeypatch)
    _bundle(workspace)
    return workspace


def _run(root: Path, model: _Model, *, use_cache: bool = True) -> svc.VolatilityOutcome:
    return svc.suggest_volatility_tiers(
        root,
        svc.VolatilityRequest(use_cache=use_cache),
        _ports(model),
        _Observer(),
    )


def _stored(root: Path) -> tuple[store.PersistedVolatilitySuggestion, ...]:
    conn = derived.open_derived_connection(
        config.WorkspaceLayout(root).findings_db_path
    )
    try:
        return store.open_volatility_suggestions(conn)
    finally:
        conn.close()


def test_an_unchanged_bundle_costs_nothing_the_second_time(root: Path) -> None:
    first_model = _Model([_reply("slow"), _reply("static")])
    first = _run(root, first_model)
    second_model = _Model([])

    second = _run(root, second_model)

    assert first_model.calls == 2
    assert second_model.calls == 0
    assert [(r.type_name, r.suggested_tier, r.rationale) for r in second.results] == [
        (r.type_name, r.suggested_tier, r.rationale) for r in first.results
    ]


def test_every_computed_answer_is_kept_not_only_the_ones_that_change_a_tier(
    root: Path,
) -> None:
    _run(root, _Model([_reply("slow"), _reply("static")]))

    assert {s.type_name for s in _stored(root)} == {"Concept", "Event"}


def test_an_edited_sampled_body_asks_only_that_type_again(root: Path) -> None:
    _run(root, _Model([_reply("slow"), _reply("static")]))
    write_doc(
        root,
        "events/b",
        {"type": "Event", "title": "events/b", "sensitivity": "private"},
        "A different body.\n",
    )
    model = _Model([_reply("volatile")])

    outcome = _run(root, model)

    assert model.calls == 1
    assert {r.type_name: r.suggested_tier for r in outcome.results} == {
        "Concept": "slow",
        "Event": "volatile",
    }


def test_a_degraded_answer_is_never_cached(root: Path) -> None:
    _run(root, _Model(["not json", _reply("static")]))

    assert {s.type_name for s in _stored(root)} == {"Event"}


def test_a_different_model_does_not_serve_the_old_answer(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(root, _Model([_reply("slow"), _reply("static")]))
    monkeypatch.setattr(config, "resolve_task_model", lambda cfg, task: "other-model")
    model = _Model([_reply("slow"), _reply("static")])

    _run(root, model)

    assert model.calls == 2


def test_without_the_cache_nothing_is_read_or_written(root: Path) -> None:
    first = _Model([_reply("slow"), _reply("static")])
    _run(root, first, use_cache=False)
    assert not config.WorkspaceLayout(root).findings_db_path.exists()
    second = _Model([_reply("slow"), _reply("static")])

    _run(root, second, use_cache=False)

    assert second.calls == 2


def test_an_unreadable_store_degrades_to_asking(root: Path) -> None:
    path = config.WorkspaceLayout(root).findings_db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"this is not a database")
    model = _Model([_reply("slow"), _reply("static")])

    outcome = _run(root, model)

    assert model.calls == 2
    assert len(outcome.results) == 2


def test_a_failed_persist_costs_a_warning_never_the_run(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*a: object, **k: object) -> None:
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(store, "record_volatility_suggestions", boom)
    model = _Model([_reply("slow"), _reply("static")])

    outcome = _run(root, model)

    assert len(outcome.results) == 2
