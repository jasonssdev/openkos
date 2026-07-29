"""Characterization tests for `bundle.provenance.resolve_source_raises` and
`bundle.provenance.find_unresolvable_provenance` -- pinned BEFORE the
extraction out of `set_sensitivity_cmd`'s inline scan (`main.py:3339-3411`,
design D1/D7/D8). Every assertion here mirrors byte-identical behavior the
extraction must preserve; these are pure `Mapping[str, str]` fixtures, no
filesystem, no CLI."""

from openkos.bundle import provenance as bundle_provenance
from openkos.model import okf


def _doc(*, sensitivity: str | None, provenance: list[str]) -> str:
    metadata: dict[str, object] = {
        "type": "Concept",
        "title": "Doc",
        "provenance": provenance,
    }
    if sensitivity is not None:
        metadata["sensitivity"] = sensitivity
    return okf.dump_frontmatter(metadata, "body text")


class TestResolveSourceRaises:
    def test_raises_descendants_sorted_by_concept_id(self) -> None:
        files = {
            "sources/a.md": _doc(sensitivity="public", provenance=[]),
            "concepts/zeta.md": _doc(sensitivity="public", provenance=["sources/a"]),
            "concepts/alpha.md": _doc(sensitivity="public", provenance=["sources/a"]),
        }

        raises = bundle_provenance.resolve_source_raises(
            files, source_id="sources/a", level="confidential"
        )

        assert [entry.concept_id for entry in raises] == [
            "concepts/alpha",
            "concepts/zeta",
        ]

    def test_content_is_byte_identical_to_dump_frontmatter(self) -> None:
        files = {
            "sources/a.md": _doc(sensitivity="public", provenance=[]),
            "concepts/zeta.md": _doc(sensitivity="public", provenance=["sources/a"]),
        }

        raises = bundle_provenance.resolve_source_raises(
            files, source_id="sources/a", level="confidential"
        )

        raise_ = raises[0]
        metadata, body = okf.load_frontmatter(files["concepts/zeta.md"])
        metadata["sensitivity"] = "confidential"
        assert raise_.current == "public"
        assert raise_.new_level == "confidential"
        assert raise_.content == okf.dump_frontmatter(metadata, body)

    def test_source_root_itself_is_excluded(self) -> None:
        files = {
            "sources/a.md": _doc(sensitivity="public", provenance=[]),
        }

        raises = bundle_provenance.resolve_source_raises(
            files, source_id="sources/a", level="confidential"
        )

        assert raises == []

    def test_descendant_at_or_above_target_is_not_staged(self) -> None:
        files = {
            "sources/a.md": _doc(sensitivity="public", provenance=[]),
            "concepts/high.md": _doc(
                sensitivity="confidential", provenance=["sources/a"]
            ),
        }

        raises = bundle_provenance.resolve_source_raises(
            files, source_id="sources/a", level="private"
        )

        assert raises == []

    def test_dirty_current_is_ranked_fail_closed(self) -> None:
        files = {
            "sources/a.md": _doc(sensitivity="public", provenance=[]),
            "concepts/dirty.md": _doc(
                sensitivity="not-a-level", provenance=["sources/a"]
            ),
        }

        raises = bundle_provenance.resolve_source_raises(
            files, source_id="sources/a", level="private"
        )

        assert len(raises) == 1
        assert raises[0].current == "not-a-level"
        assert raises[0].new_level == "confidential"

    def test_only_root_provenance_closure_members_are_staged(self) -> None:
        """A concept citing an unrelated Source is never staged (design D6's
        conservative subset rule, unchanged by the extraction)."""
        files = {
            "sources/a.md": _doc(sensitivity="public", provenance=[]),
            "sources/b.md": _doc(sensitivity="public", provenance=[]),
            "concepts/only-b.md": _doc(sensitivity="public", provenance=["sources/b"]),
        }

        raises = bundle_provenance.resolve_source_raises(
            files, source_id="sources/a", level="confidential"
        )

        assert raises == []


