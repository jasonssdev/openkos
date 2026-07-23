# Design: Directory-Walk Observability Hardening (S3 follow-up)

## Technical Approach

Two independent, additive layers over the shipped S3 fail-closed filter, mirroring
existing precedent. **Layer 1 (signal):** one shared CLI-layer helper runs
`okf._walk_errors` (metadata-only re-walk) and WARNs to STDERR when the bundle walk
was incomplete — copying `state/reindex.py:285` + `cli/main.py:3728-3734`. Wired into
all 5 sensitivity-filter verbs, suppressed under `--include-confidential`, exit 0.
**Layer 2 (leak closure):** port query's send-time re-read
(`answer.py:211-214`, `sensitivity.blocks_llm_send(metadata.get("sensitivity"))`)
into the 4 still-leaking load paths so a doc loaded by direct path is re-checked
against its own freshly re-read frontmatter before entering any prompt.
`sensitivity.py` stays a pure no-I/O leaf (spec: `sensitivity-aware-llm`).

## Architecture Decisions

### Decision: Helper home + signature

| Option | Tradeoff | Decision |
|--------|----------|----------|
| Inline per verb (like reindex) | Duplicated across 5 sites, drift risk | Rejected |
| Add to pure `sensitivity.py` | Violates no-I/O leaf invariant | Rejected (LOCKED) |
| New `cli/observability.py` module | One import, single message, testable in isolation | **Chosen** |

Signature: `warn_if_walk_incomplete(bundle_dir: Path, *, mode: str = "warn",
include_confidential: bool = False) -> None`. Body: return early if
`include_confidential`; if `bool(okf._walk_errors(bundle_dir))` and `mode == "warn"`
emit the STDERR line; `mode == "refuse"` raises `NotImplementedError` (cloud-egress
seam — signature is stable, cloud slice only fills the branch + flips call sites, no
re-threading). Message (self-explaining, reindex style):
`"openkos: bundle scan was incomplete -- a directory-scan error made part of the
bundle unreadable, so the confidential-content filter could not inspect every
document and some confidential material may not have been excluded. Fix the
directory permissions and re-run, or pass --include-confidential to bypass the
filter deliberately."`

### Decision: Double-walk cost — accept

Helper adds a second `os.walk` pass on top of each verb's existing
`sensitive_concept_ids` walk. Metadata-only, no file reads; identical to the
`reindex.py:285` precedent that already pairs `_walk_errors` with its own walk.
**Accepted for now.** Do NOT thread an error list out of the pure predicate this
slice — that changes `sensitivity.py`'s signature for a negligible saving.

### Decision: Leak re-check inside the load functions

Mirror query exactly: the guard lives where frontmatter is parsed (metadata already
in hand), threading `include_confidential` into the load functions. Contradiction/
edge_typing `_load_doc` degrade a blocked doc to `(concept_id, "")` (existing
degrade-to-empty contract); adjudication `_load_members` skips it (`continue`, its
existing contract). Volatility re-reads per surviving doc after the blocked filter.

## Data Flow

    graph.db id ──→ _load_doc(direct path, x-bit only) ──→ re-read frontmatter
                                                                   │
                          blocked-set (walk may have missed it) ── + blocks_llm_send
                                                                   │
                                              confidential? ──→ degrade/skip ✗ prompt

A doc absent from the precomputed `blocked` set but `confidential`-on-disk is now
re-read at load and excluded before `_build_messages`, independent of whether the
`sensitive_concept_ids` walk ever saw its subtree. `--include-confidential` bypasses
both layers consistently.

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `cli/observability.py` | Create | `warn_if_walk_incomplete` (mode param) |
| `cli/main.py` | Modify | Call helper after `llm = OllamaClient(...)`, before run: adjudicate ~2848, suggest-relations ~2971, suggest-volatility ~3086, contradictions ~3217, query before the index-cm block ~3488 |
| `resolution/contradiction.py` | Modify | `_load_doc` (+`include_confidential`) re-check after `load_frontmatter` (~216); thread from `find_contradictions` (~397-398) |
| `resolution/edge_typing.py` | Modify | `_load_doc` re-check (~142); thread from `suggest_edge_types` (~211-212) |
| `resolution/adjudication.py` | Modify | `_load_members` re-check → `continue` (~106); thread from `adjudicate_candidates` (~223) |
| `resolution/volatility_typing.py` | Modify | Per-doc frontmatter re-read guard after blocked filter (~187) |
| `sensitivity.py` | Unchanged | Stays pure, no I/O |

Query needs the SIGNAL wiring only; its leak is already closed (`answer.py:211-214`) — no change to `answer.py`.

**Note — volatility leak surface is narrower:** its ids come from a live
`lint.collect_docs` walk (not `graph.db`), so a lost-r-bit subtree is already absent.
The re-read guard ships for uniform defense-in-depth per LOCKED scope and future-proofs
a possible index-sourced refactor; it is the weakest-leverage of the 4.

## Testing Strategy (Strict TDD — `uv run pytest`)

| RED test | Where | Approach |
|----------|-------|----------|
| Helper warns / silent / skip / refuse-raises | `cli/test_observability.py` (new) | Unit: monkeypatch `os.walk` onerror (`test_okf.py:405-434`); assert message, empty→silent, `include_confidential`→silent, `mode="refuse"`→`NotImplementedError` |
| 5 verbs WARN to STDERR, exit 0 | `cli/test_{query,contradictions,adjudicate,suggest_relations,suggest_volatility}.py` | `CliRunner(mix_stderr=False)`; monkeypatch walk onerror; assert stderr substring + exit 0 (`test_reindex_cmd.py:86-125`) |
| Clean bundle → no warning | same | Negative: no walk error → empty stderr |
| `--include-confidential` suppresses | same | Assert stderr empty |
| 4 verbs exclude confidential-on-disk doc missed by walk | `resolution/test_{contradiction,adjudication,edge_typing,volatility_typing}.py` | Monkeypatch `sensitive_concept_ids`→`frozenset()`; place confidential doc; capture `llm.chat` messages; assert body absent (mirror `test_answer.py:2161`); `include_confidential=True` includes it |

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file
classification, or process-integration boundary. In-process filter + STDERR warning
only. (Security-relevant data-exposure change → 4R resilience/risk review on apply.)

## Migration / Rollout

No migration. Fully additive; revert removes the helper wiring and 4 load-path
re-checks. `sensitivity.py`, the walk, and query's FIX-2 untouched.

## Slice / PR Structure

Estimated authored diff ~480-530 LOC (helper ~50, 5 signal wirings ~12, 4 leak
guards ~50, tests ~370). **Fits ONE PR under the 800-line SDD budget.** It DOES
exceed the 400-line reviewer guard (Section E) → tasks must forecast
`400-line budget risk: Medium`. Recommend single cohesive PR with `size:exception`
(one fail-closed invariant; splitting fragments the guarantee), with the fallback of
2 stacked slices — A: signal (5 verbs), B: leak-closure (4 verbs) — if the reviewer
budget is enforced.

## Open Questions

- None blocking. Single vs. stacked-PR delivery resolves at tasks/apply per delivery strategy.
