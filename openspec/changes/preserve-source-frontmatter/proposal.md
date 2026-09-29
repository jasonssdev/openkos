# Proposal: preserve-source-frontmatter — keep an incoming file's frontmatter and lift a safe subset

## Intent

Refs #1062. Exploration: Engram `sdd/preserve-source-frontmatter/explore`.
Follows #1064 (okf-v02-migration, ADR-0029), which has shipped.

Many files a user ingests already carry YAML frontmatter written by another
tool (Obsidian notes, static-site posts, exported meeting notes): `tags`,
`date`, `sensitivity`, `author`, `aliases` and so on. Today `ingest` never
parses it. The block is embedded as plain text in the Source body under
`## Source content`, and the line-based `---` skip used for title derivation
is the only code that knows it exists. The Source's own `tags` is hardcoded
to `[]`, derived concepts' `tags` is hardcoded to `[]`, and the file's
`date:` plays no part in event-date resolution. The user's own metadata is
therefore invisible to retrieval, filtering and the graph, even though they
already did the curation work.

Why now: #1064 is merged, so the v0.2 frontmatter shape this change writes
into is settled, and every bundle ingested before this lands is one more
bundle whose Sources lack the structured record.

Success: ingesting a file with frontmatter produces a Source that carries
the whole incoming mapping verbatim under `source_frontmatter`, carries the
incoming `tags` (unioned with any tags already on disk), resolves
`event_date` from `date:` when neither a flag nor a stored value exists,
never lowers sensitivity, and passes the incoming `tags` to every derived
concept created in that run. A hostile, malformed or non-mapping block
never fails the ingest and never lifts anything.

## Scope

### In Scope

1. **Incoming-frontmatter parse seam (safety first).** One new function in
   `src/openkos/model/okf.py` (the OKF adapter seam, AGENTS.md) parses the
   leading frontmatter of a decoded incoming raw file:
   - YAML only (`---` fence); no TOML (`+++`) or JSON (`;;;`) fence
     auto-detection.
   - `yaml.safe_load` semantics; a bounded block size and an alias/anchor
     expansion guard.
   - A non-mapping root, malformed YAML, an over-limit block or any parse
     exception returns "no frontmatter". The ingest continues, the raw copy
     is unchanged, the body embedding is unchanged, and nothing is lifted.
   - The block boundary this function uses and the frontmatter-skip
     boundary of title derivation (ingestion spec: a leading `---` counts as
     frontmatter only when a closing `---` exists) are the same rule, so the
     two can never disagree about where the block ends.
   - Non-UTF-8 sources are never parsed.
2. **Verbatim preservation.** `build_source_concept` emits the parsed
   mapping, whole and unmodified, under the extension key
   `source_frontmatter` (legal under OKF v0.2 as an unknown frontmatter
   key). The YAML block also stays in the Source body under
   `## Source content`, exactly as today.
3. **Selective lift onto the Source.**
   - `tags`: lifted onto the Source's `tags`. On re-ingest, the result is
     the union of the tags already on the on-disk Source and the lifted
     tags (deduplicated, order-preserving). Hand-added tags are never
     removed.
   - `sensitivity`: folded into the Source's sensitivity through the
     existing `combine_sensitivity` high-water mark. It can raise, never
     lower. An unrecognized value fails closed, as `combine_sensitivity`
     already does.
   - `date:` becomes an event-date tier. On a first ingest the order is
     flag > `date:` > file name. On a re-ingest it is flag > stored >
     `date:` > file name. `created:` is not lifted. The event-date origin
     disclosure line gains an origin label for the frontmatter tier.
4. **Tag propagation to derived concepts, at creation time.**
   `build_concept` gains an optional `tags` parameter. Its default keeps
   every existing caller, including `query --save`, byte-identical.
   `stage_derived_objects` threads the Source's resolved tags to each
   derived concept created in the same run, following the shape it already
   uses for sensitivity inheritance. The existing generic list union in
   `build_merged_document` already unions `tags` on merge, so it needs no
   change. A test pins that behavior.
5. **Spec deltas and ADR-0030.** ADR-0030 (written in design): untrusted
   incoming frontmatter, stored as a verbatim namespace, with a per-key
   lift policy. Deltas are listed under Capabilities.

