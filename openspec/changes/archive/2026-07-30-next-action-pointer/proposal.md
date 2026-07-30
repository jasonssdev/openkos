# Proposal: `openkos next` — deterministic pointer to the one action worth taking (#265)

## Intent

`openkos status` answers "what is in this bundle and what is imperfect about it". It answers it
completely: five independent `bundle/**/*.md` walks (`main.py:5220-5232`, verified against the
body at `main.py:5252-5353`) producing up to seven distinct needs-attention kinds plus one
informational edge-count line. A user who just wants to know **which single command to run
next** must read that whole report and rank the findings themselves, every time.

`next` is the missing verb: one ranked answer, one runnable command, no ranking left to the
reader.

### Corrected premise (the issue overstates the gap)

The issue claims "four of the six finding kinds name no command at all". That is **false**, and
the scope below reflects the verified reality, not the issue text:

| Kind | Names a real command? | Evidence |
|---|---|---|
| missing-vector-index | Yes — `openkos reindex` | `main.py:5332-5333` |
| unextracted | Yes — `openkos ingest <resource>` | `lint.py:630` (generic fallback `lint.py:632`) |
| below-source-sensitivity | Yes — `openkos backfill-sensitivity` | `lint.py:729-730` |
| duplicate-groups | Yes — `openkos duplicates` | `main.py:5323` |
| multi-source-uncovered | No — explicitly disclaims the one plausible command | `lint.py:766-768` |
| dangling | No | `lint.py` `check_dangling_targets` |
| §9 conformance | No | `okf.survey_bundle` findings |

Accurate split: **2 of 7 name nothing** (conformance, dangling), plus 1 that names a command
only to rule it out (`multi-source-uncovered`) — so 3 at most, never 4 of 6. Consequence for
scope: `unextracted` and `below-source-sensitivity` are **not new mapping work**. `next` reads a
command string the finding already carries. The remaining work is ranking and short-circuiting.

## Settled decisions

The four questions left open by exploration are decided here, not deferred.

### D1 — Priority order

Ordering principle, stated once and testable: **what blocks other work, then what is missing,
then what is unsafe, then what is merely ambiguous.**

| Rank | Kind | Command | Why here |
|---|---|---|---|
| 1 | missing-vector-index | `openkos reindex` | Precondition. Absent/empty `vectors.db` starves dense retrieval and every embedding-derived signal. Also the cheapest possible check — one `vector_meta` row count (`state/vectorstore.py:297`), no walk. |
| 2 | unextracted | `openkos ingest <resource>` | Completeness. A `failed` Source contributed **no concept at all**: the concept set is not merely untidy, it is incomplete. Ranking anything below this means reasoning over a set that is about to grow — and a completed extraction can itself create new duplicates and new below-Source descendants, i.e. work redone. |
| 3 | below-source-sensitivity | `openkos backfill-sensitivity` | Safety. A descendant under-classified relative to its Source is a confidentiality gap, not a tidiness one (ADR-0003/0009). Its remedy is one no-argument, raise-only, monotone sweep, and raise-only means a later `merge` can never invalidate it (ADR-0011 retargets provenance; it never lowers a level). |
| 4 | duplicate-groups | `openkos duplicates` | Identity. Ranked last of the actionable tiers because it is the only one requiring human judgment before any write, and because it is the most expensive check (two walks). Still ranked **above structural work** (typed relations), per ADR-0005: `merge` rewires outbound and inbound typed edges, so structural effort spent before identity is settled is effort spent against ids a merge will rewire. |

**Extraction above identity** is the contested call. Decided for extraction: a failed extraction
is an absence of data; a duplicate group is an ambiguity in data that is present. Absence
outranks ambiguity, because the ambiguity cannot be judged correctly over an incomplete set.
Cost order corroborates it (tier 2 shares one walk; tier 4 costs two) but does not drive it.

### D2 — Implementation shape

