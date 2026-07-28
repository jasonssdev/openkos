# Proposal: `status` surfaces pending duplicate groups

Issue [#186](https://github.com/jasonssdev/openkos/issues/186) (P1 bug), **signal 1 only**. Prior reading: `explore.md` in this folder.

## Intent

`openkos status` is the orientation command, yet a workspace with unresolved duplicate concepts prints `Nothing needs attention.` The data already exists — `find_candidates` (`resolution/candidates.py:127`) computes the groups deterministically — and `status` never calls it. Users find duplicates only by guessing to run `openkos duplicates`. After this change, a bundle with pending duplicate groups says so and names the next command.

## Scope narrowing versus #186 as filed

| Signal | Verdict |
|---|---|
| 1. Pending duplicate groups | **IN.** `find_candidates` is read-only, stdlib-only, and never raises on empty bundles. |
| 2. Sources with skipped extraction | **OUT.** No durable trace exists to read. Issue #187 would create it. |
| 3. Unmerged SAME clusters | **OUT.** `AdjudicatedCandidate` is explicitly ephemeral (`resolution/adjudication.py:82`). Issue #191 owns it. |

The issue as filed is broader than what ships. Signals 2 and 3 are unimplementable today, not deprioritized; they must not creep back in later.

## Approach

Add a **fourth** `needs_attention` source in `status`, alongside §9 conformance, dangling references, and missing `vectors.db`:

1. Call `find_candidates(layout.bundle_dir)` with the default `include_deprecated=False`. No new CLI flag.
2. Keep only the exact-title-match groups (`Tier.HIGH`). When any remain, append one line: their count via `_plural()`, naming `openkos duplicates` as the next step.
3. Insert after the dangling-reference block, before the `vectors_missing` check. Unconditional — `find_candidates` reaches only `difflib`-based similarity, never embeddings.
4. Update the `status` docstring's "THREE independent bundle walks" to four; #195 owns consolidating them.

**ACTIONABLE, not INFORMATIONAL.** `status` keeps entries out of `needs_attention` when no follow-up exists (the edge-count summary). A duplicates line names a concrete command, so it belongs inside.

## Decision — remediation command is `duplicates`

`adjudicate` builds `OllamaClient(model=cfg.model)` and can exit 1 with no reachable Ollama; pointing a lightweight read-only command at it would make orientation depend on a running model server. `merge` (`main.py:3483`) needs survivor/absorbed ids `status` cannot supply. `duplicates` is deterministic and dependency-free, and already ends with `Next: openkos merge …`. Chain: `status` → `duplicates` → `merge`.

## Decision — only exact-title groups count, and the line carries no tier labels

Two separable questions, settled together.

**Which groups break the all-clear: exact-title matches only.** `find_candidates`
returns two tiers, and the near-match tier is unsuitable for an alert.
`similarity.py:48-61` documents the reason as a deliberate design choice:

> ACCEPTED precision tradeoff, BY DESIGN: LOW tier is intentionally high-recall,
> not high-precision. … LOW-tier candidates are a read-only review queue (never
> auto-merged); precision here is deliberately deferred to LLM adjudication in a
> later slice.

The same docstring shows the false positive is unfixable in principle: `"cats"`
against `"carts and currency"` is structurally identical to the `"stoicism"` ⊂
`"stoic philosophy"` case the algorithm exists to catch, so no lexical rule
separates them. A high-recall queue is right for a verb the user opts into and
wrong for an alert the user cannot dismiss. Folding it into `needs_attention`
would leave a mature bundle permanently unable to print `Nothing needs
attention.`, which reproduces #186's failure inverted: an alert that never turns
off informs as little as an all-clear that is never true. Two same-type documents
sharing a normalized title, by contrast, are near-always a real duplicate — worth
interrupting for.

**How it is worded: plain description, never the tier label.** Issue #192 (open)
records that `HIGH`/`LOW` is misread as confidence when it encodes match *method*.
The line therefore describes the match in ordinary English — identical titles —
and never prints `HIGH`, `LOW`, `exact`, or `near`. `_format_group_tally(high,
low)` exists and is deliberately unused. The spec must state the absence of tier
labels as a requirement, not leave it to implementation taste.

**Accepted consequence.** `status`'s count will be lower than `duplicates`'s
whenever near-match groups exist. That is intended — the two commands answer
different questions — but the line must not be phrased as a total, or it will
read as a contradiction the moment the user runs `duplicates`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `status`: a fifth Needs-Attention requirement, mirroring the four at `openspec/specs/status/spec.md` :77 / :97 / :129 / :164 — a "no duplicates" scenario, a "surfaced" scenario, exact-title-matches-only, deprecated-excluded-by-default, and no tier labels.

## Out of Scope (non-goals)

- Signals 2 and 3 of #186.
- Any `--include-deprecated` flag on `status`.
- Fixing `adjudicate`'s stale docstring calling `merge` a "reserved slice 3" verb.
- Consolidating the bundle walks (#195).
- Any change to `find_candidates`, `duplicates`, or `merge` themselves.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/cli/main.py:4554-4593` | Modified | fourth `needs_attention` source |
| `src/openkos/cli/main.py:4499-4521` | Modified | docstring: three walks → four |
| `openspec/specs/status/spec.md` | Modified | fifth Needs-Attention requirement |
| `tests/unit/cli/test_status.py` | Modified | four new cases; fixtures from `test_duplicates.py` |

Rough size: ~15 production lines, ~120 test lines. Well inside the 400-line review budget; single PR.

## ADR gate

**No ADR.** The project gate is significant AND hard to reverse. This adds one read-only output line to an existing command: no persisted state, no file format, no config key, no new module, no public API. Reverting is deleting the block. Contrast the prior cycle's `embedding_model`, which wrote a key into every new workspace and still concluded no ADR. `sdd-design` may override with reasoning, but should not need to.

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Fourth whole-bundle walk slows `status` | Low | Same shape as the three precedented walks; #195 owns consolidation |
| Line ordering relative to `vectors_missing` is not test-enforced | Low | Spec fixes the requirement, not the position; design may move it |
| Wording drifts toward tier labels during apply | Med | Spec states "no tier labels" as a requirement |
| Existing status fixtures incidentally produce duplicates | Low | Checked during exploration: none do |
| `status`'s count reads as contradicting `duplicates`'s larger count | Med | Accepted by design; the spec must forbid phrasing the line as a total |
| Real near-match duplicates go unmentioned by `status` | Med | Accepted: `duplicates` remains the complete view, and the line names it |

## Rollback Plan

Single-commit revert of the `main.py` block plus its tests. No state, no migration, no config; `find_candidates` is untouched, so `duplicates` and `merge` are unaffected either way.

## Dependencies

None. `find_candidates` and `merge` both ship on `main`.

## Success Criteria

- [ ] A bundle with exact-title duplicate groups lists them under "needs attention" and does not print `Nothing needs attention.`
- [ ] The line counts exact-title-match groups only, with correct singular/plural wording, no `HIGH`/`LOW`/`exact`/`near` labels, and no phrasing that presents the count as a total.
- [ ] The line names `openkos duplicates` as the next step.
- [ ] A bundle whose only candidate groups are near-matches shows no such line and still prints `Nothing needs attention.`
- [ ] A bundle with no duplicate groups shows no such line.
- [ ] A duplicate group whose members are all deprecated is excluded by default.
- [ ] `status` still exits 0 in every case and writes nothing.
