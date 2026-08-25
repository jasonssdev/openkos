"""Cited answer library: retrieve -> fuse -> assemble -> answer over a
compiled bundle.

`answer()` composes four seams end-to-end: an injected, read-only
`fts_index` handle (lexical retrieval), an injected `Embedder` + `VectorStore`
(dense retrieval, optional), `retrieval.fusion.fuse` (rank-position fusion of
exactly those two lists, and the whole of the ranking), a per-hit guarded
`okf.load_frontmatter` re-read (assemble), and an injected `llm.LLMBackend`
(answer). Core is synchronous; `llm`, `embedder`, `vector_store`, and
`fts_index` are ALL caller-supplied, so this module never imports
`openkos.config` (mirrors `llm/ollama.py`'s leaf discipline).

Slice 5 (performance-caching, PR3, design D4): `answer()` no longer builds
`fts_index` itself -- it reads whatever ALREADY-OPEN, read-only handle the
caller injects (mirroring `vector_store`'s existing contract exactly). The
caller (`query`'s CLI wiring) opens the persisted `.openkos/fts.db`
read-only and passes `None` when the store is absent or unopenable/corrupt
-- that open-failure-to-`None` decision happens ENTIRELY at the caller's
store-open call site, never here. `answer()` itself performs exactly ONE
check per handle: `is None`. It NEVER computes or compares a bundle manifest
hash (D2 binding contract) -- a properly-`reindex`ed handle is always
treated as fresh; staleness detection is `reindex`'s exclusive job. Because
there is no more per-query build, the empty-query short-circuit now touches
ZERO injected handles at all (not even a call to open one) -- provable via
spies on all three (follow-up #1).

Typed exceptions (the `OllamaError` family, or any exception a caller's
`fts_index.search`/`vector_store.query` implementation might itself raise
OUTSIDE its own documented degrade cases) propagate unswallowed to the
caller -- the exception-vs-degrade boundary lives ONLY at the handle's
OWN documented failure modes (dense: `VecUnavailable`/read-path
`sqlite3.Error`, plus the GENERIC transient `OllamaError` from the question
embed), never at a broader catch-all here. The three FATAL `OllamaError`
subclasses -- `OllamaUnavailable`, `OllamaModelNotFound`, and
`OllamaEmbeddingDimensionMismatch` (issue #209) -- are on the propagating
side of that split, never the degrading one: each names a permanent
environment or configuration fault that no re-run can heal.
`fts_index.search` itself is documented as never raising (mirrors
`state.fts.FtsIndex.search`'s own contract), so FTS stays mandatory and
un-degraded except via the `fts_index is None` branch.

Issue #434 removed a FIFTH seam: an injected, read-only `graph_index` handle
feeding a seeded personalized-PageRank stage between the two fuses, whose
bounded contribution then filled reserved tail slots of the final ranking.
Two A/B measurements ended it. On a 21-node graph one concept,
`concepts/document-skills`, was the contribution on 6 of 10 unrelated
questions; on a 27-node graph the concentration spread out but per-question
judgement was 7 harmful, 3 neutral, 0 beneficial -- including evicting
`sources/mcp-origin` from "When did MCP originate?" and `sources/10-mcp`
from a question about which protocol BigQuery belongs to.

That is a verdict on the RANKING FUNCTION, not on the typed graph. Seeded
PPR ranks by global centrality, a property of the corpus rather than of the
question, so a bigger graph only changes which central node wins the
reserved slot -- the slot still costs a base hit. The typed graph itself is
untouched and still earns its keep: `resolution/contradiction.py` derives
candidate pairs from typed edges, and `reindex` still maintains
`.openkos/graph.db`.

status-aware-retrieval (MVP-3 gap #8 · S1, Phase 2): unless the caller
passes `include_deprecated=True`, `answer()` computes the single shared
`openkos.lifecycle.deprecated_concept_ids(bundle_dir)` predicate ONCE per
call and filters `hits`/`vec_hits` via `lifecycle.filter_hits` BEFORE the
fuse. Every count on `AnswerResult` (`fts_hit_count`, `dense_hit_count`,
`fused_count`) is therefore a POST-filter count. `include_deprecated=True`
skips the predicate walk entirely -- no `_iter_docs` pass, no filtering --
restoring today's status-blind behavior byte-for-byte at zero added cost.
"""

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from openkos import lifecycle, sensitivity
from openkos.extraction.concept import LANGUAGE_FUNCTION_WORDS
from openkos.llm.base import Embedder, LLMBackend, Message
from openkos.llm.ollama import (
    OllamaEmbeddingDimensionMismatch,
    OllamaError,
    OllamaModelNotFound,
    OllamaUnavailable,
)
from openkos.model import okf
from openkos.model.types import INSIGHT_TYPE as _INSIGHT_TYPE
from openkos.retrieval import fusion, pool
from openkos.state import fts
from openkos.state.vectorstore import VecHit, VectorStore, VecUnavailable

NO_MATCH = "No matching concepts were found in the compiled bundle for this question."
"""Stable no-match text (D3): zero or all-skipped hits short-circuit to this,
without calling `llm.chat`."""

Attribution = Literal["reported", "absent", "unparsed"]
"""How `AnswerResult.citations` was decided for this call (#753).

- `"reported"` -- the reply carried a usable attribution line and the
  citations are exactly the blocks it named (possibly none).
- `"absent"`   -- no attribution line at all, so every retrieved concept is
  cited: the pre-#753 behavior, byte for byte.
- `"unparsed"` -- a line was there but named nothing usable (no integers, or
  only out-of-range ones), so the same conservative fallback applies.

`"absent"` and `"unparsed"` are kept apart rather than folded into one
"fell back" value because they answer different questions about a model: the
first says it ignored the instruction, the second says it tried and produced
something unusable. Collapsing them would make the eval harness unable to
tell a compliance problem from a format problem -- which is exactly the
number that decides whether the fallback can ever be tightened."""

