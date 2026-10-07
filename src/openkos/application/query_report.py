"""The machine-readable `query` report behind `openkos query --json` (#1345).

Observability only: this module reads a finished `QueryOutcome` and renders it;
it never retrieves, assembles, prompts or post-processes anything. The object
is versioned (`SCHEMA_VERSION`) and its field set is the public contract
documented in `docs/cli.md` and `openspec/specs/query-command/spec.md`.

Sensitivity follows the MCP gate's rule (`mcp/gate.py::disclose_query`), built
on the same allowed-set primitive: an id outside the disclosable set is never
named, titled or quoted anywhere in the object -- it is only counted -- and an
answer whose prompt held such a concept is withheld, since its wording may
have drawn on it. The local-backend exemption can let a confidential concept
into the prompt without `--include-confidential`; this is the seam that keeps
it out of the output.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from openkos import config, sensitivity
from openkos.application import query as query_service
from openkos.application.backends import BACKEND_OPENAI_COMPATIBLE
from openkos.retrieval.answer import AnswerTrace

SCHEMA_VERSION: Final = 1

OUTCOME_ANSWERED: Final = "answered"
OUTCOME_NO_MATCH: Final = "no_match"
OUTCOME_NO_ANSWER_IN_CONTEXT: Final = "no_answer_in_context"
OUTCOME_WITHHELD: Final = "withheld"


def disclosable_ids(bundle_dir: Path, *, include_confidential: bool) -> frozenset[str]:
    """The ids this report may name: one bundle walk, taken AFTER the query
    ran so a concept raised to confidential mid-run is still caught."""
    return sensitivity.disclosable_concept_ids(
        bundle_dir, expose_confidential=include_confidential
    )


def _llm_section(cfg: config.Config) -> dict[str, object]:
    """What OpenKOS sends, per backend; `None` for what it does not set.

    Ollama receives `options.num_ctx` and `options.num_predict`; an
    OpenAI-compatible server receives `max_tokens` (reported as
    `num_predict`) and never `num_ctx`. `temperature`/`seed` are forwarded
    only when the workspace pins them."""
    openai = cfg.backend == BACKEND_OPENAI_COMPATIBLE
    return {
        "backend": cfg.backend,
        "model": config.resolve_task_model(cfg, None),
        "embedding_model": cfg.embedding_model,
        "num_ctx": None if openai else cfg.context_window,
        "num_predict": cfg.max_generation_tokens,
        "temperature": cfg.temperature,
        "seed": cfg.seed,
    }


def _attribution_status(attribution: str, used_indices: tuple[int, ...]) -> str:
    """`none` is a `reported` attribution that names no block: the answer said
    it drew on none of the context. `reported` always names at least one."""
    if attribution == "reported" and not used_indices:
        return "none"
    return attribution


def _outcome(result_cause: str, answer_withheld: bool) -> str:
    if result_cause == "insufficient_context":
        return OUTCOME_NO_ANSWER_IN_CONTEXT
    if result_cause != "none":
        return OUTCOME_NO_MATCH
    return OUTCOME_WITHHELD if answer_withheld else OUTCOME_ANSWERED


def build_query_report(
    outcome: query_service.QueryOutcome,
    *,
    question: str,
    limit: int,
    cfg: config.Config,
    openkos_version: str,
    disclosable: frozenset[str],
) -> dict[str, object]:
    """Render `outcome` as the schema-v1 object, scrubbed against `disclosable`."""
    result = outcome.result
    trace = result.trace if result.trace is not None else AnswerTrace()
    withheld_ids: set[str] = set()

    def visible(concept_id: str) -> bool:
        if concept_id in disclosable:
            return True
        withheld_ids.add(concept_id)
        return False

    context_blocks = [
        {
            "index": block.index,
            "concept_id": block.concept_id,
            "title": block.title,
            "excerpted": block.excerpted,
            "text": block.text,
        }
        for block in trace.context_blocks
        if visible(block.concept_id)
    ]
    omitted = [
        {"concept_id": o.concept_id, "title": o.title, "reason": o.reason}
        for o in trace.omitted
        if visible(o.concept_id)
    ]
    retrieved = [
        {
            "rank": row.rank,
            "concept_id": row.concept_id,
            "title": row.title,
            "fts_rank": row.fts_rank,
            "dense_rank": row.dense_rank,
            "rrf_score": row.rrf_score,
        }
        for row in trace.retrieved
        if visible(row.concept_id)
    ]
    citations = [
        {
            "concept_id": citation.concept_id,
            "title": citation.title,
            "excerpted": citation.excerpted,
        }
        for citation in result.citations
        if visible(citation.concept_id)
    ]
    # Every concept whose content entered the prompt, cited or not, plus the
    # fail-closed misalignment case (`gate.disclose_query`'s rule).
    # A list, not a generator: every id must pass through `visible` so the
    # withheld count sees them all rather than stopping at the first.
    context_visible = [visible(concept_id) for concept_id in result.context_ids]
    context_withheld = len(result.context_ids) != result.context_block_count or not all(
        context_visible
    )
    answer_withheld = context_withheld or len(citations) != len(result.citations)
    outcome_name = _outcome(result.no_match_cause, answer_withheld)
    answered = outcome_name == OUTCOME_ANSWERED

    return {
        "schema_version": SCHEMA_VERSION,
        "openkos_version": openkos_version,
        "question": question,
        "limit": limit,
        "outcome": outcome_name,
        "answer": result.answer if answered else None,
        "sufficiency": {
            "enabled": cfg.sufficiency_check,
            "invoked": trace.sufficiency_invoked,
            "refused": result.no_match_cause == "insufficient_context",
            "degraded": result.sufficiency_degraded,
            # The check quotes the context; a withheld concept's sentence
            # must not leave through it.
            "raw_reply": None if context_withheld else trace.sufficiency_raw_reply,
        },
        "attribution": {
            "status": _attribution_status(result.attribution, trace.used_indices),
            "used_indices": list(trace.used_indices),
        },
        "context_blocks": context_blocks,
        "omitted": omitted,
        "retrieved": retrieved,
        "citations": citations,
        "counts": {
            "fts_hits": result.fts_hit_count,
            "dense_hits": result.dense_hit_count,
            "fused": result.fused_count,
            "context_blocks": result.context_block_count,
        },
        # Distinct concepts the report refused to name, plus those retrieval
        # dropped before fusion (#1334). A count, never an id.
        "withheld": len(withheld_ids) + result.confidential_excluded_count,
        "llm": _llm_section(cfg),
        "prompts": {
            "system_sha256": trace.system_sha256,
            "user_sha256": trace.user_sha256,
            "sufficiency_sha256": trace.sufficiency_sha256,
        },
    }
