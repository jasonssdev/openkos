# Exploration: rewrite-provenance-on-merge (issue #230)

## Current State

Merge's rewrite architecture lives in `src/openkos/cli/main.py` — `prepare_merge` (Phase A, ~L3779)
and `merge_core` (Phase B, ~L3892) — backed by pure primitives in `src/openkos/bundle/links.py` and
`src/openkos/bundle/relations.py`.

`prepare_merge` performs exactly ONE `bundle_dir.rglob("*.md")` walk, building
`other_files: dict[str, str]` (excluding survivor, absorbed, and reserved filenames), then runs both
scanners over that SAME snapshot:

- `bundle_links.find_inbound_link_rewrites(other_files, absorbed_id=..., survivor_id=...)` →
  `list[LinkRewrite]` — offset-exact, body markdown links only.
- `bundle_relations.find_inbound_relation_rewrites(other_files, ...)` → `list[RelationRewrite]` —
  whole-file snapshot, for third-party `relations:` entries targeting `absorbed_id`.

`touched_files = rewritten_files | relation_rewritten_files`. `merge_core` iterates `touched_files`
once, chaining `apply_link_rewrites` then `apply_relation_rewrites` on each file's in-memory text,
then `fsio.write_atomic`s it. A file needing both transforms gets both, because they touch disjoint
regions (body vs. frontmatter).

**Neither scanner, nor this write loop, ever looks at `provenance:`.** `grep -n provenance
src/openkos/bundle/merge.py` returns nothing — `bundle/merge.py` does pure ledger and document
planning, no scanning.

`src/openkos/bundle/references.py::find_inbound_references` DOES compose both scanners (calling them
with `absorbed_id == survivor_id == target_id` as a detect-only trick), but it is wired ONLY into
`forget`; its own docstring describes it as a detect-only helper with no scanning logic of its own.
**It is not the natural home for a provenance rewrite pass** — adding write and reverse logic there
would misplace merge-only machinery into a module documented and consumed as detect-only by a
different verb.

`bundle/provenance.py::find_provenance_descendants(files, *, root_ids)` is a pure reverse-edge
fixpoint closure: it seeds with `root_ids`, then repeatedly adds any concept whose `provenance` list
is non-empty AND fully contained in the set, until no change. Malformed frontmatter is skipped
(fail-safe against over-deletion). Consumers:

- `forget --scope source` (`main.py` ~L2184) — purge-set expansion.
- `set-sensitivity` (`main.py` ~L3340-3391) — when the target is `type == "Source"` and the
  assignment is a raise, resolves descendants and raises each via `okf.combine_sensitivity`, never
  lowering. Separately, at ~L3369-3386, it scans **every file in the whole bundle snapshot** (not
  just `descendant_ids`) for any `provenance` entry naming an id absent from `known_ids`, printing
  one stderr WARNING per such entry. This is the existing dangling-provenance mitigation the issue
  references, and it confirms issue #232's claim about the warning's scope. Noted as overlap only;
  **not to be fixed here.**

Neither the warning nor `find_provenance_descendants` performs any repair. Both are read-only.

## Affected Areas

- `src/openkos/cli/main.py` — `prepare_merge` / `merge_core` (a third scan and apply pass, reusing
  the existing `other_files` snapshot and `touched_files` write loop), `unmerge` (symmetric
  reversal). `set-sensitivity` needs no functional change.
- `src/openkos/bundle/provenance.py` — needs a
  `find_inbound_provenance_rewrites` / `apply_provenance_rewrites` / `reverse_provenance_rewrites`
  trio, shaped like `bundle/relations.py`'s.
- `src/openkos/bundle/merge.py` — `MergePlan` / `UnmergePlan` / `plan_merge` / `plan_unmerge` need a
  `provenance_rewrites` field threaded through, mirroring how `relation_rewrites` was added in the
  v1→v2 ledger change.
- `src/openkos/model/okf.py` — a new `ProvenanceRewrite` dataclass (mirroring `RelationRewrite`:
  `file`, `snapshot`), and `MergeLedgerEntry` needs a `provenance_rewrites` field plus a schema bump
  from the current `MERGE_LEDGER_SCHEMA_V2`.
- `src/openkos/lint.py::check_dangling_targets` — currently checks `relations:` and body links only,
  NOT `provenance:`.
- Tests: `tests/unit/bundle/test_provenance.py`, `test_relations.py`, `test_links.py`,
  `test_merge.py`, and `tests/unit/cli/test_merge.py` / `test_merge_core.py` /
  `test_merge_roundtrip.py`.

## Approaches

