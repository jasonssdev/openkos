# Name Normalization Specification

## Purpose

`openkos normalize-names` is openkos's dedicated mutating verb for fixing
every on-disk name under `bundle_dir` that fails NFC (Unicode Normalization
Form C) — the condition `openkos lint`'s `non-nfc-name` finding reports but
does not remediate. It follows the standard Phase A -> confirm gate -> Phase
B -> `_autocommit` shape shared by other mutating verbs (e.g.
`backfill-sensitivity`), and reuses lint's own scan so the verb and `lint`
never disagree about what counts as an offending entry. (Merged from
change `nfc-rename-migration`, PR #492, closing #474.)

## Non-Goals

- Transliteration or ASCII-folding of names (rejected in #414); only the
  normalization FORM changes, never the characters a name contains.
- NFKC or any compatibility normalization; NFC only.
- Renaming anything outside `bundle_dir` (no `raw/`-adjacent, workspace-level,
  or `.openkos/` path is ever renamed; `raw/` stays immutable).
- Rewriting concept ids, `relations:` targets, `provenance:` references,
  `index.md` links, or any file body content.
- Triggering a reindex or marking any derived store stale.
- `next_action` integration (out of scope; follow-up issue).
- Dirty-tree refusal (this verb writes and scoped-commits like every mutating
  verb except `purge`).
- Git history rewriting (`purge`-only).
- `lint --fix` (violates `lint`'s pinned read-only, non-gating contract).

## Requirements

### Requirement: Phase A Scan Reuses Lint's Non-NFC Detection

`normalize-names` MUST obtain its candidate set for `bundle_dir` from the
SAME scan definition `openkos lint`'s `non-nfc-name` finding uses (the
raw-path-and-name-carrying scan, not `LintFinding`'s NFC-normalized
projection), so there is exactly one definition of "offending entry" shared
by both commands.

#### Scenario: Verb's candidate set matches lint's findings

- GIVEN a bundle containing decomposed (NFD) directory and file names
- WHEN `openkos normalize-names` builds its Phase A plan
- THEN the set of entries the plan proposes to rename is exactly the set of
  entries `openkos lint` reports under `non-nfc-name`

### Requirement: Phase A Plan Classifies Every Offending Entry As Rename Or Skip

For each offending entry, Phase A MUST classify it as either a planned
rename (raw name -> NFC target) or a skip carrying a reason. An entry MUST
be skipped, never renamed, when: (a) an entry already exists on disk at the
NFC target spelling (collision); (b) the entry is a symlink; or (c) the
entry no longer exists at the immediate pre-Phase-B drift re-check. Skips
MUST be non-fatal: the run proceeds with the remaining planned renames.

#### Scenario: Collision with an existing NFC sibling is skipped

- GIVEN an offending entry whose NFC-spelled sibling already exists on disk
- WHEN Phase A classifies it
- THEN it is reported as a skip with a collision reason, and it is not
  renamed, overwritten, or merged

#### Scenario: A symlink is skipped

- GIVEN an offending entry that is a symlink
- WHEN Phase A classifies it
- THEN it is reported as a skip with a symlink reason, and it is never
  followed or renamed

#### Scenario: An entry that vanished before Phase B is skipped, not a crash

- GIVEN an offending entry planned for rename in Phase A, which is then
  deleted or moved by a concurrent process before Phase B applies it
- WHEN the immediate pre-Phase-B drift re-check runs
- THEN that entry is reported as a skip, the run does not crash, and the
  remaining planned renames still proceed

#### Scenario: A run with only skips writes nothing

- GIVEN a Phase A plan whose every offending entry is classified as a skip
- WHEN the verb runs to completion
- THEN no file is renamed, no `log.md` entry is written, and no commit is
  created

### Requirement: Preview Names Every Planned Rename And Skip, Decomposed Directories As One Entry

Before the confirm gate, `normalize-names` MUST render a preview
enumerating every planned rename (raw spelling -> NFC target) and every
skip with its reason. A decomposed directory whose subtree also contains
offending descendants MUST be previewed as ONE entry that states its
subtree moves with it, never as one preview line per descendant.

#### Scenario: A decomposed directory previews as a single entry

- GIVEN a decomposed on-disk directory containing offending descendant
  files
- WHEN the preview renders
- THEN the directory appears as one entry stating that its subtree moves
  with it, not as one line per descendant file

#### Scenario: Preview lists both renames and skips before any write

- GIVEN a Phase A plan containing at least one planned rename and at least
  one skip
- WHEN the preview renders
- THEN both the planned rename and the skip (with its reason) are listed,
  and no write has yet occurred

### Requirement: Confirm Gate Follows The Standard Ladder

`normalize-names` MUST resolve whether to proceed using, in order: (1)
`--auto` skips the prompt and proceeds; (2) else, config `review: false`
skips the prompt and proceeds; (3) else, in a TTY, `typer.confirm` asks
and, on decline, aborts with exit code 1 and no write; (4) else (non-TTY
without `--auto`), the verb refuses to write, tells the user to re-run with
`--auto`, and exits non-zero. Immediately before Phase B, the plan MUST be
re-validated against current on-disk state (drift re-check).

#### Scenario: --auto skips the prompt

- GIVEN `openkos normalize-names --auto` with a plan containing renames
- WHEN the verb runs
- THEN no confirmation prompt is shown and Phase B proceeds

#### Scenario: review:false config skips the prompt

- GIVEN a workspace config with `review: false` and no `--auto` flag
- WHEN `openkos normalize-names` runs
- THEN no confirmation prompt is shown and Phase B proceeds

#### Scenario: TTY decline aborts with nothing written

- GIVEN an interactive TTY, no `--auto`, and `review` not set to `false`
- WHEN the user declines the confirmation prompt
- THEN the verb exits with code 1, and no rename, `log.md` entry, or
  commit occurs

#### Scenario: Non-TTY without --auto refuses to write

- GIVEN a non-interactive (non-TTY) invocation without `--auto`
- WHEN `openkos normalize-names` runs
- THEN it writes nothing, tells the user to re-run with `--auto`, and
  exits non-zero

### Requirement: Phase B Applies Renames Deepest-First

Phase B MUST apply planned renames in order of decreasing path depth, so
every descendant is renamed before its ancestor. A parent directory MUST
NOT be renamed before any of its still-pending descendant renames.

#### Scenario: A child file renames before its decomposed parent directory

- GIVEN a Phase A plan containing a decomposed parent directory and a
  decomposed child file inside it
- WHEN Phase B applies the plan
- THEN the child file is renamed before the parent directory

### Requirement: Each Rename Is Two-Step Via A Unique Temporary Sibling, Verified By Byte-Exact Directory Listing

For each planned rename, Phase B MUST rename the entry from its raw
on-disk name to a unique temporary sibling name, then from the temporary
name to the NFC target name (never a single direct rename from raw to NFC
target). After the second rename, Phase B MUST verify the result by
reading the parent directory's listing and confirming the NFC target name
is present byte-exactly (and the raw/temporary name is absent). Verifying
via existence-testing the target path alone (e.g. an `exists()`-style
check) MUST NOT be used as this proof: such a check can report the NFC
spelling present when only the original, differently-encoded name is
actually on disk, which is exactly the silent-success failure mode this
requirement exists to rule out. If the byte-exact listing verification
fails, the rename MUST fail loudly (reported to the user; the run does not
silently continue as if it succeeded) and MUST leave no temporary-named
entry behind on disk.

The two-step primitive itself MUST reliably convert a raw name to its NFC
target even against a filesystem/rename implementation that treats
canonically equivalent names as the same file (a normalization-insensitive
`rename`, under which a single direct rename could silently no-op and
leave the original spelling on disk). This property is independent of
which real filesystem the verb happens to run on: a filesystem where a
one-step rename already changes the on-disk spelling satisfies it
trivially, and a filesystem where a one-step rename would no-op is exactly
the case the two-step scheme protects against.

#### Scenario: A successful rename passes through a temporary sibling

- GIVEN a planned rename of an offending entry
- WHEN Phase B applies it
- THEN the entry is renamed to a unique temporary sibling name first, then
  to the NFC target name, and the parent directory's listing afterward
  contains the NFC target name byte-exactly and does not contain the raw
  or temporary name

#### Scenario: Failed post-rename verification fails loudly and leaves no temp entry

- GIVEN a rename whose post-rename directory-listing check does not find
  the NFC target name present byte-exactly
- WHEN Phase B detects this
- THEN it reports the failure loudly to the user and no entry with the
  temporary name remains on disk afterward

#### Scenario: The two-step primitive converts a name even against a normalization-insensitive rename

- GIVEN the underlying rename primitive is one under which two canonically
  equivalent names are treated as the same file (so a single direct rename
  from the raw name to the NFC target could leave the original spelling on
  disk unchanged)
- WHEN Phase B applies the two-step temporary-sibling scheme to a planned
  rename
- THEN the resulting directory listing contains the NFC target name
  byte-exactly, unlike what a single direct rename would have left behind
  under the same primitive

#### Scenario: On a filesystem where a one-step rename already changes the on-disk spelling, the byte-exact result still holds
- GIVEN a real filesystem on which a single direct rename from the raw
  name to the NFC target actually changes the on-disk bytes (no no-op)
- WHEN Phase B applies the two-step scheme anyway
- THEN the parent directory's listing afterward still contains the NFC
  target name byte-exactly and does not contain the raw or temporary name
  — the two-step scheme is never observably worse than a one-step rename
  on such a filesystem

### Requirement: One `log.md` Entry Per Run; `index.md` Untouched

A completed run that renamed at least one entry MUST append exactly one
`log.md` entry summarizing the run's batch (counts of renamed and skipped
entries), not one entry per renamed path. `index.md` MUST NOT be modified
by this verb. No concept id, `relations:` target, `provenance:` reference,
or file body content MAY change as a result of this verb.

#### Scenario: A run with renames writes exactly one log entry

- GIVEN a run that successfully renames three entries and skips one
- WHEN the run completes
- THEN `log.md` gains exactly one new entry stating the renamed and
  skipped counts, and no other bundle content changes

#### Scenario: index.md is never modified

- GIVEN any run of `openkos normalize-names`, successful or partial
- WHEN the run completes
- THEN `index.md` is byte-identical to its state before the run

### Requirement: Scoped, Best-Effort, Non-Fatal Autocommit

After a run that performed at least one rename, `normalize-names` MUST
invoke `_autocommit` with its staging scope set to exactly the changed
`log.md` file plus, for every renamed entry, both its old and new path.
This requirement governs the paths PASSED to the commit step, not the
diff git ultimately records for them: on a git configuration/filesystem
pairing where git already recognized the old and new spellings as the
same content (for example, `core.precomposeunicode=true` normalizing a
decomposed name to NFC when it first records the path), staging both
paths can legitimately produce no change for git to see, and that is not
a defect — `log.md`'s entry is always a real, non-empty staged change, so
the commit is never empty and never needs to be skipped. If the workspace
is not a git repository, has no configured git identity, or the commit
otherwise raises a `GitError`, the verb MUST print a non-fatal stderr
warning and MUST NOT change its exit code; renames already applied to
disk remain in place.

#### Scenario: Autocommit's staging scope includes both old and new path per rename

- GIVEN a successful run that renamed two entries
- WHEN `_autocommit` is invoked
- THEN its staging scope names each entry's old path and new path, plus
  `log.md`, and names no unrelated path

#### Scenario: On a byte-exact filesystem, the commit records a delete and an add per rename

- GIVEN a successful run that renamed two entries on a filesystem/git
  configuration where git already saw the old, differently-spelled path
- WHEN the commit is inspected
- THEN it contains a delete of each old path and an add of each new path,
  plus the `log.md` change, and no unrelated dirty content

#### Scenario: A run where git recorded no spelling change still commits successfully with no warning

- GIVEN a successful run whose renamed old and new paths were already
  recorded identically by git before the run (for example, because
  `core.precomposeunicode=true` had already normalized the on-disk name to
  NFC when git first saw it), so the renamed paths contribute nothing to
  the diff
- WHEN `_autocommit` runs
- THEN the run still commits successfully (the `log.md` entry gives the
  commit real content), the exit code is unaffected, and no warning is
  printed for the renamed paths contributing no diff

#### Scenario: Non-repo workspace warns but does not fail the run

- GIVEN a workspace that is not a git repository
- WHEN a successful rename run completes
- THEN a non-fatal warning is printed to stderr, the exit code is
  unchanged, and the renamed files remain on disk

#### Scenario: Missing git identity warns but does not fail the run

- GIVEN a git repository with no configured user identity
- WHEN a successful rename run attempts to commit
- THEN a non-fatal warning is printed to stderr, the exit code is
  unchanged, and the renamed files remain on disk

### Requirement: Idempotency — A Second Run Plans, Writes, And Commits Nothing

Running `openkos normalize-names` again immediately after a successful run
MUST produce a Phase A plan with zero renames for the entries the prior
run already renamed, MUST perform no write, and MUST create no commit.

#### Scenario: Immediate re-run is a no-op

- GIVEN a bundle that was fully normalized by a prior successful run
- WHEN `openkos normalize-names` runs again immediately afterward
- THEN Phase A's plan contains zero renames, no file is written, and no
  commit is created

#### Scenario: Re-run after partial success only plans the remaining entries

- GIVEN a prior run that renamed some entries and left others as
  unresolved collisions
- WHEN `openkos normalize-names` runs again
- THEN the new plan contains zero renames for the already-normalized
  entries and still reports the unresolved collisions as skips

### Requirement: No Reindex Chaining

`normalize-names` MUST NOT trigger a reindex and MUST NOT cause
`stale_derived_stores` or `next_action`'s stale-derived-index tier to
report staleness, because renames change neither concept ids nor file
content bytes.

#### Scenario: A successful run marks no derived store stale

- GIVEN a successful rename run
- WHEN derived-store staleness is checked afterward
- THEN no derived store is reported stale as a result of the run, and no
  reindex is triggered by the verb