NoMatchCause = Literal[
    "none", "empty_query", "zero_hits", "all_unreadable", "insufficient_context"
]
"""Why an `AnswerResult` short-circuited to `NO_MATCH` -- `"none"` on a
successful answer, otherwise which guard tripped (D-shortcircuit): an
empty/whitespace-only question, zero hits from BOTH retrievers, hits that
were all unreadable/unparseable at re-read time, or (issue #760) context
that was assembled and then judged not to contain the answer.

`insufficient_context` is the only one reached with a NON-empty context:
the others all mean nothing usable was retrieved, while this one means
documents were found, read, and judged unable to answer. Conflating them
would tell a user to "try different wording" when the bundle simply does
not cover the topic."""

_ATTRIBUTION_KEYWORD: Final = "USED"
"""Opening token of the attribution line the model closes its answer with.

Deliberately an English all-caps keyword rather than anything localized: it
is machinery addressed to the model, and the same corpus is answered in
whatever language the question arrives in -- a marker that varied with the
answer's language would need a parser per language and would collide with
ordinary prose in each of them."""

_ATTRIBUTION_NONE: Final = "none"
"""The payload meaning "this answer draws on no context block at all".

A REPORT, not a parse failure, and the distinction is the point: it is
#753's own specimen (an answer written from the model's own knowledge) and
it must empty the citation list rather than fall back to citing everything.
`query --save` then refuses the filing outright, because `build_concept`
requires non-empty provenance -- the ungrounded answer stops at the screen
instead of becoming a permanent bundle concept with five invented
provenance edges."""


_SYSTEM_PROMPT = (
    "You are OpenKOS, a local-first knowledge assistant. Answer the question "
    "using ONLY the numbered CONTEXT concepts below -- do not use outside "
    "knowledge. Write prose only: a concept id is an internal identifier, so "
    "never quote one in your answer and never repeat the bracketed labels "
    "that head the context blocks -- refer to a concept by its number if you "
    "must refer to one at all. If the "
    "context does not contain enough information to answer, say so plainly "
    'rather than guessing; an honest "the compiled bundle does not cover '
    'this" is the correct answer when the context is insufficient. '
    "Finish with one final line of its own, exactly of the form "
    f"'{_ATTRIBUTION_KEYWORD}: 1, 3' -- the numbers of the CONTEXT blocks "
    "your answer actually draws on. List a block only if your answer genuinely "
    "uses what it says, never merely because it was provided, and write "
    f"'{_ATTRIBUTION_KEYWORD}: {_ATTRIBUTION_NONE}' if your answer draws on "
    "none of them. The caller cites exactly the blocks this line names. "
    "Close with that line every time, in exactly that form, however short "
    "or long the answer and whatever language it is written in: the "
    f"{_ATTRIBUTION_KEYWORD} line is machinery, not prose -- never "
    "translate it, never omit it."
)
"""Stable system half of the 2-message prompt (D5): local-first grounding
rules (answer only from CONTEXT, keep internal ids out of the prose, admit
gaps honestly) baked into system text; the `user` message carries the
context blocks + question.

The id instruction is INVERTED from what shipped originally (#193). It used
to read "cite the concepts you rely on by their concept id", so the model
was doing exactly as told when it emitted `[concept_id: sources/x]` into the
answer -- and the label it copied is the one `_assemble_context` writes at
the head of every block. The leak was an instruction, not a model quirk,
which is why `_strip_concept_id_scaffolding` is a backstop here rather than
the fix: leaving the old wording would have the prompt fighting the
post-processor on every single call.

The closing anchor sentence is issue #871's measured fix. The report said
Spanish answers drop the attribution line; the measurement
(`evals/query_attribution/`, mirrored ES/EN corpora crossed with a
short/long question regime, two pooled 3-run sweeps per arm, n=30 per cell)
said otherwise: omission is QUESTION-shaped, not language-shaped -- worst
in English one-line answers (en-short 0.63) and the longest structured
answers, while es-short sat at 1.00. The anchor names both measured
regimes plus language, and moved every cell non-negatively: es-long
0.83 -> 1.00 (the reported `--save` case), en-short 0.63 -> 0.77, en-long
0.77 -> 0.83, overall 0.81 -> 0.90. A residual remains (English,
question-shaped -- including, ironically, "why are verbatim citations
required?"); the conservative absent-fallback below and `--save`'s
unverified-provenance consent gate continue to catch it."""


_CONCEPT_ID_SCAFFOLD = r"""
      \[[ \t]*concept_id[ \t]*:[^\]\n]*\]        # [concept_id: id — Title]
    | \([ \t]*concept_id[ \t]*:[^)\n]*\)         # (concept_id: id)
    | concept_id[ \t]*:[ \t]*[^\s,;:!?)\]]+(?<![.])   # bare concept_id: id
"""
"""The three shapes an internal id takes when it reaches the prose (#193).

The delimited forms stop at their closing delimiter and are refused a
newline, so an unbalanced bracket consumes one line at most rather than
running to the end of the answer.

The bare form has no closing delimiter to find, so it terminates at
whitespace or sentence punctuation -- deliberately under-matching, because
truncating an identifier is a visible blemish while over-matching would
silently eat the rest of a sentence.

The dot is handled by a trailing lookbehind rather than by the terminator
class, because an id can legitimately CONTAIN one: a Source's slug is its
filename stem with only the final extension removed, so `notes.v2.txt`
ingests as `sources/notes.v2`. Excluding the dot outright stopped the match
mid-identifier and left the remainder welded onto the preceding word --
`...documented in.v2 for reference` -- which corrupts neighbouring prose
instead of merely truncating. The lookbehind lets the greedy class take the
dot and then backtrack off it, so a sentence-final dot still ends the match
while an id-internal one does not.
"""