1. **New sibling module `bundle/provenance_rewrite.py`** — find/apply/reverse trio mirroring
   `relations.py` file-for-file.
   - Pros: cleanest separation — `provenance.py` stays closure resolution, the new module stays
     merge rewrite mechanics, mirroring the links/relations split.
   - Cons: near-duplicates `relations.py`'s scanning logic unless factored into a shared helper.
   - Effort: medium.
2. **Extend the existing `bundle/provenance.py`** with the trio alongside
   `find_provenance_descendants`.
   - Pros: fewer new files; colocates all provenance list-field logic in the one canonical-layer
     module that already owns the concept.
   - Cons: broadens that module's documented single responsibility, so its docstring needs updating.
   - Effort: medium, with less file-creation overhead.
3. **Route through `bundle/references.py`.** Rejected — detect-only, consumed exclusively by
   `forget`, and explicitly documented as having no scanning logic of its own.

## Recommendation

Option 2. Extend `bundle/provenance.py`, shaped exactly like `relations.py`'s trio: whole-file
snapshot, drift-checked reversal. `provenance` is a YAML list field like `relations:`, not an inline
markdown link, so the snapshot shape is the right one — not the offset-exact shape `links.py` uses.

Wire it into `prepare_merge` as a third scanner over the SAME `other_files` snapshot (zero extra
walks), into `merge_core` as a third link in the existing per-file transform chain, into the
ledger and plan dataclasses with a schema bump and backward-compatible reads, and into `unmerge` as
a symmetric drift-checked reversal.

**Ship as one change, not split.** The ledger schema bump and the `MergePlan` / `UnmergePlan` shape
changes are shared by both directions. Splitting them would leave an intermediate state where
`unmerge` cannot round-trip a merge performed by the not-yet-released other half — the same reason
the historical relations work shipped merge and unmerge together. The tasks phase can still slice
delivery into stacked PRs under the review budget without splitting the underlying design decision.

## Risks

**Is retargeting provenance ever wrong?** This deserves a real answer, because provenance is a
first-class non-negotiable in AGENTS.md. Retargeting `sources/absorbed` → `sources/survivor`
overwrites the specific historical fact "derived from concept X". But `merge` already establishes
that the absorbed id ceases to be independently addressable, and every other pointer — links,
relations — is already retargeted for exactly that reason. The `merged_from` ledger plus `unmerge`
IS the audit trail; a dangling pointer is not a superior record of history, it is just a broken one.

Note the established local rule: `build_merged_document` already **unions** (never overwrites) the
survivor's own `provenance` list when merging two documents. So the new third-party rewrite should
retarget-then-dedupe, mirroring `apply_relation_rewrites`, not naive substring replacement. Design
must make union and dedupe an explicit, tested requirement.

**Provenance targets are NOT Source-only — this is the easiest correctness miss in the change.**
`okf.build_concept` does not restrict `provenance` values by type. `ingest` always sets
`provenance=[f"sources/{source_slug}"]`, which is a Source, but `query --save` (`main.py` ~L6463)
sets `provenance=[citation.concept_id for citation in citations]` — arbitrary cited concept ids,
Source or not. A merge absorbing ANY concept can therefore orphan third-party provenance. The
scanner MUST be ungated, like `relations.py`, and MUST NOT be `type == "Source"`-gated the way
`set-sensitivity`'s propagation is.

**Ledger schema bump** (v2→v3) needs the same "reader still accepts older schema versions"
discipline already established for v1→v2.

**`unmerge`'s existing two-way rule** — a file present in both `link_rewrites` and
`relation_rewrites` skips link reversal — needs generalizing to three rewrite kinds. The precedence
rule for a file touched by both relation and provenance rewrites must be worked out in design, not
assumed.

**Overlap with #232** (set-sensitivity's warning scans the whole bundle rather than the invoked
Source's closure) is real but explicitly out of scope. Flag it in the proposal so reviewers do not
conflate the two.

## Scope Boundaries

- No `lint` check for dangling provenance in this change — detection is useful but is not repair,
  and `check_dangling_targets` gaining a third axis is its own slice. Follow-up.
- No change to `set-sensitivity`'s warning scope (#232).
- No change to `find_provenance_descendants`' closure semantics.

## Ready for Proposal

Yes. Architecture, exact seam, the non-Source-target correctness requirement, the walk-discipline
non-issue, and the unmerge symmetry requirement are all confirmed against real code with file and
line references.

Open questions to carry into design: the exact union and dedupe semantics for a third-party file
whose provenance already lists both the survivor and the absorbed id, and the three-way
link/relation/provenance rewrite-kind precedence rule for `unmerge`.
