"""Unit tests for `lint.check_below_source_sensitivity` (#231, PR2).

Both finding kinds are keyed on CLOSURE MEMBERSHIP, not on a raw citation
count (design D3):

- `below-source-sensitivity`: `identity` is in the closure of EXACTLY ONE
  `type: Source` root, and `okf.combine_sensitivity(doc.sensitivity,
  source.sensitivity)` differs from `doc.sensitivity` -- the SAME test the
  `backfill-sensitivity` sweep uses to stage a write, so a missing, blank,
  or unrecognized `sensitivity` is ranked fail-closed (ADR-0003) and IS
  flagged even though it does not "strictly rank below".
- `multi-source-uncovered`: non-empty `provenance`, every cited id resolves
  to a doc in `docs`, `identity` is a member of NO single-Source closure,
  and `doc.sensitivity` sits strictly below the high-water-mark of the
  cited concepts' levels.

`check_below_source_sensitivity` takes ONLY `docs` (no `bundle_dir`) --
the structural no-fifth-walk guard `check_unextracted`/
`check_dangling_targets` already follow (design D3, `lint.py:556-560`). It
reuses `bundle.provenance.provenance_closure` plus `okf.combine_sensitivity`
-- never `resolve_source_raises`, which needs full file text `LintDoc`
does not keep (design D2).
"""

import re
from pathlib import Path

from openkos import lint


def _doc(
    identity: str,
    *,
    doc_type: str = "Concept",
    sensitivity: str = "public",
    provenance: tuple[str, ...] = (),
) -> lint.LintDoc:
    return lint.LintDoc(
        path=Path(f"/bundle/{identity}.md"),
        identity=identity,
        rel_dir=str(Path(identity).parent) if "/" in identity else "",
        body="",
        freshness="",
        type=doc_type,
        volatility="",
        sensitivity=sensitivity,
        provenance=provenance,
    )


def test_lint_doc_construction_with_only_seven_non_defaulted_fields() -> None:
    """`LintDoc(*seven_non_defaulted_fields)` still constructs without
    `sensitivity`/`provenance` (guards
    `tests/unit/resolution/test_volatility_typing.py:612`; task 6.2)."""
    doc = lint.LintDoc(
        path=Path("/bundle/concepts/x.md"),
        identity="concepts/x",
        rel_dir="concepts",
        body="",
        freshness="",
        type="Concept",
        volatility="",
    )

    assert doc.sensitivity == ""
    assert doc.provenance == ()


def test_below_source_sensitivity_flags_a_dirty_value_under_public_source() -> None:
    """A descendant with a missing/blank `sensitivity` under a `public`
    Source IS flagged -- `combine_sensitivity` ranks a blank value
    fail-closed at `private` (ADR-0003), which differs from the blank
    current value even though `"private" > "public"` is the only
    directional change, not a strict-rank comparison against `""`."""
    source = _doc("sources/a", doc_type="Source", sensitivity="public")
    descendant = _doc(
        "concepts/derived",
        sensitivity="",
        provenance=("sources/a",),
    )

    findings = lint.check_below_source_sensitivity([source, descendant])

    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == "below-source-sensitivity"
    assert finding.path == "concepts/derived.md"


def test_below_source_sensitivity_ignores_a_descendant_already_covered() -> None:
    """A descendant whose `sensitivity` already equals
    `combine_sensitivity(current, source_level)` is not flagged -- nothing
    for `backfill-sensitivity` to stage."""
    source = _doc("sources/a", doc_type="Source", sensitivity="private")
    descendant = _doc(
        "concepts/derived",
        sensitivity="private",
        provenance=("sources/a",),
    )

    findings = lint.check_below_source_sensitivity([source, descendant])

    assert findings == []


def test_below_source_sensitivity_same_source_multi_cite_is_covered_not_uncovered() -> (
    None
):
    """A doc citing TWO concepts that both fall inside the SAME Source's
    closure is `below-source-sensitivity`, never `multi-source-uncovered`
    (design D3 exclusion; `query --save`'s two-output rule writes such docs
    routinely)."""
    source = _doc("sources/a", doc_type="Source", sensitivity="confidential")
    derived_p = _doc("concepts/p", sensitivity="public", provenance=("sources/a",))
    derived_q = _doc("concepts/q", sensitivity="public", provenance=("sources/a",))
    doc = _doc(
        "concepts/pq",
        sensitivity="public",
        provenance=("concepts/p", "concepts/q"),
    )

    findings = lint.check_below_source_sensitivity([source, derived_p, derived_q, doc])

    kinds_by_path = {finding.path: finding.kind for finding in findings}
    assert kinds_by_path["concepts/pq.md"] == "below-source-sensitivity"
    assert "multi-source-uncovered" not in kinds_by_path.values()


