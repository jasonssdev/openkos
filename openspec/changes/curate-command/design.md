# Design: `openkos curate` — dependency-ordered decision session

## Technical Approach

A new CLI-layer module `src/openkos/cli/curate.py` owns an ordered tuple of **stage descriptors** and
one sequencer loop; `cli/main.py` gains only the Typer command (gate, context build, echo). This is
the `cli/next_action.py` shape (module owns the ordered engine, `main.py` stays thin), with one
deliberate inversion: `next` memoizes signals in `_BundleSignals`; `curate` **must not**. Every stage
derives its own queue when the loop reaches it, so a stage always reads state committed by the stage
before it (proposal D4). Memoization would be exactly the staleness bug D4 exists to exclude.

Writes go only through non-interactive cores. `merge` already has them (`prepare_merge` /
`merge_core`, `_prepare_one_merge` / `_commit_one_merge`); `relate` and `set-volatility` get the same
Phase-A/Phase-B split in slice 2. Every confirm, preview, gate and `_autocommit` stays outside the
cores — `curate` owns one uniform voice, exactly as `merge-core Extraction` (2026-07-24) decided.

## Architecture Decisions

### D1 — Module placement: `src/openkos/cli/curate.py`

| Option | Trade-off | Verdict |
|---|---|---|
| Inline in `cli/main.py` | File is ~8 700 lines; command bodies are thin by convention | Rejected |
| New top-level `openkos/curate.py` | Would import `resolution`, `bundle`, `state` and CLI helpers — inverts the canonical→derived edge `next_action`'s design already rejected | Rejected |
| `cli/curate.py` | CLI package is the composition root; precedent `cli/next_action.py`, `cli/observability.py` | **Chosen** |

The extracted `relate` / `set-volatility` cores stay in `cli/main.py` beside `prepare_merge` /
`merge_core`, for the reason that design gave verbatim: they need `config`/`fsio`/`bundle_log` and
CLI-local `_resolve_concept_path`; `bundle/*.py` is documented pure.

### D2 — One stage descriptor; all five entries declared at runtime in slice 1

**Choice**: a frozen `Stage` dataclass of `name`, `noun`, `probe`, `run`, `needs_llm`, `writes`,
`halts_run`, `live` (interfaces below). `probe` is cheap and LLM-free and returns the queue plus its
LLM-call count; `run` is the per-item apply loop. `writes` is a **capability** field, not a
convenience flag: it is what lets the non-TTY policy (D3) and slice 2's stages be decided by the
framework rather than restated per stage. `halts_run` is true for Preconditions only.

`_STAGES` carries **all five entries in slice 1**. The three not yet implemented carry `live=False`:
they are skipped without probing and without prompting, and appear in the five-entry end-of-run
summary as `not yet available in this version`. This is the honest form of the proposal's "two live,
three declared" — the tuple and every descriptor field are frozen in slice 1, so slice 2 only flips
`live` and fills `probe`/`run`.
**Rejected**: per-stage bespoke functions in sequence — decline/notice/summary logic then lives five
times and slice 2 reworks it. **Rejected**: a two-entry `_STAGES` proven only by test-only fakes —
the framework would then be exercised against five shapes nowhere the user can see, and the summary
would silently under-report what `curate` does not yet do. **Rejected**: live-but-empty placeholder
stages that prompt — dead prompts.

### D3 — Cost gate: generalize #134 into one helper, decline ≠ abort

`suggest-relations` (main.py:7434-7442) gates on `not auto` only — it is a *spend* gate, not a write
gate, so `cfg.review` is deliberately not consulted. `curate` keeps that: one `gate()` helper prints
`{n} {noun} -> {n} LLM call(s) ...` to stderr, then `typer.confirm`.

