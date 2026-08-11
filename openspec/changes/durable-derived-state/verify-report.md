# Verification Report: durable-derived-state

**Change**: durable-derived-state (issue #550), three chained PRs, tip `feat/reindex-doctor-repair-1b`.
**Mode**: Full artifact set (proposal/specs/design/tasks/apply-progress) — full verification.
**Verdict**: **FAIL** — one undisclosed CRITICAL spec/implementation gap blocks archive.

## Command Evidence (independently re-run at the tip)

| Command | Result |
|---|---|
| `uv run pytest` | 4208 passed, 1 skipped in 128.69s (unpiped, per Strict TDD mandate) |
| `uv run pytest tests/unit/bundle/test_ledger.py tests/unit/cli/test_repair.py tests/unit/cli/test_doctor.py tests/unit/resolution/test_contradiction.py -q` | 208 passed (targeted re-run of priority-check surface) |
| `ruff check` / `ruff format --check` | clean |
| `mypy .` | no issues, 192 source files |
| Coverage | 97.05% (gate 90% branch) |
| `git status` | clean |

## Task Completeness

34/34 tasks across PR #1/#2/#3 marked `[x]`. Cross-checked task IDs against `apply-progress.md`'s TDD Cycle Evidence tables and the actual test files/functions named — all cited tests exist and pass (spot-checked `test_merged_content_blocked_called_once_per_ledger_entry_not_per_survivor`, `test_single_merge_then_unmerge_is_byte_identical_modulo_log`, `test_repair_refuses_with_no_override_when_any_survivor_has_two_or_more_entries`, `test_rglob_md_walk_excludes_ledger_sidecar`). Task completion claim: **PASS**.

## Priority Requirement-by-Requirement Table

| # | Requirement | Verdict | Evidence |
|---|---|---|---|
| 1 | No `merged_from` key remains in concept frontmatter after merge; entries under `bundle/.state/ledger/` | PASS | `merge.py::plan_merge` returns `ledger_entries`, never writes `MERGED_FROM_KEY` into survivor metadata (confirmed by reading `merge.py`/`ledger.py`); `bundle/ledger.py::ledger_path_for` roots every sidecar under `bundle/.state/ledger/**.ledger.okf`, `.okf` suffix (never `.md`) |
| 2 | merge → unmerge byte-for-byte parity, genuinely through the sidecar (not just via old frontmatter path) | PASS — coverage gap claim REJECTED | `test_merge_roundtrip.py::_bundle_bytes_snapshot` uses `bundle_dir.rglob("*")` (not `*.md`), so it snapshots every file including `bundle/.state/ledger/*.ledger.okf`. `_assert_byte_parity_except_log` asserts `post_snapshot.keys() == pre_snapshot.keys()` — an un-cleaned-up sidecar left behind by `unmerge` would be a new key and fail the test. Confirmed `ledger.write_entries` deletes the sidecar file when `entries` becomes empty. The pre-existing test genuinely observes the sidecar location; task 2.9's "no new test needed" claim is verified true, not a hidden gap. |
| 3 | `merged_content_blocked` invoked once PER LEDGER ENTRY, never once per survivor | PASS | `test_merged_content_blocked_called_once_per_ledger_entry_not_per_survivor` spies on `sensitivity.merged_content_blocked`, asserts `len(seen_entries) == 3` for a 3-entry survivor. A hoisted-to-per-survivor implementation would yield 1, not 3 — the test cannot pass under the wrong behavior. Genuinely discriminating, not a tautology. |
| 4 | Two opposite walks (EXCLUDE ledger from reference scans; INCLUDE ledger in forget/purge/sensitivity sweeps) as separate mechanisms, not one shared predicate | PASS | EXCLUDE side is structural: `LEDGER_SUFFIX = ".ledger.okf"` never matches `rglob("*.md")` — zero shared code with the INCLUDE side. INCLUDE side is `bundle_ledger.iter_ledgers()`, called from `cli/main.py:596` (lint), `_sweep_ledger_sidecars_for_ids` (forget/purge), and internally by `ledger.py`'s own `scan_torn_writes`/`scan_nesting_violations`. `test_ledger_walk_exclusion.py` locks in the EXCLUDE side; `test_forget.py`/`test_purge.py`/`test_observability.py` cover the INCLUDE side. No single function serves both directions. |
| 5 | `_autocommit` stages `bundle/.state/ledger/**` at BOTH call sites (`merge` command + curate's `_commit_one_merge`) | PASS | `cli/main.py:1585` (`_commit_one_merge`) and `cli/main.py:7350` (`merge()`) both append `result.ledger_sidecar_path`/`merge_result.ledger_sidecar_path` to their `_autocommit` path list. Backed by `test_merge.py`'s `_autocommit` staging test (task 2.4). |
| 6 | `purge`'s `git filter-repo` path set covers `bundle/.state/ledger/**` | PARTIAL — matches disclosed gap, spec Scenario 2 unimplemented | `expunge_targets` (cli/main.py:4616-4621) includes each purge-set member's OWN sidecar (privacy-purge spec Scenario 1 — PASS, tested by `test_purging_a_merge_survivor_removes_its_ledger_sidecar_from_history`, mutation-tested). Scenario 2 (an absorbed concept's body surviving as a fragment inside a DIFFERENT survivor's sidecar) is genuinely NOT implemented — `git filter-repo`'s callback is hardcoded to `bundle/index.md`/`bundle/log.md` line-rewriting and cannot rewrite YAML frontmatter fragments inside another file. This was disclosed (task 3.4, apply-progress) and is independently justified: `_resolve_concept_path`'s existence gate means an already-absorbed id can never be a live purge target today, so Scenario 2 is currently unreachable via the CLI. Documented gap, not hidden — but the spec scenario remains formally unsatisfied. Should be filed as follow-up, tracked against the existence-gate precondition. |
| 7 | `doctor` stays read-only; `repair` refuses flagged ledgers with no override | PASS (with a scope caveat, see Deviation 3 below) | `doctor`'s checks 12/13 (`scan_torn_writes`, `scan_nesting_violations`) only read; `test_doctor.py`'s "never writes" scenario passes. `repair` has two refusal gates (torn-write, bundle-wide ≥2-entries), neither has an override flag — confirmed by reading `repair()`'s body and by an independent confirmation during review that the `repair` verb takes no parameters. |
| 8 | Reindex embed text matches `fts.py`'s title/description/tags/body composition; forced through the existing embedding-model-tag re-embed gate; #554 verified closed | PASS on mechanism / **#554 is OPEN, not closed** | `_compose_embed_text` in `reindex.py` builds `"\n\n".join(title, description, tags_text, body)` from the identical four fields `fts.py::_populate_docs_table` indexes (`title`, `description`, `tags`, `body`) — verified by reading both functions side by side. `_effective_model_tag` composes `EMBED_COMPOSITION_TAG` (`"compose-v1"`) into the SAME `model_tag` string the pre-existing model-change gate (`stored_model_tag != effective_model_tag`) already compares — no parallel gate was invented. **Independently checked via `gh issue view 554`: issue #554 is currently OPEN.** This is expected, not a defect — the change has not merged to `main` yet (still on a feature branch), so no "Closes #554" keyword has fired. Flagging per the explicit review instruction not to assume it closed; it will close automatically on merge if the eventual merging PR/commit uses the closing keyword — confirm that keyword is present before merge. |

## CRITICAL — Undisclosed spec/implementation gap (new finding, not in the apply phase's disclosed list)

**`entity-resolution-merge/spec.md`'s two ADDED requirements are entirely unimplemented, untested, and were never disclosed as a gap or deviation:**

1. **"`merge` Refuses On A Doctor-Flagged Ledger, With `--force`"** (Slice 1a, unless-noted default) — the spec requires `openkos merge` to run the doctor merge-ledger-integrity check (Check B, post-merge-mutation) against the survivor's sidecar before Phase A completes, refuse with exit non-zero when flagged, and support `--force` to bypass. **`merge`'s `typer.Option` list has no `--force` parameter at all** (only `--auto`) — confirmed by reading `merge()`'s full signature (`cli/main.py:7108-7122`). `merge` calls `_reject_torn_ledger_write` (Check A only) and never calls `scan_nesting_violations` (Check B) anywhere in its flow. Zero scenarios of this requirement are covered.
2. **"Repair Verb Refuses To Migrate A Flagged Ledger" (Slice 1b)** — the spec requires `repair` to run the SAME check against a concept's embedded history before migrating it and refuse **per concept** (writing nothing for that concept, migrating the rest). The actual implementation instead refuses the **entire run** whenever ANY survivor bundle-wide carries ≥2 entries (design's own, different, coarser Decision-5 gate) — this is a materially different mechanism than the spec text describes (all-or-nothing vs. per-concept), and it does not invoke Check B (`scan_nesting_violations`) as a refusal input at all; it only uses the entry-count proxy.

Evidence: `grep`-confirmed zero occurrences of `--force` handling tied to ledger/merge-flag refusal anywhere in `main.py`; the only two hits for "doctor-flagged" in the whole tree are code comments (`main.py:540`, `test_merge.py:422`) that explicitly contrast the implemented torn-write refusal against this OTHER, unbuilt refusal — i.e., the implementers were aware the doctor-flagged refusal is a distinct, separate mechanism, and built neither it nor any override flag for it.

This is a genuine spec requirement (with 4 concrete scenarios) that is neither implemented nor covered by a passing test, and — unlike the five deviations the apply phase proactively disclosed — this one is absent from `apply-progress.md`'s Deviations sections entirely. It was not raised for adjudication. **This blocks archive** until either (a) implemented and tested, or (b) the spec is formally amended/descoped with maintainer sign-off and the gap is explicitly tracked as a follow-up rather than silently left unspecified-vs-implemented.

## Disclosed Deviations — Adjudication

| # | Deviation | Verdict |
|---|---|---|
| 1 | Check B's `[FAIL]` names both remedies unconditionally | **Not actually a deviation** — re-reading `doctor-command/spec.md`'s own text: "A `[FAIL]` line's remediation MUST name BOTH the repair verb... and `git reset --hard`..." — the spec literally requires naming both, unconditionally, every time. The implementation matches the spec text exactly. No action needed. |
| 2 | `scan_nesting_violations` skips entries with nothing embedded in `survivor_before` | **Acceptable, low risk.** Logically necessary (a post-relocation entry never carries a nested `merged_from` snapshot, so there is nothing to compare) and backed by a dedicated regression test. Design's prose implies but doesn't spell out this exact rule — a SUGGESTION-level documentation gap in design.md, not a behavior risk. |
| 3 | `doctor-command/spec.md` documents only Check B, not Check A, though both are implemented per tasks.md/design.md | **Real spec drift; does not block archive, but must be filed as a follow-up.** Check A is fully implemented and tested (task 2.1, `test_doctor.py`), so behavior is correct and complete — the gap is purely in the spec ARTIFACT under-describing shipped behavior. This is lower severity than the undisclosed CRITICAL gap above because it is (a) actually disclosed, (b) the implementation is a superset of the spec rather than a shortfall, and (c) `design.md` (a later, more authoritative artifact per project convention) does fully specify Check A. Recommend a targeted spec.md correction PR, not a re-open of this change. |
| 4 | `has_reset_point` checks `HEAD~1` resolvability, not that history reaches the actual first corrupting merge | **Acceptable, documented as necessary-not-sufficient in the function's own docstring.** No spec text promises exact-commit precision; doctor's own remediation text already treats `<first-merge>~1` as a human-filled placeholder. |
| 5 | Doctor CLI wiring / repair command body written together with tests rather than strict RED-first | **Acceptable under Strict TDD verify rules.** Disclosed, not glossed over; safety-critical gates (reset-point gate, bundle-wide-entries gate) were subsequently mutation-tested and confirmed to catch a reverted guard — the assurance substitutes for pure ordering discipline on these two files only. Every other task in PR#3 followed literal RED-first. |

## Non-Goals Held

- `bundle/.state/pending/` — confirmed absent from the working tree (`find` returned nothing); nothing references a "pending work store" path outside the ledger's own `.pending` two-phase-write marker (a distinctly different, in-scope concept). #572 not started, not painted into a corner.
- `unmerge --to` — confirmed `unmerge()`'s signature has only `survivor_id`, `absorbed_id`, `--auto`; no `--to` parameter exists anywhere in `cli/main.py`. #562 not started.

## Assertion Quality Audit (Strict TDD)

Spot-checked the priority-surface test files (`test_ledger.py`, `test_repair.py`, `test_doctor.py`, `test_contradiction.py`, `test_merge_roundtrip.py`, `test_ledger_walk_exclusion.py`, `test_merge.py`): no tautologies, no assertion-free production-code calls, no ghost loops over possibly-empty collections found in the sampled tests. The two tests most load-bearing for the priority checklist (per-entry call-count test, byte-parity key-set test) both assert on values that a wrong implementation would visibly change (count, key sets) — genuinely discriminating, not smoke tests.

## Coverage / Quality (already independently confirmed during review, re-stated for completeness)

Full suite 4208 passed / 1 skipped; ruff clean; mypy clean (192 files); coverage 97.05% against a 90% branch gate; `git status` clean.

## Summary of Issues

**CRITICAL (1)**
- `entity-resolution-merge/spec.md`'s two ADDED requirements (merge's doctor-flagged `--force` refusal; repair's per-concept flagged-ledger refusal) are entirely unimplemented, untested, and undisclosed. Blocks archive.

**WARNING (2)**
- `privacy-purge/spec.md` Scenario 2 (cross-survivor sidecar content rewrite during purge) remains unimplemented — disclosed, independently justified as currently unreachable, but the spec scenario is formally unsatisfied. File as tracked follow-up.
- `doctor-command/spec.md` under-documents Check A (torn-write check exists and is tested, but the spec delta only describes Check B). File a spec-correction follow-up; does not reflect a behavior gap.

**SUGGESTION (1)**
- `scan_nesting_violations`'s "skip entries with nothing embedded" rule should be added explicitly to `design.md`'s prose (currently only implied), to avoid re-litigating the same investigation on a future read.

## Verdict

**FAIL.** All eight priority requirements flagged during review as most important resolve to PASS or an already-adjudicated PARTIAL/documented-gap, and the five disclosed deviations are reasonable engineering tradeoffs (one is not even a real deviation). Command evidence, task completion, and non-goal boundaries all hold. The blocker is a NEW finding: `entity-resolution-merge/spec.md` contains two ADDED requirements with four concrete scenarios that have zero implementation and zero test coverage, and this gap was never surfaced by the apply phase's own disclosure process. Recommend routing back to implementation (or an explicit, maintainer-approved spec descope) before archive.
