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

import json
import re
from dataclasses import dataclass
from typing import Any, Final

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


def prompt_overhead_chars(
    candidates: "list[JudgeCandidate] | tuple[JudgeCandidate, ...]",
) -> int:
    """Chars the judge prompt spends on everything EXCEPT the source text
    (#866): the system rules, the rendered candidate lines, and the fixed
    scaffold labels.

    Public so `extract_concept_union` can budget the source excerpt it
    passes to `select` against the model's context window without this
    module's prompt encoding leaking out of it: the overhead is DEFINED as
    "the prompt built over an empty source", so a change to the system
    prompt or the candidate-line encoding moves this number automatically
    and the two can never drift."""
    return sum(len(m["content"]) for m in _build_judge_messages("", candidates))


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


JUDGE_ATTEMPTS = 2
"""How many times `select` asks before declaring the judge unavailable
(issue #754).

TWO, not a loop. The failure #754 reports costs more than the selection it
loses: with no judge, the caller keeps the whole merged union, and the caller
then has nothing ranked to apply a positional cap to. One extra round trip is
cheap against that.

WHAT THIS DOES *NOT* CLAIM. #754 attributed the failure to a cold model start,
and `evals/judge_cold_start/` -- added alongside this constant -- did not
reproduce it: 45 calls at two candidate counts, cold and warm, 15 confirmed
model evictions, zero failures. #644 filed the same symptom, hypothesised the
same cause, and was falsified the same way. So the retry is NOT justified as
"the model was not resident and the first attempt loads it". The cause is
unidentified, and this is a cause-agnostic remedy: re-asking is worth one call
against ANY non-deterministic failure, and a sampling model that answers in
prose one call and clean JSON the next is the shape #644 actually measured.

Bounded at two on purpose, and the bound has a cost worth naming: `select`
inherits the workspace `chat_timeout`, so against a backend that HANGS rather
than refuses, two attempts wait up to twice that deadline before the judge is
declared unavailable -- once per source in a batch. That is accepted rather
than mitigated. Backing off would add delay to the common case to shrink a
worst case that is already pathological (the two extraction calls ahead of
this one would have had to succeed against the same hanging backend first),
and `OllamaClient.embed`'s backoff exists for a different reason: an embedding
batch competes with itself, while this is one call."""


JUDGE_FAILURE_CHAT_ERROR: Final = "chat_error"
JUDGE_FAILURE_UNPARSEABLE: Final = "unparseable"
JUDGE_FAILURE_WRONG_SHAPE: Final = "wrong_shape"
"""The three causes an attempt can fail for, named after the branch that
produces each (#795).

They are NOT new names. `evals/judge_cold_start/` had to grow its own copy
of this parse chain to say which stage said no, and measured 45 judge calls
under exactly these strings. Production adopting them keeps every stored
result comparable and lets that harness delete the copy it was pinning
against drift.

Why the distinction is load-bearing, in #795's own words: "timeout, parse
failure, and backend refusal need different fixes and are currently
indistinguishable." A single `None` for all three is what made a 2-of-3
failure rate undiagnosable.
"""

_UNPARSEABLE_NO_JSON: Final = "no-json"
_UNPARSEABLE_JSON_NOT_OBJECT: Final = "json-not-object"
"""Which way `extract_json_object` said no: nothing parsed at all, or
something parsed and was not an object. Prose instead of a reply is a
different problem from a reply of the wrong kind."""


@dataclass(frozen=True)
class JudgeOutcome:
    """What one `select` call did, cause included.

    `selected` carries exactly what `select` used to return -- the kept
    titles, or `None` when no attempt produced a usable reply -- so the
    fail-closed contract (design D7) is unchanged and callers still degrade
    on `None`.

    `failures` names one cause per FAILED attempt, in attempt order. It is
    non-empty on a success too, when an earlier attempt failed and the retry
    recovered: that run's judge did fail once, and #795 exists because a
    retry that hides its own failures makes the rate invisible from a run's
    output. A caller reporting only on `selected is None` would still
    under-report by exactly the recovered cases.
    """

    selected: tuple[str, ...] | None
    failures: tuple[str, ...] = ()


def _unparseable_cause(reply: str) -> str:
    """Which way the reply failed `extract_json_object`."""
    try:
        json.loads(reply.strip())
    except (json.JSONDecodeError, ValueError):
        return f"{JUDGE_FAILURE_UNPARSEABLE}: {_UNPARSEABLE_NO_JSON}"
    return f"{JUDGE_FAILURE_UNPARSEABLE}: {_UNPARSEABLE_JSON_NOT_OBJECT}"