The literal is pinned: `{n} {noun} -> {n} LLM call(s)`, with Structure's noun `untyped edge`
(byte-compatible with `suggest-relations`' existing line).

| Condition | Standalone verb | `curate` |
|---|---|---|
| TTY, `--auto` | proceed | cost gate auto-accepted, **loop continues** |
| TTY, declined | `return` (exit 0) | outcome `declined`, **loop continues** |
| non-TTY, no `--auto` | `suggest-relations` prompts into EOF | every LLM-costing stage → `declined` + notice, **loop continues** |
| non-TTY, `--auto`, `writes=False` (Contradictions) | proceed | cost gate auto-accepted, stage runs and reports |
| non-TTY, `--auto`, `writes=True` | proceed | cost gate auto-accepted, **write walk declines** with a pointer to the standalone verb |
| empty queue | state message, exit 0 | outcome `empty`, no gate shown, loop continues |

Two rules make that table decidable from the descriptor alone:

1. **`--auto` consents to model spend, not to writes.** It accepts every cost gate; it NEVER
   auto-accepts a per-item `[y/N/skip]` write prompt. On a TTY those prompts behave exactly as
   `adjudicate --apply` does today (main.py:1203-1209) — `--auto` changes nothing about them.
2. Because of (1), a non-TTY `--auto` run has no way to obtain per-item write consent, so a
   `writes=True` stage declines its write walk and points at the verb built for unattended use —
   Identity → `openkos adjudicate --apply-same --confirm-count <n>`. A `writes=False` stage
   (Contradictions) has nothing to consent to and runs normally.

Non-TTY declining rather than exiting is the intentional divergence from the standalone verbs: D2
forbids one stage aborting the rest.
`observability.progress_callback(verb="curate", noun=stage.noun)` is passed to every library
`on_progress` seam; `stage_notice("curate", ...)` announces each stage. No new progress plumbing.

### D4 — Queue derivation per stage (re-derived, never cached)

| Stage | Read surface | `noun` → cost unit | `writes` | Writes via |
|---|---|---|---|---|
| Preconditions | `_open_proximity_or_degrade(layout.vectors_db_path)` → `None`; wording from `next_action._tier_missing_vector_index` (next_action.py:236-248) | — (0 calls) | False | — (halts) |
| Identity | `resolution.find_candidates` → `adjudicate_candidates` | `candidate group` → 1 call/group | True | `_prepare_one_merge` / `_commit_one_merge`; N>2 via `_echo_n_gt2_skip` |
| Structure | `build_graph(..., candidates=source)` + `candidate_edges` → `suggest_edge_types` | `untyped edge` → 1 call/edge | True | `prepare_relate` / `relate_core` |
| Metadata | `lint.collect_docs` + `cfg.type_tiers` → `suggest_volatility` | `concept type` → 1 call/type | True | `prepare_set_volatility` / `set_volatility_core`; the sensitivity gap is **reported only**, naming `openkos set-sensitivity` |
| Contradictions | `build_graph` + `find_contradictions` | `pair` → 1 call/pair | False | none (terminal report) |

Structure/Metadata/Contradictions therefore open their `GraphStore` and walk the bundle *after*
Identity's `_autocommit`s, so they see post-merge ids by construction, not by discipline.

### D5 — `relate` / `set-volatility` extraction seams

Behavior-preserving, mirroring `merge`'s split. Exact line seams in today's `main.py`:

| Verb | Phase A → `prepare_*` | Command keeps | Phase B → `*_core` |
|---|---|---|---|
| `relate` | 3715-3753 (`_snapshot_read` ×2, frontmatter parse, dedupe, `encode_relations`, `dump_frontmatter`, `insert_log_entry`) | 3686-3711 gate/resolve/validate, 3760-3775 preview, 3777-3786 confirm, 3790-3797 drift guard, 3808-3818 echo + `_autocommit` | 3800-3801 (`write_atomic` ×2) |
| `set-volatility` | 4769-4776 (`_snapshot_read`, `config.set_type_tier`) | 4726-4767 vocab/gate/idempotence, 4781-4782 preview, 4784-4793 confirm, 4800-4802 guard, 4810-4819 echo + `_autocommit` | 4805 (`write_atomic`) |

`PreparedRelate` / `PreparedSetVolatility` carry the snapshot bytes as drift baselines, exactly as
`PreparedMerge` does (main.py:4908-4911). Cores are non-interactive, emit no stdout, raise
`OSError`/`ValueError`; the commands keep the pinned `refusing` / `preparing` / `writing` wording.
**Proof of preservation**: `tests/unit/cli/test_relate.py` and `test_set_volatility.py` pass
**unedited** — that unchanged pass is the gate, as in `merge-core Extraction`.

### D6 — Drift guard: apply the existing guard, add no new surface (evidence-backed correction)

Verified in code: `_commit_one_merge` (main.py:1126-1149) calls `merge_core` + `_autocommit` and
**does not** call `_reject_drifted_targets` — the guard lives in the `merge` command (main.py:5399).
So "reuse `_commit_one_merge` verbatim" would inherit `adjudicate --apply`'s missing guard.

**Choice**: extract `merge`'s inline baseline mapping (5399-5410) into
`_merge_drift_targets(layout, prepared)` and have both `merge` and `curate`'s Identity stage call
`_reject_drifted_targets(layout, _merge_drift_targets(...), "curate")` after the per-item confirm and
before `_commit_one_merge`. `adjudicate` is untouched (out of scope).
**Rejected**: pushing the guard inside `_commit_one_merge` — it would silently change `adjudicate`'s
observable behavior, which the proposal forbids.
A drift refusal is **terminal for the run** (exit 3, #319), not a stage decline: drift proves the
workspace is racing, so later stages' plans would be computed from a state already disproved.

### D7 — LLM lifecycle and the Ollama ladder

One `OllamaClient(model=cfg.model)`, built **lazily** on the first LLM stage that passes its gate
(a run whose every gate is declined constructs no client), held for the run.

| Exception | Scope | Behavior |
|---|---|---|
| `OllamaUnavailable`, `OllamaModelNotFound` | environment | Stage → `unavailable` with the standalone verbs' actionable message. Run-scoped flag set: every later `needs_llm` stage is short-circuited with `skipped -- Ollama unavailable (see above)` and makes **no second connection attempt**. |
| `OllamaError` (generic) | this call | Stage → `failed`; later stages still try. |

Handler order stays specific-before-generic (both subclass `OllamaError` — main.py:7478-7485).
Preconditions is `needs_llm=False` and always runs; Identity/Structure/Metadata/Contradictions are
all LLM-backed (verified: main.py:7102, 7444, 7566, 7713). Retrying a dead daemon four times would
cost minutes and print the same remediation paragraph four times — hence the run-scoped short-circuit.

### D8 — Sensitivity and observability

`--include-confidential` / `--include-deprecated` live on `CurateContext` and are forwarded into each
library call (never filtered at the CLI layer), fail-closed by default.
`observability.warn_if_walk_incomplete` is called **once per run**, before stage 1, not per stage: it
describes the bundle walk, and five identical paragraphs in one session is noise. A run-level flag
makes the "at most once" property structural.

### D9 — Flag surface and exit codes

Flags: `--auto`, `--include-confidential`, `--include-deprecated`. **No `--json` in v1** — the
proposal lists it as a non-goal and `status`/`next` set the precedent; adding it later is additive.
No per-stage `--skip-<stage>` (proposal assumption 4).

| Exit | Cause |
|---|---|
| 0 | Normal, including declines, empty queues, `live=False` stages, pending decisions, and the Preconditions stop |
| 1 | Workspace gate / `config.read_config` failure (shared convention) |
| 2 | Typer-native usage error (unknown flag, bad value) — inherited, not authored |
| 3 | `_reject_drifted_targets` refusal (#319 contract, propagated unchanged) |

`curate` is not a CI gate: pending work never sets a non-zero exit.

### D10 — Slice boundary

| Slice | Lands | Framework touched by slice 2? |
|---|---|---|
| 1 | `cli/curate.py` (`Stage`, `StageProbe`, `StageOutcome`, `CurateContext`, `gate`, `run_curate`, summary), `_merge_drift_targets`, **all five `_STAGES` entries** (Preconditions + Identity live, three `live=False`), `curate` command, docs | No — slice 2 flips `live` and fills `probe`/`run` on three existing descriptors |
| 2 | `prepare_relate`/`relate_core`, `prepare_set_volatility`/`set_volatility_core`, the two commands refactored onto them, Structure + Metadata + Contradictions made live | — |

Slice 1 ships the ADR-0005/ADR-0011 ordering guarantee alone and reverts as one commit; nothing else
imports `cli/curate.py`.

## Interfaces

```python
@dataclass(frozen=True)
class StageProbe:
    items: tuple[object, ...] = ()
    llm_calls: int = 0
    unavailable: str | None = None    # queue not derivable; message is user-facing
    empty_message: str | None = None  # queue derivable but empty

@dataclass(frozen=True)
class StageOutcome:
    status: Literal[
        "applied", "declined", "empty", "unavailable", "failed", "not-live"
    ]
    applied: int = 0
    skipped: int = 0
    notice: str | None = None

@dataclass(frozen=True)
class Stage:
    name: str                                       # "Preconditions" ... "Contradictions"
    noun: str                                       # cost-line unit, e.g. "untyped edge"
    probe: Callable[[CurateContext], StageProbe]    # cheap, no LLM, no writes
    run: Callable[[CurateContext, StageProbe], StageOutcome]
    needs_llm: bool = True
    writes: bool = True                             # capability; drives the non-TTY policy (D3)
    unattended_hint: str | None = None              # standalone verb for a non-TTY --auto run
    halts_run: bool = False                         # Preconditions only
    live: bool = True                               # False → skipped unprobed, summarised only

_STAGES: tuple[Stage, ...]                          # D1 order, all five, spec-locked
def cost_line(stage: Stage, probe: StageProbe) -> str
def gate(stage: Stage, probe: StageProbe, ctx: CurateContext) -> bool
def run_curate(ctx: CurateContext) -> list[StageOutcome]
def render_summary(outcomes: Sequence[StageOutcome]) -> list[str]
```

## Data Flow

    curate → require_workspace → read_config → warn_if_walk_incomplete (once)
       │
       └─ for stage in _STAGES:                  ← no state carried between iterations
             not live? ──────→ "not yet available in this version"; continue (no probe)
             probe(ctx)            reads CURRENT disk state (post-previous-commits)
               ├ unavailable ──→ notice; halts_run? stop run : continue
               ├ empty ────────→ notice; continue
               └ items:
                    cost_line → gate(--auto | TTY confirm | non-TTY decline)
                       ├ declined ──→ notice; continue
                       └ accepted ──→ lazy OllamaClient → library call (on_progress)
                                        ├ writes and no TTY ─→ decline + unattended_hint
                                        └ per item: preview → [y/N/skip]   (never auto-accepted)
                                             → _reject_drifted_targets → *_core → _autocommit
       │
       └─ render_summary(outcomes)   (always 5 entries, even when nothing was eligible)

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/cli/curate.py` | Create | Stage types, `_STAGES`, gate, sequencer, summary (slice 1) |
| `src/openkos/cli/main.py` | Modify | `curate` command; `_merge_drift_targets` (slice 1); `relate`/`set-volatility` cores + refactor (slice 2) |
| `openspec/specs/curate-command/spec.md` | Create | Stage order, gate/decline, precondition stop, report-only contradictions |
| `tests/unit/cli/test_curate.py` | Create | Sequencer matrix + CLI wiring |
| `docs/cli.md` | Modify | New verb entry |

## Testing Strategy

| Layer | What | Approach |
|---|---|---|
| Unit (engine) | Stage order, decline-continues, halt-stops, empty/unavailable/not-live notices, five-entry summary, LLM short-circuit, `writes`×TTY×`--auto` matrix | Fake `Stage` descriptors, no CliRunner, no bundle — the `test_next.py` engine-apart-from-shell pattern; `pytest.mark.parametrize` over (stage, answer) instead of one test per cell |
| Unit (declared stages) | A `live=False` stage is never probed, never prompts, and still appears in the summary | Sentinel `probe` raising `AssertionError` proves it is not called |
| Unit (consent boundary) | `--auto` never auto-accepts a per-item `[y/N/skip]`; non-TTY `--auto` declines a `writes=True` walk with its `unattended_hint` | Engine-level, then one CLI assertion on the Identity hint text |
| Unit (cores, slice 2) | `prepare_relate`/`relate_core`, `prepare_set_volatility`/`set_volatility_core` called directly | Assert identical bytes; Phase A writes nothing; cores make no git commit |
| CLI | Cost lines, gates, per-item confirms, exit codes, `--auto`, `--include-confidential` | `CliRunner` + `input="y\n"`/`"n\n"`, `monkeypatch` on `sys.stdin.isatty`/`sys.stderr.isatty` (the `test_adjudicate.py` pattern) |
| LLM fake | Verdicts / suggestions / contradictions | `monkeypatch.setattr` on the **public** `openkos.cli.main.OllamaClient`; a sentinel raising in `__init__` proves no client is built when every gate is declined |
| Post-merge freshness | Structure sees post-merge ids | Seed all five finding kinds; merge in Identity; assert Structure's queue references the survivor |
| Regression (the gate) | `test_relate.py`, `test_set_volatility.py`, `test_merge.py`, `test_adjudicate.py`, `test_next.py`, `test_status.py` | Run **unedited** |

RED-first order (`rules.apply.tdd: true`): 1. workspace gate. 2. stage order over an all-findings
bundle. 3. decline-continues + non-TTY decline. 4. cost line before any LLM call. 5. precondition
stop. 6. drift refusal exits 3. → GREEN module + verb → REFACTOR → docs.

## Threat Matrix

| Row | Status | Behavior / RED test |
|---|---|---|
| Shell / subprocess | N/A (inherited) | Only `_autocommit`'s scoped `git add -- <paths>` argv list, unchanged |
| VCS automation | N/A (inherited) | Per-item `_autocommit` identical to running the verb by hand (D5 of the proposal) |
| Untrusted content in printed output | Applicable | Concept ids, rationales and `_echo_n_gt2_skip`'s pairwise commands render verbatim, as the advisors already do. RED test: a concept id carrying shell metacharacters renders verbatim and still exits 0 |
| Routing / executable classification / process integration | N/A | None present |
| TOCTOU on write targets | Applicable | D6: guard runs after every confirm, before every write; RED test mutates a target during the prompt and asserts exit 3, nothing written |

## ADR Gate

**No ADR.** (1) Does this decide a technology, pattern, interface or trade-off? **Yes** — the stage
descriptor and the cores' shape. (2) Hard to reverse? **No** — one new module, one verb and two
behavior-preserving extractions; no dependency, on-disk format, protocol or new public data contract.
Condition 2 fails, so the gate closes. Stage order is spec-level WHAT whose WHY is ADR-0005/ADR-0011.

## Migration / Rollout

No migration. Additive; slice 1 and slice 2 revert independently. Bundle changes already made are
ordinary per-verb commits.

## Open Questions

- [x] Preconditions halting the whole run is **settled**: it stands as designed (proposal D1,
      matching `next`'s tier-1 "blocks every later judgment" rank), and the spec is corrected to
      match. Identity not strictly needing `vectors.db` does not reopen it.
- [ ] Metadata's exact cost unit (per type vs one call) is pinned when slice 2 reads
      `suggest_volatility`'s loop; `StageProbe.llm_calls` already carries whichever it is.