### Out of Scope

- Lifting `author` or `updated`. See decision 7. Both stay verbatim in
  `source_frontmatter`.
- Lifting `status`, `type`, `provenance`, `version`, `timestamp`,
  `generated`, `verified`, `sources`, `created`, `title`, `description` or
  any key not named in decision 3. All of them stay verbatim-only.
- Re-syncing tags on derived concepts that already exist when a Source's
  incoming tags change. Follow-up issue F1.
- Re-propagating a raised Source sensitivity to existing descendants.
  `set-sensitivity` (ADR-0009) remains the tool for that.
- A `doctor`/`lint` advisory for sources whose frontmatter could not be
  parsed. Possible follow-up; not required.
- Parsing TOML or JSON frontmatter.
- Any change to title derivation precedence. A frontmatter `title:` stays
  namespace-only.
- #1076 (whether a Source's `provenance` keeps its own `raw/` entry). It is
  orthogonal: `project_sources` excludes `raw/` entries either way, so it
  does not affect anything this change writes.

## Decisions

Decisions 1–6 are owner decisions (final, 2026-09-29). Decisions 7–11 are
the orchestrator's final resolutions of gaps the exploration raised.

| # | Decision | Chosen | Rejected | Why |
| --- | --- | --- | --- | --- |
| 1 | Where parsing lives (owner) | One seam function in `model/okf.py`; `application/ingest.py` calls it | Parse inside `application/ingest.py` or `source_title.py` | AGENTS.md puts all knowledge of the frontmatter format in the OKF adapter seam. |
| 2 | How the mapping is stored (owner) | Whole mapping, verbatim, under `source_frontmatter` on the Source | Store only lifted keys; spread incoming keys into top-level frontmatter | Storing verbatim loses nothing and cannot collide with OKF or OpenKOS keys. Top-level spreading would let an incoming file overwrite `type`, `provenance` or `sensitivity`. |
| 3 | Which keys are lifted (owner) | `tags`, `sensitivity` (raise-only) and `date:` only | Lift everything recognizable; lift nothing | These are the keys with a clear OpenKOS meaning and a safe merge rule. Everything else keeps its meaning only inside the namespace. |
| 4 | Keys that are never lifted (owner) | `status`, `type`, `provenance`, `version`, `timestamp`, `generated`, `verified`, `sources` | Lift with validation | These are engine-owned or trust-bearing fields. An external file must never be able to assert them. |
| 5 | Event-date tier (owner) | `date:` only, below flag and stored value, above the file name | Also accept `created:`; rank `date:` above the stored value | `date:` is the common "when it happened" key. `created:` often records when the file was made. Ranking the stored value higher keeps the existing rule that a re-ingest never clobbers it. |
| 6 | Body embedding (owner) | The YAML block stays in the Source body | Strip it from the body | No change to what gets embedded, so no retrieval regression. The raw text stays faithful. |
| 7 | `author` / `updated` (revises the issue's original row) | Not lifted; they stay verbatim in `source_frontmatter` | Lift into `sources[].author` / `last_modified` as #1062 first proposed | ADR-0029 decided that a Source document carries no `sources` entry for its raw original, so the lift target the issue named does not exist on a Source. Putting either value on derived concepts' `sources[]` would make `project_sources` read another document's frontmatter, which breaks its contract of being a pure function of `provenance`. No consumer needs the values yet. |
| 8 | When tags propagate | Only to derived concepts created in the same run | Also re-sync existing derived concepts on every re-ingest | This mirrors how Source sensitivity reaches derived concepts in `stage_derived_objects`. ADR-0009 set the precedent that rewriting existing descendants is a separate, human-invoked verb, not a side effect of re-ingest. That verb becomes follow-up F1. |
| 9 | Source tags on re-ingest | Union of the on-disk tags and the lifted tags | Overwrite with the lifted tags | Overwriting would silently discard tags a human added by hand. "Human curates, engine maintains." |
| 10 | Safety at the new trust boundary | YAML only; `safe_load` semantics; bounded size; alias-expansion guard; a non-mapping root or a failed parse lifts nothing and never fails the ingest; one shared block-boundary rule | Reuse `load_frontmatter` unguarded; fail the ingest on bad frontmatter | This is the first time the engine parses YAML it did not write. `safe_load` blocks object construction but not alias amplification. `load_frontmatter`'s dict type hint is not enforced at runtime. A user's file with a typo must not block ingesting it. |
| 11 | ADR | ADR-0030, written in design | No ADR | The trust-boundary policy and the per-key lift rules become hard to reverse once bundles carry `source_frontmatter` and lifted values. |

## Capabilities

### New Capabilities

None. Everything here extends ingestion behavior. The spec phase MAY split
out `source-frontmatter` as a new capability if the ingestion delta grows
too large, and states why if it does.

### Modified Capabilities

Checked against `openspec/specs/` for this proposal:

- `ingestion`:
  - "Ingest Raw Copy and Source Concept Generation": adds the
    `source_frontmatter` key and lifted `tags`; the body is unchanged.
  - The title-derivation frontmatter-skip line: the boundary is shared
    with the new parse.
  - "Event-Date Precedence On First Ingest" and "Re-Ingest Event-Date
    Carry-Forward": the new `date:` tier.
  - "Event-Date Origin Disclosure Line": the new origin label.
  - "Converged Re-Ingest Date-Only Rewrite": see open decision D.
  - "Derived Object Provenance and Sensitivity Inheritance": tags are
    inherited at creation, and the Source's own sensitivity can be raised
    by its incoming frontmatter.
  - "Default Sensitivity from Config": the fold order.
  - New requirements: incoming frontmatter parsing and its fail-closed
    safety contract; the `source_frontmatter` namespace; the never-lifted
    key list; the tag union on re-ingest.
- `ingest-application-service`: `compose_source_document` gains the parsed
  incoming frontmatter as an input and folds it in. `resolve_event_date`
  and `EventDateResolution.origin` gain the frontmatter tier.
- `entity-resolution-merge`: probably no delta. `tags` already goes through
  the generic list union. The spec phase confirms this and writes a delta
  only if the spec text does not already cover `tags`.
- `fts-state` and `reindex-command`: no delta. Both already index `tags`,
  so lifted tags become searchable with no spec change. This is a
  consequence of the change, not a requirement change.

## Approach

- **One seam, pure and tested first.** `okf.py` gains a pure function:
  decoded text in, either a mapping or "none" out. It is covered by unit
  tests for every fail-closed case: a non-mapping root, malformed YAML, a
  TOML/JSON fence, an unterminated fence, an oversized block, an alias
  bomb, an empty block and a BOM. Ingest calls it once per source.
- **Lift is a small, explicit policy.** One function maps the parsed
  mapping to `(tags, sensitivity, date)` candidates. Anything else is
  ignored for lifting. Invalid shapes lift nothing. For example, `tags`
  that is not a string or a list of strings lifts no tags; design fixes
  the exact normalization. `date:` reuses the same tolerant date shape
  checks as the existing stored-`event_date` reader, with a separate key.
- **Reuse existing folds.** Sensitivity goes through `combine_sensitivity`.
  Event date goes through `resolve_event_date` with one more tier. Tags
  follow the sensitivity-inheritance path in `stage_derived_objects`.
- **Byte-identity where nothing changed.** A source without frontmatter
  produces a byte-identical Source and byte-identical derived concepts, so
  existing goldens hold for frontmatter-free inputs.
- The core stays synchronous. There are no new dependencies (`pyyaml` and
  `python-frontmatter` are already present). `raw/` is untouched.

### Slice plan (auto-chain, stacked to main, dependency order)

Forecasts are the exploration's estimates scaled by the observed ~1.95x
forecast-to-actual ratio. They count authored lines (additions plus
deletions) and exclude delta specs. The exploration's four slices scale to
~1,560–2,730 lines, and three of them exceed 400 lines at the scaled low
end, so they are split here.

| # | Slice | Contents | Scaled forecast |
| --- | --- | --- | --- |
| 1 | Parse seam | `okf.py` incoming-frontmatter parser, shared block-boundary helper (title derivation switched to it), safety tests; ADR-0030 lands here or with the design commit | ~300–450 |
| 2 | Verbatim preserve | `build_source_concept` emits `source_frontmatter`; `compose_source_document` wiring; golden and byte-identity tests | ~250–400 |
| 3 | Source lift: tags + sensitivity | tag lift and re-ingest union, raise-only sensitivity fold, review-preview visibility, tests | ~300–450 |
| 4 | Source lift: `date:` tier | frontmatter date reader, `resolve_event_date` tier and origin label, disclosure line, converged-rewrite behavior per decision D, tests | ~300–450 |
| 5 | Derived tag propagation | `build_concept(tags=...)` with a byte-identical default, `stage_derived_objects` threading, merge-union pin test | ~300–450 |
| 6 | Docs (if the shape changed) | a short note in `docs/knowledge-object-model.md` and/or `docs/okf-alignment.md` on `source_frontmatter` as an extension key; no count-bearing prose | ~50–150 |

Total ~1,500–2,350. Slices 3 and 4 are independent of each other; both
depend on 2. Slice 5 depends on 3. Delta specs land with the slice whose
behavior they describe.

## Affected Areas

| Area | Impact | Description |
| --- | --- | --- |
| `src/openkos/model/okf.py` | Modified | new parse and lift functions, `build_source_concept` emits `source_frontmatter` and tags, `build_concept` gains `tags` |
| `src/openkos/source_title.py` | Modified | frontmatter-skip boundary shared with the parser |
| `src/openkos/application/ingest.py` | Modified | `compose_source_document`, `resolve_event_date`/`EventDateResolution`, `stage_derived_objects`, converged-rewrite check |
| `src/openkos/cli/main.py` | Modified (small) | event-date origin label rendering only, if rendered there |
| `docs/adr/0030-*.md`, `docs/adr/README.md` | New/Modified | ADR-0030, status Proposed |
| `openspec/specs/ingestion`, `openspec/specs/ingest-application-service` (via deltas) | Modified | as listed above |
| `tests/unit/...` | New/Modified | safety, lift, union, precedence, propagation, byte-identity, merge pin |

## Principles Impact

- **Adopt OKF:** `source_frontmatter` is an extension key allowed under
  v0.2. No OKF field is re-decided, and incoming values can never assert
  OKF or engine-owned fields.
- **Immutable raw/:** unchanged. The raw copy is still an exclusive
  byte-for-byte write.
- **Sensitivity:** only raised, through the existing high-water mark.
  Confidential material stays on the device, and no new egress is added.
- **Provenance:** `provenance` and the `sources` projection are untouched.
- **Human curates:** hand-added tags survive re-ingest. Rewriting existing
  descendants stays a future explicit verb.
- **Reconstructible:** lifted tags reach FTS and embeddings through the
  existing rebuild path.

## Risks

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| A hostile or huge YAML block (alias bomb, deep nesting) stalls or exhausts memory during ingest | Low (local, single user), high impact | size bound checked before parsing, alias-expansion guard, fail-closed tests including an alias bomb |
| A non-mapping root crashes on `.get` | Med | explicit `isinstance(..., dict)` guard in the seam; tests for list and scalar roots |
| The title-skip boundary and the parse boundary disagree on an edge-case file | Med | one shared boundary helper; a parity test across edge cases |
| An incoming `sensitivity: public` lowers a private Source | Low | raise-only through `combine_sensitivity`; a test asserts a lower incoming value is ignored |
| Lifted tags change retrieval ranking, because FTS and embeddings already index `tags` | Med | expected and desired; byte-identity for frontmatter-free inputs keeps existing eval fixtures stable; note in the design |
| A raised Source sensitivity leaves existing descendants lower | Med | existing behavior class (ADR-0009); design decides whether ingest discloses it and points to `set-sensitivity` |
| Tags on existing derived concepts go stale | Med | stated in ADR-0030 Consequences; follow-up F1 |
| `source_frontmatter` holds values that do not round-trip through YAML emission (dates, nested mappings) | Med | design decides the emission rule (for example, emit exactly what `safe_load` produced through the existing dumper); round-trip tests |
| Goldens drift | Low | frontmatter-free inputs stay byte-identical; new goldens only for frontmatter inputs |

## Rollback Plan

Revert the slices in reverse order; each one is green on its own. Before
slice 2 merges, nothing new is written to bundles. After slices 2–5, a
bundle already carries `source_frontmatter` and lifted tags. Pre-change
readers ignore the unknown extension key, and tags are an ordinary OKF
field, so reverting the code leaves those bundles readable and conformant.
To undo the data for a specific bundle, `git revert` the ingest
autocommits, or remove the key by hand. Reverting slice 4 restores the old
event-date precedence. `event_date` values already written stay as stored
values, which is the existing carry-forward behavior.

## Dependencies

- #1064 / ADR-0029, already merged: the v0.2 field set and the decision
  that a Source carries no `sources` entry for its raw original.
- No new runtime dependencies.

## Follow-up issues to open

- **F1: Re-sync tags onto existing derived concepts.** When a Source's
  incoming tags change on re-ingest, existing derived concepts keep their
  creation-time tags. Propose an explicit, reviewable verb (or an option on
  an existing one) that unions the Source's tags onto its provenance
  descendants, modeled on `set-sensitivity` (ADR-0009).
- **F2 (optional): Advisory for unparseable incoming frontmatter.** A
  `doctor`/`lint` advisory, or an ingest stderr note, when a source's
  frontmatter was present but lifted nothing.
- **F3 (only if a consumer appears): `author`/`updated`.** Revisit a
  structured home for these values if a concrete consumer needs them;
  decision 7 records why there is no honest target today.

## Success Criteria

- [ ] Ingesting a file with valid YAML frontmatter yields a Source whose
      `source_frontmatter` equals the parsed mapping, whose `tags` include
      the incoming tags, and whose body still contains the YAML block.
- [ ] Re-ingesting after a human added a tag to the Source keeps that tag.
- [ ] An incoming `sensitivity` lower than the resolved value leaves it
      unchanged; a higher value raises it.
- [ ] `date:` sets `event_date` only when no flag or stored value exists,
      and the disclosure line names the frontmatter origin; `created:` is
      ignored for this.
- [ ] Derived concepts created in the run carry the Source's tags;
      `query --save` output and all frontmatter-free ingests are
      byte-identical to before.
- [ ] A non-mapping root, malformed YAML, a TOML/JSON fence, an oversized
      block and an alias bomb each result in a successful ingest with
      nothing lifted and no `source_frontmatter`.
- [ ] Never-lifted keys (decision 4) present in the input do not change the
      Source's own values for those keys.
- [ ] `uv run ruff check .`, `uv run ruff format --check .`,
      `uv run mypy .`, `uv run pytest --cov` (90% branch gate) and the eval
      self-test sweep are green.
- [ ] ADR-0030 exists, status Proposed, indexed.

## Open decisions for the orchestrator

These are not settled by the owner or orchestrator decisions above. Slices
1–3 and 5 do not depend on them. Slice 4's converged-rewrite part does.

- **D. Does a converged plain re-ingest rewrite the Source when only the
  lifted state differs?** Today a byte-identical, already-extracted re-ingest
  writes nothing, except the "date-only rewrite" when the resolved
  `event_date` changed. A Source ingested before this change therefore
  never gains `source_frontmatter` or lifted tags unless the user passes
  `--re-extract`, which spends LLM calls. The `date:` tier already brings
  such Sources into the date-only rewrite when they have no stored date.
  Recommended: generalize the date-only rewrite into a Source-only rewrite
  that also runs when `source_frontmatter`, the tag union or the raised
  sensitivity differ from what is stored. It makes no LLM call and changes
  no derived object, and it goes through the same preview, confirm gate,
  drift guard and autocommit. Stake: with the recommendation, the first
  plain re-ingest after upgrading makes one reviewable Source-only commit
  for each source that has frontmatter. Without it, old Sources stay
  unenriched until `--re-extract`.
- **E. Is a list-valued `tags` the only accepted shape?** Recommended:
  accept a list of strings, or a single string treated as one tag; trim,
  drop empties, lift nothing for any other shape. Stake: a
  comma-separated string such as `tags: a, b` becomes one tag `"a, b"`
  rather than two. Design can settle this without the owner unless the
  owner wants comma splitting.

Note: this proposal relies on the exploration's record of the owner
decision comment on #1062; the issue itself was not re-read in this phase.
