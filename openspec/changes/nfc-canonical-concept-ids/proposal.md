# Proposal: NFC is the canonical spelling of a concept id

**Issue**: [#430](https://github.com/jasonssdev/openkos/issues/430) (P3).
**Baseline**: `main` @ `8a90b98`. **Mode**: openspec.

## Intent

The same logical concept id can be spelled two ways on disk. APFS preserves the
normalization it is handed but looks names up normalization-insensitively; HFS+
rewrites filenames to NFD on write; SMB varies. So a bundle can hold a document
whose filename is NFD while every `relations:` target naming it is NFC — and a
plain string comparison between the two fails. Every consequence is silent:

- graph edges are dropped, because an edge target string matches no node id;
- `lint` reports orphans and dangling links that are not real;
- entity-resolution candidates are never nominated for the same reason.

This was unreachable while slugs were ASCII (ASCII has no distinct decomposed
form). #429 widened slugs to Unicode and made it reachable. #453 (part 1)
consolidated the id derivation into the single `okf.concept_id_for` helper at
all eleven read sites, deliberately **without** normalizing, so the decision
could be made once. This change makes that decision.

## Decision

**NFC is the canonical spelling of a concept id, and of the filename openkos
writes for it.** This is canonical by construction, not by preference:
`_slugify` has emitted NFC explicitly since #414. What was missing is the read
side honoring it.

Two obligations follow, and both are in scope:

1. **Id derivation normalizes.** `okf.concept_id_for` NFC-normalizes the
   bundle-relative path it derives, so an id derived from an NFD filename
   compares equal to the same id spelled NFC anywhere else.
2. **Path reconstruction tolerates the old spelling.** Nine sites rebuild
   `bundle_dir / f"{id}.md"` from an id that is now canonically NFC, and the
   name on disk may still be decomposed on a byte-exact filesystem (a bundle
   authored on HFS+, committed, and cloned onto ext4 carries NFD filenames
   openkos never wrote). A new `okf.concept_path_for` probes the direct path
   first and, only on a miss for a non-ASCII id, scans the parent directory
   for a name that NFC-normalizes to the same id — admitting regular files
   only, never symlinks. Without this, normalizing ids would trade a
   comparison bug for a silent-content bug: `_load_doc`-style callers degrade
   to an empty body and hand the model nothing to judge.

## Scope

### In scope

1. `okf.concept_id_for` NFC-normalizes its result (canonical layer).
2. `okf.concept_path_for` — the tolerant inverse, with its two guards
   (symlink fail-closed, ASCII scan skip).
3. The nine reconstruction sites route through it:
   `cli/main.py:_resolve_concept_path` (the path-safety gate `merge`/`forget`
   resolve through), `cli/curate.py` Structure stage,
   `resolution/contradiction.py` (`_load_doc`, `_load_ledger_bodies`),
   `resolution/adjudication.py:_load_members`,
   `resolution/edge_typing.py:_load_doc`,
   `retrieval/answer.py:_assemble_context`.

### Out of scope (follow-up issue)

- **Rename migration.** Reads now survive either spelling, but a bundle
  carrying decomposed filenames is still inconsistent with the canonical
  spelling. Renaming a user's files is consequential and stays human-reviewed
  (AGENTS.md: human curates, engine maintains) — a `lint` finding that
  *detects* decomposed on-disk names, plus whatever rename tooling follows,
  is its own change.
- Ingest-side behavior: unchanged. `_slugify` already emits NFC (#414/#429).

## Rollback plan

`git revert` of the single PR. No data migration is involved — the change
never writes, renames, or rewrites a bundle file, so rolling back only
restores the previous (spelling-sensitive) comparison behavior. On a volume
whose filenames are all NFC (every bundle openkos itself wrote, on any
filesystem), normalization is a byte-level no-op and the revert is invisible.
