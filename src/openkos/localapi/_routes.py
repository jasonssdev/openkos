"""The versioned router. Every operation calls `openkos.api` through the module
attribute, so a test that patches `openkos.api.<fn>` intercepts the call."""

import dataclasses
from importlib import metadata
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.security import HTTPBearer

from openkos import api
from openkos.localapi import _errors
from openkos.localapi._errors import ApiProblem, ErrorBody, invoke
from openkos.localapi._models import (
    ApplyRequest,
    ApplyResponse,
    DecisionList,
    DecisionPreview,
    HealthBody,
    KeptDistinctList,
    MergedConceptList,
    MergeHistory,
    PreviewPlan,
    PreviewRequest,
    QuoteRequest,
    RefreshRequest,
    ReopenApplyRequest,
    ReopenPreviewRequest,
    ReopenResponse,
    RunRequest,
    RunStatus,
    TierChangeApplyRequest,
    TierChangePreviewRequest,
    TierChangeResponse,
    UnmergeApplyRequest,
    UnmergePreviewRequest,
    UnmergeResponse,
    UnrelateApplyRequest,
    UnrelatePreviewRequest,
    UnrelateResponse,
)
from openkos.localapi._runs import RunRegistry

# Documents the scheme in the OpenAPI schema only: the guard enforces the token
# before any route runs, so this never rejects (`auto_error=False`).
_BEARER = HTTPBearer(scheme_name="BearerToken", auto_error=False)


def prefix() -> str:
    """`/v1` today; read at call time so it always follows `API_VERSION`."""
    return f"/v{api.API_VERSION}"


def _engine_version() -> str:
    try:
        return metadata.version("openkos")
    except metadata.PackageNotFoundError:
        return "unknown"


def _workspace(request: Request) -> api.Workspace:
    """The launch's workspace, kept on the app by `create_app`."""
    workspace: api.Workspace = request.app.state.workspace
    return workspace


def _registry(request: Request) -> RunRegistry:
    """The run registry `_build` keeps on the app."""
    registry: RunRegistry = request.app.state.registry
    return registry


def _plain_mappings(s: api.WorkspaceStatus) -> api.WorkspaceStatus:
    return dataclasses.replace(
        s,
        concepts_by_type=dict(s.concepts_by_type),
        open_decisions_by_kind=dict(s.open_decisions_by_kind),
    )


def _wait(requested: float) -> float:
    """The lock wait: what the client asked for, never above the ceiling."""
    return min(requested, _errors.WRITE_WAIT_SECONDS)


def _errors_of(
    *statuses: int, retry_after: bool = False
) -> dict[int | str, dict[str, Any]]:
    """`responses=` rows for the statuses a route answers with an `ErrorBody`.

    `retry_after` documents the header a busy 503 carries."""
    rows: dict[int | str, dict[str, Any]] = {s: {"model": ErrorBody} for s in statuses}
    if retry_after:
        rows[503]["headers"] = {
            "Retry-After": {
                "description": "Seconds to wait before retrying a busy workspace.",
                "schema": {"type": "integer"},
            }
        }
    return rows


