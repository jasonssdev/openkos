"""Unit tests for `resolution/adjudication.py`: the config-free LLM
adjudication leaf over slice-1 `find_candidates` output.

All tests use a `tmp_path` bundle and a reply-QUEUE fake `LLMBackend`
(records `.calls`, returns queued replies in call order) -- zero network,
zero real Ollama process. Mirrors `_FakeLLM` in
`tests/unit/retrieval/test_answer.py` / `tests/unit/extraction/test_concept.py`,
extended to a queue since each group needs its own reply.
"""

import dataclasses
import inspect
from collections.abc import Sequence
from pathlib import Path

import pytest

from openkos.llm.base import Message
from openkos.llm.ollama import OllamaGenerationCapped, OllamaUnavailable
from openkos.resolution import adjudication as adjudication_mod
from openkos.resolution.candidates import CandidateGroup, Tier


class _FakeLLM:
    """A structural `LLMBackend`: records every `chat` call, returns queued
    replies in call order. If `error` is set, `chat` raises it instead of
    returning (and does not consume a queued reply); `error_at` narrows the
    raise to the k-th (1-based) call so mid-loop failure can be staged after
    some groups already succeeded (#441)."""

    def __init__(
        self,
        replies: Sequence[str] = (),
        *,
        error: BaseException | None = None,
        error_at: int | None = None,
    ) -> None:
        self._replies = list(replies)
        self.error = error
        self.error_at = error_at
        self.calls: list[list[Message]] = []

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls.append(list(messages))
        if self.error is not None and (
            self.error_at is None or len(self.calls) == self.error_at
        ):
            raise self.error
        return self._replies.pop(0)