_SCAFFOLD_AT_LINE_START_RE = re.compile(
    rf"(?m)^[ \t]*(?:{_CONCEPT_ID_SCAFFOLD})[ \t]*", re.VERBOSE
)
"""Scaffolding that opens a line, with the space that followed it.

Separate from the inline rule because the two need opposite treatment of
trailing space: absorbing it inline would weld the surrounding words
together, while NOT absorbing it here would leave the line indented by one
space -- which in markdown is noise at best and a changed block at worst."""

_SCAFFOLD_INLINE_RE = re.compile(rf"[ \t]*(?:{_CONCEPT_ID_SCAFFOLD})", re.VERBOSE)
"""Scaffolding inside a line, with the space that preceded it.

Only HORIZONTAL space is absorbed. Using `\\s*` would swallow the newline
before a scaffold that opens a line and silently reflow the answer's
markdown -- joining a list item or a heading onto the previous paragraph."""


_ATTRIBUTION_LINE_RE = re.compile(
    rf"(?im)^[ \t]*{re.escape(_ATTRIBUTION_KEYWORD)}[ \t]*:[ \t]*(?P<payload>[^\n]*)$"
)
"""The attribution line, anchored to a line of its own.

Case-insensitive and tolerant of surrounding space because the marker is
produced by a model rather than by code, but NOT tolerant of the keyword
appearing mid-sentence: requiring the line start is what keeps an answer
that legitimately contains the word from being truncated at it."""

_ATTRIBUTION_INDEX_RE = re.compile(r"\d+")


def _split_attribution(
    reply: str, block_count: int
) -> tuple[str, frozenset[int] | None, Attribution]:
    """Split `reply` into its prose, the block numbers it reports, and how.

    Returns the prose with the marker line removed, either the reported
    1-based block indices (possibly an EMPTY set, meaning the answer reports
    drawing on nothing) or `None` when nothing usable was reported, and the
    `Attribution` value naming which of those three cases happened. The
    status is returned rather than left for the caller to infer, because
    `None` alone cannot tell "no line" from "a line naming nothing" -- and
    the whole reason those are separate values is that a caller reading only
    the set would collapse them.

    The LAST marker line wins. A model that restates the format mid-answer
    before emitting the real one at the end would otherwise have its example
    read as the report -- and the instruction asks for a closing line, so the
    final one is the one that was meant.

    Out-of-range numbers are DROPPED rather than clamped: `9` against three
    blocks names no concept that was ever sent, and clamping it to the third
    would manufacture a citation the model never claimed. A report whose
    numbers are all out of range therefore survives as `None`, not as an
    empty set -- an empty set is a positive claim ("none of them"), which is
    not what a hallucinated index says.

    The marker is stripped whether or not it parsed. It is machinery either
    way, and `query --save` files this prose as a real bundle concept, so
    leaving an unparsed one behind writes it into the bundle permanently.
    """
    matches = list(_ATTRIBUTION_LINE_RE.finditer(reply))
    if not matches:
        return reply, None, "absent"
    last = matches[-1]
    prose = (reply[: last.start()] + reply[last.end() :]).strip()
    payload = last.group("payload").strip()
    if payload.casefold() == _ATTRIBUTION_NONE.casefold():
        return prose, frozenset(), "reported"
    reported = {
        index
        for raw in _ATTRIBUTION_INDEX_RE.findall(payload)
        if 1 <= (index := int(raw)) <= block_count
    }
    if not reported:
        return prose, None, "unparsed"
    return prose, frozenset(reported), "reported"


def _strip_concept_id_scaffolding(text: str) -> str:
    """Remove internal concept-id scaffolding from answer prose (#193).

    A BACKSTOP, not the fix. The fix is `_SYSTEM_PROMPT`, which no longer
    asks for ids in the answer; this exists because prompt compliance is not
    a guarantee, and because `query --save` files the answer text as a real
    concept -- so an id that survives here is not merely shown once, it is
    written into the bundle permanently.

    Scoped to removal and nothing else: no whitespace normalization, no
    punctuation repair. A reply with no scaffolding is returned
    byte-identical, which is the property that keeps this from quietly
    becoming a rewriting pass over compliant answers. The cost is that
    `... based on [concept_id: x] .` keeps its stranded space -- accepted,
    because the alternative is a rule that edits prose no model ever
    scaffolded.

    Provenance is unaffected: `AnswerResult.citations` is built from the
    context assembly, never parsed back out of the reply, so stripping the
    prose removes the redundant copy and not the traceability.
    """
    return _SCAFFOLD_INLINE_RE.sub("", _SCAFFOLD_AT_LINE_START_RE.sub("", text))


@dataclass(frozen=True)
class Citation:
    """One concept whose body was placed in the LLM context (D4)."""

    concept_id: str
    """The OKF concept ID (bundle-relative path, `.md` suffix removed)."""
    title: str
    """Frontmatter `title`; falls back to `concept_id` when missing/empty."""
    confidential: bool = False
    """Whether this concept's freshly re-read frontmatter EXPLICITLY carries
    `sensitivity: confidential` (issue #569). Transparency, not a gate --
    it mirrors `_commit_has_confidential`'s explicit-value-only posture
    rather than `sensitivity.should_block`'s fail-closed one, because a
    false 'confidential' marker on an unlabeled doc would train users to
    ignore the real ones. Defaults `False` so every existing construction
    site stays valid."""


