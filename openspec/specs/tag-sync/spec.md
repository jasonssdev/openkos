# Tag Sync Specification

## Purpose

`openkos sync-tags` is the human-invoked verb that adds a Source's current
`tags` to the derived concepts that Source grounds. `ingest` gives a
derived concept its Source's tags only when both are written in the same
run (ingestion: "Derived Object Provenance and Sensitivity Inheritance"),
so a Source whose tags change later drifts apart from what it produced.
Following the precedent ADR-0009 set for sensitivity, closing that drift is
an explicit, reviewable write — never a side effect of re-ingest.

The verb is additive by construction (ADR-0033): it unions the Source's
tags into each descendant's `tags` and never removes one. It runs no LLM,
touches only the `tags` field of the concepts it stages, and lands in one
preview, one confirmation, one `log.md` entry, and one commit.

## Non-Goals

- **Removing a tag downstream.** A tag removed from the Source stays on
  every derived concept that already carries it. Without per-tag
  provenance, a descendant's tag cannot be told apart from one a human
  added by hand, so any removal rule risks deleting human curation
  (ADR-0033).
- **Writing the Source.** The Source is read, never written. Its tags come
  only from its own incoming frontmatter and from human edits.
- **Multi-Source descendants outside every single Source's closure.** The
  write set is `find_provenance_descendants`' conservative subset closure,
  the same set `set-sensitivity` propagates to. A concept citing two
  different Sources belongs to neither closure and is not tagged.
- **Automatic sync on `ingest`.** `ingest` only advises the verb (ingestion:
  "Converged Re-Ingest Source-Only Rewrite").
- **A `--dry-run` flag.** The preview shown before confirmation, or
  declining the prompt, already serves as the dry run.
- **Any other frontmatter field.** Sensitivity propagation stays with
  `set-sensitivity` and `backfill-sensitivity`.

## Requirements

### Requirement: Target Selection Is One Source Or Every Source

`openkos sync-tags` MUST accept exactly one of: a positional `<source-id>`
(a bundle-relative concept id, path minus `.md`), or the `--all` flag.
Passing both, or neither, MUST refuse with exit 1 before any bundle read,
naming the two accepted forms on stderr.

A `<source-id>` MUST be resolved through the same concept-id resolution
the other id-taking write verbs use, rejecting an absolute id, any `..`
segment, a reserved basename, or a nonexistent concept file with exit 1
and no write. A resolved concept whose frontmatter `type` is not `Source`
MUST refuse with exit 1, naming the concept and stating that `sync-tags`
takes a Source. Source detection MUST read the `type` field, never a path
convention.

With `--all`, every concept in the bundle whose `type` is `Source` is an
independent root, processed as if `sync-tags` had been run on each one,
with every addition folded into a single preview, write, log entry, and
commit.

#### Scenario: Both a Source id and --all refuse

- GIVEN a workspace with a Source `sources/notes`
- WHEN `openkos sync-tags sources/notes --all` runs
- THEN it exits 1, stderr names the two accepted forms, and no file
  changes

#### Scenario: Neither a Source id nor --all refuses

- GIVEN a workspace
- WHEN `openkos sync-tags` runs with no argument
- THEN it exits 1 and no file changes

#### Scenario: A non-Source target refuses

- GIVEN a concept `concepts/alpha` whose `type` is `Concept`
- WHEN `openkos sync-tags concepts/alpha` runs
- THEN it exits 1, stderr states that `sync-tags` takes a Source, and no
  file changes

#### Scenario: An unsafe id refuses before any write

- GIVEN a workspace
- WHEN `openkos sync-tags ../outside` runs
- THEN it exits 1 and no file changes

### Requirement: The Write Set Is The Source's Provenance Closure, Minus Sources

For each root Source, the candidate set MUST be the Source's provenance
closure computed by `bundle.provenance.find_provenance_descendants` with the
Source as the only root, excluding the Source itself. A candidate whose
`type` is `Source` MUST NOT be written: a Source's tags come only from its
own incoming frontmatter and from human edits. The bundle snapshot MUST be
whole-bundle (reserved files excluded), so that an id existing anywhere in
the bundle resolves.

#### Scenario: A single-Source descendant is a candidate

- GIVEN a Source `sources/notes` and a concept `concepts/alpha` whose
  `provenance` is `[sources/notes]`
- WHEN `openkos sync-tags sources/notes` computes its plan
- THEN `concepts/alpha` is a candidate

#### Scenario: A descendant reached through an intermediate concept is a candidate

