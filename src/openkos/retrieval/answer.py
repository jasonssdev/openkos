"""Cited answer library: retrieve -> assemble -> answer over a compiled bundle.

`answer()` composes three archived seams end-to-end: `state.fts.build_index`
(retrieve), a per-hit guarded `okf.load_frontmatter` re-read (assemble), and
an injected `llm.LLMBackend` (answer). Core is synchronous; the `llm`
instance is caller-supplied, so this module never imports `openkos.config`
(mirrors `llm/ollama.py`'s leaf discipline). Typed exceptions
(`FtsUnavailable`, the `OllamaError` family) propagate unswallowed to the
caller, such as the `query` command.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from openkos.llm.base import LLMBackend, Message
from openkos.model import okf
from openkos.state import fts

NO_MATCH = "No matching concepts were found in the compiled bundle for this question."
"""Stable no-match text (D3): zero or all-skipped hits short-circuit to this,
without calling `llm.chat`."""

NoMatchCause = Literal["none", "empty_query", "zero_hits", "all_unreadable"]
"""Why an `AnswerResult` short-circuited to `NO_MATCH` -- `"none"` on a
successful answer, otherwise which guard tripped (D-shortcircuit): an
empty/whitespace-only question, zero raw FTS hits, or hits that were all
unreadable/unparseable at re-read time."""

_SYSTEM_PROMPT = (
    "You are OpenKOS, a local-first knowledge assistant. Answer the question "
    "using ONLY the numbered CONTEXT concepts below -- do not use outside "
    "knowledge. Cite the concepts you rely on by their concept id. If the "
    "context does not contain enough information to answer, say so plainly "
    'rather than guessing; an honest "the compiled bundle does not cover '
    'this" is the correct answer when the context is insufficient.'
)
"""Stable system half of the 2-message prompt (D5): local-first grounding
rules (answer only from CONTEXT, cite by concept id, admit gaps honestly)
baked into system text; the `user` message carries the context blocks +
question."""


@dataclass(frozen=True)
class Citation:
    """One concept whose body was placed in the LLM context (D4)."""

    concept_id: str
    """The OKF concept ID (bundle-relative path, `.md` suffix removed)."""
    title: str
    """Frontmatter `title`; falls back to `concept_id` when missing/empty."""


@dataclass(frozen=True)
class AnswerResult:
    """The LLM's answer text, the concepts cited to produce it, and the
    retrieval metadata that explains how the answer was reached (surfaced by
    `query` on stderr and used to render a cause-specific no-match message)."""

    answer: str
    """The LLM's reply text, or the stable `NO_MATCH` string (D3)."""
    citations: list[Citation]
    """One `Citation` per concept placed in context, in hit-rank order."""
    fts_hit_count: int
    """Raw `FtsIndex.search` hit count, BEFORE guarded re-read filtering --
    always `len(hits)`, so it stays `> 0` even when every hit is later
    skipped as unreadable."""
    llm_invoked: bool
    """Whether `llm.chat` was called for this answer."""
    no_match_cause: NoMatchCause
    """`"none"` on a successful answer; otherwise which no-match guard
    tripped. Never derived from `citations` alone -- distinguishes a
    zero-hit search from hits that were found but all unreadable."""
    skip_notices: list[str]
    """Copied from `FtsIndex.skipped` for this build: files skipped while
    building the search index. A whole-bundle build-time signal, unrelated
    to whether this query's hits matched."""


def _assemble_context(
    bundle_dir: Path, hits: list[fts.FtsHit]
) -> tuple[list[str], list[Citation]]:
    """Guarded per-hit re-read (D2): re-read + re-parse each hit's concept doc,
    skipping anything unreadable or unparseable rather than raising. Returns
    one labeled context block and one `Citation` per successfully read hit,
    both in hit-rank order.
    """
    context_blocks: list[str] = []
    citations: list[Citation] = []
    for hit in hits:
        try:
            text = (bundle_dir / f"{hit.concept_id}.md").read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            metadata, body = okf.load_frontmatter(text)
        except Exception:  # noqa: S112 -- broad: any parse failure skips this hit (D2)
            continue
        title = str(metadata.get("title") or "") or hit.concept_id
        context_blocks.append(f"[concept_id: {hit.concept_id} — {title}]\n{body}")
        citations.append(Citation(concept_id=hit.concept_id, title=title))
    return context_blocks, citations


def _build_messages(context_blocks: list[str], question: str) -> list[Message]:
    """Assemble the 2-message prompt (D5): system grounding + delimited
    context blocks + question."""
    user_content = (
        "CONTEXT:\n\n" + "\n\n".join(context_blocks) + f"\n\nQUESTION:\n{question}"
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _classify_no_match(question: str, hits: list[fts.FtsHit]) -> NoMatchCause:
    """Classify why a no-match happened: an empty/whitespace-only `question`
    always wins (checked before hits, so it never collapses into
    `"zero_hits"`), then a genuine zero-hit search, else every hit was
    unreadable/unparseable at re-read time."""
    if not question.split():
        return "empty_query"
    if not hits:
        return "zero_hits"
    return "all_unreadable"


def answer(
    question: str, *, bundle_dir: Path, llm: LLMBackend, limit: int = 5
) -> AnswerResult:
    """Answer `question` from `bundle_dir` using `llm`, citing the concepts used.

    See module docstring for the retrieve/assemble/answer flow.
    """
    with fts.build_index(bundle_dir) as index:
        hits = index.search(question, limit)
        context_blocks, citations = _assemble_context(bundle_dir, hits)
        skip_notices = list(index.skipped)

    if not context_blocks:
        return AnswerResult(
            answer=NO_MATCH,
            citations=[],
            fts_hit_count=len(hits),
            llm_invoked=False,
            no_match_cause=_classify_no_match(question, hits),
            skip_notices=skip_notices,
        )

    reply = llm.chat(_build_messages(context_blocks, question))
    return AnswerResult(
        answer=reply,
        citations=citations,
        fts_hit_count=len(hits),
        llm_invoked=True,
        no_match_cause="none",
        skip_notices=skip_notices,
    )