def _write_doc(
    path: Path,
    *,
    doc_type: str = "Concept",
    title: str = "Stub",
    body: str = "Body.",
    sensitivity_value: str | None = "private",
) -> None:
    """`sensitivity_value` defaults to `"private"` (`config.DEFAULT_SENSITIVITY`,
    matching what a real `ingest` always writes) so fixtures unrelated to the
    sensitivity-fail-closed-filter feature are never collaterally blocked by
    the fail-closed default; pass `None` explicitly for the absent-field
    case."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"type: {doc_type}", f"title: {title}"]
    if sensitivity_value is not None:
        lines.append(f"sensitivity: {sensitivity_value}")
    lines.append("---")
    frontmatter = "\n".join(lines) + "\n"
    path.write_text(f"{frontmatter}{body}", encoding="utf-8")


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
    ).results

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
    ).results

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
    ).results

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
    ).results

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
    ).results

    assert result[0].confidence == 1.0


def test_out_of_range_confidence_is_clamped_low(tmp_path: Path) -> None:
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    group = _group("a", "b")
    llm = _FakeLLM(replies=[_valid_reply("different", -0.3, "not sure")])

    result = adjudication_mod.adjudicate_candidates(
        [group], bundle_dir=tmp_path, llm=llm
    ).results

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
    ).results

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
    ).results

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
    ).results

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
    ).results

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
    ).results

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
    ).results

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
    ).results

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
    ).results

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
    ).results

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
    ).results

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
    ).results

    assert len(result) == 1
    assert len(llm.calls) == 1
    user_message = llm.calls[0][-1]["content"]
    assert "Readable Member" in user_message
    assert "corrupt" not in user_message


def test_system_prompt_distinguishes_part_whole_from_identity(tmp_path: Path) -> None:
    """The adjudication rubric must explicitly separate "same entity" from a
    part/component/aspect relationship and bias away from SAME when the
    relationship is part-whole -- a SAME verdict feeds a destructive merge
    (issue #138). This pins the instruction against silent regression.
    """
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    group = _group("a", "b")
    llm = _FakeLLM(replies=[_valid_reply("different", 0.7, "component, not identity")])

    adjudication_mod.adjudicate_candidates([group], bundle_dir=tmp_path, llm=llm)

    system_message = llm.calls[0][0]["content"].lower()
    assert "component" in system_message
    assert "part" in system_message
    # Bias toward not-SAME for part-whole must be stated, not merely implied.
    assert "different" in system_message
    assert "merge" in system_message


# ---------------------------------------------------------------------------
# Requirement: Cross-Type Prompt Honesty (#437)
# ---------------------------------------------------------------------------


def test_single_type_group_prompt_bytes_unchanged(tmp_path: Path) -> None:
    """Regression pin: a same-type group's rendered prompt keeps today's
    exact bytes -- `"OKF TYPE: {okf_type}"` plus each member's untagged
    `[concept_id — title]` header, no per-member type suffix, no NOTE turn."""
    _write_doc(tmp_path / "a.md", title="Ada Lovelace", body="Body A.")
    _write_doc(tmp_path / "b.md", title="Ada L.", body="Body B.")
    group = _group("a", "b", okf_type="Person", tier=Tier.HIGH)
    llm = _FakeLLM(replies=[_valid_reply("same")])

    adjudication_mod.adjudicate_candidates([group], bundle_dir=tmp_path, llm=llm)

    user_message = llm.calls[0][-1]["content"]
    assert user_message == (
        "OKF TYPE: Person\nTIER: high\n\nMEMBERS:\n\n"
        "[a — Ada Lovelace]\nBody A.\n\n"
        "[b — Ada L.]\nBody B."
    )
    assert "(OKF type:" not in user_message
    assert "NOTE" not in user_message


def test_cross_type_group_prompt_names_both_types_and_tags_each_member(
    tmp_path: Path,
) -> None:
    """A cross-type group's prompt names every distinct type present and
    tags each member's own header with that member's own type -- sourced
    from `member_types` (via a concept_id-keyed mapping the caller builds),
    NEVER from `candidate.okf_type`, which is only the joined display
    label."""
    _write_doc(tmp_path / "concepts" / "a.md", title="Stoicism", body="Body A.")
    _write_doc(tmp_path / "entities" / "b.md", title="Stoic School", body="Body B.")
    group = CandidateGroup(
        okf_type="Concept+Entity",
        member_ids=("concepts/a", "entities/b"),
        tier=Tier.HIGH,
        trigger="stoicism",
        member_types=("Concept", "Entity"),
    )
    llm = _FakeLLM(replies=[_valid_reply("different")])

    adjudication_mod.adjudicate_candidates([group], bundle_dir=tmp_path, llm=llm)

    user_message = llm.calls[0][-1]["content"]
    assert "Concept" in user_message
    assert "Entity" in user_message
    assert "[concepts/a — Stoicism] (OKF type: Concept)" in user_message
    assert "[entities/b — Stoic School] (OKF type: Entity)" in user_message
    assert "NOTE" in user_message.upper()
    # Exact-bytes regression pin (#479): an UNFILTERED cross-type group --
    # every member survives -- keeps today's exact rendering.
    assert user_message == (
        "OKF TYPE: Concept+Entity\nTIER: high\n\n"
        f"{adjudication_mod._CROSS_TYPE_NOTE}\n\n"
        "MEMBERS:\n\n"
        "[concepts/a — Stoicism] (OKF type: Concept)\nBody A.\n\n"
        "[entities/b — Stoic School] (OKF type: Entity)\nBody B."
    )


def test_blocked_member_cross_type_prompt_still_tags_correctly(
    tmp_path: Path,
) -> None:
    """When a candidate's `member_ids` is a FILTERED subset (a blocked
    member was dropped before `_build_messages` runs -- adjudication.py's
    sensitivity-fail-closed-filter), the surviving members are still tagged
    with their OWN type via the concept_id-keyed lookup, never by position."""
    _write_doc(tmp_path / "concepts" / "a.md", title="Stoicism", body="Body A.")
    _write_doc(
        tmp_path / "entities" / "b.md",
        title="Stoic School",
        body="Body B.",
        sensitivity_value="confidential",
    )
    _write_doc(tmp_path / "people" / "c.md", title="Zeno", body="Body C.")
    # The full 3-member cross-type group -- member "b" is confidential and
    # will be dropped BEFORE `_load_members` ever reads it.
    group = CandidateGroup(
        okf_type="Concept+Entity+Person",
        member_ids=("concepts/a", "entities/b", "people/c"),
        tier=Tier.HIGH,
        trigger="stub",
        member_types=("Concept", "Entity", "Person"),
    )
    llm = _FakeLLM(replies=[_valid_reply("different")])

    adjudication_mod.adjudicate_candidates([group], bundle_dir=tmp_path, llm=llm)

    user_message = llm.calls[0][-1]["content"]
    # "entities/b" was blocked -- never reaches the prompt at all.
    assert "entities/b" not in user_message
    assert "Stoic School" not in user_message
    # The surviving members keep their OWN correct type, not shifted by
    # the missing middle member's absence (positional alignment would
    # mislabel "people/c" as "Entity" here).
    assert "[concepts/a — Stoicism] (OKF type: Concept)" in user_message
    assert "[people/c — Zeno] (OKF type: Person)" in user_message


def test_cross_type_group_whose_other_type_member_is_filtered_renders_single_type_prompt(
    tmp_path: Path,
) -> None:
    """#479: the cross-type framing derives from the members ACTUALLY
    RENDERED in the prompt, never the original pre-filter `member_types`.
    When a Concept+Entity group's only Entity member is blocked, the
    survivors all share one type -- the prompt takes the plain single-type
    path (no per-member tags, no NOTE), byte-identical to an equivalent
    genuinely-single-type group's prompt."""
    _write_doc(tmp_path / "concepts" / "a.md", title="Stoicism", body="Body A.")
    _write_doc(tmp_path / "concepts" / "b.md", title="Stoic Philosophy", body="Body B.")
    _write_doc(
        tmp_path / "entities" / "c.md",
        title="Stoic School",
        body="Body C.",
        sensitivity_value="confidential",
    )
    cross_group = CandidateGroup(
        okf_type="Concept+Entity",
        member_ids=("concepts/a", "concepts/b", "entities/c"),
        tier=Tier.HIGH,
        trigger="stoicism",
        member_types=("Concept", "Concept", "Entity"),
    )
    cross_llm = _FakeLLM(replies=[_valid_reply("same")])
    adjudication_mod.adjudicate_candidates(
        [cross_group], bundle_dir=tmp_path, llm=cross_llm
    )
    cross_message = cross_llm.calls[0][-1]["content"]

    single_group = CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/a", "concepts/b"),
        tier=Tier.HIGH,
        trigger="stoicism",
    )
    single_llm = _FakeLLM(replies=[_valid_reply("same")])
    adjudication_mod.adjudicate_candidates(
        [single_group], bundle_dir=tmp_path, llm=single_llm
    )
    single_message = single_llm.calls[0][-1]["content"]

    assert "(OKF type:" not in cross_message
    assert "NOTE" not in cross_message
    assert cross_message == single_message


