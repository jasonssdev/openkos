"""Whole-source prompts are bounded to fit the model's context window (#866).

The measured defect: on a 54K-char chunked Spanish transcript the judge
prompt (system rules + FULL source + 24 candidate lines) reached 16,091
real tokens against the shipped `num_ctx` 12,288. Ollama does not raise on
an oversized prompt -- llama.cpp keeps 4 head tokens plus the LAST half of
the window (`truncating input prompt` limit=6146 prompt=16091 keep=4), so
the SYSTEM PROMPT and the first ~60% of the source were silently cut. The
model received a decapitated transcript with no instructions, answered in
helpful prose, and both identical retry attempts failed `unparseable:
no-json` -- deterministically, on every chunked source of that size.

Three seams send the whole source in one prompt while extraction itself
fans out over windows: the judge, the #584 re-ask, and the #668
participant capture. Each is bounded the same way: when the assembled
prompt cannot fit the planning window, the SOURCE portion is replaced by
an even-coverage excerpt built from the existing `_chunk_lines` windows --
instructions and candidate list always survive intact, and the excerpt
keeps the first and last windows whenever at least two fit.

Grounding checks (`_strip_ungrounded_expansions`) keep reading the FULL
source: the excerpt exists for the model's eyes only, and a fact grounded
in an elided window must not be stripped for it.
"""

from collections.abc import Sequence

from openkos.extraction import concept as concept_mod
from openkos.extraction import judge as judge_mod
from openkos.llm.base import Message


class _FakeLLM:
    """Records every `chat` call, returns a fixed reply, no window attr."""

    def __init__(self, reply: str = "[]") -> None:
        self.reply = reply
        self.calls: list[list[Message]] = []

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls.append(list(messages))
        return self.reply


class _WindowedLLM(_FakeLLM):
    """A fake that, like `OllamaClient`, advertises its context window."""

    def __init__(self, reply: str = "[]", context_window: int | None = None) -> None:
        super().__init__(reply)
        self.context_window = context_window


class _SequencedLLM:
    """Replies differ per call; records every call."""

    def __init__(self, replies: Sequence[str]) -> None:
        self.replies = list(replies)
        self.calls: list[list[Message]] = []

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls.append(list(messages))
        return self.replies[len(self.calls) - 1]


def _keep_reply(*titles: str) -> str:
    quoted = ", ".join(f'"{t}"' for t in titles)
    return f'{{"keep": [{quoted}]}}'


def _lined_source(lines: int, *, label: str = "note") -> str:
    """Deterministic multi-line text; every line is unique and findable."""
    return "\n".join(f"{label} {i:04d} " + "x" * 30 for i in range(lines))


def _default_budget_chars(*, overhead_chars: int, reserve_tokens: int) -> int:
    """The char budget the module's own constants imply at the packaged
    default window -- recomputed here so a silent change to the formula or
    to either constant fails a test instead of shipping unnoticed."""
    planning = concept_mod._PROMPT_PLANNING_CONTEXT_WINDOW
    ratio = concept_mod._PROMPT_TOKENS_PER_CHAR
    return int((planning - reserve_tokens) / ratio) - overhead_chars


# --- The bounding helper ----------------------------------------------------


def test_source_under_budget_passes_through_byte_identical() -> None:
    """A fitting source is returned untouched with the flag down -- the
    working class must stay byte-identical to what shipped."""
    source = _lined_source(40)

    bounded, excerpted = concept_mod._bounded_prompt_source(
        source,
        overhead_chars=2_000,
        llm=_FakeLLM(),
        generation_reserve_tokens=concept_mod._JUDGE_REPLY_RESERVE_TOKENS,
    )

    assert bounded == source
    assert excerpted is False


