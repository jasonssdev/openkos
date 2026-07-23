"""Unit tests for `resolution/edge_typing.py`: the config-free LLM
relation-type suggestion leaf over untyped body-link edges from the derived
graph projection (MVP-2 slice 2b).

All tests use a `tmp_path` bundle and a reply-QUEUE fake `LLMBackend`
(records `.calls`, returns queued replies in call order) -- zero network,
zero real Ollama process. Mirrors `_FakeLLM` in
`tests/unit/resolution/test_adjudication.py`.
"""

from collections.abc import Sequence
from pathlib import Path

import pytest

from openkos.graph.base import Edge, GraphStore
from openkos.llm.base import Message
from openkos.llm.ollama import OllamaUnavailable
from openkos.resolution import edge_typing as edge_typing_mod


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

    result = edge_typing_mod.suggest_edge_types(edges, bundle_dir=tmp_path, llm=llm)

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

    result = edge_typing_mod.suggest_edge_types(edges, bundle_dir=tmp_path, llm=llm)

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

    result = edge_typing_mod.suggest_edge_types(edges, bundle_dir=tmp_path, llm=llm)

    assert len(result) == 1
    assert result[0].suggested_type is None


def test_suggest_edge_types_propagates_ollama_error_unswallowed(
    tmp_path: Path,
) -> None:
    _write_doc(tmp_path / "a.md", title="A")
    _write_doc(tmp_path / "b.md", title="B")
    edges = [Edge(source_id="a", target_id="b")]
    llm = _FakeLLM(error=OllamaUnavailable("not reachable"))

    with pytest.raises(OllamaUnavailable):
        edge_typing_mod.suggest_edge_types(edges, bundle_dir=tmp_path, llm=llm)


def test_suggest_edge_types_handles_unreadable_source_or_target_doc(
    tmp_path: Path,
) -> None:
    """Neither `a` nor `b` has a document on disk (dangling ids) -- the
    guarded doc re-read degrades to `(concept_id, "")` rather than raising,
    and `llm.chat` is still called exactly once for the edge."""
    edges = [Edge(source_id="missing-a", target_id="missing-b")]
    llm = _FakeLLM(replies=[_valid_reply("references", "best guess")])

    result = edge_typing_mod.suggest_edge_types(edges, bundle_dir=tmp_path, llm=llm)

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

    result = edge_typing_mod.suggest_edge_types(edges, bundle_dir=tmp_path, llm=llm)

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

    result = edge_typing_mod.suggest_edge_types(edges, bundle_dir=tmp_path, llm=llm)

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

    result = edge_typing_mod.suggest_edge_types(edges, bundle_dir=tmp_path, llm=llm)

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

    result = edge_typing_mod.suggest_edge_types(edges, bundle_dir=tmp_path, llm=llm)

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

    result = edge_typing_mod.suggest_edge_types(edges, bundle_dir=tmp_path, llm=llm)

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

    result = edge_typing_mod.suggest_edge_types(edges, bundle_dir=tmp_path, llm=llm)

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

    result = edge_typing_mod.suggest_relations(bundle_dir, llm=llm)

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

    result = edge_typing_mod.suggest_relations(bundle_dir, llm=llm)

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

    result = edge_typing_mod.suggest_relations(bundle_dir, llm=llm)

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

    result = edge_typing_mod.suggest_relations(bundle_dir, llm=llm)

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
    )

    assert len(result) == 1
    assert result[0].edge.source_id == "concepts/a"
    assert result[0].edge.target_id == "concepts/b"


def test_include_confidential_true_never_calls_the_predicate_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`include_confidential=True` skips `sensitivity.sensitive_concept_ids`
    entirely (spy) -- the escape flag is the zero-cost path (design R1)."""
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

    edge_typing_mod.suggest_relations(bundle_dir, llm=llm, include_confidential=True)

    assert walk_calls == []


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
