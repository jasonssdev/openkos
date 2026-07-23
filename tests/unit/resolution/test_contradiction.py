"""Unit tests for `resolution/contradiction.py`: the config-free LLM
contradiction-detection precision leaf over graph typed-edge pairs (MVP-2
slice 3, freshness-lint-v1 S3).

All tests use a `tmp_path` bundle and a reply-QUEUE fake `LLMBackend`
(records `.calls`, returns queued replies in call order) -- zero network,
zero real Ollama process. Mirrors `_FakeLLM` in
`tests/unit/resolution/test_edge_typing.py` /
`tests/unit/resolution/test_adjudication.py`.
"""

import contextlib
import dataclasses
import math
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from openkos import lifecycle, sensitivity
from openkos.graph.base import Edge, GraphStore
from openkos.llm import parsing
from openkos.llm.base import Message
from openkos.llm.ollama import OllamaUnavailable
from openkos.resolution import contradiction as contradiction_mod


class _FakeLLM:
    """A structural `LLMBackend`: records every `chat` call, returns queued
    replies in call order. If `error` is set, `chat` raises it instead of
    returning (and does not consume a queued reply)."""

    def __init__(
        self, replies: Sequence[object] = (), *, error: BaseException | None = None
    ) -> None:
        self._replies = list(replies)
        self.error = error
        self.calls: list[list[Message]] = []

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls.append(list(messages))
        if self.error is not None:
            raise self.error
        return self._replies.pop(0)  # type: ignore[return-value]


class _FakeGraphStore:
    """A minimal `GraphStore` fixture carrying a fixed edge list."""

    def __init__(self, edges: list[Edge]) -> None:
        self._edges = edges

    def nodes(self) -> list[str]:
        return sorted(
            {e.source_id for e in self._edges} | {e.target_id for e in self._edges}
        )

    def edges(self) -> list[Edge]:
        return self._edges

    def neighbors(self, concept_id: str) -> list[str]:
        raise NotImplementedError("not exercised by _candidate_pairs")


