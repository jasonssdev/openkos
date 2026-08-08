# Design: Exact-title duplicates are visible across OKF types

**Issue**: [#437](https://github.com/jasonssdev/openkos/issues/437). **Mode**:
hybrid. **Engram twin**: `sdd/cross-type-duplicate-candidates/design`.

## Technical Approach

Split today's `_keyed_docs_by_type` into a *flat* shared prelude plus a *pure*
partition step. The HIGH pass consumes the flat list (one cross-type bucket
keyed by normalized title, still bucket-then-sort, zero pairwise work);
ACRONYM/LOW keep consuming the per-type partitions, unchanged. `CandidateGroup`
gains `member_types`, derived by default so the 76 existing construction sites
compile untouched. Adjudication renders types honestly for cross-type groups
only; merge behavior is documented, not changed.

## Architecture Decisions

### D1 — Bucketing: flat prelude + pure partition (not a parameter, not a sibling)

| Option | Tradeoff | Decision |
|---|---|---|
| `cross_type: bool` param on `_keyed_docs_by_type` | One function returns two incompatible shapes | Rejected |
| Sibling `_keyed_docs_all_types` doing its own walk | Two walks; eligibility/deprecation logic can drift — exactly what #216's prelude forbids | Rejected |
| **Split: `_eligible_keyed_docs` (I/O) + `_keyed_docs_by_type` (pure)** | One extra function; both tiers provably share one walk | **Chosen** |

```python
def _eligible_keyed_docs(bundle_dir, *, include_deprecated) -> list[tuple[str, str, str]]:
    """(concept_id, okf_type, normalize_key(title)), concept_id ascending."""

def _keyed_docs_by_type(keyed) -> list[tuple[str, list[tuple[str, str]]]]:
    """Pure partition of the SAME list -- no bundle read. Shape unchanged."""

def _high_candidate_groups(keyed) -> list[CandidateGroup]:
    """Cross-type: buckets the FLAT list by normalized key."""
```

Both entry points call `_eligible_keyed_docs` then `_high_candidate_groups`;
HIGH groups are still constructed in exactly one place, so drift stays
impossible. `find_candidates_report` computes `_pairs_covered_by_high_groups`
once over the **global** HIGH set and reuses that one set inside every per-type
LOW loop — cross-type pairs never appear there, so the exclusion is a superset
and same-type exclusion is preserved verbatim.

### D2 — Label and ordering

`okf_type` = the shared type when all member types are equal, else
`"+".join(sorted(set(member_types)))` — deduped, ascending, `+` separator, via
one module-level `_type_label()`. Canonical example: `"Concept+Entity"`, never
`"Entity+Concept"`. The final sort key `(okf_type, _TIER_ORDER[tier],
member_ids)` and `_cap_rank_key` are **unchanged**: they only need a stable
string, and `"Concept" < "Concept+Entity" < "Entity"` is a deterministic slot.
Both entry points apply the identical key, so the `:636`/`:660` equivalence and
the `:1075` prefix relation survive by construction; the `:1075` fixture is
Concept-only, so its literal ordering does not move.

### D3 — `__post_init__` with `object.__setattr__`

No precedent exists in `src/` (grep: zero hits). Adopted anyway, narrowly:
`member_types: tuple[str, ...] = ()`, and `__post_init__` sets
`(okf_type,) * len(member_ids)` when empty and raises `ValueError` on a
length mismatch. Rejected: a required field (76 mechanical test edits, and the
field can be forgotten); `tuple | None` + property (reintroduces the `None`
branch at every consumer the proposal set out to avoid).

### D4 — Prompt rendering

`_build_messages` gains `member_types_by_id: Mapping[str, str] | None`. **Keyed
by concept_id, never by index** — `adjudicate_candidates` filters blocked ids
(adjudication.py:336) and `_load_members` drops unreadable ones, so `members`
is a subset and positional alignment would mislabel. Single-type groups pass
`None` and emit byte-identical prompts. Cross-type adds: the joined `OKF TYPE`
value, a `(OKF type: {t})` suffix on each member header, and one user-turn NOTE
that a type disagreement alone does not make members different entities.
`_SYSTEM_PROMPT`, the verdict schema, and `--json` keys are untouched.

### D5 — Survivor type

No code change. `build_merged_document`'s docstring names `type` explicitly
under the existing scalar rule (okf.py:995-997, 1045-1047), plus one pinning
test.

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/resolution/candidates.py` | Modify | D1 split, `member_types`, `_type_label`, docstrings |
| `src/openkos/resolution/adjudication.py` | Modify | D4 rendering |
| `src/openkos/model/okf.py` | Modify | D5 docstring only |
| `tests/unit/resolution/test_candidates.py` | Modify | Replace `:167`; extend `:636`/`:660`/`:688`/`:1075`; new invariants |
| `tests/unit/resolution/test_adjudication.py` | Modify | Prompt-bytes tests |
| `tests/unit/model/test_okf.py` | Modify | Survivor-type pin |

## Testing Strategy (strict TDD — RED first)

| Test | Action |
|---|---|
| `test_cross_type_identical_normalized_title_produces_no_candidate` (:167) | **Replace** with its inverse: one HIGH group, `member_types == ("Concept","Entity")`, `okf_type == "Concept+Entity"` |
| `:179` two-types-own-pairs | Keep; re-verify no accidental cross-type hit |
| `:636`, `:660` equivalence | Extend fixture with a cross-type pair; literal `member_ids` lists updated |
| `:688` `near_match_score` spy | Keep verbatim — zero calls must still hold |
| `:1075` prefix | Keep; assert unchanged literal ordering |
| New | `member_types` length invariant + `ValueError`; default derivation; `_type_label` determinism (`Entity`,`Concept` → `"Concept+Entity"`); ACRONYM/LOW byte-identical on a no-overlap fixture; single-type prompt bytes unchanged; cross-type prompt names both types and tags members; blocked-member prompt still tags correctly; survivor `type` wins on cross-type merge |

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file
classification, or process-integration boundary.

## Migration / Rollout

No migration. Candidates are ephemeral; nothing in `bundle/` or `state/`
changes shape.

## Size Forecast

~430 changed lines (candidates.py ~115, adjudication.py ~35, okf.py ~6, tests
~275). Well under the 2000-line budget — **single PR, no chaining**. The 76
`CandidateGroup(` construction sites need zero edits (D3); if that assumption
breaks, the footprint roughly doubles and chaining should be reconsidered.

## Open Questions

- [ ] Cross-type bucketing can merge a 2-member group and a third same-title
      doc of another type into an N>2 group, which `adjudicate --apply` skips
      (`_echo_n_gt2_skip`, main.py:1356). Behavior is correct and already
      surfaced to the operator, but should be pinned by a test.