@dataclass(frozen=True)
class AnswerResult:
    """The LLM's answer text, the concepts cited to produce it, and the
    retrieval metadata that explains how the answer was reached (surfaced by
    `query` on stderr and used to render a cause-specific no-match message)."""

    answer: str
    """The LLM's reply text, or the stable `NO_MATCH` string (D3)."""
    citations: list[Citation]
    """One `Citation` per concept placed in context, in fused-rank order."""
    fts_hit_count: int
    """FTS hit count for this call, BEFORE guarded re-read filtering but
    AFTER the lifecycle status filter (status-aware-retrieval, Phase 2):
    always `len(hits)` where `hits` has already had deprecated/superseded
    concept ids removed (unless `include_deprecated=True`), so it stays `>
    0` even when every surviving hit is later skipped as unreadable."""
    llm_invoked: bool
    """Whether the SYNTHESIS `llm.chat` call was made for this answer.

    Narrowed from "whether `llm.chat` was called" when the sufficiency check
    (#760) added a SECOND chat seam: a refused answer HAS made a chat call
    and still produced no synthesis. This field feeds `query`'s
    `LLM invoked|skipped` summary term, which is about the expensive call;
    `no_match_cause == "insufficient_context"` is what says the cheap one ran
    and refused."""
    no_match_cause: NoMatchCause
    """`"none"` on a successful answer; otherwise which no-match guard
    tripped. Never derived from `citations` alone -- distinguishes a
    zero-hit search (from BOTH retrievers) from hits that were found but all
    unreadable."""
    skip_notices: list[str]
    """Copied from the injected `fts_index.skipped` for this call -- `[]`
    when `fts_index` is absent, or when it is a read-only-opened persisted
    handle (which carries no reindex-time skip notices forward)."""
    dense_hit_count: int = 0
    """`vector_store.query` hit count for this call, AFTER the lifecycle
    status filter (additive; status-aware-retrieval, Phase 2)."""
    fused_count: int = 0
    """Number of distinct `concept_id`s in the fused, limit-truncated list
    (additive). Already reflects the lifecycle status filter, since both
    `hits` and `vec_hits` are filtered before this fuse."""
    context_block_count: int = 0
    """How many context blocks were actually SENT to the model.

    NOT `fused_count`, which is counted before `_assemble_context`'s
    per-concept skip guard runs: a fused hit that is unreadable, unparseable
    or sensitivity-blocked at re-read never reaches the prompt, so the two
    diverge exactly when a concept vanishes between the fuse and the send.
    Anything reporting "the answer drew on none of N concepts" must use THIS
    number -- `fused_count` would name concepts the model was never shown.

    Defaults to `0`, which is the honest value for every short-circuit
    return above: those assemble no context at all."""
    attribution: Attribution = "absent"
    """How this call's `citations` list was decided (#753).

    Defaults to `"absent"`, which is both the pre-#753 behavior and the
    honest value for every short-circuit return above: those never reach
    `llm.chat`, so no model ever reported anything for them."""
    sufficiency_degraded: bool = False
    """`True` when the pre-synthesis sufficiency check was REQUESTED and could
    not run (#764), so synthesis proceeded unguarded.

    Never `True` when the check was not requested at all, and never for a
    check that ran and allowed the answer through: the flag means "could not
    run", so an operator notice built on it fires only when the guard they
    configured is actually missing. Mirrors `dense_degraded` below, which
    models the same shape for the retrieval half."""
    dense_degraded: bool = False
    """`True` when dense retrieval could not proceed this call (absent
    `vector_store`, `VecUnavailable`, a read-path `sqlite3.Error`, or the
    GENERIC transient `OllamaError` from the question embed) and FTS-only
    fusion was used instead; `False` when dense retrieval ran normally
    (additive). NEVER set for a FATAL question-embed subclass
    (`OllamaUnavailable`, `OllamaModelNotFound`,
    `OllamaEmbeddingDimensionMismatch`) -- those propagate instead, so no
    `AnswerResult` is produced at all (issue #209)."""