def test_overhead_consumes_the_budget() -> None:
    """A source that fits the raw window budget but NOT the budget minus
    the prompt's own overhead is excerpted: the system prompt and the
    candidate list spend the same window the source does."""
    reserve = concept_mod._JUDGE_REPLY_RESERVE_TOKENS
    raw_budget = _default_budget_chars(overhead_chars=0, reserve_tokens=reserve)
    lines = raw_budget // 41  # each _lined_source line is 40 chars + newline
    source = _lined_source(lines)
    assert len(source) <= raw_budget

    _, without_overhead = concept_mod._bounded_prompt_source(
        source, overhead_chars=0, llm=_FakeLLM(), generation_reserve_tokens=reserve
    )
    _, with_overhead = concept_mod._bounded_prompt_source(
        source, overhead_chars=8_000, llm=_FakeLLM(), generation_reserve_tokens=reserve
    )

    assert without_overhead is False
    assert with_overhead is True


def test_excerpt_fits_the_budget_and_keeps_head_and_tail() -> None:
    """An oversized source comes back within budget, elision is MARKED, and
    the first and last windows survive -- even coverage, not a head cut and
    not the tail-only view the server-side truncation produced."""
    reserve = concept_mod._OPTIONAL_CALL_RESERVE_TOKENS
    overhead = 1_000
    budget = _default_budget_chars(overhead_chars=overhead, reserve_tokens=reserve)
    source = _lined_source(1_200)  # ~49K chars, comfortably over any budget
    assert len(source) > budget

    bounded, excerpted = concept_mod._bounded_prompt_source(
        source,
        overhead_chars=overhead,
        llm=_FakeLLM(),
        generation_reserve_tokens=reserve,
    )

    assert excerpted is True
    assert len(bounded) <= budget
    assert concept_mod._SOURCE_ELISION_MARKER.strip() in bounded
    assert source.split("\n")[0] in bounded
    assert source.split("\n")[-1] in bounded


def test_planning_window_mirrors_the_packaged_default() -> None:
    """`_PROMPT_PLANNING_CONTEXT_WINDOW` is `config.DEFAULT_CONTEXT_WINDOW`
    mirrored as a literal (the extraction module is a config-free leaf, so
    it cannot import the real one). Tests CAN import both, and this pin is
    what keeps the mirror honest: a change to the packaged default that
    forgets the mirror fails here instead of silently desyncing the budget
    every window-less backend plans against."""
    from openkos import config

    assert concept_mod._PROMPT_PLANNING_CONTEXT_WINDOW == config.DEFAULT_CONTEXT_WINDOW


def test_a_three_window_source_excerpts_to_first_and_last() -> None:
    """The smallest source the window-selection loop can act on (three
    windows, budget for two): the excerpt is first window + elision marker
    + last window -- never the head-only truncation fallback, which is
    reserved for sources where not even two windows fit."""
    source = _lined_source(270)  # ~11K chars: three ~4K windows
    windows = concept_mod._chunk_lines(source)
    assert len(windows) == 3
    reserve = concept_mod._JUDGE_REPLY_RESERVE_TOKENS
    raw_budget = _default_budget_chars(overhead_chars=0, reserve_tokens=reserve)
    # Overhead sized so the first and last windows fit and all three do not.
    overhead = raw_budget - (len(windows[0]) + len(windows[2]) + 200)
    first_line = windows[0].split("\n")[0]
    middle_line = windows[1].split("\n")[5]
    last_line = windows[2].split("\n")[-1]

    bounded, excerpted = concept_mod._bounded_prompt_source(
        source,
        overhead_chars=overhead,
        llm=_FakeLLM(),
        generation_reserve_tokens=reserve,
    )

    assert excerpted is True
    assert first_line in bounded
    assert last_line in bounded
    assert middle_line not in bounded
    assert concept_mod._SOURCE_ELISION_MARKER.strip() in bounded


