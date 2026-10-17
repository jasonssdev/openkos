"""The framework rows of the error table; later slices add the API rows."""

import logging
from collections.abc import Iterator
from typing import get_args, get_type_hints

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from openkos import api
from openkos.localapi import _errors
from openkos.localapi._app import ServerSettings, create_app
from openkos.localapi._guard import MAX_REQUEST_BODY_BYTES
from tests.unit.localapi.conftest import AUTH, PORT, TOKEN

SENTINEL = "sentinel-value-9f3a"


class _Body(BaseModel):
    count: int


@pytest.fixture
def app(settings: ServerSettings) -> FastAPI:
    app = create_app(settings)

    @app.post("/v1/_probe_body")
    def probe_body(body: _Body) -> dict[str, int]:
        return {"count": body.count}

    @app.get("/v1/_probe_boom")
    def probe_boom() -> None:
        def boom() -> None:
            raise RuntimeError(f"leaks {SENTINEL}")

        _errors.invoke(boom)

    @app.get("/v1/_probe_problem")
    def probe_problem() -> None:
        raise _errors.ApiProblem(
            409,
            _errors.ErrorBody(code="demo", retryable=True, message="demo message"),
            {"Retry-After": "2"},
        )

    @app.get("/v1/_probe_ok")
    def probe_ok() -> dict[str, str]:
        return {"ok": _errors.invoke(lambda: "yes")}

    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(
        app, base_url=f"http://127.0.0.1:{PORT}", raise_server_exceptions=False
    ) as c:
        yield c


def test_a_validation_error_is_422_and_never_echoes_the_input(
    client: TestClient,
) -> None:
    response = client.post("/v1/_probe_body", json={"count": SENTINEL}, headers=AUTH)

    assert response.status_code == 422
    assert response.json() == {
        "code": "invalid_request",
        "retryable": False,
        "message": "the request is not valid",
    }
    assert SENTINEL not in response.text


def test_an_unknown_exception_is_a_fixed_500_and_the_server_keeps_serving(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger="openkos.localapi"):
        response = client.get("/v1/_probe_boom", headers=AUTH)

    assert response.status_code == 500
    assert response.json() == {
        "code": "internal_error",
        "retryable": False,
        "message": "internal error",
    }
    assert SENTINEL not in response.text
    assert TOKEN not in response.text
    assert any(r.exc_info for r in caplog.records)  # the defect is logged
    assert TOKEN not in caplog.text  # and the log never carries the request
    assert client.get("/v1/_probe_ok", headers=AUTH).json() == {"ok": "yes"}


def test_an_api_problem_renders_its_status_body_and_headers(
    client: TestClient,
) -> None:
    response = client.get("/v1/_probe_problem", headers=AUTH)

    assert response.status_code == 409
    assert response.json() == {
        "code": "demo",
        "retryable": True,
        "message": "demo message",
    }
    assert response.headers["retry-after"] == "2"


def test_error_body_maps_an_unknown_exception_to_the_generic_row() -> None:
    status, body, headers = _errors.error_body(ValueError("x"))

    assert (status, body.code, body.retryable, headers) == (
        500,
        "internal_error",
        False,
        {},
    )


# -- the write rows: returned outcomes and raised exceptions ---------------------- #

_KEY = "identity:abc123"
_VERSION = api.DecisionVersion(_KEY, "p", "c")
_APPLY_BODY = {
    "action": "merge",
    "version": {"decision_key": _KEY, "payload_digest": "p", "content_digest": "c"},
}
_APPLY_PATH = f"/v1/decisions/{_KEY}/apply"
_STALE_REASONS = get_args(get_type_hints(api.Stale)["reason"])
_NOT_APPLIED_REASONS = get_args(get_type_hints(api.NotApplied)["reason"])


def _returning(monkeypatch: pytest.MonkeyPatch, name: str, value: object) -> None:
    def stub(*args: object, **kwargs: object) -> object:
        return value

    monkeypatch.setattr(api, name, stub)


