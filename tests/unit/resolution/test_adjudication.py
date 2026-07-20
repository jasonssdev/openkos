"""Unit tests for `resolution/adjudication.py`: the config-free LLM
adjudication leaf over slice-1 `find_candidates` output.

All tests use a `tmp_path` bundle and a reply-QUEUE fake `LLMBackend`
(records `.calls`, returns queued replies in call order) -- zero network,
zero real Ollama process. Mirrors `_FakeLLM` in
`tests/unit/retrieval/test_answer.py` / `tests/unit/extraction/test_concept.py`,
extended to a queue since each group needs its own reply.
"""

from collections.abc import Sequence
from pathlib import Path

import pytest

from openkos.llm.base import Message
from openkos.llm.ollama import OllamaUnavailable
from openkos.resolution import adjudication as adjudication_mod
from openkos.resolution.candidates import CandidateGroup, Tier


class _FakeLLM:
    """A structural `LLMBackend`: records every `chat` call, returns queued
    replies in call order. If `error` is set, `chat` raises it instead of
    returning (and does not consume a queued reply)."""

    def __init__(
        self, replies: Sequence[str] = (), *, error: BaseException | None = None
    ) -> None:
        self._replies = list(replies)
        self.error = error
        self.calls: list[list[Message]] = []

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls.append(list(messages))
        if self.error is not None:
            raise self.error
        return self._replies.pop(0)


def _write_doc(
    path: Path, *, doc_type: str = "Concept", title: str = "Stub", body: str = "Body."
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: {doc_type}\ntitle: {title}\n---\n{body}", encoding="utf-8"
    )


def _group(
    *member_ids: str, okf_type: str = "Person", tier: Tier = Tier.HIGH
) -> CandidateGroup:
    return CandidateGroup(
        okf_type=okf_type, member_ids=tuple(member_ids), tier=tier, trigger="stub"
    )


def _valid_reply(
    verdict: str = "same", confidence: float = 0.9, rationale: str = "match"
) -> str:
    return f'{{"verdict": "{verdict}", "confidence": {confidence}, "rationale": "{rationale}"}}'


# ---------------------------------------------------------------------------
# Requirement: Verdict / AdjudicatedCandidate shape
# ---------------------------------------------------------------------------


def test_verdict_enum_has_exactly_three_members() -> None:
    assert {member.name for member in adjudication_mod.Verdict} == {
        "SAME",
        "DIFFERENT",
        "UNCERTAIN",
    }


def test_adjudicated_candidate_exposes_candidate_verdict_confidence_rationale(
    tmp_path: Path,
) -> None:
    _write_doc(tmp_path / "a.md", title="Ada Lovelace")
    _write_doc(tmp_path / "b.md", title="Ada L.")
    group = _group("a", "b")
    llm = _FakeLLM(
        replies=[_valid_reply("same", 0.92, "Identical entity, different casing")]
    )

    result = adjudication_mod.adjudicate_candidates(
        [group], bundle_dir=tmp_path, llm=llm
    )

    assert len(result) == 1
    adjudicated = result[0]
    assert adjudicated.candidate == group
    assert adjudicated.verdict == adjudication_mod.Verdict.SAME
    assert adjudicated.confidence == 0.92
    assert adjudicated.rationale == "Identical entity, different casing"


# ---------------------------------------------------------------------------
# Requirement: Per-Group LLM Adjudication Preserving Order
# ---------------------------------------------------------------------------


def test_one_result_per_input_group_same_order(tmp_path: Path) -> None:
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    _write_doc(tmp_path / "c.md", title="C")
    _write_doc(tmp_path / "d.md", title="D")
    _write_doc(tmp_path / "e.md", title="E")
    _write_doc(tmp_path / "f.md", title="F")
    groups = [_group("a", "b"), _group("c", "d"), _group("e", "f")]
    llm = _FakeLLM(
        replies=[
            _valid_reply("same"),
            _valid_reply("different"),
            _valid_reply("uncertain"),
        ]
    )

    result = adjudication_mod.adjudicate_candidates(
        groups, bundle_dir=tmp_path, llm=llm
    )

    assert len(result) == 3
    assert [r.candidate for r in result] == groups
    assert [r.verdict for r in result] == [
        adjudication_mod.Verdict.SAME,
        adjudication_mod.Verdict.DIFFERENT,
        adjudication_mod.Verdict.UNCERTAIN,
    ]
    assert len(llm.calls) == 3


