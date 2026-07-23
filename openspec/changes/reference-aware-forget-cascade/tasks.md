# Tasks: Reference-Aware Forget — Scope/Depth Cascade (S2b)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~750–950 (prod ~250–350, tests ~450–600) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 provenance helper → PR 2 forget cascade wiring |
| Delivery strategy | ask-on-risk (default; orchestrator to confirm) |
| Chain strategy | pending — ask user: stacked-to-main vs feature-branch-chain |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

S2a alone was ~259 prod / 739 w/ tests as a single PR; S2b adds a new pure
module plus ~18 named edge cases on the most destructive verb (multi-file
delete) — PR 2 alone likely exceeds 400 lines.

### Suggested Work Units

| Unit | Goal | PR | Focused test | Runtime harness | Rollback boundary |
|------|------|----|--------------|------------------|--------------------|
| 1 | `find_provenance_descendants` closure | PR 1 | `pytest tests/unit/bundle/test_provenance.py -q` | N/A — pure function, unit tests are the harness | delete `bundle/provenance.py` + its test file |
| 2 | `forget --scope` cascade wiring | PR 2 (base PR 1) | `pytest tests/unit/cli/test_forget.py -q` | `openkos forget <source-id> --scope source --auto` in scratch workspace | `git checkout` `cli/main.py` + `test_forget.py`; PR 1 stays mergeable |

## Phase 1: Provenance Helper — RED

- [x] 1.1 `tests/unit/bundle/test_provenance.py`: single-source child joins; multi-source child joins once both sources purged; chain X←A←B fixpoint pulls all.
- [x] 1.2 Empty-provenance concept does NOT join (vacuous-subset trap); `provenance:[X,Y]`, purge `{X}` → does NOT join (orphan invariant).
- [x] 1.3 Root with no descendants → `{root}` only; sorted/deterministic regardless of input order; unparseable file skipped (preserved).

## Phase 2: Provenance Helper — GREEN

- [x] 2.1 `src/openkos/bundle/provenance.py`: `find_provenance_descendants(files, *, root_ids)`; parse `provenance` once into `id -> frozenset`; fixpoint-join iff non-empty ⊆ purge; no `openkos.graph` import.
- [x] 2.2 Run `test_provenance.py`; all green.

## Phase 3: Provenance Helper — REFACTOR

- [x] 3.1 Docstring pass (rationale, matches `references.py` style); confirm no `openkos.graph` import.

## Phase 4: Forget Cascade — RED (`tests/unit/cli/test_forget.py`)

- [ ] 4.1 Threat: path traversal on root id — `..`/absolute/reserved refuses BEFORE descendant resolution, `--scope source`.
- [ ] 4.2 Threat: destructive over-delete — multi-source child untouched by a `source` purge (full-CLI orphan-guard regression).
- [ ] 4.3 Threat: partial write corruption — index+log written before any unlink; unlinks `sorted(purge_ids)`; interrupted run leaves git-recoverable, catalog-consistent state.
- [ ] 4.4 Threat: path traversal via descendant ids — every member path stays inside `bundle_dir`; members disk-discovered only.
- [ ] 4.5 `--scope self` default byte-identical to S2a (no cascade, verbatim prompt/refusal text).
- [ ] 4.6 `--scope source` Source + 2 single-source children → 3 deletes, 3 tombstones, `index.md` updated for all 3.
- [ ] 4.7 Intra-set `## Related` backlink excluded from gate-1; external inbound ref refuses unless `--force`; external `unverifiable` referrer naming a non-root member refuses unless `--force`.
- [ ] 4.8 Preview states count; `--force` alone doesn't auto-confirm gate 2; non-TTY without `--auto` refuses even with `--force`.
- [ ] 4.9 Per-member resurrection: a member's outbound `supersedes` to an out-of-set concept is disclosed.

## Phase 5: Forget Cascade — GREEN (`src/openkos/cli/main.py`)

- [ ] 5.1 Add `--scope {self,source}` option, default `self`; invalid value → `ValueError` (reuse `except (OSError, ValueError)`).
- [ ] 5.2 After path-safety/existence: `source` → `find_provenance_descendants(other_files, root_ids={canonical_id})`; else `purge_ids = {canonical_id}`.
- [ ] 5.3 Per-member `find_inbound_references(other_files, target_id=member)`; merge; drop refs with `referrer_id ∈ purge_ids`; dedup `unverifiable` by `referrer_id`.
- [ ] 5.4 Per-member resurrection + index/log removal; preview lists every member id, per-member `!`/`?`/`~` lines, count line for `source`.
- [ ] 5.5 Scope-conditional text: `self` keeps S2a's verbatim strings; `source` states delete count in gate-1/gate-2 prompts.
- [ ] 5.6 Phase B: write `index.md` then `log.md` (N tombstones), then `for id in sorted(purge_ids): fsio.remove_file(id)` LAST.
- [ ] 5.7 Run `test_forget.py`; all new + existing S2a cases green, zero regression.

## Phase 6: Forget Cascade — REFACTOR + Verify

- [ ] 6.1 Extend `forget`'s docstring for `--scope`, cascade resolution, per-member gates.
- [ ] 6.2 `pytest tests/unit/bundle/test_provenance.py tests/unit/cli/test_forget.py -q`; confirm no `openkos.graph` import in `provenance.py`.