def test_source_plus_foreign_derived_cite_is_multi_source_uncovered() -> None:
    """A doc citing one Source PLUS one concept derived from a DIFFERENT
    Source is a member of no single-Source closure and is flagged
    `multi-source-uncovered` -- a rule keyed on "cites two or more Sources"
    would silently miss this exact case (design D3)."""
    source_a = _doc("sources/a", doc_type="Source", sensitivity="public")
    source_c = _doc("sources/c", doc_type="Source", sensitivity="confidential")
    derived_from_c = _doc(
        "concepts/from-c", sensitivity="confidential", provenance=("sources/c",)
    )
    doc = _doc(
        "concepts/mixed",
        sensitivity="public",
        provenance=("sources/a", "concepts/from-c"),
    )

    findings = lint.check_below_source_sensitivity(
        [source_a, source_c, derived_from_c, doc]
    )

    mixed_findings = [f for f in findings if f.path == "concepts/mixed.md"]
    assert len(mixed_findings) == 1
    finding = mixed_findings[0]
    assert finding.kind == "multi-source-uncovered"
    assert "sources/a" in finding.detail
    assert "concepts/from-c" in finding.detail
    # #697/ADR-0016 flipped this clause's polarity: the sweep now DOES repair
    # this document, so the detail states the defect and offers both remedies
    # instead of ruling the sweep out.
    assert "below the high-water mark of what it cites" in finding.detail
    assert "backfill-sensitivity" in finding.detail


def test_unresolvable_cite_falls_into_neither_category() -> None:
    """A doc citing an id absent from `docs` falls into neither category
    (fail-safe; it already surfaces separately as the `dangling` lint
    finding)."""
    source = _doc("sources/a", doc_type="Source", sensitivity="public")
    doc = _doc(
        "concepts/orphaned-cite",
        sensitivity="public",
        provenance=("concepts/does-not-exist",),
    )

    findings = lint.check_below_source_sensitivity([source, doc])

    assert findings == []


def test_clean_bundle_with_no_sources_reports_zero_findings() -> None:
    """A bundle with no `type: Source` docs at all yields zero findings --
    no closure roots, nothing to raise or report."""
    doc = _doc("concepts/lonely", sensitivity="public", provenance=())

    findings = lint.check_below_source_sensitivity([doc])

    assert findings == []


def test_multi_source_uncovered_not_flagged_when_already_at_high_water_mark() -> None:
    """A doc already at (or above) the high-water-mark of its cited
    concepts' levels is not flagged, even though it is a member of no
    single-Source closure."""
    source_a = _doc("sources/a", doc_type="Source", sensitivity="public")
    source_c = _doc("sources/c", doc_type="Source", sensitivity="private")
    doc = _doc(
        "concepts/mixed",
        sensitivity="confidential",
        provenance=("sources/a", "sources/c"),
    )

    findings = lint.check_below_source_sensitivity([source_a, source_c, doc])

    assert findings == []


# --- #231, PR2: `collect_docs` provenance decoding feeds both checks ---


def test_collect_docs_skips_doc_with_corrupt_provenance_key(tmp_path: Path) -> None:
    """A `provenance:` value that is present but not a list (e.g. hand-edited
    to a scalar) is skipped with a notice, exactly like a corrupt
    `relations:` -- never silently coerced to an empty tuple, which would
    hide the doc from both finding kinds above."""
    bundle_dir = tmp_path / "bundle"
    (bundle_dir / "concepts").mkdir(parents=True)
    (bundle_dir / "concepts" / "broken.md").write_text(
        "---\ntype: Concept\ntitle: Broken\nprovenance: sources/a\n---\nBody.\n",
        encoding="utf-8",
    )

    docs, skipped = lint.collect_docs(bundle_dir)

    assert docs == []
    assert skipped == ["concepts/broken.md: skipped (invalid provenance)"]


def test_collect_docs_defaults_provenance_to_empty_tuple_when_absent(
    tmp_path: Path,
) -> None:
    """A doc with NO `provenance:` key is collected normally with an empty
    tuple and no notice -- an absent key is the overwhelmingly common,
    perfectly valid case, never an invalid one."""
    bundle_dir = tmp_path / "bundle"
    (bundle_dir / "concepts").mkdir(parents=True)
    (bundle_dir / "concepts" / "stoicism.md").write_text(
        "---\ntype: Concept\ntitle: Stoicism\n---\nBody.\n",
        encoding="utf-8",
    )

    docs, skipped = lint.collect_docs(bundle_dir)

    assert docs[0].provenance == ()
    assert skipped == []