def _write_doc(
    path: Path,
    *,
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
    lines = ["---", "type: Concept", f"title: {title}"]
    if sensitivity_value is not None:
        lines.append(f"sensitivity: {sensitivity_value}")
    lines.append("---")
    frontmatter = "\n".join(lines) + "\n"
    path.write_text(f"{frontmatter}{body}", encoding="utf-8")


def _write_lifecycle_doc(
    path: Path,
    *,
    title: str = "Stub",
    body: str = "Body.",
    status: str | None = None,
    relations: list[tuple[str, str]] | None = None,
    sensitivity_value: str | None = "private",
) -> None:
    """`_write_doc` plus optional `status`/`relations` frontmatter
    (status-aware-retrieval, Phase 3) -- `relations` is a list of `(target,
    type)` pairs, mirroring `test_answer.py`'s/`test_lifecycle.py`'s helper
    so every lifecycle-fixture-building test module shares the same shape.
    `sensitivity_value` defaults to `"private"` for the same collateral-safety
    reason as `_write_doc`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: Concept", f"title: {title}"]
    if status is not None:
        lines.append(f"status: {status}")
    if sensitivity_value is not None:
        lines.append(f"sensitivity: {sensitivity_value}")
    if relations is not None:
        lines.append("relations:")
        for target, rel_type in relations:
            lines.append(f"  - target: {target}")
            lines.append(f"    type: {rel_type}")
    lines.append("---")
    frontmatter = "\n".join(lines) + "\n"
    path.write_text(f"{frontmatter}{body}", encoding="utf-8")


def _valid_reply(
    verdict: str = "contradicts",
    confidence: float = 0.9,
    rationale: str = "explicit conflict",
    conflicting_claims: Sequence[str] = ("A says X", "B says not X"),
) -> str:
    claims = ", ".join(f'"{c}"' for c in conflicting_claims)
    return (
        f'{{"verdict": "{verdict}", "confidence": {confidence}, '
        f'"rationale": "{rationale}", "conflicting_claims": [{claims}]}}'
    )


# ---------------------------------------------------------------------------
# Phase 1: `Verdict` enum + `ContradictionVerdict` shape
# ---------------------------------------------------------------------------


def test_verdict_enum_has_exactly_the_three_expected_values() -> None:
    assert contradiction_mod.Verdict.CONTRADICTS.value == "contradicts"
    assert contradiction_mod.Verdict.CONSISTENT.value == "consistent"
    assert contradiction_mod.Verdict.UNCERTAIN.value == "uncertain"
    assert {v.value for v in contradiction_mod.Verdict} == {
        "contradicts",
        "consistent",
        "uncertain",
    }


def test_contradiction_verdict_carries_all_expected_fields() -> None:
    verdict = contradiction_mod.ContradictionVerdict(
        pair_ids=("a", "b"),
        verdict=contradiction_mod.Verdict.CONTRADICTS,
        confidence=0.9,
        rationale="explicit conflict",
        conflicting_claims=("A says X", "B says not X"),
    )

    assert verdict.pair_ids == ("a", "b")
    assert verdict.verdict is contradiction_mod.Verdict.CONTRADICTS
    assert verdict.confidence == 0.9
    assert verdict.rationale == "explicit conflict"
    assert verdict.conflicting_claims == ("A says X", "B says not X")


def test_contradiction_verdict_is_frozen() -> None:
    verdict = contradiction_mod.ContradictionVerdict(
        pair_ids=("a", "b"),
        verdict=contradiction_mod.Verdict.UNCERTAIN,
        confidence=0.0,
        rationale="",
        conflicting_claims=(),
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        verdict.rationale = "changed"  # type: ignore[misc]


def test_module_constants_pinned_to_spec_values() -> None:
    assert contradiction_mod._MAX_PAIRS == 200
    assert contradiction_mod._CONFIDENCE_DISPLAY_THRESHOLD == 0.7


# ---------------------------------------------------------------------------
# Phase 2: `_candidate_pairs` -- dedup, ordering, cap (Req: Dedup, Ordering, Cap)
# ---------------------------------------------------------------------------


def test_candidate_pairs_ignores_untyped_edges() -> None:
    untyped = Edge(source_id="a", target_id="b")
    store: GraphStore = _FakeGraphStore([untyped])

    pairs, total = contradiction_mod._candidate_pairs(store)

    assert pairs == []
    assert total == 0


def test_candidate_pairs_symmetric_edge_pair_collapses_to_one() -> None:
    """A -->relation1--> B and B -->relation2--> A collapse to a single
    unordered pair via `frozenset({source_id, target_id})` (spec: Symmetric
    and multi-edge pairs judged once)."""
    forward = Edge(source_id="a", target_id="b", relation_type="relation1")
    backward = Edge(source_id="b", target_id="a", relation_type="relation2")
    store: GraphStore = _FakeGraphStore([forward, backward])

    pairs, total = contradiction_mod._candidate_pairs(store)

    assert pairs == [("a", "b")]
    assert total == 1


def test_candidate_pairs_multi_edge_same_pair_collapses_to_one() -> None:
    """Two typed edges for the SAME (source, target) row-pair (e.g. two
    different `relation_type`s between the same concepts) still collapse to
    exactly one candidate pair."""
    first = Edge(source_id="a", target_id="b", relation_type="references")
    second = Edge(source_id="a", target_id="b", relation_type="depends_on")
    store: GraphStore = _FakeGraphStore([first, second])

    pairs, total = contradiction_mod._candidate_pairs(store)

    assert pairs == [("a", "b")]
    assert total == 1


def test_candidate_pairs_deterministic_sorted_order() -> None:
    edges = [
        Edge(source_id="z", target_id="a", relation_type="references"),
        Edge(source_id="m", target_id="n", relation_type="references"),
        Edge(source_id="b", target_id="c", relation_type="references"),
    ]
    store: GraphStore = _FakeGraphStore(edges)

    first_run, _ = contradiction_mod._candidate_pairs(store)
    second_run, _ = contradiction_mod._candidate_pairs(store)

    assert first_run == [("a", "z"), ("b", "c"), ("m", "n")]
    assert first_run == second_run


def test_candidate_pairs_over_cap_truncates_to_stable_sorted_prefix() -> None:
    """More than `_MAX_PAIRS` deduped pairs truncates to a stable sorted
    prefix, and the returned total count reflects the FULL deduped set, not
    the capped subset (spec: Cap truncation is reported)."""
    edges = [
        Edge(source_id=f"n{i:04d}", target_id=f"n{i:04d}-b", relation_type="references")
        for i in range(contradiction_mod._MAX_PAIRS + 10)
    ]
    store: GraphStore = _FakeGraphStore(edges)

    pairs, total = contradiction_mod._candidate_pairs(store)

    assert len(pairs) == contradiction_mod._MAX_PAIRS
    assert total == contradiction_mod._MAX_PAIRS + 10
    assert pairs == sorted(pairs)[: contradiction_mod._MAX_PAIRS]


def test_candidate_pairs_on_empty_store_returns_empty_and_zero_total() -> None:
    store: GraphStore = _FakeGraphStore([])

    pairs, total = contradiction_mod._candidate_pairs(store)

    assert pairs == []
    assert total == 0


# ---------------------------------------------------------------------------
# Phase 3: fail-closed parse table tests (Req: Verdict Shape, Citation Gate, Parse)
#
# `contradiction.py`'s own JSON-extraction step now delegates to the shared
# `openkos.llm.parsing.extract_json_object` (gap #8 · S3c hygiene, #1606) --
# these tests exercise that shared helper the same way `_parse_reply` uses
# it, rather than a module-local clone. Full coverage of the helper itself
# lives in `tests/unit/llm/test_parsing.py`.
# ---------------------------------------------------------------------------


def test_extract_json_object_recovers_from_code_fence_and_prose() -> None:
    fenced = '```json\n{"verdict": "consistent"}\n```'
    prose = 'Sure, here you go: {"verdict": "consistent"} thanks!'

    assert parsing.extract_json_object(fenced) == {"verdict": "consistent"}
    assert parsing.extract_json_object(prose) == {"verdict": "consistent"}


def test_extract_json_object_non_string_input_returns_none() -> None:
    assert parsing.extract_json_object(None) is None
    assert parsing.extract_json_object(42) is None


def test_extract_json_object_non_dict_json_returns_none() -> None:
    assert parsing.extract_json_object("[1, 2, 3]") is None


@pytest.mark.parametrize(
    ("raw_verdict", "expected"),
    [
        ("contradicts", contradiction_mod.Verdict.CONTRADICTS),
        ("CONTRADICTS", contradiction_mod.Verdict.CONTRADICTS),
        (" consistent ", contradiction_mod.Verdict.CONSISTENT),
        ("uncertain", contradiction_mod.Verdict.UNCERTAIN),
        ("not-a-verdict", None),
        (42, None),
        (None, None),
    ],
)
def test_map_verdict_table(raw_verdict: object, expected: object) -> None:
    assert contradiction_mod._map_verdict(raw_verdict) == expected


@pytest.mark.parametrize(
    ("raw_confidence", "expected"),
    [
        (0.5, 0.5),
        (0, 0.0),
        (1, 1.0),
        (-5, 0.0),
        (5, 1.0),
        (True, 0.0),
        (False, 0.0),
        (math.nan, 0.0),
        (math.inf, 0.0),
        (-math.inf, 0.0),
        ("0.9", 0.0),
        (None, 0.0),
    ],
)
def test_coerce_confidence_table(raw_confidence: object, expected: float) -> None:
    assert contradiction_mod._coerce_confidence(raw_confidence) == expected


def test_parse_reply_valid_contradicts_with_claims_keeps_verdict() -> None:
    verdict, confidence, rationale, claims = contradiction_mod._parse_reply(
        _valid_reply()
    )

    assert verdict is contradiction_mod.Verdict.CONTRADICTS
    assert confidence == 0.9
    assert rationale == "explicit conflict"
    assert claims == ("A says X", "B says not X")


def test_parse_reply_contradicts_with_empty_claims_degrades_to_uncertain() -> None:
    """Citation-Gated Precision: CONTRADICTS without non-empty
    `conflicting_claims` MUST degrade to UNCERTAIN (spec)."""
    raw = (
        '{"verdict": "contradicts", "confidence": 0.9, "rationale": "no cite", '
        '"conflicting_claims": []}'
    )

    verdict, confidence, _rationale, claims = contradiction_mod._parse_reply(raw)

    assert verdict is contradiction_mod.Verdict.UNCERTAIN
    assert confidence == 0.9
    assert claims == ()


def test_parse_reply_contradicts_with_missing_claims_key_degrades_to_uncertain() -> (
    None
):
    raw = '{"verdict": "contradicts", "confidence": 0.9, "rationale": "no cite"}'

    verdict, _confidence, _rationale, claims = contradiction_mod._parse_reply(raw)

    assert verdict is contradiction_mod.Verdict.UNCERTAIN
    assert claims == ()


def test_parse_reply_unknown_verdict_string_degrades_to_uncertain() -> None:
    raw = '{"verdict": "maybe", "confidence": 0.4, "rationale": "unsure"}'

    verdict, confidence, rationale, _claims = contradiction_mod._parse_reply(raw)

    assert verdict is contradiction_mod.Verdict.UNCERTAIN
    assert confidence == 0.4
    assert rationale == "unsure"


def test_parse_reply_non_json_reply_degrades_to_uncertain_without_raising() -> None:
    raw = "not json at all -- garbage reply"

    verdict, confidence, rationale, claims = contradiction_mod._parse_reply(raw)

    assert verdict is contradiction_mod.Verdict.UNCERTAIN
    assert confidence == 0.0
    assert rationale == contradiction_mod._MALFORMED_REPLY_RATIONALE
    assert claims == ()


def test_parse_reply_non_object_json_reply_degrades_to_uncertain() -> None:
    verdict, confidence, rationale, claims = contradiction_mod._parse_reply("[1, 2]")

    assert verdict is contradiction_mod.Verdict.UNCERTAIN
    assert confidence == 0.0
    assert rationale == contradiction_mod._MALFORMED_REPLY_RATIONALE
    assert claims == ()


def test_parse_reply_non_string_reply_degrades_to_uncertain() -> None:
    """A backend that violates the `-> str` contract (e.g. returns `None`)
    must not crash the parser (fail-closed guard, mirrors
    `adjudication.py`'s equivalent test)."""
    verdict, confidence, rationale, claims = contradiction_mod._parse_reply(None)

    assert verdict is contradiction_mod.Verdict.UNCERTAIN
    assert confidence == 0.0
    assert rationale == contradiction_mod._MALFORMED_REPLY_RATIONALE
    assert claims == ()


def test_parse_reply_non_list_conflicting_claims_coerces_to_empty() -> None:
    raw = (
        '{"verdict": "contradicts", "confidence": 0.8, "rationale": "x", '
        '"conflicting_claims": "not a list"}'
    )

    verdict, _confidence, _rationale, claims = contradiction_mod._parse_reply(raw)

    assert claims == ()
    assert verdict is contradiction_mod.Verdict.UNCERTAIN


def test_parse_reply_non_string_claim_entries_are_dropped() -> None:
    raw = (
        '{"verdict": "contradicts", "confidence": 0.8, "rationale": "x", '
        '"conflicting_claims": ["real claim", 42, null]}'
    )

    _verdict, _confidence, _rationale, claims = contradiction_mod._parse_reply(raw)

    assert claims == ("real claim",)


def test_parse_reply_consistent_verdict_ignores_conflicting_claims_gate() -> None:
    """The citation gate only applies to CONTRADICTS -- a CONSISTENT verdict
    with no claims is kept as CONSISTENT, not forced to UNCERTAIN."""
    raw = '{"verdict": "consistent", "confidence": 0.6, "rationale": "aligned"}'

    verdict, confidence, rationale, claims = contradiction_mod._parse_reply(raw)

    assert verdict is contradiction_mod.Verdict.CONSISTENT
    assert confidence == 0.6
    assert rationale == "aligned"
    assert claims == ()


def test_is_high_confidence_contradiction_public_helper() -> None:
    """`is_high_confidence_contradiction` is the CLI's stable public entry
    point into the display-threshold decision -- keeps
    `_CONFIDENCE_DISPLAY_THRESHOLD` private to this module (no cross-import
    of an underscore constant, mirrors D4's no-private-cross-import rule)."""
    high = contradiction_mod.ContradictionVerdict(
        pair_ids=("a", "b"),
        verdict=contradiction_mod.Verdict.CONTRADICTS,
        confidence=0.9,
        rationale="",
        conflicting_claims=("x",),
    )
    low = contradiction_mod.ContradictionVerdict(
        pair_ids=("a", "b"),
        verdict=contradiction_mod.Verdict.CONTRADICTS,
        confidence=0.5,
        rationale="",
        conflicting_claims=("x",),
    )
    consistent = contradiction_mod.ContradictionVerdict(
        pair_ids=("a", "b"),
        verdict=contradiction_mod.Verdict.CONSISTENT,
        confidence=0.99,
        rationale="",
        conflicting_claims=(),
    )

    assert contradiction_mod.is_high_confidence_contradiction(high) is True
    assert contradiction_mod.is_high_confidence_contradiction(low) is False
    assert contradiction_mod.is_high_confidence_contradiction(consistent) is False


def test_is_high_confidence_contradiction_at_exact_threshold_boundary() -> None:
    """Boundary-coverage regression (reliability review): confidence EXACTLY
    at `_CONFIDENCE_DISPLAY_THRESHOLD` (0.7) IS high-confidence (shown) --
    pins the `>=` operator in `is_high_confidence_contradiction` so a silent
    flip to `>` is caught. `0.6999...` (just below) is NOT high-confidence
    (hidden). Also asserts the constant itself equals 0.7, so a change to
    the constant's value cannot silently pass this test unnoticed."""
    assert contradiction_mod._CONFIDENCE_DISPLAY_THRESHOLD == 0.7

    at_threshold = contradiction_mod.ContradictionVerdict(
        pair_ids=("a", "b"),
        verdict=contradiction_mod.Verdict.CONTRADICTS,
        confidence=contradiction_mod._CONFIDENCE_DISPLAY_THRESHOLD,
        rationale="",
        conflicting_claims=("x",),
    )
    just_below_threshold = contradiction_mod.ContradictionVerdict(
        pair_ids=("a", "b"),
        verdict=contradiction_mod.Verdict.CONTRADICTS,
        confidence=0.6999,
        rationale="",
        conflicting_claims=("x",),
    )

    assert contradiction_mod.is_high_confidence_contradiction(at_threshold) is True
    assert (
        contradiction_mod.is_high_confidence_contradiction(just_below_threshold)
        is False
    )


# ---------------------------------------------------------------------------
# Phase 4: `find_contradictions` orchestration (Req: propagation, empty graph)
# ---------------------------------------------------------------------------


def test_find_contradictions_no_typed_edges_returns_empty_zero_llm_calls(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    (bundle_dir / "concepts" / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\n---\nNo links here.\n", encoding="utf-8"
    )
    llm = _FakeLLM()

    verdicts, total = contradiction_mod.find_contradictions(bundle_dir, llm=llm)

    assert verdicts == []
    assert total == 0
    assert llm.calls == []


def test_find_contradictions_reads_graph_and_judges_one_typed_pair(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    (bundle_dir / "concepts" / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\nsensitivity: private\n"
        "relations:\n  - target: concepts/b\n    type: references\n"
        "---\nA claims the meeting is on Tuesday.\n",
        encoding="utf-8",
    )
    (bundle_dir / "concepts" / "b.md").write_text(
        "---\ntype: Concept\ntitle: B\nsensitivity: private\n---\n"
        "B claims the meeting is on Wednesday.\n",
        encoding="utf-8",
    )
    llm = _FakeLLM(replies=[_valid_reply()])

    verdicts, total = contradiction_mod.find_contradictions(bundle_dir, llm=llm)

    assert total == 1
    assert len(verdicts) == 1
    assert verdicts[0].pair_ids == ("concepts/a", "concepts/b")
    assert verdicts[0].verdict is contradiction_mod.Verdict.CONTRADICTS
    assert len(llm.calls) == 1


def test_find_contradictions_symmetric_edges_judged_exactly_once(
    tmp_path: Path,
) -> None:
    """GIVEN two concepts connected by both A-->B and B-->A typed edges, WHEN
    `find_contradictions` runs, THEN exactly one judgment is produced (spec:
    Symmetric and multi-edge pairs judged once)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    (bundle_dir / "concepts" / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\nsensitivity: private\n"
        "relations:\n  - target: concepts/b\n    type: references\n"
        "---\nBody A.\n",
        encoding="utf-8",
    )
    (bundle_dir / "concepts" / "b.md").write_text(
        "---\ntype: Concept\ntitle: B\nsensitivity: private\n"
        "relations:\n  - target: concepts/a\n    type: related_to\n"
        "---\nBody B.\n",
        encoding="utf-8",
    )
    llm = _FakeLLM(replies=[_valid_reply(verdict="consistent")])

    verdicts, total = contradiction_mod.find_contradictions(bundle_dir, llm=llm)

    assert total == 1
    assert len(verdicts) == 1
    assert len(llm.calls) == 1


def test_find_contradictions_malformed_reply_degrades_only_that_pair(
    tmp_path: Path,
) -> None:
    """One pair's malformed reply degrades only that pair -- the other
    pair's result is unaffected, and neither raises (spec: Malformed reply
    degrades one pair only)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    for name in ("a", "b", "c", "d"):
        (bundle_dir / "concepts" / f"{name}.md").write_text(
            f"---\ntype: Concept\ntitle: {name.upper()}\nsensitivity: private\n---\n"
            f"Body {name}.\n",
            encoding="utf-8",
        )
    (bundle_dir / "concepts" / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\nsensitivity: private\n"
        "relations:\n  - target: concepts/b\n    type: references\n"
        "  - target: concepts/c\n    type: references\n"
        "---\nBody A.\n",
        encoding="utf-8",
    )
    llm = _FakeLLM(
        replies=[
            "not json at all -- garbage reply",
            _valid_reply(verdict="consistent"),
        ]
    )

    verdicts, total = contradiction_mod.find_contradictions(bundle_dir, llm=llm)

    assert total == 2
    assert len(verdicts) == 2
    verdicts_by_target = {v.pair_ids: v for v in verdicts}
    malformed = verdicts_by_target[("concepts/a", "concepts/b")]
    healthy = verdicts_by_target[("concepts/a", "concepts/c")]
    assert malformed.verdict is contradiction_mod.Verdict.UNCERTAIN
    assert malformed.rationale == contradiction_mod._MALFORMED_REPLY_RATIONALE
    assert healthy.verdict is contradiction_mod.Verdict.CONSISTENT


def test_find_contradictions_ollama_error_propagates_unswallowed(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    (bundle_dir / "concepts" / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\nsensitivity: private\n"
        "relations:\n  - target: concepts/b\n    type: references\n"
        "---\nBody A.\n",
        encoding="utf-8",
    )
    (bundle_dir / "concepts" / "b.md").write_text(
        "---\ntype: Concept\ntitle: B\nsensitivity: private\n---\nBody B.\n",
        encoding="utf-8",
    )
    llm = _FakeLLM(error=OllamaUnavailable("not reachable"))

    with pytest.raises(OllamaUnavailable):
        contradiction_mod.find_contradictions(bundle_dir, llm=llm)


def test_load_doc_handles_unreadable_or_missing_document(tmp_path: Path) -> None:
    """A dangling concept id with no document on disk degrades to
    `(concept_id, "")` rather than raising (mirrors
    `edge_typing._load_doc`'s guarded re-read; tested directly on `_load_doc`
    since `build_graph` itself never surfaces an edge whose endpoint fails
    to resolve to a node)."""
    title, body = contradiction_mod._load_doc(tmp_path, "missing-concept")

    assert title == "missing-concept"
    assert body == ""


def test_load_doc_handles_unparseable_frontmatter_document(tmp_path: Path) -> None:
    """A document that exists and is readable, but whose frontmatter fails
    to parse, degrades the same way an unreadable doc does -- `(concept_id,
    "")` -- rather than raising."""
    (tmp_path / "broken.md").write_text(
        "---\ntitle: [unclosed\n---\nbroken frontmatter", encoding="utf-8"
    )

    title, body = contradiction_mod._load_doc(tmp_path, "broken")

    assert title == "broken"
    assert body == ""


def test_find_contradictions_calls_llm_once_per_capped_candidate_pair(
    tmp_path: Path,
) -> None:
    """A pair whose both concept documents ARE readable and resolvable in
    the graph is judged normally -- `llm.chat` is called exactly once for
    the one candidate pair, proving the doc re-read/prompt-build/parse path
    runs end-to-end through the real `build_graph` read."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    (bundle_dir / "concepts" / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\nsensitivity: private\n"
        "relations:\n  - target: concepts/b\n    type: references\n"
        "---\nBody A.\n",
        encoding="utf-8",
    )
    (bundle_dir / "concepts" / "b.md").write_text(
        "---\ntype: Concept\ntitle: B\nsensitivity: private\n---\nBody B.\n",
        encoding="utf-8",
    )
    llm = _FakeLLM(replies=[_valid_reply(verdict="uncertain")])

    verdicts, _total = contradiction_mod.find_contradictions(bundle_dir, llm=llm)

    assert len(verdicts) == 1
    assert len(llm.calls) == 1


def test_find_contradictions_builds_a_two_message_json_only_prompt(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    (bundle_dir / "concepts" / "a.md").write_text(
        "---\ntype: Concept\ntitle: Alpha\nsensitivity: private\n"
        "relations:\n  - target: concepts/b\n    type: references\n"
        "---\nAlpha body text.\n",
        encoding="utf-8",
    )
    (bundle_dir / "concepts" / "b.md").write_text(
        "---\ntype: Concept\ntitle: Beta\nsensitivity: private\n---\nBeta body text.\n",
        encoding="utf-8",
    )
    llm = _FakeLLM(replies=[_valid_reply()])

    contradiction_mod.find_contradictions(bundle_dir, llm=llm)

    assert len(llm.calls) == 1
    messages = llm.calls[0]
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "Alpha" in messages[1]["content"]
    assert "Beta" in messages[1]["content"]
    assert "Alpha body text." in messages[1]["content"]
    assert "Beta body text." in messages[1]["content"]
    assert "references" in messages[1]["content"]
    assert "JSON" in messages[0]["content"]


# ---------------------------------------------------------------------------
# Boundary-coverage regression (reliability review): `_MAX_PAIRS` exact edge
# at the `find_contradictions` orchestration level. A fake `build_graph` is
# injected so exactly `_MAX_PAIRS`/`_MAX_PAIRS + 1` distinct typed-edge pairs
# can be constructed programmatically without writing hundreds of real
# bundle documents on disk -- `_load_doc` degrades missing docs to
# `(concept_id, "")` gracefully, so no doc files are needed for this test.
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _store_context(store: GraphStore) -> Iterator[GraphStore]:
    yield store


def _chain_edges(pair_count: int) -> list[Edge]:
    """`pair_count` typed edges c0000->c0001->...->c{pair_count-1}->c{pair_count},
    each connecting a fresh pair of concepts -- exactly `pair_count` distinct
    deduped candidate pairs, already in sorted order."""
    return [
        Edge(
            source_id=f"c{i:04d}",
            target_id=f"c{i + 1:04d}",
            relation_type="references",
        )
        for i in range(pair_count)
    ]


def test_find_contradictions_at_exact_cap_boundary_all_pairs_judged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EXACTLY `_MAX_PAIRS` (200) distinct candidate pairs: every pair is
    judged and NO cap-reached truncation occurs --
    `total_pair_count == len(verdicts)` (pins the exact boundary itself,
    distinct from the existing `_MAX_PAIRS + 10` over-cap coverage)."""
    max_pairs = contradiction_mod._MAX_PAIRS
    store: GraphStore = _FakeGraphStore(_chain_edges(max_pairs))
    monkeypatch.setattr(
        contradiction_mod, "build_graph", lambda _bundle_dir: _store_context(store)
    )
    llm = _FakeLLM(replies=[_valid_reply(verdict="consistent")] * max_pairs)

    verdicts, total = contradiction_mod.find_contradictions(tmp_path, llm=llm)

    assert total == max_pairs
    assert len(verdicts) == max_pairs
    assert total == len(verdicts)


def test_find_contradictions_one_over_cap_boundary_truncates_and_signals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EXACTLY `_MAX_PAIRS + 1` (201) distinct candidate pairs: the result
    truncates to `_MAX_PAIRS` verdicts and the cap-reached signal is
    detectable via `total_pair_count > len(verdicts)` (pins the off-by-one
    edge at 201, distinct from the existing `_MAX_PAIRS + 10` coverage)."""
    max_pairs = contradiction_mod._MAX_PAIRS
    store: GraphStore = _FakeGraphStore(_chain_edges(max_pairs + 1))
    monkeypatch.setattr(
        contradiction_mod, "build_graph", lambda _bundle_dir: _store_context(store)
    )
    llm = _FakeLLM(replies=[_valid_reply(verdict="consistent")] * max_pairs)

    verdicts, total = contradiction_mod.find_contradictions(tmp_path, llm=llm)

    assert total == max_pairs + 1
    assert len(verdicts) == max_pairs
    assert total > len(verdicts)


# ---------------------------------------------------------------------------
# Phase 3 (status-aware-retrieval, PR3): `include_deprecated` on
# `find_contradictions` -- deprecated/superseded concepts never in a
# candidate pair by default (spec: Superseded concept absent from
# contradiction candidates), `include_deprecated=True` restores them and
# skips the predicate walk (design R1's zero-cost escape), and an all-live
# bundle is unaffected either way.
# ---------------------------------------------------------------------------


def test_pair_touching_a_concept_superseded_by_another_is_excluded_by_default(
    tmp_path: Path,
) -> None:
    """A supersedes edge itself forms a typed-edge candidate pair (a, b);
    since `b` is the TARGET, it is deprecated regardless of its own status,
    so the pair must be dropped and never judged (spec: Superseded concept
    absent from contradiction candidates)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    _write_lifecycle_doc(
        bundle_dir / "concepts" / "a.md",
        title="A",
        relations=[("concepts/b", "supersedes")],
    )
    _write_lifecycle_doc(bundle_dir / "concepts" / "b.md", title="B")
    llm = _FakeLLM()

    verdicts, _total = contradiction_mod.find_contradictions(bundle_dir, llm=llm)

    assert verdicts == []
    assert llm.calls == []


def test_pair_touching_a_concept_with_its_own_deprecated_status_is_excluded(
    tmp_path: Path,
) -> None:
    """A concept deprecated via its OWN `status` field (not a supersedes
    edge) is excluded from candidate pairs the same way."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    _write_lifecycle_doc(
        bundle_dir / "concepts" / "a.md",
        title="A",
        relations=[("concepts/b", "references")],
    )
    _write_lifecycle_doc(
        bundle_dir / "concepts" / "b.md", title="B", status="deprecated"
    )
    llm = _FakeLLM()

    verdicts, _total = contradiction_mod.find_contradictions(bundle_dir, llm=llm)

    assert verdicts == []
    assert llm.calls == []


def test_pair_with_deprecated_concept_as_the_alphabetically_first_element_is_excluded(
    tmp_path: Path,
) -> None:
    """Regression (WARNING, both-sides coverage): the deprecated id may land
    in EITHER `pair[0]` or `pair[1]` after `_pair_key`'s sort. Every other
    exclusion test above happens to put the deprecated concept in `pair[1]`
    (its id sorts after the live side); this fixture pins the deprecated
    concept as the alphabetically FIRST element (`pair[0]`) instead, so a
    regression that only checks `pair[1] in deprecated` is caught."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    _write_lifecycle_doc(
        bundle_dir / "concepts" / "a.md",
        title="A",
        status="deprecated",
        relations=[("concepts/z", "references")],
    )
    _write_lifecycle_doc(bundle_dir / "concepts" / "z.md", title="Z")
    llm = _FakeLLM()

    verdicts, _total = contradiction_mod.find_contradictions(bundle_dir, llm=llm)

    assert verdicts == []
    assert llm.calls == []


def test_live_pair_beyond_cap_index_is_not_starved_by_deprecated_pairs_in_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CONFIRMED HIGH regression: deprecation filtering MUST happen BEFORE
    the `_MAX_PAIRS` cap slice, not after. Construct more than `_MAX_PAIRS`
    deduped pairs where the alphabetically-first `_MAX_PAIRS + 5` pairs all
    touch ONE deprecated concept (so, under a buggy filter-after-cap
    ordering, they would consume every single cap slot) plus exactly one
    LIVE pair sorting well beyond index `_MAX_PAIRS`. The live pair MUST
    still be judged -- the 200-pair budget had ample unused capacity once
    the deprecated-touching pairs are dropped -- and `total_pair_count`
    must reflect the live-only count (1), not the raw pre-filter deduped
    count, so the cap-reached signal (`total_pair_count > len(verdicts)`)
    is never misleadingly triggered by deprecation filtering alone."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    _write_lifecycle_doc(
        bundle_dir / "target_dep.md", title="Target", status="deprecated"
    )
    max_pairs = contradiction_mod._MAX_PAIRS
    dep_pair_count = max_pairs + 5  # > cap, every one touches the deprecated concept
    edges = [
        Edge(
            source_id=f"a{i:04d}",
            target_id="target_dep",
            relation_type="references",
        )
        for i in range(dep_pair_count)
    ]
    edges.append(
        Edge(
            source_id="zzz_live",
            target_id="zzz_live-tgt",
            relation_type="references",
        )
    )
    store: GraphStore = _FakeGraphStore(edges)
    monkeypatch.setattr(
        contradiction_mod, "build_graph", lambda _bundle_dir: _store_context(store)
    )
    llm = _FakeLLM(replies=[_valid_reply(verdict="consistent")])

    verdicts, total = contradiction_mod.find_contradictions(bundle_dir, llm=llm)

    assert len(verdicts) == 1
    assert verdicts[0].pair_ids == ("zzz_live", "zzz_live-tgt")
    assert total == 1
    assert len(llm.calls) == 1


def test_pair_with_both_sides_live_is_still_judged_alongside_a_dropped_pair(
    tmp_path: Path,
) -> None:
    """GIVEN one pair touching a deprecated concept and one pair between two
    live concepts, WHEN `find_contradictions` runs, THEN only the live pair
    is judged -- proving the filter drops selectively, not wholesale."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    _write_lifecycle_doc(
        bundle_dir / "concepts" / "a.md",
        title="A",
        relations=[
            ("concepts/b", "supersedes"),
            ("concepts/c", "references"),
        ],
    )
    _write_lifecycle_doc(bundle_dir / "concepts" / "b.md", title="B")
    _write_lifecycle_doc(bundle_dir / "concepts" / "c.md", title="C")
    llm = _FakeLLM(replies=[_valid_reply(verdict="consistent")])

    verdicts, _total = contradiction_mod.find_contradictions(bundle_dir, llm=llm)

    assert len(verdicts) == 1
    assert verdicts[0].pair_ids == ("concepts/a", "concepts/c")
    assert len(llm.calls) == 1


def test_include_deprecated_true_restores_a_pair_touching_a_superseded_concept(
    tmp_path: Path,
) -> None:
    """`include_deprecated=True` restores a pair that would otherwise be
    dropped, judging it normally (spec: Flag restores a deprecated
    concept)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    _write_lifecycle_doc(
        bundle_dir / "concepts" / "a.md",
        title="A",
        relations=[("concepts/b", "supersedes")],
    )
    _write_lifecycle_doc(bundle_dir / "concepts" / "b.md", title="B")
    llm = _FakeLLM(replies=[_valid_reply(verdict="consistent")])

    verdicts, _total = contradiction_mod.find_contradictions(
        bundle_dir, llm=llm, include_deprecated=True
    )

    assert len(verdicts) == 1
    assert verdicts[0].pair_ids == ("concepts/a", "concepts/b")
    assert len(llm.calls) == 1


def test_include_deprecated_true_never_calls_the_predicate_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`include_deprecated=True` skips `lifecycle.deprecated_concept_ids`
    entirely (spy) -- the escape flag is the zero-cost / status-blind path
    (design R1)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    _write_lifecycle_doc(
        bundle_dir / "concepts" / "a.md",
        title="A",
        relations=[("concepts/b", "supersedes")],
    )
    _write_lifecycle_doc(bundle_dir / "concepts" / "b.md", title="B")
    llm = _FakeLLM(replies=[_valid_reply(verdict="consistent")])
    walk_calls: list[Path] = []
    original_predicate = lifecycle.deprecated_concept_ids

    def _spy_predicate(bundle_dir: Path) -> frozenset[str]:
        walk_calls.append(bundle_dir)
        return original_predicate(bundle_dir)

    monkeypatch.setattr(lifecycle, "deprecated_concept_ids", _spy_predicate)

    contradiction_mod.find_contradictions(bundle_dir, llm=llm, include_deprecated=True)

    assert walk_calls == []


def test_default_include_deprecated_false_calls_the_predicate_walk_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default `include_deprecated=False` DOES call
    `lifecycle.deprecated_concept_ids` exactly once per `find_contradictions`
    call (mirrors `answer()`'s equivalent contract)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    _write_lifecycle_doc(
        bundle_dir / "concepts" / "a.md",
        title="A",
        relations=[("concepts/b", "references")],
    )
    _write_lifecycle_doc(bundle_dir / "concepts" / "b.md", title="B")
    llm = _FakeLLM(replies=[_valid_reply(verdict="consistent")])
    walk_calls: list[Path] = []
    original_predicate = lifecycle.deprecated_concept_ids

    def _spy_predicate(bundle_dir: Path) -> frozenset[str]:
        walk_calls.append(bundle_dir)
        return original_predicate(bundle_dir)

    monkeypatch.setattr(lifecycle, "deprecated_concept_ids", _spy_predicate)

    contradiction_mod.find_contradictions(bundle_dir, llm=llm)

    assert walk_calls == [bundle_dir]


def test_all_live_bundle_is_identical_with_and_without_include_deprecated(
    tmp_path: Path,
) -> None:
    """A bundle where every concept's effective status is live produces the
    identical verdict/total result whether `include_deprecated` is `False`
    (the default) or `True` (spec: All-live bundle is unaffected)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    _write_lifecycle_doc(
        bundle_dir / "concepts" / "a.md",
        title="A",
        relations=[("concepts/b", "references")],
    )
    _write_lifecycle_doc(bundle_dir / "concepts" / "b.md", title="B")

    llm_default = _FakeLLM(replies=[_valid_reply(verdict="consistent")])
    default_result = contradiction_mod.find_contradictions(bundle_dir, llm=llm_default)

    llm_included = _FakeLLM(replies=[_valid_reply(verdict="consistent")])
    included_result = contradiction_mod.find_contradictions(
        bundle_dir, llm=llm_included, include_deprecated=True
    )

    assert default_result == included_result


# ---------------------------------------------------------------------------
# sensitivity-fail-closed-filter, S3a/PR1: confidential concepts excluded
# from contradiction candidates
# ---------------------------------------------------------------------------


def test_pair_touching_a_confidential_concept_is_excluded_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pair where either side is `sensitivity: confidential` never reaches
    `llm.chat` (spec: Confidential excluded from adjudicate/contradictions/
    suggest-relations)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    _write_doc(
        bundle_dir / "concepts" / "a.md",
        title="A",
        sensitivity_value="confidential",
    )
    _write_doc(bundle_dir / "concepts" / "b.md", title="B")
    _write_doc(bundle_dir / "concepts" / "c.md", title="C")
    fake_store: GraphStore = _FakeGraphStore(
        [
            Edge(source_id="concepts/a", target_id="concepts/b", relation_type="rel"),
            Edge(source_id="concepts/b", target_id="concepts/c", relation_type="rel"),
        ]
    )
    monkeypatch.setattr(
        contradiction_mod, "build_graph", lambda _bundle_dir: _store_context(fake_store)
    )
    llm = _FakeLLM(replies=[_valid_reply(verdict="consistent")])

    verdicts, _total = contradiction_mod.find_contradictions(bundle_dir, llm=llm)

    assert len(verdicts) == 1
    assert verdicts[0].pair_ids == ("concepts/b", "concepts/c")
    assert len(llm.calls) == 1


def test_include_confidential_true_restores_the_confidential_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--include-confidential`'s library seam, `include_confidential=True`,
    restores a pair touching a confidential concept, judging it normally
    (spec: `--include-confidential` Escape Flag)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    _write_doc(
        bundle_dir / "concepts" / "a.md",
        title="A",
        sensitivity_value="confidential",
    )
    _write_doc(bundle_dir / "concepts" / "b.md", title="B")
    fake_store: GraphStore = _FakeGraphStore(
        [Edge(source_id="concepts/a", target_id="concepts/b", relation_type="rel")]
    )
    monkeypatch.setattr(
        contradiction_mod, "build_graph", lambda _bundle_dir: _store_context(fake_store)
    )
    llm = _FakeLLM(replies=[_valid_reply(verdict="consistent")])

    verdicts, _total = contradiction_mod.find_contradictions(
        bundle_dir, llm=llm, include_confidential=True
    )

    assert len(verdicts) == 1
    assert verdicts[0].pair_ids == ("concepts/a", "concepts/b")


def test_include_confidential_true_never_calls_the_sensitivity_predicate_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`include_confidential=True` skips `sensitivity.sensitive_concept_ids`
    entirely (spy) -- the escape flag is the zero-cost path (design R1)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    _write_doc(bundle_dir / "concepts" / "a.md", title="A")
    _write_doc(bundle_dir / "concepts" / "b.md", title="B")
    llm = _FakeLLM(replies=[_valid_reply(verdict="consistent")])
    walk_calls: list[Path] = []
    original_predicate = sensitivity.sensitive_concept_ids

    def _spy_predicate(bundle_dir: Path, **kwargs: object) -> frozenset[str]:
        walk_calls.append(bundle_dir)
        return original_predicate(bundle_dir, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(sensitivity, "sensitive_concept_ids", _spy_predicate)

    contradiction_mod.find_contradictions(
        bundle_dir, llm=llm, include_confidential=True
    )

    assert walk_calls == []


def test_default_include_confidential_false_calls_the_sensitivity_predicate_walk_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default `include_confidential=False` DOES call
    `sensitivity.sensitive_concept_ids` exactly once per `find_contradictions`
    call."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    _write_doc(bundle_dir / "concepts" / "a.md", title="A")
    _write_doc(bundle_dir / "concepts" / "b.md", title="B")
    llm = _FakeLLM(replies=[])
    walk_calls: list[Path] = []
    original_predicate = sensitivity.sensitive_concept_ids

    def _spy_predicate(bundle_dir: Path, **kwargs: object) -> frozenset[str]:
        walk_calls.append(bundle_dir)
        return original_predicate(bundle_dir, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(sensitivity, "sensitive_concept_ids", _spy_predicate)

    contradiction_mod.find_contradictions(bundle_dir, llm=llm)

    assert walk_calls == [bundle_dir]


# ---------------------------------------------------------------------------
# directory-walk-observability follow-up: `_load_doc` leak-closure re-check
# ---------------------------------------------------------------------------


def test_load_doc_independently_excludes_a_confidential_doc_the_walk_never_saw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_load_doc` re-checks EACH doc's own frontmatter at re-read time,
    independent of the precomputed confidential-id set built during the
    walk -- a confidential concept the walk silently missed (e.g. an
    unlistable subtree, `okf.py`'s documented `_walk_errors` case) but still
    directly readable by path MUST still be excluded from the `llm.chat`
    prompt (mirrors `retrieval/answer.py`'s `_assemble_context` re-check,
    correction batch FIX 2)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    _write_doc(
        bundle_dir / "concepts" / "a.md",
        title="A",
        body="dichotomyzz confidential note",
        sensitivity_value="confidential",
    )
    _write_doc(
        bundle_dir / "concepts" / "b.md",
        title="B",
        body="dichotomyzz private note",
    )
    fake_store: GraphStore = _FakeGraphStore(
        [Edge(source_id="concepts/a", target_id="concepts/b", relation_type="rel")]
    )
    monkeypatch.setattr(
        contradiction_mod, "build_graph", lambda _bundle_dir: _store_context(fake_store)
    )
    # Simulate the walk missing "concepts/a"'s subtree entirely: the
    # precomputed confidential-id set is empty, so the pair is NOT filtered
    # upstream in `_candidate_pairs` -- the independent per-doc re-check in
    # `_load_doc` is the ONLY thing standing between "concepts/a"'s content
    # and `llm.chat`.
    monkeypatch.setattr(
        sensitivity, "sensitive_concept_ids", lambda *a, **k: frozenset()
    )
    llm = _FakeLLM(replies=[_valid_reply(verdict="consistent")])

    contradiction_mod.find_contradictions(bundle_dir, llm=llm)

    assert len(llm.calls) == 1
    sent_content = " ".join(str(message["content"]) for message in llm.calls[0])
    assert "confidential note" not in sent_content
    assert "private note" in sent_content


def test_include_confidential_true_bypasses_the_load_doc_recheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`include_confidential=True` bypasses the independent `_load_doc`
    re-check too, restoring byte-identical pre-filter behavior (not just at
    the upstream `_candidate_pairs` filter)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    _write_doc(
        bundle_dir / "concepts" / "a.md",
        title="A",
        body="dichotomyzz confidential note",
        sensitivity_value="confidential",
    )
    _write_doc(
        bundle_dir / "concepts" / "b.md",
        title="B",
        body="dichotomyzz private note",
    )
    fake_store: GraphStore = _FakeGraphStore(
        [Edge(source_id="concepts/a", target_id="concepts/b", relation_type="rel")]
    )
    monkeypatch.setattr(
        contradiction_mod, "build_graph", lambda _bundle_dir: _store_context(fake_store)
    )
    llm = _FakeLLM(replies=[_valid_reply(verdict="consistent")])

    contradiction_mod.find_contradictions(
        bundle_dir, llm=llm, include_confidential=True
    )

    assert len(llm.calls) == 1
    sent_content = " ".join(str(message["content"]) for message in llm.calls[0])
    assert "confidential note" in sent_content
