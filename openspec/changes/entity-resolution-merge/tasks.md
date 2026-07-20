# Tasks: Entity-Resolution Merge (slice 3)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | 900-1300 (new `bundle/merge.py`, `bundle/links.py`; mod `model/okf.py`, `cli/main.py`; heavy tests incl. 2 property tests; 2 ADRs; docs) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | U1→U2→U3→U4→U5 |
| Delivery strategy | ask-on-risk |
| Chain strategy | feature-branch-chain |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | PR (base) | Focused test | Runtime harness | Rollback boundary |
|---|---|---|---|---|---|
| U1 | `combine_sensitivity`+ordering | PR1 (tracker) | `pytest tests/unit/model/test_okf.py -k sensitivity` | N/A, pure fn | revert `okf.py` additions |
| U2 | ledger schema + `plan_merge`/`plan_unmerge` (library, no CLI) | PR2 (base PR1) | `pytest tests/unit/model/test_okf.py tests/unit/bundle/test_merge.py` | N/A, text in/out | revert `bundle/merge.py`+ledger |
| U3 | `bundle/links.py` rewrite/reverse | PR3 (base PR2) | `pytest tests/unit/bundle/test_links.py` | N/A, pure text | delete `bundle/links.py` |
| U4 | `merge` verb + confirm gate | PR4 (base PR3) | `pytest tests/unit/cli/test_merge.py` | `okos merge a b --auto` on scratch bundle | remove `merge` verb |
| U5 | `unmerge` + round-trip parity | PR5 (base PR4) | `pytest tests/unit/cli/test_merge_roundtrip.py` | merge then unmerge on scratch bundle, diff bytes | remove `unmerge`; human checkpoint before merge-to-main |

## Phase 1: U1 — Sensitivity HWM (`model/okf.py`)
- [x] 1.1 RED `test_okf.py`: parametrized `combine_sensitivity` (all pairs, missing→private, malformed/non-str→confidential)
- [x] 1.2 GREEN: add `SENSITIVITY_ORDER`, `combine_sensitivity(a,b)` to `okf.py`
- [x] 1.3 REFACTOR: extract `rank()`; mypy strict

## Phase 2: U2 — Merge Core (ledger + combine, library only)
- [ ] 2.1 RED: `build_merged_document` scalar-survivor-wins/list-union/freshness-most-recent
- [ ] 2.2 GREEN: `build_merged_document()` in `okf.py`
- [ ] 2.3 RED: `merged_from` ledger encode/decode round-trips via `dump_frontmatter`/`load_frontmatter`
- [ ] 2.4 GREEN: `MERGED_FROM_KEY` + LIFO ledger encode/decode
- [ ] 2.5 RED: `test_merge.py`: `plan_merge`/`plan_unmerge` pure text-in/out
- [ ] 2.6 GREEN: create `bundle/merge.py` — `MergePlan`/`UnmergePlan`, no I/O

## Phase 3: U3 — Inbound Link Rewrite (`bundle/links.py`)
- [ ] 3.1 RED: `test_links.py`: fence-masked, anchor-preserving rewrite, no over-match
- [ ] 3.2 GREEN: create `links.py` — own `_LINK_RE`/`_mask_fenced_code_blocks` (duplicated, not imported from `graph`)
- [ ] 3.3 RED: reversal bounded to recorded `{file,old_link,new_link}`, never replace-all
- [ ] 3.4 GREEN: `reverse_link_rewrites` exact-substring bounded, fail-closed if absent

## Phase 4: U4 — `merge` Verb + Confirm Gate
- [ ] 4.1 RED (threat: self/unknown-id): same-id, unknown-id refuse, no write
- [ ] 4.2 RED (threat: path-traversal): both ids via `_resolve_concept_path`
- [ ] 4.3 GREEN: `merge` verb — Phase A preview → gate (`--auto`>`review:false`>TTY>refusal) → Phase B (index/log, rewritten docs, survivor, delete absorbed last)
- [ ] 4.4 RED: declined/non-TTY-no-auto write nothing
- [ ] 4.5 RED (threat: partial write): survivor-before-delete stays recoverable
- [ ] 4.6 GREEN: wire Phase B ordering

## Phase 5: U5 — `unmerge` + Round-Trip Parity
- [ ] 5.1 RED (threat: collision): non-tail `absorbed-id` refuses, no write
- [ ] 5.2 RED: unmerge of non-merged pair refuses, no write
- [ ] 5.3 GREEN: `unmerge` verb — LIFO-tail check, restore survivor/absorbed/index/`log_before`, reverse rewrites, append audit line
- [ ] 5.4 RED property (single): merge(A,B)→unmerge(A,B) byte-identical (log.md growth excepted)
- [ ] 5.5 RED property (sequential/LIFO): merge(A,B)→merge(A,C)→unmerge(A,C)→unmerge(A,B) → original bytes
- [ ] 5.6 GREEN: fix drift until 5.4/5.5 pass
- [ ] 5.7 RED (threat: link drift): fail-closed if `new_link` absent

## Phase 6: Docs & ADR Promotion
- [ ] 6.1 Flip ADR-0002/0003 status Proposed→Accepted post-review
- [ ] 6.2 Update `docs/knowledge-object-model.md`, `docs/cli.md`
- [ ] 6.3 Verify ≥90% branch coverage, mypy strict, ruff clean