def _assemble_context(
    bundle_dir: Path,
    concept_ids: list[str],
    blocked: frozenset[str] = frozenset(),
    *,
    include_confidential: bool = False,
    local_exemption: bool = False,
) -> tuple[list[str], list[Citation]]:
    """Guarded per-hit re-read (D2): re-read + re-parse each fused
    `concept_id`'s doc, skipping anything unreadable or unparseable rather
    than raising. Returns one labeled context block and one `Citation` per
    successfully read concept, both in fused-rank order.

    sensitivity-fail-closed-filter (S3b, defense-in-depth): any `concept_id`
    present in `blocked` is ALSO skipped here, even though it is normally
    already excluded upstream at the hit-seam filter -- this is a redundant
    post-filter safety net (spec: Exclusion, Not Redaction), never the
    primary enforcement point. Defaults to an empty frozenset, a total
    no-op, preserving byte-identical behavior for every caller that never
    passes one.

    Correction batch (post-4R-review, FIX 2 -- R4 walk-bypass leak, CRITICAL):
    the `blocked` set above is itself derived from ONE `okf._iter_docs` walk
    (`sensitivity.sensitive_concept_ids`), which can silently drop a subtree
    it cannot list (`okf.py`'s documented `_walk_errors` case) -- a doc
    invisible to that walk is never in `blocked`, yet is still directly
    readable by path here (a known path needs only the directory's x-bit,
    not r-bit, to open). Unless `include_confidential` is `True`, THIS
    function therefore also re-checks each doc's OWN freshly re-read
    frontmatter via `sensitivity.should_block` -- walk-independent
    defense-in-depth at the actual `llm.chat` send point, never dependent on
    whether the walk happened to see this doc at all. `include_confidential`
    mirrors `answer()`'s own flag exactly (skips this re-check entirely,
    restoring byte-identical opt-in behavior), so a caller that passes
    neither `blocked` nor `include_confidential` still gets today's
    unfiltered behavior for every OTHER concern, but is NEVER silently
    exposed to a confidential doc the walk missed.

    Directory-walk-observability follow-up (correction batch, post-4R-review
    readability FIX 1): this re-check now calls the centralized
    `sensitivity.should_block(metadata, include_confidential=...)` predicate
    instead of inlining `not include_confidential and
    sensitivity.blocks_llm_send(...)` directly -- behavior-preserving; the
    same 5-way-duplicated expression is now a single source of truth shared
    by every send-time re-check in the codebase (see `sensitivity.py`'s
    module docstring).

    `local_exemption` (issue #240) is the second escape hatch defined by
    `sensitivity.should_block`: the caller asserting that the `llm.chat`
    backend this run will actually reach is verifiably this machine, so a
    `confidential` concept is not leaving anywhere and the gate has nothing
    to protect. It is threaded, never re-derived -- the disjunction with
    `include_confidential` lives ONLY in `sensitivity.py` (see its module
    docstring). Defaults to `False`: a caller that cannot prove locality
    gets today's blanket blocking, so forgetting the parameter can only ever
    be MORE restrictive.

    The exemption must be honored HERE as well as at the hit-seam filter,
    not only there: this is the layer that actually assembles the prompt, so
    a re-check that ignored it would drop every concept the upstream filter
    had just admitted and make the exemption cosmetic."""
    context_blocks: list[str] = []
    citations: list[Citation] = []
    for concept_id in concept_ids:
        if concept_id in blocked:
            continue
        try:
            text = okf.concept_path_for(concept_id, bundle_dir).read_text(
                encoding="utf-8"
            )
        except (OSError, UnicodeDecodeError):
            continue
        try:
            metadata, body = okf.load_frontmatter(text)
        except Exception:  # noqa: S112 -- broad: any parse failure skips this hit (D2)
            continue
        if sensitivity.should_block(
            metadata,
            include_confidential=include_confidential,
            local_exemption=local_exemption,
        ):
            continue
        title = str(metadata.get("title") or "") or concept_id
        # Issue #570: an `Insight` is a FILED SYNTHESIS -- model output over
        # the bundle's state at some earlier answer time, not knowledge
        # extracted from an immutable Source. The synthesizer must know
        # which of its context legs stand on model output, or a synthesis
        # can compound on syntheses indistinguishably from compounding on
        # sources. Identity by frontmatter type, matching what `query`'s
        # citation marker derives from the link dir.
        synthesis_note = (
            " (filed synthesis: model output over an earlier bundle state, "
            "not a source-backed concept)"
            if metadata.get("type") == _INSIGHT_TYPE
            else ""
        )
        context_blocks.append(
            f"[concept_id: {concept_id} — {title}{synthesis_note}]\n{body}"
        )
        citations.append(
            Citation(
                concept_id=concept_id,
                title=title,
                # Explicit top-rank value only (#569) -- disclosure, never
                # enforcement; the gates above already decided admission.
                confidential=(
                    str(metadata.get("sensitivity", "")).strip()
                    == okf.SENSITIVITY_ORDER[-1]
                ),
            )
        )
    return context_blocks, citations


_SUFFICIENCY_NONE: Final = "NONE"
"""The reply meaning "no sentence here answers the question" (#760)."""

_SUFFICIENCY_PROMPT = (
    "You judge whether a body of CONTEXT can answer a QUESTION. Quote, "
    "verbatim, the sentence or sentences from the CONTEXT that answer the "
    "QUESTION. Quote only text that appears in the CONTEXT word for word. If "
    f"no sentence in the CONTEXT answers the QUESTION, reply with exactly the "
    f"single word {_SUFFICIENCY_NONE}. Sharing a topic with the question is "
    "not enough -- a sentence must actually answer it. Do not answer from "
    "your own knowledge."
)
"""System half of the pre-synthesis sufficiency check (#760).

EVIDENCE-FIRST, not a verdict, and that is the whole finding. A yes/no
formulation ("reply SUFFICIENT or INSUFFICIENT") was measured side by side in
`evals/query_sufficiency/` and false-refused a grounded question -- the exact
cost that rejected #753's ruled distance floor. Making the model produce the
quotation first, and deriving the verdict from whether it found one, refused
0 of 10 grounded questions across 100 checks while still refusing all 10
adjacent ones. Two formulations were measured precisely because a single
negative arm cannot distinguish "this mechanism does not work" from "this
wording does not work"."""


