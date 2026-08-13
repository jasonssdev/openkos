"""Selector judge: given a source's text and the CLOSED, already-merged list
of candidate derived objects `extraction.concept.extract_concept_union`
proposes, ask an injected `LLMBackend` which candidates are genuine and
which are decayed/duplicate noise, then return the titles it keeps.

Config-free leaf, mirroring `retrieval/answer.py` and `extraction/concept.py`
(module docstrings): this module never imports `openkos.config`, and never
imports `extraction.concept` (design D2) -- it takes and returns plain
strings via its own `JudgeCandidate`, so the union orchestrator in
`concept.py` can call this module without either module importing the
other.

`select()` NEVER raises (design D7): any exception from its own `llm.chat`
call -- `OllamaError`-family or otherwise -- is caught in ONE named place
and degrades to `None`, because the judge is an optional refinement whose
failure must never destroy already-validated extraction work. An empty,
unparseable, or wrong-shaped reply degrades the same way. The caller
(`extract_concept_union`) is responsible for falling back to the full
candidate set, backstop-truncated, whenever `select()` returns `None`.
"""

import re
from dataclasses import dataclass
from typing import Any

from openkos.llm import parsing
from openkos.llm.base import LLMBackend, Message


@dataclass(frozen=True)
class JudgeCandidate:
    """One candidate derived object the judge is asked to keep or drop.

    Deliberately plain strings, not `extraction.concept.ExtractionResult`:
    this module is a leaf and must never import `concept.py` (design D2)."""

    type: str
    title: str
    description: str


_JUDGE_SYSTEM_PROMPT = (
    "You are a selection step in a local-first knowledge engine. Below is a "
    "SOURCE text and a CLOSED list of candidate derived objects that two "
    "extraction passes already proposed for it. Some candidates may be "
    "genuine distinct subjects the source is about; others may be "
    "decayed near-duplicates, shallow facets of a genuine subject, or "
    "over-eager enumeration.\n\n"
    "Decide which candidates are GENUINE, distinct subjects worth keeping "
    "as standalone derived objects. Prefer FEWER, RICHER objects over many "
    "shallow ones -- the same restraint the extraction step itself was "
    "asked to apply.\n\n"
    "A candidate whose title names the source document, meeting, or "
    "gathering itself -- a container or framing title for the WHOLE source "
    "rather than a subject the source discusses -- is NEVER a genuine "
    "subject. Always drop it, even when no other candidate covers its "
    "content. Concrete decisions, commitments, and named topics the source "
    "records are worth keeping AHEAD of any summary-of-the-source "
    "candidate.\n\n"
    "You MUST select ONLY from the candidate titles given below -- you MUST "
    "NOT invent, rename, or rephrase a title. Echo each kept title EXACTLY "
    "as it appears in the candidate list.\n\n"
    "Return ONLY a JSON object, with NO prose, NO markdown, and NO code "
    'fences around it, in exactly this shape: {"keep": ["<exact candidate '
    'title>", ...]}. Do NOT wrap it in an array, and do NOT return a bare '
    "array of titles."
)
"""Stable system half of the 2-message prompt: closed-list selection framing
(design: judge cannot fabricate a candidate), the same fewer-richer
restraint `concept._SYSTEM_PROMPT` carries, and the `{"keep": [...]}` reply
shape (design D3 -- an object, not a bare array, because
`parsing.extract_json_object` is the shared parser this module reuses
verbatim rather than adding new parsing code)."""