def test_three_type_group_with_one_member_filtered_names_only_surviving_types(
    tmp_path: Path,
) -> None:
    """#479: a 3-type group whose Person member is blocked still renders
    cross-type framing -- the survivors span two types -- but names exactly
    the two SURVIVING types, on the `OKF TYPE:` line and in the per-member
    tags, never the filtered-out one."""
    _write_doc(tmp_path / "concepts" / "a.md", title="Stoicism", body="Body A.")
    _write_doc(tmp_path / "entities" / "b.md", title="Stoic School", body="Body B.")
    _write_doc(
        tmp_path / "people" / "c.md",
        title="Zeno",
        body="Body C.",
        sensitivity_value="confidential",
    )
    group = CandidateGroup(
        okf_type="Concept+Entity+Person",
        member_ids=("concepts/a", "entities/b", "people/c"),
        tier=Tier.HIGH,
        trigger="stub",
        member_types=("Concept", "Entity", "Person"),
    )
    llm = _FakeLLM(replies=[_valid_reply("different")])

    adjudication_mod.adjudicate_candidates([group], bundle_dir=tmp_path, llm=llm)

    user_message = llm.calls[0][-1]["content"]
    assert user_message.startswith("OKF TYPE: Concept+Entity\nTIER: high")
    assert "Person" not in user_message
    assert "[concepts/a — Stoicism] (OKF type: Concept)" in user_message
    assert "[entities/b — Stoic School] (OKF type: Entity)" in user_message
    assert adjudication_mod._CROSS_TYPE_NOTE in user_message


def test_verdict_schema_unchanged_for_a_cross_type_group(tmp_path: Path) -> None:
    """A cross-type group's `AdjudicatedCandidate` exposes only
    `candidate`/`verdict`/`confidence`/`rationale` -- no new field, even
    though the group carries `member_types`."""
    _write_doc(tmp_path / "concepts" / "a.md", title="Stoicism")
    _write_doc(tmp_path / "entities" / "b.md", title="Stoic School")
    group = CandidateGroup(
        okf_type="Concept+Entity",
        member_ids=("concepts/a", "entities/b"),
        tier=Tier.HIGH,
        trigger="stoicism",
        member_types=("Concept", "Entity"),
    )
    llm = _FakeLLM(replies=[_valid_reply("different", 0.8, "different entities")])

    result = adjudication_mod.adjudicate_candidates(
        [group], bundle_dir=tmp_path, llm=llm
    ).results[0]

    assert {f.name for f in dataclasses.fields(result)} == {
        "candidate",
        "verdict",
        "confidence",
        "rationale",
    }
    assert result.verdict == adjudication_mod.Verdict.DIFFERENT
    assert result.confidence == 0.8


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
    ).results

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
    ).results

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
    ).results

    assert len(result) == 1
    assert result[0].candidate == group
    assert result[0].verdict == adjudication_mod.Verdict.UNCERTAIN
    assert result[0].confidence == 0.0
    assert result[0].rationale == "no readable member content"
    assert len(llm.calls) == 0


# ---------------------------------------------------------------------------
# Requirement: Partial Batch Preserved On Mid-Loop `OllamaError` (#441)
# ---------------------------------------------------------------------------


def test_mid_loop_ollama_error_returns_completed_results_and_failure(
    tmp_path: Path,
) -> None:
    """An `OllamaError`-family raise on the k-th group's `llm.chat` stops the
    loop and returns every already-completed result in input order, the
    exception instance itself, and the 1-based failed index -- the caller
    paid for k-1 completed calls and keeps all of them (#441). No group
    after the failed one is ever prompted; the failed group produces no
    result and no `on_progress` call."""
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    _write_doc(tmp_path / "c.md", title="C")
    _write_doc(tmp_path / "d.md", title="D")
    _write_doc(tmp_path / "e.md", title="E")
    _write_doc(tmp_path / "f.md", title="F")
    groups = [_group("a", "b"), _group("c", "d"), _group("e", "f")]
    capped = OllamaGenerationCapped("generation hit the token ceiling")
    llm = _FakeLLM(replies=[_valid_reply("same")], error=capped, error_at=2)
    seen: list[tuple[int, int, adjudication_mod.AdjudicatedCandidate]] = []

    def _cb(
        index: int, total: int, result: adjudication_mod.AdjudicatedCandidate
    ) -> None:
        seen.append((index, total, result))

    batch = adjudication_mod.adjudicate_candidates(
        groups, bundle_dir=tmp_path, llm=llm, on_progress=_cb
    )

    assert isinstance(batch, adjudication_mod.AdjudicationBatch)
    assert [r.candidate for r in batch.results] == groups[:1]
    assert batch.results[0].verdict is adjudication_mod.Verdict.SAME
    assert batch.failure is capped
    assert batch.failed_index == 2
    # The failed call itself happened; the group AFTER it was never prompted.
    assert len(llm.calls) == 2
    # `on_progress` fired only for completed results -- never for the failure.
    assert [(index, total) for index, total, _ in seen] == [(1, 3)]


