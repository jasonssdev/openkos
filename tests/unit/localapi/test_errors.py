"""The framework rows of the error table; later slices add the API rows."""

import logging
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from openkos.localapi import _errors
from openkos.localapi._app import ServerSettings, create_app
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
