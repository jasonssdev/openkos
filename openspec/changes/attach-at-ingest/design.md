# Design: attach-at-ingest

Refs #1268, #1259. Inputs: `proposal.md`, the owner's binding decisions on
#1268 (Event and Person excluded at first; opt-in-first for the structural
Identity class, which is deliverable 4, not this one), and the code at
`111d8c39` (0.4.0 plus review-prompt work).

## Technical approach

Ingest today decides a collision by SLUG, after extraction, inside
`stage_derived_objects`: `derived_path.exists()`, then `family_owns_source`
(same Source: create-only no-op) or `first_free_disambiguated_slug` (foreign
Source: `<slug>-N`). This change inserts one structural lookup in front of
that path and turns a hit into a revision of the existing concept.

```
 extractor (unchanged) -> candidates
        |
        v   per candidate, reply order
 slugify, empty-slug / in-batch guards (unchanged)
        |
        v
 type in ATTACH_EXCLUDED_TYPES, or attach_at_ingest off? --yes--> today's path
        |no
        v
 AttachIndex[(type, normalize_key(title))]  (non-deprecated, built in Phase A)
        |
   +----+----------------------------------+
   | no match                              | match(es)
   v                                       v
 today's path                       any match already carries
 (exists? own-source no-op /        sources/<this> in provenance?
  foreign -> -N)                     |yes: "already-exists" no-op (unchanged)
                                     |no : pick canonical member, compose
                                           attached document -> attach plan
```

Phase B treats an attach plan as a guarded rewrite of an existing file:

```
 _prepare (no lock)            commit phase (lock, ADR-0036)
 snapshot_read targets  --->   _revalidate (drift guard over guarded_targets,
 build AttachIndex                          now including attach targets)
 stage + compose attach        write_atomic(attach target)   [not write_exclusive]
 text from baseline bytes      index.md unchanged for the target
                               log.md += **Attach** entry
                               autocommit
```

## Decisions

### D1. The lookup is a parameter, not a read inside staging

`stage_derived_objects` receives `existing: AttachIndex | None`. `_prepare`
builds it from the same `fsio.snapshot_read` observations that feed
`guarded_targets`, so the baseline bytes the drift guard compares are the
very bytes the attach text was composed from (the #306/#313/#318 one-read
rule). `None` means attach is off for this run (key off, or a path that
passes no index), and staging behaves exactly as today. This keeps the
"service reads no files" requirement of `ingest-application-service` true.

### D2. Match key, canonical member, and eligibility

- **Key:** `(okf type, resolution.normalize.normalize_key(title))`. This is
  the key the HIGH Identity tier already uses (`resolution/candidates.py`),
  so "same family" means the same thing in ingest and in `duplicates`.
- **Eligibility:** documents that read and parse, have a non-empty string
  `type` and `title`, are not `Source`, are not effective-deprecated
  (`lifecycle.deprecated_concept_ids`), and whose type is not in
  `ATTACH_EXCLUDED_TYPES = {"Event", "Person"}`. `candidates._iter_eligible`
  and `_eligible_keyed_docs` already implement the first four; they are
  promoted to one public function (`keyed_documents(bundle_dir,
  include_deprecated=False)`) returning the concept id, type, title and
  key, and the existing HIGH/LOW passes are re-pointed at it, so there is
  exactly one eligibility rule. `ATTACH_EXCLUDED_TYPES` lives in
  `application/ingest.py` beside the other staging constants.
- **Several matches** (a pre-existing `base`/`-N` family): attach to the
  canonical member, the un-suffixed id, else the lowest `-N`. This reuses
  the identity rule of #1228 (`lifecycle._is_suffix_family` and
  `ordered_merge_pair`), which is promoted to a small public
  `canonical_family_member(ids)` rather than re-derived.
