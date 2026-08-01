# Exploration: source-title-backfill

**Issue**: [#298](https://github.com/jasonssdev/openkos/issues/298) — backfill content-derived titles onto Sources ingested before #248
**Baseline**: `main` @ `61b50ce`
**Mode**: hybrid (OpenSpec + Engram `sdd/source-title-backfill/explore`, observation 2245)

## Current State

`source-title-from-heading` (#248, archived 2026-07-31) made `openkos ingest` derive a Source's `title` from its raw content via `source_title.derive_source_title` (`src/openkos/cli/main.py:1738-1743`), but explicitly deferred backfilling existing Sources. The archived report states the deferral verbatim: "No backfill of existing bundles... A backfill is a full-document regeneration... plus an unanswered question about whether historical `log.md` link labels are retroactively rewritten." Issue #298 is that follow-up.

A Source's title reaches four places, all fed by one assignment at ingest time:

| Consumer | Location |
| --- | --- |
| Frontmatter `title:` | `src/openkos/model/okf.py:195` |
| The document's own `# {title}` H1 | `src/openkos/model/okf.py:217` |
| `index.md` bullet label | `src/openkos/bundle/index.py:102` |
| `log.md` entry label | written at ingest |

## Corrected Mechanical "Never Curated" Test

Issue #298 proposes `title == _titleize(slug)` as the test for a title no human has curated. **That test is wrong.**

`_slugify(stem)` lowercases and collapses `[^a-z0-9]+` (`main.py:1070-1080`). `_titleize(stem)` only maps `[-_]+` to a space and strips — no lowercasing (`main.py:1083-1085`). The original assignment is `_titleize(src.stem)`, not `_titleize(slug)`.

Counterexample:

```
01-Introduction.md
  slug            = _slugify("01-Introduction") = "01-introduction"
  stored title    = _titleize("01-Introduction") = "01 Introduction"
  _titleize(slug) = "01 introduction"            ← lowercase, does not match
```

A genuinely mechanical title is therefore misclassified as curated and silently skipped.

The correct test is:

```python
title == _titleize(Path(resource).stem)
```

Since `resource = f"raw/{name}"` and `name = src.name`, `Path(resource).stem == src.stem` holds algebraically, by construction. `okf.py:145-158` documents that `resource` preserves the original basename verbatim, and `purge`'s containment check depends on that correspondence — independent confirmation that the identity is load-bearing rather than incidental.

**Where the test cannot be applied**: a Source whose `resource` is absent or malformed. `purge` (`main.py:2791-2821`) is the exact precedent — warn, never refuse, and contribute nothing for that member.

## The Three Open Questions

### 1. `log.md` history — recommendation: do NOT rewrite

`bundle/log.py` exposes only `insert_log_entry` (append) and `remove_log_entry` (delete by identity). No update-in-place primitive exists anywhere in the codebase for `log.md` or `index.md`; `update_index_entry`, `retitle`, and `rename_` all return zero grep hits across `src/`.

The only historical content-scrub precedent is `purge`'s `git filter-repo` erasure (`main.py:2672-2718`) — right-to-be-forgotten for a concept that no longer exists. It does not transfer to a still-existing, merely relabeled concept.

The issue author's own lean ("a log is a record... rewriting it to look tidy erodes trust in it later") matches this codebase's only real precedent. Recommendation stands: history keeps the label it was written with; only `index.md` and the document move forward.

### 2. Scope — recommendation: bundle-wide only, no `<concept-id>` argument

`backfill-sensitivity` (`main.py:3590-3597`) is bundle-wide by design: it closes the gap left by documents created before a feature existed, and the single-Source case is already covered by another verb. The same logic applies here — re-running `openkos ingest` on a byte-identical file already regenerates one Source and picks up the new title.

Reuse `backfill-sensitivity`'s preview/confirm/`--auto` shape exactly.

### 3. Curated titles — recommendation: skip, and say so in the preview

Skip rule, in order:

1. Read `resource`. Absent or malformed → warn and skip (`purge` precedent).
2. Compute `_titleize(Path(resource).stem)`. If the on-disk title differs → presumed curated, skip.
3. Otherwise re-derive from `raw/<name>` via `derive_source_title`. Stage only if the result is non-`None` and differs from the current title.

The preview needs **three buckets**, not the stage-or-nothing shape `backfill-sensitivity` uses: staged, skipped-as-curated, warned-malformed-resource.

The companion lint check is separate follow-up work, matching #248's own "What Is Still Owed" list.

## Write Set, Slug Safety, Derived Indexes

**Per backfilled Source, exactly two byte-level changes**: the frontmatter `title:` value, and the document body's literal first line `# {old_title}` (`okf.py:217`). `description` never embeds the title — confirmed by reading the exact f-strings at `main.py:1745-1754` — and stays untouched. The embedded `## Source content` section stays untouched.

Recommend a **surgical patch** (`load_frontmatter` → mutate the `title` key → replace only the first body line → `dump_frontmatter`) over a full `build_source_concept` regeneration, because backfill does not have the original `<path>` argument needed to reconstruct `description`.

**`index.md` needs a new bullet-label-update primitive.** None exists today — only insert and remove. This is the single piece of this change with no precedent to copy.

**`log.md` needs no new primitive** (history untouched, per question 1).

**The slug and concept id never change.** The slug derives from the filename, not the title. Inbound markdown links and typed relations resolve by id, so they are structurally unaffected. This materially lowers the risk profile and should be stated explicitly in the proposal.

**`.openkos/{fts,vectors,graph}.db`**: follow `backfill-sensitivity` exactly — leave them untouched. `reindex` is the sole, always-manual writer. Only `purge` deletes and rebuilds them, for leak-prevention reasons that do not apply to a relabel.

## Reuse From `backfill-sensitivity`

Reusable verbatim: Phase A read-only snapshot, empty-result short circuit, preview, confirm-gate precedence (`--auto` / `cfg.review` / TTY confirm / non-TTY refuse), Phase B per-file atomic writes with a `landed` accumulator, one `log.md` entry, one `_autocommit`.

Must diverge:

- The resolver re-reads `raw/<name>` — a new I/O shape, passed as text into a pure core, absent from `resolve_backfill_raises`.
- Two write targets per item (the Source file plus the new `index.md` primitive) instead of one.
- A three-bucket preview instead of stage-or-nothing.

## Test Surface

Models to follow: `tests/unit/bundle/test_resolve_backfill_raises.py` and `tests/unit/cli/test_backfill_sensitivity.py`. Use the shared `tests/unit/cli/conftest.py` workspace-snapshot helpers added in #281 to prove that `raw/*` and historical `log.md` bytes are untouched.

Estimated 20-28 new test cases.

## Size Estimate

Roughly 650-900 changed lines: new pure resolver 60-100, new `index.py` primitive 30-50 plus its own tests, CLI command 120-160, tests 400-550.

This likely exceeds the 800-line single-PR review budget once tests are counted. `sdd-tasks` should plan at least a 2-PR chain, similar to #248's own multi-PR delivery.

## Risks For The Proposal Phase

1. The `index.md` bullet-update primitive is genuinely new, with no code precedent to copy — a sizing risk for `sdd-design`.
2. The body first-line patch MUST assert that `# {old_title}` matches exactly before overwriting, or it can silently corrupt a hand-edited document.
3. The 650-900 line estimate likely needs a chained-PR delivery plan against the 800-line budget.
