"""HTTP models the adapter owns: only what `openkos.api` has no type for."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from openkos import api
from openkos.localapi._errors import ErrorBody


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