def _context_holds_the_answer(llm: LLMBackend, user_content: str) -> tuple[bool, bool]:
    """Whether the assembled context contains an answer to the question.

    `user_content` is the SAME string synthesis will be sent, so the check
    judges exactly what would be answered from rather than a reconstruction
    that could drift.

    FAILS OPEN on a transient backend error: an error is not evidence of
    insufficiency, and refusing on one would deny answers for infrastructure
    reasons. The shipped `USED:` attribution (#753) still strips the
    citations off an ungrounded answer, so the backstop that makes this check
    optional at all is exactly what covers its failure. The three FATAL
    `OllamaError` subclasses propagate untouched (issue #209): "the server is
    down" must not become "the bundle answered your question".

    A reply is a refusal only when it IS the sentinel, modulo whitespace,
    surrounding punctuation and case -- never when it merely contains it. A
    context sentence containing the word would otherwise read as a refusal
    and silently refuse an answerable question.

    Returns `(context_holds_the_answer, degraded)`. `degraded` is `True` only
    on the fail-open path: failing open is the right call, but failing open
    SILENTLY leaves an operator whose backend has been flaky no way to learn
    that the guard they configured has not run once (#764).
    """
    try:
        reply = llm.chat(
            [
                {"role": "system", "content": _SUFFICIENCY_PROMPT},
                {"role": "user", "content": user_content},
            ]
        )
    except (
        OllamaUnavailable,
        OllamaModelNotFound,
        OllamaEmbeddingDimensionMismatch,
    ):
        raise
    except OllamaError:
        return True, True
    return reply.strip().strip("\"'`*. \t\n").upper() != _SUFFICIENCY_NONE, False


def _user_content(context_blocks: list[str], question: str) -> str:
    """The user half of the prompt: numbered context blocks + the question.

    Split out of `_build_messages` so the sufficiency check (#760) and
    synthesis are sent the IDENTICAL string. A check judging a
    reconstruction of the context rather than the context itself would be
    answering a different question, and the two would drift the moment
    either assembly changed."""
    # The system prompt has always called these "the numbered CONTEXT
    # concepts"; until #753 nothing numbered them. The number is the
    # vocabulary the model attributes with, and it exists precisely so that
    # attribution never needs a concept id -- #193's leak was the model
    # copying back the `[concept_id: ...]` label it was shown, so making ids
    # the attribution vocabulary would re-open it by design.
    numbered = [
        f"[{position}] {block}" for position, block in enumerate(context_blocks, 1)
    ]
    return "CONTEXT:\n\n" + "\n\n".join(numbered) + f"\n\nQUESTION:\n{question}"


def _build_messages(user_content: str) -> list[Message]:
    """The 2-message synthesis prompt (D5): system grounding + `user_content`."""
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _classify_no_match(
    question: str, hits: list[fts.FtsHit], vec_hits: list[VecHit]
) -> NoMatchCause:
    """Classify why a no-match happened: an empty/whitespace-only `question`
    always wins (checked before hits, so it never collapses into
    `"zero_hits"`), then a genuine zero-hit search from BOTH retrievers,
    else at least one retriever had hits but every fused concept was
    unreadable/unparseable at re-read time."""
    if not question.split():
        return "empty_query"
    if not hits and not vec_hits:
        return "zero_hits"
    return "all_unreadable"


_QUESTION_WORD_RE = re.compile(r"[^\W\d_]+")
"""Letter runs of the question, for `_fts_query_terms` -- the same
letters-only family as the language voters' tokenizer: digits and
underscores carry no language and are not the terms causing #648."""


def _fts_query_terms(question: str) -> str:
    """The FTS query for `question`: content words only (#648).

    On a bundle spanning unrelated domains, the QUESTION's function words
    are what pull the wrong domain in through the lexical channel --
    `¿qué es MCP y para qué sirve?` ranked the Spanish MVP concept FIRST
    for an English-domain question, because `qué`/`es`/`para` match every
    Spanish document (FTS5 ships no Spanish stopword list). Dropping the
    ES/EN function words (`LANGUAGE_FUNCTION_WORDS`, the #618 voter's own
    vocabulary) measured strictly better on `evals/retrieval_stability/`:
    cross-domain intrusions halved in the failing family, the
    `¿qué es el Model Context Protocol?` variant became exactly the
    canonical set, and canonical retention stayed 8/8. The pool-size
    hypothesis the issue also named was measured and FALSIFIED (floors
    10/20/30 produced identical citation sets).

    Fail-open: a question made only of function words keeps its raw text
    -- an empty FTS query would silently disable the lexical channel. The
    DENSE channel always embeds the full question; meaning lives there,
    while FTS is bag-of-words and only ever saw the noise."""
    tokens = _QUESTION_WORD_RE.findall(question)
    kept = [token for token in tokens if token.lower() not in LANGUAGE_FUNCTION_WORDS]
    return " ".join(kept) if kept else question


def _fts_search(
    fts_index: fts.FtsSearchHandle | None, question: str, *, limit: int
) -> tuple[list[fts.FtsHit], list[str]]:
    """Run the FTS retrieval sub-phase over an ALREADY-OPEN, read-only
    `fts_index` handle (opened by the caller against the persisted on-disk
    FTS store; no per-call build).

    Degrades to `([], [])` whenever `fts_index` is absent (`None`) -- the
    caller has ALREADY resolved an unopenable/corrupt on-disk store to
    `None` before calling `answer()` (query-answer: FTS Retrieval Reads A
    Persisted, Read-Only Index Handle And Degrades To Empty). When present,
    `fts_index.search` is called directly and never wrapped in a try/except
    here -- any exception it raises (outside its own documented
    never-raises contract) propagates unswallowed; the degrade boundary
    applies ONLY at the caller's store-open call site, never inside this
    function (exception-vs-degrade boundary).
    """
    if fts_index is None:
        return [], []
    return fts_index.search(question, limit), list(fts_index.skipped)