**Approach 1 accepted**: `next` is an independent command reusing the existing pure functions
(`okf.survey_bundle`, `lint_check.collect_docs` + its checks, `find_exact_title_groups`,
`vector_store_is_empty`). `status`'s body is **not** refactored and its spec is **not** touched.

Accepted cost, stated plainly: the priority order lives in exactly one place — `next` — and
`status` does not consume it. The two verbs can therefore frame the same bundle differently:
`status` renders every finding in its own fixed, spec-locked order; `next` renders one, ranked.

That is acceptable because they answer different questions, and because unifying them would
force a delta against eight shipped requirements in `openspec/specs/status/spec.md` for zero
user-visible gain. A follow-up issue should be filed to revisit a shared ordered module **only
if a third consumer of the ranking appears**; until then unification is speculative.

### D3 — `find_exact_title_groups` gating

`next` MUST call `find_exact_title_groups` only after tiers 1-3 have all produced no finding.
This diverges deliberately from `status`, which calls it unconditionally (`main.py:5319`,
never gated on `vectors_missing` — see its docstring at `main.py:5226-5229`). The divergence is
permitted because `openspec/specs/status/spec.md` constrains `status` alone; `next` gets its own
spec. Since the two-walk check ranks last, gating it costs nothing and is where most of the
short-circuit saving comes from.

### D4 — Commandless findings

`conformance`, `dangling`, and `multi-source-uncovered` are **never** "the one action" in this
change. `next` ranks only kinds that carry a real runnable command.

Rationale: `next`'s entire contract is *one command to run*. A finding with no command cannot
satisfy it, and printing "edit this file by hand" is a different product — a review queue, which
`status` and `lint` already are. Nothing is lost: those findings stay one command away.

**Honesty guard (mandatory).** Because `next` short-circuits, it cannot know whether commandless
findings exist without paying for the walks it just skipped. It therefore MUST NOT ever assert
that the bundle is clean. When no ranked tier fires, its output names `openkos status` as the
place remaining findings live. Same line whether the bundle is pristine or full of manual work —
it costs nothing and never lies.

For the same reason `next` MUST NOT print a "N other items pending" count. The issue's sample
output shows one; a count requires every walk, which the issue's own cost requirement forbids.
The two cannot both hold, and the cost requirement wins. Pointer, no number.

## Scope

### In Scope

- New `openkos next` verb: read-only, human-readable, exits 0 (workspace gate exits 1, matching
  `main.py:5253-5256`).
- Ordered, short-circuiting evaluation over the four ranked tiers in D1, returning on first hit.
- Output shape: the runnable command, a one-line reason, and a pointer to `openkos status`.
- Empty/no-runnable-action output per D4's honesty guard.
- New capability spec; `test_next.py` reusing the shared `seed_vectors_db` fixture
  (`tests/unit/cli/conftest.py:25`); `docs/cli.md` entry.

### Out of Scope (non-goals)

- **Any model call, ever.** `next` MUST NOT construct an `OllamaClient` or any `LLMBackend`. Its
  answer is a pure function of files on disk.
- **`suggest-relations`, `contradictions`, `suggest-volatility`.** All three gate on a live
  backend (`main.py:6053`, `6371`, `6257`). The `candidate_edges` proxy
  (`resolution/edge_typing.py:296-311`) is deliberately **not** used: it needs `vectors.db` and a
  `build_graph` walk, and "N candidate edges exist" is not an action — the action would be an
  inference run whose value `next` cannot deterministically assess. Recommending it would be
  exactly the false completeness the issue forbids.
- **Any change to `status`** — its body, its output, its ordering, or its spec.
- **The informational edge-count line.** No needs-attention finding derives from it; `next` never
  calls `build_graph`.
- **`--json` or any structured output.** Follows the `status` precedent.
- **Non-zero exit on findings.** `next` is not a CI gate.

## Capabilities

### New Capabilities

- `next-action-pointer`: the `openkos next` verb — the ranked tier order as a testable contract,
  first-hit short-circuit including the cost guarantee, per-tier command derivation, the
  no-runnable-action output, read-only/exit-0 behavior.

### Modified Capabilities

