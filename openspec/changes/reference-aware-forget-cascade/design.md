# Design: Reference-Aware Forget — Scope/Depth Cascade (S2b)

## Technical Approach

Extend S2a's `forget` (`cli/main.py::forget`) from a single-concept delete to a
purge **set**, keeping the same Phase A (pure) / gate / Phase B (write) shape. A
new pure canonical helper `bundle/provenance.py::find_provenance_descendants`
computes the orphan-closure set. The existing S2a snapshot (`other_files`) is the
single input feeding descendant resolution, inbound detection, resurrection, and
tombstones — no extra bundle scan. `--scope self` (default) collapses the set to
`{root}` and reproduces S2a byte-for-byte.

## Architecture Decisions

### Decision: `find_provenance_descendants` signature + closure

**Choice**: New `src/openkos/bundle/provenance.py` (canonical layer, MUST NOT
import `openkos.graph` — same rule `references.py`/`links.py` follow).

```python
def find_provenance_descendants(
    files: Mapping[str, str], *, root_ids: Collection[str]
) -> list[str]:
    """Return the sorted orphan-closure purge set (roots + descendants)."""
```

Algorithm: `purge = set(root_ids)`; parse each file's `provenance` frontmatter
list ONCE into `id -> frozenset(provenance)` (a file that fails to parse is
skipped → preserved, fail-safe against over-delete); iterate — a candidate
`C ∉ purge` joins iff **`provenance` is non-empty AND ⊆ purge**; repeat to
fixpoint. **Non-empty guard is the critical over-deletion barrier**: an empty
provenance is vacuously a subset and would swallow unrelated concepts. Candidate
id = key `.removesuffix(".md")`; provenance entries and `root_ids` are canonical
ids. **Termination**: monotonic growth bounded by `|files|`. **Determinism**:
`sorted()` output; fixpoint set is order-independent. Source concepts
(`provenance: [sources/<name>]`, pointing to `raw/`, never in-set) are preserved;
`raw/<name>` is never touched.

**Alternatives**: recursive DFS from root (rejected — provenance is a reverse
edge; closure is cleaner and handles hand-authored multi-level chains). Reusing
`openkos.graph` (rejected — layering violation).

### Decision: Set-difference refuse gate (reuse S2a helper, no fork)

**Choice**: Call `find_inbound_references(other_files, target_id=member)` once
per purge member (loop), tagging each result with its target member. Then apply
set-difference: **drop any `InboundReference` whose `referrer_id ∈ purge_set`**.
Dedup `unverifiable` records by `referrer_id`. The per-member calls make the
`unverifiable` substring check cover EVERY member id (spec: "ANY purge-set
member's canonical id"). Intra-set backlinks (child → Source) are dropped by the
referrer∈set filter; external refs + external unverifiable referrers survive and
drive gate 1. `--force` bypasses only gate 1; the confirm gate is orthogonal.

**Alternatives**: a new set-aware scanner (rejected — forks S2a's audited
fail-closed helper). Cost: N re-scans of the snapshot, O(N·|files|), acceptable
at MVP bundle sizes.

### Decision: `--scope self` byte-identity via scope-conditional presentation

**Choice**: Unified Phase-A data path (set of size 1 for `self`); a
scope-conditional presentation/gate layer reproduces S2a's exact strings for
`self`. The count-stating confirm prompt and multi-member gate-1 message apply to
`source` only; `self` keeps S2a's verbatim `"Proceed with these changes?"` and
single-`<id>` refusal text. This resolves the tension between "Scope Selection:
self is byte-identical" and "Full-Set Preview: prompt states count" — the count
belongs to the cascade preview.

**Alternatives**: fully duplicated self branch (rejected — drift risk); force
count into self prompt (rejected — violates byte-identity scenario).

## Data Flow

    other_files snapshot (ONE read)
      ├─ find_provenance_descendants(root) ─→ purge_ids (sorted)
      ├─ per member: find_inbound_references ─→ refs ─(drop referrer∈set)→ external
      ├─ per member: supersedes edges (target∉set) ─→ resurrection
      └─ per member: title, index-removal, tombstone line
    Preview(full set + count) → gate1(external∧¬force) → gate2(confirm)
    Phase B: write index → write log → unlink N files (sorted) LAST

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `src/openkos/bundle/provenance.py` | Create | Pure orphan-closure helper |
| `src/openkos/cli/main.py` | Modify | `forget`: `--scope` opt, set-driven Phase A/B |
| `tests/…/test_forget*.py` | Modify/Create | Cascade + self-regression coverage |

## Interfaces / Contracts

- `--scope {self,source}` typer option, default `self`; invalid value →
  `ValueError` refusal (reuse S2a `except (OSError, ValueError)`).
- Preview prefixes unchanged: `~` catalog/resurrection, `-` deleted file, `!`
  external inbound, `?` unverifiable. Each purge-set id printed; count line for
  `source`.
- Phase B: `write_atomic(index)` → `write_atomic(log)` → `for id in
  sorted(purge_ids): fsio.remove_file(id)`. Non-transactional, git-recoverable.

## Testing Strategy

| Layer | What | Approach |
|-------|------|----------|
| Unit | closure: single-source children join, multi-source preserved, empty-provenance NOT swallowed, fixpoint chain, sorted determinism | pure `find_provenance_descendants` |
| Unit | set-difference: intra-set backlink dropped, external blocks, unverifiable over every member | per-member calls + filter |
| Integration | `self` byte-identity; N tombstones; count prompt; `--force` no auto-confirm; path-safety before resolution; catalog-before-unlink; partial-unlink recovery | CLI runner + fs asserts |

## Threat Matrix

| Row | Status | Behavior / RED test |
|-----|--------|---------------------|
| Path traversal on root id | Applicable | Reuse S2a `_resolve_concept_path` — refuses `..`/absolute/reserved BEFORE resolution (spec scenario) |
| Path traversal via descendant ids | Applicable | Member ids are **disk-discovered** from `other_files` keys, never user input — inherently in-bundle; assert no member path escapes `bundle_dir` |
| Destructive over-delete | Applicable | Non-empty-provenance subset guard; multi-source preservation test |
| Partial write corruption | Applicable | Catalog-first, sorted unlink last; git-recoverable; interrupted-run test |
| Shell / subprocess / routing | N/A | No process integration in this change |

## Migration / Rollout

No migration. Additive flag; default preserves S2a. Rollback: `git checkout`
restores deleted concepts + `index.md`/`log.md`; `raw/` untouched.

## Open Questions

None — all six open decisions resolved above.