def test_collect_docs_surfaces_corrupt_provenance_below_a_source(
    tmp_path: Path,
) -> None:
    """A descendant that would sit below its Source but whose `provenance:`
    is corrupt must not read as a false clean scan: it is absent from
    `check_below_source_sensitivity`'s input, so the skip notice is the ONLY
    signal the operator gets."""
    bundle_dir = tmp_path / "bundle"
    (bundle_dir / "sources").mkdir(parents=True)
    (bundle_dir / "sources" / "a.md").write_text(
        "---\ntype: Source\ntitle: A\nsensitivity: confidential\n---\nBody.\n",
        encoding="utf-8",
    )
    (bundle_dir / "concepts").mkdir()
    (bundle_dir / "concepts" / "derived.md").write_text(
        "---\ntype: Concept\ntitle: Derived\nsensitivity: public\n"
        "provenance: sources/a\n---\nBody.\n",
        encoding="utf-8",
    )

    docs, skipped = lint.collect_docs(bundle_dir)

    assert lint.check_below_source_sensitivity(docs) == []
    assert skipped == ["concepts/derived.md: skipped (invalid provenance)"]


# --- #693: the finding names the verb that actually RESOLVES it -------------


def test_multi_source_uncovered_names_set_sensitivity_as_the_resolving_verb() -> None:
    """The detail names `openkos set-sensitivity`, the command that fixes it.

    Before #693 the detail named only `openkos backfill-sensitivity` -- the
    command that explicitly CANNOT resolve this kind -- so a reader was
    pointed at the one verb guaranteed not to help and never at the one that
    does. `doctor` already names the resolving verb; `lint` had not followed.
    """
    source_a = _doc("sources/a", doc_type="Source", sensitivity="public")
    source_c = _doc("sources/c", doc_type="Source", sensitivity="confidential")
    derived_from_c = _doc(
        "concepts/from-c", sensitivity="confidential", provenance=("sources/c",)
    )
    doc = _doc(
        "concepts/mixed",
        sensitivity="public",
        provenance=("sources/a", "concepts/from-c"),
    )

    findings = lint.check_below_source_sensitivity(
        [source_a, source_c, derived_from_c, doc]
    )

    (finding,) = [f for f in findings if f.path == "concepts/mixed.md"]
    assert "`openkos set-sensitivity concepts/mixed confidential`" in finding.detail
    # The negation is GONE (#697/ADR-0016): the sweep does pick this up now.
    # What stays is the closure clause, which is still true and is still why
    # this is a distinct finding kind.
    assert "not covered by" not in finding.detail
    assert "member of no single Source's closure" in finding.detail


def test_multi_source_uncovered_carries_a_runnable_remediation() -> None:
    """`remediation` carries the exact resolving command, ENGINE-COMPUTED.

    `next` reads this field rather than re-parsing the detail prose. That
    matters for more than tidiness: the detail interpolates document-controlled
    values (a raw `sensitivity` string, cited ids), so a document could plant
    a backtick span spelling a plausible command with the WRONG level. A field
    the engine computes cannot be forged from a document's frontmatter.
    """
    source_a = _doc("sources/a", doc_type="Source", sensitivity="public")
    source_c = _doc("sources/c", doc_type="Source", sensitivity="confidential")
    derived_from_c = _doc(
        "concepts/from-c", sensitivity="confidential", provenance=("sources/c",)
    )
    doc = _doc(
        "concepts/mixed",
        sensitivity="public",
        provenance=("sources/a", "concepts/from-c"),
    )

    findings = lint.check_below_source_sensitivity(
        [source_a, source_c, derived_from_c, doc]
    )

    (finding,) = [f for f in findings if f.path == "concepts/mixed.md"]
    assert finding.remediation == "openkos set-sensitivity concepts/mixed confidential"


def test_remediation_names_the_high_water_mark_not_the_first_cite() -> None:
    """The level is the high-water-mark ACROSS every cite, not whichever one
    happened to be listed first. Naming the first cite's level would produce
    a runnable command that leaves the document still under-labelled."""
    source_a = _doc("sources/a", doc_type="Source", sensitivity="public")
    source_c = _doc("sources/c", doc_type="Source", sensitivity="private")
    derived_from_c = _doc(
        "concepts/from-c", sensitivity="private", provenance=("sources/c",)
    )
    doc = _doc(
        "concepts/mixed",
        sensitivity="public",
        provenance=("sources/a", "concepts/from-c"),
    )

    findings = lint.check_below_source_sensitivity(
        [source_a, source_c, derived_from_c, doc]
    )

    (finding,) = [f for f in findings if f.path == "concepts/mixed.md"]
    assert finding.remediation == "openkos set-sensitivity concepts/mixed private"


