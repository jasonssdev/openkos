# Proposal: Durable pending work — the contradictions vertical

> **Verification note.** Every code citation below was re-read in this checkout.
> Issue bodies for #572/#556 were **not** re-fetched (this executor has no shell), so
> issue content is carried from the validated exploration artifact
> (`openspec/changes/durable-pending-work/exploration.md`), not from `gh`.

## Intent

The engine buys judgment at real cost and then throws it away. `curate`'s
contradictions stage runs an LLM over candidate pairs — measured at 64 calls /
3m59s for three contradictions — and produces `ContradictionVerdict` objects
whose own docstring states the outcome plainly:
`resolution/contradiction.py:163-164` — *"Ephemeral -- never a persisted OKF type
or `bundle`/`state` file."* The verdicts are echoed to the terminal
(`cli/curate.py:1289-1297`) and discarded with the process.

Two consequences follow, and they are **not the same problem**:

| | Nature | Recomputable | Volume | Embeds concept text |
|---|---|---|---|---|
| **Findings** — contradiction verdicts | machine inference | yes, at cost | large | yes (rationale quotes bodies) |
| **Decisions** — declined / accepted | human judgment | **never** | tiny (ids + verdict) | no |

That distinction is the load-bearing insight of this proposal. A lost finding
costs money and minutes. A lost decision is unrecoverable and user-visible as
proposals the operator already rejected coming back. Conflating them is what made
the storage question look open: it is not one store, it is two, with opposite
requirements.

The gap in `next` is structural, not a ranking defect. All seven tiers
(`cli/next_action.py:599-607`) read cheap deterministic signals — index presence,
mtimes, frontmatter, name normalization, exact-title duplicate groups. None reads
LLM-derived output because **nothing is persisted to read**. `next` is not missing
a mechanism; it is missing an input.

## Scope

### In scope — the contradictions vertical (#556), end to end

One advisor, carrying all five properties from #572:

| Property | Observable outcome for contradictions |
|---|---|
| **Written down** | A `CONTRADICTS` verdict from `curate` is readable in a later, unrelated process without re-running the LLM |
| **Rankable** | `next` can surface an open contradiction as a ranked action, using the persisted finding as its input |
| **Retractable** | An operator can decline a specific contradiction, and it does not reappear as open on the next run |
| **Re-openable** | A declined contradiction can be reinstated by explicit operator action |
| **Invalidated honestly** | When either concept the finding was computed from changes, the finding is marked stale — never presented as current, never silently deleted |

Plus: a new ADR citing ADR-0013 and stating exactly how far it extends it.

### Out of scope

- **The other three advisor kinds** — candidate edges, duplicate groups, volatility
  proposals. Replication to them is named follow-on work, deliberately not built here.
- **#553** (FTS never built) and **#557** (false all-clear on an unrelated graph) —
  independent defects cited in #572 as evidence of the pattern, not work items here.
- **#565** — `_tier_duplicate_groups` already exists, so this is a `status`/`next`
  divergence, not an absent signal. Confirm in design; do not assume persistence fixes it.
- Any general declaration that `bundle/.state/` is the home for arbitrary state.
  This change extends ADR-0013 by exactly one kind, and says so.

## Approach

**Split storage, along the two natures.**

| | Location | Rationale |
|---|---|---|
| **Decisions** (declined/accepted) | `bundle/.state/` | Irreplaceable, tiny, ids + verdicts only. Inside `bundle/`, so committed, surviving a derived-index rebuild and travelling with a clone. Reuses ADR-0013's mechanism verbatim: frontmatter container with empty body via `okf.dump_frontmatter`/`load_frontmatter`; a non-`.md` suffix for free structural exclusion from the six `rglob("*.md")` walks; one shared INCLUDE-walk primitive (`bundle/ledger.py:115 iter_ledgers`) as the pattern for privacy sweeps; path mapping via `okf.concept_path_for` (`model/okf.py:1296`, already `(concept_id, bundle_dir, *, suffix)`); the existing `lint` rule flagging any `.md` under `bundle/.state/` (`lint.py:1275`) |
| **Findings** (contradiction verdicts) | `.openkos/` | Recomputable at known cost, bulky, and the rationale embeds concept bodies. `purge` already physically deletes `.openkos/{fts,vectors,graph}.db` and rebuilds (`cli/main.py:4660,4803`), so this adds **no new privacy surface** |