def test_ollama_error_on_first_group_returns_empty_results(tmp_path: Path) -> None:
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    group = _group("a", "b")
    failure = OllamaUnavailable("no local Ollama server reachable")
    llm = _FakeLLM(error=failure, error_at=1)

    batch = adjudication_mod.adjudicate_candidates(
        [group], bundle_dir=tmp_path, llm=llm
    )

    assert batch.results == []
    assert batch.failure is failure
    assert batch.failed_index == 1


def test_complete_run_reports_no_failure(tmp_path: Path) -> None:
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    _write_doc(tmp_path / "c.md", title="C")
    _write_doc(tmp_path / "d.md", title="D")
    groups = [_group("a", "b"), _group("c", "d")]
    llm = _FakeLLM(replies=[_valid_reply("same"), _valid_reply("different")])

    batch = adjudication_mod.adjudicate_candidates(groups, bundle_dir=tmp_path, llm=llm)

    assert batch.failure is None
    assert batch.failed_index is None
    assert len(batch.results) == len(groups)


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
    ).results

    llm_two = _FakeLLM(replies=[_valid_reply("same", 0.77, "match")])
    result_two = adjudication_mod.adjudicate_candidates(
        [group], bundle_dir=tmp_path, llm=llm_two
    ).results

    assert result_one == result_two


# ---------------------------------------------------------------------------
# sensitivity-fail-closed-filter, S3a/PR1: confidential members excluded
# before adjudication
# ---------------------------------------------------------------------------


def test_confidential_member_excluded_before_load_members_by_default(
    tmp_path: Path,
) -> None:
    """A `sensitivity: confidential` member is dropped from `member_ids`
    BEFORE `_load_members`, so the LLM prompt only ever sees the private
    member's content (spec: Confidential excluded from adjudicate/
    contradictions/suggest-relations)."""
    _write_doc(tmp_path / "a.md", title="A", sensitivity_value="confidential")
    _write_doc(tmp_path / "b.md", title="B", sensitivity_value="private")
    group = _group("a", "b")
    llm = _FakeLLM(replies=[_valid_reply("same", 0.9, "match")])

    result = adjudication_mod.adjudicate_candidates(
        [group], bundle_dir=tmp_path, llm=llm
    ).results

    assert len(llm.calls) == 1
    (message,) = [m for m in llm.calls[0] if m["role"] == "user"]
    assert "[a — A]" not in message["content"]
    assert "[b — B]" in message["content"]
    assert result[0].candidate == group


def test_all_members_confidential_short_circuits_to_uncertain(tmp_path: Path) -> None:
    """A group whose members are ALL confidential degrades to `UNCERTAIN`
    without calling `llm.chat` -- the same short-circuit as all-unreadable
    members (spec: Exclusion, not redaction)."""
    _write_doc(tmp_path / "a.md", title="A", sensitivity_value="confidential")
    _write_doc(tmp_path / "b.md", title="B", sensitivity_value="confidential")
    group = _group("a", "b")
    llm = _FakeLLM()

    result = adjudication_mod.adjudicate_candidates(
        [group], bundle_dir=tmp_path, llm=llm
    ).results

    assert result[0].verdict is adjudication_mod.Verdict.UNCERTAIN
    assert result[0].confidence == 0.0
    assert len(llm.calls) == 0


def test_include_confidential_true_restores_the_confidential_member(
    tmp_path: Path,
) -> None:
    """`include_confidential=True` restores a confidential member to the
    prompt, unchanged (spec: `--include-confidential` Escape Flag)."""
    _write_doc(tmp_path / "a.md", title="A", sensitivity_value="confidential")
    _write_doc(tmp_path / "b.md", title="B", sensitivity_value="private")
    group = _group("a", "b")
    llm = _FakeLLM(replies=[_valid_reply("same", 0.9, "match")])

    adjudication_mod.adjudicate_candidates(
        [group], bundle_dir=tmp_path, llm=llm, include_confidential=True
    )

    (message,) = [m for m in llm.calls[0] if m["role"] == "user"]
    assert "[a — A]" in message["content"]
    assert "[b — B]" in message["content"]