def _dense_search(
    question: str,
    *,
    embedder: Embedder | None,
    vector_store: VectorStore | None,
    pool_limit: int,
) -> tuple[list[VecHit], bool]:
    """Run the dense retrieval sub-phase: embed `question` and query
    `vector_store` for up to `pool_limit` nearest hits.

    Degrades to `([], True)` -- FTS-only fusion, `dense_degraded=True` --
    whenever dense retrieval cannot proceed this call: `embedder` or
    `vector_store` absent (e.g. a cold store the CLI passes as `None`), a
    `VecUnavailable`/read-path `sqlite3.Error` raised by `vector_store.query`,
    OR a generic (non-fatal) `OllamaError` raised while embedding the
    question (`embedder.embed([question])`) -- reindex-embedding-resilience:
    a flaky embedding path degrades the QUESTION embed the same way a flaky
    vector read already degrades, rather than aborting the whole `query`
    call (D4). The three FATAL `OllamaError` subclasses, `OllamaUnavailable`
    (down server), `OllamaModelNotFound` (missing model), and
    `OllamaEmbeddingDimensionMismatch` (the configured embedding model does
    not emit `EMBED_DIM`-dimensional vectors), are explicitly EXCLUDED from
    this degrade and re-raised instead -- they propagate to `query`'s fatal
    exit-1 ladder, mirroring the same fatal/transient split already applied
    on the reindex side (review correction, CRITICAL finding: the first two
    subclasses were previously swallowed by the broad `OllamaError` catch
    below). `OllamaEmbeddingDimensionMismatch` joins them because a wrong
    dimension is a PERMANENT, non-healing misconfiguration -- no retry and
    no re-run can fix it, so dense retrieval is structurally impossible
    rather than merely unhelpful this call, unlike a generic transient
    `OllamaError` whose next attempt may well succeed; degrading it silently
    handed the user an FTS-only answer at exit 0 with no indication that
    semantic retrieval could never have run (issue #209). Never raises for a
    transient generic `OllamaError`; only ever called with a
    non-empty/whitespace `question`.
    """
    if embedder is None or vector_store is None:
        return [], True
    try:
        embedding = embedder.embed([question])[0]
        return vector_store.query(embedding, k=pool_limit), False
    except (
        OllamaUnavailable,
        OllamaModelNotFound,
        OllamaEmbeddingDimensionMismatch,
    ):
        raise
    except (VecUnavailable, sqlite3.Error, OllamaError):
        return [], True