- GIVEN a Source `sources/notes`, a concept `concepts/alpha` citing only
  `sources/notes`, and a concept `concepts/beta` citing only
  `concepts/alpha`
- WHEN `openkos sync-tags sources/notes` computes its plan
- THEN both `concepts/alpha` and `concepts/beta` are candidates

#### Scenario: A concept citing two Sources is not a candidate of either

- GIVEN Sources `sources/a` and `sources/b`, and a concept `concepts/joint`
  whose `provenance` is `[sources/a, sources/b]`
- WHEN `openkos sync-tags sources/a` computes its plan
- THEN `concepts/joint` is not a candidate and its file is byte-unchanged

### Requirement: Union Only, Never Remove

For each candidate, the added tags MUST be the Source's current tags —
normalized through `okf.normalize_tags` — that the candidate does not
already carry, by exact string equality. The candidate's new `tags` MUST be
`okf.union_tags(existing, source_tags)`: every existing tag first, in its
stored order and unchanged, then each added tag in the Source's order. No
existing tag MAY be removed, reordered, or rewritten. A candidate whose
union adds nothing MUST NOT be staged. Only the `tags` field MAY change in
a staged file; every other frontmatter key and the body MUST be preserved
through the engine's normal frontmatter re-render.

A Source whose normalized tags are empty contributes nothing. When no
candidate is staged, the run MUST print that there is nothing to sync and
exit 0 with no write and no commit.

#### Scenario: Missing tags are appended after the existing ones

- GIVEN a Source with `tags: [alpha, beta]` and a descendant with
  `tags: [gamma, alpha]`
- WHEN `openkos sync-tags <source-id> --auto` runs
- THEN the descendant's `tags` is exactly `[gamma, alpha, beta]`

#### Scenario: A hand-added tag survives

- GIVEN a descendant carrying a tag `reviewed` that its Source does not
  carry
- WHEN `openkos sync-tags <source-id> --auto` runs
- THEN the descendant still carries `reviewed`

#### Scenario: A tag removed from the Source is kept downstream

- GIVEN a descendant carrying `alpha`, and its Source whose `alpha` tag has
  since been removed by hand
- WHEN `openkos sync-tags <source-id> --auto` runs
- THEN the descendant still carries `alpha`

#### Scenario: A descendant already carrying every tag is not staged

- GIVEN a Source with `tags: [alpha]` and its only descendant already
  carrying `alpha`
- WHEN `openkos sync-tags <source-id>` runs
- THEN it reports nothing to sync, exits 0, writes nothing, and makes no
  commit

#### Scenario: The run is idempotent

- GIVEN a `sync-tags` run that just staged and wrote additions
- WHEN the same command runs again on the unchanged bundle
- THEN it reports nothing to sync and writes nothing

#### Scenario: Only the tags field changes

- GIVEN a descendant with a body, a `sensitivity`, a `provenance`, and a
  `title`
- WHEN `sync-tags` adds a tag to it
- THEN its body, `sensitivity`, `provenance`, and `title` are unchanged,
  and only `tags` differs

### Requirement: A Descendant With A Malformed Tags Value Is Never Rewritten

A candidate whose `tags` value is absent or null MUST be treated as having
no tags. A candidate whose `tags` value is present but is not a list whose
every item is a string (a bare string, a mapping, a number, or a list
holding a non-string item) MUST NOT be staged: the run MUST print one
stderr WARNING naming the candidate and the reason, and MUST continue with
the other candidates. Rewriting such a value would discard what a human
wrote.

#### Scenario: A mapping-valued tags field is skipped with a warning

- GIVEN a descendant whose `tags` is a mapping
- WHEN `openkos sync-tags <source-id> --auto` runs
- THEN that descendant's file is byte-unchanged, stderr names it in a
  WARNING, and every other eligible descendant is still written

#### Scenario: An absent tags field gains the Source's tags

- GIVEN a descendant with no `tags` key, and a Source with `tags: [alpha]`
- WHEN `openkos sync-tags <source-id> --auto` runs
- THEN the descendant's `tags` is exactly `[alpha]`

### Requirement: A Descendant Below The Source's Sensitivity Is Not Tagged