**Two hazards inherited from ADR-0013 that the design must resolve explicitly:**

1. **Scoped staging.** `_autocommit` uses `git add -- <paths>`, never `-A`
   (`cli/main.py:6937-6942` documents this for the ledger sidecar). Any new
   `bundle/.state/**` decision path not explicitly added to the caller's path list
   **silently never enters git** — and a decision that never enters git is exactly
   the loss this change exists to prevent.
2. **`purge`'s path set.** History rewriting builds an explicit `literal:<path>`
   list (`vcs/git.py:519-530`); an empty or incomplete list is rejected or misses.
   A decision referencing a purged concept id must be swept, following ADR-0013's
   INCLUDE-walk pattern.

**Staleness** is proposed as a content digest over the inputs a finding was computed
from — the same exact-rather-than-heuristic technique `origin_key`
(`model/okf.py:154 origin_key_for`) shipped for #552, and immune to the mtime reset a
`git checkout` causes.

## Capabilities

### New Capabilities

- `pending-work-store`: durable persistence of contradiction findings (`.openkos/`)
  and operator decisions (`bundle/.state/`), their identity, lifecycle, and staleness.

### Modified Capabilities

- `contradiction-detection`: `ContradictionVerdict` gains a persisted representation;
  the ephemeral-only clause at `contradiction.py:163-164` is superseded.
- `curate-command`: **"Contradictions Stage Is Report-Only And Last"**
  (`openspec/specs/curate-command/spec.md:166`) is a shipped requirement. Adding a
  decline interaction changes it. This must be an explicit delta, not drift.
- `next-action-pointer`: a new tier reading persisted findings; the D4 honesty clause
  is preserved, not replaced.
- `privacy-purge`, `forget-command`: sweep coverage for the new `bundle/.state/` subtree.
- `workspace-autocommit`: the decision path joins the scoped stage list.
- `lint`: existing `.state/` guard extended to the new subtree if its suffix differs.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Decision identity keyed on a finding row id** — the finding is recomputed, the id changes, and every declination silently evaporates; rejected proposals return | High if unguarded | **Critical** | Identity keys on the *proposal*: sorted `pair_ids` + kind + `merged_absorbed_id`. That last field is not optional — `contradiction.py:185-198` warns it is the **SOLE** discriminator between a typed-edge and a merged-body candidate, and that `pair_ids` shape is *not* a safe stand-in. Test: recompute the finding, assert the declination still binds |
| **`next`'s honesty guard replaced by a false cleanliness claim** | Med | High | `next_action.py:617-618` — "A `None` `action` means no ranked tier produced a finding -- **not that the bundle is clean** (D4)" — and `_NO_ACTION_LINE` (`:77`) is deliberately silent about commandless findings. A persisted-findings tier makes *more* findings rankable; it must not license the inverse inference for the ones still unranked |
| Decision file never staged by `_autocommit`'s scoped `git add` | Med | High | Explicit path in the caller's list, mirroring `MergeResult.ledger_sidecar_path`; test asserting the path appears in the committed set |
| Decision referencing a purged concept id survives a privacy purge | Med | High | INCLUDE-walk primitive + explicit `literal:` path coverage, each with a test |
| Staleness digest granularity wrong (per-input vs. one digest over the ordered set) | Med | Med | Named open question; design must decide before spec freeze |
| `bundle/.state/` accreting into a general dumping ground | Low | Med | The new ADR states the extension is exactly one kind and why the other kind was excluded |

## Open questions for design

1. **Digest granularity** — per input object, or one digest over the ordered set.
2. **`.openkos/` container format** — SQLite beside `fts.db`/`graph.db` (precedent:
   delete-and-rebuild) or frontmatter sidecars (precedent: reviewability).
3. **Re-open ergonomics** — is re-opening a command, a flag on `curate`, or an edit?
4. **Does #565 need an independent fix**, given `_tier_duplicate_groups` exists?
5. **Decline interaction shape** — contradictions is spec-bound report-only today;
   `StageOutcome.skipped_items` (`cli/curate.py:130-142`) already models per-item
   operator declination for other stages. Reuse it or introduce a distinct path?

## Rollback Plan

