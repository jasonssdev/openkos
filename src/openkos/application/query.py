"""The query bounded-context application service (ADR-0018).

Composes the read-path orchestration around `retrieval.answer()` --
existence-gated store opening with degrade-to-`None` handling, and the
`answer()` call itself -- into a synchronous callable any adapter can use
without importing `openkos.cli`. `llm` and `embedder` are constructor
parameters built by the caller (D1): this module never binds a concrete
backend, so it stays usable by an MVP 3 `api`/`mcp` adapter as well as the
CLI. Exceptions from `answer()` propagate unwrapped (D2) -- ordering,
rendering and exit-code selection stay the calling adapter's job.

`--save` filing composition (Slice 2, issue #918, design D3/D4) lives here
too: `stage_filed_answer` computes a `FiledAnswerPlan` from an `AnswerResult`
and its citations WITHOUT writing -- the write mechanics
(`_reject_drifted_targets`, `_autocommit`, `_refresh_derived_after_write`)
stay in the CLI adapter and are called through, never forked or duplicated
here. Interactive confirmation, TTY detection, stdout/stderr rendering and
exit-code selection also stay the adapter's job (D4) -- this module never
calls `typer.confirm`/`isatty`/`input`, and never imports `openkos.cli`.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from openkos import config
from openkos.bundle import index as bundle_index
from openkos.bundle import source_titles
from openkos.llm.base import Embedder, LLMBackend
from openkos.model import okf
from openkos.model.types import (
    BUILDABLE_TYPES,
    INSIGHT_TYPE,
    TYPE_TO_LINK_DIR,
    TYPE_TO_SECTION,
)
from openkos.resolution import insight_identity
from openkos.retrieval.answer import AnswerResult, Citation, answer
from openkos.state import fts, question_vectors
from openkos.state.vectorstore import VectorStoreDB, VecUnavailable, open_vector_store


@dataclass(frozen=True)
class QueryOutcome:
    """One `run_query` call's typed result.

    `result` is `answer()`'s own return value, unmodified. `vector_store_unavailable`
    and `fts_unavailable` report whether THIS call detected the corresponding
    derived store as unavailable (absent, or unopenable/corrupt) -- distinct
    from `result.dense_degraded`, which `answer()` sets for a read-path
    failure at query time rather than at store-open time."""

    result: AnswerResult
    vector_store_unavailable: bool
    fts_unavailable: bool


def _open_vector_store_or_degrade(
    path: Path,
) -> tuple[AbstractContextManager[VectorStoreDB | None], bool]:
    """Existence-gated store open for the read-only dense seam.

    `run_query` never CREATES `vectors.db` -- `open_vector_store` (which
    lazily creates `.openkos/vectors.db` on a successful open) is only
    called when `path` already exists on disk. Returns a context manager
    yielding either an open `VectorStoreDB` or `None`, plus whether this
    call detected the store as unavailable (absent, `VecUnavailable` at
    open, or a raw `sqlite3.Error` -- e.g. a corrupt/locked EXISTING
    `vectors.db` raising `DatabaseError`/`OperationalError` from
    `open_vector_store`'s CREATE TABLE step, which is not mapped to
    `VecUnavailable`) -- distinct from `AnswerResult.dense_degraded`, which
    is set INSIDE `answer()` for a read-path failure at query time."""
    if not path.exists():
        return nullcontext(None), True
    try:
        return open_vector_store(path), False
    except (VecUnavailable, sqlite3.Error):
        return nullcontext(None), True


def _open_fts_or_degrade(
    path: Path,
) -> tuple[AbstractContextManager[fts.FtsIndex | None], bool]:
    """Existence-gated, read-only handle open for the persisted FTS seam.

    Same INTENT and RETURN SHAPE as `_open_vector_store_or_degrade` --
    `(context_manager, bool)`, degrading to `(nullcontext(None), True)` on
    absence or failure -- but not structurally identical: this function has
    no explicit existence check of its own, because
    `fts.open_fts_index_readonly` is already existence-gated internally and
    returns `None` for an absent path on its own; and it catches only
    `sqlite3.Error`, since FTS has no typed "unavailable" exception
    analogous to `VecUnavailable`."""
    try:
        handle = fts.open_fts_index_readonly(path)
    except sqlite3.Error:
        return nullcontext(None), True
    if handle is None:
        return nullcontext(None), True
    return handle, False


def run_query(
    question: str,
    *,
    layout: config.WorkspaceLayout,
    cfg: config.Config,
    llm: LLMBackend,
    embedder: Embedder,
    limit: int,
    include_deprecated: bool,
    include_confidential: bool,
    local_exemption: bool,
) -> QueryOutcome:
    """Compose store opening (degrade-to-`None`) and the `answer()` call for
    one query.

    Raises:
        OllamaUnavailable: the configured Ollama server is unreachable.
        OllamaModelNotFound: the configured chat or embedding model is not
            pulled.
        OllamaEmbeddingDimensionMismatch: the configured embedding model
            does not emit `EMBED_DIM`-dimensional vectors -- a permanent
            misconfiguration, not a transient failure.
        FtsUnavailable: sqlite's `fts5` module is not compiled in.
        OllamaError: any other, generic backend failure.

    The three `OllamaError` subclasses above (`OllamaUnavailable`,
    `OllamaModelNotFound`, `OllamaEmbeddingDimensionMismatch`) MUST be
    handled before a catch-all `except (FtsUnavailable, OllamaError)` --
    reordering silently swallows them into the generic branch and loses
    their actionable, cause-specific remediation (D2, ADR-0018). This
    function itself never catches any of them; it propagates whatever
    `answer()` raises unwrapped.
    """
    vector_store_cm, vector_store_unavailable = _open_vector_store_or_degrade(
        layout.vectors_db_path
    )
    fts_index_cm, fts_unavailable = _open_fts_or_degrade(layout.fts_db_path)
    with vector_store_cm as vector_store, fts_index_cm as fts_index:
        result = answer(
            question,
            bundle_dir=layout.bundle_dir,
            llm=llm,
            embedder=embedder,
            vector_store=vector_store,
            fts_index=fts_index,
            limit=limit,
            include_deprecated=include_deprecated,
            include_confidential=include_confidential,
            local_exemption=local_exemption,
            # The ONE place that injects `cfg.sufficiency_check` explicitly
            # (#760), so the product-ON default lives in the config and
            # `answer` itself stays OFF for library callers.
            sufficiency_check=cfg.sufficiency_check,
        )
    return QueryOutcome(
        result=result,
        vector_store_unavailable=vector_store_unavailable,
        fts_unavailable=fts_unavailable,
    )


@dataclass(frozen=True)
class FiledAnswerPlan:
    """One validated `query --save` filing staged for Phase B write --
    mirrors `_DerivedPlan`'s shape (design: "`_stage_filed_answer` helper
    (not inline)"). Computed by `stage_filed_answer` WITHOUT writing; the
    calling adapter commits it through the shared write mechanics
    (`_reject_drifted_targets`, `fsio.write_exclusive`, `_autocommit`,
    `_refresh_derived_after_write`), which this module never owns or
    duplicates (D3)."""

    link_dir: str
    section: str
    slug: str
    title: str
    description: str
    path: Path
    content: str
    sensitivity: str
    type_floor_raised: bool = False
    """`True` when this filing's resolved `sensitivity` is strictly above
    the cited-concept high-water-mark because of the per-type offset
    mapping (issue #669, design D3) -- `resolved != cited_high_water_mark`,
    the `--save`-site mirror of `_DerivedPlan.type_floor_raised`. `False`
    on the common path (no offset configured for this type, or the
    citation high-water-mark already at or above the floor-plus-offset)."""


_DECLARATIVE_TITLE_MAX_CHARS = 90
"""Longest first sentence `_declarative_answer_title` will promote to a
title (issue #570). Above this a sentence is prose, not a name, and the
slug -- the permanent Concept ID -- would be a paragraph."""

_DECLARATIVE_TITLE_MIN_CHARS = 15
"""Shortest first sentence worth promoting: below this the sentence is
usually a fragment ("Yes.", "It depends.") that names nothing."""

_SYNTHESIS_SHARE_WARN_THRESHOLD = 0.5
"""Share of an answer's citations that are filed syntheses (`insights/`)
at which `query` warns (issue #649). The all-or-nothing predecessor fired
only at 1.0, a threshold a drifting base approaches without crossing;
half-or-more means the answer stands as much on model output as on
sources. Below it the `[synthesis]` markers still disclose each leg."""


def _declarative_answer_title(answer_text: str) -> str | None:
    """Derive a DECLARATIVE title from `answer_text`'s first sentence, or
    `None` when no usable one exists (issue #570).

    `query --save` used to title the filed document with the QUESTION
    verbatim, so the slug -- the permanent OKF Concept ID -- was an
    interrogative sentence (`qué-relación-hay-entre-...`). The answer's
    opening sentence is the declarative statement of the same content, so
    it is the natural title; the question survives as the default
    DESCRIPTION, where prose belongs.

    Deterministic and conservative: take the text up to the first `.` that
    ends a word (or the whole first line), collapse whitespace, and refuse
    (`None`) when the result is shorter than `_DECLARATIVE_TITLE_MIN_CHARS`,
    longer than `_DECLARATIVE_TITLE_MAX_CHARS`, or itself a question. The
    caller falls back to the question, exactly the pre-#570 behavior --
    this helper only ever improves the default, and `--title` still
    overrides everything."""
    first_line = answer_text.strip().split("\n", 1)[0]
    sentence = first_line.split(". ", 1)[0].removesuffix(".")
    candidate = " ".join(sentence.split())
    if not (
        _DECLARATIVE_TITLE_MIN_CHARS <= len(candidate) <= _DECLARATIVE_TITLE_MAX_CHARS
    ):
        return None
    if candidate.endswith("?") or candidate.startswith(("¿", "#", "-", "*", ">")):
        return None
    return candidate


_QUESTION_SUBJECT_PREFIXES: Final = (
    "qué es ",
    "qué son ",
    "cuál es ",
    "cuáles son ",
    "qué significa ",
    "para qué sirve ",
    "para qué sirven ",
    "cómo funciona ",
    "cómo funcionan ",
    "what is ",
    "what are ",
)
"""Definitional interrogative scaffolds `_question_subject` strips (#646).
Deliberately narrow: only shapes where the remainder IS the subject. An
open question (`¿qué decidimos ...?`) has no extractable subject and must
fall through to the question-verbatim safety net, never be guessed at."""

_QUESTION_SUBJECT_TRAILING_RE: Final = re.compile(
    r"\s+(?:y|e|and)\s+(?:para qué|cómo|cuál|qué|what|how|why)\b.*$",
    re.IGNORECASE,
)
"""A chained second interrogative clause (`... y para qué sirve`) is
scaffolding, not subject; a plain conjunction between nouns (`entrada y
salida`) never matches because the next word is not an interrogative."""

_QUESTION_SUBJECT_ARTICLES: Final = (
    "el ",
    "la ",
    "los ",
    "las ",
    "un ",
    "una ",
    "the ",
    "a ",
    "an ",
)


def _question_subject(question: str) -> str | None:
    """Extract the SUBJECT of a definitional question, or `None` (#646).

    `¿qué es el Model Context Protocol?` names `Model Context Protocol`;
    filing the question verbatim makes the slug -- the permanent Concept ID
    -- an interrogative sentence, and makes two insights about the same
    subject look unrelated whenever the questions were phrased differently.
    This is the middle rung of the title ladder: it runs only when
    `_declarative_answer_title` refused (the answer's first sentence was
    unusable -- in production, long Spanish openings routinely exceed the
    declarative ceiling), and falls through to the question verbatim when
    the question's shape is not recognizably definitional.

    Deterministic: normalize whitespace, strip interrogative punctuation,
    match a known scaffold prefix case-insensitively, cut a chained
    interrogative clause, strip one leading article, and capitalize the
    first letter. Refuses a residue shorter than 2 characters, longer than
    `_DECLARATIVE_TITLE_MAX_CHARS`, or with no letters at all."""
    text = " ".join(question.split()).strip("¿?¡!. ")
    lowered = text.lower()
    for prefix in _QUESTION_SUBJECT_PREFIXES:
        if lowered.startswith(prefix):
            subject = text[len(prefix) :]
            break
    else:
        return None
    subject = _QUESTION_SUBJECT_TRAILING_RE.sub("", subject).strip()
    lowered_subject = subject.lower()
    for article in _QUESTION_SUBJECT_ARTICLES:
        if lowered_subject.startswith(article):
            subject = subject[len(article) :].strip()
            break
    if not (2 <= len(subject) <= _DECLARATIVE_TITLE_MAX_CHARS):
        return None
    if not any(char.isalpha() for char in subject):
        return None
    return subject[0].upper() + subject[1:]


_CLAUSE_CONNECTORS: Final = (
    " porque ",
    " ya que ",
    " puesto que ",
    " debido a ",
    " radica en ",
    " consiste en ",
    " se refiere a ",
    " se basa en ",
    " permite ",
    " sirve para ",
    " es que ",
    " es la ",
    " es el ",
    " es una ",
    " es un ",
    " son las ",
    " son los ",
)
"""Spanish clause boundaries `_clause_answer_title` may cut at, alongside a
plain comma. Closed and deliberately small: a shape nobody listed simply
does not cut, and an uncut sentence falls through to the question verbatim
-- the same safety net as today, never a wrong title.

EVERY entry is space-delimited on both sides, and that is what makes each
one match a clause boundary rather than a substring inside a word: without
the trailing space ` es un ` would fire inside `es una`, and without the
leading one ` permite ` would fire inside `impermite`-shaped tokens. The
spaces are the word boundaries, spelled literally.

`_clause_answer_title` searches these through `_CLAUSE_CONNECTOR_RE` and
keeps `match.start() > 0`. A match AT index 0 is unreachable anyway -- the
candidate has been through `" ".join(...split())`, so it never begins with
a space -- and were one to occur it would cut an empty residue, which fails
the minimum-length bound and falls through safely. An entry added without
its spaces would therefore go silently inert rather than loudly wrong, so
`test_clause_connectors_are_space_delimited` pins the invariant instead of
leaving it to a reader to reconstruct."""

_CLAUSE_CONNECTOR_RE: Final = re.compile(
    "|".join(re.escape(connector) for connector in _CLAUSE_CONNECTORS),
    re.IGNORECASE,
)
"""`_CLAUSE_CONNECTORS` as one leftmost-wins, case-insensitive alternation.

Exists so the cut index is measured against the SAME string it slices.
Matching case-insensitively here rather than searching a `candidate.lower()`
copy is a correctness requirement, not a style choice: `str.lower()` is not
length-preserving (`"İ"` becomes two codepoints), so indices taken from a
lower-cased copy drift right against the original."""


def _clause_answer_title(answer_text: str) -> str | None:
    """Derive a title from the first CLAUSE of an over-long declarative
    opening, or `None` (issue #696).

    #696 reports that filed insights are still named after the question, and
    proposes reordering the ladder so the subject rung runs first. Measured
    (`evals/query_title/`), that proposal is a no-op on its own two evidence
    questions: `_question_subject` returns `None` for both, so promoting it
    promotes a refusal. What actually happens is that BOTH existing rungs
    refuse -- `_declarative_answer_title` because a real Spanish opening runs
    past `_DECLARATIVE_TITLE_MAX_CHARS` (the evidence sentence measures 158),
    and `_question_subject` because `¿por qué es importante X?` is not one of
    the eleven definitional scaffolds #646 narrowed itself to.

    So this rung attacks the ceiling, which is the binding constraint
    `_question_subject`'s own docstring already named in prose. It cuts the
    first sentence at its earliest comma or clause connector and applies rung
    1's own bounds to the residue.

    TWO GUARDS, both load-bearing:

    It refuses anything rung 1 could have promoted. The over-ceiling check is
    what makes this additive: every answer resolving at rung 1 today reaches
    here only to be refused, so the filings whose titles are already right
    keep them byte for byte. Without it, a 50-character sentence would be cut
    at its copula and the ~30 e2e filings pinned on
    `stoicism-teaches-the-dichotomy-of-control` would silently change name.

    It re-checks the question/markdown shapes rung 1 rejects. Length is not
    why rung 1 refuses those, so a length-only gate would admit a long
    question as a title -- exactly the interrogative Concept ID #696 exists
    to remove.

    Ordering is MEASURED, not assumed: this rung sits BELOW `_question_subject`
    (see `stage_filed_answer`). Placed above it, `¿qué es la trazabilidad?`
    over a long opening cut to `La trazabilidad`, article and all, where the
    subject rung gives the cleaner `Trazabilidad`. A clause cut is a degraded
    declarative -- it is what you reach for when the sentence overran AND the
    question named no subject."""
    first_line = answer_text.strip().split("\n", 1)[0]
    sentence = first_line.split(". ", 1)[0].removesuffix(".")
    candidate = " ".join(sentence.split())
    if len(candidate) <= _DECLARATIVE_TITLE_MAX_CHARS:
        return None
    if candidate.endswith("?") or candidate.startswith(("¿", "#", "-", "*", ">")):
        return None
    cuts = [candidate.index(",")] if "," in candidate else []
    # Searched case-insensitively against `candidate` ITSELF, never against a
    # lower-cased copy. `str.lower()` is not length-preserving -- `"İ"` lowers
    # to two codepoints -- so an index taken from `candidate.lower()` and used
    # to slice `candidate` drifts right by one per such character, cutting
    # mid-word once more than one appears. Two review lenses found this
    # independently. A regex alternation also returns the LEFTMOST match, so
    # it replaces the previous per-connector `min` scan outright.
    match = _CLAUSE_CONNECTOR_RE.search(candidate)
    if match is not None and match.start() > 0:
        cuts.append(match.start())
    if not cuts:
        return None
    residue = candidate[: min(cuts)].strip().rstrip(",;:")
    if not (
        _DECLARATIVE_TITLE_MIN_CHARS <= len(residue) <= _DECLARATIVE_TITLE_MAX_CHARS
    ):
        return None
    if not any(char.isalpha() for char in residue):
        return None
    return residue


def stage_filed_answer(
    *,
    question: str,
    answer_text: str,
    citations: list[Citation],
    bundle_dir: Path,
    default_sensitivity: str,
    timestamp: str,
    title: str | None = None,
    description: str | None = None,
    doc_type: str = INSIGHT_TYPE,
    cfg: config.Config,
) -> FiledAnswerPlan:
    """Stage a `query --save` filing of `answer_text` as a new derived OKF
    concept -- a pure, in-memory Phase A step mirroring
    `_stage_derived_objects`'s staging shape: every refusal below raises
    `ValueError`, caught once at the CLI call site; nothing is written
    here -- Phase B (in the CLI adapter) does the actual `mkdir` +
    `write_exclusive` (D3).

    Refuses when `citations` is empty (design: "Refuse `--save` when zero
    citations") -- `okf.build_concept` requires non-empty provenance, and a
    sourceless "derived" concept is not a real derived node. `title`/
    `description` default to `question` when not overridden; `doc_type`
    defaults to `"Insight"`. `doc_type` MUST be a member of the classifiable
    vocabulary, else `ValueError` (same gate `okf.build_concept` enforces,
    checked here first so the bundle subdirectory can be resolved safely).
    `slug = source_titles.slugify(title)`; an empty slug, or a slug that
    collides with an existing file at the target path, both refuse (design:
    "Slug collision handling (mirror ingest)").

    Sensitivity is the high-water-mark (`okf.combine_sensitivity`) folded
    over each cited concept's RE-READ frontmatter, seeded at
    `default_sensitivity`; an unreadable OR unparseable cited concept folds
    the running floor to `"confidential"` -- the most-restrictive level,
    NOT skipped (fail-closed: "cannot verify sensitivity -> confidential",
    the same stance as `okf._rank` / `sensitivity.blocks_llm_send`).
    Skipping would under-classify: a cited concept surfaced under
    `--include-confidential` that becomes unreadable at save time could
    otherwise leave a filed answer -- which may have synthesized
    confidential content -- classified below `confidential`, a future-leak
    vector.

    `cfg` is REQUIRED (#685 item 2; issue #669, design D3): the
    cited-concept high-water-mark computed above is a FLOOR, not the final
    value -- `config.type_birth_sensitivity(cfg, doc_type,
    cited_high_water_mark)` may raise it further, never lower it, per
    `doc_type`'s configured offset (`query-command` spec: "Sensitivity Is
    The High-Water-Mark Of Cited Concepts"). The former `cfg=None` default
    silently skipped that security raise for any caller that omitted the
    parameter; an empty `type_sensitivity_defaults` mapping is the
    supported opt-out and leaves the high-water-mark alone.
    """
    if not citations:
        raise ValueError(
            "nothing to file -- the answer cited no concepts; --save records "
            "provenance from citations"
        )
    if doc_type not in BUILDABLE_TYPES:
        raise ValueError(
            f"type must be one of {sorted(BUILDABLE_TYPES)}, got {doc_type!r}"
        )

    # Issue #570: the default title is DECLARATIVE, derived from the
    # answer's first sentence -- the slug is the permanent Concept ID, and
    # an interrogative sentence is not an identity. The question keeps its
    # place as the default description. #646 added the middle rung: when
    # the first sentence is unusable (long Spanish openings routinely
    # exceed the declarative ceiling in production), a definitional
    # question's SUBJECT titles the filing; only an unrecognizable
    # question still falls back to the question verbatim.
    resolved_title = (
        (
            _declarative_answer_title(answer_text)
            or _question_subject(question)
            or _clause_answer_title(answer_text)
            or question
        )
        if title is None
        else title
    )
    # Neutralize markdown link LABEL delimiters before the title reaches the
    # `index.md`/`log.md` bullets (the answer's first sentence or a `--title`
    # can carry `[`/`]`); the slug is derived independently and is unaffected.
    resolved_title = bundle_index.sanitize_link_label(resolved_title)
    resolved_description = question if description is None else description

    slug = source_titles.slugify(resolved_title)
    if not slug:
        raise ValueError(
            f"cannot derive a filename from title {resolved_title!r}; pass --title"
        )

    link_dir = TYPE_TO_LINK_DIR[doc_type]
    section = TYPE_TO_SECTION[doc_type]
    path = bundle_dir / link_dir / f"{slug}.md"
    if path.exists():
        raise ValueError(
            f"a concept already exists at bundle/{link_dir}/{slug}.md; use "
            "--title to file under a different name, or forget the existing one"
        )

    cited_high_water_mark = default_sensitivity
    for citation in citations:
        try:
            # `okf.concept_path_for`, not `bundle_dir / f"{id}.md"` (#473):
            # citation ids come out of `okf.concept_id_for` and are NFC, while
            # the name on disk may be decomposed on a byte-exact filesystem.
            # A direct read of the NFC spelling misses a file that exists,
            # falls into the fail-closed `except` below, and folds a READABLE
            # citation's sensitivity to `confidential` -- fail-closed is for
            # documents that cannot be verified, not for a spelling mismatch
            # the rest of the pipeline already tolerates.
            text = okf.concept_path_for(citation.concept_id, bundle_dir).read_text(
                encoding="utf-8"
            )
            metadata, _ = okf.load_frontmatter(text)
        except Exception:  # broad: any read/parse failure
            # fails CLOSED to "confidential" (cannot verify -> most
            # restrictive), mirroring `_assemble_context`'s broad
            # `except Exception` in retrieval/answer.py.
            cited_high_water_mark = okf.combine_sensitivity(
                cited_high_water_mark, "confidential"
            )
            continue
        cited_high_water_mark = okf.combine_sensitivity(
            cited_high_water_mark, metadata.get("sensitivity")
        )

    # Per-type sensitivity default (issue #669, design D3): the offset
    # applies to the CONFIG FLOOR, never to `cited_high_water_mark` itself,
    # so a citation set already resolved above the floor-plus-offset still
    # wins via the high-water-mark inside `type_birth_sensitivity`.
    sensitivity = config.type_birth_sensitivity(cfg, doc_type, cited_high_water_mark)

    content = okf.build_concept(
        type=doc_type,
        title=resolved_title,
        description=resolved_description,
        body=answer_text,
        provenance=[citation.concept_id for citation in citations],
        sensitivity=sensitivity,
        timestamp=timestamp,
        related_note="concept cited to produce this answer",
    )

    return FiledAnswerPlan(
        link_dir=link_dir,
        section=section,
        slug=slug,
        title=resolved_title,
        description=resolved_description,
        path=path,
        content=content,
        sensitivity=sensitivity,
        type_floor_raised=(sensitivity != cited_high_water_mark),
    )


def grounding_unverified(result: AnswerResult) -> bool:
    """Whether `result`'s citations are UNVERIFIED provenance rather than
    something the model itself accounted for (issue #774, design D4).

    `True` exactly when the LLM ran (`llm_invoked`) and its own attribution
    line never confirmed the citation list (`attribution != "reported"`):
    under the fallback (`"absent"`/`"unparsed"`) every retrieved concept is
    cited regardless of whether the answer actually drew on it, so filing
    those citations as provenance records what retrieval FOUND, not what
    the model USED. A short-circuit result (`llm_invoked=False`) has no
    grounding claim to verify at all and is never unverified by this
    predicate -- `attribution` defaults to `"absent"` on every such result,
    which would otherwise misreport it. The CLI adapter gates the #774
    stronger confirmation (or `--allow-unattributed` refusal) on this value;
    the policy decision lives here so it is asserted once, not re-derived at
    each call site."""
    return result.llm_invoked and result.attribution != "reported"


def synthesis_share_warrants_warning(citations: list[Citation]) -> bool:
    """Whether the SHARE of `citations` that are themselves filed syntheses
    (`insights/`) meets `_SYNTHESIS_SHARE_WARN_THRESHOLD` (issue #649).

    Compounding on sources is the product's thesis; compounding on model
    output with no source underneath is how a knowledge base rots. The
    all-or-nothing predecessor guard fired only when EVERY citation was a
    synthesis, a threshold a drifting base approaches without ever crossing
    -- this predicate is proportional instead, so `query` can warn well
    before that. `False` for an empty citation list (nothing to compound
    on, and division by zero besides)."""
    if not citations:
        return False
    insight_prefix = f"{TYPE_TO_LINK_DIR[INSIGHT_TYPE]}/"
    synthesis_count = sum(
        1 for citation in citations if citation.concept_id.startswith(insight_prefix)
    )
    return synthesis_count / len(citations) >= _SYNTHESIS_SHARE_WARN_THRESHOLD


def scan_for_duplicates(
    question: str,
    *,
    layout: config.WorkspaceLayout,
    cfg: config.Config,
    embedder: Embedder,
) -> insight_identity.DuplicateScan:
    """Look up already-filed insights whose SOURCE QUESTION resembles
    `question` (issue #762, design D3) -- advisory only, NEVER refuses or
    alters a filing; the caller decides what to do with the disclosure.

    Opens the persisted question-vector cache at
    `layout.insight_questions_db_path` for the lifetime of this one call
    and closes it before returning -- the cache is what lets this compare
    the WHOLE bundle instead of a bounded recent window (#764's retired
    cap): a stored question's embedding never changes, so only NEW or
    changed filed questions cost an embed call. A cache that cannot open
    (absent parent directory aside -- `open_question_vectors` creates it
    lazily -- but a corrupt or otherwise unopenable file) degrades to
    `cache=None`, which `insight_identity.near_duplicate_insights` reports
    as `unavailable=True` rather than raising: a down cache must never
    block a save that has nothing to do with it."""
    question_cache_conn: sqlite3.Connection | None = None
    try:
        question_cache_conn = question_vectors.open_question_vectors(
            layout.insight_questions_db_path
        )
        question_cache = question_vectors.QuestionVectorStore(
            question_cache_conn, cfg.embedding_model
        )
    except Exception:  # advisory: a cache that will not open never blocks a save
        question_cache = None
    try:
        return insight_identity.near_duplicate_insights(
            question,
            bundle_dir=layout.bundle_dir,
            embedder=embedder,
            cache=question_cache,
        )
    finally:
        if question_cache_conn is not None:
            question_cache_conn.close()
