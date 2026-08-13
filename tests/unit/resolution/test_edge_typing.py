"""Unit tests for `resolution/edge_typing.py`: the config-free LLM
relation-type suggestion leaf over untyped body-link edges from the derived
graph projection (MVP-2 slice 2b).

All tests use a `tmp_path` bundle and a reply-QUEUE fake `LLMBackend`
(records `.calls`, returns queued replies in call order) -- zero network,
zero real Ollama process. Mirrors `_FakeLLM` in
`tests/unit/resolution/test_adjudication.py`.
"""

import inspect
from collections.abc import Sequence
from pathlib import Path

import pytest

from openkos.graph.base import Edge, GraphStore
from openkos.graph.proximity import ProximityPair
from openkos.graph.sqlite_graph import CandidateReport, build_graph
from openkos.llm.base import Message
from openkos.llm.ollama import OllamaGenerationCapped, OllamaUnavailable
from openkos.resolution import edge_typing as edge_typing_mod


class _FakeLLM:
    """A structural `LLMBackend`: records every `chat` call, returns queued
    replies in call order. If `error` is set, `chat` raises it instead of
    returning (and does not consume a queued reply); `error_at` narrows the
    raise to the k-th (1-based) call so mid-loop failure can be staged after
    some edges already succeeded (#441)."""

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
        raise NotImplementedError("not exercised by untyped_edges")


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


def _valid_reply(rel_type: str = "references", rationale: str = "mentions it") -> str:
    return f'{{"type": "{rel_type}", "rationale": "{rationale}"}}'


# ---------------------------------------------------------------------------
# Phase 1: `EdgeSuggestion` + `untyped_edges`
# ---------------------------------------------------------------------------


def test_untyped_edges_returns_only_edges_with_relation_type_none() -> None:
    typed = Edge(source_id="a", target_id="b", relation_type="references")
    untyped_one = Edge(source_id="c", target_id="d")
    untyped_two = Edge(source_id="e", target_id="f")
    store: GraphStore = _FakeGraphStore([typed, untyped_one, untyped_two])

    result = edge_typing_mod.untyped_edges(store)

    assert result == [untyped_one, untyped_two]


def test_untyped_edges_on_empty_store_returns_empty_list() -> None:
    store: GraphStore = _FakeGraphStore([])

    assert edge_typing_mod.untyped_edges(store) == []


def test_untyped_edges_preserves_store_order_deterministically() -> None:
    first = Edge(source_id="a", target_id="b")
    second = Edge(source_id="b", target_id="c")
    store: GraphStore = _FakeGraphStore([first, second])

    assert edge_typing_mod.untyped_edges(store) == [first, second]


def test_untyped_edges_does_not_exclude_already_typed_pairs_row_level_only() -> None:
    """`untyped_edges` is a ROW-level filter only -- it does NOT exclude an
    untyped edge whose `(source, target)` pair also has a separate typed edge
    row. Pair-level exclusion lives in `suggest_relations`'s candidate
    selection (`_candidate_edges`), not here (docstring correction)."""
    typed = Edge(source_id="a", target_id="b", relation_type="references")
    untyped_same_pair = Edge(source_id="a", target_id="b")
    store: GraphStore = _FakeGraphStore([typed, untyped_same_pair])

    result = edge_typing_mod.untyped_edges(store)

    assert result == [untyped_same_pair]


def test_edge_suggestion_carries_edge_suggested_type_and_rationale() -> None:
    edge = Edge(source_id="a", target_id="b")

    suggestion = edge_typing_mod.EdgeSuggestion(
        edge=edge, suggested_type="references", rationale="mentions it"
    )

    assert suggestion.edge == edge
    assert suggestion.suggested_type == "references"
    assert suggestion.rationale == "mentions it"


def test_edge_suggestion_is_frozen() -> None:
    import dataclasses

    suggestion = edge_typing_mod.EdgeSuggestion(
        edge=Edge(source_id="a", target_id="b"), suggested_type=None, rationale=""
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        suggestion.rationale = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Phase 2: `suggest_edge_types` (fail-closed LLM leaf)
# ---------------------------------------------------------------------------


def test_suggest_edge_types_returns_one_suggestion_per_input_edge_same_order(
    tmp_path: Path,
) -> None:
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    _write_doc(tmp_path / "c.md", title="C")
    edges = [
        Edge(source_id="a", target_id="b"),
        Edge(source_id="b", target_id="c"),
    ]
    llm = _FakeLLM(
        replies=[
            _valid_reply("references", "a refs b"),
            _valid_reply("depends_on", "b depends on c"),
        ]
    )

    result = edge_typing_mod.suggest_edge_types(
        edges, bundle_dir=tmp_path, llm=llm
    ).results

    assert len(result) == 2
    assert [s.edge for s in result] == edges
    assert result[0].suggested_type == "references"
    assert result[0].rationale == "a refs b"
    assert result[1].suggested_type == "depends_on"
    assert result[1].rationale == "b depends on c"
    assert len(llm.calls) == 2


def test_suggest_edge_types_malformed_reply_degrades_only_that_edge(
    tmp_path: Path,
) -> None:
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    _write_doc(tmp_path / "c.md", title="C")
    _write_doc(tmp_path / "d.md", title="D")
    _write_doc(tmp_path / "e.md", title="E")
    edges = [
        Edge(source_id="a", target_id="b"),
        Edge(source_id="c", target_id="d"),
        Edge(source_id="d", target_id="e"),
        Edge(source_id="e", target_id="a"),
        Edge(source_id="b", target_id="c"),
    ]
    llm = _FakeLLM(
        replies=[
            _valid_reply("references"),
            "not json at all -- garbage reply",
            _valid_reply("depends_on"),
            _valid_reply("related_to"),
            _valid_reply("part_of"),
        ]
    )

    result = edge_typing_mod.suggest_edge_types(
        edges, bundle_dir=tmp_path, llm=llm
    ).results

    assert len(result) == 5
    assert result[0].suggested_type == "references"
    assert result[1].suggested_type is None
    assert result[2].suggested_type == "depends_on"
    assert result[3].suggested_type == "related_to"
    assert result[4].suggested_type == "part_of"


def test_suggest_edge_types_invalid_type_degrades_to_none_never_valid(
    tmp_path: Path,
) -> None:
    """A reply whose `type` field fails `validate_relation_type` (blank
    after stripping) degrades to `suggested_type=None`, never surfaced as a
    valid suggestion."""
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    edges = [Edge(source_id="a", target_id="b")]
    llm = _FakeLLM(replies=[_valid_reply("   ", "blank type")])

    result = edge_typing_mod.suggest_edge_types(
        edges, bundle_dir=tmp_path, llm=llm
    ).results

    assert len(result) == 1
    assert result[0].suggested_type is None


def test_suggest_edge_types_handles_unreadable_source_or_target_doc(
    tmp_path: Path,
) -> None:
    """Neither `a` nor `b` has a document on disk (dangling ids) -- the
    guarded doc re-read degrades to `(concept_id, "")` rather than raising,
    and `llm.chat` is still called exactly once for the edge."""
    edges = [Edge(source_id="missing-a", target_id="missing-b")]
    llm = _FakeLLM(replies=[_valid_reply("references", "best guess")])

    result = edge_typing_mod.suggest_edge_types(
        edges, bundle_dir=tmp_path, llm=llm
    ).results

    assert len(result) == 1
    assert result[0].suggested_type == "references"
    assert len(llm.calls) == 1


def test_suggest_edge_types_handles_unparseable_frontmatter_doc(
    tmp_path: Path,
) -> None:
    """A document that exists and is readable, but whose frontmatter fails
    to parse, degrades the same way an unreadable doc does -- `(concept_id,
    "")` -- rather than raising."""
    (tmp_path / "a.md").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "a.md").write_text(
        "---\ntitle: [unclosed\n---\nbroken frontmatter", encoding="utf-8"
    )
    _write_doc(tmp_path / "b.md", title="B")
    edges = [Edge(source_id="a", target_id="b")]
    llm = _FakeLLM(replies=[_valid_reply("references", "still tries")])

    result = edge_typing_mod.suggest_edge_types(
        edges, bundle_dir=tmp_path, llm=llm
    ).results

    assert len(result) == 1
    assert result[0].suggested_type == "references"


