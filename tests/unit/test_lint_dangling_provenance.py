"""Unit tests for `lint.check_dangling_provenance` (issue #257).

`provenance:` is the one outbound-reference family `check_dangling_targets`
never scans (it reads `relations:` and body markdown links only), so a doc
whose provenance cites a mistyped or since-removed id was silent in every
check. The consequence is what earns the dedicated kind: such a doc is
excluded fail-closed from EVERY Source's provenance closure (the subset
rule), so `backfill-sensitivity` will never raise it and `set-sensitivity`
cannot cascade to it.

The check takes ONLY `docs` (no `bundle_dir`) -- the SAME structural
no-fifth-walk guard `check_dangling_targets`/`check_unextracted`/
`check_below_source_sensitivity` follow -- and it MUST NOT fire on a
Source's own raw `resource` entry: every Source is built with
`provenance=[resource]` (cli/main.py, ingest), and a raw resource path
never normalizes to a bundle id, so without the exclusion the check would
report every Source in every bundle on every run (the trap design D8
names for `resolve_backfill_raises`).
"""

from pathlib import Path

from openkos import lint


def _doc(
    identity: str,
    *,
    doc_type: str = "Concept",
    resource: str = "",
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
        resource=resource,
        provenance=provenance,
    )


def test_concept_citing_missing_id_is_flagged_with_the_consequence() -> None:
    """A concept whose `provenance` cites an id absent from `docs` produces
    one `dangling-provenance` finding, and its detail carries the
    sensitivity consequence that justified the dedicated kind: the doc is
    unreachable from any Source closure, so `backfill-sensitivity` will
    never raise it and `set-sensitivity` cannot cascade to it."""
    source = _doc("sources/a", doc_type="Source", resource="raw/a.txt")
    doc = _doc("concepts/orphaned-cite", provenance=("concepts/does-not-exist",))

    findings = lint.check_dangling_provenance([source, doc])

    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == "dangling-provenance"
    assert finding.path == "concepts/orphaned-cite.md"
    assert "'concepts/does-not-exist'" in finding.detail
    assert "backfill-sensitivity" in finding.detail
    assert "set-sensitivity" in finding.detail


def test_source_citing_only_its_own_raw_resource_is_never_flagged() -> None:
    """A Source's own raw `resource` entry never fires: `ingest` builds
    every Source with `provenance=[resource]`, and `raw/<name>` never
    resolves to a bundle id -- without this exclusion the check would
    report every Source in every bundle on every run (design D8's trap)."""
    source = _doc(
        "sources/notes",
        doc_type="Source",
        resource="raw/notes.txt",
        provenance=("raw/notes.txt",),
    )

    findings = lint.check_dangling_provenance([source])

    assert findings == []


def test_own_resource_entry_survives_collect_docs_md_stripping() -> None:
    """`collect_docs` strips `.md` off every provenance entry, so a Source
    ingested from a markdown file carries `provenance=("raw/notes",)`
    against `resource="raw/notes.md"` -- the exclusion must match the
    `.md`-stripped form too, or every markdown-ingested Source fires."""
    source = _doc(
        "sources/notes",
        doc_type="Source",
        resource="raw/notes.md",
        provenance=("raw/notes",),
    )

    findings = lint.check_dangling_provenance([source])

    assert findings == []


def test_mixed_provenance_fires_only_for_the_dangling_id() -> None:
    """A doc citing its own resource entry PLUS a dangling id fires exactly
    once, for the dangling id only -- the own-resource exclusion is
    per-entry, never per-doc."""
    source = _doc(
        "sources/notes",
        doc_type="Source",
        resource="raw/notes.txt",
        provenance=("raw/notes.txt", "concepts/does-not-exist"),
    )

    findings = lint.check_dangling_provenance([source])

    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == "dangling-provenance"
    assert finding.path == "sources/notes.md"
    assert "'concepts/does-not-exist'" in finding.detail


def test_resolvable_provenance_yields_no_finding() -> None:
    """A provenance entry naming a collected doc's identity resolves and is
    never flagged."""
    source = _doc("sources/a", doc_type="Source", resource="raw/a.txt")
    derived = _doc("concepts/derived", provenance=("sources/a",))

    findings = lint.check_dangling_provenance([source, derived])

    assert findings == []


def test_one_finding_per_unique_doc_target_pair() -> None:
    """A missing id cited twice by the SAME doc reports once (dedup), while
    two docs citing the same missing id report once EACH, in doc order then
    each doc's own provenance order -- the same one-finding-per-unique-pair
    contract `check_dangling_targets` pins."""
    doc_a = _doc(
        "concepts/a",
        provenance=("concepts/gone", "concepts/gone", "concepts/also-gone"),
    )
    doc_b = _doc("concepts/b", provenance=("concepts/gone",))

    findings = lint.check_dangling_provenance([doc_a, doc_b])

    assert [(f.path, f.kind) for f in findings] == [
        ("concepts/a.md", "dangling-provenance"),
        ("concepts/a.md", "dangling-provenance"),
        ("concepts/b.md", "dangling-provenance"),
    ]
    assert "'concepts/gone'" in findings[0].detail
    assert "'concepts/also-gone'" in findings[1].detail
    assert "'concepts/gone'" in findings[2].detail