def make_router() -> APIRouter:
    router = APIRouter(
        prefix=prefix(),
        dependencies=[Depends(_BEARER)],
        responses={
            401: {"model": ErrorBody},
            403: {"model": ErrorBody},
            422: {"model": ErrorBody},
            500: {"model": ErrorBody},
        },
    )

    @router.get("/health")
    def health() -> HealthBody:
        return HealthBody(
            status="ok",
            api_version=api.API_VERSION,
            engine_version=_engine_version(),
        )

    @router.get("/status")
    def status(workspace: api.Workspace = Depends(_workspace)) -> api.WorkspaceStatus:  # noqa: B008
        # FastAPI cannot serialise the read-only mappings the API returns.
        return invoke(lambda: _plain_mappings(api.status(workspace)))

    @router.get("/pending", responses=_errors_of(503))
    def pending(workspace: api.Workspace = Depends(_workspace)) -> api.PendingReport:  # noqa: B008
        return invoke(lambda: api.pending_report(workspace))

    @router.get("/decisions")
    def decisions(workspace: api.Workspace = Depends(_workspace)) -> DecisionList:  # noqa: B008
        return DecisionList(
            decisions=list(invoke(lambda: api.list_decisions(workspace)))
        )

    @router.post("/decisions/{decision_key}/preview", responses=_errors_of(409, 413))
    def preview(
        decision_key: str,
        body: PreviewRequest,
        workspace: api.Workspace = Depends(_workspace),  # noqa: B008
    ) -> DecisionPreview:
        result = invoke(
            lambda: api.preview_decision(
                workspace, decision_key, body.action, selection=body.selection
            )
        )
        if isinstance(result, api.DecisionPreview):
            return DecisionPreview(
                decision_key=result.decision_key,
                action=result.action,
                plan=PreviewPlan(result.plan),
                version=result.version,
            )
        raise _errors.outcome_problem(result)

    @router.post(
        "/decisions/{decision_key}/apply",
        responses=_errors_of(409, 413, 503, retry_after=True),
    )
    def apply(
        decision_key: str,
        body: ApplyRequest,
        workspace: api.Workspace = Depends(_workspace),  # noqa: B008
    ) -> ApplyResponse:
        if body.version.decision_key != decision_key:
            raise _errors.key_mismatch()
        warnings: list[str] = []
        result = invoke(
            lambda: api.apply_decision(
                workspace,
                body.version,
                body.action,
                wait_seconds=_wait(body.wait_seconds),
                on_warning=warnings.append,
            )
        )
        if isinstance(result, api.Applied):
            return ApplyResponse(applied=result, warnings=warnings)
        raise _errors.outcome_problem(result)

    @router.post("/queue/refresh", responses=_errors_of(413, 503, retry_after=True))
    def refresh(
        body: RefreshRequest | None = None,
        workspace: api.Workspace = Depends(_workspace),  # noqa: B008
    ) -> api.QueueRefresh:
        wait = _wait(body.wait_seconds if body else _errors.WRITE_WAIT_SECONDS)
        return invoke(lambda: api.refresh_decision_queue(workspace, wait_seconds=wait))

    @router.get("/merges")
    def merges(
        concept_id: str,
        workspace: api.Workspace = Depends(_workspace),  # noqa: B008
    ) -> MergeHistory:
        # A Concept ID contains `/`, so it travels as a query parameter.
        return MergeHistory(
            merges=list(invoke(lambda: api.merge_history(workspace, concept_id)))
        )

    @router.get("/merged")
    def merged(
        workspace: api.Workspace = Depends(_workspace),  # noqa: B008
    ) -> MergedConceptList:
        return MergedConceptList(
            concepts=list(invoke(lambda: api.merged_concepts(workspace)))
        )

    @router.get("/pages", responses=_errors_of(404))
    def page(
        concept_id: str,
        workspace: api.Workspace = Depends(_workspace),  # noqa: B008
    ) -> api.Page:
        # A Concept ID contains `/`, so it travels as a query parameter. An
        # unknown page is `not_found`, like an unknown run id, and the fixed
        # message never echoes the id.
        found = invoke(lambda: api.read_page(workspace, concept_id))
        if found is None:
            raise ApiProblem(
                404,
                ErrorBody(
                    code=_errors.NOT_FOUND, retryable=False, message="no such page"
                ),
            )
        return found

    @router.post("/unmerge/preview", responses=_errors_of(409, 413))
    def unmerge_preview(
        body: UnmergePreviewRequest,
        workspace: api.Workspace = Depends(_workspace),  # noqa: B008
    ) -> api.UnmergePreview:
        result = invoke(
            lambda: api.preview_unmerge(
                workspace,
                body.survivor_id,
                body.absorbed_id,
                discard_survivor_edits=body.discard_survivor_edits,
            )
        )
        if isinstance(result, api.UnmergePreview):
            return result
        raise _errors.outcome_problem(result)

    @router.post(
        "/unmerge/apply", responses=_errors_of(409, 413, 503, retry_after=True)
    )
    def unmerge_apply(
        body: UnmergeApplyRequest,
        workspace: api.Workspace = Depends(_workspace),  # noqa: B008
    ) -> UnmergeResponse:
        warnings: list[str] = []
        result = invoke(
            lambda: api.apply_unmerge(
                workspace,
                body.version,
                wait_seconds=_wait(body.wait_seconds),
                on_warning=warnings.append,
            )
        )
        if isinstance(result, api.Unmerged):
            return UnmergeResponse(unmerged=result, warnings=warnings)
        raise _errors.outcome_problem(result)

    @router.get("/kept-distinct")
    def kept_distinct(
        workspace: api.Workspace = Depends(_workspace),  # noqa: B008
    ) -> KeptDistinctList:
        return KeptDistinctList(
            groups=list(invoke(lambda: api.kept_distinct(workspace)))
        )

    @router.post("/reopen/preview", responses=_errors_of(409, 413))
    def reopen_preview(
        body: ReopenPreviewRequest,
        workspace: api.Workspace = Depends(_workspace),  # noqa: B008
    ) -> api.ReopenPreview:
        result = invoke(lambda: api.preview_reopen(workspace, body.member_ids))
        if isinstance(result, api.ReopenPreview):
            return result
        raise _errors.outcome_problem(result)

    @router.post("/reopen/apply", responses=_errors_of(409, 413, 503, retry_after=True))
    def reopen_apply(
        body: ReopenApplyRequest,
        workspace: api.Workspace = Depends(_workspace),  # noqa: B008
    ) -> ReopenResponse:
        warnings: list[str] = []
        result = invoke(
            lambda: api.apply_reopen(
                workspace,
                body.version,
                wait_seconds=_wait(body.wait_seconds),
                on_warning=warnings.append,
            )
        )
        if isinstance(result, api.Reopened):
            return ReopenResponse(reopened=result, warnings=warnings)
        raise _errors.outcome_problem(result)

    @router.post("/unrelate/preview", responses=_errors_of(409, 413))
    def unrelate_preview(
        body: UnrelatePreviewRequest,
        workspace: api.Workspace = Depends(_workspace),  # noqa: B008
    ) -> api.UnrelatePreview:
        result = invoke(
            lambda: api.preview_unrelate(
                workspace, body.source_id, body.relation_type, body.target_id
            )
        )
        if isinstance(result, api.UnrelatePreview):
            return result
        raise _errors.outcome_problem(result)

    @router.post(
        "/unrelate/apply", responses=_errors_of(409, 413, 503, retry_after=True)
    )
    def unrelate_apply(
        body: UnrelateApplyRequest,
        workspace: api.Workspace = Depends(_workspace),  # noqa: B008
    ) -> UnrelateResponse:
        warnings: list[str] = []
        result = invoke(
            lambda: api.apply_unrelate(
                workspace,
                body.version,
                wait_seconds=_wait(body.wait_seconds),
                on_warning=warnings.append,
            )
        )
        if isinstance(result, api.Unrelated):
            return UnrelateResponse(unrelated=result, warnings=warnings)
        raise _errors.outcome_problem(result)

    @router.post("/tiers/preview", responses=_errors_of(409, 413))
    def tier_preview(
        body: TierChangePreviewRequest,
        workspace: api.Workspace = Depends(_workspace),  # noqa: B008
    ) -> api.TierChangePreview:
        result = invoke(
            lambda: api.preview_tier_change(workspace, body.type_name, body.tier)
        )
        if isinstance(result, api.TierChangePreview):
            return result
        raise _errors.outcome_problem(result)

    @router.post("/tiers/apply", responses=_errors_of(409, 413, 503, retry_after=True))
    def tier_apply(
        body: TierChangeApplyRequest,
        workspace: api.Workspace = Depends(_workspace),  # noqa: B008
    ) -> TierChangeResponse:
        warnings: list[str] = []
        result = invoke(
            lambda: api.apply_tier_change(
                workspace,
                body.version,
                wait_seconds=_wait(body.wait_seconds),
                on_warning=warnings.append,
            )
        )
        if isinstance(result, api.TierChanged):
            return TierChangeResponse(changed=result, warnings=warnings)
        raise _errors.outcome_problem(result)

    @router.post("/judging/quote", responses=_errors_of(413, 424, 502, 503))
    def quote(
        body: QuoteRequest,
        workspace: api.Workspace = Depends(_workspace),  # noqa: B008
    ) -> api.JudgingQuote:
        return invoke(
            lambda: api.quote_judging(
                workspace,
                body.kind,
                include_confidential=body.include_confidential,
                include_uncertain=body.include_uncertain,
            )
        )

    @router.post("/judging/runs", status_code=202, responses=_errors_of(409, 413))
    def start_run(
        body: RunRequest,
        response: Response,
        workspace: api.Workspace = Depends(_workspace),  # noqa: B008
        registry: RunRegistry = Depends(_registry),  # noqa: B008
    ) -> RunStatus:
        if body.token is None:
            raise _errors.outcome_problem(api.RunRefused("no_quote_token"))
        status = registry.start(workspace, body.token)
        response.headers["Location"] = f"{prefix()}/judging/runs/{status.run_id}"
        return status

    @router.get("/judging/runs/{run_id}", responses=_errors_of(404))
    def run_status(
        run_id: str,
        registry: RunRegistry = Depends(_registry),  # noqa: B008
    ) -> RunStatus:
        return registry.status(run_id)

    @router.post("/judging/runs/{run_id}/stop", responses=_errors_of(404))
    def stop_run(
        run_id: str,
        registry: RunRegistry = Depends(_registry),  # noqa: B008
    ) -> RunStatus:
        return registry.stop(run_id)

    return router
