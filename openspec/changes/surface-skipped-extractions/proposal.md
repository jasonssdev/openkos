# Proposal: Record and Surface Sources Whose Extraction Was Skipped (#187)

## Intent

`ingest` degrades to a Source-only write through four distinct paths in `_stage_derived_objects`
(`cli/main.py:1298,1305,1316,1329`). Each prints one transient stderr line and then leaves no
durable trace. A Source with zero derived objects is therefore indistinguishable, minutes later,
from a Source the model legitimately found nothing in. Silent, unrecoverable extraction debt
accumulates and no command can list it. Retrying is already possible but undiscoverable.

## Scope

### In Scope

- A closed-vocabulary `extraction_status` frontmatter key on the Source, written on and only on
  the four zero-derived-object paths.
- A `lint` finding + `status` `needs_attention` line for the one retryable reason, naming the
  exact retry command.
- Delta specs for `ingestion`, `lint`, `status`; `docs/cli.md` update in Slice 2.

### Out of Scope

- **No `reextract` verb.** A byte-identical re-ingest (`main.py:1643-1654`, `regenerate=True`)
  already skips the raw copy (`main.py:1735-1745,1552`) and unconditionally re-attempts
  extraction, with per-slug reconciliation (`main.py:1253-1258`) preventing duplicates. So
  `openkos ingest raw/<name>` already *is* the retry. Adding a verb would mean new command
  surface, tests, docs, and duplicated Phase A/B logic for zero new capability, against
  `rules.tasks` ("no empty scaffolding"). Rejected, but a candidate for a separate issue if
  translating a Source id to its `raw/` path proves to be real friction in use.
- Per-candidate drop reasons (empty slug, slug collision, `build_concept` failure) — a different
  state, already reported individually.
- Splitting `OllamaUnavailable` / `OllamaModelNotFound` / bare `OllamaError` into distinct
  reasons — today's code does not distinguish them either.
- Making `lint` or `status` gate (exit non-zero).

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `ingestion`: `ingest` MUST record, on the Source's frontmatter, why it produced zero derived
  objects; a later successful ingest MUST clear that record.
- `lint`: new finding kind for Sources whose extraction failed, rendered in its own section,
  still non-gating.
- `status`: the same finding folds into `needs_attention`, reusing the existing
  `lint_check.collect_docs()` call.

## Approach

### A. Key and value vocabulary

Key: **`extraction_status`** on the Source document, written **only when the run produced zero
derived objects**; **absent** otherwise. No `none`/`ok` sentinel: absence then means exactly one
thing ("this Source has derived objects"), the healthy path stays byte-identical to today (zero
diff churn on the common case), and OKF §9 (`okf.py:1040-1078`, parseable frontmatter + non-empty
`type`) is unaffected either way — this is ordinary §4.1-tolerated data, exactly like `relations:`
and `merged_from:`.

Closed vocabulary, keyed on **why**, never on today's gate condition (so #240 changes the second
row's frequency, not the schema):

| Path | Value | Debt? |
|---|---|---|
| `main.py:1298` empty/undecodable content | `no-extractable-text` | No — permanent for these bytes |
| `main.py:1305` confidential floor blocks the send | `blocked-by-sensitivity` | No — deliberate policy |
| `main.py:1316` `OllamaError` | `failed` | **Yes — the only retryable value** |
| `main.py:1329` successful call returned `[]` | `no-concepts-found` | No |

All four write a value. Recording only the debt case would make absence conflate "succeeded",
"empty file", and "policy-blocked", leaving `lint` unable to explain any concept-less Source.
Readers MUST ignore an unrecognized value (forward-compatible, fail-silent).

### B. Clearing

No clearing code. The Source is rebuilt from scratch by `okf.build_source_concept`
(`main.py:1717`) on every ingest and re-ingest, then written with `fsio.write_atomic`
(`main.py:1865`). The key is therefore recomputed per run and a successful re-ingest simply omits
it. **Named constraint**: the value MUST be stamped on the freshly built content, never merged
onto the on-disk frontmatter — a merge would make a stale marker sticky forever. This requires
`_stage_derived_objects` to return its skip reason alongside the plans (it is called at
`main.py:1735`, after the build), which the design phase must shape.
**Required test**: mock `OllamaError` → key is `failed`; re-ingest the same bytes with a working
LLM → key absent and derived objects present.

### C. `lint` finding

New `LintFinding.kind` = **`unextracted`** (joining `stale` / `orphan` / `dangling`,
`lint.py:73-74`). Emitted **only** for `extraction_status: failed`; the three non-debt values are
never findings.

Section `Unextracted sources:`, empty state `No unextracted sources.`, detail text:

```
concept extraction failed during ingest — retry with `openkos ingest <resource>`
```

`<resource>` is the doc's own `resource:` value, which ingest sets to `f"raw/{name}"`
(`main.py:1674`) — i.e. literally the argument the retry needs. `LintDoc` therefore gains both
`extraction_status` and `resource`. If `resource` is missing or empty, fall back to a generic
"re-run `openkos ingest` on this source's raw file".

**Exit code: 0.** `lint`'s spec has an explicit *Non-Gating Exit Contract* — every successful read
exits 0 whether the bundle is clean or has findings; `lint` is not a CI gate in MVP-1
(`main.py:5229-5233`). All three existing kinds are treated this way; a fourth kind must not
silently turn `lint` into a gate.

### D. `status` integration

