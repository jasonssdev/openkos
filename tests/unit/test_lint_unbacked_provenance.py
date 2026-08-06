"""Unit tests for `lint.check_unbacked_provenance` (issue #421).

`derived_from` MEANS provenance -- "this object was compiled from that
source" -- and it is the guarantee behind citations and behind sensitivity
propagation under the high-water-mark rule. The graph projection SYNTHESIZES
it from each document's `provenance:` frontmatter (#135), so a
`relations:`-written `derived_from` whose target is absent from that same
document's `provenance:` lands in the same graph, with the same type string,
as the synthesized ones -- indistinguishable from real provenance, which is
what makes it silent corruption rather than a visible mistake.

#380/#418 closed the INGRESS (the suggester is no longer handed the type);
this check is the DETECTION half, for the claims already on disk. It is
PURE: no LLM, no clock, no bundle walk -- it takes ONLY `docs`, the SAME
structural no-fifth-walk guard `check_dangling_targets`/`check_unextracted`/
`check_below_source_sensitivity`/`check_dangling_provenance` follow.

The subject set is `relations.ENGINE_OWNED_RELATION_TYPES`, read at call
time rather than hard-coded, so a second engine-derived type is FOLLOWED
rather than requiring this check to be re-written (issue #421's third open
question).
"""

import inspect
from pathlib import Path

from openkos import lint
from openkos.model.relations import ENGINE_OWNED_RELATION_TYPES


def _doc(
    identity: str,
    *,
    doc_type: str = "Concept",
    provenance: tuple[str, ...] = (),
    engine_owned_relations: tuple[tuple[str, str], ...] = (),
) -> lint.LintDoc:
    return lint.LintDoc(
        path=Path(f"/bundle/{identity}.md"),
        identity=identity,
        rel_dir=str(Path(identity).parent) if "/" in identity else "",
        body="",
        freshness="",
        type=doc_type,
        volatility="",
        provenance=provenance,
        engine_owned_relations=engine_owned_relations,
    )


def test_derived_from_absent_from_provenance_is_flagged() -> None:
    """The exact defect #421 reports: a `relations:` entry typed
    `derived_from` whose target is not in that document's `provenance:`
    asserts a compilation that never happened. The finding names the citing
    document, the relation type, the offending target, and the provenance
    the document actually records -- everything a human needs to judge it
    without reopening the file."""
    doc = _doc(
        "concepts/adk-agent-development-kit",
        provenance=("sources/9-productionize-agent",),
        engine_owned_relations=(("derived_from", "concepts/agent-development"),),
    )

    findings = lint.check_unbacked_provenance([doc])

    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == "unbacked-provenance"
    assert finding.path == "concepts/adk-agent-development-kit.md"
    assert "derived_from" in finding.detail
    assert "'concepts/agent-development'" in finding.detail
    assert "sources/9-productionize-agent" in finding.detail


def test_derived_from_backed_by_provenance_is_never_flagged() -> None:
    """A `derived_from` whose target IS in the same document's `provenance:`
    states exactly what the projection would synthesize -- a real provenance
    claim, never a finding."""
    doc = _doc(
        "concepts/derived",
        provenance=("sources/a",),
        engine_owned_relations=(("derived_from", "sources/a"),),
    )

    findings = lint.check_unbacked_provenance([doc])

    assert findings == []


def test_derived_from_on_a_document_with_no_provenance_is_flagged() -> None:
    """An empty `provenance:` backs nothing at all, so every engine-owned
    relation on such a document is unbacked -- the maximal case, never a
    skipped one."""
    doc = _doc(
        "concepts/document-skills",
        engine_owned_relations=(("derived_from", "concepts/pre-built-skills"),),
    )

    findings = lint.check_unbacked_provenance([doc])

    assert len(findings) == 1
    assert findings[0].kind == "unbacked-provenance"
    assert "'concepts/pre-built-skills'" in findings[0].detail


def test_a_human_authored_relation_type_is_never_the_subject() -> None:
    """Only ENGINE-OWNED types are checked. `related_to`/`references` and
    every other open-vocabulary type carry no provenance meaning, so a
    target absent from `provenance:` is correct and unremarkable for
    them -- flagging those would report every typed edge in every bundle."""
    doc = _doc(
        "concepts/a",
        provenance=("sources/a",),
        engine_owned_relations=(),
    )

    assert lint.check_unbacked_provenance([doc]) == []


def test_every_engine_owned_type_is_followed_not_just_derived_from() -> None:
    """The check reads `ENGINE_OWNED_RELATION_TYPES` rather than hard-coding
    `derived_from`, so a second engine-derived type is followed rather than
    silently unchecked (issue #421: "this check should follow it rather than
    be re-written")."""
    for engine_owned in ENGINE_OWNED_RELATION_TYPES:
        doc = _doc(
            "concepts/a",
            provenance=("sources/real",),
            engine_owned_relations=((engine_owned, "concepts/invented"),),
        )

        findings = lint.check_unbacked_provenance([doc])

        assert len(findings) == 1, engine_owned
        assert engine_owned in findings[0].detail