def test_excerpt_is_deterministic() -> None:
    """Two calls over the same input agree byte-for-byte -- the retry
    resends the identical prompt, and a drifting excerpt would silently
    turn the retry into an unmeasured A/B."""
    source = _lined_source(1_200)

    first = concept_mod._bounded_prompt_source(
        source,
        overhead_chars=1_000,
        llm=_FakeLLM(),
        generation_reserve_tokens=concept_mod._OPTIONAL_CALL_RESERVE_TOKENS,
    )
    second = concept_mod._bounded_prompt_source(
        source,
        overhead_chars=1_000,
        llm=_FakeLLM(),
        generation_reserve_tokens=concept_mod._OPTIONAL_CALL_RESERVE_TOKENS,
    )

    assert first == second


def test_a_clients_narrower_context_window_narrows_the_budget() -> None:
    """A backend advertising a smaller pinned window shrinks the budget:
    the bound follows the window that will actually truncate, not the
    packaged default."""
    reserve = concept_mod._JUDGE_REPLY_RESERVE_TOKENS
    source = _lined_source(200)  # ~8.2K chars: fits the default budget

    _, under_default = concept_mod._bounded_prompt_source(
        source, overhead_chars=0, llm=_FakeLLM(), generation_reserve_tokens=reserve
    )
    _, under_narrow = concept_mod._bounded_prompt_source(
        source,
        overhead_chars=0,
        llm=_WindowedLLM(context_window=3_000),
        generation_reserve_tokens=reserve,
    )

    assert under_default is False
    assert under_narrow is True


def test_an_unpinned_window_plans_at_the_packaged_default() -> None:
    """`context_window=None` (an unpinned workspace) plans at the packaged
    default rather than trusting an unknowable model-side limit: a
    conservative excerpt is strictly safer than a decapitated prompt."""
    reserve = concept_mod._JUDGE_REPLY_RESERVE_TOKENS
    source = _lined_source(200)

    _, excerpted = concept_mod._bounded_prompt_source(
        source,
        overhead_chars=0,
        llm=_WindowedLLM(context_window=None),
        generation_reserve_tokens=reserve,
    )

    assert excerpted is False


def test_a_single_oversized_window_is_truncated_not_sent_whole() -> None:
    """One giant line (its own oversized `_chunk_lines` window) cannot be
    excerpted by window selection; it is hard-truncated instead of being
    sent whole to be decapitated server-side."""
    reserve = concept_mod._OPTIONAL_CALL_RESERVE_TOKENS
    budget = _default_budget_chars(overhead_chars=0, reserve_tokens=reserve)
    source = "y" * (budget + 10_000)

    bounded, excerpted = concept_mod._bounded_prompt_source(
        source, overhead_chars=0, llm=_FakeLLM(), generation_reserve_tokens=reserve
    )

    assert excerpted is True
    assert len(bounded) <= budget


def test_a_budget_swallowed_by_overhead_sends_no_source() -> None:
    """When the prompt's own overhead exceeds the whole window budget (a
    small pinned window under many long candidate lines), the source
    portion is EMPTY rather than floored: any floor above the budget would
    re-create the very overflow the bound exists to close, and an
    instructed call with no source evidence is strictly safer than a
    decapitated one."""
    reserve = concept_mod._JUDGE_REPLY_RESERVE_TOKENS
    source = _lined_source(200)

    bounded, excerpted = concept_mod._bounded_prompt_source(
        source,
        overhead_chars=20_000,
        llm=_WindowedLLM(context_window=3_000),
        generation_reserve_tokens=reserve,
    )

    assert excerpted is True
    assert bounded == ""


# --- The judge seam ---------------------------------------------------------


def _window_replies(source: str, item: str, *extra: str) -> list[str]:
    """One extraction reply per fan-out window, then `extra` in order."""
    windows = len(concept_mod._chunk_lines(source))
    return [item] * windows + list(extra)


