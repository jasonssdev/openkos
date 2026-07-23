"""Unit tests for `resolution/contradiction.py`: the config-free LLM
contradiction-detection precision leaf over graph typed-edge pairs (MVP-2
slice 3, freshness-lint-v1 S3).

All tests use a `tmp_path` bundle and a reply-QUEUE fake `LLMBackend`
(records `.calls`, returns queued replies in call order) -- zero network,
zero real Ollama process. Mirrors `_FakeLLM` in
`tests/unit/resolution/test_edge_typing.py` /
`tests/unit/resolution/test_adjudication.py`.
"""

import dataclasses
import math
from collections.abc import Sequence
from pathlib import Path

import pytest

from openkos.graph.base import Edge, GraphStore
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


def _write_doc(path: Path, *, title: str = "Stub", body: str = "Body.") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: Concept\ntitle: {title}\n---\n{body}", encoding="utf-8"
    )


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
# ---------------------------------------------------------------------------


def test_extract_json_object_recovers_from_code_fence_and_prose() -> None:
    fenced = '```json\n{"verdict": "consistent"}\n```'
    prose = 'Sure, here you go: {"verdict": "consistent"} thanks!'

    assert contradiction_mod._extract_json_object(fenced) == {"verdict": "consistent"}
    assert contradiction_mod._extract_json_object(prose) == {"verdict": "consistent"}


def test_extract_json_object_non_string_input_returns_none() -> None:
    assert contradiction_mod._extract_json_object(None) is None
    assert contradiction_mod._extract_json_object(42) is None


def test_extract_json_object_non_dict_json_returns_none() -> None:
    assert contradiction_mod._extract_json_object("[1, 2, 3]") is None


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
        "---\ntype: Concept\ntitle: A\n"
        "relations:\n  - target: concepts/b\n    type: references\n"
        "---\nA claims the meeting is on Tuesday.\n",
        encoding="utf-8",
    )
    (bundle_dir / "concepts" / "b.md").write_text(
        "---\ntype: Concept\ntitle: B\n---\nB claims the meeting is on Wednesday.\n",
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
        "---\ntype: Concept\ntitle: A\n"
        "relations:\n  - target: concepts/b\n    type: references\n"
        "---\nBody A.\n",
        encoding="utf-8",
    )
    (bundle_dir / "concepts" / "b.md").write_text(
        "---\ntype: Concept\ntitle: B\n"
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
            f"---\ntype: Concept\ntitle: {name.upper()}\n---\nBody {name}.\n",
            encoding="utf-8",
        )
    (bundle_dir / "concepts" / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\n"
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
        "---\ntype: Concept\ntitle: A\n"
        "relations:\n  - target: concepts/b\n    type: references\n"
        "---\nBody A.\n",
        encoding="utf-8",
    )
    (bundle_dir / "concepts" / "b.md").write_text(
        "---\ntype: Concept\ntitle: B\n---\nBody B.\n", encoding="utf-8"
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
        "---\ntype: Concept\ntitle: A\n"
        "relations:\n  - target: concepts/b\n    type: references\n"
        "---\nBody A.\n",
        encoding="utf-8",
    )
    (bundle_dir / "concepts" / "b.md").write_text(
        "---\ntype: Concept\ntitle: B\n---\nBody B.\n", encoding="utf-8"
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
        "---\ntype: Concept\ntitle: Alpha\n"
        "relations:\n  - target: concepts/b\n    type: references\n"
        "---\nAlpha body text.\n",
        encoding="utf-8",
    )
    (bundle_dir / "concepts" / "b.md").write_text(
        "---\ntype: Concept\ntitle: Beta\n---\nBeta body text.\n", encoding="utf-8"
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