Revert the slice PR. Findings live in `.openkos/`, which is derived and rebuildable,
so a revert loses nothing irreplaceable there. Decisions in `bundle/.state/` are
git-tracked plain text: a revert leaves the files on disk, orphaned but readable and
recoverable by hand. No migration runs against pre-existing data — there is no
pre-existing durable pending work to migrate.

## Dependencies

- ADR-0013 (`docs/adr/0013-relocate-merge-ledger-to-bundle-state.md`), shipped —
  supplies the storage mechanism this change extends.

## Success Criteria

- [ ] A `CONTRADICTS` verdict written by one `curate` run is read by a later process without an LLM call.
- [ ] `next` ranks an open contradiction, with the D4 honesty clause intact and tested.
- [ ] A declined contradiction survives full recomputation of the finding, proven by a test that recomputes.
- [ ] A declined contradiction can be re-opened by explicit operator action.
- [ ] Changing either concept marks the finding stale; the stale finding is neither shown as current nor silently dropped.
- [ ] Decision paths appear in `_autocommit`'s staged set, with a test.
- [ ] `purge`/`forget` sweep decisions referencing purged ids, each with a test.
- [ ] A new ADR cites ADR-0013 and states exactly which kind of state it adds to `bundle/.state/` and which it deliberately keeps out.

## Proposal question round

`execution_mode` is `auto`, so the interactive question round did not run. The
following are **assumptions**, open to correction before spec:

1. **Re-open is explicit, never automatic.** A declination is not silently
   invalidated by content change; staleness marks the *finding*, not the decision.
2. **Findings are not shown to the operator once declined**, at any verbosity,
   unless re-opened. If a "show declined" view is wanted, say so now.
3. **No migration or import** of contradictions from prior runs — durable capture
   starts at first run after this change.
4. **`bundle/.state/` decision files are inspectable but not hand-edited**, matching
   ADR-0013's convention; no mechanism enforces it.

---

## Maintainer decisions — 2026-08-12 (supersede the assumptions above)

Recorded after the proposal question round was surfaced. Spec and design MUST
treat these as settled and MUST NOT re-open them.

### D1 — The Contradictions stage persists, but never proposes

The stage keeps printing exactly as it does today and additionally persists
each finding. It gains **no prompt** and proposes nothing to the operator, so
the shipped requirement *"Contradictions Stage Is Report-Only And Last"*
survives in substance.

The spec delta is therefore narrow and must be argued on that narrowness: it
distinguishes a **write to the knowledge bundle** — which the requirement
exists to forbid from this stage — from **recording what the stage already
computed**. The delta MUST state that distinction explicitly rather than
simply relaxing the MUST NOT.

Rejected: adding a `[y/N]` prompt to the stage (repeals a deliberate
requirement and returns `curate` to writing from its last stage), and moving
contradictions wholly into a new verb (duplicates the compute path already in
`curate` and leaves the user two places where contradictions appear).

### D2 — Declining is a non-interactive verb, inside this change

"Retractable" is one of the five properties this slice must deliver, and D1
removes the prompt that would have carried it. The decline path is therefore
a **non-interactive command surface** (a verb or flag addressing a persisted
finding by its stable identity), not an interactive walk, and it is IN scope
for this change. Without it the slice cannot demonstrate the property.

### D3 — Declined findings are hidden by default, with an explicit view

A declined finding does not reappear in ordinary output — that is the
retractable property. An explicit way to list declined findings MUST exist,
because an invisible declination is indistinguishable from the lost finding
this whole change exists to eliminate.

### D4 — Assumptions carried unchanged from the question round

1. Re-open is explicit, never automatic: staleness marks the **finding**, not
   the decision.
2. No migration or import of contradictions produced before this change;
   durable capture starts at the first run after it.
3. `bundle/.state/` decision files are inspectable but not hand-edited,
   matching ADR-0013's convention. No mechanism enforces it.

### D5 — Second shipped requirement needing an explicit delta

*"Resumability By Construction"* (`openspec/specs/curate-command/spec.md`)
states `curate` MUST NOT persist any queue or checkpoint file. Persisting
findings and decisions is **not** persisting a queue: the stage queue is
still re-derived from bundle state on every run, and no run-scoped progress
is written. The delta MUST make that boundary explicit rather than leave a
reviewer to infer it, since the requirement's wording is broad enough to read
either way.