_CONCEPT_ITEM = (
    '{"type": "Concept", "title": "Stoicism", '
    '"description": "A school of philosophy.", "body": ""}'
)
_ENTITY_ITEM = (
    '{"type": "Entity", "title": "Zettelkasten App", '
    '"description": "A note-taking tool.", "body": ""}'
)


def _array(*items: str) -> str:
    return "[" + ", ".join(items) + "]"


def test_union_judge_prompt_is_bounded_on_an_oversized_source() -> None:
    """On a chunked source too large for the window, the judge call keeps
    its system prompt and full candidate list but reads an excerpt: the
    total prompt stays within the planning budget, and the report names
    the bounded call."""
    source = _lined_source(900)  # ~37K chars: chunked, over the judge budget
    llm = _SequencedLLM(
        _window_replies(
            source,
            _array(_CONCEPT_ITEM, _ENTITY_ITEM),
            _keep_reply("Stoicism", "Zettelkasten App"),
        )
    )

    outcome = concept_mod.extract_concept_union(source, source_title="Notes", llm=llm)

    judge_call = llm.calls[-1]
    assert judge_call[0]["content"] == judge_mod._JUDGE_SYSTEM_PROMPT
    assert "Stoicism" in judge_call[1]["content"]
    assert "Zettelkasten App" in judge_call[1]["content"]
    prompt_chars = sum(len(m["content"]) for m in judge_call)
    planning = concept_mod._PROMPT_PLANNING_CONTEXT_WINDOW
    reserve = concept_mod._JUDGE_REPLY_RESERVE_TOKENS
    assert prompt_chars <= int(
        (planning - reserve) / concept_mod._PROMPT_TOKENS_PER_CHAR
    )
    assert concept_mod._SOURCE_ELISION_MARKER.strip() in judge_call[1]["content"]
    assert outcome.report.judge_status == "ok"
    assert outcome.report.bounded_prompt_calls == (concept_mod.BOUNDED_CALL_JUDGE,)
    # The fan-out windows themselves are untouched: every extraction call
    # still received its full window, and joining them restores the source.
    window_texts = [call[1]["content"] for call in llm.calls[:-1]]
    for window in concept_mod._chunk_lines(source):
        assert any(window in text for text in window_texts)


def test_union_judge_prompt_is_untouched_when_it_fits() -> None:
    """A fitting judge prompt ships byte-identical to before this change:
    the full source in the user turn, no elision marker, nothing named in
    `bounded_prompt_calls`."""
    run = _array(_CONCEPT_ITEM, _ENTITY_ITEM)
    llm = _SequencedLLM([run, run, _keep_reply("Stoicism")])

    outcome = concept_mod.extract_concept_union(
        "Short notes.", source_title="Notes", llm=llm
    )

    judge_call = llm.calls[-1]
    assert "Short notes." in judge_call[1]["content"]
    assert concept_mod._SOURCE_ELISION_MARKER.strip() not in judge_call[1]["content"]
    assert outcome.report.bounded_prompt_calls == ()


# --- The optional-call seams ------------------------------------------------


def _elided_ground_phrase(source: str, *, overhead_chars: int) -> str:
    """A grounding phrase present in the SOURCE but absent from the excerpt
    the optional-call bound will send -- located from the helper's own
    deterministic output, so the test never guesses which windows elide."""
    bounded, excerpted = concept_mod._bounded_prompt_source(
        source,
        overhead_chars=overhead_chars,
        llm=_FakeLLM(),
        generation_reserve_tokens=concept_mod._OPTIONAL_CALL_RESERVE_TOKENS,
    )
    assert excerpted is True
    for i in range(0, 1_200, 7):
        phrase = f"Ground Phrase {i:04d}"
        if phrase in source and phrase not in bounded:
            return phrase
    raise AssertionError("no elided grounding phrase found")


def _grounded_source(lines: int) -> str:
    """A long source seeding one distinct grounding phrase per line."""
    return "\n".join(
        f"prose {i:04d} about Ground Phrase {i:04d} " + "x" * 20 for i in range(lines)
    )