def test_suggest_edge_types_non_string_type_field_degrades_to_none(
    tmp_path: Path,
) -> None:
    """A reply whose `type` field is present but not a string (e.g. a
    number) degrades to `suggested_type=None`, keeping the rationale."""
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    edges = [Edge(source_id="a", target_id="b")]
    llm = _FakeLLM(replies=['{"type": 42, "rationale": "numeric type"}'])

    result = edge_typing_mod.suggest_edge_types(
        edges, bundle_dir=tmp_path, llm=llm
    ).results

    assert len(result) == 1
    assert result[0].suggested_type is None
    assert result[0].rationale == "numeric type"


def test_suggest_edge_types_non_string_reply_degrades_to_none(tmp_path: Path) -> None:
    """A backend that violates the `-> str` contract (e.g. returns `None`)
    must not crash the parser (fail-closed: `_extract_json_object`'s
    non-string guard, mirrors `adjudication.py`'s own equivalent test)."""
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    edges = [Edge(source_id="a", target_id="b")]
    llm = _FakeLLM(replies=[None])  # type: ignore[list-item]

    result = edge_typing_mod.suggest_edge_types(
        edges, bundle_dir=tmp_path, llm=llm
    ).results

    assert len(result) == 1
    assert result[0].suggested_type is None
    assert result[0].rationale == edge_typing_mod._MALFORMED_REPLY_RATIONALE


def test_suggest_edge_types_reply_that_is_valid_json_but_not_an_object_degrades(
    tmp_path: Path,
) -> None:
    """A reply that parses as valid JSON but is not a `dict` (e.g. a bare
    JSON array) is treated the same as an unparseable reply -- degrades to
    `suggested_type=None`, never crashes."""
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    edges = [Edge(source_id="a", target_id="b")]
    llm = _FakeLLM(replies=["[1, 2, 3]"])

    result = edge_typing_mod.suggest_edge_types(
        edges, bundle_dir=tmp_path, llm=llm
    ).results

    assert len(result) == 1
    assert result[0].suggested_type is None
    assert result[0].rationale == edge_typing_mod._MALFORMED_REPLY_RATIONALE


def test_suggest_edge_types_degrade_with_blank_rationale_falls_back_to_stable_text(
    tmp_path: Path,
) -> None:
    """A reply that fails `validate_relation_type` (blank `type` after
    stripping) AND omits `rationale` entirely must not surface a blank
    rationale on the degrade path (`EdgeSuggestion.rationale` docstring:
    "never blank on the fail-closed degrade paths")."""
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    edges = [Edge(source_id="a", target_id="b")]
    llm = _FakeLLM(replies=['{"type": "   "}'])

    result = edge_typing_mod.suggest_edge_types(
        edges, bundle_dir=tmp_path, llm=llm
    ).results

    assert len(result) == 1
    assert result[0].suggested_type is None
    assert result[0].rationale.strip() != ""


def test_suggest_edge_types_non_string_type_with_blank_rationale_falls_back(
    tmp_path: Path,
) -> None:
    """Same invariant as above, on the OTHER degrade branch: `type` present
    but not a string, and `rationale` present but blank."""
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    edges = [Edge(source_id="a", target_id="b")]
    llm = _FakeLLM(replies=['{"type": 42, "rationale": "   "}'])

    result = edge_typing_mod.suggest_edge_types(
        edges, bundle_dir=tmp_path, llm=llm
    ).results

    assert len(result) == 1
    assert result[0].suggested_type is None
    assert result[0].rationale.strip() != ""


def test_suggest_edge_types_builds_a_two_message_json_only_prompt(
    tmp_path: Path,
) -> None:
    _write_doc(tmp_path / "a.md", title="Alpha", body="Alpha body text.")
    _write_doc(tmp_path / "b.md", title="Beta", body="Beta body text.")
    edges = [Edge(source_id="a", target_id="b")]
    llm = _FakeLLM(replies=[_valid_reply()])

    edge_typing_mod.suggest_edge_types(edges, bundle_dir=tmp_path, llm=llm)

    assert len(llm.calls) == 1
    messages = llm.calls[0]
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "Alpha" in messages[1]["content"]
    assert "Beta" in messages[1]["content"]
    assert "JSON" in messages[0]["content"]


def test_system_prompt_constrains_to_the_seeded_relation_vocabulary(
    tmp_path: Path,
) -> None:
    """The rubric must name every SUGGESTABLE relation type so the model picks
    from the closed set instead of inventing out-of-vocab verbs (issue #134:
    the model overwhelmingly proposed `source_of`/`describes`/... none of
    which are valid `relate` inputs).

    It must also NOT name the engine-owned ones (#380): offering
    `derived_from` invites the model to use it colloquially ("builds upon"),
    and the result is indistinguishable from the provenance edges the engine
    synthesizes at projection."""
    from openkos.model.relations import (
        ENGINE_OWNED_RELATION_TYPES,
        SUGGESTABLE_RELATION_TYPES,
    )

    _write_doc(tmp_path / "a.md", title="Alpha", body="Alpha body.")
    _write_doc(tmp_path / "b.md", title="Beta", body="Beta body.")
    edges = [Edge(source_id="a", target_id="b")]
    llm = _FakeLLM(replies=[_valid_reply()])

    edge_typing_mod.suggest_edge_types(edges, bundle_dir=tmp_path, llm=llm)

    system = llm.calls[0][0]["content"]
    for suggestable in SUGGESTABLE_RELATION_TYPES:
        assert suggestable in system
    for engine_owned in ENGINE_OWNED_RELATION_TYPES:
        assert engine_owned not in system


