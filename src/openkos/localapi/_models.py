"""HTTP models the adapter owns: only what `openkos.api` has no type for."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

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


class PreviewPlan(
    RootModel[
        Annotated[
            api.MergePlan | api.RelatePlan | api.TierPlan | api.KeepDistinctPlan,
            Field(discriminator="kind"),
        ]
    ]
):
    """The plan of a preview, declared as a discriminated union.

    The plan types carry a literal `kind`, so the OpenAPI document names it as the
    discriminator and a generated client narrows `plan` with `switch (plan.kind)`.
    The plan types are `openkos.api`'s own, and the wrapper serialises as the bare
    plan. A defaulted `kind` is always sent, so the response schema marks it (and
    every other defaulted plan field) required. That flag lives HERE and not on
    `DecisionPreview`, because it reaches every nested dataclass: on the preview it
    would also mark `DecisionVersion.selection` required in the response only, and
    `DecisionVersion`, which a client echoes back in a request, would split into
    `-Input` and `-Output` components."""

    model_config = ConfigDict(
        frozen=True, json_schema_serialization_defaults_required=True
    )


class DecisionPreview(BaseModel):
    """`api.DecisionPreview` with its plan declared as a discriminated union. The
    field names are `openkos.api`'s own. A response model only: no
    `extra="forbid"`, which would reach the nested dataclasses and split their
    components into `-Input` and `-Output` variants."""

    model_config = ConfigDict(frozen=True)

    decision_key: str
    action: api.Action
    plan: PreviewPlan
    version: api.DecisionVersion


class FindingPlan(
    RootModel[
        Annotated[api.ReconcilePlan | api.DeclinePlan, Field(discriminator="kind")]
    ]
):
    """The plan of a finding preview, a union discriminated on `kind`, kept apart
    from `PreviewPlan` so the decision preview's union does not change (ADR-0072).
    Defaulted fields are marked required in the response, as for `PreviewPlan`."""

    model_config = ConfigDict(
        frozen=True, json_schema_serialization_defaults_required=True
    )


class FindingPreview(BaseModel):
    """`api.FindingPreview` with its plan declared as a discriminated union. A
    response model only, like `DecisionPreview`."""

    model_config = ConfigDict(frozen=True)

    decision_key: str
    action: api.Action
    plan: FindingPlan
    version: api.DecisionVersion


class FindingPreviewRequest(BaseModel):
    """Which contradiction or revision action to preview for the decision named
    in the path."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: api.Action


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


class PullStatus(BaseModel):
    """A model pull as a poll sees it. The pull's own result is a field here,
    never the poll's HTTP status."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pull_id: str
    model: str
    state: Literal["running", "stopping", "finished", "failed"]
    phase: api.PullPhase | None
    completed_bytes: int
    total_bytes: int | None
    outcome: api.PullOutcome | None
    error: ErrorBody | None


class RebuildStatus(BaseModel):
    """A search rebuild as a poll sees it. `done` of `total` counts the pages
    sent during `embedding`; the rebuild's own result is a field here, never
    the poll's HTTP status. `stopped`: it stopped before writing anything."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rebuild_id: str
    state: Literal["running", "stopping", "stopped", "finished", "failed"]
    phase: api.RebuildPhase | None
    done: int
    total: int
    outcome: api.RebuildOutcome | None
    error: ErrorBody | None


class RebuildRequest(BaseModel):
    """Start a search rebuild. `allow_remote` agrees to send page text to an
    AI model for search that is not on this machine; it is never needed for one
    that is."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    allow_remote: bool = False


class PullRequest(BaseModel):
    """Start a pull of one configured model tag."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: Annotated[str, Field(min_length=1, max_length=256)]


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


class PreviewRequest(_EchoRequest):
    """Which action to preview for the decision named in the path, and, for
    `merge_group` only, the members the person marked and the survivor.

    A request model that carries a nested API dataclass, so it rejects unknown
    top-level keys itself (see `_EchoRequest`) rather than through `extra="forbid"`,
    which would split `GroupSelection` into `-Input` and `-Output` components."""

    action: api.Action
    selection: api.GroupSelection | None = None


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


