# Exploration: durable-pending-work (issue #572)

Investigated 2026-08-12, by the orchestrator, against the code — not from the
issue text. Two of the issue's own framing claims did not survive that check.

## The issue's claim, and what the code says

#572 collects eight symptoms and names one cause: the engine produces a
judgment at real cost and has nowhere to put it. That holds. Two of its
supporting claims do not.

### Claim 1: "the storage question is genuinely open"

**Substantially answered already, by ADR-0013 (#550, shipped).** The merge
ledger was relocated to `bundle/.state/ledger/<concept_id>.ledger.okf`, and
that decision carries a complete, working mechanism this change can reuse:

| Property | Mechanism |
|---|---|
| Location | `bundle/.state/<kind>/`, inside `bundle/` so it is committed and survives a derived-index rebuild |
| Format | a frontmatter document with an empty body, via `okf.dump_frontmatter`/`load_frontmatter` (ADR-0002 invariant 3, preserved literally) |
| Exclusion | the non-`.md` suffix excludes it from all six `rglob("*.md")` walks with ZERO edits at those sites — structural, not a maintained predicate |
| Inclusion | one shared `iter_ledgers`-style primitive is the single INCLUDE walk for privacy sweeps, kept structurally separate from the EXCLUDE walks so the two cannot drift |
| Guard | a `lint` rule flags any `.md` under `bundle/.state/` |
| Path mapping | `okf.concept_path_for`, generalized to `(root, suffix)` |

It also records two hazards this change inherits verbatim:

1. `_autocommit` uses scoped `git add -- <paths>`, never `-A`, so a new
   `bundle/.state/**` path that is not explicitly staged **silently never
   enters git**.
2. `purge`'s `git filter-repo` path set must explicitly cover any new
   subtree, or historical confidential content survives a privacy purge.

ADR-0013 is scoped: it "supersedes ADR-0002's STORAGE clause only". It did
not declare `bundle/.state/` a general home. So the mechanism is settled;
the policy question of *what else belongs there* is the open part.

### Claim 2: the eight symptoms are one problem

They are two, with different natures, and conflating them is what makes the
storage question look harder than it is:

| | Nature | Recomputable? | Volume | Embeds concept content? |
|---|---|---|---|---|
| **Findings** — contradictions, candidate edges, duplicate groups, volatility proposals | machine inference | **Yes**, at cost (measured: 64 LLM calls / 3m59s for three contradictions) | large | yes — rationale quotes bodies |
| **Decisions** — declined / accepted | human judgment | **No, never** | tiny (ids + verdict) | no |

A finding lost to a rebuild costs money and time. A decision lost to a
rebuild is unrecoverable, and its loss is user-visible as proposals the user
already rejected coming back.

## Current state

### Where decisions die today

`cli/curate.py:130-135` — a stage result already carries
`status: Literal["applied", "declined", ...]` and `skipped_items`, "the
identities the items the OPERATOR declined at this stage". It is rendered
into the run summary and then discarded. The data structure for the
retractable property already exists in memory; nothing persists it.

Not to be confused with `next_action`'s `declinations`
(`cli/next_action.py:139-180`), which are in-run notices about documents
whose retry command could not be safely spelled (#276) — a different concept
sharing a word.

### What `next` reads, and why the expensive advisors have no tier

`cli/next_action.py` evaluates `_TIERS` in order, first hit wins:

1. `_tier_bootstrap_empty_bundle`
2. `_tier_missing_vector_index`
3. `_tier_stale_derived_indexes`
4. `_tier_unextracted_source`
5. `_tier_below_source_sensitivity`
6. `_tier_duplicate_groups`
7. `_tier_non_nfc_names`

Every one reads a **cheap, deterministic** signal: index presence, mtimes,
frontmatter fields, name normalization, exact-title duplicate groups. Not
one reads LLM-derived output, because there is nothing persisted to read.
That is the gap stated structurally: `next` is not missing a ranking
mechanism, it is missing an input.

The honesty guard is already correct and should be preserved:
`next_action:617-618` — "A `None` `action` means no ranked tier produced a
finding — **not that the bundle is clean** (D4)", and `_NO_ACTION_LINE` is
documented as "deliberately silent about whether OTHER, commandless
findings exist".

Note `_tier_duplicate_groups` already exists, so #565's "status reports
nothing needs attention while 42 duplicate groups remain" is a divergence
between `status` and `next`, not an absent signal. Worth confirming during
design rather than assuming it is fixed by persistence.

## Decisions taken with the maintainer (2026-08-12)

1. **Split storage.** Decisions → `bundle/.state/` (irreplaceable, tiny, ids
   and verdicts only, must survive and travel with a clone). Findings →
   `.openkos/` (recomputable, bulky, may embed confidential text — where
   `purge`'s existing delete-and-rebuild already covers them, adding no new
   privacy surface).
2. **First slice is one advisor, vertically.** Contradictions (#556), with
   all five properties: written down, rankable, retractable, re-openable,
   invalidated honestly. Replicate to edges/duplicates/volatility after.
3. **Full SDD cycle**, with its own ADR citing ADR-0013.

## Open questions for design

- **Staleness.** The recommended mechanism is a content digest of the inputs
  a finding was computed from, exactly the technique `origin_key` shipped for
  #552 — exact rather than heuristic, and immune to the mtime reset a `git
  checkout` causes. Needs a decision on digest granularity (per input object
  vs. one digest over the ordered set).
- **`.openkos/` container format.** Findings could be a SQLite store beside
  `fts.db`/`graph.db`, or frontmatter sidecars. The DBs have a precedent for
  delete-and-rebuild; sidecars have a precedent for reviewability.
- **Decision identity.** A declination must survive the finding being
  recomputed, so it has to key on something stable about the *proposal*
  (e.g. the ordered pair of concept ids plus finding kind), never on a
  finding row id.
- **`purge` and `forget` interaction.** A decision in `bundle/.state/`
  referencing a purged concept id must be swept, exactly as the ledger is —
  ADR-0013's INCLUDE-walk pattern applies.
- **Does #565 need its own fix?** `_tier_duplicate_groups` already exists;
  confirm whether `status` diverges for an independent reason.

## Scope boundary

This change does NOT fix the eight symptoms individually. #553 (FTS never
built) and #557 (false all-clear on an unrelated graph) are independent
defects that appear in #572's list as evidence of the pattern, not as work
items here.