def test_every_suggestable_type_carries_a_definition_in_the_rubric() -> None:
    """#388: the prompt listed seven bare NAMES and no definitions, while
    `extraction/concept.py`'s prompt defines all nine of its types and gives a
    tie-break chain. A model handed `caused_by, depends_on, member_of, ...`
    with no meanings has nothing to discriminate WITH, so it falls to the
    explicit `related_to` escape -- 67% of the accepted edges in the measured
    bundle.

    Keys must equal the suggestable vocabulary EXACTLY. A type added to
    `REGISTRY` without a definition here would otherwise reach the model as a
    bare name again, silently reintroducing the defect."""
    from openkos.model.relations import SUGGESTABLE_RELATION_TYPES

    assert set(edge_typing_mod._RELATION_RUBRIC) == set(SUGGESTABLE_RELATION_TYPES)
    for definition in edge_typing_mod._RELATION_RUBRIC.values():
        assert definition.strip()


def test_the_rubric_never_defines_an_engine_owned_type() -> None:
    """Defining `derived_from` would put it back in front of the model in
    everything but name, undoing #380."""
    from openkos.model.relations import ENGINE_OWNED_RELATION_TYPES

    for engine_owned in ENGINE_OWNED_RELATION_TYPES:
        assert engine_owned not in edge_typing_mod._RELATION_RUBRIC


def test_the_prompt_states_each_type_with_its_definition(tmp_path: Path) -> None:
    """The rubric has to actually reach the model, not merely exist."""
    _write_doc(tmp_path / "a.md", title="A", body="x")
    _write_doc(tmp_path / "b.md", title="B", body="y")
    llm = _FakeLLM(replies=[_valid_reply()])

    edge_typing_mod.suggest_edge_types(
        [Edge(source_id="a", target_id="b")], bundle_dir=tmp_path, llm=llm
    )

    system = llm.calls[0][0]["content"]
    for name, definition in edge_typing_mod._RELATION_RUBRIC.items():
        assert name in system
        assert definition in system


def test_the_prompt_protects_the_honest_fallback(tmp_path: Path) -> None:
    """The goal is NOT to drive `related_to` down.

    Forcing a specific type where the documents do not support one would trade
    an honest "related" for a confident lie, and a knowledge graph traversed on
    false precision is worse than one traversed on admitted vagueness. The
    rubric must therefore still tell the model to choose `related_to` when
    nothing fits -- what changes is that it now has definitions to decide
    AGAINST, instead of seven bare words."""
    _write_doc(tmp_path / "a.md", title="A", body="x")
    _write_doc(tmp_path / "b.md", title="B", body="y")
    llm = _FakeLLM(replies=[_valid_reply()])

    edge_typing_mod.suggest_edge_types(
        [Edge(source_id="a", target_id="b")], bundle_dir=tmp_path, llm=llm
    )

    system = llm.calls[0][0]["content"].lower()
    assert "related_to" in system
    assert "do not guess" in system


def test_engine_owned_type_is_rejected_even_when_the_model_proposes_it(
    tmp_path: Path,
) -> None:
    """#380: the prompt no longer offers `derived_from`, and that alone is not
    the fix.

    Prompt wording is not an enforcement mechanism at this tier -- the same
    lesson `_drop_source_title_twins` was built on, where a clause forbidding
    a shape made that shape MORE frequent via priming. A model that emits
    `derived_from` anyway must be refused here, deterministically, or the
    corruption #380 reports still reaches the operator's accept prompt."""
    _write_doc(tmp_path / "a.md", title="A", body="x")
    _write_doc(tmp_path / "b.md", title="B", body="y")
    edges = [Edge(source_id="a", target_id="b")]
    llm = _FakeLLM(
        replies=[_valid_reply("derived_from", "It builds upon the origin of MCP.")]
    )

    result = edge_typing_mod.suggest_edge_types(
        edges, bundle_dir=tmp_path, llm=llm
    ).results

    assert result[0].suggested_type is None
    assert "provenance" in result[0].rationale


def test_rejecting_an_engine_owned_type_does_not_echo_the_models_reasoning(
    tmp_path: Path,
) -> None:
    """The rationale on this degrade is OURS, not the model's.

    #380's evidence is a rationale that reads plausibly and is factually
    wrong: "derived from the MCP Origin Date as it builds upon the origin of
    MCP". Surfacing that sentence next to a refusal would hand the operator
    the very argument that produced the corruption."""
    _write_doc(tmp_path / "a.md", title="A", body="x")
    _write_doc(tmp_path / "b.md", title="B", body="y")
    edges = [Edge(source_id="a", target_id="b")]
    llm = _FakeLLM(replies=[_valid_reply("derived_from", "it builds upon the origin")])

    result = edge_typing_mod.suggest_edge_types(
        edges, bundle_dir=tmp_path, llm=llm
    ).results

    assert "builds upon" not in result[0].rationale