**Named constraint: no fifth bundle walk.** `status` already calls
`lint_check.collect_docs(layout.bundle_dir)` at `main.py:5010` and folds dangling findings into
`needs_attention` at `main.py:5012-5013`. The new check MUST consume that same in-memory `docs`
list — no second `collect_docs`, no new `rglob`. This deliberately avoids repeating the #216
compute-then-discard pattern and does not touch the four-walk consolidation deferred to #195.
Only `failed` reaches `needs_attention`, phrased with the same retry command; `status` stays
read-only and exits 0.

### E. The `OllamaError` message is NOT recorded

Confirmed on the exploration's first point: all three siblings collapse to the single `failed`
token, because today's handler does not distinguish them.
**Revised on the second point**: `str(exc)` is *not* written to frontmatter. Ollama error strings
are built from the base URL and model name (`llm/ollama.py`), so they can embed a host, a port, a
path, or a private model identifier — and Source frontmatter is canonical, git-tracked, and
shareable. The full message keeps going to stderr at ingest time, where it is transient and local.
The durable record stays a closed vocabulary token with no free text.

### F. Slice boundaries

| | Slice 1 (PR1) — record | Slice 2 (PR2) — surface |
|---|---|---|
| Code | `model/okf.py` (key + vocabulary constants, `build_source_concept` param), `cli/main.py` (`_stage_derived_objects` return shape, ingest stamping) | `lint.py` (`LintDoc.extraction_status`/`.resource`, `check_unextracted`, `LintReport.unextracted`), `cli/main.py` (`lint` section, `status` fold-in) |
| Tests | `tests/unit/model/test_okf.py`, `tests/unit/cli/test_ingest.py` — one per reason + the clearing test | `tests/unit/test_lint.py`, `tests/unit/cli/test_lint.py`, status tests |
| Docs/specs | `openspec/specs/ingestion/spec.md` delta | `openspec/specs/lint/spec.md` + `openspec/specs/status/spec.md` deltas, `docs/cli.md` |
| Estimate | ~220–280 changed lines | ~320–400 changed lines |

`docs/cli.md` waits for Slice 2: Slice 1 changes no command output or user-facing behavior, only
the on-disk record. Each slice stays inside the 400-line per-PR guard and well inside the 800-line
session budget. Each is independently useful and independently revertable, matching the issue's
own "either useful on its own" framing.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/model/okf.py` | Modified | `EXTRACTION_STATUS_KEY` + vocabulary constants; optional param on `build_source_concept` |
| `src/openkos/cli/main.py` | Modified | `_stage_derived_objects` return shape; ingest stamping; `lint` section; `status` fold-in |
| `src/openkos/lint.py` | Modified | Two new `LintDoc` fields, `check_unextracted`, `LintReport.unextracted` |
| `docs/cli.md` | Modified | New `lint` section + retry guidance (Slice 2) |
| `openspec/specs/{ingestion,lint,status}/spec.md` | Modified | Delta specs |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Marker never clears; a fixed Source reports forever | Med | Rebuild-not-merge constraint (B) + a dedicated clearing test |
| `blocked-by-sensitivity` wrongly treated as debt | Med | Only `failed` produces a finding; asserted by test |
| Extra frontmatter key breaks a reader | Low | §4.1 tolerance; §9 checks only parseability + `type`; precedent in `relations`/`merged_from` |
| `_stage_derived_objects` return-shape change ripples through callers/tests | Med | Single call site (`main.py:1735`); design phase pins the shape |
| #240 lands mid-work | Low | Schema keys on *why*, not on the gate condition — no migration |
| 90% branch-coverage gate | Med | One test per new branch, per `rules.verify` |

## Rollback Plan

- **Slice 2**: pure `git revert`. It is read-only surfacing with no on-disk state.
- **Slice 1**: `git revert` leaves already-written `extraction_status` keys in user bundles. They
  are inert extra frontmatter — tolerated by OKF §4.1, ignored by every reader, invisible to §9
  conformance — and disappear on the next re-ingest of that Source. **No migration required.**
- **Ordering constraint**: revert Slice 2 before Slice 1. Reverting Slice 1 alone would leave
  `lint`/`status` reading a key nothing writes (harmless but permanently silent).

## Dependencies

- None. `raw/` immutability, reconstructibility, and sensitivity fail-closed behavior are all
  preserved (AGENTS.md non-negotiables).

## Success Criteria

- [ ] Each of the four degrade paths writes its own `extraction_status` value.
- [ ] A Source with derived objects has no `extraction_status` key.
- [ ] A successful re-ingest clears a previously written value (tested).
- [ ] `blocked-by-sensitivity` never produces a `lint`/`status` finding (tested).
- [ ] `lint` shows an `Unextracted sources:` section naming `openkos ingest <resource>`, exit 0.
- [ ] `status` lists it under `needs_attention` with no additional bundle walk.
- [ ] `uv run pytest`, `ruff`, `mypy` clean; branch coverage stays ≥ 90%.

## Proposal question round

Session `execution_mode` is `auto`, so these were resolved by written rationale rather than asked.
Flag any of them for correction before `sdd-spec`:

1. All four zero-object paths write a value (chosen so absence is unambiguous) — acceptable
   frontmatter noise, or record only `failed`?
2. The raw `OllamaError` text is deliberately kept out of git-tracked frontmatter on
   host/path/model-leak grounds — accepted, or is the diagnostic detail worth more?
3. `lint` stays non-gating for the new kind, matching its Non-Gating Exit Contract — confirmed?