def _select_once(
    source_text: str,
    candidates: "list[JudgeCandidate] | tuple[JudgeCandidate, ...]",
    llm: LLMBackend,
) -> "tuple[tuple[str, ...], None] | tuple[None, str]":
    """One judge attempt: chat, parse, validate, salvage.

    Returns `(selected, cause)` -- EXACTLY ONE is set, and the return type
    says so rather than a comment claiming it. A plain
    `tuple[... | None, str | None]` let the caller reach a state the
    function cannot produce, and the `cause or <default>` guard that state
    needed would have mislabelled a real failure as a wrong-shape reply if
    the invariant ever broke -- quietly defeating the per-cause diagnostics
    this change exists to add. Under this type mypy narrows `cause` to `str`
    once `selected` is `None`, so no fallback is needed and none is written.

    The cause is returned rather than logged so the decision about what to
    do with it stays with the caller, and so this leaf keeps taking and
    returning plain values.
    """
    try:
        reply = llm.chat(_build_judge_messages(source_text, candidates))
    except Exception as exc:  # broad: design D7 -- the judge's failure must
        # never destroy already-validated extraction work. Every exception
        # `llm.chat` can raise -- the `OllamaError` family or anything else
        # a backend implementation might throw -- degrades here, in this ONE
        # named place, rather than propagating or being caught piecemeal at
        # each call site.
        #
        # The TYPE is carried out (#795) and the message is not: a type
        # separates a timeout from a refusal, which is the distinction that
        # changes what an operator does, while a message can carry a host,
        # a path, or a model's own text into a line this repo also writes
        # to a Source's frontmatter.
        return None, f"{JUDGE_FAILURE_CHAT_ERROR}: {type(exc).__name__}"

    return classify_reply(reply, candidates)


def classify_reply(
    reply: str,
    candidates: "list[JudgeCandidate] | tuple[JudgeCandidate, ...]",
) -> "tuple[tuple[str, ...], None] | tuple[None, str]":
    """`(selected, cause)` for a reply that arrived without raising.

    Exactly one of the two is set, spelled in the type. Split out of `_select_once` (#795) so
    the parse chain and the names for its failure stages live in ONE place:
    `evals/judge_cold_start/` previously kept its own copy of this ordering
    purely to report WHICH stage said no, and pinned in its self-test that
    the copy still agreed with production. That copy existed only because
    production discarded the answer; now it does not, so the harness calls
    this and there is nothing left to drift.
    """
    parsed = parsing.extract_json_object(reply)
    if parsed is None:
        return None, _unparseable_cause(reply)
    validated = _validate_selection(parsed)
    if validated is None:
        return None, JUDGE_FAILURE_WRONG_SHAPE
    return _salvage_full_line_echoes(validated, candidates), None


def select(
    source_text: str,
    candidates: "list[JudgeCandidate] | tuple[JudgeCandidate, ...]",
    llm: LLMBackend,
) -> JudgeOutcome:
    """Ask `llm` which of `candidates` are genuine, distinct subjects.

    `JudgeOutcome.selected` carries the titles it keeps, echoed verbatim
    from the closed candidate list, in reply order -- a kept string that
    instead echoes a whole candidate line is first resolved back to its bare
    candidate title (`_salvage_full_line_echoes`, #644). `None` means
    unusable -- `llm.chat` raised any exception, the reply was not valid
    JSON, or the parsed shape failed `_validate_selection` -- and the caller
    must fail closed to the whole candidate set (design D7). Never raises.

    `JudgeOutcome.failures` names WHY each failed attempt failed (#795).
    The retry still covers all three causes without distinguishing them --
    a sampling model producing prose one call and clean JSON the next is as
    transient as a dropped connection, so retrying is right for all of them.
    What changed is that the causes are no longer discarded on the way out:
    they were the only evidence that could tell a timeout from a refusal
    from a bad reply, and the caller has to be able to report them.

    Asks up to `JUDGE_ATTEMPTS` times (#754), re-sending the IDENTICAL
    prompt. A first attempt that succeeds costs exactly what it always did --
    the retry is reached only after a failure.

    Deliberate bound (#457): the reply is TITLE-ONLY, so it cannot
    disambiguate two candidates of different types sharing one normalized
    title -- the caller admits every same-titled candidate when the title
    is selected, damage bounded by its backstop cap. The reply-protocol
    change that could tell them apart is tracked in #457.
    """
    failures: list[str] = []
    for _attempt in range(JUDGE_ATTEMPTS):
        # Indexed rather than unpacked: mypy narrows the
        # `tuple[selection, None] | tuple[None, cause]` union on `[0] is not
        # None`, but loses it across a destructuring assignment -- and the
        # narrowing is the whole reason the union is spelled that way.
        attempt = _select_once(source_text, candidates, llm)
        if attempt[0] is not None:
            return JudgeOutcome(selected=attempt[0], failures=tuple(failures))
        failures.append(attempt[1])
    return JudgeOutcome(selected=None, failures=tuple(failures))