def test_reask_prompt_is_bounded_but_grounding_reads_the_full_source() -> None:
    """The re-ask's chat prompt reads the excerpt, while
    `_strip_ungrounded_expansions` keeps reading the FULL source: an
    expansion grounded only in an ELIDED window survives."""
    source = _grounded_source(1_200)
    kept = concept_mod.ExtractionResult(
        type="Concept", title="Base Subject", description="d", body="b"
    )
    overhead = sum(
        len(m["content"])
        for m in concept_mod._build_reask_messages("", "Notes", kept.title)
    )
    phrase = _elided_ground_phrase(source, overhead_chars=overhead)
    addition = (
        f'{{"type": "Concept", "title": "GP ({phrase})", '
        f'"description": "d", "body": ""}}'
    )
    llm = _FakeLLM(reply=_array(addition))

    outcome = concept_mod._reask_for_further_subjects(source, "Notes", kept, llm)

    prompt = llm.calls[0][1]["content"]
    assert phrase not in prompt
    assert concept_mod._SOURCE_ELISION_MARKER.strip() in prompt
    assert outcome.prompt_bounded is True
    assert [r.title for r in outcome.additions] == [f"GP ({phrase})"]


def test_capture_prompt_is_bounded_on_an_oversized_source() -> None:
    """The participant-capture call reads the same even-coverage excerpt,
    keeps its instructions, and reports the bound on its outcome."""
    source = _grounded_source(1_200)
    person = (
        '{"type": "Person", "title": "Ana Gomez", '
        '"description": "Chaired the meeting.", "body": ""}'
    )
    llm = _FakeLLM(reply=_array(person))

    outcome = concept_mod._capture_further_participants(source, "Sync", llm)

    system = llm.calls[0][0]["content"]
    prompt = llm.calls[0][1]["content"]
    assert system == concept_mod._PARTICIPANT_CAPTURE_SYSTEM_PROMPT
    assert concept_mod._SOURCE_ELISION_MARKER.strip() in prompt
    assert outcome.prompt_bounded is True
    assert [r.title for r in outcome.additions] == ["Ana Gomez"]


def test_optional_call_prompts_are_untouched_when_they_fit() -> None:
    """Fitting optional-call prompts ship the full source, flag down."""
    kept = concept_mod.ExtractionResult(
        type="Concept", title="Base Subject", description="d", body="b"
    )
    llm = _FakeLLM(reply="[]")

    reask = concept_mod._reask_for_further_subjects("Short.", "Notes", kept, llm)
    capture = concept_mod._capture_further_participants("Short.", "Sync", llm)

    assert reask.prompt_bounded is False
    assert capture.prompt_bounded is False
    for call in llm.calls:
        assert "Short." in call[1]["content"]
        assert concept_mod._SOURCE_ELISION_MARKER.strip() not in call[1]["content"]


# --- Report propagation -----------------------------------------------------


def test_union_report_names_every_bounded_call_in_spend_order() -> None:
    """A meeting-shaped oversized source whose merged set has one candidate
    fires all three whole-source calls; the report names each bounded call
    in the order the pipeline spent it: re-ask, participant capture, judge."""
    lines = []
    for i in range(1_400):
        speaker = "Ana" if i % 2 == 0 else "Luis"
        lines.append(f"{speaker}: turn {i:04d} " + "x" * 25)
    source = "\n".join(lines)  # transcript-shaped, chunked, oversized

    single = (
        '{"type": "Concept", "title": "Sole Subject", '
        '"description": "The one recovered subject.", "body": ""}'
    )
    reask_addition = (
        '{"type": "Decision", "title": "Adopt The Plan", '
        '"description": "A decision the re-ask recovered.", "body": ""}'
    )
    person = (
        '{"type": "Person", "title": "Ana Gomez", '
        '"description": "Chaired the meeting.", "body": ""}'
    )
    windows = len(concept_mod._chunk_lines(source))
    llm = _SequencedLLM(
        [_array(single)] * windows
        + [
            _array(reask_addition),
            _array(person),
            _keep_reply("Sole Subject", "Adopt The Plan", "Ana Gomez"),
        ]
    )

    outcome = concept_mod.extract_concept_union(
        source, source_title="weekly sync meeting", llm=llm
    )

    assert outcome.report.bounded_prompt_calls == (
        concept_mod.OPTIONAL_CALL_REASK,
        concept_mod.OPTIONAL_CALL_PARTICIPANT_CAPTURE,
        concept_mod.BOUNDED_CALL_JUDGE,
    )


