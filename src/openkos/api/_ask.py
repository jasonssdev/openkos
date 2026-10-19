"""Asking a question through the API (ADR-0068).

`ask` answers through the query service the CLI's `query` calls, and gates
the result with the rule `query --json` and the MCP `query` tool share:
`application.query_report` builds the report against the disclosable set
taken after the query ran, and `Answer` is read from that report, so this
module makes no disclosure decision of its own. It is the one result of the
API that is gated; every other result is owner-local.

The model boundary is the CLI's: confidential concepts enter retrieval only
with `include_confidential`, or for a chat client the shared resolver finds
verifiably local while the workspace keeps its local exemption on.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, cast

from openkos import config
from openkos.api import _wiring
from openkos.api._workspace import Workspace, WorkspaceUnreadable
from openkos.application import backends, query_report
from openkos.application import query as query_service
from openkos.application import status as application_status

AskPhase = Literal["retrieving", "assembling", "checking", "synthesizing"]
"""One step of an ask, in the order it always runs them; `checking` only when
the workspace's sufficiency check is on."""

AskOutcome = Literal["answered", "no_match", "no_answer_in_context", "withheld"]
"""`answered`; `no_match` (nothing usable was retrieved, or the question was
blank); `no_answer_in_context` (the sufficiency check refused); `withheld`
(the answer's prompt held a concept that may not be disclosed)."""

NoMatchCause = Literal[
    "none", "empty_query", "zero_hits", "all_unreadable", "insufficient_context"
]
"""Why an ask found nothing to answer from; `none` when it answered."""

AttributionStatus = Literal["reported", "none", "absent", "unparsed"]
"""How the citations were decided: the model reported the blocks it used
(`reported`), reported using none (`none`), or never reported and every
retrieved concept is cited (`absent`, `unparsed`)."""

DEFAULT_LIMIT = 8
"""The CLI's default (ADR-0062); a test holds it equal to the query default."""


@dataclass(frozen=True)
class AskProgress:
    """One progress event: the phase reached, how many came before it, and how
    many there are (4 with the sufficiency check, 3 without)."""

    phase: AskPhase
    completed: int
    total: int


@dataclass(frozen=True)
class AnswerCitation:
    """A concept the answer drew on."""

    concept_id: str
    title: str
    excerpted: bool
    """The model was shown an excerpt of it, not the whole body."""
    confidential: bool
    """It is explicitly marked confidential; only possible with consent."""
    history: Literal["superseded", "refined"] | None
    """Set when it is an earlier version attached as history."""


@dataclass(frozen=True)
class OmittedConcept:
    """A retrieved concept the context budget left out of the prompt entirely."""

    concept_id: str
    title: str
    reason: Literal["context_budget", "context_budget_earlier_version"]


@dataclass(frozen=True)
class Answer:
    """What an ask returns, already gated: no field names, titles or quotes a
    concept outside the disclosable set; `withheld` counts them."""

    question: str
    outcome: AskOutcome
    answer: str | None
    """The answer text; `None` unless `outcome` is `answered`."""
    no_match_cause: NoMatchCause
    citations: tuple[AnswerCitation, ...]
    omitted: tuple[OmittedConcept, ...]
    attribution: AttributionStatus
    withheld: int
    """How many concepts the answer declined to name: a count, never an id."""
    sufficiency_checked: bool
    """The pre-answer sufficiency check ran."""
    sufficiency_degraded: bool
    """The check was on and could not run, so the answer was not guarded."""
    lexical_unavailable: bool
    """The lexical index (`fts.db`) was absent or unreadable."""
    dense_unavailable: bool
    """The vector store was absent or unreadable, or the dense read degraded."""
    stale_indexes: tuple[str, ...]
    """The derived stores this ask reads whose contents predate the bundle."""


def _validated_limit(limit: int) -> int:
    # `type` and not `isinstance`: a `bool` is an `int` and is refused.
    if type(limit) is not int or limit < 1:
        raise ValueError(f"limit must be an integer of at least 1, got {limit!r}")
    return limit


def _read_config(workspace: Workspace) -> config.Config:
    try:
        return config.read_config(workspace.root)
    except (OSError, ValueError) as exc:
        raise WorkspaceUnreadable(
            workspace.root, f"openkos.yaml could not be read ({exc})"
        ) from exc


def _progress_sink(
    progress: Callable[[AskProgress], None] | None,
) -> Callable[[AskPhase, int, int], None] | None:
    if progress is None:
        return None
    sink = progress

    def forward(phase: AskPhase, completed: int, total: int) -> None:
        sink(AskProgress(phase=phase, completed=completed, total=total))

    return forward


