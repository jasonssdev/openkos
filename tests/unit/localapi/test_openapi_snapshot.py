"""The HTTP contract is pinned by a committed OpenAPI document."""

import json
import re
import socket
from pathlib import Path

import pytest

from openkos import api
from openkos.localapi._app import ServerSettings, create_app, openapi_document
from tests.unit.localapi import openapi_snapshot

_REGENERATE = "run: uv run python -m tests.unit.localapi.openapi_snapshot --write"


def _committed() -> str:
    assert openapi_snapshot.SNAPSHOT_PATH.exists(), _REGENERATE
    return openapi_snapshot.SNAPSHOT_PATH.read_text(encoding="utf-8")


def test_the_live_document_matches_the_committed_snapshot() -> None:
    assert _committed() == openapi_snapshot.dumps(openapi_document()), _REGENERATE


def test_the_running_app_serves_the_same_document(settings: ServerSettings) -> None:
    assert json.loads(_committed()) == create_app(settings).openapi()


def test_the_document_needs_no_workspace_and_opens_no_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        pytest.fail("generating the contract opened a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.chdir(tmp_path)  # an empty directory: no workspace anywhere

    assert openapi_document()["openapi"]


def test_every_path_is_versioned() -> None:
    paths = openapi_document()["paths"]
    assert isinstance(paths, dict)

    assert paths  # not vacuous
    assert all(path.startswith("/v1/") for path in paths)


def test_no_component_is_split_or_module_qualified() -> None:
    components = openapi_document()["components"]
    assert isinstance(components, dict)
    names = list(components["schemas"])

    assert names  # not vacuous
    assert not [n for n in names if n.endswith(("-Input", "-Output"))]
    assert not [n for n in names if "." in n or re.search(r"[a-z]__[A-Za-z]", n)]


def test_the_version_is_the_api_version_not_the_package_version() -> None:
    info = openapi_document()["info"]
    assert isinstance(info, dict)

    assert info["version"] == str(api.API_VERSION)


def test_a_bearer_security_scheme_is_published() -> None:
    components = openapi_document()["components"]
    assert isinstance(components, dict)

    assert components["securitySchemes"] == {
        "BearerToken": {"type": "http", "scheme": "bearer"}
    }


def test_the_uniform_error_body_is_described() -> None:
    components = openapi_document()["components"]
    assert isinstance(components, dict)

    assert set(components["schemas"]["ErrorBody"]["properties"]) == {
        "code",
        "retryable",
        "message",
    }


def _schemas() -> dict[str, dict[str, object]]:
    components = openapi_document()["components"]
    assert isinstance(components, dict)
    schemas: dict[str, dict[str, object]] = components["schemas"]
    return schemas


def test_the_judging_contract_names_its_components() -> None:
    schemas = _schemas()

    for name in ("RunStatus", "JudgingOutcomeBody", "QuoteToken", "JudgingQuote"):
        assert name in schemas


def test_the_quote_token_describes_member_digests_as_two_string_arrays() -> None:
    digests = _schemas()["WorkItem"]["properties"]["member_digests"]  # type: ignore[index]

    item = digests["items"]
    assert item["type"] == "array"
    assert item["prefixItems"] == [{"type": "string"}, {"type": "string"}]
    assert (item["minItems"], item["maxItems"]) == (2, 2)


def test_the_run_start_route_is_202_with_a_job_and_a_run_refusal_row() -> None:
    paths = openapi_document()["paths"]
    assert isinstance(paths, dict)

    start = paths["/v1/judging/runs"]["post"]

    assert "202" in start["responses"]
    assert start["responses"]["202"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/RunStatus"
    }


def test_the_ask_start_route_is_202_with_a_job() -> None:
    paths = openapi_document()["paths"]
    assert isinstance(paths, dict)

    start = paths["/v1/ask"]["post"]

    assert "202" in start["responses"]
    assert start["responses"]["202"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/AskJob"
    }


_ERROR_REF = {"$ref": "#/components/schemas/ErrorBody"}
_EVERY_ROUTE = {"401", "403", "422", "500"}
# The statuses a route answers beyond the ones every route can.
_ROUTE_ERRORS: dict[tuple[str, str], set[str]] = {
    ("get", "/v1/health"): set(),
    ("get", "/v1/status"): set(),
    ("get", "/v1/pending"): {"503"},
    ("get", "/v1/decisions"): set(),
    ("post", "/v1/decisions/{decision_key}/preview"): {"409", "413"},
    ("post", "/v1/decisions/{decision_key}/preview-finding"): {"409", "413"},
    ("post", "/v1/decisions/{decision_key}/apply"): {"409", "413", "503"},
    ("post", "/v1/queue/refresh"): {"413", "503"},
    ("get", "/v1/merges"): set(),
    ("get", "/v1/merged"): set(),
    ("get", "/v1/pages"): {"404"},
    ("post", "/v1/unmerge/preview"): {"409", "413"},
    ("post", "/v1/unmerge/apply"): {"409", "413", "503"},
    ("get", "/v1/kept-distinct"): set(),
    ("post", "/v1/reopen/preview"): {"409", "413"},
    ("post", "/v1/reopen/apply"): {"409", "413", "503"},
    ("post", "/v1/unrelate/preview"): {"409", "413"},
    ("post", "/v1/unrelate/apply"): {"409", "413", "503"},
    ("post", "/v1/tiers/preview"): {"409", "413"},
    ("post", "/v1/tiers/apply"): {"409", "413", "503"},
    ("post", "/v1/pages/sensitivity/preview"): {"404", "409", "413"},
    ("post", "/v1/pages/sensitivity/apply"): {"404", "409", "413", "503"},
    ("get", "/v1/search"): set(),
    ("post", "/v1/search/rebuilds"): {"409", "413"},
    ("get", "/v1/search/rebuilds/{rebuild_id}"): {"404"},
    ("post", "/v1/search/rebuilds/{rebuild_id}/stop"): {"404"},
    ("get", "/v1/settings/model"): set(),
    ("post", "/v1/settings/model/preview"): {"409", "413"},
    ("post", "/v1/settings/model/apply"): {"409", "413", "503"},
    ("get", "/v1/declined-contradictions"): set(),
    ("post", "/v1/contradictions/reopen/preview"): {"409", "413"},
    ("post", "/v1/contradictions/reopen/apply"): {"409", "413", "503"},
    ("post", "/v1/judging/quote"): {"413", "424", "502", "503"},
    ("post", "/v1/judging/runs"): {"409", "413"},
    ("get", "/v1/judging/runs/{run_id}"): {"404"},
    ("post", "/v1/judging/runs/{run_id}/stop"): {"404"},
    ("post", "/v1/ask"): {"409", "413"},
    ("get", "/v1/ask/{ask_id}"): {"404"},
    ("post", "/v1/ingest/quote"): {"413", "503"},
    ("post", "/v1/decisions/{decision_key}/ingest-quote"): {"409", "413", "503"},
    ("post", "/v1/ingest/runs"): {"409", "413", "503"},
    ("get", "/v1/ingest/runs/{run_id}"): {"404"},
    ("post", "/v1/ingest/runs/{run_id}/stop"): {"404"},
    ("get", "/v1/setup/readiness"): set(),
    ("post", "/v1/setup/pulls"): {"409", "413", "424", "502", "503"},
    ("get", "/v1/setup/pulls/{pull_id}"): {"404"},
    ("post", "/v1/setup/pulls/{pull_id}/stop"): {"404"},
}
_RETRY_AFTER = {
    ("post", "/v1/decisions/{decision_key}/apply"),
    ("post", "/v1/queue/refresh"),
    ("post", "/v1/unmerge/apply"),
    ("post", "/v1/reopen/apply"),
    ("post", "/v1/unrelate/apply"),
    ("post", "/v1/tiers/apply"),
    ("post", "/v1/settings/model/apply"),
    ("post", "/v1/pages/sensitivity/apply"),
    ("post", "/v1/contradictions/reopen/apply"),
}


def _operations() -> dict[tuple[str, str], dict[str, dict[str, object]]]:
    paths = openapi_document()["paths"]
    assert isinstance(paths, dict)
    return {
        (m, p): op["responses"] for p, ops in paths.items() for m, op in ops.items()
    }


def test_every_route_is_listed_here() -> None:
    assert set(_operations()) == set(_ROUTE_ERRORS)


def test_every_route_declares_its_error_statuses_with_the_error_body() -> None:
    for key, responses in _operations().items():
        errors = {s for s in responses if not s.startswith("2")}

        assert errors == _EVERY_ROUTE | _ROUTE_ERRORS[key], key
        for status in errors:
            body = responses[status]["content"]["application/json"]["schema"]  # type: ignore[index]
            assert body == _ERROR_REF, (key, status)


def test_a_busy_503_declares_its_retry_after_header() -> None:
    for key in _RETRY_AFTER:
        busy = _operations()[key]["503"]
        headers = busy["headers"]
        assert isinstance(headers, dict)

        assert "Retry-After" in headers, key


def test_the_preview_plan_is_a_discriminated_union_on_kind() -> None:
    schemas = openapi_document()["components"]["schemas"]  # type: ignore[index]
    assert schemas["DecisionPreview"]["properties"]["plan"] == {
        "$ref": "#/components/schemas/PreviewPlan"
    }
    plan = schemas["PreviewPlan"]

    assert plan["discriminator"]["propertyName"] == "kind"
    assert plan["discriminator"]["mapping"] == {
        "merge": "#/components/schemas/MergePlan",
        "relate": "#/components/schemas/RelatePlan",
        "set_tier": "#/components/schemas/TierPlan",
        "keep_distinct": "#/components/schemas/KeepDistinctPlan",
    }
    assert len(plan["oneOf"]) == 4
    for name, kind in {
        "MergePlan": "merge",
        "RelatePlan": "relate",
        "TierPlan": "set_tier",
        "KeepDistinctPlan": "keep_distinct",
    }.items():
        assert schemas[name]["properties"]["kind"]["const"] == kind
        assert "kind" in schemas[name]["required"]


def test_the_group_selection_is_one_component_the_preview_request_names() -> None:
    schemas = openapi_document()["components"]["schemas"]  # type: ignore[index]

    assert [n for n in schemas if "GroupSelection" in n] == ["GroupSelection"]
    request = schemas["PreviewRequest"]
    assert "selection" in request["properties"]
    assert "selection" not in request.get("required", [])
    assert (
        request.get("additionalProperties") is None
    )  # unknown keys are rejected above the schema
    assert schemas["GroupSelection"]["required"] == ["survivor_id", "member_ids"]
    version = schemas["DecisionVersion"]
    assert "selection" in version["properties"]
    assert "selection" not in version["required"]


def test_the_finding_plan_is_its_own_union_and_the_decision_plan_is_unchanged() -> None:
    """ADR-0072: the finding preview's plan is a second discriminated union, so
    the decision preview's union, which a client switches over exhaustively,
    keeps exactly its four members."""
    schemas = _schemas()
    finding = schemas["FindingPlan"]
    decision = schemas["PreviewPlan"]

    assert finding["discriminator"] == {
        "propertyName": "kind",
        "mapping": {
            "decline": "#/components/schemas/DeclinePlan",
            "reconcile": "#/components/schemas/ReconcilePlan",
        },
    }
    assert sorted(decision["discriminator"]["mapping"]) == [  # type: ignore[index]
        "keep_distinct",
        "merge",
        "relate",
        "set_tier",
    ]
