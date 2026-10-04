# Proposal: Attach, Don't Fork, At Ingest (#1268 deliverable 1)

Refs #1268, #1259. Part of the arc "The Quiet Engine". Deliverable 2 of the
arc is the sibling change `retire-superseded-sources`, which depends on this
one.

## Intent

A concept name that already exists is written as `<slug>-N` instead of
revising the existing concept. The 0.4.0 human E2E produced 9 `-N` families
(4 ids for one file) and every one was a true duplicate: an artifact of
ingest, not a judgment call. Editing one watched source re-extracted it into
four more `-N` copies (#1259). Each family is later queued as an Identity
decision for a person, so the engine manufactures most of the review load
it then asks a human to clear.

This change makes ingest revise the concept instead: an extracted candidate
that matches an existing concept on the same OKF type and the same
normalized title key appends its provenance to that concept, merges its new
evidence into it, bumps `version`, and keeps the canonical Concept ID. That
is the "living objects" principle (AGENTS.md) implemented at the one place
the engine currently forks.

## Scope

### In Scope

- Staging (`application/ingest.py::stage_derived_objects`) looks a candidate
  up by `(type, normalize_key(title))` among existing, non-deprecated
  concepts, before the slug-collision path. A match becomes an **attach plan**
  instead of a `<slug>-N` create plan.
- An attach rewrites the existing concept: `provenance` unioned (existing
  first), `sources` re-projected, new body evidence appended in a delimited
  section, `## Related` backlink for the new Source added, `tags` unioned,
  `sensitivity` recomputed as the high-water mark (`combine_sensitivity`),
  `freshness`/`generated` taken from the newer side, `version` incremented.
  `type`, `title`, `description` and the Concept ID are the existing
  concept's and never change.
- Event and Person are excluded and keep today's `-N` behavior (owner
  decision on #1268, because of homonyms, #776 and #796). The exclusion is a
  named constant, so admitting a type later is a one-line change backed by a
  measurement.
- A same-source match stays the create-only no-op it is today (a concept
  already carrying this Source's provenance is left byte-untouched), so
  re-ingest remains idempotent and never bumps `version` twice.
- A deprecated concept is never an attach target; a candidate whose only
  same-key match is deprecated falls back to today's disambiguation.
- One config key, `attach_at_ingest` (boolean), gates the behavior; the
  default is Open Question 1.
- Disclosure: an `**Attach**` entry per attached concept in `log.md`, an
  `attached` count in the ingest outcome, one run-summary line naming each
  attach.
- A new version of a watched Source revises the concepts its predecessor
  produced as a consequence of the above, with no watch-specific code.
- Living-spec deltas, `docs/cli.md` (ingest summary), an ADR.

### Out of Scope

- Deprecating concepts whose provenance is a superseded Source, retiring the
  old Source without `--force`, and the `supersedes` vocabulary note: all in
  `retire-superseded-sources`.
- Any cross-type attach, any fuzzy or alias matching, any LLM judgment in
  the match (ADR-0034: judge confidence is not a signal; the match is purely
  structural).
- Merging two existing concepts that already form a `base`/`-N` family
  (#1268 deliverable 4, which needs its own pre-registered measurement).
- Letting the extractor see the existing body (a prompt change; prompt
  changes are adopted only after an A/B measurement).
- An `unattach` verb. Undo is reverting the ingest's own commit.
- Any `index.md` bullet change: the concept keeps its id, title and
  description, so its catalog entry is untouched.

## Approach

`_prepare` already reads the bundle through `fsio.snapshot_read` for the
drift guard. It additionally builds one `AttachIndex`, a mapping
`(type, normalized key) -> existing concept (id, text, bytes)` over
non-deprecated documents of non-excluded types, using the same walk and the
same `normalize_key` that `resolution/candidates.py` uses for its HIGH tier
(a private helper promoted to a public one, not copied). It passes the index
into `stage_derived_objects`, which stays free of filesystem reads.

For a match, staging composes the attached document with a new
`okf.build_attached_document`, a sibling of `build_merged_document` that
shares its field-union core (list union, high-water sensitivity,
freshness-winner, `sources` projection) so the two cannot drift. The attach
plan carries the new text and the baseline bytes; Phase B writes it with
`write_atomic` after the drift guard (`guarded_targets`), not the
create-only `write_exclusive` path used for new objects. See `design.md`
for the decisions and the code reality check.

## Capabilities

### Modified Capabilities

- `ingestion` -- MODIFIED "Bounded, Deduplicated Derived-Object Staging",
  "Re-Extraction Reconciles Derived Objects Per Slug", "Disambiguated
  Concepts Remain Resolvable", "Derived Object Cataloging and Logging";
  ADDED "An Extracted Candidate Attaches To An Existing Same-Type,
  Same-Key Concept", "An Attach Revises The Existing Concept
  Deterministically", "Attach Is Gated By A Config Key And Disclosed".
- `ingest-application-service` -- ADDED "Staging Plans Distinguish A New
  Object From An Attachment".
- `folder-watch` -- ADDED "A New Version Revises The Concepts Its
  Predecessor Produced".

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/application/ingest.py` | Modified | `DerivedPlan` attach fields, attach lookup before the collision path, `compose_catalog_update` log entry |
| `src/openkos/application/ingest_service.py` | Modified | Build the `AttachIndex`; attach targets join `guarded_targets`, not `created_targets`; Phase B atomic write; outcome count |
| `src/openkos/model/okf.py` | Modified | `build_attached_document`; shared union core factored out of `build_merged_document` (merge output byte-identical) |
| `src/openkos/resolution/candidates.py` | Modified | Public same-type/key index over the existing private eligibility walk |
| `src/openkos/config.py`, `src/openkos/templates/` | Modified | `attach_at_ingest` key and its template comment |
| `src/openkos/cli/main.py` | Modified | Run-summary attach lines (presentation only) |
| `docs/cli.md`, `docs/adr/` | Modified | Ingest summary; ADR (Proposed) |
| `tests/unit/...` | Modified | Staging, composition, service, watch, merge-equivalence tests |

## Risks

| Risk | Mitigation |
|---|---|
| **Homonyms attach wrongly** ("Representation, not truth"). Same type and same key can still name two things (a `Project` named "Atlas" at two companies). | Event and Person excluded; matching is exact on type and key only; per-attach disclosure; kill-switch key; the arc's pre-registered metrics (decisions per source, zero `-N` families) are measured before any further type is admitted. |
| **First `version` bump in the engine.** `version` is written as 1 by the builders and no engine path increments it today (merge keeps the survivor's). | Defined here: +1 per attach; a missing or non-integer value is read as 1. Specified and tested. |
| **Body growth.** Appended sections accumulate across versions. | Skip the body append when the candidate body is already contained in the existing body (provenance and `version` still update); `curate` reconcile remains the consolidator. Open Question 2. |
| **Concurrent revision.** An attach target can change between Phase A and the commit phase (a person, a second watch pass). | The target is a guarded target: the existing drift guard refuses the whole ingest before the first write; the watch retries on its next pass. |
| **Sensitivity.** An attach can raise an existing concept's level. | Recomputed with `combine_sensitivity`, exactly as merge does; concepts that cite the raised concept are reported by the existing `lint` high-water scans, as after a merge. |
| **Pre-existing `-N` families.** A match may be a family. | Attach to the canonical member: the un-suffixed id, else the lowest `-N` (the #1228 rule `ordered_merge_pair` already encodes). Families are not merged here. |
| **Normalized key is broader than the slug.** `normalize_key` strips diacritics and punctuation that `slugify` keeps, so "Café" and "Cafe" now match. | Intended (it is the key the HIGH Identity tier already uses); stated in the spec and tested. |

## Rollback Plan

Set `attach_at_ingest: false` to restore today's behavior with no data
change. Revert the commits to remove the code: no migration is needed
because an attached concept is an ordinary conformant document; `version`
and the extra `provenance` entries are valid OKF content. A single bad
attach is undone by reverting the ingest commit that carries it.

## Open Questions (for the owner)

1. **Default for `attach_at_ingest`.** (a) on for every ingest path,
   including the unattended watch, with the kill switch; (b) on for a
   person's `ingest` only, the watch keeps `-N` and enqueues; (c) off by
   default, opt in. ADR-0034 ties skipping a person's consent to an opt-in
   class that passed a pre-registered measurement. (a) cuts the load at the
   source and is what the exit criterion "zero `-N` duplicates outside the
   excluded types" needs, but it rewrites existing concepts unattended
   before a measurement exists. (c) is the most conservative and leaves the
   default behavior unchanged, so the arc's deliverable 2 stays inert until
   switched on. **Recommendation: (a).** Attach asks for no decision that
   exists today; it prevents the duplicate that creates one. It meets
   ADR-0034's other conditions (announced, committed, logged, undone by
   reverting one commit), the types where a homonym is likely are excluded,
   and the kill switch is one key. If you want ADR-0034 applied to it
   literally, choose (c) and flip the default in the arc's closing ADR. The
   specs below are written for (a); choosing (b) or (c) changes one
   sentence and one scenario.
2. **How new evidence enters an existing body.** (a) deterministic append of
   the candidate body under a delimited heading, no model call; (b) reuse
   merge's reconcile (a model call that rewrites the stacked result into one
   voice past a threshold); (c) feed the existing body to the extractor so
   it emits the revised body. (a) is predictable, free, and fits the commit
   phase (ADR-0036: no model call under the lock), but concepts grow and
   read as stacked sections until `curate` reconciles them. (b) reads better
   but spends a model call per attach and rewrites prose unattended. (c)
   reads best and is a prompt change that needs its own measurement.
   **Recommendation: (a)**, with the contained-body skip, and let the
   existing reconcile in `curate` consolidate.
3. **What retiring the old Source does** (owned by the sibling change
   `retire-superseded-sources`, listed here because it decides how an
   attached concept's two Sources are cleaned up). See that proposal's
   Open Question 1.

### Decision recorded, no owner input needed

A same-key match that is deprecated is never an attach target; the candidate
takes today's `<slug>-N` path. Attaching to a deprecated concept would
quietly resurrect a superseded concept; refusing would add a queue row for a
rare case. The cost is a `-N` twin when a retired concept's subject returns,
which `curate` already handles. Say so if you want it revived instead.

## Resolved Decisions (owner, #1268)

1. `attach_at_ingest` is ON by default on every ingest path, including the
   unattended watch, with the key as the kill switch (option a).
2. New evidence enters an existing body by deterministic append, no model
   call (option a).
3. Retiring the old Source removes the `supersedes` edge and detaches the old
   Source from surviving concepts in the same confirmed forget (option a;
   implemented by `retire-superseded-sources`).
4. A deprecated same-key match is never an attach target (the smaller call
   above stands).

## Success Criteria

- [ ] A candidate with the same type and normalized key as an existing
      non-excluded, non-deprecated concept writes no `<slug>-N` file; the
      existing concept gains the Source in `provenance`/`sources`, the new
      evidence, and `version + 1`.
- [ ] Event and Person candidates behave byte-for-byte as before.
- [ ] Re-ingesting the same Source after an attach writes nothing and does
      not bump `version`.
- [ ] A watched file's new version revises its predecessor's concepts and
      writes no `-N` copy of them.
- [ ] Attaching a candidate whose Source is more sensitive raises the
      concept's `sensitivity` to the high-water mark.
- [ ] `merge` output is byte-identical before and after the shared core is
      factored out.
- [ ] All gates green (ruff check, ruff format --check, mypy ., pytest
      --cov, eval self-tests); the PR uses `Refs #1268` and `Refs #1259`.