def test_include_confidential_true_never_calls_the_predicate_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`include_confidential=True` reaches
    `sensitivity.sensitive_concept_ids` as the flag it is, so the predicate
    short-circuits before touching `okf._iter_docs` -- the escape flag is
    still the zero-cost path (design R1).

    Issue #240 moved WHERE the skip is decided, not whether it happens: the
    guarding `if not include_confidential:` used to live here, but a second
    hatch (`local_exemption`) would have made that a two-term disjunction
    restated at five call sites, which is the duplication `sensitivity.py`'s
    module docstring exists to prevent. The walk-skip itself is pinned once,
    centrally, by
    `tests/unit/test_sensitivity.py::test_sensitive_concept_ids_escape_hatches_skip_the_walk_entirely`."""
    from openkos import sensitivity

    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    group = _group("a", "b")
    llm = _FakeLLM(replies=[_valid_reply("same", 0.9, "match")])
    walk_calls: list[dict[str, object]] = []
    original_predicate = sensitivity.sensitive_concept_ids

    def _spy_predicate(bundle_dir: Path, **kwargs: object) -> frozenset[str]:
        walk_calls.append(kwargs)
        return original_predicate(bundle_dir, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(sensitivity, "sensitive_concept_ids", _spy_predicate)

    adjudication_mod.adjudicate_candidates(
        [group], bundle_dir=tmp_path, llm=llm, include_confidential=True
    )

    assert walk_calls == [{"include_confidential": True, "local_exemption": False}]


def test_default_include_confidential_false_calls_the_predicate_walk_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default `include_confidential=False` DOES call
    `sensitivity.sensitive_concept_ids` exactly once per
    `adjudicate_candidates` call."""
    from openkos import sensitivity

    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    group = _group("a", "b")
    llm = _FakeLLM(replies=[_valid_reply("same", 0.9, "match")])
    walk_calls: list[Path] = []
    original_predicate = sensitivity.sensitive_concept_ids

    def _spy_predicate(bundle_dir: Path, **kwargs: object) -> frozenset[str]:
        walk_calls.append(bundle_dir)
        return original_predicate(bundle_dir, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(sensitivity, "sensitive_concept_ids", _spy_predicate)

    adjudication_mod.adjudicate_candidates([group], bundle_dir=tmp_path, llm=llm)

    assert walk_calls == [tmp_path]


# ---------------------------------------------------------------------------
# directory-walk-observability follow-up: `_load_members` leak-closure
# re-check
# ---------------------------------------------------------------------------


def test_load_members_independently_excludes_a_confidential_member_the_walk_never_saw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_load_members` re-checks EACH member's own frontmatter at re-read
    time, independent of the precomputed confidential-id set built during
    the walk -- a confidential member the walk silently missed (e.g. an
    unlistable subtree, `okf.py`'s documented `_walk_errors` case) but still
    directly readable by path MUST still be excluded from the `llm.chat`
    prompt (mirrors `retrieval/answer.py`'s `_assemble_context` re-check,
    correction batch FIX 2)."""
    from openkos import sensitivity

    _write_doc(tmp_path / "a.md", title="A", sensitivity_value="confidential")
    _write_doc(tmp_path / "b.md", title="B", sensitivity_value="private")
    group = _group("a", "b")
    # Simulate the walk missing "a"'s subtree entirely: the precomputed
    # confidential-id set is empty, so "a" is NOT dropped from `member_ids`
    # upstream -- the independent per-member re-check in `_load_members` is
    # the ONLY thing standing between "a"'s content and `llm.chat`.
    monkeypatch.setattr(
        sensitivity, "sensitive_concept_ids", lambda *a, **k: frozenset()
    )
    llm = _FakeLLM(replies=[_valid_reply("same", 0.9, "match")])

    adjudication_mod.adjudicate_candidates([group], bundle_dir=tmp_path, llm=llm)

    assert len(llm.calls) == 1
    (message,) = [m for m in llm.calls[0] if m["role"] == "user"]
    assert "[a — A]" not in message["content"]
    assert "[b — B]" in message["content"]


def test_include_confidential_true_bypasses_the_load_members_recheck(
    tmp_path: Path,
) -> None:
    """`include_confidential=True` bypasses the independent `_load_members`
    re-check too, restoring byte-identical pre-filter behavior (not just at
    the upstream predicate-walk filter)."""
    _write_doc(tmp_path / "a.md", title="A", sensitivity_value="confidential")
    _write_doc(tmp_path / "b.md", title="B", sensitivity_value="private")
    group = _group("a", "b")
    llm = _FakeLLM(replies=[_valid_reply("same", 0.9, "match")])

    adjudication_mod.adjudicate_candidates(
        [group], bundle_dir=tmp_path, llm=llm, include_confidential=True
    )

    (message,) = [m for m in llm.calls[0] if m["role"] == "user"]
    assert "[a — A]" in message["content"]
    assert "[b — B]" in message["content"]


# ---------------------------------------------------------------------------
# Requirement: `on_progress` per-group progress hook (issue #190)
# ---------------------------------------------------------------------------


def test_adjudicate_candidates_invokes_on_progress_once_per_group_in_order(
    tmp_path: Path,
) -> None:
    """`on_progress` fires once per candidate group, in input order, AFTER
    that group's `AdjudicatedCandidate` is built -- carrying `(index, total,
    result)` with a 1-based `index` and `total == len(candidates)` so a CLI
    can render a per-group progress line during an otherwise opaque,
    minutes-long run (issue #190, mirrors `suggest_edge_types`'s #134
    contract). The no-readable-members short-circuit result COUNTS: a
    result is a result, whether or not `llm.chat` was ever called."""
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    _write_doc(tmp_path / "c.md", title="C")
    _write_doc(tmp_path / "d.md", title="D")
    readable_1 = _group("a", "b")
    unreadable = _group("missing-1", "missing-2")  # short-circuits, no chat call
    readable_2 = _group("c", "d")
    llm = _FakeLLM(replies=[_valid_reply("same"), _valid_reply("different")])
    seen: list[tuple[int, int, adjudication_mod.AdjudicatedCandidate]] = []

    def _cb(
        index: int, total: int, result: adjudication_mod.AdjudicatedCandidate
    ) -> None:
        seen.append((index, total, result))

    results = adjudication_mod.adjudicate_candidates(
        [readable_1, unreadable, readable_2],
        bundle_dir=tmp_path,
        llm=llm,
        on_progress=_cb,
    ).results

    assert [(index, total) for index, total, _ in seen] == [(1, 3), (2, 3), (3, 3)]
    # Identity, not equality: the callback receives the SAME objects the
    # function returns.
    assert [result for _, _, result in seen] == results
    assert all(
        cb_result is result
        for (_, _, cb_result), result in zip(seen, results, strict=True)
    )
    assert seen[1][2].verdict is adjudication_mod.Verdict.UNCERTAIN


def test_adjudicate_candidates_on_progress_omitted_changes_nothing(
    tmp_path: Path,
) -> None:
    """Omitting `on_progress` (the default) keeps the return value and call
    pattern byte-identical -- the hook never affects the returned list
    (issue #190)."""
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    group = _group("a", "b")
    llm = _FakeLLM(replies=[_valid_reply("same")])

    results = adjudication_mod.adjudicate_candidates(
        [group], bundle_dir=tmp_path, llm=llm
    ).results

    assert len(results) == 1
    assert len(llm.calls) == 1


def test_adjudicate_candidates_on_progress_exception_propagates(
    tmp_path: Path,
) -> None:
    """An exception raised inside `on_progress` propagates to the caller
    unswallowed -- it is the caller's own callback (issue #190, same
    contract as `suggest_edge_types`)."""
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    group = _group("a", "b")
    llm = _FakeLLM(replies=[_valid_reply("same")])

    def _boom(index: int, total: int, result: object) -> None:
        raise RuntimeError("callback failed")

    with pytest.raises(RuntimeError, match="callback failed"):
        adjudication_mod.adjudicate_candidates(
            [group], bundle_dir=tmp_path, llm=llm, on_progress=_boom
        )


# ---------------------------------------------------------------------------
# issue #240: the confidential local exemption
# ---------------------------------------------------------------------------


def test_local_exemption_restores_the_confidential_member(tmp_path: Path) -> None:
    """With a verified-local backend, a `confidential` member is adjudicated
    with its real content -- no `--include-confidential` required (#240)."""
    _write_doc(
        tmp_path / "a.md",
        title="A",
        body="Alpha secret body.",
        sensitivity_value="confidential",
    )
    _write_doc(tmp_path / "b.md", title="B")
    llm = _FakeLLM(replies=[_valid_reply("same", 0.9, "match")])

    adjudication_mod.adjudicate_candidates(
        [_group("a", "b")], bundle_dir=tmp_path, llm=llm, local_exemption=True
    )

    (message,) = [m for m in llm.calls[0] if m["role"] == "user"]
    assert "[a — A]" in message["content"]
    assert "Alpha secret body." in message["content"]


def test_local_exemption_defaults_to_false_on_adjudicate_candidates(
    tmp_path: Path,
) -> None:
    """`adjudicate_candidates` defaults `local_exemption` to `False`, so a
    group of confidential members still short-circuits to `UNCERTAIN`
    without an `llm.chat` call (#240)."""
    assert (
        inspect.signature(adjudication_mod.adjudicate_candidates)
        .parameters["local_exemption"]
        .default
        is False
    )

    _write_doc(tmp_path / "a.md", title="A", sensitivity_value="confidential")
    _write_doc(tmp_path / "b.md", title="B", sensitivity_value="confidential")
    llm = _FakeLLM()

    result = adjudication_mod.adjudicate_candidates(
        [_group("a", "b")], bundle_dir=tmp_path, llm=llm
    ).results

    assert result[0].verdict is adjudication_mod.Verdict.UNCERTAIN
    assert llm.calls == []


# --------------------------------------------------------------------------- #
# #796: a SAME verdict whose rationale argues the members are two occurrences
# --------------------------------------------------------------------------- #


def test_a_rationale_calling_them_separate_occurrences_withdraws_same() -> None:
    """The reported defect's signature. The model answered `same` and its
    own rationale said the two are different instances of one recurring
    event -- an argument for `different` written under a `same` verdict."""
    verdict, rationale = adjudication_mod.withdraw_self_refuting_same(
        adjudication_mod.Verdict.SAME,
        "Both members refer to the same recurring event called 'Weekly "
        "Design Review', indicating they are different instances of the "
        "same event.",
    )

    assert verdict is adjudication_mod.Verdict.DIFFERENT
    assert adjudication_mod.OCCURRENCE_WITHDRAWN_NOTE in rationale
    assert rationale.startswith("Both members refer")


def test_a_negated_marker_is_not_a_self_refutation() -> None:
    """`There is no indication of different occurrences` ARGUES FOR the
    verdict it carries. A rule that read the phrase without its negation
    would withdraw a correct answer -- measured at 9 of 60 correct `same`
    verdicts before this guard existed."""
    verdict, rationale = adjudication_mod.withdraw_self_refuting_same(
        adjudication_mod.Verdict.SAME,
        "The dates, attendees and figures match exactly. There is no "
        "indication of different occurrences or different entities.",
    )

    assert verdict is adjudication_mod.Verdict.SAME
    assert adjudication_mod.OCCURRENCE_WITHDRAWN_NOTE not in rationale


def test_the_negation_must_share_the_markers_sentence() -> None:
    """A negation belonging to a DIFFERENT claim cannot excuse the marker.
    Scoped per sentence, the same way #807 scopes its direction markers."""
    verdict, _ = adjudication_mod.withdraw_self_refuting_same(
        adjudication_mod.Verdict.SAME,
        "The attendees do not overlap. These are different occurrences of "
        "one standing meeting.",
    )

    assert verdict is adjudication_mod.Verdict.DIFFERENT


def test_the_negation_must_precede_the_marker() -> None:
    """`different occurrences, not one event` negates AFTER the claim is
    made, which does not unmake it."""
    verdict, _ = adjudication_mod.withdraw_self_refuting_same(
        adjudication_mod.Verdict.SAME,
        "These are different occurrences of the series, not one event.",
    )

    assert verdict is adjudication_mod.Verdict.DIFFERENT


def test_only_a_same_verdict_is_ever_withdrawn() -> None:
    """`different` and `uncertain` already refuse the merge; re-deciding
    them would be the rule inventing a verdict rather than withdrawing
    one."""
    for verdict in (
        adjudication_mod.Verdict.DIFFERENT,
        adjudication_mod.Verdict.UNCERTAIN,
    ):
        got, rationale = adjudication_mod.withdraw_self_refuting_same(
            verdict, "They are different instances of the same event."
        )
        assert got is verdict
        assert adjudication_mod.OCCURRENCE_WITHDRAWN_NOTE not in rationale


def test_a_rationale_without_the_signature_is_left_alone() -> None:
    """The rule fires on a self-refutation, never on the topic. A `same`
    verdict about two records of one meeting keeps its verdict and its
    rationale byte-for-byte."""
    original = (
        "Identical date, identical attendees, identical figures. Two sets "
        "of notes from one meeting."
    )

    assert adjudication_mod.withdraw_self_refuting_same(
        adjudication_mod.Verdict.SAME, original
    ) == (
        adjudication_mod.Verdict.SAME,
        original,
    )


def test_a_spanish_self_refutation_is_caught_too() -> None:
    """The corpus is bilingual and #812 lets a run pin the rationale's
    language, so an English-only marker table would fail closed on exactly
    the workspaces that reported this."""
    verdict, _ = adjudication_mod.withdraw_self_refuting_same(
        adjudication_mod.Verdict.SAME,
        "Ambas entradas describen la misma reunión recurrente, por lo que "
        "son ocurrencias distintas de la serie.",
    )

    assert verdict is adjudication_mod.Verdict.DIFFERENT


def test_the_withdrawal_is_idempotent() -> None:
    """It runs on freshly judged verdicts AND on verdicts served from the
    store, and a served row may already carry the note. Applying it twice
    must not stack a second one."""
    once = adjudication_mod.withdraw_self_refuting_same(
        adjudication_mod.Verdict.SAME, "They are different instances of the same event."
    )
    twice = adjudication_mod.withdraw_self_refuting_same(*once)

    assert twice == once


def test_adjudicate_candidates_withdraws_a_self_refuting_same(
    tmp_path: Path,
) -> None:
    """The public seam: a model reply that answers `same` while arguing for
    two occurrences comes back `different`."""
    _write_doc(tmp_path / "a.md", doc_type="Event", title="Weekly Design Review")
    _write_doc(tmp_path / "b.md", doc_type="Event", title="Weekly Design Review")
    llm = _FakeLLM(
        replies=[
            _valid_reply(
                "same",
                0.95,
                "These are different occurrences of one recurring meeting",
            )
        ]
    )

    batch = adjudication_mod.adjudicate_candidates(
        [_group("a", "b", okf_type="Event")], bundle_dir=tmp_path, llm=llm
    )

    assert batch.results[0].verdict is adjudication_mod.Verdict.DIFFERENT
    assert adjudication_mod.OCCURRENCE_WITHDRAWN_NOTE in batch.results[0].rationale


def test_naming_the_series_is_not_claiming_two_of_its_occurrences() -> None:
    """Every marker must carry a DISTINCTNESS word.

    `the same recurring meeting` names the SERIES; it does not say the two
    members are two of its sessions, and a correct `same` rationale about
    one meeting recorded twice says exactly this. An earlier marker table
    matched the bare phrase and withdrew that verdict.
    """
    for rationale in (
        "Both entries describe the same recurring meeting and record "
        "identical attendees and decisions.",
        "The title names a recurring event, and both notes cover the same session.",
    ):
        verdict, unchanged = adjudication_mod.withdraw_self_refuting_same(
            adjudication_mod.Verdict.SAME, rationale
        )
        assert verdict is adjudication_mod.Verdict.SAME
        assert unchanged == rationale


def test_a_downgrader_negates_as_firmly_as_a_denial() -> None:
    """`hardly any indication of different occurrences` makes the same
    claim as `no indication`, and a guard reading only the flat denials
    withdraws a correct verdict on a rewording."""
    for rationale in (
        "There is hardly any indication of different occurrences.",
        "Never do the entries describe different occurrences.",
        "Nunca se observan ocurrencias distintas entre ambas entradas.",
    ):
        verdict, _ = adjudication_mod.withdraw_self_refuting_same(
            adjudication_mod.Verdict.SAME, rationale
        )
        assert verdict is adjudication_mod.Verdict.SAME


def test_the_rubric_tells_the_model_what_an_identical_event_title_means() -> None:
    """#796's fix is a rubric paragraph, and a rubric paragraph is only a
    fix while it is in the rubric. Pinned by its load-bearing claims rather
    than byte-for-byte, so rewording stays possible and deletion does not.

    Measured before adoption (`evals/adjudication/README.md`): it takes the
    recurrence class from 0.33 to 1.00 at stability 1.00, and costs nothing
    on one-meeting-recorded-twice, an identical Person name, an alias pair,
    or a part-whole.
    """
    rubric = adjudication_mod._SYSTEM_PROMPT.lower()

    assert "recurring series" in rubric
    assert "occurrences" in rubric
    assert "person" in rubric, "the frame is type-aware, not Event-only"
    # The reported rationale disqualified itself in exactly these words.
    assert "continuation" in rubric
    assert "follow-up" in rubric


def test_cannot_negates_as_firmly_as_can_not() -> None:
    """`\\bnot\\b` never matches inside `cannot` -- there is no word boundary
    before it -- so the one-word spelling slipped the guard and withdrew a
    correct verdict."""
    verdict, _ = adjudication_mod.withdraw_self_refuting_same(
        adjudication_mod.Verdict.SAME,
        "The bodies cannot be different occurrences: every detail matches.",
    )

    assert verdict is adjudication_mod.Verdict.SAME


@pytest.mark.parametrize(
    "rationale",
    [
        pytest.param(
            "The event appears to be a continuation of the same meeting.",
            id="continuation-of",
        ),
        pytest.param(
            "The second entry is a follow-up to the first.", id="follow-up-to"
        ),
        pytest.param(
            "These notes record a follow up of the earlier discussion.",
            id="follow-up-unhyphenated",
        ),
        pytest.param(
            "This is a later session of the same standing meeting.",
            id="later-session",
        ),
        pytest.param(
            "It records a subsequent meeting of the same group.",
            id="subsequent-meeting",
        ),
        pytest.param(
            "Ambas describen una continuación de la sesión anterior.",
            id="continuacion-de",
        ),
        pytest.param(
            "La segunda es una sesión posterior de la misma serie.",
            id="sesion-posterior",
        ),
    ],
)
def test_every_marker_alternative_withdraws(rationale: str) -> None:
    """Each alternative in the table must actually fire.

    `continuation`/`follow-up` carry the phrasing of the ORIGINALLY
    reported rationale -- *"the event appears to be a continuation or
    follow-up of the same meeting"* -- so a table that grew a typo there
    would leave the reported defect unguarded while every other test
    stayed green.
    """
    verdict, rendered = adjudication_mod.withdraw_self_refuting_same(
        adjudication_mod.Verdict.SAME, rationale
    )

    assert verdict is adjudication_mod.Verdict.DIFFERENT
    assert adjudication_mod.OCCURRENCE_WITHDRAWN_NOTE in rendered


def test_a_later_marker_in_a_negated_sentence_stays_negated() -> None:
    """Examining only the first marker in a sentence is not a shortcut that
    loses verdicts.

    The negation window is the sentence PREFIX, and a prefix only grows: a
    cue standing before the first marker stands before every later one in
    that sentence as well. So a sentence whose first marker is negated
    cannot hold a second, un-negated one, and stopping at the first is
    equivalent to iterating them.
    """
    for rationale in (
        "There is no indication of different occurrences, but these are "
        "separate sessions.",
        "Nothing suggests different instances; still, this is a follow-up "
        "to the first.",
        "No hay ocurrencias distintas, aunque son sesiones posteriores de la serie.",
    ):
        verdict, unchanged = adjudication_mod.withdraw_self_refuting_same(
            adjudication_mod.Verdict.SAME, rationale
        )
        assert verdict is adjudication_mod.Verdict.SAME
        assert unchanged == rationale


# --- issue #838: rubric_digest() (the serve gate's rubric fingerprint) ------


def test_rubric_digest_is_stable_within_a_build() -> None:
    """#838: two calls agree, and the shape is a prefixed sha256 hex -- a
    value `state.adjudications` can store and compare byte-for-byte."""
    first = adjudication_mod.rubric_digest()

    assert first == adjudication_mod.rubric_digest()
    assert first.startswith("sha256:")
    assert len(first) == len("sha256:") + 64


def test_rubric_digest_tracks_the_system_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#838's core claim: a verdict computed under a different system
    prompt was computed over a DIFFERENT prompt -- the same argument
    `include_confidential` already encodes, one input wider. A prompt
    edit MUST change the digest, or the serve gate lets a stale rubric
    keep answering."""
    before = adjudication_mod.rubric_digest()
    monkeypatch.setattr(
        adjudication_mod, "_SYSTEM_PROMPT", adjudication_mod._SYSTEM_PROMPT + " edited"
    )

    assert adjudication_mod.rubric_digest() != before


@pytest.mark.parametrize(
    "attribute",
    [
        "_OCCURRENCE_MARKERS",
        "_NEGATION_CUES",
        "_SENTENCE_SPLIT",
        "OCCURRENCE_WITHDRAWN_NOTE",
    ],
)
def test_rubric_digest_tracks_every_post_parse_component(
    monkeypatch: pytest.MonkeyPatch, attribute: str
) -> None:
    """#838: the digest covers EVERY input of the deterministic post-parse
    rule that can change a verdict -- the occurrence markers, the negation
    cues, the sentence boundary that scopes them to each other, and the
    note the withdrawal appends -- so the prompt and the rule cannot drift
    apart. Parametrized over all four rather than sampling one: a
    component silently dropped from the digest's part list would otherwise
    keep serving verdicts across an edit to it."""
    import re as _re

    before = adjudication_mod.rubric_digest()
    current = getattr(adjudication_mod, attribute)
    edited: object
    if isinstance(current, str):
        edited = current + " edited"
    else:
        edited = _re.compile(current.pattern + "|extra-alternative")
    monkeypatch.setattr(adjudication_mod, attribute, edited)

    assert adjudication_mod.rubric_digest() != before