class TestFindUnresolvableProvenance:
    def test_reports_dangling_entry(self) -> None:
        files = {
            "concepts/a.md": _doc(sensitivity="public", provenance=["sources/missing"]),
        }

        result = bundle_provenance.find_unresolvable_provenance(files)

        assert result == [("concepts/a", "sources/missing")]

    def test_known_extra_ids_excludes_entry(self) -> None:
        files = {
            "concepts/a.md": _doc(sensitivity="public", provenance=["sources/target"]),
        }

        result = bundle_provenance.find_unresolvable_provenance(
            files, known_extra_ids={"sources/target"}
        )

        assert result == []

    def test_order_is_files_order_then_provenance_list_order_no_dedupe(self) -> None:
        files = {
            "concepts/b.md": _doc(
                sensitivity="public",
                provenance=[
                    "sources/missing-1",
                    "sources/missing-1",
                    "sources/missing-2",
                ],
            ),
            "concepts/a.md": _doc(
                sensitivity="public", provenance=["sources/missing-3"]
            ),
        }

        result = bundle_provenance.find_unresolvable_provenance(files)

        assert result == [
            ("concepts/b", "sources/missing-1"),
            ("concepts/b", "sources/missing-1"),
            ("concepts/b", "sources/missing-2"),
            ("concepts/a", "sources/missing-3"),
        ]

    def test_resource_shaped_entry_is_unresolvable(self) -> None:
        """A raw resource path (e.g. `raw/<resource>`) never normalizes to a
        bundle id, so it always reports as unresolvable (design D6/D8)."""
        files = {
            "sources/a.md": _doc(sensitivity="public", provenance=["raw/report.pdf"]),
        }

        result = bundle_provenance.find_unresolvable_provenance(files)

        assert result == [("sources/a", "raw/report.pdf")]

    def test_resolved_entries_are_silent(self) -> None:
        files = {
            "sources/a.md": _doc(sensitivity="public", provenance=[]),
            "concepts/a.md": _doc(sensitivity="public", provenance=["sources/a"]),
        }

        result = bundle_provenance.find_unresolvable_provenance(files)

        assert result == []

    def test_malformed_frontmatter_sibling_is_skipped_not_raised(self) -> None:
        """A sibling file with malformed YAML frontmatter is SKIPPED by the
        scan rather than crashing the command -- a deliberate WIDENING from
        the original inline loop's narrow `except (OSError, ValueError)`
        to `except Exception`, matching this module's other broad-except
        conventions (`provenance.py:194`, `:473`). This is NOT a
        byte-identical move: `frontmatter.loads` raises
        `yaml.parser.ParserError` (a `yaml.YAMLError`, not a `ValueError`)
        on malformed YAML, so on `main` a malformed sibling crashes
        `set-sensitivity` with an uncaught traceback; here it is silently
        skipped, same as any other file whose frontmatter fails to parse."""
        files = {
            "concepts/a.md": _doc(sensitivity="public", provenance=["sources/missing"]),
            "concepts/malformed.md": "---\nkey: [unterminated\n---\nbody",
        }

        result = bundle_provenance.find_unresolvable_provenance(files)

        assert result == [("concepts/a", "sources/missing")]

    def test_scoped_to_a_root_reports_a_reachable_citers_dangling_entry(self) -> None:
        """A concept citing `[<invoked source>, ghost]` IS in the invoked
        Source's reporting scope, so a scoped call still reports its dangling
        entry -- the concept the fail-closed subset rule excludes from the
        closure, and the one whose warning matters most (#232)."""
        files = {
            "concepts/citer.md": _doc(
                sensitivity="public", provenance=["sources/a", "ghost"]
            ),
        }

        result = bundle_provenance.find_unresolvable_provenance(
            files, known_extra_ids={"sources/a"}, root_ids={"sources/a"}
        )

        assert result == [("concepts/citer", "ghost")]

    def test_default_root_ids_stays_bundle_wide_but_a_scope_narrows_it(self) -> None:
        """The `root_ids=None` default is byte-identical to the historical
        bundle-wide scan (every citing concept, `files` order then list
        order); passing a scope reports ONLY concepts reachable from that
        root, dropping an unrelated Source's own unresolvable raw resource
        and everything derived from it (#232)."""
        files = {
            "sources/b.md": _doc(sensitivity="public", provenance=["b.txt"]),
            "concepts/only-b.md": _doc(
                sensitivity="public", provenance=["sources/b", "ghost"]
            ),
            "concepts/citer.md": _doc(
                sensitivity="public", provenance=["sources/a", "missing"]
            ),
        }

        bundle_wide = bundle_provenance.find_unresolvable_provenance(
            files, known_extra_ids={"sources/a"}
        )
        scoped = bundle_provenance.find_unresolvable_provenance(
            files, known_extra_ids={"sources/a"}, root_ids={"sources/a"}
        )

        assert bundle_wide == [
            ("sources/b", "b.txt"),
            ("concepts/only-b", "ghost"),
            ("concepts/citer", "missing"),
        ]
        assert bundle_wide == bundle_provenance.find_unresolvable_provenance(
            files, known_extra_ids={"sources/a"}, root_ids=None
        )
        assert scoped == [("concepts/citer", "missing")]

    def test_scoped_call_omits_a_concept_citing_only_an_unresolvable_id(self) -> None:
        """DELIBERATE COVERAGE GAP (#232): a concept whose `provenance` names
        ONLY an unresolvable id is not reachable from the invoked Source at
        all, so a scoped call stays silent about it even though it is
        genuinely broken. NOTHING reports this case today -- `lint`'s
        dangling check scans `relations:` and body links only, never
        `provenance:`. Catching it needs a bundle-wide sweep that `lint` is
        the INTENDED FUTURE owner of, tracked as issue #257 and never
        `set-sensitivity`'s job."""
        files = {
            "concepts/orphan.md": _doc(sensitivity="public", provenance=["ghost"]),
        }

        scoped = bundle_provenance.find_unresolvable_provenance(
            files, known_extra_ids={"sources/a"}, root_ids={"sources/a"}
        )

        assert scoped == []
        assert bundle_provenance.find_unresolvable_provenance(files) == [
            ("concepts/orphan", "ghost")
        ]

    def test_known_ids_come_from_the_full_files_mapping_not_the_scope(self) -> None:
        """Only the set of CITING concepts is scoped; the id-EXISTENCE set is
        still computed from the FULL `files` mapping. An in-scope concept
        citing an id that exists elsewhere in the bundle but OUTSIDE the
        scope is resolvable and must stay silent -- filtering the snapshot
        itself would invent a false warning (#232)."""
        files = {
            "concepts/citer.md": _doc(
                sensitivity="public", provenance=["sources/a", "concepts/out-of-scope"]
            ),
            "concepts/out-of-scope.md": _doc(
                sensitivity="public", provenance=["sources/b"]
            ),
        }

        result = bundle_provenance.find_unresolvable_provenance(
            files, known_extra_ids={"sources/a"}, root_ids={"sources/a"}
        )

        assert result == []

    def test_scoped_order_is_files_order_then_list_order_no_dedupe(self) -> None:
        """The pinned ordering contract holds on the SCOPED path too, not
        only with `root_ids=None`: `files` iteration order, then each file's
        `provenance:` list order, with NO dedupe -- one tuple per
        occurrence, including the duplicate `ghost` entry. Every citing
        concept here is reachable from `sources/a`, so the scope filter
        removes nothing and only the ordering is under test (#232)."""
        files = {
            "concepts/b.md": _doc(
                sensitivity="public",
                provenance=["sources/a", "ghost", "ghost", "missing-1"],
            ),
            "concepts/a.md": _doc(
                sensitivity="public", provenance=["concepts/b", "missing-2"]
            ),
        }

        result = bundle_provenance.find_unresolvable_provenance(
            files, known_extra_ids={"sources/a"}, root_ids={"sources/a"}
        )

        assert result == [
            ("concepts/b", "ghost"),
            ("concepts/b", "ghost"),
            ("concepts/b", "missing-1"),
            ("concepts/a", "missing-2"),
        ]