def _raising(monkeypatch: pytest.MonkeyPatch, name: str, exc: Exception) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise exc

    monkeypatch.setattr(api, name, boom)


def test_the_parametrized_reasons_are_the_ones_the_api_declares() -> None:
    assert set(_STALE_REASONS) == {
        "content_changed",
        "row_resolved",
        "row_superseded",
        "row_inputs_changed",
        "unknown_key",
    }
    assert set(_NOT_APPLIED_REASONS) == {
        "skipped",
        "member_missing",
        "record_failed",
        "not_mergeable",
        "not_offered",
        "invalid_selection",
        "already_reconciled",
        "wrong_preview",
    }


@pytest.mark.parametrize("reason", _STALE_REASONS)
def test_every_stale_reason_is_409_stale_with_a_fixed_message(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    _returning(monkeypatch, "apply_decision", api.Stale(_KEY, reason))  # type: ignore[arg-type]

    response = client.post(_APPLY_PATH, json=_APPLY_BODY, headers=AUTH)

    body = response.json()
    assert response.status_code == 409
    assert (body["code"], body["retryable"]) == ("stale", False)
    assert body["message"].endswith("preview it again")
    assert _KEY not in response.text


def test_each_stale_reason_has_its_own_message(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    messages = set()
    for reason in _STALE_REASONS:
        _returning(monkeypatch, "apply_decision", api.Stale(_KEY, reason))
        messages.add(
            client.post(_APPLY_PATH, json=_APPLY_BODY, headers=AUTH).json()["message"]
        )

    assert len(messages) == len(_STALE_REASONS)


@pytest.mark.parametrize(
    ("reason", "status", "code", "retryable"),
    [
        ("not_offered", 422, "action_not_offered", False),
        ("skipped", 422, "skip_not_applicable", False),
        ("not_mergeable", 409, "not_mergeable", False),
        ("member_missing", 409, "member_missing", False),
        ("record_failed", 409, "record_failed", True),
        ("invalid_selection", 422, "invalid_selection", False),
        ("wrong_preview", 422, "wrong_preview", False),
        ("already_reconciled", 409, "already_reconciled", False),
    ],
)
def test_not_applied_reasons_map_to_their_rows_and_never_echo_the_detail(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    reason: str,
    status: int,
    code: str,
    retryable: bool,
) -> None:
    assert reason in _NOT_APPLIED_REASONS
    outcome = api.NotApplied(api.Action.MERGE, reason, detail="SENTINEL")  # type: ignore[arg-type]
    _returning(monkeypatch, "apply_decision", outcome)

    response = client.post(_APPLY_PATH, json=_APPLY_BODY, headers=AUTH)

    body = response.json()
    assert response.status_code == status
    assert (body["code"], body["retryable"]) == (code, retryable)
    assert body["message"]
    assert "SENTINEL" not in response.text


def test_a_preview_outcome_that_is_not_a_preview_is_an_error_row(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _returning(monkeypatch, "preview_decision", api.Stale(_KEY, "unknown_key"))

    response = client.post(
        f"/v1/decisions/{_KEY}/preview", json={"action": "merge"}, headers=AUTH
    )

    assert (response.status_code, response.json()["code"]) == (409, "stale")


class _Unreachable(api.WriteRefused):
    """Stands for the confirmation refusals this API never raises."""


@pytest.mark.parametrize(
    ("exc", "status", "code", "retryable"),
    [
        (api.WorkspaceBusyError("SENTINEL"), 503, "workspace_busy", True),
        (
            api.WorkspaceLockUnavailableError("SENTINEL"),
            500,
            "workspace_lock_unavailable",
            False,
        ),
        (api.DriftDetected("SENTINEL"), 409, "write_drift", True),
        (api.WriteFailed("merge", ("a",), "SENTINEL"), 500, "write_failed", False),
        (
            api.MergeGroupInterrupted((("SENTINEL", "b"),), "abc1234", "SENTINEL", "x"),
            500,
            "merge_group_interrupted",
            False,
        ),
        (api.QueueRefreshRefused("SENTINEL"), 503, "queue_refresh_refused", False),
        (_Unreachable("SENTINEL"), 500, "internal_error", False),
        (OSError("SENTINEL"), 500, "internal_error", False),
        (ValueError("SENTINEL"), 500, "internal_error", False),
    ],
)
def test_raised_write_failures_map_to_fixed_rows_that_never_echo(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
    status: int,
    code: str,
    retryable: bool,
) -> None:
    _raising(monkeypatch, "apply_decision", exc)
    _raising(monkeypatch, "refresh_decision_queue", exc)

    for response in (
        client.post(_APPLY_PATH, json=_APPLY_BODY, headers=AUTH),
        client.post("/v1/queue/refresh", headers=AUTH),
    ):
        assert response.status_code == status
        assert response.json()["code"] == code
        assert response.json()["retryable"] is retryable
        assert "SENTINEL" not in response.text


def test_only_a_busy_workspace_sends_retry_after(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _raising(monkeypatch, "apply_decision", api.WorkspaceBusyError("x"))
    busy = client.post(_APPLY_PATH, json=_APPLY_BODY, headers=AUTH)
    _raising(monkeypatch, "apply_decision", api.DriftDetected("x"))
    drift = client.post(_APPLY_PATH, json=_APPLY_BODY, headers=AUTH)

    assert busy.headers["retry-after"] == str(_errors.RETRY_AFTER_SECONDS)
    assert "retry-after" not in drift.headers


def test_a_write_refusal_passes_its_own_user_facing_message(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    for exc in (
        api.WriteRefused("Cannot merge: b is open."),
        api.Refused("Cannot merge: b is open."),
    ):
        _raising(monkeypatch, "apply_decision", exc)

        response = client.post(_APPLY_PATH, json=_APPLY_BODY, headers=AUTH)

        assert response.status_code == 422
        assert response.json() == {
            "code": "write_refused",
            "retryable": False,
            "message": "Cannot merge: b is open.",
        }


def test_an_empty_write_refusal_message_gets_a_fixed_one(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _raising(monkeypatch, "apply_decision", api.WriteRefused(""))

    response = client.post(_APPLY_PATH, json=_APPLY_BODY, headers=AUTH)

    assert response.json()["code"] == "write_refused"
    assert response.json()["message"] == "the write was refused"


def test_an_interrupted_group_is_its_own_row_not_a_plain_write_failure() -> None:
    interrupted = _errors.error_body(
        api.MergeGroupInterrupted((("a", "b"),), None, "c", "boom")
    )
    plain = _errors.error_body(api.WriteFailed("merge", ("a",), "boom"))

    assert (interrupted[0], interrupted[1].code) == (500, "merge_group_interrupted")
    assert interrupted[1].retryable is False
    assert interrupted[1].message != plain[1].message
    assert plain[1].code == "write_failed"


_UNDO_ROWS = {
    "not_kept_distinct": (409, "not_kept_distinct"),
    "not_related": (409, "not_related"),
    "tier_unchanged": (409, "tier_unchanged"),
    "unknown_type": (422, "unknown_type"),
}


def test_every_undo_reason_has_its_own_row_and_none_is_retryable() -> None:
    reasons = get_args(get_type_hints(api.NotUndoable)["reason"])
    assert set(reasons) == set(_UNDO_ROWS)
    for reason in reasons:
        problem = _errors.outcome_problem(api.NotUndoable(reason))
        assert (problem.status, problem.body.code) == _UNDO_ROWS[reason]
        assert problem.body.retryable is False


def test_a_stale_undo_is_409_stale() -> None:
    problem = _errors.outcome_problem(api.UndoStale())

    assert (problem.status, problem.body.code, problem.body.retryable) == (
        409,
        "stale",
        False,
    )


def test_drift_is_not_reported_as_a_plain_refusal() -> None:
    drift = _errors.error_body(api.DriftDetected("m"))
    refused = _errors.error_body(api.WriteRefused("m"))

    assert drift[1].code == "write_drift"
    assert refused[1].code == "write_refused"


# -- the table is complete ---------------------------------------------------------- #

# The `code` column of the spec's error table, as literals so a row cannot be
# dropped or invented without this test noticing.
SPEC_CODES = {
    "unauthorized",
    "forbidden",
    "host_not_allowed",
    "origin_not_allowed",
    "preflight_rejected",
    "request_too_large",
    "not_found",
    "method_not_allowed",
    "invalid_request",
    "key_mismatch",
    "stale",
    "quote_stale",
    "action_not_offered",
    "skip_not_applicable",
    "not_mergeable",
    "member_missing",
    "record_failed",
    "invalid_selection",
    "wrong_preview",
    "already_reconciled",
    "run_refused",
    "run_active",
    "ingest_active",
    "write_drift",
    "write_refused",
    "write_failed",
    "merge_group_interrupted",
    "unmerge_interrupted",
    "survivor_missing",
    "not_merged",
    "survivor_edited",
    "not_kept_distinct",
    "not_related",
    "tier_unchanged",
    "unknown_type",
    "workspace_busy",
    "workspace_lock_unavailable",
    "queue_unavailable",
    "queue_refresh_refused",
    "judging_refused",
    "ingest_refused",
    "workspace_not_found",
    "workspace_unreadable",
    "backend_unavailable",
    "backend_model_not_found",
    "generation_capped",
    "backend_error",
    "internal_error",
}


def _produced_by_the_guard_and_the_framework(client: TestClient) -> list[str]:
    foreign = {"Origin": "https://evil.example"}
    responses = [
        client.get("/v1/health"),
        client.get("/v1/health", headers={"Authorization": "Bearer wrong"}),
        client.get("/v1/health", headers={**AUTH, "Host": "evil.com"}),
        client.get("/v1/health", headers={**AUTH, **foreign}),
        client.options(
            "/v1/health", headers={**foreign, "Access-Control-Request-Method": "GET"}
        ),
        client.get("/v1/nothing", headers=AUTH),
        client.delete("/v1/health", headers=AUTH),
        client.post("/v1/_probe_body", json={"count": "x"}, headers=AUTH),
        client.post(
            "/v1/_probe_body",
            content=b"x" * (MAX_REQUEST_BODY_BYTES + 1),
            headers=AUTH,
        ),
    ]
    return [r.json()["code"] for r in responses]


def test_every_code_in_the_spec_table_is_produced_exactly_once_and_no_other(
    client: TestClient,
) -> None:
    rows = [row.code for row in _errors._ROWS]
    not_applied = [row[1] for row in _errors._NOT_APPLIED.values()]
    not_applied += [row[1] for row in _errors._NOT_UNMERGEABLE.values()]
    not_applied += [row[1] for row in _errors._NOT_UNDOABLE.values()]
    helpers = [
        _errors.key_mismatch().body.code,
        _errors.outcome_problem(api.Stale(_KEY, "content_changed")).body.code,
        _errors.outcome_problem(api.QuoteStale("identity", ("model",))).body.code,
        _errors.outcome_problem(api.RunRefused("no_quote_token")).body.code,
        _errors.run_active().body.code,
        _errors.ingest_active().body.code,
        _errors.error_body(RuntimeError("x"))[1].code,
    ]
    produced = [
        *rows,
        *not_applied,
        *helpers,
        *_produced_by_the_guard_and_the_framework(client),
    ]

    assert len(produced) == len(set(produced))  # exactly one producer per code
    assert set(produced) == SPEC_CODES
