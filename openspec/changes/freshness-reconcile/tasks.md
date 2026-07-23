# Tasks: freshness-reconcile (S4 — `reconcile` write verb)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~750-950 (main.py +260-300, tests/unit/cli/test_reconcile.py +500-650) |
| 400-line budget risk | High |
| Granted review budget (this slice) | 800 lines (orchestrator override) |
| 800-line budget risk | Medium (estimate straddles the line) |
| Chained PRs recommended | No |
| Suggested split | Single PR (verb + its tests are one coherent safety unit) |
| Delivery strategy | auto-forecast |
| Chain strategy | size-exception |

Decision needed before apply: Yes
Chained PRs recommended: No
Chain strategy: size-exception
400-line budget risk: High

Rationale: reconcile clones relate/merge's Phase-A/confirm-gate/Phase-B template with no LLM leaf, so it is smaller than an S3 slice, but it writes to TWO concept files (vs relate's one) with a symmetric/`--winner` branch, anchor-gated note append, and two log-line variants — pushing it past 400 but plausibly under the granted 800. Splitting logic from its own safety tests would break the load-bearing RED-test-before-write contract, so `size:exception` is preferred over a chain if it lands over 800.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `reconcile` verb (Phase-A/B + tests) | PR 1 (single, size:exception if >800) | `uv run pytest tests/unit/cli/test_reconcile.py` | `openkos reconcile <a> <b> [--winner] [--auto]` over `CliRunner` temp workspace | Revert `reconcile` command block in `main.py` + delete `test_reconcile.py`; no other verb touched |

## Phase 1: RED — Failing Safety Tests First (`tests/unit/cli/test_reconcile.py`)

- [x] 1.1 Snapshot helper + `_init_workspace`/`_ingest_source`/`_simulate_tty` fixtures, mirroring `test_relate.py`.
- [x] 1.2 Error-before-write: unknown id (either side), self-pair (a==a), `--winner` not in `{a,b}` — assert exit 1, snapshot unchanged (no file touched).
- [x] 1.3 Confirm-gate: interactive decline aborts no-write; `--auto` bypasses; config `review: false` bypasses; non-TTY without `--auto` refuses.
- [x] 1.4 Symmetric success: two outbound `reconciled_with` edges (A→B, B→A), `## Reconciliation` note + anchor on both bodies, `**Reconcile**` log line.
- [x] 1.5 `--winner` success: single outbound `supersedes` edge W→L only, notes on both, log line.
- [x] 1.6 Idempotent re-run: symmetric and winner variants — no duplicate edge/note (anchor-suppressed), "no change" log line.
- [x] 1.7 Additive-only: pre-existing unrelated body content and relations on both concepts preserved verbatim; only new edge + note appended.

## Phase 2: GREEN — Implement `reconcile` (`src/openkos/cli/main.py`)

- [x] 2.1 Add `reconcile` command signature (`id_a`, `id_b`, `--winner`, `--auto`) + docstring mirroring `relate`/`merge` density.
- [x] 2.2 Phase-A: `require_workspace` gate, `_resolve_concept_path` both ids, self-pair guard, `--winner` ∈ `{id_a, id_b}` validation (loser = other member).
- [x] 2.3 Compute edges via `okf.decode_relations`/`Relation`/`encode_relations` with `(target,type)` dedup: symmetric = two appends; winner = one `supersedes` append on winner only.
- [x] 2.4 Add anchor-detection helper: `<!-- okos:reconcile target=<id> role=... -->`; build `## Reconciliation` note per side, skip append if counterpart anchor already present.
- [x] 2.5 Build `**Reconcile**` log line variants (symmetric-new, winner-new, no-change) via `bundle_log.insert_log_entry`.
- [x] 2.6 Preview render + confirm-gate, reusing `relate`'s exact precedence (`--auto` | `cfg.review` False | TTY confirm | non-TTY refuse).
- [x] 2.7 Phase-B atomic writes in order: doc_a → doc_b → log.md via `fsio.write_atomic`; catch `(OSError, ValueError)` on both phases, exit 1 with stderr message (no traceback).
- [x] 2.8 Confirm command registers on `app`; run `uv run pytest tests/unit/cli/test_reconcile.py` to GREEN.

## Phase 3: REFACTOR

- [x] 3.1 De-duplicate per-side note/anchor construction into one private helper if the symmetric/winner branches repeat logic.
- [x] 3.2 Docstring/comment pass for parity with `relate`/`merge` (Phase A/B narrative, threat-matrix N/A note).

## Phase 4: Verify Gate

- [x] 4.1 `uv run pytest` (full suite green — 1476 passed).
- [x] 4.2 `uv run ruff format .` then `uv run ruff format --check .` (109 files formatted/clean).
- [x] 4.3 `uv run ruff check .` (all checks passed).
- [x] 4.4 `uv run mypy .` (no issues found in 109 source files).

## Apply Result

All 21 tasks complete. Actual changed lines: `main.py` +333/-0, `test_reconcile.py` +429 (new file) — 762 total, within the granted 800-line budget.

## Correction: CRITICAL mode-switch data-integrity fix (post-4R-review)

A 4R review (risk+resilience+reliability converged) found `reconcile` produced
self-contradictory state on a mode-switch re-run of the same pair (e.g.
symmetric then `--winner`, or opposite `--winner`s): a new, DIFFERENT-typed
edge was added alongside the stale one (`_add_relation_if_absent` dedups only
on `(target, type)`), while the anchor-gated note (`_reconcile_anchor_present`,
matches on `target` alone, ignores `role`) was never updated — frontmatter and
note went permanently out of sync.

- [x] C.1 RED: added 3 mode-switch tests to `test_reconcile.py`
      (`test_symmetric_then_winner_mode_switch_refuses`,
      `test_winner_a_then_winner_b_mode_switch_refuses`,
      `test_winner_then_symmetric_mode_switch_refuses`) — confirmed FAILING
      (2nd call exited 0, silently wrote contradictory state) before the fix.
- [x] C.2 GREEN: added `_existing_reconciliation_state` +
      `_reconciliation_state_description` helpers to `main.py`; `reconcile`
      now classifies the pair's existing reconciliation state (none /
      symmetric / directional+winner) BEFORE computing any new edge, and
      REFUSES (`ValueError`, exit 1, zero writes) when the existing state
      differs from the requested one — a pair can carry at most one
      reconciliation resolution written by `reconcile`.
- [x] C.3 Also fixed the readability WARNING: `_reconcile_sentence`'s `role`
      is now `Literal["reconciled","supersedes","superseded"]` and raises
      `ValueError` on any unexpected value instead of silently falling
      through to the "superseded" sentence.
- [x] C.4 Closed the coverage-gap WARNING: added `test_traversal_id_b_refuses`,
      `test_traversal_winner_refuses`, `test_reserved_basename_id_a_refuses`,
      `test_reserved_basename_id_b_refuses` (all passed unchanged —
      `_resolve_concept_path` already handled these correctly; only test
      coverage was missing).
- [x] C.5 Verify gate: `uv run pytest` — 1483 passed (was 1476; +7 net new
      tests); `uv run ruff format .` (1 file reformatted, whitespace-only);
      `uv run ruff check .` — all checks passed; `uv run ruff format --check .`
      — 109 files already formatted; `uv run mypy .` — success, no issues in
      109 source files.