def answer(
    question: str,
    *,
    bundle_dir: Path,
    llm: LLMBackend,
    embedder: Embedder | None = None,
    vector_store: VectorStore | None = None,
    fts_index: fts.FtsSearchHandle | None = None,
    limit: int = 5,
    include_deprecated: bool = False,
    include_confidential: bool = False,
    local_exemption: bool = False,
    sufficiency_check: bool = False,
) -> AnswerResult:
    """Answer `question` from `bundle_dir` using `llm`, citing the concepts used.

    See module docstring for the retrieve/fuse/assemble/answer flow.

    An empty/whitespace-only `question` short-circuits BEFORE any
    retrieval -- `fts_index.search`, `embedder.embed`, and
    `vector_store.query` are never called (follow-up #1: provable via spies
    on all three). Otherwise, both retrievers are queried with
    `pool_limit = pool.pool_limit(limit)`. Unless `include_deprecated`
    is `True`, the shared `lifecycle.deprecated_concept_ids(bundle_dir)`
    predicate is computed ONCE and both `hits` and `vec_hits` are filtered
    against it via `lifecycle.filter_hits` BEFORE the fuse
    (status-aware-retrieval, Phase 2). `fusion.fuse(hits, vec_hits)`, sliced
    to `limit`, is the FINAL ranking -- nothing is layered on top of it
    (issue #434) -- so every `AnswerResult` count is a POST-filter count.
    `include_deprecated=True` skips the predicate walk entirely (zero added
    cost) and restores today's status-blind behavior byte-for-byte.

    `answer` NEVER computes or compares a bundle manifest hash (D2 binding
    contract) -- neither this module nor any of its imports references
    `state/derived.py` at all; a properly-`reindex`ed handle is always
    treated as fresh here.

    sensitivity-fail-closed-filter (S3a): unless `include_confidential` is
    `True`, the shared `sensitivity.sensitive_concept_ids(bundle_dir)`
    predicate is likewise computed ONCE and unioned with the `deprecated` set
    before filtering `hits`/`vec_hits` -- a confidential concept
    is excluded from both retrieval channels exactly like a deprecated one,
    and `include_confidential=True` independently skips its own predicate
    walk at zero added cost. Correction batch (post-4R-review, FIX 2): that
    predicate is a SINGLE `okf._iter_docs` walk and can silently miss a doc
    under a subtree it cannot list, so `_assemble_context` ALSO
    independently re-checks each doc's own freshly re-read frontmatter at
    the actual send point -- see its own docstring for the walk-independent
    defense-in-depth this adds.

    `local_exemption` (issue #240) is the second escape hatch defined by
    `sensitivity.should_block`: the caller asserting that the `llm.chat`
    backend this run will actually reach is verifiably this machine, so a
    `confidential` concept is not leaving anywhere and the gate has nothing
    to protect. It is threaded, never re-derived -- the disjunction with
    `include_confidential` lives ONLY in `sensitivity.py` (see its module
    docstring). Defaults to `False`: a caller that cannot prove locality
    gets today's blanket blocking, so forgetting the parameter can only ever
    be MORE restrictive.
    """
    if not question.split():
        return AnswerResult(
            answer=NO_MATCH,
            citations=[],
            fts_hit_count=0,
            llm_invoked=False,
            no_match_cause="empty_query",
            skip_notices=[],
        )

    limit_pool = pool.pool_limit(limit)

    # #648: the lexical channel searches content words only; the dense
    # channel keeps the full question (see `_fts_query_terms`).
    hits, skip_notices = _fts_search(
        fts_index, _fts_query_terms(question), limit=limit_pool
    )

    vec_hits, dense_degraded = _dense_search(
        question, embedder=embedder, vector_store=vector_store, pool_limit=limit_pool
    )

    # status-aware-retrieval (Phase 2) + sensitivity-fail-closed-filter (S3a):
    # compute each shared predicate ONCE, only when its own filtering is
    # actually needed. The zero-cost escape path (design R1) is unchanged,
    # but the two axes now express it differently: `include_deprecated=True`
    # still skips its walk with a guard HERE, while the sensitivity walk's
    # own two hatches (`include_confidential`, and #240's `local_exemption`)
    # are passed IN and short-circuit inside `sensitive_concept_ids` before
    # it touches `okf._iter_docs`. That asymmetry is deliberate: the
    # sensitivity condition is a two-term disjunction reached from five
    # seams, and restating it at each of them is exactly the duplication
    # `sensitivity.py`'s module docstring exists to prevent -- a guard
    # half-updated at one seam fails OPEN. The two excluded-id sets are
    # unioned before filtering, so `lifecycle.filter_hits` (axis-agnostic)
    # is still called exactly once per hit list.
    deprecated: frozenset[str] = frozenset()
    if not include_deprecated:
        deprecated = lifecycle.deprecated_concept_ids(bundle_dir)
    confidential = sensitivity.sensitive_concept_ids(
        bundle_dir,
        include_confidential=include_confidential,
        local_exemption=local_exemption,
    )
    excluded = deprecated | confidential
    hits = lifecycle.filter_hits(hits, excluded)
    vec_hits = lifecycle.filter_hits(vec_hits, excluded)

    # ONE fuse, of exactly the two retriever lists, sliced to `limit` (issue
    # #434). There used to be a second fuse here that topped this ranking up
    # with a seeded-PageRank concept in a reserved tail slot; measurement
    # showed the slot cost a real hit and bought centrality, not relevance.
    fused_ids = fusion.fuse(hits, vec_hits)[: max(limit, 0)]
    context_blocks, citations = _assemble_context(
        bundle_dir,
        fused_ids,
        confidential,
        include_confidential=include_confidential,
        local_exemption=local_exemption,
    )

    if not context_blocks:
        return AnswerResult(
            answer=NO_MATCH,
            citations=[],
            fts_hit_count=len(hits),
            llm_invoked=False,
            no_match_cause=_classify_no_match(question, hits, vec_hits),
            skip_notices=skip_notices,
            dense_hit_count=len(vec_hits),
            fused_count=len(fused_ids),
            dense_degraded=dense_degraded,
        )

    user_content = _user_content(context_blocks, question)

    # #760: judge whether this context can answer at all, BEFORE paying for
    # synthesis. #753 ruled "refuse below a relevance floor" and #760 measured
    # that no such floor exists on any retrieval signal -- `fusion.fuse` is
    # RRF and encodes position only, and embedding proximity reports topical
    # relatedness, which is the defect's own premise. The judgement is about
    # entailment, so nothing in retrieval can make it.
    #
    # Defaults OFF here and ON in the workspace config: every existing caller,
    # the eval harnesses included, keeps byte-identical behavior and pays no
    # added latency unless it opts in.
    sufficiency_degraded = False
    if sufficiency_check:
        holds, sufficiency_degraded = _context_holds_the_answer(llm, user_content)
    else:
        holds = True
    if not holds:
        return AnswerResult(
            answer=NO_MATCH,
            citations=[],
            fts_hit_count=len(hits),
            llm_invoked=False,
            no_match_cause="insufficient_context",
            skip_notices=skip_notices,
            dense_hit_count=len(vec_hits),
            fused_count=len(fused_ids),
            dense_degraded=dense_degraded,
            sufficiency_degraded=sufficiency_degraded,
            context_block_count=len(context_blocks),
        )

    reply = llm.chat(_build_messages(user_content))

    # #753: the citation list is decided HERE, after the model has spoken,
    # from the blocks it reports drawing on. Until this it was
    # `_assemble_context`'s output verbatim -- computed before `llm.chat` ran
    # and never compared to the reply, which made "citations" the retrieval
    # set under another name. Measured over the 170 stored answers in
    # `evals/query_title/results/`: 170 of 170 cited exactly `limit`, in only
    # four distinct sets, and not one answer had every citation supported by
    # its own text.
    #
    # `citations` and `context_blocks` are index-aligned by construction --
    # `_assemble_context` appends to both in the same iteration, after every
    # skip guard -- so a block number indexes its citation directly.
    prose, reported, attribution = _split_attribution(reply, len(context_blocks))
    if reported is not None:
        # A SUBSET, never a reordering: `citations` is documented as being in
        # fused-rank order and the CLI renders the list in it, so honouring
        # the model's own listing order here would quietly re-rank the output
        # by whatever sequence it happened to type.
        citations = [
            citation
            for position, citation in enumerate(citations, 1)
            if position in reported
        ]
    return AnswerResult(
        # Stripped HERE, not at the print site, because `AnswerResult.answer`
        # feeds both -- `query` echoes it and `query --save` files it as a
        # new concept. Cleaning only the echo would leave the leak permanent
        # in the bundle while looking fixed on screen (#193).
        answer=_strip_concept_id_scaffolding(prose),
        citations=citations,
        fts_hit_count=len(hits),
        llm_invoked=True,
        no_match_cause="none",
        skip_notices=skip_notices,
        dense_hit_count=len(vec_hits),
        fused_count=len(fused_ids),
        dense_degraded=dense_degraded,
        sufficiency_degraded=sufficiency_degraded,
        context_block_count=len(context_blocks),
        attribution=attribution,
    )
