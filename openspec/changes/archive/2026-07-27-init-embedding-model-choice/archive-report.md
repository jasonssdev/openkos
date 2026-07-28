# Archive Report: init-embedding-model-choice

**Date Archived**: 2026-07-27
**Status**: ARCHIVED
**GitHub Issue**: #189 (Closed)

| Slice | PR | Merged as | Scope |
|---|---|---|---|
| PR1 | [#205](https://github.com/jasonssdev/openkos/pull/205) | `444e513` | `config.py`: `EMBEDDING_MODEL_ALLOWLIST`, `validate_embedding_model`, `_PLACEHOLDER_RE` single-pass substitution, template placeholder, `write_config(embedding_model=)` |
| PR2 | [#206](https://github.com/jasonssdev/openkos/pull/206) | `427957e` | `llm/ollama.py` + `state/reindex.py`: `OllamaEmbeddingDimensionMismatch` treated as permanent/fatal |
| PR3 | [#207](https://github.com/jasonssdev/openkos/pull/207) | `584e3e1` | `cli/main.py`: `--embedding-model`, `_resolve_embedding_model`, `_pick_embedding_model`, `_canonical_allowlist_spelling`, off-allowlist warning, sticky re-embed warning, `reindex` dimension-mismatch branch |
| PR4 | [#208](https://github.com/jasonssdev/openkos/pull/208) | `7d44b2e` | `tests/unit/cli/test_init.py`: closes the embedding-picker selection/reprompt/exhaustion coverage gap `sdd-verify` found |

## Summary

`init` previously picked the chat model interactively but silently hardcoded `bge-m3`
for embeddings; `embedding_model` was never written to `openkos.yaml`, and a
wrong-dimension model failed permanently while `reindex` reported it as transient
("will retry next run") forever. This change:

1. Adds a vetted, code-level 1024-dim `EMBEDDING_MODEL_ALLOWLIST` and
   `validate_embedding_model` (YAML-safety only, independent of allowlist membership).
2. Adds `--embedding-model`, an interactive picker over installed-∩-allowlisted
   candidates, and a silent-default fallback, mirroring the existing chat-model
   resolver's precedence shape (flag > picker > default).
3. Writes `embedding_model:` into `openkos.yaml` via a second, independent
   plain-text placeholder token.
4. Prints an unconditional sticky re-embed warning on every successful `init`,
   worded about future cost only.
5. Adds `OllamaEmbeddingDimensionMismatch`, a permanent `OllamaError` subclass
   raised directly from the wrong-length branch, exempted from `embed()`'s retry
   loop, and treated as fatal (not a per-doc `embed_failed` skip) in `reindex`'s
   per-doc loop and CLI error ladder — closing the "will retry next run" lie for a
   failure mode that cannot heal by retrying.

## ADR Gate

**Verdict: NO new ADR** — confirmed correct, not merely asserted. The proposal
flagged an ADR as "likely required" pending `sdd-design`'s judgment. `design.md`'s
two-gate check: (1) decides a technology/pattern/trade-off — yes (curated allowlist
over runtime probing; a new exception type); (2) hard-to-reverse — **no**. The
allowlist gates only the picker, never `read_config`; reverting it removes a menu,
not a capability. Reverting `write_config(embedding_model=)` leaves already-written
workspaces with an explicit key `read_config` already honors — no workspace breaks,
no migration. `OllamaEmbeddingDimensionMismatch` is purely additive — every existing
`except OllamaError` site keeps compiling and catching it, so removing the subclass
re-widens rather than breaks anything. The genuinely hard-to-reverse decisions here —
`EMBED_DIM = 1024` and "reliability is a prior hard filter over the allowlist" — are
already owned by **ADR-0006**; this change is that ADR's *implementation*, not a new
decision. Next free ADR number remains 0008 for a future change.

## Design Correction: D4 Superseded During Implementation

`design.md`'s Decision D4 stated that `_pick_embedding_model` should run its own
`list_models()` probe, structurally cloned from `_pick_chat_model`, and explicitly
named "hoist one shared probe into `init`" as the **rejected** alternative.

The shipped code does the opposite, and correctly so — this was a deliberate,
spec-driven override, not a silent drift. `tasks.md` 4.11/4.12 ("reuse the chat
picker's existing probe call — no second reachability request") and
`specs/workspace-init/spec.md`'s "Graceful Degradation Of The Embedding Picker"
requirement ("This probe MUST reuse the chat picker's existing probe call — it MUST
NOT issue a second, separate reachability request") both directly contradict D4's
own-probe rationale. Those are the reviewed acceptance criteria and they govern.

What shipped: one shared `_probe_installed_models()`, hoisted into `init` and called
at most once per run (skipped when both `--model`/`--embedding-model` flags are given,
or on non-TTY), threaded into both `_resolve_embedding_model`/`_pick_embedding_model`
and a refactored `_resolve_model`/`_pick_chat_model` (which now accepts
`installed: list[InstalledModel]` instead of probing internally — the exact signature
change D4's rationale predicted and warned against).

`design.md` D4 now carries a correcting note added at archive time recording this
supersession, why, and what shipped instead — the original D4 text is preserved
unmodified with the correction appended, rather than rewritten as though D4 always
said the right thing. Independently confirmed twice: `apply-progress.md` (Deviation 1,
flagged during PR3) and `verify-report.md` ("Known contradiction: `design.md` D4 vs.
shipped code" — classified as a deliberate, spec-driven deviation with zero functional
defect).

## Defects Caught By Review And Fixed Before Landing

Two real defects were caught by review during this change's development and fixed
before landing on `main` — neither shipped:

1. **`write_config`'s two sequential `str.replace` calls were order-dependent.**
   With two independent placeholders substituted via sequential `str.replace` calls
   rather than a single-pass substitution, `--model __OPENKOS_EMBEDDING_MODEL__`
   (an adversarial-but-YAML-safe flag value) would silently corrupt the write: the
   first replace pass would substitute the *embedding* placeholder text into the
   *model* line, and the second pass would then also match and overwrite it, ending
   with `model: bge-m3` on disk instead of the value the user passed. Worse, the
   first regression test written against this bug **passed** — its
   `_expected_config_bytes` test helper mirrored the same two-pass substitution
   order, so the test asserted the buggy output against itself: bug compared against
   bug, not against correct behavior. The fix landed as `_PLACEHOLDER_RE`, a
   single-pass substitution that resolves both placeholders in one regex pass with
   an independent per-placeholder occurrence-count guard, eliminating the
   order-dependency class entirely; the test helper was corrected alongside it.

2. **The `--embedding-model` flag path used exact allowlist string equality while
   the interactive picker used `model_tag_matches` normalization.** This meant
   `--embedding-model bge-m3:latest` (the exact form Ollama's `/api/tags` reports)
   was flagged as off-allowlist and written raw to `openkos.yaml`, diverging from
   `DEFAULT_EMBEDDING_MODEL` (`bge-m3`) for what is semantically a no-op selection —
   which would have silently forced a full corpus re-embed via the model-tag gate on
   the next `reindex`, for a user who selected the recommended default via its
   `:latest`-qualified server tag. Fixed via `_canonical_allowlist_spelling`, which
   applies the same `ollama.model_tag_matches` normalization on the flag path that
   the picker already used (design decision D3), so both paths write the allowlist
   spelling consistently.

## Verification

`sdd-verify` ran against the state after PR1+PR2+PR3 merged (`verify-report.md`,
Engram #2038). Verdict at that time: **FAIL (schema-level, evidence-completeness)**
— `36/37` scenarios compliant, one CRITICAL: the `workspace-init` spec's "Selecting a
number picks that embedding model" scenario had no covering test, because the shipped
`EMBEDDING_MODEL_ALLOWLIST` contained exactly one entry (`bge-m3`) and no test — and
no reachable production input — could present the picker with a second real candidate
to select by number. The report was explicit that this was zero functional defects in
shipped code, purely a test-completeness gap, and recommended closing it before
archive rather than treating it as a revert-worthy blocker.

**PR4 (`#208`, `7d44b2e`) closed that gap**, adding coverage (via a monkeypatched
two-entry allowlist, matching `verify-report.md`'s own recommended remediation) for:
the numbered-selection path, the invalid-input reprompt branch, and the
`_MAX_PICKER_ATTEMPTS`-exhaustion fallback (the latter two were the associated
WARNING-level uncovered lines `cli/main.py:365-372` in the same report). This is the
final state and supersedes the CRITICAL finding recorded in `verify-report.md`.

**Final gate on `main` after all four PRs**: 2333 passed, coverage 97.61% against a
90% branch gate, ruff/format/mypy clean. 38/38 Phase 1–4 implementation tasks
complete (`tasks.md`, Engram #2030); Phase 5 (5.1 full-gate reproduction, 5.2 rollback
confirmation) is satisfied by this report and the follow-up issues filed below.

**Exceptional checkbox reconciliation**: `tasks.md`'s Phase 5 items (5.1–5.3) were
left unchecked by `sdd-apply`, by design — `tasks.md` itself names Phase 5 as the
orchestrator's responsibility, not part of the 38 implementation tasks. At archive
time, this report reconciles them to checked in the archived `tasks.md` because the
orchestrator's launch prompt supplied the concrete final-state evidence proving each
is done: 5.1 (full gate) is reproduced above (2333 passed, 97.61% coverage,
ruff/format/mypy clean); 5.2 (rollback boundaries) is confirmed independent and clean
per the ADR-gate section above; 5.3 (follow-up filing) is satisfied by issues #209 and
#210, both cited by number below. This is a mechanical reconciliation against explicit
final-state proof, not a claim that unverified work is done, and it is limited to
Phase 5 — no Phase 1–4 implementation-task checkbox was touched.

## Open Follow-Ups (Not This Change's Scope)

Filed as GitHub issues, not carried as unclosed work in this change:

- **[#209](https://github.com/jasonssdev/openkos/issues/209)** — `retrieval/answer.py`'s
  `_vector_hits` still catches `OllamaEmbeddingDimensionMismatch` generically via
  `except (VecUnavailable, sqlite3.Error, OllamaError):` and degrades `query` to
  FTS-only silently on a permanent dimension mismatch, rather than surfacing it.
  Named as a deliberate deferral in `design.md`'s call-site audit table (flipping this
  to fatal is a `query`-capability change out of this proposal's scope) and confirmed
  still real by `verify-report.md`.
- **[#210](https://github.com/jasonssdev/openkos/issues/210)** — `config.py`'s
  `_PLACEHOLDER_RE` (built from the two placeholder constants) and `write_config`'s
  `substitutions` dict (built from the same two constants) are two separate
  hand-maintained structures. They cannot drift today, but a future third placeholder
  added to one and forgotten in the other fails asymmetrically: forgotten in
  `substitutions` raises `KeyError` (loud, safe); forgotten in the regex tuple leaves
  a literal `__OPENKOS_*__` token unsubstituted with no exception (silent, unsafe).

## Known Gap: No Manual TTY Verification Against A Real Ollama Server

No PR in this change included a manual `openkos init` run on a real TTY against a
live Ollama server with `bge-m3` installed. `tasks.md`'s own suggested runtime
harness for PR3 named exactly this scenario; `apply-progress.md` disclosed the gap
explicitly at the time, and `verify-report.md` confirmed it accurate as a SUGGESTION-
level finding (not blocking, given the extensive faked-client coverage). All picker
coverage across all four PRs is `typer.testing.CliRunner` plus a faked `OllamaClient`
(`_fake_ollama_client`/`_CountingFakeOllamaClient`) — never a real Ollama process.
This is recorded here as an honest residual gap, not resolved by this archive; a
manual smoke test before wide feature announcement remains advisable.

## Specs Merged

Delta specs from `openspec/changes/init-embedding-model-choice/specs/` (Engram #2028)
merged into the living specs:

| Domain | Requirement | Change |
|---|---|---|
| `workspace-init` | Static openkos.yaml Template | MODIFIED — now names BOTH `model:` and `embedding_model:` as the only user-selectable fields (previously stated `model:` was "the single user-selectable field") |
| `workspace-init` | Vetted 1024-Dim Embedding Model Allowlist | ADDED |
| `workspace-init` | Interactive Embedding Model Picker Over The Vetted Allowlist | ADDED |
| `workspace-init` | Graceful Degradation Of The Embedding Picker | ADDED |
| `workspace-init` | Off-Allowlist Embedding Model Flag Is Warned, Not Blocked | ADDED |
| `workspace-init` | Sticky Re-Embed Warning On Every Successful Init | ADDED |
| `llm-client` | OllamaClient Embeds Text Via /api/embed | MODIFIED — wrong-length rows now raise `OllamaEmbeddingDimensionMismatch`, distinct from generic malformed/non-numeric `OllamaError` |
| `llm-client` | Dimension Mismatch Is A Distinct Permanent Error | ADDED |
| `reindex-command` | Per-Doc Embed Failure Is Isolated, Not Fatal | MODIFIED — `OllamaEmbeddingDimensionMismatch` added to the fatal (non-`embed_failed`) tuple, checked ahead of the generic `OllamaError` catch |
| `reindex-command` | Reindex Surfaces An Actionable Re-Run Notice On Embed-Failure Skips | MODIFIED — fatal-exit exclusion list extended to include dimension mismatch; "will retry next run" wording reserved for the transient case |

All `(Previously: ...)` provenance lines were dropped during merge — they existed to
make the delta reviewable, not to accumulate history in the living contract. Verified
by grep-equivalent read-back: the phrase "single user-selectable field" no longer
appears anywhere in `openspec/specs/workspace-init/spec.md`.

**Known pre-existing note not touched by this merge**: `llm-client`'s own "Embedding
Model Defaults Independently From The Chat Model" requirement (untouched by this
change's delta) still states "`embedding_model` is a code-level default only — it is
NOT added to `openkos.yaml.template` and has no per-workspace override or CLI flag;
that remains a separate future slice." That statement is now stale given this change,
but it was not part of this change's delta spec (no MODIFIED section touched it), so
it was left as-is per the skill's "preserve requirements not mentioned in the delta"
rule rather than edited speculatively. Flagged here for a future change to correct.

## Deliverables

Archived to `openspec/changes/archive/2026-07-27-init-embedding-model-choice/`:

- `proposal.md` — Intent, decisions, scope, capabilities, affected areas
- `explore.md` — Exploration notes
- `design.md` — Decisions D1–D8 (D4 corrected at archive time), sequence diagram, exception taxonomy audit, testing strategy
- `tasks.md` — 38/38 Phase 1–4 implementation tasks complete across PR1–PR3, Phase 5 verification tasks
- `apply-progress.md` — Full RED/GREEN/TRIANGULATE evidence for all three PRs
- `verify-report.md` — `sdd-verify` findings after PR1+PR2+PR3 (superseded by PR4 + this report for the CRITICAL finding)
- `archive-report.md` — this report
- `specs/workspace-init/spec.md`, `specs/llm-client/spec.md`, `specs/reindex-command/spec.md` — delta specs, now merged into `openspec/specs/`

## Filesystem Move Note

This executor's available tools are limited to file read/write and Glob — no
move, rename, or delete primitive. All change artifacts (`proposal.md`,
`explore.md`, `design.md` with the D4 correction, `tasks.md`, `apply-progress.md`
with its post-archive note, `verify-report.md` with its superseded-by note, this
`archive-report.md`, and the three delta spec files) were written in full to
`openspec/changes/archive/2026-07-27-init-embedding-model-choice/`, matching the
target archive layout. The source files under
`openspec/changes/init-embedding-model-choice/` could NOT be deleted by this
executor and remain on disk alongside the new archive copies. The orchestrator
(or a follow-up step with filesystem-delete access, e.g. `git rm -r
openspec/changes/init-embedding-model-choice/`) must remove the original
directory to complete the move and keep `openspec/changes/` free of this closed
change. Flagged as a risk in the return envelope.

## Engram Observation IDs (For Traceability)

- Proposal: #2027
- Delta Spec: #2028
- Design: #2029
- Tasks: #2030
- Verify Report: #2038

## SDD Cycle Complete

The change has been fully planned, explored, designed, implemented across four PRs
(three chained + one gap-closing), verified, and archived. All 38 Phase 1–4
implementation tasks are complete and confirmed against shipped code, not merely
checked off. Two real pre-landing defects were caught by review and fixed. One
CRITICAL verification finding (a test-coverage gap, not a functional defect) was
closed by PR4 before archive. The D4 design/spec conflict is documented, not
silently resolved. Two follow-ups (#209, #210) and one honest residual gap (no
live-Ollama manual smoke test) are carried forward explicitly rather than dropped.
Ready for the next change.
