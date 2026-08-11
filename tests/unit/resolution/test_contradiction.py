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
import inspect
import math
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from openkos import lifecycle, sensitivity
from openkos.bundle import ledger
from openkos.graph.base import Edge, GraphStore
from openkos.graph.proximity import ProximityPair
from openkos.graph.sqlite_graph import build_graph
from openkos.llm import parsing
from openkos.llm.base import Message
from openkos.llm.ollama import OllamaGenerationCapped, OllamaUnavailable
from openkos.model import okf
from openkos.resolution import contradiction as contradiction_mod


class _FakeLLM:
    """A structural `LLMBackend`: records every `chat` call, returns queued
    replies in call order. If `error` is set, `chat` raises it instead of
    returning (and does not consume a queued reply); `error_at` narrows the
    raise to the k-th (1-based) call so mid-loop failure can be staged after
    some pairs already succeeded (#441)."""

    def __init__(
        self,
        replies: Sequence[object] = (),
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


def test_contradiction_verdict_merged_absorbed_id_defaults_to_none() -> None:
    """`merged_absorbed_id` (surface-merged-body-contradictions, Correction
    2) defaults to `None` for a typed-edge candidate -- every existing
    construction site that never passes it keeps working unchanged."""
    verdict = contradiction_mod.ContradictionVerdict(
        pair_ids=("a", "b"),
        verdict=contradiction_mod.Verdict.CONSISTENT,
        confidence=0.5,
        rationale="",
        conflicting_claims=(),
    )

    assert verdict.merged_absorbed_id is None


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


def test_candidate_pairs_excludes_derived_from_typed_edges() -> None:
    """A `derived_from`-typed edge -- whether synthesized by graph-projection
    provenance-mirror synthesis or hand-authored in `relations:` frontmatter
    -- is NEVER a contradiction candidate: a derivation is not a peer
    contradiction candidate (spec: "Candidate generation MUST NOT surface
    edges whose relation_type == derived_from"; task 5.1/5.2). A genuine
    typed non-`derived_from` edge is unaffected."""
    derived = Edge(
        source_id="concepts/a", target_id="sources/foo", relation_type="derived_from"
    )
    genuine = Edge(
        source_id="concepts/b", target_id="concepts/c", relation_type="related_to"
    )
    store: GraphStore = _FakeGraphStore([derived, genuine])

    pairs, total = contradiction_mod._candidate_pairs(store)

    assert pairs == [("concepts/b", "concepts/c")]
    assert total == 1


def test_candidate_pairs_excludes_typed_self_loop_edges() -> None:
    """A typed edge whose target IS its source (`concepts/x -> concepts/x`)
    is never a contradiction candidate (issue #411): asking the model whether
    a document contradicts itself loads the same body twice and spends a call
    on a question with no answer.

    Self-loops are reachable -- the `Relation` codec accepts them, only the
    interactive `relate` verb refuses to create one, and
    `okf.merge_relations` deliberately preserves a pre-existing one. So this
    is filtered here rather than assumed impossible upstream."""
    self_loop = Edge(
        source_id="concepts/stoicism",
        target_id="concepts/stoicism",
        relation_type="contradicts",
    )
    store: GraphStore = _FakeGraphStore([self_loop])

    pairs, total = contradiction_mod._candidate_pairs(store)

    assert pairs == []
    assert total == 0


def test_candidate_pairs_self_loop_never_consumes_the_pair_budget() -> None:
    """The self-loop drop happens BEFORE `total_count` is taken, so a
    self-pair neither occupies a `_MAX_PAIRS` slot nor inflates the
    truncation signal (issue #411).

    Counting it in `total_count` would make the "N of M pairs shown (cap
    reached)" notice claim work was truncated that was never judgeable in the
    first place."""
    self_loop = Edge(
        source_id="concepts/a", target_id="concepts/a", relation_type="contradicts"
    )
    genuine = Edge(
        source_id="concepts/b", target_id="concepts/c", relation_type="related_to"
    )
    store: GraphStore = _FakeGraphStore([self_loop, genuine])

    pairs, total = contradiction_mod._candidate_pairs(store, cap=1)

    assert pairs == [("concepts/b", "concepts/c")]
    assert total == 1


def test_candidate_pairs_self_loop_drop_survives_the_uncapped_call() -> None:
    """`find_contradictions` calls this with `cap=None` to merge typed-edge
    candidates with merged-body ones before a single higher-level cap
    (#409 Requirement 3). The self-loop drop must hold on that branch too,
    which returns through a different `return` statement."""
    self_loop = Edge(
        source_id="concepts/a", target_id="concepts/a", relation_type="contradicts"
    )
    store: GraphStore = _FakeGraphStore([self_loop])

    pairs, total = contradiction_mod._candidate_pairs(store, cap=None)

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

    batch, total = contradiction_mod.find_contradictions(bundle_dir, llm=llm)
    verdicts = batch.results

    assert verdicts == []
    assert total == 0
    assert llm.calls == []


def test_find_contradictions_provenance_only_bundle_yields_zero_candidates(
    tmp_path: Path,
) -> None:
    """A bundle whose only typed edges are provenance-mirror edges typed
    `derived_from` by graph projection (concept-to-source links backed by
    `provenance:` frontmatter membership) yields zero candidate pairs and
    zero LLM calls (spec: "Provenance-only bundle yields zero contradiction
    candidates"; task 5.1)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    (bundle_dir / "concepts" / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\nsensitivity: private\n"
        "provenance:\n  - sources/foo\n"
        "---\n[Foo](/sources/foo.md)\n",
        encoding="utf-8",
    )
    (bundle_dir / "sources").mkdir()
    (bundle_dir / "sources" / "foo.md").write_text(
        "---\ntype: Source\ntitle: Foo\nsensitivity: private\n---\nBody.\n",
        encoding="utf-8",
    )
    llm = _FakeLLM()

    batch, total = contradiction_mod.find_contradictions(bundle_dir, llm=llm)
    verdicts = batch.results

    assert verdicts == []
    assert total == 0
    assert llm.calls == []


def test_find_contradictions_excludes_hand_authored_derived_from_relation(
    tmp_path: Path,
) -> None:
    """A `derived_from` edge hand-authored via `relations:` frontmatter (not
    graph-projection-synthesized) is ALSO excluded from candidate pairs --
    the guard is type-based, not origin-based (spec: "hand-authored
    derived_from entry in relations: frontmatter" exclusion; task 5.2)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    (bundle_dir / "concepts" / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\nsensitivity: private\n"
        "relations:\n  - target: concepts/b\n    type: derived_from\n"
        "---\nBody A.\n",
        encoding="utf-8",
    )
    (bundle_dir / "concepts" / "b.md").write_text(
        "---\ntype: Concept\ntitle: B\nsensitivity: private\n---\nBody B.\n",
        encoding="utf-8",
    )
    llm = _FakeLLM()

    batch, total = contradiction_mod.find_contradictions(bundle_dir, llm=llm)
    verdicts = batch.results

    assert verdicts == []
    assert total == 0
    assert llm.calls == []


def test_find_contradictions_with_supplied_store_matches_own_build(
    tmp_path: Path,
) -> None:
    """Supplying an already-open `store` returns the same `(verdicts, total)`
    as letting `find_contradictions` open its own build over the same
    bundle (graph-projection-reuse)."""
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

    with build_graph(bundle_dir) as store:
        supplied_llm = _FakeLLM(replies=[_valid_reply()])
        supplied_result = contradiction_mod.find_contradictions(
            bundle_dir, llm=supplied_llm, store=store
        )

    own_llm = _FakeLLM(replies=[_valid_reply()])
    own_result = contradiction_mod.find_contradictions(bundle_dir, llm=own_llm)

    assert supplied_result == own_result


def test_find_contradictions_does_not_close_supplied_store(tmp_path: Path) -> None:
    """`find_contradictions` must never close a store it did not open itself
    (graph-projection-reuse ownership rule)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    (bundle_dir / "concepts" / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\n---\nNo links here.\n", encoding="utf-8"
    )
    llm = _FakeLLM()

    with build_graph(bundle_dir) as store:
        contradiction_mod.find_contradictions(bundle_dir, llm=llm, store=store)
        store.edges()


def test_find_contradictions_relation_label_prefers_genuine_type_over_derived_from(
    tmp_path: Path,
) -> None:
    """4R-review FIX 1: when a candidate pair has BOTH a genuine typed edge
    (e.g. `related_to`) AND a provenance-mirror-synthesized `derived_from`
    edge to the SAME target, the LLM prompt's `RELATION:` line MUST show the
    genuine type, never `derived_from` -- a derivation is not the relation
    being judged for contradiction, and the pair is only a candidate at all
    because of its non-`derived_from` edge (`_candidate_pairs`'s guard).

    `related_to` is chosen deliberately because it sorts AFTER
    `derived_from` alphabetically (`d` < `r`) -- `store.edges()`'s
    `ORDER BY ... relation_type` would otherwise put `derived_from` FIRST,
    exposing the bug that a type sorting before it (like `depends_on`)
    would mask."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    (bundle_dir / "concepts" / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\nsensitivity: private\n"
        "provenance:\n  - concepts/b\n"
        "relations:\n  - target: concepts/b\n    type: related_to\n"
        "---\nA claims the meeting is on Tuesday. [B](/concepts/b.md)\n",
        encoding="utf-8",
    )
    (bundle_dir / "concepts" / "b.md").write_text(
        "---\ntype: Concept\ntitle: B\nsensitivity: private\n---\n"
        "B claims the meeting is on Wednesday.\n",
        encoding="utf-8",
    )
    llm = _FakeLLM(replies=[_valid_reply()])

    contradiction_mod.find_contradictions(bundle_dir, llm=llm)

    assert len(llm.calls) == 1
    user_message = llm.calls[0][1]["content"]
    relation_line = user_message.splitlines()[0]
    assert relation_line == "RELATION: concepts/a --related_to--> concepts/b"
    assert "derived_from" not in user_message


def test_find_contradictions_genuine_typed_edge_still_surfaced(
    tmp_path: Path,
) -> None:
    """A genuine typed non-`derived_from` edge (e.g. two event concepts
    linked `related_to`) is still surfaced and judged, confirming the
    exclusion applies only to `derived_from`, not to all typed edges (spec:
    "Genuine typed contradiction-eligible edge is still surfaced"; task
    5.3)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    (bundle_dir / "concepts" / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\nsensitivity: private\n"
        "relations:\n  - target: concepts/b\n    type: related_to\n"
        "---\nA claims the meeting is on Tuesday.\n",
        encoding="utf-8",
    )
    (bundle_dir / "concepts" / "b.md").write_text(
        "---\ntype: Concept\ntitle: B\nsensitivity: private\n---\n"
        "B claims the meeting is on Wednesday.\n",
        encoding="utf-8",
    )
    llm = _FakeLLM(replies=[_valid_reply()])

    batch, total = contradiction_mod.find_contradictions(bundle_dir, llm=llm)
    verdicts = batch.results

    assert total == 1
    assert len(verdicts) == 1
    assert verdicts[0].pair_ids == ("concepts/a", "concepts/b")
    assert len(llm.calls) == 1


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

    batch, total = contradiction_mod.find_contradictions(bundle_dir, llm=llm)
    verdicts = batch.results

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

    batch, total = contradiction_mod.find_contradictions(bundle_dir, llm=llm)
    verdicts = batch.results

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

    batch, total = contradiction_mod.find_contradictions(bundle_dir, llm=llm)
    verdicts = batch.results

    assert total == 2
    assert len(verdicts) == 2
    verdicts_by_target = {v.pair_ids: v for v in verdicts}
    malformed = verdicts_by_target[("concepts/a", "concepts/b")]
    healthy = verdicts_by_target[("concepts/a", "concepts/c")]
    assert malformed.verdict is contradiction_mod.Verdict.UNCERTAIN
    assert malformed.rationale == contradiction_mod._MALFORMED_REPLY_RATIONALE
    assert healthy.verdict is contradiction_mod.Verdict.CONSISTENT


# ---------------------------------------------------------------------------
# Requirement: Partial Batch Preserved On Mid-Loop `OllamaError` (#441)
# ---------------------------------------------------------------------------


def _three_pair_bundle(tmp_path: Path) -> Path:
    """Four concepts chained by three typed `references` relations, yielding
    the deterministic candidate order `(a,b) < (b,c) < (c,d)`."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    for name, target in (("a", "b"), ("b", "c"), ("c", "d"), ("d", None)):
        lines = [
            "---",
            "type: Concept",
            f"title: {name.upper()}",
            "sensitivity: private",
        ]
        if target is not None:
            lines += [
                "relations:",
                f"  - target: concepts/{target}",
                "    type: references",
            ]
        lines += ["---", f"Body {name.upper()}."]
        (bundle_dir / "concepts" / f"{name}.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
    return bundle_dir


def test_mid_loop_ollama_error_returns_completed_results_and_failure(
    tmp_path: Path,
) -> None:
    """An `OllamaError`-family raise on the k-th candidate's `llm.chat` stops
    the loop and returns every already-completed verdict in input order, the
    exception instance itself, and the 1-based failed index -- the caller
    paid for k-1 completed calls and keeps all of them (#441). No candidate
    after the failed one is ever prompted; the failed candidate produces no
    verdict and no `on_progress` call."""
    bundle_dir = _three_pair_bundle(tmp_path)
    capped = OllamaGenerationCapped("generation hit the token ceiling")
    llm = _FakeLLM(replies=[_valid_reply()], error=capped, error_at=2)
    seen: list[tuple[int, int, contradiction_mod.ContradictionVerdict]] = []

    def _cb(
        index: int, total: int, verdict: contradiction_mod.ContradictionVerdict
    ) -> None:
        seen.append((index, total, verdict))

    batch, total = contradiction_mod.find_contradictions(
        bundle_dir, llm=llm, on_progress=_cb
    )

    assert isinstance(batch, contradiction_mod.ContradictionBatch)
    assert [v.pair_ids for v in batch.results] == [("concepts/a", "concepts/b")]
    assert batch.results[0].verdict is contradiction_mod.Verdict.CONTRADICTS
    assert batch.failure is capped
    assert batch.failed_index == 2
    # `total_pair_count` keeps its own pre-cap contract on a partial run.
    assert total == 3
    # The failed call itself happened; the candidate AFTER it was never
    # prompted.
    assert len(llm.calls) == 2
    # `on_progress` fired only for completed verdicts -- never for the
    # failure.
    assert [(index, judged) for index, judged, _ in seen] == [(1, 3)]


def test_ollama_error_on_first_pair_returns_empty_results(tmp_path: Path) -> None:
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
    failure = OllamaUnavailable("no local Ollama server reachable")
    llm = _FakeLLM(error=failure, error_at=1)

    batch, total = contradiction_mod.find_contradictions(bundle_dir, llm=llm)

    assert batch.results == []
    assert batch.failure is failure
    assert batch.failed_index == 1
    assert total == 1


def test_complete_run_reports_no_failure(tmp_path: Path) -> None:
    bundle_dir = _three_pair_bundle(tmp_path)
    llm = _FakeLLM(replies=[_valid_reply(), _valid_reply(), _valid_reply()])

    batch, _total = contradiction_mod.find_contradictions(bundle_dir, llm=llm)

    assert batch.failure is None
    assert batch.failed_index is None
    assert len(batch.results) == 3


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

    batch, _total = contradiction_mod.find_contradictions(bundle_dir, llm=llm)
    verdicts = batch.results

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
        contradiction_mod,
        "build_graph",
        lambda _bundle_dir, **_kw: _store_context(store),
    )
    llm = _FakeLLM(replies=[_valid_reply(verdict="consistent")] * max_pairs)

    batch, total = contradiction_mod.find_contradictions(tmp_path, llm=llm)
    verdicts = batch.results

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
        contradiction_mod,
        "build_graph",
        lambda _bundle_dir, **_kw: _store_context(store),
    )
    llm = _FakeLLM(replies=[_valid_reply(verdict="consistent")] * max_pairs)

    batch, total = contradiction_mod.find_contradictions(tmp_path, llm=llm)
    verdicts = batch.results

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

    batch, _total = contradiction_mod.find_contradictions(bundle_dir, llm=llm)
    verdicts = batch.results

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

    batch, _total = contradiction_mod.find_contradictions(bundle_dir, llm=llm)
    verdicts = batch.results

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

    batch, _total = contradiction_mod.find_contradictions(bundle_dir, llm=llm)
    verdicts = batch.results

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
        contradiction_mod,
        "build_graph",
        lambda _bundle_dir, **_kw: _store_context(store),
    )
    llm = _FakeLLM(replies=[_valid_reply(verdict="consistent")])

    batch, total = contradiction_mod.find_contradictions(bundle_dir, llm=llm)
    verdicts = batch.results

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

    batch, _total = contradiction_mod.find_contradictions(bundle_dir, llm=llm)
    verdicts = batch.results

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

    batch, _total = contradiction_mod.find_contradictions(
        bundle_dir, llm=llm, include_deprecated=True
    )
    verdicts = batch.results

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
        contradiction_mod,
        "build_graph",
        lambda _bundle_dir, **_kw: _store_context(fake_store),
    )
    llm = _FakeLLM(replies=[_valid_reply(verdict="consistent")])

    batch, _total = contradiction_mod.find_contradictions(bundle_dir, llm=llm)
    verdicts = batch.results

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
        contradiction_mod,
        "build_graph",
        lambda _bundle_dir, **_kw: _store_context(fake_store),
    )
    llm = _FakeLLM(replies=[_valid_reply(verdict="consistent")])

    batch, _total = contradiction_mod.find_contradictions(
        bundle_dir, llm=llm, include_confidential=True
    )
    verdicts = batch.results

    assert len(verdicts) == 1
    assert verdicts[0].pair_ids == ("concepts/a", "concepts/b")


