# Proposal: Add a `list` verb so concept ids are discoverable from the CLI

Issue #184 (P1). Exploration: `openspec/changes/discover-concept-ids/explore.md`.

## Intent

`forget`, `relate`, `merge`, `unmerge`, and `set-sensitivity` all take a raw bundle-relative
concept id, and no verb prints one. `status` reports counts only. Today the only way to obtain an
id is to browse the bundle directory by hand, which makes five write verbs effectively
unreachable from the CLI alone. `list` closes that gap.

## Scope

### In Scope
- New `openkos list [TYPE]` read-only verb: workspace gate, exactly one bundle walk, `typer.echo`.
- New single-pass `_iter_docs` enumerator (preferred placement `src/openkos/bundle/listing.py`,
  a leaf like `lifecycle.py`; final placement is a design call).
- Optional positional type filter, `--limit N`, `--all`.
- Alphabetical ordering by id (free — `_iter_docs` is already `sorted`).
- Unit tests, and a new `### openkos list` section in `docs/cli.md`.

### Out of Scope (explicit non-goals)
- `--json` or any structured output (decision C).
- Recency ordering, `--fields`, `--sensitivity` filter, full-text search over titles.
- Any change to `status`, `duplicates`, `survey_bundle`, or the id format.
- Any change to what `--include-confidential` means.
- MCP/API surfaces.

## Capabilities

### New Capabilities
- `list-command`: enumeration of bundle objects with their ids, sensitivity, lifecycle status,
  and titles; type filtering; output bounding.

### Modified Capabilities
- None. `status`'s spec is untouched; `list` does not inherit its
  "Read-Only and Human-Readable Only" clause.

## Decisions

**1. Confidential titles are printed in full (decided by the user; do not reopen).**
`list` prints every object's full title regardless of `sensitivity`. No redaction, no flag, no
omitted rows; output is identical at every sensitivity level. Rationale: the bundle is local and
the terminal belongs to the data's owner. `sensitivity` governs what LEAVES the machine (LLM
send), not what the owner sees on their own screen. Precedent: `duplicates`
(`src/openkos/cli/main.py:5149-5218`) already prints ids for confidential objects with no gate,
and `--include-confidential` (`src/openkos/sensitivity.py:78-99`) is exclusively an LLM-send gate
(`should_block` / `blocks_llm_send`) that MUST NOT be overloaded into a display gate. Rejected:
redacting titles while showing ids; omitting confidential rows by default — an undiscoverable id
would keep blocking `forget` and `set-sensitivity` on exactly the most delicate objects. This
MUST become a named spec scenario carrying this rationale, never an implicit default.

**2. Approach: a dedicated verb (decided).** New `@app.command()` copying `duplicates`'s
skeleton, backed by a new single-pass enumerator. Do not extend `status` (rejected in the issue
itself; `status` already carries four walks per #195). Do not reuse `survey_bundle` (count-only;
carrying ids would be a breaking signature change). **`list` MUST perform exactly one bundle
walk** — a named spec constraint, since #195 and #216 both targeted this class of waste. In
particular `list` MUST NOT call `lifecycle.deprecated_concept_ids`: the same walk collects both
`status` frontmatter and `supersedes` edges, so effective-deprecated is derived in-pass.

**A. Type vocabulary: `link_dir` (lowercase plural), with `REGISTRY.name` as an alias.**
Primary is `link_dir` (`people`, `sources`), because every printed id literally begins with that
segment — `list people` and `people/jane-doe` teach each other, and the issue's own examples use
it. `set-volatility`'s PascalCase is not a counter-precedent: its argument becomes a config key
(`type_tiers[Person]`), a different job. The loser is not rejected outright: `REGISTRY.name` is
accepted as a case-sensitive alias resolving to the same type, because failing `list Person`
right after teaching `set-volatility Person` is a gratuitous error. Error messages and `--help`
enumerate only the canonical `link_dir` names, so exactly one vocabulary is taught.

**B. Default `--limit 50`, with `--all` and `--limit N`.** A local-first personal knowledge base
realistically holds hundreds to low thousands of objects — enough to scroll a terminal away, not
enough to justify cursors or paging state. The full row set is already in memory from the single
walk, so truncation is a slice and the footer (`Showing 50 of 412 — use --all`) costs nothing.
`--limit 0` is rejected as invalid; `--all` is the only unbounded path.