# ---------------------------------------------------------------------------
# Requirement: Fail-Closed Reply Parsing And Validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw_verdict", "expected"),
    [
        ("same", adjudication_mod.Verdict.SAME),
        ("SAME", adjudication_mod.Verdict.SAME),
        ("Same", adjudication_mod.Verdict.SAME),
        ("different", adjudication_mod.Verdict.DIFFERENT),
        ("DIFFERENT", adjudication_mod.Verdict.DIFFERENT),
        ("uncertain", adjudication_mod.Verdict.UNCERTAIN),
        ("UNCERTAIN", adjudication_mod.Verdict.UNCERTAIN),
    ],
)
def test_verdict_mapping_case_insensitive(
    tmp_path: Path, raw_verdict: str, expected: "adjudication_mod.Verdict"
) -> None:
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    group = _group("a", "b")
    llm = _FakeLLM(replies=[_valid_reply(raw_verdict)])

    result = adjudication_mod.adjudicate_candidates(
        [group], bundle_dir=tmp_path, llm=llm
    )

    assert result[0].verdict == expected


def test_unknown_verdict_maps_to_uncertain_keeping_confidence(tmp_path: Path) -> None:
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    group = _group("a", "b")
    llm = _FakeLLM(
        replies=['{"verdict": "maybe", "confidence": 0.4, "rationale": "unclear"}']
    )

    result = adjudication_mod.adjudicate_candidates(
        [group], bundle_dir=tmp_path, llm=llm
    )

    assert result[0].verdict == adjudication_mod.Verdict.UNCERTAIN
    assert result[0].confidence == 0.4
    assert result[0].rationale == "unclear"


def test_out_of_range_confidence_is_clamped_high(tmp_path: Path) -> None:
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    group = _group("a", "b")
    llm = _FakeLLM(replies=[_valid_reply("same", 1.5, "very sure")])

    result = adjudication_mod.adjudicate_candidates(
        [group], bundle_dir=tmp_path, llm=llm
    )

    assert result[0].confidence == 1.0


def test_out_of_range_confidence_is_clamped_low(tmp_path: Path) -> None:
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    group = _group("a", "b")
    llm = _FakeLLM(replies=[_valid_reply("different", -0.3, "not sure")])

    result = adjudication_mod.adjudicate_candidates(
        [group], bundle_dir=tmp_path, llm=llm
    )

    assert result[0].confidence == 0.0


def test_malformed_reply_degrades_to_uncertain_run_continues(tmp_path: Path) -> None:
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    _write_doc(tmp_path / "c.md", title="C")
    _write_doc(tmp_path / "d.md", title="D")
    malformed_group = _group("a", "b")
    valid_group = _group("c", "d")
    llm = _FakeLLM(
        replies=["not json at all", _valid_reply("same", 0.8, "clear match")]
    )

    result = adjudication_mod.adjudicate_candidates(
        [malformed_group, valid_group], bundle_dir=tmp_path, llm=llm
    )

    assert len(result) == 2
    assert result[0].verdict == adjudication_mod.Verdict.UNCERTAIN
    assert result[0].confidence == 0.0
    assert result[0].rationale != ""
    assert result[1].verdict == adjudication_mod.Verdict.SAME
    assert result[1].confidence == 0.8


def test_malformed_reply_rationale_notes_parse_failure(tmp_path: Path) -> None:
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    group = _group("a", "b")
    llm = _FakeLLM(replies=["<<not json>>"])

    result = adjudication_mod.adjudicate_candidates(
        [group], bundle_dir=tmp_path, llm=llm
    )

    assert (
        "malformed" in result[0].rationale.lower()
        or "parse" in result[0].rationale.lower()
    )


def test_non_string_reply_degrades_to_uncertain(tmp_path: Path) -> None:
    """A backend that violates the `-> str` contract (e.g. returns `None`)
    must not crash the parser (fail-closed: `_extract_json_object`'s
    non-string guard)."""
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    group = _group("a", "b")
    llm = _FakeLLM(replies=[None])  # type: ignore[list-item]

    result = adjudication_mod.adjudicate_candidates(
        [group], bundle_dir=tmp_path, llm=llm
    )

    assert result[0].verdict == adjudication_mod.Verdict.UNCERTAIN
    assert result[0].confidence == 0.0