- None. D2 keeps `status` untouched at both implementation and spec level.

## Approach

A module-local ordered list of check steps, each yielding an optional `(command, reason)`, which
`next` iterates and returns on first hit. New control flow — no existing helper does partial or
early-exit computation — but every underlying signal comes from the existing pure functions, so
no walk logic is duplicated.

Cost guarantee, as a hard requirement rather than an aspiration:

| Stops at tier | Walks paid |
|---|---|
| 1 (reindex) | 0 (one `vector_meta` row count) |
| 2 (ingest) | 1 (`collect_docs`) |
| 3 (backfill-sensitivity) | 1 (same `collect_docs` list — no second call) |
| 4 (duplicates) | 3 |
| No action | 3 |

Worst case 3 of `status`'s 5; best case 0. Tiers 2 and 3 MUST share one `collect_docs()` call,
matching the no-extra-walk convention already enforced on `status` (`main.py:5293-5299`).

## Delivery

One reviewable slice, forecast **~400-600 changed lines** (verb + ordered steps in
`cli/main.py`, spec, `test_next.py`, `docs/cli.md`) — fits the 800-line budget without chaining.
Strict TDD applies (`openspec/config.yaml: rules.apply.tdd`): the tier order and the cost
guarantee land as RED tests before the verb exists.

No ADR is proposed. The priority order is spec-level WHAT (a testable requirement), not an
irreversible architectural WHY, and it is reversible by editing one list. The design phase
re-evaluates this against the ADR gate.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/cli/main.py` | Modified | New `next` command + ordered short-circuiting steps |
| `openspec/specs/next-action-pointer/spec.md` | New | The ranked-order and cost contract |
| `tests/unit/cli/test_next.py` | New | Tier order, short-circuit cost, output shape |
| `docs/cli.md` | Modified | New verb entry |
| `openspec/specs/status/spec.md` | Untouched | Explicitly unchanged (D2) |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| `status` and `next` disagree in framing over the same bundle | High (accepted, D2) | Different questions by design; `next` always points back at `status`; follow-up issue only if a third consumer appears |
| The tier order proves wrong in practice | Med | It is one ordered list behind a spec'd contract; reordering is a spec delta plus a list edit, not a redesign |
| Short-circuiting silently regresses into paying every walk | Med | The cost table becomes explicit tests asserting call counts per tier, not a comment |
| A user reads "no runnable action" as "bundle is clean" | Med | D4's honesty guard: the line names `openkos status` unconditionally and never claims cleanliness |
| Commandless findings stay invisible to `next` forever | Low (accepted) | They remain fully visible in `status`/`lint`; a future tier can be appended without reordering |
| `next` slice runs long | Low | Spec and `docs/cli.md` can split into a trailing docs commit |

## Rollback Plan

Single-slice revert. `next` is additive and read-only: reverting removes one command, touches no
bundle file, and leaves `status`, its spec, and every existing test byte-identical — D2's
no-refactor decision is what makes the rollback this cheap.

## Dependencies

- No new runtime dependencies. Every signal comes from functions already shipped and tested.
- No blocking change; `backfill-sensitivity` (#231) and the exact-title entry point (#216, #186)
  are merged and are what make tiers 3 and 4 available deterministically.

## Success Criteria

- [ ] `openkos next` prints exactly one runnable command, or exactly one no-action line.
- [ ] The four tiers fire in the D1 order, pinned by tests over bundles seeded with several
      findings at once.
- [ ] Stopping at tier 1 performs zero bundle walks; stopping at tier 2 or 3 performs exactly
      one; the maximum is three — asserted, not documented.
- [ ] Tiers 2 and 3 share one `collect_docs()` call.
- [ ] No model backend is constructed on any path.
- [ ] The no-action output names `openkos status` and never claims the bundle is clean.
- [ ] No count of unseen findings is ever printed.
- [ ] `next` exits 0 on every workspace state; exits 1 only outside a workspace.
- [ ] `status`'s output, body, spec, and tests are unchanged.