def test_out_of_vocab_suggestion_is_kept_but_prints_no_advisory_note(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An out-of-vocab suggested type is still returned (open vocabulary),
    but on this PREVIEW path it must NOT print the per-type "not a seeded
    relation type" note -- that note flooded stderr, one line per edge
    (issue #134). The note belongs to the `relate` write path only."""
    _write_doc(tmp_path / "a.md", title="A", body="x")
    _write_doc(tmp_path / "b.md", title="B", body="y")
    edges = [Edge(source_id="a", target_id="b")]
    llm = _FakeLLM(replies=[_valid_reply("source_of", "provenance edge")])

    result = edge_typing_mod.suggest_edge_types(
        edges, bundle_dir=tmp_path, llm=llm
    ).results

    assert result[0].suggested_type == "source_of"
    assert "is not a seeded relation type" not in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Phase 3.5: Pair-level candidate exclusion (`_candidate_edges`) -- fixes the
# CRITICAL forever-re-suggested bug: `untyped_edges` alone only excludes
# already-typed ROWS, not already-typed PAIRS, so an untyped body-link edge
# and a `relations:`-typed edge for the SAME (source, target) pair coexisted
# as two distinct rows and `suggest-relations` re-suggested the accepted
# pair forever.
# ---------------------------------------------------------------------------


def test_candidate_edges_excludes_untyped_edge_whose_pair_has_a_typed_edge() -> None:
    """A pair (a, b) has BOTH a typed edge (as written by `relate`) AND a
    coexisting untyped body-link row for the SAME pair -- the candidate set
    must exclude the untyped row so an accepted suggestion is never
    re-suggested. An unrelated untyped-only pair stays included."""
    typed = Edge(source_id="a", target_id="b", relation_type="references")
    untyped_same_pair = Edge(source_id="a", target_id="b")
    untyped_other_pair = Edge(source_id="c", target_id="d")
    store: GraphStore = _FakeGraphStore([typed, untyped_same_pair, untyped_other_pair])

    result = edge_typing_mod._candidate_edges(store)

    assert result == [untyped_other_pair]


def test_candidate_edges_includes_untyped_edge_with_no_typed_counterpart() -> None:
    untyped_only = Edge(source_id="c", target_id="d")
    store: GraphStore = _FakeGraphStore([untyped_only])

    result = edge_typing_mod._candidate_edges(store)

    assert result == [untyped_only]


def test_candidate_edges_keeps_different_pairs_typed_excluded_untyped_included() -> (
    None
):
    """Regression: the ordinary case (typed and untyped edges on DIFFERENT
    pairs) that `untyped_edges` already handled correctly stays correct
    through `_candidate_edges`."""
    typed = Edge(source_id="a", target_id="b", relation_type="references")
    untyped = Edge(source_id="c", target_id="d")
    store: GraphStore = _FakeGraphStore([typed, untyped])

    result = edge_typing_mod._candidate_edges(store)

    assert result == [untyped]


def test_candidate_edges_returns_untyped_pairs_without_calling_an_llm(
    tmp_path: Path,
) -> None:
    """`candidate_edges` (issue #134 preview surface) opens `build_graph`
    and returns the exact candidate set `suggest_relations` would type --
    the untyped body-link `a -> c`, with the `relations:`-typed `a -> b`
    excluded -- taking NO `LLMBackend` and issuing NO inference, so a caller
    can bound the one-call-per-edge cost before committing to it."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    (bundle_dir / "concepts" / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\nsensitivity: private\n"
        "relations:\n  - target: concepts/b\n    type: references\n"
        "---\nSee [C](/concepts/c.md) for more.\n",
        encoding="utf-8",
    )
    (bundle_dir / "concepts" / "b.md").write_text(
        "---\ntype: Concept\ntitle: B\nsensitivity: private\n---\nBody.\n",
        encoding="utf-8",
    )
    (bundle_dir / "concepts" / "c.md").write_text(
        "---\ntype: Concept\ntitle: C\nsensitivity: private\n---\nBody.\n",
        encoding="utf-8",
    )

    edges = edge_typing_mod.candidate_edges(bundle_dir)

    assert [(e.source_id, e.target_id) for e in edges] == [("concepts/a", "concepts/c")]


def test_candidate_edges_with_supplied_store_matches_own_build(tmp_path: Path) -> None:
    """Supplying an already-open `store` returns the same candidate list as
    letting `candidate_edges` open its own build over the same bundle
    (graph-projection-reuse)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "a.md",
        title="A",
        body="See [C](/concepts/c.md).\n",
    )
    _write_doc(bundle_dir / "concepts" / "c.md", title="C")

    with build_graph(bundle_dir) as store:
        supplied_result = edge_typing_mod.candidate_edges(bundle_dir, store=store)

    assert supplied_result == edge_typing_mod.candidate_edges(bundle_dir)


def test_candidate_edges_does_not_close_supplied_store(tmp_path: Path) -> None:
    """`candidate_edges` must never close a store it did not open itself
    (graph-projection-reuse ownership rule)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="A")

    with build_graph(bundle_dir) as store:
        edge_typing_mod.candidate_edges(bundle_dir, store=store)
        store.edges()


def test_candidate_edges_ignores_bundle_walk_when_store_supplied(
    tmp_path: Path,
) -> None:
    """When `store` is supplied, `candidate_edges` must compute over THAT
    store's projection, not re-walk `bundle_dir` -- passing bundle B's path
    while supplying a store built from bundle A returns A's edges."""
    bundle_a = tmp_path / "bundle_a"
    _write_doc(
        bundle_a / "concepts" / "a.md",
        title="A",
        body="See [C](/concepts/c.md).\n",
    )
    _write_doc(bundle_a / "concepts" / "c.md", title="C")

    bundle_b = tmp_path / "bundle_b"
    bundle_b.mkdir()

    with build_graph(bundle_a) as store:
        result = edge_typing_mod.candidate_edges(bundle_b, store=store)

    assert [(e.source_id, e.target_id) for e in result] == [
        ("concepts/a", "concepts/c")
    ]


# ---------------------------------------------------------------------------
# Provenance-mirror exclusion (#135): confirms NO code change is needed --
# `derived_from` provenance-mirror edges are excluded automatically once
# `relation_type` is non-`None` at the graph-projection layer.
# ---------------------------------------------------------------------------


def test_derived_from_provenance_mirror_edge_absent_from_candidate_edges() -> None:
    """A `derived_from`-typed provenance-mirror edge is a typed row like any
    other, so `_candidate_edges` (which filters on `relation_type is None`
    via `untyped_edges`) excludes it -- confirming the exclusion is
    automatic, with zero code change to this module (task 3.1)."""
    provenance_mirror = Edge(
        source_id="concepts/a", target_id="sources/foo", relation_type="derived_from"
    )
    genuine_untyped = Edge(source_id="concepts/a", target_id="concepts/b")
    store: GraphStore = _FakeGraphStore([provenance_mirror, genuine_untyped])

    result = edge_typing_mod._candidate_edges(store)

    assert result == [genuine_untyped]


def test_provenance_only_bundle_yields_zero_candidates_and_zero_llm_calls(
    tmp_path: Path,
) -> None:
    """A bundle whose only body link is a provenance-mirror edge (typed
    `derived_from` by graph projection) produces zero suggestion candidates
    and `suggest_relations` never calls `llm.chat` (spec: "Bundle with only
    provenance-mirror edges surfaces zero candidates"; task 3.2)."""
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

    edges = edge_typing_mod.candidate_edges(bundle_dir)
    result = edge_typing_mod.suggest_relations(bundle_dir, llm=llm).results

    assert edges == []
    assert result == []
    assert llm.calls == []


def test_suggest_edge_types_invokes_on_progress_once_per_edge_in_order(
    tmp_path: Path,
) -> None:
    """`on_progress` fires once per edge, in input order, AFTER that edge's
    suggestion is built -- carrying `(index, total, suggestion)` so a CLI can
    render a per-edge progress line (issue #134)."""
    _write_doc(tmp_path / "a.md", title="A", body="x")
    _write_doc(tmp_path / "b.md", title="B", body="y")
    _write_doc(tmp_path / "c.md", title="C", body="z")
    edges = [Edge(source_id="a", target_id="b"), Edge(source_id="a", target_id="c")]
    llm = _FakeLLM(replies=[_valid_reply("references"), _valid_reply("related_to")])
    seen: list[tuple[int, int, str, str, str | None]] = []

    def _cb(index: int, total: int, suggestion: edge_typing_mod.EdgeSuggestion) -> None:
        seen.append(
            (
                index,
                total,
                suggestion.edge.source_id,
                suggestion.edge.target_id,
                suggestion.suggested_type,
            )
        )

    edge_typing_mod.suggest_edge_types(
        edges, bundle_dir=tmp_path, llm=llm, on_progress=_cb
    )

    assert seen == [
        (1, 2, "a", "b", "references"),
        (2, 2, "a", "c", "related_to"),
    ]


# ---------------------------------------------------------------------------
# Requirement: Partial Batch Preserved On Mid-Loop `OllamaError` (#441)
# ---------------------------------------------------------------------------


def test_mid_loop_ollama_error_returns_completed_results_and_failure(
    tmp_path: Path,
) -> None:
    """An `OllamaError`-family raise on the k-th edge's `llm.chat` stops the
    loop and returns every already-completed suggestion in input order, the
    exception instance itself, and the 1-based failed index -- the caller
    paid for k-1 completed calls and keeps all of them (#441). No edge
    after the failed one is ever prompted; the failed edge produces no
    suggestion and no `on_progress` call."""
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    _write_doc(tmp_path / "c.md", title="C")
    edges = [
        Edge(source_id="a", target_id="b"),
        Edge(source_id="b", target_id="c"),
        Edge(source_id="c", target_id="a"),
    ]
    capped = OllamaGenerationCapped("generation hit the token ceiling")
    llm = _FakeLLM(replies=[_valid_reply("references")], error=capped, error_at=2)
    seen: list[tuple[int, int, edge_typing_mod.EdgeSuggestion]] = []

    def _cb(index: int, total: int, suggestion: edge_typing_mod.EdgeSuggestion) -> None:
        seen.append((index, total, suggestion))

    batch = edge_typing_mod.suggest_edge_types(
        edges, bundle_dir=tmp_path, llm=llm, on_progress=_cb
    )

    assert isinstance(batch, edge_typing_mod.EdgeSuggestionBatch)
    assert [s.edge for s in batch.results] == edges[:1]
    assert batch.results[0].suggested_type == "references"
    assert batch.failure is capped
    assert batch.failed_index == 2
    # The failed call itself happened; the edge AFTER it was never prompted.
    assert len(llm.calls) == 2
    # `on_progress` fired only for completed suggestions -- never for the
    # failure.
    assert [(index, total) for index, total, _ in seen] == [(1, 3)]


def test_ollama_error_on_first_edge_returns_empty_results(tmp_path: Path) -> None:
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    edges = [Edge(source_id="a", target_id="b")]
    failure = OllamaUnavailable("no local Ollama server reachable")
    llm = _FakeLLM(error=failure, error_at=1)

    batch = edge_typing_mod.suggest_edge_types(edges, bundle_dir=tmp_path, llm=llm)

    assert batch.results == []
    assert batch.failure is failure
    assert batch.failed_index == 1


def test_complete_run_reports_no_failure(tmp_path: Path) -> None:
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    _write_doc(tmp_path / "c.md", title="C")
    edges = [Edge(source_id="a", target_id="b"), Edge(source_id="b", target_id="c")]
    llm = _FakeLLM(replies=[_valid_reply("references"), _valid_reply("depends_on")])

    batch = edge_typing_mod.suggest_edge_types(edges, bundle_dir=tmp_path, llm=llm)

    assert batch.failure is None
    assert batch.failed_index is None
    assert len(batch.results) == len(edges)


# ---------------------------------------------------------------------------
# Phase 3: `suggest_relations` orchestrator (owns `build_graph`)
# ---------------------------------------------------------------------------


def test_suggest_relations_reads_graph_filters_untyped_and_delegates(
    tmp_path: Path,
) -> None:
    """`suggest_relations` opens `build_graph` internally over a real
    bundle: an untyped body link between two concepts produces exactly one
    `EdgeSuggestion`; a `relations:`-typed edge is excluded entirely."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    (bundle_dir / "concepts" / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\nsensitivity: private\n"
        "relations:\n  - target: concepts/b\n    type: references\n"
        "---\nSee [C](/concepts/c.md) for more.\n",
        encoding="utf-8",
    )
    (bundle_dir / "concepts" / "b.md").write_text(
        "---\ntype: Concept\ntitle: B\nsensitivity: private\n---\nBody.\n",
        encoding="utf-8",
    )
    (bundle_dir / "concepts" / "c.md").write_text(
        "---\ntype: Concept\ntitle: C\nsensitivity: private\n---\nBody.\n",
        encoding="utf-8",
    )
    llm = _FakeLLM(replies=[_valid_reply("related_to", "mentions C")])

    result = edge_typing_mod.suggest_relations(bundle_dir, llm=llm).results

    assert len(result) == 1
    assert result[0].edge.source_id == "concepts/a"
    assert result[0].edge.target_id == "concepts/c"
    assert result[0].edge.relation_type is None
    assert result[0].suggested_type == "related_to"


def test_suggest_relations_excludes_untyped_edge_when_pair_already_typed(
    tmp_path: Path,
) -> None:
    """End-to-end: a pair with BOTH a `relations:`-typed edge and a
    coexisting untyped body-link duplicate for the SAME target is excluded
    entirely from candidates -- the LLM is never called for it -- while an
    unrelated untyped-only pair still produces a suggestion. This is the
    same-pair re-suggestion scenario `relate` -> `suggest-relations` ->
    `relate` -> ... previously hit forever."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    (bundle_dir / "concepts" / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\nsensitivity: private\n"
        "relations:\n  - target: concepts/b\n    type: references\n"
        "---\nAlso see [B](/concepts/b.md) again, and [C](/concepts/c.md).\n",
        encoding="utf-8",
    )
    (bundle_dir / "concepts" / "b.md").write_text(
        "---\ntype: Concept\ntitle: B\nsensitivity: private\n---\nBody.\n",
        encoding="utf-8",
    )
    (bundle_dir / "concepts" / "c.md").write_text(
        "---\ntype: Concept\ntitle: C\nsensitivity: private\n---\nBody.\n",
        encoding="utf-8",
    )
    llm = _FakeLLM(replies=[_valid_reply("related_to", "mentions C")])

    result = edge_typing_mod.suggest_relations(bundle_dir, llm=llm).results

    assert len(result) == 1
    assert result[0].edge.source_id == "concepts/a"
    assert result[0].edge.target_id == "concepts/c"
    assert result[0].edge.relation_type is None
    assert len(llm.calls) == 1


