"""The route table is the security boundary: every registered route is run
through the same adversarial matrix, and the route set is asserted exactly so
the matrix cannot silently go vacuous or miss a new route."""

import re
from collections.abc import Iterable, Iterator

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.routing import BaseRoute

from openkos import api
from openkos.localapi._app import ServerSettings, _build, create_app, openapi_document
from tests.unit.localapi.conftest import AUTH, PORT, TAURI, TOKEN

EXPECTED_ROUTES = {("GET", "/v1/health")}


@pytest.fixture
def app(settings: ServerSettings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as c:
        yield c


def _api_routes(routes: Iterable[BaseRoute]) -> Iterator[APIRoute]:
    """Every `APIRoute`, looking inside the routers FastAPI includes lazily."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        else:
            yield from _api_routes(route.original_router.routes)  # type: ignore[attr-defined]


def _route_table(app: FastAPI) -> set[tuple[str, str]]:
    return {
        (method, route.path)
        for route in _api_routes(app.routes)
        for method in route.methods or ()
    }


def test_the_route_set_is_exactly_the_expected_one(app: FastAPI) -> None:
    assert _route_table(app) == EXPECTED_ROUTES


def test_every_route_is_in_the_published_schema(app: FastAPI) -> None:
    assert all(r.include_in_schema for r in _api_routes(app.routes))
    published = {
        (method.upper(), path)
        for path, item in openapi_document()["paths"].items()  # type: ignore[attr-defined]
        for method in item
    }
    assert published == EXPECTED_ROUTES


def _each_route() -> list[tuple[str, str]]:
    """The matrix runs over the real table, not over the expected set."""
    return sorted((m, re.sub(r"\{[^}]+\}", "x", p)) for m, p in _route_table(_build()))


@pytest.mark.parametrize(("method", "path"), _each_route())
class TestEveryRouteIsGuarded:
    def test_no_token_is_401(self, client: TestClient, method: str, path: str) -> None:
        response = client.request(method, path)

        assert response.status_code == 401
        assert response.json()["code"] == "unauthorized"

    def test_a_wrong_token_is_403(
        self, client: TestClient, method: str, path: str
    ) -> None:
        response = client.request(
            method, path, headers={"Authorization": "Bearer wrong"}
        )

        assert response.status_code == 403
        assert response.json()["code"] == "forbidden"

    def test_a_foreign_host_is_403(
        self, client: TestClient, method: str, path: str
    ) -> None:
        response = client.request(method, path, headers={**AUTH, "Host": "evil.com"})

        assert response.status_code == 403
        assert response.json()["code"] == "host_not_allowed"

    def test_the_right_host_on_the_wrong_port_is_403(
        self, client: TestClient, method: str, path: str
    ) -> None:
        response = client.request(
            method, path, headers={**AUTH, "Host": f"127.0.0.1:{PORT + 1}"}
        )

        assert response.status_code == 403
        assert response.json()["code"] == "host_not_allowed"

    def test_a_foreign_origin_with_a_valid_token_is_403(
        self, client: TestClient, method: str, path: str
    ) -> None:
        response = client.request(
            method, path, headers={**AUTH, "Origin": "https://evil.example"}
        )

        assert response.status_code == 403
        assert response.json()["code"] == "origin_not_allowed"

    def test_a_token_only_in_the_query_is_401(
        self, client: TestClient, method: str, path: str
    ) -> None:
        response = client.request(method, path, params={"token": TOKEN})

        assert response.status_code == 401


def test_a_preflight_from_the_shell_needs_no_token(client: TestClient) -> None:
    response = client.options(
        "/v1/health",
        headers={"Origin": TAURI, "Access-Control-Request-Method": "GET"},
    )

    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == TAURI


def test_a_preflight_with_a_foreign_host_is_403(client: TestClient) -> None:
    response = client.options(
        "/v1/health",
        headers={
            "Host": "evil.com",
            "Origin": TAURI,
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "host_not_allowed"


def test_an_unknown_path_is_401_without_a_token_and_404_with_one(
    client: TestClient,
) -> None:
    assert client.get("/v1/nothing").status_code == 401

    found = client.get("/v1/nothing", headers=AUTH)

    assert found.status_code == 404
    assert found.json() == {
        "code": "not_found",
        "retryable": False,
        "message": "no such route",
    }


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_no_documentation_page_is_served(client: TestClient, path: str) -> None:
    assert client.get(path, headers=AUTH).status_code == 404


def test_a_wrong_method_is_405_in_the_uniform_body(client: TestClient) -> None:
    response = client.delete("/v1/health", headers=AUTH)

    assert response.status_code == 405
    assert response.json()["code"] == "method_not_allowed"
    assert "GET" in response.headers["allow"]


def test_health_is_authenticated_and_reports_the_versions(
    client: TestClient,
) -> None:
    response = client.get("/v1/health", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"status", "api_version", "engine_version"}
    assert body["status"] == "ok"
    assert body["api_version"] == api.API_VERSION
    assert isinstance(body["engine_version"], str)
    assert body["engine_version"]