def test_include_confidential_true_never_calls_the_sensitivity_predicate_walk(
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
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    _write_doc(bundle_dir / "concepts" / "a.md", title="A")
    _write_doc(bundle_dir / "concepts" / "b.md", title="B")
    llm = _FakeLLM(replies=[_valid_reply(verdict="consistent")])
    walk_calls: list[dict[str, object]] = []
    original_predicate = sensitivity.sensitive_concept_ids

    def _spy_predicate(bundle_dir: Path, **kwargs: object) -> frozenset[str]:
        walk_calls.append(kwargs)
        return original_predicate(bundle_dir, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(sensitivity, "sensitive_concept_ids", _spy_predicate)

    contradiction_mod.find_contradictions(
        bundle_dir, llm=llm, include_confidential=True
    )

    assert walk_calls == [{"include_confidential": True, "local_exemption": False}]


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
        contradiction_mod,
        "build_graph",
        lambda _bundle_dir, **_kw: _store_context(fake_store),
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
        contradiction_mod,
        "build_graph",
        lambda _bundle_dir, **_kw: _store_context(fake_store),
    )
    llm = _FakeLLM(replies=[_valid_reply(verdict="consistent")])

    contradiction_mod.find_contradictions(
        bundle_dir, llm=llm, include_confidential=True
    )

    assert len(llm.calls) == 1
    sent_content = " ".join(str(message["content"]) for message in llm.calls[0])
    assert "confidential note" in sent_content


# --- Phase 2.5 (#183): candidate rows never become contradiction pairs -----


class _StubCandidateSource:
    """Stands in for `graph.proximity.VectorProximitySource`."""

    def __init__(self, pairs: list[tuple[str, str]]) -> None:
        self._pairs = pairs

    def pairs(self, concept_ids: Sequence[str]) -> list[ProximityPair]:
        return [
            ProximityPair(source_id=s, target_id=t, distance=0.1)
            for s, t in self._pairs
        ]


def test_proximity_candidate_rows_are_not_contradiction_candidates(
    tmp_path: Path,
) -> None:
    """A pass-3 candidate row is UNTYPED, and `_candidate_pairs` admits only
    typed edges -- so proximity can never manufacture a contradiction
    judgement, exactly as `derived_from` cannot.

    This matters because the two features share one graph: candidate edges
    exist to feed `suggest-relations`, and leaking them into
    `contradictions` would spend an LLM call per merely-similar pair and
    report disagreements between concepts nobody ever claimed were related.
    `_candidate_pairs` is deliberately NOT modified -- its
    `relation_type is not None` test already excludes them."""
    bundle = tmp_path / "bundle"
    _write_doc(bundle / "concepts" / "a.md", title="A")
    _write_doc(bundle / "concepts" / "b.md", title="B")
    source = _StubCandidateSource([("concepts/a", "concepts/b")])
    calls: list[object] = []

    class _RecordingLLM:
        def chat(self, messages: Sequence[Message]) -> str:
            calls.append(messages)
            raise AssertionError("no pair should have been judged")

    batch, total = contradiction_mod.find_contradictions(
        bundle, llm=_RecordingLLM(), candidates=source
    )
    verdicts = batch.results

    assert verdicts == []
    assert total == 0
    assert calls == []


def test_typed_edges_still_judged_when_a_candidate_source_is_present(
    tmp_path: Path,
) -> None:
    """Regression guard: introducing a candidate source must not suppress
    the typed pairs `contradictions` already judged."""
    bundle = tmp_path / "bundle"
    _write_doc(bundle / "concepts" / "b.md", title="B")
    (bundle / "concepts" / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\nsensitivity: private\n"
        "relations:\n  - target: concepts/b\n    type: contradicts\n---\nBody.",
        encoding="utf-8",
    )
    source = _StubCandidateSource([("concepts/a", "concepts/b")])

    class _AgreeLLM:
        def chat(self, messages: Sequence[Message]) -> str:
            return '{"verdict": "no_contradiction", "rationale": "fine"}'

    batch, total = contradiction_mod.find_contradictions(
        bundle, llm=_AgreeLLM(), candidates=source
    )
    verdicts = batch.results

    assert total == 1
    assert len(verdicts) == 1


# ---------------------------------------------------------------------------
# `on_progress` per-pair progress hook (issue #190)
# ---------------------------------------------------------------------------


def test_find_contradictions_invokes_on_progress_once_per_pair_in_order(
    tmp_path: Path,
) -> None:
    """`on_progress` fires once per judged pair, in the deterministic pair
    order, AFTER that pair's `ContradictionVerdict` is built -- carrying
    `(index, total, verdict)` with a 1-based `index` and `total` equal to
    the number of pairs actually judged (post-cap), so a CLI can render a
    per-pair progress line during an otherwise opaque, minutes-long run
    (issue #190, mirroring `suggest_edge_types`'s #134 contract)."""
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
    (bundle_dir / "concepts" / "c.md").write_text(
        "---\ntype: Concept\ntitle: C\nsensitivity: private\n"
        "relations:\n  - target: concepts/d\n    type: references\n"
        "---\nBody C.\n",
        encoding="utf-8",
    )
    (bundle_dir / "concepts" / "d.md").write_text(
        "---\ntype: Concept\ntitle: D\nsensitivity: private\n---\nBody D.\n",
        encoding="utf-8",
    )
    llm = _FakeLLM(replies=[_valid_reply(), _valid_reply(verdict="consistent")])
    seen: list[tuple[int, int, contradiction_mod.ContradictionVerdict]] = []

    def _cb(
        index: int, total: int, verdict: contradiction_mod.ContradictionVerdict
    ) -> None:
        seen.append((index, total, verdict))

    batch, _total_pairs = contradiction_mod.find_contradictions(
        bundle_dir, llm=llm, on_progress=_cb
    )
    verdicts = batch.results

    assert [(index, total) for index, total, _ in seen] == [(1, 2), (2, 2)]
    # Identity, not equality: the callback receives the SAME objects the
    # function returns.
    assert all(
        cb_verdict is verdict
        for (_, _, cb_verdict), verdict in zip(seen, verdicts, strict=True)
    )
    assert [v.pair_ids for _, _, v in seen] == [
        ("concepts/a", "concepts/b"),
        ("concepts/c", "concepts/d"),
    ]


def test_find_contradictions_on_progress_exception_propagates(tmp_path: Path) -> None:
    """An exception raised inside `on_progress` propagates to the caller
    unswallowed -- it is the caller's own callback (issue #190)."""
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
    llm = _FakeLLM(replies=[_valid_reply()])

    def _boom(index: int, total: int, verdict: object) -> None:
        raise RuntimeError("callback failed")

    with pytest.raises(RuntimeError, match="callback failed"):
        contradiction_mod.find_contradictions(bundle_dir, llm=llm, on_progress=_boom)


# ---------------------------------------------------------------------------
# issue #240: the confidential local exemption
# ---------------------------------------------------------------------------


def test_local_exemption_restores_the_confidential_pair_and_its_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a verified-local backend, a pair touching a `confidential`
    concept is judged normally and that concept's real body reaches the
    prompt -- no `--include-confidential` required (#240).

    Asserting on the PROMPT, not just the verdict, is what proves the
    exemption reached `_load_doc` too: the pair-level filter alone would
    still have degraded the body to `""` and produced a verdict about
    nothing."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    _write_doc(
        bundle_dir / "concepts" / "a.md",
        title="A",
        body="Alpha secret body.",
        sensitivity_value="confidential",
    )
    _write_doc(bundle_dir / "concepts" / "b.md", title="B")
    fake_store: GraphStore = _FakeGraphStore(
        [Edge(source_id="concepts/a", target_id="concepts/b", relation_type="rel")]
    )
    monkeypatch.setattr(
        contradiction_mod,
        "build_graph",
        lambda _bundle_dir, **_kw: _store_context(fake_store),
    )
    llm = _FakeLLM(replies=[_valid_reply(verdict="consistent")])

    batch, _total = contradiction_mod.find_contradictions(
        bundle_dir, llm=llm, local_exemption=True
    )
    verdicts = batch.results

    assert [v.pair_ids for v in verdicts] == [("concepts/a", "concepts/b")]
    (message,) = [m for m in llm.calls[0] if m["role"] == "user"]
    assert "Alpha secret body." in message["content"]


def test_local_exemption_defaults_to_false_on_find_contradictions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`find_contradictions` defaults `local_exemption` to `False` (#240)."""
    assert (
        inspect.signature(contradiction_mod.find_contradictions)
        .parameters["local_exemption"]
        .default
        is False
    )

    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    _write_doc(
        bundle_dir / "concepts" / "a.md", title="A", sensitivity_value="confidential"
    )
    _write_doc(bundle_dir / "concepts" / "b.md", title="B")
    fake_store: GraphStore = _FakeGraphStore(
        [Edge(source_id="concepts/a", target_id="concepts/b", relation_type="rel")]
    )
    monkeypatch.setattr(
        contradiction_mod,
        "build_graph",
        lambda _bundle_dir, **_kw: _store_context(fake_store),
    )
    llm = _FakeLLM()

    batch, _total = contradiction_mod.find_contradictions(bundle_dir, llm=llm)
    verdicts = batch.results

    assert verdicts == []
    assert llm.calls == []


# ---------------------------------------------------------------------------
# surface-merged-body-contradictions (issue #409, detection half): intra-
# document candidates built from `merged_from`. A merge stacks the absorbed
# body under a `## Merged content` heading, which hides the disagreement
# from the typed-edge candidate walk above -- these candidates pair
# `entry.survivor_before`'s body against `entry.absorbed_snapshot`'s body,
# recovered deterministically from the survivor's own ledger alone.
# ---------------------------------------------------------------------------


def test_ledger_suffix_constant_matches_bundle_ledger_module() -> None:
    """`contradiction_mod._LEDGER_SUFFIX` is a deliberate, layering-forced
    duplicate of `bundle.ledger.LEDGER_SUFFIX` (resolution may import
    `openkos.model.okf` read-only, never `openkos.bundle` -- see
    `contradiction_mod._LEDGER_SUFFIX`'s docstring). This parity test is the
    guard that keeps the duplicate from drifting, mirroring `vcs/git.py`'s
    `_identity`/`test_scrub_snippet_parity.py` precedent."""
    assert contradiction_mod._LEDGER_SUFFIX == ledger.LEDGER_SUFFIX


def _write_survivor_with_merges(
    path: Path,
    *,
    title: str = "Survivor",
    body: str = "Current body.",
    sensitivity_value: str | None = "private",
    entries: Sequence[okf.MergeLedgerEntry] = (),
) -> None:
    """Write a concept `.md` file and -- when `entries` is non-empty -- its
    ledger sidecar under `bundle/.state/ledger/`, via the real
    `ledger.write_entries` primitive (design: "Relocate the merge ledger to
    `bundle/.state/ledger/`"; PR#1). The concept's OWN frontmatter never
    carries `merged_from` any more -- entries live only in the sidecar."""
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata: dict[str, object] = {"type": "Concept", "title": title}
    if sensitivity_value is not None:
        metadata["sensitivity"] = sensitivity_value
    path.write_text(okf.dump_frontmatter(metadata, body), encoding="utf-8")
    if entries:
        bundle_dir = path.parents[1]
        survivor_id = okf.concept_id_for(path, bundle_dir)
        ledger.write_entries(
            survivor_id,
            bundle_dir,
            survivor_id=survivor_id,
            entries=list(entries),
        )


def _ledger_entry(
    *,
    absorbed_id: str = "concepts/absorbed",
    survivor_before_body: str = "Survivor before.",
    absorbed_body: str = "Absorbed body.",
    survivor_title: str = "Survivor",
    absorbed_title: str = "Absorbed",
    sensitivity_before: str = "private",
    sensitivity_after: str = "private",
    merged_at: str = "2026-01-01T00:00:00Z",
) -> okf.MergeLedgerEntry:
    """`MergeLedgerEntry` fixture whose `survivor_before`/`absorbed_snapshot`
    are real, parseable `.md` document texts -- both bodies an intra-
    document candidate must recover deterministically from the ledger
    alone."""
    return okf.MergeLedgerEntry(
        schema=okf.MERGE_LEDGER_SCHEMA_V3,
        merged_at=merged_at,
        absorbed_id=absorbed_id,
        absorbed_snapshot=okf.dump_frontmatter(
            {"type": "Concept", "title": absorbed_title}, absorbed_body
        ),
        survivor_before=okf.dump_frontmatter(
            {"type": "Concept", "title": survivor_title}, survivor_before_body
        ),
        index_before="",
        log_before="",
        link_rewrites=[],
        sensitivity_before=sensitivity_before,
        sensitivity_after=sensitivity_after,
        relation_rewrites=[],
        provenance_rewrites=[],
    )


def test_merged_body_candidates_builds_one_candidate_per_ledger_entry(
    tmp_path: Path,
) -> None:
    """One `MergeLedgerEntry` on a survivor yields exactly one intra-document
    candidate, pairing that entry's `survivor_before`/`absorbed_snapshot`
    bodies."""
    bundle_dir = tmp_path / "bundle"
    entry = _ledger_entry(absorbed_id="concepts/apatheia-2")
    _write_survivor_with_merges(
        bundle_dir / "concepts" / "apatheia.md", entries=[entry]
    )

    candidates = contradiction_mod._merged_body_candidates(bundle_dir, frozenset())

    assert len(candidates) == 1
    (candidate,) = candidates
    assert candidate.pair_ids == ("concepts/apatheia", "concepts/apatheia")
    assert candidate.merge_entry == entry


def test_merged_body_candidates_nested_merges_are_linear_not_quadratic(
    tmp_path: Path,
) -> None:
    """N ledger entries on one survivor produce exactly N candidates --
    `merged_from` is a flat LIFO list, so no recursion, and never the
    fully-stacked current body compared against every fragment nor
    all-pairs-of-fragments."""
    bundle_dir = tmp_path / "bundle"
    entries = [_ledger_entry(absorbed_id=f"concepts/absorbed-{i}") for i in range(5)]
    _write_survivor_with_merges(
        bundle_dir / "concepts" / "survivor.md", entries=entries
    )

    candidates = contradiction_mod._merged_body_candidates(bundle_dir, frozenset())

    assert len(candidates) == 5
    absorbed_ids = [c.merge_entry.absorbed_id for c in candidates if c.merge_entry]
    assert absorbed_ids == [f"concepts/absorbed-{i}" for i in range(5)]


def test_merged_body_candidates_no_merges_yields_none(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    _write_survivor_with_merges(bundle_dir / "concepts" / "plain.md", entries=[])

    candidates = contradiction_mod._merged_body_candidates(bundle_dir, frozenset())

    assert candidates == []


def test_merged_body_candidates_excludes_deprecated_survivor(tmp_path: Path) -> None:
    """A deprecated/superseded survivor contributes no merged-body
    candidates, mirroring `_candidate_pairs`'s own deprecation treatment."""
    bundle_dir = tmp_path / "bundle"
    entry = _ledger_entry()
    _write_survivor_with_merges(
        bundle_dir / "concepts" / "survivor.md", entries=[entry]
    )

    candidates = contradiction_mod._merged_body_candidates(
        bundle_dir, frozenset({"concepts/survivor"})
    )

    assert candidates == []


def test_merged_body_candidates_corrupt_ledger_degrades_that_survivor_only(
    tmp_path: Path,
) -> None:
    """A survivor whose ledger SIDECAR is corrupt (non-list `merged_from`)
    contributes zero candidates for THAT survivor rather than raising or
    aborting the whole walk -- a sibling survivor with a valid ledger is
    unaffected."""
    bundle_dir = tmp_path / "bundle"
    (bundle_dir / "concepts").mkdir(parents=True)
    (bundle_dir / "concepts" / "corrupt.md").write_text(
        "---\ntype: Concept\ntitle: Corrupt\n---\nBody.\n",
        encoding="utf-8",
    )
    corrupt_ledger_path = ledger.ledger_path_for("concepts/corrupt", bundle_dir)
    corrupt_ledger_path.parent.mkdir(parents=True, exist_ok=True)
    corrupt_ledger_path.write_text(
        "---\nschema: openkos.merge_ledger_sidecar/v1\n"
        "survivor_id: concepts/corrupt\nmerged_from: not-a-list\n---\n",
        encoding="utf-8",
    )
    entry = _ledger_entry()
    _write_survivor_with_merges(bundle_dir / "concepts" / "clean.md", entries=[entry])

    candidates = contradiction_mod._merged_body_candidates(bundle_dir, frozenset())

    assert len(candidates) == 1
    assert candidates[0].pair_ids == ("concepts/clean", "concepts/clean")


def test_find_contradictions_judges_merged_body_candidate_end_to_end(
    tmp_path: Path,
) -> None:
    """A survivor with one ledger entry and no typed edges at all still
    yields exactly one judged verdict, carrying `merged_absorbed_id`."""
    bundle_dir = tmp_path / "bundle"
    entry = _ledger_entry(
        absorbed_id="concepts/apatheia-2",
        survivor_before_body=(
            "Apatheia is freedom from the pathe, the destructive passions."
        ),
        absorbed_body=(
            "Apatheia is the Stoic goal of emotional indifference, freedom "
            "from passion."
        ),
    )
    _write_survivor_with_merges(
        bundle_dir / "concepts" / "apatheia.md",
        body="Apatheia is freedom from the pathe, the destructive passions.",
        entries=[entry],
    )
    llm = _FakeLLM(replies=[_valid_reply(verdict="contradicts")])

    batch, total = contradiction_mod.find_contradictions(bundle_dir, llm=llm)
    verdicts = batch.results

    assert total == 1
    assert len(verdicts) == 1
    (verdict,) = verdicts
    assert verdict.pair_ids == ("concepts/apatheia", "concepts/apatheia")
    assert verdict.merged_absorbed_id == "concepts/apatheia-2"
    assert verdict.verdict is contradiction_mod.Verdict.CONTRADICTS
    (message,) = [m for m in llm.calls[0] if m["role"] == "user"]
    assert "freedom from the pathe" in message["content"]
    assert "emotional indifference" in message["content"]


# ---------------------------------------------------------------------------
# A typed self-loop and a merged-body candidate on the SAME id (#411 + #409).
#
# Before #411 a typed self-loop reached candidate generation, so a `(x, x)`
# `pair_ids` had two possible origins and `merged_absorbed_id` was the only
# thing telling them apart. #411 removes the self-loop origin, which must NOT
# collaterally suppress the merged-body candidate on the same id.
#
# The renderer-level invariant -- every renderer branches on
# `merged_absorbed_id is not None`, never on `pair_ids` shape -- is guarded
# independently, and without a generation path, by
# `tests/unit/cli/test_contradictions.py::
# test_contradictions_renders_merged_body_verdict_distinctly_from_pair_verdict`.
# ---------------------------------------------------------------------------


def test_self_loop_is_dropped_without_suppressing_a_merged_candidate_on_that_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hand-authored typed self-loop on `concepts/x` and a merged-body
    candidate on the SAME `concepts/x` in the same run yield exactly ONE
    verdict: the merged-body one.

    The self-loop contributes nothing (#411), and the drop is targeted --
    filtering by `source_id == target_id` at the typed-edge step must not
    reach the intra-document candidates, which legitimately carry
    `pair_ids == (x, x)`."""
    bundle_dir = tmp_path / "bundle"
    entry = _ledger_entry(absorbed_id="concepts/absorbed")
    _write_survivor_with_merges(
        bundle_dir / "concepts" / "x.md",
        title="X",
        entries=[entry],
    )
    self_loop_edge = Edge(
        source_id="concepts/x", target_id="concepts/x", relation_type="related_to"
    )
    fake_store: GraphStore = _FakeGraphStore([self_loop_edge])
    monkeypatch.setattr(
        contradiction_mod,
        "build_graph",
        lambda _bundle_dir, **_kw: _store_context(fake_store),
    )
    llm = _FakeLLM(replies=[_valid_reply(verdict="consistent")])

    batch, total = contradiction_mod.find_contradictions(bundle_dir, llm=llm)
    verdicts = batch.results

    assert total == 1
    (verdict,) = verdicts
    assert verdict.pair_ids == ("concepts/x", "concepts/x")
    assert verdict.merged_absorbed_id == "concepts/absorbed"
    # One call, not two: the self-loop never reached the model.
    assert len(llm.calls) == 1


# ---------------------------------------------------------------------------
# `_MAX_PAIRS` over a MIXED candidate list (Requirement 3): typed-edge and
# merged-body candidates merge into ONE ordered list before a SINGLE cap
# slice -- one `total_pair_count`, one truncation signal, never a second
# parallel cap.
# ---------------------------------------------------------------------------


def test_mixed_candidate_list_shares_a_single_cap_and_truncation_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    max_pairs = contradiction_mod._MAX_PAIRS
    edge_count = max_pairs - 2
    merged_count = 5  # pushes the combined total 3 past the cap
    bundle_dir = tmp_path / "bundle"
    (bundle_dir / "concepts").mkdir(parents=True)
    for i in range(merged_count):
        _write_survivor_with_merges(
            bundle_dir / "concepts" / f"survivor{i:04d}.md",
            entries=[_ledger_entry(absorbed_id=f"concepts/absorbed{i:04d}")],
        )
    store: GraphStore = _FakeGraphStore(_chain_edges(edge_count))
    monkeypatch.setattr(
        contradiction_mod,
        "build_graph",
        lambda _bundle_dir, **_kw: _store_context(store),
    )
    llm = _FakeLLM(replies=[_valid_reply(verdict="consistent")] * max_pairs)

    batch, total = contradiction_mod.find_contradictions(bundle_dir, llm=llm)
    verdicts = batch.results

    assert total == edge_count + merged_count
    assert len(verdicts) == max_pairs
    assert total > len(verdicts)


def test_mixed_candidate_list_under_cap_judges_every_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_dir = tmp_path / "bundle"
    (bundle_dir / "concepts").mkdir(parents=True)
    _write_survivor_with_merges(
        bundle_dir / "concepts" / "survivor.md",
        entries=[_ledger_entry()],
    )
    store: GraphStore = _FakeGraphStore(_chain_edges(3))
    monkeypatch.setattr(
        contradiction_mod,
        "build_graph",
        lambda _bundle_dir, **_kw: _store_context(store),
    )
    llm = _FakeLLM(replies=[_valid_reply(verdict="consistent")] * 4)

    batch, total = contradiction_mod.find_contradictions(bundle_dir, llm=llm)
    verdicts = batch.results

    assert total == 4
    assert len(verdicts) == 4


# ---------------------------------------------------------------------------
# The ledger-aware sensitivity gate wired into `find_contradictions`
# (Correction 1): a candidate whose `sensitivity_before`/`sensitivity_after`
# is confidential stays blocked even after the survivor's CURRENT value was
# lowered via `set-sensitivity --allow-downgrade`.
# ---------------------------------------------------------------------------


def test_merged_candidate_stays_blocked_after_survivor_downgraded(
    tmp_path: Path,
) -> None:
    """The whole point of Correction 1: a survivor absorbed a confidential
    document (frozen in the ledger at merge time), then was later downgraded
    to `public` via `set-sensitivity --allow-downgrade`. The CURRENT on-disk
    frontmatter now reads `public`, but the ledger-aware gate must still
    block -- proven by asserting the body NEVER reaches the LLM prompt."""
    bundle_dir = tmp_path / "bundle"
    entry = _ledger_entry(
        absorbed_id="concepts/secret-source",
        survivor_before_body="Secret pre-merge body.",
        absorbed_body="Secret absorbed body.",
        sensitivity_before="confidential",
        sensitivity_after="confidential",
    )
    _write_survivor_with_merges(
        bundle_dir / "concepts" / "survivor.md",
        body="Current public body.",
        sensitivity_value="public",  # downgraded via --allow-downgrade
        entries=[entry],
    )
    llm = _FakeLLM(replies=[_valid_reply(verdict="uncertain")])

    batch, _total = contradiction_mod.find_contradictions(bundle_dir, llm=llm)
    verdicts = batch.results

    assert len(verdicts) == 1
    (message,) = [m for m in llm.calls[0] if m["role"] == "user"]
    assert "Secret pre-merge body." not in message["content"]
    assert "Secret absorbed body." not in message["content"]


def test_merged_candidate_include_confidential_restores_the_blocked_body(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    entry = _ledger_entry(
        absorbed_id="concepts/secret-source",
        survivor_before_body="Secret pre-merge body.",
        absorbed_body="Secret absorbed body.",
        sensitivity_before="confidential",
        sensitivity_after="confidential",
    )
    _write_survivor_with_merges(
        bundle_dir / "concepts" / "survivor.md",
        body="Current public body.",
        sensitivity_value="public",
        entries=[entry],
    )
    llm = _FakeLLM(replies=[_valid_reply(verdict="uncertain")])

    batch, _total = contradiction_mod.find_contradictions(
        bundle_dir, llm=llm, include_confidential=True
    )
    verdicts = batch.results

    assert len(verdicts) == 1
    (message,) = [m for m in llm.calls[0] if m["role"] == "user"]
    assert "Secret pre-merge body." in message["content"]
    assert "Secret absorbed body." in message["content"]


# ---------------------------------------------------------------------------
# `_load_ledger_bodies` -- the ledger-body sibling of `_load_doc`
# (Requirement 4): same degrade-on-parse-failure contract, parsing via
# `okf.load_frontmatter` instead of reading a path, calling the new
# ledger-aware gate instead of `should_block`.
# ---------------------------------------------------------------------------


def test_load_ledger_bodies_returns_both_bodies_when_unblocked(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    entry = _ledger_entry(
        absorbed_id="concepts/absorbed",
        survivor_before_body="Before body.",
        absorbed_body="Absorbed body.",
    )
    _write_survivor_with_merges(
        bundle_dir / "concepts" / "survivor.md", entries=[entry]
    )

    before_doc, absorbed_doc = contradiction_mod._load_ledger_bodies(
        bundle_dir, "concepts/survivor", entry
    )

    assert before_doc == ("Survivor", "Before body.")
    assert absorbed_doc == ("Absorbed", "Absorbed body.")


def test_load_ledger_bodies_drops_previously_absorbed_sections_from_before_body(
    tmp_path: Path,
) -> None:
    """`build_merged_document` APPENDS, so a later entry's `survivor_before`
    already embeds every earlier absorbed body. Sending that accumulated body
    is quadratic in cost across a survivor's ledger AND attributes an earlier
    candidate's disagreement to this one, so the before-body is cut at the
    first `## Merged content (` heading.

    Found by this change's 4R review; the exploration's linearity analysis
    covered candidate COUNT only, never per-candidate payload size."""
    accumulated = (
        "Own content at this point.\n\n"
        "## Merged content (concepts/absorbed-earlier)\n\n"
        "Earlier absorbed body that belongs to an earlier candidate."
    )
    entry = _ledger_entry(
        absorbed_id="concepts/absorbed",
        survivor_before_body=accumulated,
        absorbed_body="Absorbed body.",
    )
    bundle_dir = tmp_path / "bundle"
    _write_survivor_with_merges(
        bundle_dir / "concepts" / "survivor.md", entries=[entry]
    )

    before_doc, absorbed_doc = contradiction_mod._load_ledger_bodies(
        bundle_dir, "concepts/survivor", entry
    )

    assert before_doc == ("Survivor", "Own content at this point.")
    assert "Earlier absorbed body" not in before_doc[1]
    assert "concepts/absorbed-earlier" not in before_doc[1]
    assert absorbed_doc == ("Absorbed", "Absorbed body.")


def test_own_body_before_merge_returns_a_first_merge_body_unchanged() -> None:
    """A survivor's FIRST merge has no prior `## Merged content (` section,
    so nothing is cut -- the split must not truncate ordinary content."""
    body = "Ordinary body mentioning merged content in prose, unheaded."

    assert contradiction_mod._own_body_before_merge(body) == body


def test_load_ledger_bodies_degrades_only_the_unparseable_side(
    tmp_path: Path,
) -> None:
    """A ledger string that fails `okf.load_frontmatter` degrades ONLY its
    own side; the healthy side is still sent, so one corrupt snapshot does
    not silently discard a judgeable half. The review flagged both broad
    `except` branches as reachable but unasserted."""
    entry = _ledger_entry(absorbed_id="concepts/absorbed")
    broken = dataclasses.replace(
        entry, survivor_before="---\nthis: [is not: valid yaml\n---\nbody"
    )
    bundle_dir = tmp_path / "bundle"
    _write_survivor_with_merges(
        bundle_dir / "concepts" / "survivor.md", entries=[broken]
    )

    before_doc, absorbed_doc = contradiction_mod._load_ledger_bodies(
        bundle_dir, "concepts/survivor", broken
    )

    assert before_doc == ("concepts/survivor", "")
    assert absorbed_doc == ("Absorbed", "Absorbed body.")


def test_load_ledger_bodies_degrades_when_survivor_current_doc_missing(
    tmp_path: Path,
) -> None:
    """No current on-disk survivor document (dangling/deleted) degrades both
    bodies to `(id, "")`, exactly like `_load_doc`'s own missing-document
    contract -- fail-closed, since the current sensitivity cannot be read."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    entry = _ledger_entry(absorbed_id="concepts/absorbed")

    before_doc, absorbed_doc = contradiction_mod._load_ledger_bodies(
        bundle_dir, "concepts/missing-survivor", entry
    )

    assert before_doc == ("concepts/missing-survivor", "")
    assert absorbed_doc == ("concepts/absorbed", "")


def test_merged_content_blocked_called_once_per_ledger_entry_not_per_survivor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`merged_content_blocked` MUST be invoked once PER ledger entry read
    from a survivor's `bundle/.state/ledger/` sidecar, never once per
    survivor (sensitivity-aware-llm delta: "Per-Entry Merged-Content Gate,
    Never Per-Survivor"). A hoisted per-survivor call would still let
    `find_contradictions` complete, so this asserts on invocation COUNT and
    per-entry ARGUMENTS, not merely on the final verdicts."""
    bundle_dir = tmp_path / "bundle"
    entries = [_ledger_entry(absorbed_id=f"concepts/absorbed-{i}") for i in range(3)]
    _write_survivor_with_merges(
        bundle_dir / "concepts" / "survivor.md", entries=entries
    )

    seen_entries: list[okf.MergeLedgerEntry] = []
    real_gate = sensitivity.merged_content_blocked

    def _spy(
        current_sensitivity: object, entry: okf.MergeLedgerEntry, **kwargs: object
    ) -> bool:
        seen_entries.append(entry)
        return real_gate(current_sensitivity, entry, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(sensitivity, "merged_content_blocked", _spy)
    llm = _FakeLLM(replies=[_valid_reply(verdict="consistent")] * 3)

    contradiction_mod.find_contradictions(bundle_dir, llm=llm)

    assert len(seen_entries) == 3, (
        "merged_content_blocked must be called once PER ledger entry -- "
        f"got {len(seen_entries)} call(s) for 3 entries"
    )
    assert {entry.absorbed_id for entry in seen_entries} == {
        "concepts/absorbed-0",
        "concepts/absorbed-1",
        "concepts/absorbed-2",
    }


def test_load_ledger_bodies_degrades_when_gate_blocks(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    entry = _ledger_entry(
        absorbed_id="concepts/absorbed",
        survivor_before_body="Before body.",
        absorbed_body="Absorbed body.",
        sensitivity_before="confidential",
        sensitivity_after="confidential",
    )
    _write_survivor_with_merges(
        bundle_dir / "concepts" / "survivor.md",
        sensitivity_value="public",
        entries=[entry],
    )

    before_doc, absorbed_doc = contradiction_mod._load_ledger_bodies(
        bundle_dir, "concepts/survivor", entry
    )

    assert before_doc == ("concepts/survivor", "")
    assert absorbed_doc == ("concepts/absorbed", "")


# ---------------------------------------------------------------------------
# `_MERGED_BODY_FLOOR` (issue #444): typed-edge candidates were unconditionally
# first in the single `_MAX_PAIRS` budget, so a corpus whose live typed-edge
# pairs alone reach the cap judged ZERO merged-body candidates -- #409's
# detection went silent in exactly the busy, long-lived corpora it exists for.
#
# The cap stays single and shared (#409 Requirement 3). Only the ORDER changes:
# merged-body candidates get a reserved floor of slots.
# ---------------------------------------------------------------------------


def _merged_bundle(bundle_dir: Path, count: int) -> None:
    """`count` survivors, one merge-ledger entry each -- `count` merged-body
    candidates."""
    (bundle_dir / "concepts").mkdir(parents=True, exist_ok=True)
    for i in range(count):
        _write_survivor_with_merges(
            bundle_dir / "concepts" / f"survivor{i:04d}.md",
            entries=[_ledger_entry(absorbed_id=f"concepts/absorbed{i:04d}")],
        )


def _plan_for(
    bundle_dir: Path, *, edges: int, merges: int
) -> contradiction_mod.CandidatePlan:
    _merged_bundle(bundle_dir, merges)
    store: GraphStore = _FakeGraphStore(_chain_edges(edges))
    return contradiction_mod.plan_candidates(bundle_dir, store=store)


def _kinds(plan: contradiction_mod.CandidatePlan) -> tuple[int, int]:
    """`(typed_edge_judged, merged_body_judged)` in the planned list."""
    merged = sum(1 for s in plan.specs if s.merge_entry is not None)
    return len(plan.specs) - merged, merged


def test_merged_body_candidates_survive_a_typed_edge_saturated_budget(
    tmp_path: Path,
) -> None:
    """Typed-edge pairs alone overflow the cap. Every merged-body candidate is
    still judged, because the floor reserves its slots before typed edges fill
    the budget (issue #444)."""
    plan = _plan_for(tmp_path / "bundle", edges=contradiction_mod._MAX_PAIRS, merges=5)

    edge_judged, merged_judged = _kinds(plan)

    assert merged_judged == 5, "the whole point: merged-body detection stays alive"
    assert edge_judged == contradiction_mod._MAX_PAIRS - 5
    assert plan.llm_calls == contradiction_mod._MAX_PAIRS


def test_merged_body_floor_is_a_guarantee_not_a_ceiling(tmp_path: Path) -> None:
    """When typed edges do not fill the budget, merged-body candidates take
    every remaining slot -- far past the floor. The floor sets a minimum, never
    a maximum (issue #444)."""
    floor = contradiction_mod._MERGED_BODY_FLOOR
    # More merged-body candidates than the whole budget, so the only limit on
    # what they take is what typed edges leave behind.
    plan = _plan_for(
        tmp_path / "bundle", edges=10, merges=contradiction_mod._MAX_PAIRS + 50
    )

    edge_judged, merged_judged = _kinds(plan)

    assert edge_judged == 10, "typed edges are never starved to honour the floor"
    assert merged_judged == contradiction_mod._MAX_PAIRS - 10
    assert merged_judged > floor


def test_merged_body_floor_reserves_nothing_when_the_budget_is_not_reached(
    tmp_path: Path,
) -> None:
    """Under the cap, the floor is inert: every candidate of both kinds is
    judged and nothing is reserved or dropped (issue #444)."""
    plan = _plan_for(tmp_path / "bundle", edges=3, merges=2)

    assert _kinds(plan) == (3, 2)
    assert plan.llm_calls == 5
    assert plan.total_count == 5


def test_typed_edges_keep_the_budget_they_do_not_owe_the_floor(
    tmp_path: Path,
) -> None:
    """The floor only ever reserves as many slots as there are merged-body
    candidates to fill it -- 2 merges reserve 2 slots, not
    `_MERGED_BODY_FLOOR` (issue #444). A fixed reservation would silently cost
    typed-edge coverage on every corpus with few merges."""
    plan = _plan_for(tmp_path / "bundle", edges=contradiction_mod._MAX_PAIRS, merges=2)

    edge_judged, merged_judged = _kinds(plan)

    assert merged_judged == 2
    assert edge_judged == contradiction_mod._MAX_PAIRS - 2


def _plan(
    *, edge_total: int, merged_total: int, judged_edges: int, judged_merged: int
) -> contradiction_mod.CandidatePlan:
    specs = [
        contradiction_mod._CandidateSpec(
            pair_ids=(f"c{i:04d}", f"c{i + 1:04d}"), relation_type="references"
        )
        for i in range(judged_edges)
    ] + [
        contradiction_mod._CandidateSpec(
            pair_ids=(f"s{i:04d}", f"s{i:04d}"),
            relation_type=None,
            merge_entry=_ledger_entry(absorbed_id=f"concepts/absorbed{i:04d}"),
        )
        for i in range(judged_merged)
    ]
    return contradiction_mod.CandidatePlan(
        specs=tuple(specs), edge_total=edge_total, merged_total=merged_total
    )


def test_truncation_notice_is_none_when_nothing_was_dropped() -> None:
    plan = _plan(edge_total=3, merged_total=2, judged_edges=3, judged_merged=2)

    assert contradiction_mod.contradiction_truncation_notice(plan) is None


def test_truncation_notice_names_only_the_kind_that_was_dropped() -> None:
    """The whole point of #444's reporting half: an operator over the cap can
    tell WHICH kind went unjudged. A notice that named both unconditionally,
    or neither, would not answer that."""
    edges_only = _plan(
        edge_total=210, merged_total=4, judged_edges=196, judged_merged=4
    )
    merged_only = _plan(
        edge_total=150, merged_total=90, judged_edges=150, judged_merged=50
    )

    edges_notice = contradiction_mod.contradiction_truncation_notice(edges_only)
    merged_notice = contradiction_mod.contradiction_truncation_notice(merged_only)

    assert edges_notice is not None
    assert "14 typed-edge" in edges_notice
    assert "merged-body" not in edges_notice

    assert merged_notice is not None
    assert "40 merged-body" in merged_notice
    assert "typed-edge" not in merged_notice


def test_truncation_notice_reports_both_kinds_and_the_shared_total() -> None:
    plan = _plan(edge_total=180, merged_total=60, judged_edges=150, judged_merged=50)

    notice = contradiction_mod.contradiction_truncation_notice(plan)

    assert notice == (
        "200 of 240 candidate(s) shown (cap reached); "
        "dropped: 30 typed-edge, 10 merged-body"
    )