def test_suggest_relations_on_bundle_with_no_untyped_edges_returns_empty(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    (bundle_dir / "concepts" / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\nsensitivity: private\n---\nNo links here.\n",
        encoding="utf-8",
    )
    llm = _FakeLLM()

    result = edge_typing_mod.suggest_relations(bundle_dir, llm=llm).results

    assert result == []
    assert llm.calls == []


# ---------------------------------------------------------------------------
# sensitivity-fail-closed-filter, S3a/PR1: confidential endpoints excluded
# from suggest-relations
# ---------------------------------------------------------------------------


def test_edge_with_confidential_endpoint_excluded_by_default(tmp_path: Path) -> None:
    """An untyped edge whose SOURCE is `sensitivity: confidential` never
    reaches `llm.chat` (spec: Confidential excluded from adjudicate/
    contradictions/suggest-relations)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    (bundle_dir / "concepts" / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\nsensitivity: confidential\n---\n"
        "See [B](/concepts/b.md).\n",
        encoding="utf-8",
    )
    (bundle_dir / "concepts" / "b.md").write_text(
        "---\ntype: Concept\ntitle: B\nsensitivity: private\n---\nBody.\n",
        encoding="utf-8",
    )
    llm = _FakeLLM()

    result = edge_typing_mod.suggest_relations(bundle_dir, llm=llm).results

    assert result == []
    assert llm.calls == []


def test_include_confidential_true_restores_the_confidential_edge(
    tmp_path: Path,
) -> None:
    """`include_confidential=True` restores an edge with a confidential
    endpoint, suggesting it normally (spec: `--include-confidential` Escape
    Flag)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    (bundle_dir / "concepts" / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\nsensitivity: confidential\n---\n"
        "See [B](/concepts/b.md).\n",
        encoding="utf-8",
    )
    (bundle_dir / "concepts" / "b.md").write_text(
        "---\ntype: Concept\ntitle: B\nsensitivity: private\n---\nBody.\n",
        encoding="utf-8",
    )
    llm = _FakeLLM(replies=[_valid_reply("related_to", "mentions B")])

    result = edge_typing_mod.suggest_relations(
        bundle_dir, llm=llm, include_confidential=True
    ).results

    assert len(result) == 1
    assert result[0].edge.source_id == "concepts/a"
    assert result[0].edge.target_id == "concepts/b"


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

    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    _write_doc(bundle_dir / "concepts" / "a.md", title="A")
    llm = _FakeLLM()
    walk_calls: list[dict[str, object]] = []
    original_predicate = sensitivity.sensitive_concept_ids

    def _spy_predicate(bundle_dir: Path, **kwargs: object) -> frozenset[str]:
        walk_calls.append(kwargs)
        return original_predicate(bundle_dir, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(sensitivity, "sensitive_concept_ids", _spy_predicate)

    edge_typing_mod.suggest_relations(bundle_dir, llm=llm, include_confidential=True)

    assert walk_calls == [{"include_confidential": True, "local_exemption": False}]


def test_default_include_confidential_false_calls_the_predicate_walk_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default `include_confidential=False` DOES call
    `sensitivity.sensitive_concept_ids` exactly once per `suggest_relations`
    call."""
    from openkos import sensitivity

    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    _write_doc(bundle_dir / "concepts" / "a.md", title="A")
    llm = _FakeLLM()
    walk_calls: list[Path] = []
    original_predicate = sensitivity.sensitive_concept_ids

    def _spy_predicate(bundle_dir: Path, **kwargs: object) -> frozenset[str]:
        walk_calls.append(bundle_dir)
        return original_predicate(bundle_dir, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(sensitivity, "sensitive_concept_ids", _spy_predicate)

    edge_typing_mod.suggest_relations(bundle_dir, llm=llm)

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
    from openkos import sensitivity

    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    (bundle_dir / "concepts" / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\nsensitivity: confidential\n---\n"
        "dichotomyzz confidential note. See [B](/concepts/b.md).\n",
        encoding="utf-8",
    )
    (bundle_dir / "concepts" / "b.md").write_text(
        "---\ntype: Concept\ntitle: B\nsensitivity: private\n---\n"
        "dichotomyzz private note.\n",
        encoding="utf-8",
    )
    # Simulate the walk missing "concepts/a"'s subtree entirely: the
    # precomputed confidential-id set is empty, so the edge is NOT filtered
    # upstream in `suggest_relations` -- the independent per-doc re-check in
    # `_load_doc` is the ONLY thing standing between "concepts/a"'s content
    # and `llm.chat`.
    monkeypatch.setattr(
        sensitivity, "sensitive_concept_ids", lambda *a, **k: frozenset()
    )
    llm = _FakeLLM(replies=[_valid_reply("related_to", "mentions B")])

    edge_typing_mod.suggest_relations(bundle_dir, llm=llm)

    assert len(llm.calls) == 1
    sent_content = " ".join(str(m["content"]) for m in llm.calls[0])
    assert "confidential note" not in sent_content
    assert "private note" in sent_content


def test_include_confidential_true_bypasses_the_load_doc_recheck(
    tmp_path: Path,
) -> None:
    """`include_confidential=True` bypasses the independent `_load_doc`
    re-check too, restoring byte-identical pre-filter behavior (not just at
    the upstream predicate-walk filter)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    (bundle_dir / "concepts" / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\nsensitivity: confidential\n---\n"
        "dichotomyzz confidential note. See [B](/concepts/b.md).\n",
        encoding="utf-8",
    )
    (bundle_dir / "concepts" / "b.md").write_text(
        "---\ntype: Concept\ntitle: B\nsensitivity: private\n---\n"
        "dichotomyzz private note.\n",
        encoding="utf-8",
    )
    llm = _FakeLLM(replies=[_valid_reply("related_to", "mentions B")])

    edge_typing_mod.suggest_relations(bundle_dir, llm=llm, include_confidential=True)

    assert len(llm.calls) == 1
    sent_content = " ".join(str(m["content"]) for m in llm.calls[0])
    assert "confidential note" in sent_content


# --- Phase 2.5 (#183): candidates kwarg plumbed to build_graph -------------


class _StubCandidateSource:
    """Stands in for `graph.proximity.VectorProximitySource` -- fixed pairs,
    no `vectors.db`, no sqlite-vec, no Ollama."""

    def __init__(self, pairs: list[tuple[str, str]]) -> None:
        self._pairs = pairs

    def pairs(self, concept_ids: Sequence[str]) -> list[ProximityPair]:
        return [
            ProximityPair(source_id=s, target_id=t, distance=0.1)
            for s, t in self._pairs
        ]


def test_candidate_edges_surfaces_proximity_rows_through_unmodified_filter(
    tmp_path: Path,
) -> None:
    """The whole point of #183: a bundle whose concepts share no links can
    still yield suggestions, because pass-3 candidate rows reach
    `_candidate_edges` untouched.

    `_candidate_edges` is deliberately NOT modified -- a proximity row is
    just an untyped edge, indistinguishable from a body link, so the
    existing untyped-and-not-typed-elsewhere filter already does the right
    thing."""
    bundle = tmp_path / "bundle"
    _write_doc(bundle / "concepts" / "a.md", title="A")
    _write_doc(bundle / "concepts" / "b.md", title="B")
    source = _StubCandidateSource([("concepts/a", "concepts/b")])

    without = edge_typing_mod.candidate_edges(bundle)
    with_candidates = edge_typing_mod.candidate_edges(bundle, candidates=source)

    assert without == []
    assert with_candidates == [
        Edge(source_id="concepts/a", target_id="concepts/b", relation_type=None)
    ]


def test_candidate_edges_still_excludes_a_pair_typed_elsewhere(
    tmp_path: Path,
) -> None:
    """Regression guard on the pre-existing pair-level exclusion: adding a
    candidate source must not resurrect a pair a human already typed. Pass 3
    declines to emit the row at all, and `_candidate_edges` would filter it
    even if it did."""
    bundle = tmp_path / "bundle"
    _write_doc(bundle / "concepts" / "b.md", title="B")
    (bundle / "concepts" / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\nsensitivity: private\n"
        "relations:\n  - target: concepts/b\n    type: relates_to\n---\nBody.",
        encoding="utf-8",
    )
    source = _StubCandidateSource([("concepts/a", "concepts/b")])

    assert edge_typing_mod.candidate_edges(bundle, candidates=source) == []


def test_candidate_edges_drops_a_proximity_row_with_a_confidential_endpoint(
    tmp_path: Path,
) -> None:
    """The sensitivity fail-closed filter applies to candidate rows exactly
    as it does to body links -- proximity must not become a side channel
    that walks a confidential concept into an LLM prompt."""
    bundle = tmp_path / "bundle"
    _write_doc(bundle / "concepts" / "a.md", title="A")
    _write_doc(
        bundle / "concepts" / "b.md", title="B", sensitivity_value="confidential"
    )
    source = _StubCandidateSource([("concepts/a", "concepts/b")])

    assert edge_typing_mod.candidate_edges(bundle, candidates=source) == []


# ---------------------------------------------------------------------------
# issue #240: the confidential local exemption
# ---------------------------------------------------------------------------


def test_candidate_edges_local_exemption_keeps_a_confidential_endpoint(
    tmp_path: Path,
) -> None:
    """With a verified-local backend, an edge touching a `confidential`
    endpoint stays a candidate without `--include-confidential` (#240)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "a.md", title="A", sensitivity_value="confidential"
    )
    _write_doc(bundle_dir / "concepts" / "b.md", title="B")
    fake_store: GraphStore = _FakeGraphStore(
        [Edge(source_id="concepts/a", target_id="concepts/b")]
    )

    result = edge_typing_mod.candidate_edges(
        bundle_dir, store=fake_store, local_exemption=True
    )

    assert [(e.source_id, e.target_id) for e in result] == [
        ("concepts/a", "concepts/b")
    ]


def test_local_exemption_carries_confidential_body_into_the_prompt(
    tmp_path: Path,
) -> None:
    """`suggest_edge_types` threads the exemption into `_load_doc`, so a
    confidential endpoint's real title and body reach the prompt rather than
    the `(concept_id, "")` degrade (#240).

    The upstream candidate filter and the per-doc re-check must agree: if
    only one honored the exemption, a local-backend run would still prompt
    with an empty body and the exemption would be cosmetic."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "a.md",
        title="A",
        body="Alpha secret body.",
        sensitivity_value="confidential",
    )
    _write_doc(bundle_dir / "concepts" / "b.md", title="B")
    edge = Edge(source_id="concepts/a", target_id="concepts/b")
    llm = _FakeLLM(replies=[_valid_reply("related_to", "mentions B")])

    edge_typing_mod.suggest_edge_types(
        [edge], bundle_dir=bundle_dir, llm=llm, local_exemption=True
    )

    (message,) = [m for m in llm.calls[0] if m["role"] == "user"]
    assert "Alpha secret body." in message["content"]
    assert "[concepts/a — A]" in message["content"]


def test_local_exemption_defaults_to_false_across_the_edge_typing_seams(
    tmp_path: Path,
) -> None:
    """Every #240 seam in this module defaults `local_exemption` to `False`
    -- forgetting it can only ever be MORE restrictive (#240)."""
    for func in (
        edge_typing_mod.candidate_edges,
        edge_typing_mod.suggest_relations,
        edge_typing_mod.suggest_edge_types,
    ):
        assert inspect.signature(func).parameters["local_exemption"].default is False

    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "a.md", title="A", sensitivity_value="confidential"
    )
    _write_doc(bundle_dir / "concepts" / "b.md", title="B")
    fake_store: GraphStore = _FakeGraphStore(
        [Edge(source_id="concepts/a", target_id="concepts/b")]
    )

    assert edge_typing_mod.candidate_edges(bundle_dir, store=fake_store) == []


# ---------------------------------------------------------------------------
# `candidate_truncation_notice` (#378 post-review correction): the pass-3
# candidate-edge cap notice, restricted to what THIS caller may see. Pass 3
# has no sensitivity awareness of its own, so `CandidateReport.produced`/
# `.retained` can count pairs with a confidential endpoint; rendering those
# raw ints would disclose an aggregate volume the same command's printed
# edge list deliberately withholds. These tests build a `CandidateReport`
# directly (no real graph/proximity source needed) and a bundle carrying
# only the sensitivity frontmatter the function's `sensitive_concept_ids`
# walk reads.
# ---------------------------------------------------------------------------


def _confidential_doc(bundle_dir: Path, concept_id: str) -> None:
    _write_doc(
        bundle_dir / f"{concept_id}.md",
        title=concept_id,
        sensitivity_value="confidential",
    )


def test_candidate_truncation_notice_none_when_visible_produced_equals_visible_retained(
    tmp_path: Path,
) -> None:
    """No truncation once the counts are re-derived from `pairs` -- mirrors
    the pre-cap `produced == retained` case, nothing to disclose."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    report = CandidateReport(
        produced=1,
        retained=1,
        pairs=(("concepts/a", "concepts/b"),),
    )

    assert edge_typing_mod.candidate_truncation_notice(report, bundle_dir) is None


def test_candidate_truncation_notice_full_notice_when_no_endpoint_is_confidential(
    tmp_path: Path,
) -> None:
    """With nothing confidential in the drop, the visible counts equal the
    raw counts -- byte-identical wording to the pre-correction behavior."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    pairs = tuple((f"concepts/a{i:03d}", f"concepts/b{i:03d}") for i in range(1, 4))
    report = CandidateReport(produced=3, retained=2, pairs=pairs)

    notice = edge_typing_mod.candidate_truncation_notice(report, bundle_dir)

    assert notice == "2 of 3 candidate edge(s) shown (cap reached)"


def test_candidate_truncation_notice_suppressed_when_every_dropped_pair_is_confidential(
    tmp_path: Path,
) -> None:
    """A run truncated PURELY in material the caller cannot see must stay
    silent -- the reader must never learn an aggregate volume the same
    command's printed edge list withholds. Without `--include-confidential`,
    the dropped pair vanishes from both the produced and retained counts,
    so `visible_produced == visible_retained` and nothing is printed."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    _write_doc(bundle_dir / "concepts" / "keep1.md", title="keep1")
    _write_doc(bundle_dir / "concepts" / "keep2.md", title="keep2")
    _confidential_doc(bundle_dir, "concepts/secret")
    report = CandidateReport(
        produced=3,
        retained=2,
        pairs=(
            ("concepts/hub", "concepts/keep1"),
            ("concepts/hub", "concepts/keep2"),
            ("concepts/hub", "concepts/secret"),
        ),
    )

    notice = edge_typing_mod.candidate_truncation_notice(report, bundle_dir)

    assert notice is None


def test_candidate_truncation_notice_full_notice_with_include_confidential(
    tmp_path: Path,
) -> None:
    """The SAME confidential-only-drop fixture, but with
    `include_confidential=True`: the escape hatch restores every pair, so
    the full raw-count notice is rendered -- proves suppression is
    sensitivity-gated, not a silent regression of the notice itself."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    _write_doc(bundle_dir / "concepts" / "keep1.md", title="keep1")
    _write_doc(bundle_dir / "concepts" / "keep2.md", title="keep2")
    _confidential_doc(bundle_dir, "concepts/secret")
    report = CandidateReport(
        produced=3,
        retained=2,
        pairs=(
            ("concepts/hub", "concepts/keep1"),
            ("concepts/hub", "concepts/keep2"),
            ("concepts/hub", "concepts/secret"),
        ),
    )

    notice = edge_typing_mod.candidate_truncation_notice(
        report, bundle_dir, include_confidential=True
    )

    assert notice == "2 of 3 candidate edge(s) shown (cap reached)"


def test_candidate_truncation_notice_reports_only_visible_counts_for_a_mixed_drop(
    tmp_path: Path,
) -> None:
    """A mixed drop -- some dropped pairs confidential, some not -- must
    report the counts a caller without `--include-confidential` can
    actually see, not the raw pre-cap totals. Two retained pairs, three
    dropped: one confidential (invisible, vanishes from `produced`), two
    visible (still counted as dropped)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    for name in ("keep1", "keep2", "visible-drop-1", "visible-drop-2"):
        _write_doc(bundle_dir / "concepts" / f"{name}.md", title=name)
    _confidential_doc(bundle_dir, "concepts/secret-drop")
    report = CandidateReport(
        produced=5,
        retained=2,
        pairs=(
            ("concepts/hub", "concepts/keep1"),
            ("concepts/hub", "concepts/keep2"),
            ("concepts/hub", "concepts/secret-drop"),
            ("concepts/hub", "concepts/visible-drop-1"),
            ("concepts/hub", "concepts/visible-drop-2"),
        ),
    )

    notice = edge_typing_mod.candidate_truncation_notice(report, bundle_dir)

    # Visible produced: 4 (5 raw minus the 1 confidential-endpoint pair).
    # Visible retained: 2 (unaffected -- the confidential pair was already
    # in the dropped, not retained, slice).
    assert notice == "2 of 4 candidate edge(s) shown (cap reached)"


def test_candidate_truncation_notice_suppressed_when_confidentiality_removes_a_retained_pair(
    tmp_path: Path,
) -> None:
    """A confidential endpoint inside the RETAINED prefix must also drop out
    of the visible retained count, not just the visible produced count --
    proves the function filters `pairs[:retained]` independently rather
    than assuming every confidential pair sits in the dropped tail."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "concepts").mkdir()
    _write_doc(bundle_dir / "concepts" / "keep1.md", title="keep1")
    _write_doc(bundle_dir / "concepts" / "visible-drop.md", title="visible-drop")
    _confidential_doc(bundle_dir, "concepts/secret-retained")
    report = CandidateReport(
        produced=3,
        retained=2,
        pairs=(
            ("concepts/hub", "concepts/keep1"),
            ("concepts/hub", "concepts/secret-retained"),
            ("concepts/hub", "concepts/visible-drop"),
        ),
    )

    notice = edge_typing_mod.candidate_truncation_notice(report, bundle_dir)

    # Visible produced: 2 (secret-retained excluded). Visible retained: 1
    # (only keep1 survives from the retained prefix). 2 > 1, so the notice
    # still fires, with the reduced counts.
    assert notice == "1 of 2 candidate edge(s) shown (cap reached)"


def test_candidate_truncation_notice_counts_the_offset_window(
    tmp_path: Path,
) -> None:
    """With a non-zero `offset`, the visible retained count comes from the
    OFFSET window `pairs[offset : offset + retained]` -- the slice pass 3
    actually inserted -- never from the head of the ranked list (#567)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    pairs = tuple((f"concepts/a{i:03d}", f"concepts/b{i:03d}") for i in range(1, 6))
    report = CandidateReport(produced=5, retained=2, pairs=pairs, offset=2)

    notice = edge_typing_mod.candidate_truncation_notice(report, bundle_dir)

    assert notice == "2 of 5 candidate edge(s) shown (cap reached)"