def test_single_run_report_names_a_bounded_reask() -> None:
    """`extract_concept` (the non-union sibling) reports its re-ask bound
    through the same field -- the two entry points must not drift."""
    source = _lined_source(900)
    single = (
        '{"type": "Concept", "title": "Sole Subject", '
        '"description": "The one recovered subject.", "body": ""}'
    )
    windows = len(concept_mod._chunk_lines(source))
    llm = _SequencedLLM([_array(single)] * windows + ["[]"])

    outcome = concept_mod.extract_concept(source, source_title="Notes", llm=llm)

    assert outcome.report.bounded_prompt_calls == (concept_mod.OPTIONAL_CALL_REASK,)


class _ThrowingWindowLLM(_FakeLLM):
    """A backend whose `context_window` property raises -- the one seam of
    `_bounded_prompt_source` that can throw on an exotic backend."""

    @property
    def context_window(self) -> int:
        raise RuntimeError("backend property exploded")


def test_a_throwing_window_property_still_bounds_at_the_default() -> None:
    """The bound never raises AND never silently skips: a backend whose
    `context_window` property throws is treated exactly like one that
    advertises no window -- the source is still excerpted against the
    packaged-default planning window. Neither failure mode survives: the
    union pipeline is not aborted by a prompt-planning helper, and the
    model is not handed the full oversized prompt the bound exists to
    prevent."""
    source = _lined_source(1_200)

    throwing = concept_mod._bounded_prompt_source(
        source,
        overhead_chars=1_000,
        llm=_ThrowingWindowLLM(),
        generation_reserve_tokens=concept_mod._JUDGE_REPLY_RESERVE_TOKENS,
    )
    windowless = concept_mod._bounded_prompt_source(
        source,
        overhead_chars=1_000,
        llm=_FakeLLM(),
        generation_reserve_tokens=concept_mod._JUDGE_REPLY_RESERVE_TOKENS,
    )

    assert throwing == windowless
    assert throwing[1] is True


def test_a_bounded_judge_that_still_fails_is_reported_as_both() -> None:
    """The disclosure and the failure are independent facts: a judge call
    that read an excerpt and STILL failed to parse reports the bound in
    `bounded_prompt_calls` AND the degrade in `judge_status`/causes -- the
    exact class #866 exists to diagnose, and the combination an operator
    needs to tell "the bound was not enough" from "the prompt overflowed"."""
    source = _lined_source(900)
    prose = "Aqui tienes una estructura de conocimiento del documento."
    llm = _SequencedLLM(
        _window_replies(source, _array(_CONCEPT_ITEM, _ENTITY_ITEM), prose, prose)
    )

    outcome = concept_mod.extract_concept_union(source, source_title="Notes", llm=llm)

    assert outcome.report.judge_status == "failed"
    assert outcome.report.bounded_prompt_calls == (concept_mod.BOUNDED_CALL_JUDGE,)
    assert outcome.report.judge_failure_causes == (
        "unparseable: no-json",
        "unparseable: no-json",
    )
    # Degrade contract unchanged: the full ceiling-truncated set is kept.
    assert {r.title for r in outcome.objects} == {"Stoicism", "Zettelkasten App"}
