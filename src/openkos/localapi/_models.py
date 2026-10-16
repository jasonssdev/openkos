"""HTTP models the adapter owns: only what `openkos.api` has no type for."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openkos import api
from openkos.localapi._errors import WRITE_WAIT_SECONDS, ErrorBody


class HealthBody(BaseModel):
    """The authenticated liveness answer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["ok"]
    api_version: int
    engine_version: str


class DecisionList(BaseModel):
    """Every open decision, in an envelope that leaves room for additive fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decisions: list[api.Decision]


class JudgingOutcomeBody(BaseModel):
    """What a judging run returned, field for field, with a failure as a body."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: api.JudgingKind
    status: Literal["completed", "stopped", "failed", "unpersisted"]
    judged: int
    calls: int
    persisted: int
    unpersisted: int
    unpersisted_cause: Literal["workspace_busy", "store_error"] | None
    dropped: int
    deferred: int
    not_in_quote: list[str]
    settled_since_quote: int
    failure: ErrorBody | None
    failed_at: int | None
    observer_failed: bool


class RunStatus(BaseModel):
    """A judging run as a poll sees it. The run's own result is a field here,
    never the poll's HTTP status."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    kind: api.JudgingKind
    state: Literal["running", "stopping", "finished", "failed"]
    index: int | None
    total: int | None
    persisted: int
    outcome: JudgingOutcomeBody | None
    error: ErrorBody | None


class PreviewRequest(BaseModel):
    """Which action to preview for the decision named in the path."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: api.Action


class _EchoRequest(BaseModel):
    """A request that carries an echoed API dataclass.

    `extra="forbid"` would also reach the nested dataclass and give its request
    schema `additionalProperties: false`, splitting the component into `-Input`
    and `-Output` variants. Unknown keys are therefore rejected here, at the top
    level only, and a nested unknown key is ignored: the service re-derives and
    compares every digest, so it is never read."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _reject_unknown_fields(cls, data: Any) -> Any:
        if isinstance(data, dict) and not data.keys() <= cls.model_fields.keys():
            raise ValueError("unknown field")
        return data


class QuoteRequest(BaseModel):
    """Which judging to price, and whether the quote may include what the run
    needs consent for."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: api.JudgingKind
    include_confidential: bool = False
    include_uncertain: bool = False


class RunRequest(_EchoRequest):
    """The quote's token, echoed unchanged. A missing or null token is a
    refusal the route answers (422 `run_refused`), not a validation error."""

    token: api.QuoteToken | None = None


class ApplyRequest(_EchoRequest):
    """The action, the version the preview issued (echoed unchanged) and how
    long to wait for the workspace lock; a client can shorten the wait only."""

    action: api.Action
    version: api.DecisionVersion
    wait_seconds: float = Field(default=WRITE_WAIT_SECONDS, ge=0, le=WRITE_WAIT_SECONDS)


class RefreshRequest(BaseModel):
    """How long a queue refresh waits for the workspace lock."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    wait_seconds: float = Field(default=WRITE_WAIT_SECONDS, ge=0, le=WRITE_WAIT_SECONDS)


class ApplyResponse(BaseModel):
    """What was applied, and the warnings the apply raised along the way (for
    example a commit that could not be made)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    applied: api.Applied
    warnings: list[str]