def test_below_source_sensitivity_findings_carry_no_remediation() -> None:
    """`remediation` is opt-in per kind, not retrofitted onto all nine.

    `below-source-sensitivity` already has a working tier that reads its
    command out of the detail, and rewriting that seam is not what #693 asks
    for. An empty default keeps every other kind byte-identical.
    """
    source = _doc("sources/a", doc_type="Source", sensitivity="confidential")
    descendant = _doc("concepts/d", sensitivity="public", provenance=("sources/a",))

    findings = lint.check_below_source_sensitivity([source, descendant])

    (finding,) = findings
    assert finding.kind == "below-source-sensitivity"
    assert finding.remediation == ""


def test_multi_source_uncovered_spells_exactly_one_runnable_command() -> None:
    """The detail spells ONE `openkos ...` command, and it is the one that works.

    `next` echoes this detail verbatim as its reason line, so a negated
    `openkos backfill-sensitivity` sitting there in copy-paste shape would
    put the one command that cannot help back on the screen -- which is
    exactly what `cli/next_action.py`'s trap-1 filter exists to prevent. The
    sweep is still named, as prose; it is no longer offered as a command.
    """
    source_a = _doc("sources/a", doc_type="Source", sensitivity="public")
    source_c = _doc("sources/c", doc_type="Source", sensitivity="confidential")
    derived_from_c = _doc(
        "concepts/from-c", sensitivity="confidential", provenance=("sources/c",)
    )
    doc = _doc(
        "concepts/mixed",
        sensitivity="public",
        provenance=("sources/a", "concepts/from-c"),
    )

    findings = lint.check_below_source_sensitivity(
        [source_a, source_c, derived_from_c, doc]
    )

    (finding,) = [f for f in findings if f.path == "concepts/mixed.md"]
    spans = re.findall(r"`([^`]+)`", finding.detail)
    commands = [s for s in spans if s.startswith("openkos ")]
    assert commands == ["openkos set-sensitivity concepts/mixed confidential"]
    # The sweep is still named -- just never as something to run.
    assert "backfill-sensitivity sweep" in finding.detail


def test_a_backtick_bearing_identity_never_forges_a_command_in_the_detail() -> None:
    """A ``` in the concept id must not close the command span early.

    This is #274's defect, one field over. That issue fixed it for a Source's
    `resource`; #693 introduced a SECOND value interpolated inside a backtick
    span -- the concept's own identity -- and a backtick in it closes the span
    early, leaving a well-formed `openkos set-sensitivity <other id> <level>`
    behind. `next` echoes this detail verbatim as its reason line, so the
    forged command would reach the screen looking exactly like a real
    recommendation.

    A concept id really can carry one: identity is derived from the on-disk
    path, and a backtick is a legal POSIX filename character.
    """
    source_a = _doc("sources/a", doc_type="Source", sensitivity="public")
    source_c = _doc("sources/c", doc_type="Source", sensitivity="confidential")
    derived_from_c = _doc(
        "concepts/from-c", sensitivity="confidential", provenance=("sources/c",)
    )
    doc = _doc(
        "concepts/od`d",
        sensitivity="public",
        provenance=("sources/a", "concepts/from-c"),
    )

    findings = lint.check_below_source_sensitivity(
        [source_a, source_c, derived_from_c, doc]
    )

    (finding,) = [f for f in findings if f.path == "concepts/od`d.md"]
    # No `openkos ...` span at all -- not a truncated one, not a forged one.
    assert [s for s in re.findall(r"`([^`]+)`", finding.detail) if "openkos" in s] == []
    assert finding.remediation == ""
    # The finding still fires and still says what to do about it.
    assert "below the high-water mark of what it cites" in finding.detail
    assert "rename" in finding.detail


def test_the_safe_argument_regexes_have_not_drifted_apart() -> None:
    """`lint._SAFE_COMMAND_ARGUMENT` and `next_action._SAFE_ARGUMENT` agree.

    They are separate objects on purpose -- `lint` is a leaf that `cli` reads,
    so importing across that boundary would invert the dependency -- but
    `remediation`'s whole claim to be trustworthy downstream is that it
    already cleared the bar its consumer would apply. If the two drift, a
    `remediation` `lint` considers safe could be one `next` would refuse, and
    the guarantee becomes a comment rather than a fact.
    """
    from openkos.cli import next_action

    assert lint._SAFE_COMMAND_ARGUMENT.pattern == next_action._SAFE_ARGUMENT.pattern