def test_json_array_reply_is_not_an_object_degrades_to_uncertain(
    tmp_path: Path,
) -> None:
    """A reply that parses as valid JSON but is an array, not an object, must
    still degrade fail-closed rather than crash on a missing `.get`."""
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    group = _group("a", "b")
    llm = _FakeLLM(replies=["[1, 2, 3]"])

    result = adjudication_mod.adjudicate_candidates(
        [group], bundle_dir=tmp_path, llm=llm
    )

    assert result[0].verdict == adjudication_mod.Verdict.UNCERTAIN
    assert result[0].confidence == 0.0


def test_non_string_verdict_field_maps_to_uncertain(tmp_path: Path) -> None:
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    group = _group("a", "b")
    llm = _FakeLLM(
        replies=['{"verdict": 42, "confidence": 0.6, "rationale": "weird reply"}']
    )

    result = adjudication_mod.adjudicate_candidates(
        [group], bundle_dir=tmp_path, llm=llm
    )

    assert result[0].verdict == adjudication_mod.Verdict.UNCERTAIN
    assert result[0].confidence == 0.6


def test_non_numeric_confidence_field_defaults_to_zero(tmp_path: Path) -> None:
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    group = _group("a", "b")
    llm = _FakeLLM(
        replies=['{"verdict": "same", "confidence": true, "rationale": "bool conf"}']
    )

    result = adjudication_mod.adjudicate_candidates(
        [group], bundle_dir=tmp_path, llm=llm
    )

    assert result[0].verdict == adjudication_mod.Verdict.SAME
    assert result[0].confidence == 0.0


def test_nan_confidence_fails_closed_to_zero(tmp_path: Path) -> None:
    """`json.loads` parses a bare `NaN` literal by default; a NaN confidence
    must fail closed to `0.0`, not clamp to `1.0` (NaN comparisons are always
    False, so the naive `max(0.0, min(1.0, nan))` silently returns `1.0` --
    the opposite of fail-closed)."""
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    group = _group("a", "b")
    llm = _FakeLLM(replies=['{"verdict": "same", "confidence": NaN, "rationale": "x"}'])

    result = adjudication_mod.adjudicate_candidates(
        [group], bundle_dir=tmp_path, llm=llm
    )

    assert result[0].verdict == adjudication_mod.Verdict.SAME
    assert result[0].confidence == 0.0


def test_infinity_confidence_fails_closed_to_zero(tmp_path: Path) -> None:
    """Same fail-closed guarantee for a bare `Infinity` literal (also parsed
    by `json.loads` by default)."""
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    group = _group("a", "b")
    llm = _FakeLLM(
        replies=['{"verdict": "same", "confidence": Infinity, "rationale": "x"}']
    )

    result = adjudication_mod.adjudicate_candidates(
        [group], bundle_dir=tmp_path, llm=llm
    )

    assert result[0].verdict == adjudication_mod.Verdict.SAME
    assert result[0].confidence == 0.0


def test_valid_reply_wrapped_in_json_code_fence_is_parsed(tmp_path: Path) -> None:
    """A well-formed reply wrapped in a ```json fence must be recovered by
    `_strip_code_fence` and parsed correctly, not degraded to UNCERTAIN
    (parity with `concept.py`'s extraction path)."""
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    group = _group("a", "b")
    fenced_reply = f"```json\n{_valid_reply('same', 0.85, 'fenced match')}\n```"
    llm = _FakeLLM(replies=[fenced_reply])

    result = adjudication_mod.adjudicate_candidates(
        [group], bundle_dir=tmp_path, llm=llm
    )

    assert result[0].verdict == adjudication_mod.Verdict.SAME
    assert result[0].confidence == 0.85
    assert result[0].rationale == "fenced match"


def test_valid_reply_embedded_in_prose_is_recovered(tmp_path: Path) -> None:
    """A well-formed reply embedded in surrounding prose must be recovered by
    `_first_brace_block` and parsed correctly, not degraded to UNCERTAIN
    (parity with `concept.py`'s extraction path)."""
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    group = _group("a", "b")
    prose_reply = (
        f"Here is my answer: {_valid_reply('different', 0.6, 'prose match')} "
        "hope that helps"
    )
    llm = _FakeLLM(replies=[prose_reply])

    result = adjudication_mod.adjudicate_candidates(
        [group], bundle_dir=tmp_path, llm=llm
    )

    assert result[0].verdict == adjudication_mod.Verdict.DIFFERENT
    assert result[0].confidence == 0.6
    assert result[0].rationale == "prose match"