def _build_judge_messages(
    source_text: str, candidates: "list[JudgeCandidate] | tuple[JudgeCandidate, ...]"
) -> list[Message]:
    """Assemble the 2-message prompt: system selection rules + the source
    text and the numbered candidate list as the user turn."""
    candidate_lines = "\n".join(
        f"{i}. type={c.type!r} title={c.title!r} description={c.description!r}"
        for i, c in enumerate(candidates, start=1)
    )
    user_content = f"SOURCE TEXT:\n{source_text}\n\nCANDIDATES:\n{candidate_lines}"
    return [
        {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _validate_selection(data: dict[str, Any]) -> tuple[str, ...] | None:
    """Fail-closed validation of a parsed judge reply: `keep` MUST be a
    present, non-empty list of strings. Any other shape returns `None`
    (task 1.5: non-JSON handled by the caller via `extract_json_object`
    returning `None`; this function validates the shape of a parsed dict)."""
    keep = data.get("keep")
    if not isinstance(keep, list) or not keep:
        return None
    if not all(isinstance(title, str) for title in keep):
        return None
    return tuple(keep)


_ECHOED_TITLE_RE = re.compile(r"title=(['\"])(.*?)\1 description=")
"""The `title=...` span of one `_build_judge_messages` candidate line
(`{i}. type={c.type!r} title={c.title!r} description={c.description!r}`),
anchored on both sides to that exact encoding: `!r` quotes with `'` normally
and switches to `\"` when the value contains an apostrophe, so both quote
styles are one alternation. Exists ONLY for `_salvage_full_line_echoes`
(#644) -- it recognizes this module's own prompt format echoed back, never
arbitrary prose."""


def _normalize_title(value: str) -> str:
    """strip + casefold + collapsed internal whitespace -- MIRRORS
    `concept._normalize_title` byte-for-byte, because that is the
    normalization `extract_concept_union` applies to BOTH sides of its
    post-judge title matching (design D4) and this module must resolve a
    salvaged echo to a title that matching will accept. Mirrored, not
    imported: this module is a leaf that never imports `concept.py`
    (design D2)."""
    return " ".join(value.strip().casefold().split())


def _salvage_full_line_echoes(
    kept: tuple[str, ...],
    candidates: "list[JudgeCandidate] | tuple[JudgeCandidate, ...]",
) -> tuple[str, ...]:
    """Resolve kept strings that echo a WHOLE candidate line back to the
    bare candidate title (#644, measured on a cold-start probe): despite
    the prompt's echo-the-title-EXACTLY instruction, the model sometimes
    replies `{"keep": ["type='Concept' title='X' description='...'"]}` --
    a valid shape to `_validate_selection`, but a string the union's
    closed-set title matching can never match, so a genuine selection
    degraded to the full unfiltered set.

    Deterministic and fail-closed: a kept string is rewritten ONLY when it
    (a) matches no candidate title under the shared normalization, (b)
    carries this module's own candidate-line encoding, and (c) that
    encoding's extracted title normalizes to a real candidate title -- in
    which case the CANDIDATE's exact title is returned, so downstream
    matching cannot miss it. Anything else passes through byte-identical,
    exactly as before."""
    titles_by_norm = {_normalize_title(c.title): c.title for c in candidates}
    resolved: list[str] = []
    for title in kept:
        if _normalize_title(title) in titles_by_norm:
            resolved.append(title)
            continue
        match = _ECHOED_TITLE_RE.search(title)
        candidate_title = (
            titles_by_norm.get(_normalize_title(match.group(2))) if match else None
        )
        resolved.append(candidate_title if candidate_title is not None else title)
    return tuple(resolved)


def select(
    source_text: str,
    candidates: "list[JudgeCandidate] | tuple[JudgeCandidate, ...]",
    llm: LLMBackend,
) -> tuple[str, ...] | None:
    """Ask `llm` which of `candidates` are genuine, distinct subjects.

    Returns the titles it keeps, echoed verbatim from the closed candidate
    list, in reply order -- a kept string that instead echoes a whole
    candidate line is first resolved back to its bare candidate title
    (`_salvage_full_line_echoes`, #644). `None` means unusable -- `llm.chat`
    raised any exception, the reply was not valid JSON, or the parsed shape
    failed `_validate_selection` -- and the caller must fail closed to the
    whole candidate set (design D7). Never raises.

    Deliberate bound (#457): the reply is TITLE-ONLY, so it cannot
    disambiguate two candidates of different types sharing one normalized
    title -- the caller admits every same-titled candidate when the title
    is selected, damage bounded by its backstop cap. The reply-protocol
    change that could tell them apart is tracked in #457.
    """
    try:
        reply = llm.chat(_build_judge_messages(source_text, candidates))
    except Exception:  # broad: design D7 -- the judge's failure must
        # never destroy already-validated extraction work. Every exception
        # `llm.chat` can raise -- the `OllamaError` family or anything else
        # a backend implementation might throw -- degrades to `None` here,
        # in this ONE named place, rather than propagating or being caught
        # piecemeal at each call site.
        return None

    parsed = parsing.extract_json_object(reply)
    if parsed is None:
        return None
    validated = _validate_selection(parsed)
    if validated is None:
        return None
    return _salvage_full_line_echoes(validated, candidates)