def _citations(
    report: Mapping[str, object], outcome: query_service.QueryOutcome
) -> tuple[AnswerCitation, ...]:
    """The report's citations, in its order, with the two markers the report
    does not carry read from the same citation the report kept."""
    by_id = {c.concept_id: c for c in outcome.result.citations}
    rows = cast("list[dict[str, object]]", report["citations"])
    cited: list[AnswerCitation] = []
    for row in rows:
        source = by_id[cast(str, row["concept_id"])]
        cited.append(
            AnswerCitation(
                concept_id=source.concept_id,
                title=cast(str, row["title"]),
                excerpted=cast(bool, row["excerpted"]),
                confidential=source.confidential,
                history=source.history,
            )
        )
    return tuple(cited)


def _omitted(report: Mapping[str, object]) -> tuple[OmittedConcept, ...]:
    rows = cast("list[dict[str, object]]", report["omitted"])
    return tuple(
        OmittedConcept(
            concept_id=cast(str, row["concept_id"]),
            title=cast(str, row["title"]),
            reason=cast(
                Literal["context_budget", "context_budget_earlier_version"],
                row["reason"],
            ),
        )
        for row in rows
    )


def ask(
    workspace: Workspace,
    question: str,
    *,
    limit: int = DEFAULT_LIMIT,
    include_deprecated: bool = False,
    include_confidential: bool = False,
    progress: Callable[[AskProgress], None] | None = None,
) -> Answer:
    """Answer `question` from `workspace`, citing the concepts used.

    Read-only: writes nothing, takes no lock and creates no derived store. A
    blank question, a question nothing matches and a sufficiency refusal are
    outcomes, not errors.

    `include_confidential` is the one consent, with the CLI's meaning:
    confidential concepts may enter retrieval, and so reach the configured
    model even when it is not verifiably local, and may be named in the
    answer. Without it a local model may still see them through the local
    exemption, and an answer whose prompt held one is `withheld`.

    `progress` hears each phase in order; an exception it raises ends the ask.

    Raises `ValueError` for a `limit` below 1 (before any client is built),
    `WorkspaceUnreadable` for an unreadable configuration, and the neutral
    backend errors (`BackendUnavailable`, `BackendModelNotFound`,
    `BackendEmbeddingDimensionMismatch`, `BackendGenerationCapped`,
    `BackendError`) unchanged."""
    limit = _validated_limit(limit)
    cfg = _read_config(workspace)
    layout = config.WorkspaceLayout(workspace.root)
    llm = _wiring.default_chat_client(cfg, None)
    # #1560: the pre-synthesis check runs on its own task model.
    sufficiency_llm = query_service.build_sufficiency_client(
        cfg, lambda task: _wiring.default_chat_client(cfg, task)
    )
    embedder = _wiring.default_embedder(cfg)
    # Confidential bodies reach the check model as well as the synthesis one,
    # so the exemption holds only when every chat client is local.
    local_exemption = backends.resolve_local_exemption_all(
        [
            cast(backends.HasLocality, client)
            for client in (llm, sufficiency_llm)
            if client is not None
        ],
        cfg,
    )
    stale = application_status.stale_index_names(layout, reads=("fts",))
    outcome = query_service.run_query(
        question,
        layout=layout,
        cfg=cfg,
        llm=llm,
        embedder=embedder,
        limit=limit,
        include_deprecated=include_deprecated,
        include_confidential=include_confidential,
        local_exemption=local_exemption,
        sufficiency_llm=sufficiency_llm,
        progress=_progress_sink(progress),
    )
    # Taken after the query ran, so a concept raised to confidential while it
    # ran is still withheld.
    disclosable = query_report.disclosable_ids(
        layout.bundle_dir, include_confidential=include_confidential
    )
    report = query_report.build_query_report(
        outcome,
        question=question,
        limit=limit,
        cfg=cfg,
        openkos_version="",  # the report's version field is not part of an Answer
        disclosable=disclosable,
    )
    sufficiency = cast("dict[str, object]", report["sufficiency"])
    attribution = cast("dict[str, object]", report["attribution"])
    result = outcome.result
    return Answer(
        question=question,
        outcome=cast(AskOutcome, report["outcome"]),
        answer=cast("str | None", report["answer"]),
        no_match_cause=result.no_match_cause,
        citations=_citations(report, outcome),
        omitted=_omitted(report),
        attribution=cast(AttributionStatus, attribution["status"]),
        withheld=cast(int, report["withheld"]),
        sufficiency_checked=cast(bool, sufficiency["invoked"]),
        sufficiency_degraded=cast(bool, sufficiency["degraded"]),
        lexical_unavailable=outcome.fts_unavailable,
        dense_unavailable=outcome.vector_store_unavailable or result.dense_degraded,
        stale_indexes=stale,
    )