**C. `--json` is out of scope.** Ids are whitespace-free path-like strings on the first column,
so line output is already consumable via `cut`/`awk` — the scripting need is met without freezing
a serialization contract before we know which columns matter. This is a deferral, not a ban:
`list` deliberately does not adopt `status`'s blanket "human-readable only" requirement. Trigger
for a follow-up issue: a second consumer (MCP surface, or a script needing `sensitivity`/`status`
programmatically) appears.

**D. Deprecated and superseded objects are shown by default, marked, with no flag.** The verb's
purpose is completeness; the ids most likely to need `forget`, `set-sensitivity`, or `unmerge`
are exactly the deprecated ones, so hiding them recreates the gap being closed. `duplicates`
`--include-deprecated` is not mirrored. The merged case is unreachable: `merge` deletes the
absorbed file from disk before ledger commit (`src/openkos/bundle/merge.py:23`), so merged-away
objects cannot appear in a walk.

**E. Columns: `ID  SENSITIVITY  STATUS  TITLE`.** `ID` first — it is the payload, so `cut -f1`
and copy-paste stay trivial. No `TYPE` column: an id's first segment already IS its `link_dir`,
so a type column is pure redundancy. `SENSITIVITY` and `STATUS` are fixed-width and always
present (`active` / `deprecated`), so `| grep deprecated` works — a conditional marker would not.
`TITLE` is last because it is the only variable-width free-text field, keeping earlier columns
aligned.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/bundle/listing.py` | New | Single-pass `_iter_docs` enumerator returning ordered rows |
| `src/openkos/cli/main.py` | Modified | New `list` command (~70 lines, `duplicates` skeleton) |
| `tests/unit/bundle/test_listing.py` | New | Enumerator, single-walk assertion, malformed docs |
| `tests/unit/cli/test_list.py` | New | Filter, alias, limit/`--all`, confidential title, deprecated |
| `docs/cli.md` | Modified | New `### openkos list` section next to `### openkos status` |
| `openspec/specs/list-command/spec.md` | New | Merged on archive |

`docs/cli.md` update is required: it documents every existing verb, so omitting `list` breaks it.

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Intra-command double walk (#195/#216 class) | Med | Named spec constraint plus a test asserting one `_iter_docs` call |
| Confidential titles disclosed on a shared screen | Low | Accepted and documented; local-terminal ownership rationale in the spec |
| Two type spellings drift apart | Low | Alias resolves to one type; help/errors teach only `link_dir` |
| Branch-coverage gate (90) missed on filter/limit paths | Med | Parametrized tests per filter, limit, alias, and empty-bundle path |
| Empty bundle / unreadable docs | Low | Empty prints a friendly note, exit 0; unparseable docs listed with blank title, never raise |

## Rollback Plan

Low risk, plainly: `list` is additive, read-only, and touched by nothing else. `git revert` the
commit removes the verb. No migration, no on-disk format change, no state to unwind, no existing
verb altered, so no partial-rollback hazard. The only user-visible regression is losing the new
verb.

## Delivery Forecast

Estimate ~120 production, ~300 test, ~25 docs, ~150 spec lines — roughly 600 changed lines,
inside the 800-line review budget but above the 400 default. Single PR is the expected shape.
If tests overshoot, slice as PR1 (enumerator + unit tests) then PR2 (CLI verb + docs), stacked.

## Dependencies

None. `_iter_docs` and `REGISTRY` already exist; no new runtime dependency.

## Success Criteria

- [ ] `openkos list` prints every bundle object with id, sensitivity, status, and title.
- [ ] `openkos list people` and `openkos list Person` both filter to that type.
- [ ] Default output is bounded at 50 with a truncation footer; `--all` and `--limit N` work.
- [ ] Confidential objects appear with full titles, identical to any other row.
- [ ] Deprecated and superseded objects appear, marked `deprecated`.
- [ ] Exactly one `_iter_docs` walk per invocation, asserted by test.
- [ ] `require_workspace` failure is the only non-zero exit path.
- [ ] `uv run pytest`, ruff, and mypy strict pass; branch coverage stays at or above 90.