- **Cross-type:** never. A same-key document of a different type is not a
  match (matches the proposal's out-of-scope and the "no automatic
  cross-type merges" line of #1268).

### D3. The attached document shares `build_merged_document`'s core

`okf.build_attached_document(existing_metadata, existing_body, candidate,
source_id, ...)` produces the revised `(metadata, body)`. The frontmatter
rules are those of `build_merged_document` and MUST NOT be copied: the
list-union (`_union_dedup`, survivor first), the high-water
`combine_sensitivity`, the freshness-winner (`_absorbed_is_more_recent`),
the `generated` handling and the `sources` re-projection from the unioned
`provenance` are factored into one private core that both builders call.
The `merge` golden tests pin that merge output is byte-identical after the
refactor; that is the guard that the two cannot drift.

Differences from merge, all deliberate:

| Field | Merge | Attach |
|---|---|---|
| `type`, `title`, `description` | survivor's | existing concept's (the candidate's never replace them) |
| `version` | survivor's, untouched | existing `version` read as an int (absent or not an int counts as 1), plus 1 |
| `generated` / `freshness` | newer side | newer side (a freshly extracted candidate is newer) |
| `tags` | union | union (the candidate carries the Source's tags) |
| `merged_from` ledger | written by `plan_merge` | none: an attach is not a merge of two concepts, it has no absorbed document to restore |
| `type_alternative` | never imported | never imported (same reason, #803) |
| body | survivor, then `## Merged content (id)` | existing body, then a delimited section (D4) |

### D4. Body: deterministic append, contained-body skip

The candidate's body evidence enters the existing body as a new section
inserted immediately above the existing `## Related` list, and one bullet
is appended to that list using the generator's own phrase "source this was
extracted from". Nothing already in the body is reordered or rewritten:

```
## Update from <Source title> (sources/<slug>)

<candidate body, headings demoted two levels, as merge does>
```

The heading names the Source so each stacked section is attributable, which
matches "provenance is first-class" without a citations list (OKF §5.1).
`_demote_absorbed_headings` is reused. When the candidate body, compared
after whitespace normalization, is already a substring of the existing body
(the same sentence re-extracted from a new version), no section is appended;
provenance, `sources`, the Related bullet and `version` still update,
because the Source did support the concept. No model call is made anywhere
in attach: the commit phase may not call a model (ADR-0036) and the
extractor is unchanged. Whether to prefer a model reconcile is Open
Question 2 of the proposal.

### D5. Phase B: attach targets are guarded rewrites

In `_prepare` an attach target goes into `guarded_targets` (baseline bytes
from the same `snapshot_read`) and NOT into `created_targets`; the Phase B
loop writes it with `fsio.write_atomic`, never `write_exclusive` (which
would always fail on an existing file). `index.md` is untouched for the
target (the concept keeps id, title and description). `log.md` gains one
`**Attach**` entry per attach; `compose_catalog_update` skips
`insert_index_entry` for an attach plan. `ports.autocommit` receives the
attach path alongside the created ones, and the commit message becomes
`openkos: ingest <name> (+N concepts, ~M revised)` when M > 0 and is
byte-identical when M == 0.

The catalog recompose callback (`recompose_catalog`) already re-applies the
staged delta over concurrent appends; the attach log line is part of that
delta. If an attach target changed since Phase A, `_revalidate` raises
`DriftDetected` before the first write: nothing is written, the watch
retries on its next pass, and a person's `ingest` re-runs. This is the
existing behavior for every guarded target and is not weakened.

### D6. Idempotency

A key match whose document already lists `sources/<this>` in `provenance` is
the existing "already-exists" no-op (byte-untouched). The check runs across
ALL key matches, not only the canonical one, so a Source that earlier won a
`-N` twin (a pre-change bundle) is still recognized as owning its object and
is not attached a second time. Re-ingesting after an attach therefore
writes nothing and does not bump `version` (the convergence gate in
`converged_reingest` is unchanged and runs first for byte-identical raw).

### D7. Gate and exclusions

`attach_at_ingest` (bool; default per Open Question 1) is read from
`openkos.yaml` like every other key and is named in the ingestion spec (the
`test_config_keys_specified` guard requires it). The template comment states
the behavior timelessly. `ATTACH_EXCLUDED_TYPES` is a constant, not config:
admitting Person or Event is a measured change that ships with its own
evidence, not a user knob (per #1268 decision 3).

## Code reality check

Places where the code contradicts the issue's wording or the current specs:

1. **`version` is never bumped.** The issue says "bump `version`". The
   builders write `version: 1` and no engine path increments it, merge
   included. This change defines the first bump (D3). `docs/knowledge-
   object-model.md` already describes `version` as a monotonic revision
   counter, so no doc contradiction arises; the spec states the rule.
2. **"Re-Extraction Reconciles Derived Objects Per Slug" forbids a merge.**
   It says an existing derived object is left byte-untouched, "no
   overwrite, no re-typing, no merge". That holds for the same-Source case
   and is kept; a foreign-Source key match is now the attach case. The
   requirement is MODIFIED, not worked around.
3. **"Disambiguated Concepts Remain Resolvable" would become false.** Its
   scenario produces `<slug>` and `<slug>-2` from two Sources with the same
   title; after this change that pair no longer forms for non-excluded
   types. The scenario is moved to an excluded type (Person).
4. **Match by key is not match by slug.** Today only an identical slug
   collides; `normalize_key` also folds diacritics and punctuation. The
   attach path therefore fires in cases the slug path never collided on,
   and the Concept ID kept is the existing one, not the candidate's slug.
5. **`compose_catalog_update` assumes every plan is new.** It inserts an
   index bullet per plan; an attach plan must skip that, or `index.md`
   gains a duplicate bullet.
6. **The raw-copy and Source write order is unaffected**, but the `ingest_
   pending` two-step marker (#1136) lists "adopted" uncatalogued objects; an
   interrupted run may leave a rewritten attach target uncommitted. The
   rewrite is idempotent by D6 (the target already carries the Source), so
   the next run treats it as already-exists and adopts nothing new. Tested.

## ADR gate

Condition 1 (decides a pattern/trade-off): yes, ingest changes from "fork on
collision" to "revise on structural match", which redefines what a Concept
ID stands for across sources. Condition 2 (hard to reverse): yes in
practice, because attached concepts accumulate provenance and `version` that
a later revert of the code does not undo, and external references keep the
canonical id. An ADR is therefore created at apply time (next free number,
status Proposed): "Ingest attaches to an existing same-type, same-key
concept instead of forking it". It records the alternatives (post-hoc
`-N` merge only, which is deliverable 4; an LLM judge at ingest, which
ADR-0034's FAIL rules out; opt-in only). It does not supersede ADR-0034: it
is not a skipped-consent Identity merge between two existing concepts.

## Testing approach

Strict TDD. Each task below begins with a failing test; the merge-equivalence
golden is written BEFORE the core is factored out so the refactor has a
byte-exact oracle. Mutation checks target the exact lines (type exclusion,
same-source guard, `version + 1`, the guarded-target registration), restored
by inverse edit.