def test_member_with_unparseable_frontmatter_is_skipped(tmp_path: Path) -> None:
    """A member whose frontmatter fails to parse is skipped, not raised
    (mirrors `retrieval/answer.py`'s guarded re-read)."""
    (tmp_path / "corrupt.md").write_text(
        "---\ntitle: [unclosed\n---\nbroken frontmatter", encoding="utf-8"
    )
    _write_doc(tmp_path / "readable.md", title="Readable Member")
    group = _group("corrupt", "readable")
    llm = _FakeLLM(replies=[_valid_reply("uncertain", 0.5, "only one member seen")])

    result = adjudication_mod.adjudicate_candidates(
        [group], bundle_dir=tmp_path, llm=llm
    )

    assert len(result) == 1
    assert len(llm.calls) == 1
    user_message = llm.calls[0][-1]["content"]
    assert "Readable Member" in user_message
    assert "corrupt" not in user_message


# ---------------------------------------------------------------------------
# Requirement: All Three Verdicts Preserved, Never Auto-Dropped
# ---------------------------------------------------------------------------


def test_different_verdict_is_present_in_returned_list(tmp_path: Path) -> None:
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    group = _group("a", "b")
    llm = _FakeLLM(replies=[_valid_reply("different", 0.7, "distinct people")])

    result = adjudication_mod.adjudicate_candidates(
        [group], bundle_dir=tmp_path, llm=llm
    )

    assert result[0].verdict == adjudication_mod.Verdict.DIFFERENT


# ---------------------------------------------------------------------------
# Requirement: Read-Only Full-Body Member Loading, Degrade Per Member
# ---------------------------------------------------------------------------


def test_unreadable_member_is_skipped_group_still_adjudicated(tmp_path: Path) -> None:
    _write_doc(tmp_path / "readable.md", title="Readable Member")
    # "missing.md" is never written -- unreadable (file does not exist).
    group = _group("readable", "missing")
    llm = _FakeLLM(replies=[_valid_reply("uncertain", 0.5, "only one member seen")])

    result = adjudication_mod.adjudicate_candidates(
        [group], bundle_dir=tmp_path, llm=llm
    )

    assert len(result) == 1
    assert result[0].verdict == adjudication_mod.Verdict.UNCERTAIN
    assert len(llm.calls) == 1
    user_message = llm.calls[0][-1]["content"]
    assert "Readable Member" in user_message
    assert "missing" not in user_message


def test_all_members_unreadable_short_circuits_without_llm_call(tmp_path: Path) -> None:
    # Neither "ghost-a.md" nor "ghost-b.md" exists -- both unreadable.
    group = _group("ghost-a", "ghost-b")
    llm = _FakeLLM(replies=[])

    result = adjudication_mod.adjudicate_candidates(
        [group], bundle_dir=tmp_path, llm=llm
    )

    assert len(result) == 1
    assert result[0].candidate == group
    assert result[0].verdict == adjudication_mod.Verdict.UNCERTAIN
    assert result[0].confidence == 0.0
    assert result[0].rationale == "no readable member content"
    assert len(llm.calls) == 0


# ---------------------------------------------------------------------------
# Requirement: `OllamaError`-Family Propagates Unswallowed From The Leaf
# ---------------------------------------------------------------------------


def test_ollama_error_propagates_unswallowed(tmp_path: Path) -> None:
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    group = _group("a", "b")
    llm = _FakeLLM(error=OllamaUnavailable("no local Ollama server reachable"))

    with pytest.raises(OllamaUnavailable):
        adjudication_mod.adjudicate_candidates([group], bundle_dir=tmp_path, llm=llm)


# ---------------------------------------------------------------------------
# Requirement: Deterministic Given A Fixed Backend
# ---------------------------------------------------------------------------


def test_repeated_runs_with_fake_backend_are_deterministic(tmp_path: Path) -> None:
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    group = _group("a", "b")

    llm_one = _FakeLLM(replies=[_valid_reply("same", 0.77, "match")])
    result_one = adjudication_mod.adjudicate_candidates(
        [group], bundle_dir=tmp_path, llm=llm_one
    )

    llm_two = _FakeLLM(replies=[_valid_reply("same", 0.77, "match")])
    result_two = adjudication_mod.adjudicate_candidates(
        [group], bundle_dir=tmp_path, llm=llm_two
    )

    assert result_one == result_two
