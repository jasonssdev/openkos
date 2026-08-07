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
    user_content = (
        f"SOURCE TEXT:\n{source_text}\n\n"
        f"CANDIDATES:\n{candidate_lines}"
    )
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


def select(
    source_text: str,
    candidates: "list[JudgeCandidate] | tuple[JudgeCandidate, ...]",
    llm: LLMBackend,
) -> tuple[str, ...] | None:
    """Ask `llm` which of `candidates` are genuine, distinct subjects.

    Returns the titles it keeps, echoed verbatim from the closed candidate
    list, in reply order. `None` means unusable -- `llm.chat` raised any
    exception, the reply was not valid JSON, or the parsed shape failed
    `_validate_selection` -- and the caller must fail closed to the whole
    candidate set (design D7). Never raises.

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
    return _validate_selection(parsed)