class MergeHistory(BaseModel):
    """What was merged into a concept, newest first, in an envelope that leaves
    room for additive fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    merges: list[api.MergeRecord]


class MergedConceptList(BaseModel):
    """Every combined page, newest combine first, in an envelope that leaves
    room for additive fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    concepts: list[api.MergedConcept]


class KeptDistinctList(BaseModel):
    """Every member set kept distinct, newest first, in an envelope that leaves
    room for additive fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    groups: list[api.KeptDistinct]


class ReopenPreviewRequest(BaseModel):
    """The members of the kept-distinct ruling to reopen, in any order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    member_ids: list[str]


class ReopenApplyRequest(_EchoRequest):
    """The version the reopen preview issued (echoed unchanged) and how long to
    wait for the workspace lock; a client can shorten the wait only."""

    version: api.ReopenVersion
    wait_seconds: float = Field(default=WRITE_WAIT_SECONDS, ge=0, le=WRITE_WAIT_SECONDS)


class ReopenResponse(BaseModel):
    """What was reopened, and the warnings the apply raised along the way."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reopened: api.Reopened
    warnings: list[str]


class UnrelatePreviewRequest(BaseModel):
    """The typed relation to remove: the source page, the type and the target."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    relation_type: str
    target_id: str


class UnrelateApplyRequest(_EchoRequest):
    """The version the unrelate preview issued (echoed unchanged) and how long
    to wait for the workspace lock; a client can shorten the wait only."""

    version: api.UnrelateVersion
    wait_seconds: float = Field(default=WRITE_WAIT_SECONDS, ge=0, le=WRITE_WAIT_SECONDS)


class UnrelateResponse(BaseModel):
    """What was removed, and the warnings the apply raised along the way."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    unrelated: api.Unrelated
    warnings: list[str]


class DeclinedContradictionList(BaseModel):
    """Every declined contradiction, newest first, in an envelope that leaves
    room for additive fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contradictions: list[api.DeclinedContradiction]


class ContradictionReopenPreviewRequest(BaseModel):
    """The pair of the declined contradiction to reopen, in either order, and
    the page combined into one of them when the contradiction was found inside a
    combined page."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    first_id: str
    second_id: str
    merged_absorbed_id: str | None = None


class ContradictionReopenApplyRequest(_EchoRequest):
    """The version the contradiction reopen preview issued (echoed unchanged)
    and how long to wait for the workspace lock; a client can shorten the wait
    only."""

    version: api.ContradictionReopenVersion
    wait_seconds: float = Field(default=WRITE_WAIT_SECONDS, ge=0, le=WRITE_WAIT_SECONDS)


class ContradictionReopenResponse(BaseModel):
    """What was reopened, and the warnings the apply raised along the way."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reopened: api.ContradictionReopened
    warnings: list[str]


class TierChangePreviewRequest(BaseModel):
    """The type whose tier to set, and the tier, or `null` to remove its
    setting so the engine's default applies."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type_name: str
    tier: api.VolatilityTier | None


class TierChangeApplyRequest(_EchoRequest):
    """The version the tier preview issued (echoed unchanged) and how long to
    wait for the workspace lock; a client can shorten the wait only."""

    version: api.TierChangeVersion
    wait_seconds: float = Field(default=WRITE_WAIT_SECONDS, ge=0, le=WRITE_WAIT_SECONDS)


class TierChangeResponse(BaseModel):
    """What was changed, and the warnings the apply raised along the way."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    changed: api.TierChanged
    warnings: list[str]


class SensitivityPreviewRequest(BaseModel):
    """The page, by its Concept ID, and who may see it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    concept_id: Annotated[str, Field(max_length=1024)]
    level: api.PageSensitivity


class SensitivityApplyRequest(_EchoRequest):
    """The version the sensitivity preview issued (echoed unchanged) and how
    long to wait for the workspace lock; a client can shorten the wait only."""

    version: api.SensitivityVersion
    wait_seconds: float = Field(default=WRITE_WAIT_SECONDS, ge=0, le=WRITE_WAIT_SECONDS)


class SensitivityResponse(BaseModel):
    """What was set, and the warnings the apply raised along the way."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    changed: api.SensitivityChanged
    warnings: list[str]