A Source's tags are text derived from the Source, so they carry its
sensitivity. A candidate whose `sensitivity` ranks strictly below the
Source's — `okf.sensitivity_direction(candidate_sensitivity,
source_level) == "raise"`, where `source_level` is the Source's own value
ranked fail-closed through `okf.combine_sensitivity` — MUST NOT be staged.
The run MUST print one stderr note per such candidate naming it and
`openkos set-sensitivity` (or `openkos backfill-sensitivity`) as the way to
raise it first. A candidate with a missing or unrecognized `sensitivity`
ranks fail-closed and is therefore never below the Source.

#### Scenario: A confidential Source's tags never reach a private descendant

- GIVEN a Source at `confidential` with `tags: [diagnosis]` and a
  descendant at `private`
- WHEN `openkos sync-tags <source-id> --auto` runs
- THEN the descendant's file is byte-unchanged and stderr names it and
  `openkos set-sensitivity`

#### Scenario: A descendant at the Source's level is tagged

- GIVEN a Source at `private` with `tags: [alpha]` and a descendant at
  `private`
- WHEN `openkos sync-tags <source-id> --auto` runs
- THEN the descendant carries `alpha`

### Requirement: Every Source Folds Into One Plan With --all

With `--all`, additions from every Source MUST be accumulated per
descendant so that each file is staged at most once, carrying the union of
every Source's added tags in root order (Sources sorted by concept id).
Each Source's sensitivity guard applies to its own tags only. The preview,
log entry, and commit cover the whole sweep once.

#### Scenario: Two Sources each tag their own descendants in one commit

- GIVEN Sources `sources/a` (`tags: [x]`) and `sources/b` (`tags: [y]`),
  each with one untagged descendant
- WHEN `openkos sync-tags --all --auto` runs
- THEN the descendant of `sources/a` carries `x`, the descendant of
  `sources/b` carries `y`, and exactly one commit and one `log.md` entry
  are made

### Requirement: Preview, Confirm Gate, And --auto

Before any write, the run MUST print a preview listing every staged file
as `~ bundle/<id>.md (tags added: <a>, <b>)` in concept-id order, followed
by `~ log.md (new dated entry)`. The confirm gate MUST follow the shared
precedence: `--auto` skips it; otherwise config `review: false` skips it;
otherwise an interactive TTY prompts and a decline aborts with nothing
written; otherwise (non-TTY, no `--auto`) the run refuses to write with
exit 1, naming `--auto`.

#### Scenario: The preview names each staged file and its added tags

- GIVEN a plan staging one descendant that gains `alpha` and `beta`
- WHEN the preview prints
- THEN it includes `~ bundle/<id>.md (tags added: alpha, beta)` and the
  `log.md` line

#### Scenario: Declining the prompt writes nothing

- GIVEN a plan with at least one staged descendant and an interactive TTY
- WHEN the user declines the confirmation
- THEN no file changes and no commit is made

#### Scenario: A non-TTY run without --auto refuses

- GIVEN a plan with at least one staged descendant, stdin not a TTY, and
  config `review` not `false`
- WHEN `openkos sync-tags <source-id>` runs
- THEN it exits 1, names `--auto`, and writes nothing

### Requirement: Drift Guard, Write Order, One Log Entry, One Commit

After the confirm gate — and on the runs that skip it — the run MUST
re-read every staged descendant, every root Source whose tags fed the
plan, and `log.md`, and MUST refuse the whole run with exit 3 and nothing
written when any of them changed or vanished since the plan read it. It
MUST then write every staged descendant (concept-id order), then `log.md`,
each via an atomic write, then make exactly one workspace autocommit
covering every changed path. The `log.md` entry MUST name the Source id
(with `--all`, the number of Sources that contributed) and the number of
concepts updated; the commit message MUST be `openkos: sync-tags
<source-id>` or `openkos: sync-tags --all`. Neither MAY contain any tag
value: `log.md` and commit messages are shared
history, and a tag lifted from a confidential Source must not reach them.
A write failure after the first write MUST name every path already
written; there is no cross-file rollback. A successful write MUST refresh
the derived stores once, as every other bundle-writing verb does.

#### Scenario: A descendant edited while the prompt waits refuses the run

- GIVEN a plan staging a descendant, and that descendant's file edited on
  disk after the plan was computed
- WHEN the run passes the confirm gate
- THEN it exits 3 and no file is written

#### Scenario: A Source edited while the prompt waits refuses the run

- GIVEN a plan computed from a Source's tags, and that Source's file edited
  on disk after the plan was computed
- WHEN the run passes the confirm gate
- THEN it exits 3 and no file is written

#### Scenario: One commit and one log entry carry no tag value

- GIVEN a Source with `tags: [secret-project]` staging two descendants
- WHEN `openkos sync-tags <source-id> --auto` runs
- THEN exactly one commit is made, `log.md` gains exactly one dated
  `**Sync-tags**` entry naming the Source and `2` concepts, and neither
  the entry nor the commit message contains `secret-project`
