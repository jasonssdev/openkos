# Proposal: `openkos curate` — one consolidated, dependency-ordered decision queue

**Issue**: [#266](https://github.com/jasonssdev/openkos/issues/266) (P1).
**Baseline**: `main` @ `18830e2`. **Mode**: hybrid.

## Intent

A bundle accumulates five independent kinds of pending human judgment — duplicate identities,
untyped edges, unset volatility tiers, sensitivity gaps, and contradictions. Today each has its
own advisor verb (`duplicates`/`adjudicate`, `suggest-relations`, `suggest-volatility`,
`contradictions`), each read-only, each pointing at a different write verb (`merge`, `relate`,
`set-volatility`). The operator must know all five verbs, and — critically — must know the
**order** they have to run in. `openkos next` (#265) names only the single highest-priority
command and stops at "duplicates exist"; it never walks a queue.

The ordering is not a preference, it is a correctness constraint. ADR-0005/ADR-0011: `merge`
rewires outbound and inbound typed edges and retargets provenance, so any structural or metadata
decision taken before identity is settled is a decision taken against ids that a later merge will
rewire. An operator who runs `suggest-relations` before `adjudicate` does avoidable rework and can
type edges onto a concept that is about to disappear.

`curate` makes that order the product, not tribal knowledge: one session that walks the pending
decisions in the only order that is safe.

## Decisions settled here

| # | Decision | Rationale |
|---|---|---|
| D1 | **Stage order is a product invariant**, spec-locked and testable: Preconditions → Identity → Structure → Metadata → Contradictions. | Preconditions gate signal availability (`vectors.db`); Identity must precede Structure and Metadata per ADR-0005/ADR-0011; Contradictions is report-only and therefore last — it informs human judgment, it does not feed a write verb. |
| D2 | **Every stage states its cost and is individually declinable; declining one stage MUST NOT abort later stages.** | Generalizes the #134 cost gate (`suggest-relations` prints `N untyped edges → N LLM calls`, confirms unless `--auto`). New control flow: no existing verb has "gate, maybe skip, continue anyway". Local-first means the operator, not the tool, decides which model spend to pay. |
| D3 | **Reuse write cores; never re-implement a walk.** `merge`'s `_prepare_one_merge`/`_commit_one_merge` are consumed verbatim. `relate` and `set-volatility` get a **small surgical Phase-A/Phase-B core extraction** (mirroring `bundle/merge.py`) as part of this change. | Their write bodies live inline inside Typer command functions. Calling those callables directly would drag standalone-invocation banners and `typer.Exit`-on-refusal into a queue that needs to skip and continue; copying them would recreate exactly the duplication #191 removed. Extraction is the only option that satisfies "must not become another copy of any walk". |
| D4 | **Resumability by construction — no new state files.** Each run re-derives its queue fresh from current bundle state; per-verb `_autocommit` is the checkpoint boundary. | Every write verb is already atomic per item (Phase A snapshot → `_reject_drifted_targets` → `write_atomic` → `_autocommit`). An interrupt therefore leaves the workspace at the last committed item, and the next run simply re-queries. Caching a queue across stages would let an earlier merge make a later decision stale — the one failure mode a checkpoint file would introduce. |
| D5 | **`curate` writes only through existing verbs' semantics.** No new bundle mutation, no new file format, no new log-entry kind. | It is an orchestrator, not a new writer. Anything `curate` does must be reproducible by running the underlying verbs by hand. |

## Scope

### In scope

- New `openkos curate` verb: an interactive, dependency-ordered session over the five stages of D1.
- **Preconditions stage**: reuse the `_open_proximity_or_degrade` / missing-`vectors.db` seam and
  `next`'s tier-1 wording; a missing vector index stops the run pointing at `openkos reindex`.
- **Identity stage**: `find_candidates` → `adjudicate_candidates` → per-pair
  `_prepare_one_merge`/`_commit_one_merge`; N>2 groups reported via the existing `_echo_n_gt2_skip`
  pairwise-command output (#191), never auto-merged.
- **Structure stage**: candidate edges → `suggest_edge_types` → `relate` core.
- **Metadata stage**: `suggest_volatility` → `set-volatility` core (and the sensitivity gap surfaced
  by the same pass).
- **Contradictions stage**: `find_contradictions`, report-only, terminal, no write hint.
- **Surgical core extraction** of `relate` and `set-volatility` write bodies into pure Phase-A/Phase-B
  functions; the existing verbs are refactored onto them with byte-identical output.
- Per-stage cost gate + decline, per D2; `--auto` to accept every gate; `--include-confidential` and
  `--include-deprecated` forwarded to every stage's underlying call (fail-closed by default).
- Reuse of `observability.progress_callback` / `stage_notice` (#190) — no new progress plumbing.
- NO_COLOR / non-TTY discipline: non-TTY without `--auto` declines a gate rather than hanging.
- New capability spec, `tests/unit/cli/test_curate.py`, `docs/cli.md` entry.

### Out of scope (non-goals)

- **Any change to the five advisor verbs' own CLI behavior or specs.** `duplicates`, `adjudicate`,
  `suggest-relations`, `suggest-volatility`, `contradictions` keep their output byte-for-byte.
  `relate`/`set-volatility` are refactored internally only; their observable behavior is unchanged.
- **Any change to `openkos next` or `openkos status`.** `next` remains the one-command pointer.
- **A new checkpoint/state file, resume token, or `--resume` flag** (D4).
- **Auto-merging N>2 duplicate groups**, or any write that the underlying verb would not perform.
- **`--json` / structured output** — follows the `status` / `next` precedent.
- **A non-zero exit on pending decisions.** `curate` is not a CI gate.
- **Extracting a core for `set-sensitivity`** (provenance-descendant propagation plus
  `--allow-downgrade` is materially more complex); the Metadata stage reports sensitivity gaps and
  points at the existing verb.

## Capabilities

### New capabilities

- `curate-command`: the `openkos curate` verb — spec-locked stage order, per-stage cost gate and
  decline semantics, skip-does-not-abort, fresh-queue resumability, precondition stop, report-only
  contradictions, confidential/deprecated fail-closed threading.

### Modified capabilities

- None. D3's extraction is a behavior-preserving refactor; `typed-relationships` and
  `volatility-config` requirements are unchanged.

## Approach

An ordered tuple of stage descriptors, each exposing: a cheap availability probe, a cost statement
(item count → LLM-call count), a gate, and a per-item apply loop over a pure write core. `curate`
iterates the tuple; a declined or unavailable stage records a notice and the loop continues. The
stage sequencer is a module-local pure unit, testable independently of the CLI shell — the shape
`cli/next_action.py` established and `test_next.py` proves testable.

State is never carried across stages: each stage re-derives its inputs from disk after the previous
stage's commits, which is what makes both correctness (post-merge ids) and resumability fall out for
free.

## Delivery

Forecast **>800 changed lines** for a minimal-but-complete single PR (5 stages + gates + a
`test_curate.py` sized like `test_adjudicate.py`'s 2252 lines). The 2-slice split is **load-bearing,
not optional** — each slice is its own PR to `main`:

| Slice | Content | Standalone value |
|---|---|---|
| 1 | `curate` skeleton, stage framework, cost-gate/decline machinery, Preconditions + Identity stages (merge cores reused verbatim) | Ships the ADR-0005/ADR-0011 ordering guarantee the issue is actually worried about: identity settled before anything types or tags it |
| 2 | Structure + Metadata + Contradictions stages, plus the `relate` / `set-volatility` core extractions | Completes the queue on the skeleton slice 1 froze |

Strict TDD applies (`rules.apply.tdd`): stage order, decline-continues, and cost statements land as
RED tests before the verb exists.

No ADR is proposed. The stage order is spec-level WHAT and already derives its WHY from ADR-0005 and
ADR-0011; `sdd-design` re-evaluates against the ADR gate.

## Affected areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/cli/main.py` | Modified | New `curate` command; `relate` / `set-volatility` refactored onto extracted cores (slice 2) |
| new stage-sequencer module | New | Ordered stage descriptors + gate/decline engine, CLI-free |
| `src/openkos/cli/next_action.py` | Reused/precedent | Precondition wording; not restructured |
| `src/openkos/cli/observability.py` | Reused | `progress_callback`, `stage_notice` |
| `src/openkos/bundle/merge.py`, `_prepare_one_merge`/`_commit_one_merge` | Reused as-is | Identity stage |
| `openspec/specs/curate-command/spec.md` | New | Stage-order and gate contract |
| `tests/unit/cli/test_curate.py` | New | Per-stage × per-decline matrix |
| `docs/cli.md` | Modified | New verb entry |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Extraction of `relate` / `set-volatility` regresses their standalone behavior | Med | Behavior-preserving refactor pinned by the *existing* test suites, which must pass unchanged; extraction happens in slice 2 only, isolated from the skeleton |
| A second, subtly different copy of a walk lands inside `curate` | Med | D3 forbids it explicitly; review checks that no confirm/drift-guard/autocommit sequence exists in `curate` outside a shared core |
| Decline/skip/interrupt matrix across 5 stages under-tested | High | The matrix is the test plan, not an afterthought; `sdd-tasks` sizes it per stage |
| Cached queue replays a decision made stale by an earlier merge | Low (structurally excluded by D4) | Fresh re-derivation per stage, asserted by a test that merges in stage 2 and checks stage 3 sees post-merge ids |
| Stitched-together per-stage banners read inconsistently | Med | `curate` owns one uniform preview/confirm voice over the pure cores rather than inheriting five standalone-verb banner styles |
| Slice 1 ships a stage framework that slice 2 has to rework | Med | Slice 1 must implement the framework against all five stage shapes (two live, three declared), not just the two it fills in |
| Exceeds the 400-line review budget | High (accepted) | 2-slice chain above; `sdd-tasks` may split further |

## Rollback plan

`curate` is additive. Slice 1 reverts as a single commit: the verb disappears, no bundle file is
touched by the revert, and every existing verb, spec, and test is byte-identical — nothing else
imports it. Slice 2 reverts independently, restoring the inline `relate` / `set-volatility` bodies
along with the stages that consumed them. Any bundle change `curate` already made is a normal
per-item `_autocommit`, revertible exactly as if the operator had run `merge` / `relate` /
`set-volatility` by hand — which is precisely D5's point.

## Dependencies

- No new runtime dependencies.
- Shipped and merged: `_prepare_one_merge`/`_commit_one_merge` and `_echo_n_gt2_skip` (#191),
  `on_progress` callbacks (#190), `_open_proximity_or_degrade` (#183), `next`'s tier engine (#265).
- Slice 2 depends on slice 1's stage framework.

## Success criteria

- [ ] `openkos curate` walks pending decisions in the D1 order, asserted over a bundle seeded with
      findings in all five categories at once.
- [ ] Each stage prints its cost before any model call, and declining it exits that stage cleanly
      while later stages still run.
- [ ] A missing/empty `vectors.db` stops the run pointing at `openkos reindex`.
- [ ] Interrupting mid-run and re-invoking resumes from current bundle state with no state file on
      disk, and never replays a committed decision.
- [ ] The Structure and Metadata stages operate on post-merge ids.
- [ ] N>2 duplicate groups are never auto-merged; exact pairwise `openkos merge` commands are printed.
- [ ] Contradictions are report-only and always last.
- [ ] Confidential content is excluded unless `--include-confidential` is passed.
- [ ] `duplicates`, `adjudicate`, `suggest-relations`, `suggest-volatility`, `contradictions`,
      `relate`, `set-volatility`, `next`, `status` produce unchanged output; their tests pass unedited.
- [ ] Quality gate green: `uv run pytest --cov`, ruff check + format, mypy strict.

## Proposal question round

No interactive round was run in this execution. Assumptions open to correction:

1. Contradictions stay report-only and terminal — `curate` never proposes a write from a contradiction
   (D1), even though one often implies a `merge` or `forget`.
2. Sensitivity is reported, not written, inside the Metadata stage: no `set-sensitivity` core
   extraction in this change (scope boundary).
3. Declining a stage is per-run and never remembered — the same stage re-offers on the next run.
4. `--auto` accepts every stage's cost gate globally; there is no per-stage `--skip-<stage>` flag in
   the first slice.
5. Slice 1 alone (Preconditions + Identity) is a shippable, user-valuable release on its own.