class ModelSettingsPreviewRequest(BaseModel):
    """The model setup asked for. A `null` URL removes its line, so the
    backend's own default applies. There is no field for a key: the engine
    reads a key only from its environment, which the desktop shell sets."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    backend: api.ModelBackend
    base_url: Annotated[str, Field(max_length=2048)] | None = None
    embedding_base_url: Annotated[str, Field(max_length=2048)] | None = None
    model: Annotated[str, Field(max_length=256)]
    embedding_model: Annotated[str, Field(max_length=256)]


class ModelSettingsApplyRequest(_EchoRequest):
    """The version the settings preview issued (echoed unchanged) and how long
    to wait for the workspace lock; a client can shorten the wait only."""

    version: api.ModelSettingsVersion
    wait_seconds: float = Field(default=WRITE_WAIT_SECONDS, ge=0, le=WRITE_WAIT_SECONDS)


class ModelSettingsResponse(BaseModel):
    """What was written, and the warnings the apply raised along the way."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    changed: api.ModelSettingsChanged
    warnings: list[str]


class UnmergePreviewRequest(BaseModel):
    """Which merge to undo: the survivor and the absorbed concept, and whether
    the first step may discard an edit made to the survivor since its merge."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    survivor_id: str
    absorbed_id: str
    discard_survivor_edits: bool = False


class UnmergeApplyRequest(_EchoRequest):
    """The version the unmerge preview issued (echoed unchanged) and how long to
    wait for the workspace lock; a client can shorten the wait only."""

    version: api.UnmergeVersion
    wait_seconds: float = Field(default=WRITE_WAIT_SECONDS, ge=0, le=WRITE_WAIT_SECONDS)


class UnmergeResponse(BaseModel):
    """What the unmerge restored, and the warnings it raised along the way (for
    example a commit that could not be made)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    unmerged: api.Unmerged
    warnings: list[str]


class AskRequest(BaseModel):
    """A question to ask. An omitted `limit` is the API's default."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str
    limit: Annotated[int, Field(ge=1, strict=True)] | None = None
    include_deprecated: bool = False
    include_confidential: bool = False


class AskJob(BaseModel):
    """An ask as a poll sees it. The answer is `openkos.api.ask`'s own, already
    gated; a failure is a field here, never the poll's HTTP status. A response
    model only: no `extra="forbid"`, which would reach the nested dataclasses."""

    model_config = ConfigDict(frozen=True)

    ask_id: str
    state: Literal["running", "finished", "failed"]
    phase: Literal["retrieving", "assembling", "checking", "synthesizing"] | None
    completed: int | None
    total: int | None
    answer: api.Answer | None
    error: ErrorBody | None


class IngestQuoteRequest(BaseModel):
    """The files to price, by absolute path, and whether the run may send text
    above the workspace's floor. Every path check is the API's."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    paths: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=4096)]],
        Field(min_length=1, max_length=500),
    ]
    include_confidential: bool = False


class RefusedFileQuoteRequest(BaseModel):
    """Whether the run of a refused file's quote may send text above the
    workspace's floor."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    include_confidential: bool = False


class IngestRunRequest(_EchoRequest):
    """The ingest quote's token, echoed unchanged. A missing or null token is a
    refusal the route answers (422 `run_refused`), not a validation error."""

    token: api.IngestQuoteToken | None = None


class IngestJob(BaseModel):
    """An ingest run as a poll sees it. The run's own result is a field here,
    never the poll's HTTP status. A response model only: no `extra="forbid"`,
    which would reach the nested dataclasses."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    state: Literal["running", "stopping", "finished", "failed"]
    index: int | None
    total: int | None
    phase: Literal["reading", "extracting", "writing", "refreshing"] | None
    calls: int
    """Chat calls spent so far."""
    outcome: api.IngestRunOutcome | None
    warnings: list[str]
    """Commits that could not be made, in the API's words."""
    error: ErrorBody | None