def test_one_finding_per_unique_type_target_pair() -> None:
    """The same unbacked target claimed twice by one document reports once;
    two documents claiming it report once EACH, in document order then each
    document's own `relations:` order -- the SAME
    one-finding-per-unique-pair contract `check_dangling_targets` and
    `check_dangling_provenance` pin."""
    doc_a = _doc(
        "concepts/a",
        provenance=("sources/real",),
        engine_owned_relations=(
            ("derived_from", "concepts/invented"),
            ("derived_from", "concepts/invented"),
            ("derived_from", "concepts/also-invented"),
        ),
    )
    doc_b = _doc(
        "concepts/b",
        engine_owned_relations=(("derived_from", "concepts/invented"),),
    )

    findings = lint.check_unbacked_provenance([doc_a, doc_b])

    assert [(f.path, f.kind) for f in findings] == [
        ("concepts/a.md", "unbacked-provenance"),
        ("concepts/a.md", "unbacked-provenance"),
        ("concepts/b.md", "unbacked-provenance"),
    ]
    assert "'concepts/invented'" in findings[0].detail
    assert "'concepts/also-invented'" in findings[1].detail
    assert "'concepts/invented'" in findings[2].detail


def test_a_backed_and_an_unbacked_claim_on_one_document_fires_only_once() -> None:
    """The check is per-ENTRY, never per-document: a document holding one
    real provenance claim and one invented one reports the invented one
    alone."""
    doc = _doc(
        "concepts/mixed",
        provenance=("sources/real",),
        engine_owned_relations=(
            ("derived_from", "sources/real"),
            ("derived_from", "concepts/invented"),
        ),
    )

    findings = lint.check_unbacked_provenance([doc])

    assert len(findings) == 1
    assert "'concepts/invented'" in findings[0].detail
    assert "'sources/real'" not in findings[0].detail.split("provenance")[0]


def test_the_check_takes_only_docs_and_is_incapable_of_a_walk() -> None:
    """The structural no-fifth-walk guard, pinned the way the module
    docstrings state it: a function that never receives a directory cannot
    open one. Also the determinism guard #421 requires -- `docs` is the
    whole input, so no model, clock, or filesystem can vary the result."""
    parameters = list(inspect.signature(lint.check_unbacked_provenance).parameters)

    assert parameters == ["docs"]


def test_collect_docs_captures_engine_owned_relations_from_frontmatter(
    tmp_path: Path,
) -> None:
    """End-to-end over real files: `collect_docs` decodes `relations:` via
    `okf.decode_relations` and keeps the engine-owned (type, target) pairs,
    so the reproduction in issue #421 fires through the collected docs with
    no extra parsing of its own."""
    bundle = tmp_path / "bundle"
    (bundle / "concepts").mkdir(parents=True)
    (bundle / "sources").mkdir()
    (bundle / "sources" / "9-productionize-agent.md").write_text(
        "---\ntype: Source\ntitle: Productionize\n---\nBody.\n", encoding="utf-8"
    )
    (bundle / "concepts" / "agent-development.md").write_text(
        "---\ntype: Concept\ntitle: Agent Development\n---\nBody.\n", encoding="utf-8"
    )
    (bundle / "concepts" / "adk-agent-development-kit.md").write_text(
        "---\ntype: Concept\ntitle: ADK\n"
        "provenance:\n  - sources/9-productionize-agent\n"
        "relations:\n"
        "  - type: derived_from\n    target: concepts/agent-development.md\n"
        "  - type: related_to\n    target: concepts/agent-development.md\n"
        "---\nBody.\n",
        encoding="utf-8",
    )

    docs, notices = lint.collect_docs(bundle)

    assert notices == []
    by_id = {doc.identity: doc for doc in docs}
    assert by_id["concepts/adk-agent-development-kit"].engine_owned_relations == (
        ("derived_from", "concepts/agent-development"),
    )
    findings = lint.check_unbacked_provenance(docs)
    assert [f.path for f in findings] == ["concepts/adk-agent-development-kit.md"]


def test_repeated_calls_over_the_same_docs_are_byte_identical() -> None:
    """Determinism, stated as an assertion rather than an intention (#421:
    "a provenance-integrity check that depends on a model is not a
    check")."""
    docs = [
        _doc(
            "concepts/a",
            provenance=("sources/real",),
            engine_owned_relations=(("derived_from", "concepts/invented"),),
        ),
        _doc(
            "concepts/b",
            engine_owned_relations=(("derived_from", "concepts/other"),),
        ),
    ]

    first = lint.check_unbacked_provenance(docs)
    second = lint.check_unbacked_provenance(docs)

    assert first == second


def test_the_kind_is_distinct_from_every_existing_lint_kind() -> None:
    """`unbacked-provenance` must not collide with `dangling-provenance`:
    the two name different defects (a provenance ENTRY pointing nowhere vs.
    a `derived_from` RELATION no entry backs) and `status` labels each
    finding by its kind."""
    doc = _doc(
        "concepts/a",
        engine_owned_relations=(("derived_from", "concepts/invented"),),
    )

    (finding,) = lint.check_unbacked_provenance([doc])

    assert finding.kind not in {
        "stale",
        "orphan",
        "dangling",
        "unextracted",
        "below-source-sensitivity",
        "multi-source-uncovered",
        "dangling-provenance",
    }
