# Privacy Purge Specification

## Purpose

`openkos purge <concept-id>` is the irreversible, true-erasure counterpart to
`forget`: it whole-file-expunges a concept's source `raw/<name>` and bundle
file from ALL git history (not just the working tree) via `git-filter-repo`.
It is honest whole-file erasure, including whole-history content-scrub of
`index.md` and `log.md`, of commit messages and of other concepts' historical
`relations:`, with named residuals: titles and prose (see the Whole-History
Content-Scrub requirement for exactly what is and is not scrubbed), and a
`.openkos/` directory that was committed into git history (the packaged
`.gitignore` keeps it out of git, so this occurs only when a user overrides
that). It erases the id completely; it does not claim to erase every mention
of a title. `purge` itself owns argument parsing, workspace and
configuration setup, the confirmation gate, rendering the "IRREVERSIBLE
history rewrite" disclosure from the templates the lifecycle application
service returns, byte-for-byte, and invoking `git-filter-repo`; purge-set
resolution, the fail-closed safety rails, and the disclosure content itself
are composed by the lifecycle application service.

## Non-Goals

This spec DOES define whole-history content-scrub: `purge` removes a
purge-set member's catalog bullet, log entries, and any `forget` tombstone
text from every historical commit's `bundle/index.md` and `bundle/log.md`
blobs, rewrites commit messages, removes the purged id from other concepts'
historical `relations:`, and removes any live `forget` tombstone from
`log.md`. What this spec
does not define is:

- **The committed-`.openkos` (`fts.db`) leak vector.** A prior commit of
  `.openkos/fts.db` into git history is not detected or scrubbed by `purge`.
- **A `forget --hard` alias.**

## Requirements

### Requirement: Purge Set Resolution Reuses Forget Phase A

`purge <concept-id>` MUST accept `--scope {self,source}` (default `self`) and
MUST resolve the purge set using `forget`'s existing pure Phase A: concept-id
path-safety/resolution, `--scope source` Provenance Descendant Resolution
(orphan-after-delete fixed point), and reference-aware detection, unchanged.
`purge` MUST NOT take `forget`'s retire path ("Retiring A Superseded Source
Detaches Its Historical References" in `forget-command`): a `supersedes`
relation or a generated provenance entry or `## Related` bullet that refers
to a purge-set Source remains an external reference that blocks `purge`
exactly as before, and `purge` MUST NOT rewrite any document outside the
purge set.

#### Scenario: Self scope purge set is one concept
- GIVEN `openkos purge <concept-id>` with no `--scope` flag
- WHEN Phase A resolves the purge set
- THEN it contains exactly `<concept-id>`

#### Scenario: Source scope cascades to orphaned descendants
- GIVEN Source X and a concept C with `provenance: [X]` only
- WHEN `openkos purge X --scope source` runs
- THEN the purge set contains X and C, and X's `raw/<name>` plus both
  `bundle/<id>.md` paths are targeted for history expunge — C, a derived
  concept with no `resource` of its own, contributes no raw path

#### Scenario: Purge does not take the retire path
- GIVEN `sources/v2` has `supersedes -> sources/v1`
- WHEN `openkos purge sources/v1 --scope source` runs
- THEN the `supersedes` relation is an external reference that refuses the
  purge as before, and no document outside the purge set is rewritten
### Requirement: Fail-Closed Safety Rails Run In Fixed Order Before Any Write

`purge` MUST evaluate the following rails in this exact order and refuse
(exit non-zero, write nothing, no partial rewrite) at the FIRST rail that
fails: (1) reference-aware refusal — any surviving inbound reference or
unverifiable referrer outside the purge set, unless `--force`, the refusal
naming which concept holds each reference and whether it is a link or a
typed relation (with its type); (2) `git` or
`git-filter-repo` is not available; (3) workspace is not a git repository, or
the workspace root is not the git repository root; (4) the working tree is
dirty (uncommitted changes); (5) the local repo has commits present on ANY
configured remote; (6) the typed confirmation phrase does not match exactly.
No rail after the first failing one MUST be evaluated, and no history rewrite
or index deletion MUST begin until ALL six rails pass. (Tool availability is
checked at rail 2 — immediately after the reference-aware gate — because it
is the cheapest, most deterministic remaining check and carries no repo-state
assumption; the git-root/dirty-tree/remote-state rails run after it.)

#### Scenario: Reference-aware refusal blocks first
- GIVEN a concept outside the purge set holds a reference to a purge-set
  member, and the workspace is otherwise git-clean
- WHEN `openkos purge <concept-id>` runs without `--force`
- THEN it refuses at rail 1, exits non-zero, and writes nothing

#### Scenario: Missing git-filter-repo refuses with an install message
- GIVEN `git-filter-repo` is not installed (or `git` is unavailable)
- WHEN `openkos purge <concept-id>` runs
- THEN it refuses at rail 2, exits non-zero, and prints a clear install
  remediation, writing nothing

#### Scenario: Not a git repository refuses
- GIVEN the workspace root is not itself a git repository root
- WHEN `openkos purge <concept-id>` runs
- THEN it refuses at rail 3, exits non-zero, and writes nothing

#### Scenario: Dirty working tree refuses
- GIVEN the git working tree has uncommitted changes
- WHEN `openkos purge <concept-id>` runs
- THEN it refuses at rail 4, exits non-zero, and writes nothing

#### Scenario: Commits present on a remote refuse
- GIVEN the local branch has commits already present on a configured remote
- WHEN `openkos purge <concept-id>` runs
- THEN it refuses at rail 5, exits non-zero, and writes nothing, citing
  published history as the reason

#### Scenario: Typed confirmation mismatch aborts with no write
- GIVEN all prior rails pass and the user is prompted for a typed
  confirmation phrase
- WHEN the entered text does not match the required phrase exactly (a bare
  `y`/`yes` MUST NOT satisfy it)
- THEN `purge` aborts, exits non-zero, and no rewrite or index deletion
  occurs

#### Scenario: All rails pass, rewrite proceeds
- GIVEN no external references, a clean tree, a local-only repo (no
  matching remote commits), `git`/`git-filter-repo` available, and an
  exact typed-phrase match
- WHEN `openkos purge <concept-id>` runs
- THEN the history rewrite begins

### Requirement: Preview Names Every Target And Any Absence Of Raw Material

Before rail 1, `purge` MUST print to stdout a preview naming EVERY path the
rewrite would expunge — each purge-set member's `bundle/<id>.md`, each
resolved `raw/<name>`, each ledger sidecar and each decision sidecar — plus
one warning line per member whose `resource` frontmatter is absent or fails
validation, and, under `--scope source`, the total purge-set count. When the
purge set resolves NO raw source path at all, the preview MUST state that
absence in words rather than leaving it to be inferred from a shorter list.
The preview is the LAST thing shown before rail 6's typed confirmation
phrase, so it — not `--help` — is what an operator acts on, and the one
distinction deciding whether the source material survives (a Source with a
resolvable `resource`, or a derived concept) MUST be legible there.

#### Scenario: Preview lists every expunge target before any rail runs
- GIVEN a purge set whose members resolve raw paths, bundle files and
  sidecars
- WHEN `openkos purge <concept-id>` runs
- THEN every one of those paths is printed to stdout before rail 1 is
  evaluated

#### Scenario: A purge set resolving no raw source path says so
- GIVEN `openkos purge <concept-id>` where no purge-set member contributes a
  raw source path — for example a derived concept, which carries no
  `resource` at all
- WHEN the preview is printed
- THEN it states that no raw source material is part of this purge

#### Scenario: The absence is not stated when a raw path resolves
- GIVEN `openkos purge <concept-id>` on a Source whose `resource` validates
- WHEN the preview is printed
- THEN it lists that `raw/<name>` as a target and does NOT state an absence
  of raw source material

### Requirement: Whole-History Expunge Via git-filter-repo

Once all rails pass, `purge` MUST invoke `git-filter-repo` to remove, from
ALL git history and the working tree, every purge-set member's source
`raw/<name>` (resolved from that source's `resource: raw/<name>` frontmatter)
and every purge-set member's `bundle/<id>.md`.

#### Scenario: Self-scope purge removes raw and bundle files from history
- GIVEN a successful self-scope purge of concept-id `<id>` sourced from
  `raw/<name>`
- WHEN the rewrite completes
- THEN neither `raw/<name>` nor `bundle/<id>.md` appears in `git rev-list
  --objects --all`, reflog, or `git cat-file` output

#### Scenario: Source-scope cascade removes all purge-set files from history
- GIVEN a successful `--scope source` purge whose set contains a source and
  two descendant concepts
- WHEN the rewrite completes
- THEN the source's `raw/<name>` and all three `bundle/<id>.md` files are
  absent from git history

### Requirement: Whole-History Expunge Covers The Ledger Sidecar Store

`purge`'s `git-filter-repo` rewrite MUST include, in the SAME single pass
as the existing whole-file expunge, every purge-set member's content
preserved under `bundle/.state/ledger/`: a survivor's own sidecar file (if
the purge-set member is a survivor) and any OTHER survivor's sidecar entry
whose snapshot fields embed the purge-set member's body or id (if the
purge-set member was absorbed by a prior merge). This coverage MUST NOT
introduce a second `git-filter-repo` invocation.

`purge`'s live-tree half of this sweep is the SAME shared primitive
`forget`'s Phase B calls, and therefore carries the same
reference-scrubbing obligation: a purge-set member that was
merely REFERENCED from a surviving sidecar — a `## Related` link, a
catalog bullet, a log line, each carrying its title and former path — MUST
be dropped from every snapshot field, not only members whose absorbed
BODY is embedded there. `purge` is irreversible and cannot be re-run to
correct a partial erasure, so this MUST be verified as a rail against the
real rewrite, not on the primitive alone.

#### Scenario: Purging a referenced concept scrubs it from a surviving sidecar
- GIVEN a concept is referenced as an ordinary bullet inside another
  survivor's ledger snapshot, without ever having been absorbed by it
- WHEN `openkos purge <concept-id>` completes
- THEN that survivor's sidecar remains, and neither the purged concept's
  title nor its former path appears anywhere in it

#### Scenario: Purging a merge survivor removes its ledger sidecar from history
- GIVEN a successful purge of a concept-id that is a merge survivor with a
  ledger sidecar under `bundle/.state/ledger/`
- WHEN the rewrite completes
- THEN that sidecar file is absent from `git rev-list --objects --all`,
  reflog, and `git cat-file` output, alongside the concept's own bundle
  file

#### Scenario: Purging a previously-absorbed concept removes its snapshot from another survivor's sidecar
- GIVEN a concept was absorbed by an earlier merge and its pre-merge body
  is preserved as an `absorbed_snapshot` in a different survivor's ledger
  sidecar
- WHEN `openkos purge <absorbed-concept-id>` completes
- THEN that snapshot's embedded body no longer appears in any commit's
  blob of the survivor's sidecar, and the rewrite ran in the same
  `git-filter-repo` pass as the concept's own file expunge

> **UNIMPLEMENTED, and UNREACHABLE under the current command surface
> ** This scenario is not satisfied and is recorded here rather than
> left silent: an unimplemented privacy scenario that reads as implemented
> is worse than a tracked gap.
>
> **What is implemented.** `purge` expunges each purge-set member's OWN
> sidecar (`bundle_ledger.ledger_path_for(member, ...)`, `cli/main.py`).
> A cross-survivor `absorbed_snapshot` — the absorbed concept's body living
> inside a DIFFERENT survivor's sidecar — is not a whole-file expunge
> target and is not reached.
>
> **Why it is not a live leak.** The scenario's own precondition is
> unreachable: `openkos purge <absorbed-concept-id>` cannot run. `purge`
> resolves its root id through `_resolve_concept_path`, which refuses with
> `ValueError` when `<canonical_id>.md` does not exist, and an absorbed
> concept's file is gone once the merge completes. No supported command can
> put the system in the state this scenario describes.
>
> **What would make it reachable.** Any verb that learns to address an
> ABSORBED id — that is, any change relaxing or bypassing
> `_resolve_concept_path`'s existence gate for a ledger-known id. The
> concrete candidate is `unmerge --to <id>`, which reads the same
> ledger. Whoever implements that MUST close this gap in the same change,
> or the existence gate stops being the thing that makes this safe.

#### Scenario: Unrelated sidecar entries are untouched
- GIVEN a survivor's ledger sidecar holds entries for both the purge-set
  member and an unrelated concept
- WHEN the purge completes
- THEN the unrelated entry remains byte-identical in every historical
  commit

### Requirement: Whole-History Expunge Covers The Pending-Work Decision Subtree

`purge`'s `git-filter-repo` rewrite MUST include, in the SAME single pass as
the existing whole-file expunge, every `bundle/.state/**` decision path
(contradiction decline/re-open) that references a purge-set member's
concept id, either as a member of the decision's proposal identity or as
the concept the decision was recorded against. This coverage MUST NOT
introduce a second `git-filter-repo` invocation, and follows the same
INCLUDE-walk pattern ADR-0013 established for the merge-ledger sidecar.

#### Scenario: Purging a concept removes its decision from history

- GIVEN a `bundle/.state/**` decision file references a concept id that is
  subsequently purged
- WHEN the purge rewrite completes
- THEN no historical commit's blob of that decision path contains the
  purged concept's id, verified by `git rev-list --objects --all` and `git
  cat-file`, and the rewrite ran in the same `git-filter-repo` pass as the
  concept's own file expunge

#### Scenario: An unrelated decision entry is untouched

- GIVEN a decision path references a concept unrelated to the purge set
- WHEN the purge completes
- THEN that decision's content remains byte-identical in every historical
  commit

### Requirement: Index Cleanup Is Delete-And-Rebuild, No Tombstone

After a successful rewrite, `purge` MUST delete
`.openkos/{fts,vectors,graph}.db` (not row-level `DELETE`, since SQLite's
freelist can retain deleted content) and MUST rebuild `fts.db` and `graph.db`
from the post-rewrite bundle state. `purge` MUST NOT rebuild `vectors.db` —
re-embedding requires a running Ollama embedder, a dependency `purge` must
never require; `vectors.db` stays deleted for the next `openkos reindex` to
lazily re-embed. `purge` MUST NOT write any `log.md` tombstone entry.

#### Scenario: Index files are deleted; fts.db and graph.db are rebuilt
- GIVEN a successful purge
- WHEN index cleanup runs
- THEN `.openkos/fts.db`, `.openkos/vectors.db`, and `.openkos/graph.db` are
  each deleted, `.openkos/fts.db` and `.openkos/graph.db` are replaced with
  freshly rebuilt files, and `.openkos/vectors.db` stays deleted (no Ollama
  dependency introduced)

#### Scenario: No tombstone is written
- GIVEN a successful purge of any scope
- WHEN `log.md` is inspected afterward
- THEN it contains no new tombstone entry for the purged concept(s)

### Requirement: A Failed Store Delete Is Reported As An Incomplete Erasure

The delete IS the erasure — the reason above for unlinking rather than
issuing a row-level `DELETE` is that freelist pages would otherwise remain
recoverable. When a store's `unlink` fails, pre-purge content is therefore
still on disk, and `purge` MUST NOT report a completed erasure: it MUST exit
non-zero, MUST name the full path of every store it could not delete, and
MUST state that removing those files is what finishes the erasure. It MUST
NOT present `openkos reindex` as the remedy for the residue — a reindex
rebuilds index content and removes no residual pages.

This covers the rebuilt stores (`fts.db`, `graph.db`) as well as the dropped
ones: rebuilding content over a file that was never unlinked leaves the
pre-purge pages equally recoverable, even though nothing was lost. A REBUILD
failure remains non-fatal (it is a convenience over the survivors) — only a
failed DELETE is an erasure gap.

#### Scenario: A store that could not be deleted fails the run
- GIVEN a purge whose history rewrite succeeded
- WHEN any derived store's `unlink` raises
- THEN `purge` still prints its expunge summary, additionally prints an
  incomplete-erasure report naming that store's full path and the removal
  that finishes the erasure, and exits non-zero

#### Scenario: A failed rebuild still succeeds
- GIVEN a purge whose deletes all succeeded
- WHEN the in-line `fts.db` or `graph.db` rebuild raises
- THEN the failure is reported and `purge` exits zero

### Requirement: Whole-History Content-Scrub Of index.md And log.md

After a successful rewrite, `purge` MUST content-scrub `bundle/index.md` and
`bundle/log.md` across ALL git history (every past commit's blob of exactly
these two files) by removing, as FULL LINE removals, each purge-set member's
catalog bullet, log entries, and any `forget` tombstone referencing it.
Matching MUST use markdown link-identity (the same `_link_identity` used
elsewhere), never a bare id-substring match. In `index.md` a bullet matches
on its FIRST link (its own concept). In `log.md` a line matches when ANY of
its markdown links resolves to a purge-set member, or its `(id: <x>)` anchor
equals one, because a `**Relate**`/`**Unrelate**` line names the relation's
target as a later link. Dropping the whole line is intended: it also removes
the audit line of a SURVIVING concept that merely links to the purged one
(for example `**Ingest**: Extracted [Derived] ... from [Purged Source]`),
because privacy takes precedence over keeping that line. A line none of whose
links resolves to a purge-set member — including a surviving sibling
concept's catalog bullet or a log entry that merely mentions the purged id in
prose — MUST be left byte-identical in every commit. The scrub MUST run in the
SAME single `git-filter-repo` pass as the whole-file expunge (no second
rewrite).

In that same pass `purge` MUST ALSO:

- **Rewrite commit messages.** Replace every occurrence of a purge-set
  member's id in every commit message with `[purged]`, leaving the rest of
  the message. An id matches only on its own boundaries (never inside a
  longer id such as `concepts/bee-keeper`); an id with no directory part
  matches only as the final segment of a path.
- **Scrub other concepts' historical `relations:`.** In every historical
  version of every other `bundle/**.md` document (never `bundle/.state/`),
  remove each `relations:` entry whose `target` is a purge-set member; a list
  left empty loses its `relations:` key. Only the frontmatter `relations:`
  block is touched. This applies only when the purge found no surviving
  inbound reference. Under `--force` past a live reference the references are
  left dangling by contract, and since the rewrite would also change the
  current version, no `relations:` is rewritten.

What is NOT scrubbed, and so what the claim "no trace" does and does not
cover: a purge-set member's TITLE is removed only where a `log.md` line
linking to it is dropped; titles are not removed from commit messages, from
other concepts' bodies or prose in any version, or from `provenance`/
`sources` frontmatter. Body links or text in other concepts' history that
name the purged id are likewise left (a surviving concept's body MUST NOT be
scrubbed even if it contains the purged id or title).

#### Scenario: Purged concept is gone from index.md and log.md history
- GIVEN a successful purge of concept `<id>`
- WHEN every commit's `bundle/index.md` and `bundle/log.md` blobs are
  inspected after the rewrite
- THEN `<id>` appears in no commit's blob of either file, and neither does
  `<title>` where it appeared only as link text on a dropped line

#### Scenario: Relate and Unrelate lines naming the target are dropped
- GIVEN `log.md` holds `**Relate**` and `**Unrelate**` lines from concept A to
  the purged concept B, as B's id is their second link
- WHEN B is purged
- THEN neither line remains in the live `log.md` or in any historical blob

#### Scenario: Surviving sibling and prose mention round-trip unchanged
- GIVEN a purge-set member's catalog bullet exists alongside a surviving
  sibling concept's catalog bullet in `index.md`, and a `log.md` entry that
  mentions the purge-set member's id only in prose (not as a link)
- WHEN the history scrub runs
- THEN the sibling's catalog bullet and the prose-mention log entry are
  byte-identical, in every historical commit, to their pre-purge content

#### Scenario: Commit messages are redacted on id boundaries
- GIVEN commits whose messages read `relate a -> <id> (depends_on)`,
  `note <id>-keeper` and `note my<id>`
- WHEN `<id>` is purged
- THEN the first reads `relate a -> [purged] (depends_on)` and the other two
  are unchanged

#### Scenario: Other concepts' historical relations lose the target
- GIVEN concept A once held `relations:` entries to the purged B and to C
- WHEN B is purged with no surviving inbound reference
- THEN every historical version of A parses, keeps its entry to C, and has no
  entry to B

#### Scenario: --force leaves the live relation alone
- GIVEN concept A still holds a `relations:` entry to B and `--force` is
  passed
- WHEN B is purged
- THEN A's `relations:` entry to B is unchanged, as the documented dangling
  reference

#### Scenario: Scrub leaves bundle bodies alone
- GIVEN a surviving concept's bundle body contains the purged id or title in
  its own text
- WHEN the history scrub runs
- THEN that body is unchanged in every commit

### Requirement: Live log.md Tombstone Cleanup

After a successful rewrite, `purge` MUST remove any LIVE `bundle/log.md`
`forget` tombstone entry referencing a purge-set member, via a new
`remove_log_entry` function mirroring `remove_index_entry`'s live-index
cleanup, matched by link-identity on ANY link of the line (as in the history
scrub), so live `**Relate**`/`**Unrelate**` lines naming a member go too.

#### Scenario: Prior forget tombstone removed from live log.md
- GIVEN a concept was previously `forget`-ed (leaving a tombstone in the
  live `log.md`) and is now purged
- WHEN the purge completes
- THEN the live `log.md` no longer contains a tombstone entry for that
  concept's id

### Requirement: Live Index Cleanup After Successful Purge

After a successful rewrite, `purge` MUST remove the LIVE `index.md` catalog
bullet for every purge-set member (reusing `forget`'s own
`remove_index_entry`/write path), so the live catalog never keeps a bullet
pointing at a concept absent from every commit. `purge` MUST NOT print any
warning stating that purged content remains in `index.md`/`log.md` history,
because the whole-history content-scrub requirement (above) removes it: after
a successful purge, the purged id MUST NOT appear in `index.md` or
`log.md`, in any commit message, or in other concepts' `relations:`, in any
commit, live or historical (titles: see the Whole-History Content-Scrub
requirement for the exact limits).

#### Scenario: Live index bullet is removed
- GIVEN a successful purge of any scope
- WHEN the command completes
- THEN `index.md` no longer contains a catalog bullet for any purge-set
  member

#### Scenario: No residual warning is printed
- GIVEN a successful purge of any scope
- WHEN the command completes
- THEN stdout does NOT contain any warning stating that purged content
  remains in `index.md`/`log.md` history, because no such residual exists

### Requirement: Deferred-Reembed Warning On Success

Because a successful purge deletes `.openkos/vectors.db` without rebuilding
it (per the Index Cleanup requirement), `purge`'s success output MUST
include a warning stating that dense retrieval is degraded until the index
is rebuilt, and MUST instruct the user to run `openkos reindex`. This MUST
be message-only: `purge` MUST NOT prompt interactively and MUST NOT
auto-run `reindex` itself.

That warning MUST also disclose what the rebuild costs (a full re-embed).
`vectors.db` holds BOTH the `vector_meta` content-hash cache and the `meta`
embedding-model tag, so dropping the file drops both: the restore is a FULL
re-embed of every surviving document, one embedding call each (more precisely, one
embedding call per CHUNK, per the `embedding-chunking` capability), never
an incremental top-up. Because the tag lived in the dropped store, the
NEXT `openkos reindex` run finds NO stored tag at all and takes reindex's
corrected disclosure's "no embedding-model tag stored (fresh or dropped
store)" branch — it MUST NOT take the "embedding model changed" branch,
because there is no old tag left to compare against a new one. The warning
MUST pre-empt THAT exact wording, naming it explicitly, so an operator
reading reindex's next output does not mistake the absent-tag disclosure
for a configuration change they did not make.

Preserving the model tag alone would NOT make the rebuild incremental, and
MUST NOT be offered as if it would: the vectors themselves are gone, so
every document must be embedded again whatever the tag says. Only carrying
the SURVIVORS' vectors into a fresh database would deliver incrementality,
which changes the delete-and-rebuild erasure posture above and is out of
scope for this requirement.

#### Scenario: Successful purge warns about degraded dense retrieval

- GIVEN a successful `openkos purge <concept-id>` run
- WHEN the command prints its success output
- THEN the output includes a warning that dense retrieval is degraded, an
  instruction to run `openkos reindex`, the fact that the restore is a full
  re-embed, and states that the next `reindex` run will report no stored
  embedding-model tag — NOT a model change — because `purge` dropped the
  store that held it

#### Scenario: No interactive prompt or auto-reindex occurs

- GIVEN a successful purge that deleted `.openkos/vectors.db`
- WHEN the command completes
- THEN `purge` does not prompt for confirmation to reindex and does not
  invoke `reindex` itself

#### Scenario: Purge's pre-emptive quoting matches reindex's corrected wording

- GIVEN a successful purge whose warning pre-empts the next `reindex` run's
  disclosure
- WHEN that warning text is inspected
- THEN it quotes or paraphrases the "no embedding-model tag stored (fresh
  or dropped store)" wording, and does NOT quote the retired
  `embedding model changed (unset -> <model>)` wording

### Requirement: Post-Rewrite Live-Tree Auto-Commit

After a successful purge completes its live-tree cleanup (removal of the
live `index.md` catalog bullet and any live `log.md` tombstone for every
purge-set member), `purge` MUST commit the resulting live-tree state via
the shared `_autocommit(root, paths, message)` helper, staging
`bundle/index.md` and `bundle/log.md`, with commit message `openkos: purge 1
concept` (or `openkos: purge N concepts` when the purge set holds N members).
The message MUST NOT name any purged id, since this commit survives the
purge. This commit MUST run strictly after the live-tree
cleanup and MUST leave the working tree clean.

The commit step MUST be non-fatal: a git failure (workspace not a git
working tree, missing identity, or `commit_paths` raising `GitError`/
`OSError`) MUST emit a non-fatal WARNING to stderr and MUST NOT change
`purge`'s exit code, because the already-irreversible history rewrite and
index cleanup have already landed. The commit step MUST also tolerate an
empty diff: when `git-filter-repo`'s rewrite already left the live tree
identical to what `_autocommit` would stage (no pending changes to
`index.md`/`log.md`), `purge` MUST still succeed with no error.

#### Scenario: Successful purge leaves a clean working tree via commit

- GIVEN a purge that has passed all six safety rails, completed the
  history rewrite, and finished live-tree cleanup
- WHEN the commit step runs
- THEN exactly one commit exists with message `openkos: purge <id>`
  (or the `(+N)` cascaded form), containing `bundle/index.md` and
  `bundle/log.md`, and `git status` reports a clean tree

#### Scenario: Commit failure does not fail the already-irreversible purge

- GIVEN a purge that has completed its history rewrite and index cleanup,
  where the commit step raises `GitError` or `OSError`
- WHEN the commit step runs
- THEN a non-fatal WARNING is printed to stderr, and `purge` still exits
  with its normal success code

#### Scenario: Empty diff after filter-repo's own rewrite still succeeds

- GIVEN a purge where `git-filter-repo`'s rewrite already left
  `index.md`/`log.md` identical to the state `_autocommit` would stage (no
  pending changes to commit)
- WHEN the commit step runs
- THEN `purge` completes successfully with no error, whether or not a new
  commit was created

### Requirement: Irreversibility — No Rollback After Rewrite Begins

`purge` MUST NOT create a pre-purge backup, and MUST NOT provide any
rollback mechanism once the `git-filter-repo` rewrite has started. The
entirety of the safety model MUST occur before the rewrite begins: all six
rails from the ordered-rails requirement MUST pass before ANY write (rewrite
or index deletion) occurs, and once started, the operation runs to
completion or leaves a state requiring manual git-level recovery, not an
automated undo.

#### Scenario: No backup file is created
- GIVEN any purge invocation, successful or refused
- WHEN the command completes
- THEN no backup of the purged content is written anywhere by `purge`

#### Scenario: No rail evaluation occurs after the rewrite starts
- GIVEN a purge that has passed all six rails and begun the rewrite
- WHEN the rewrite is in progress
- THEN `purge` performs no further refusal check and offers no abort path

### Requirement: Every Store Left Deleted Is Named, With Its Own Restore Cost

`purge` deletes five derived stores and rebuilds two, so `vectors.db`,
`findings.db` and `insight_questions.db` are all left deleted. The success
output MUST name EVERY store left deleted and state what restoring it
costs. It MUST NOT name the stores it rebuilds in-line, which would send an
operator to restore something already back.

The disclosure MUST derive its list, and its count, from the same structure
the delete path and the sidecar sweep use, with each store's cost carried
BESIDE its path rather than in a separate table keyed on filename — so a
store added to the delete set cannot be omitted from the disclosure, arrive
with no cost, or disagree with the stated count.

A store MUST be reported as dropped only when it is ACTUALLY gone. `unlink`
failures are warned about rather than raised, so a notice built from the
intended list would announce a store as dropped while it is still on disk,
sending the operator to pay for a restore of something they still have. That drift is what produced the
defect: the delete loop grew from two stores to five while the warning kept
naming one, and two stores holding paid-for model work were destroyed in
silence — in the reported session, 11 persisted contradiction verdicts, 9
edge suggestions and 7 identity adjudications, minutes after
`contradictions` reported `11 of 11 candidate(s) served from persisted
findings; 0 judged fresh`.

The costs MUST be stated per store, because one shared "run `openkos
reindex`" line misprices two of the three:

- `vectors.db` — a full re-embed, under the Deferred-Reembed requirement
  above, which continues to govern its wording.
- `findings.db` — persisted contradiction verdicts, identity adjudications
  and edge-typing suggestions, and the pending-work queue. Each verdict is
  recomputable only by paying its model call again on the next
  `contradictions`, `adjudicate` or `suggest-relations` run. The queue holds
  every open review row for every concept, not only the purged ones, and
  `openkos pending` reports it as not computed until `openkos daemon --once`
  rebuilds it; the line MUST say so. `openkos reindex` restores none of them.
- `insight_questions.db` — cached question embeddings for `query --save`'s
  near-duplicate scan. Free, and nothing needs to be run: a miss re-embeds
  on the next save.

The notice MUST also state what happens to the operator's own rulings, and
the statement MUST be QUALIFIED. All three `findings.db` tenants hold
MACHINE-computed verdicts, while a `--decline` or `--keep-distinct` ruling
is written under the bundle's decision subtree and committed with the
bundle, so a ruling on a concept OUTSIDE the purge set is untouched by the
store drop. A ruling that named a purge-set member is expunged in the same
rewrite pass, per the Whole-History Expunge requirement above, and the
notice MUST say so: an unqualified promise that rulings survive would read
as the erasure having missed something.

An operator who read that `findings.db` was destroyed and inferred their
rulings went with it would re-enter decisions that are still on disk.

#### Scenario: The notice names all three dropped stores

- GIVEN a successful purge
- WHEN the command prints its success output
- THEN the output names `vectors.db`, `findings.db` and
  `insight_questions.db`

#### Scenario: The notice does not name the rebuilt stores

- GIVEN a successful purge that rebuilt the lexical and graph indexes
- WHEN the command prints its success output
- THEN the output names neither `fts.db` nor `graph.db`

#### Scenario: Each dropped store carries its own cost

- GIVEN a successful purge
- WHEN the notice is inspected
- THEN the vectors line instructs an `openkos reindex`, the findings line
  names the verbs that must re-judge, and the question-cache line states
  that restoring it is free

#### Scenario: Operator rulings are reported as surviving, with the limit named

- GIVEN a workspace holding a recorded `--keep-distinct` ruling on a concept
  outside the purge set
- WHEN a purge completes
- THEN the ruling is still readable from the bundle, and the notice states
  that a ruling outside the purge set survives while one naming a purged
  concept was expunged with it

#### Scenario: A store whose delete failed is not reported as dropped

- GIVEN a purge in which one dropped store's `unlink` raises
- WHEN the notice is printed
- THEN that store is absent from the list, and the stated count matches the
  stores actually gone

### Requirement: Dropped Stores Leave No Orphan Sidecars

For each store `purge` leaves deleted, it MUST also remove that store's
`-wal` and `-shm` sidecars. It MUST NOT remove the sidecars of a store it
rebuilds in-line, which belong to a live database.

This is hygiene, not erasure, and MUST NOT be described as erasure: the WAL
measured in the reported run was 0 bytes, so no data residue survived in it.
What survived was a sidecar pair with no database, which makes the engine
cache directory misreport what still exists.

#### Scenario: A dropped store's sidecars go with it

- GIVEN a workspace whose dropped stores each have `-wal`/`-shm` sidecars
- WHEN a purge completes
- THEN neither sidecar remains for any dropped store

#### Scenario: The sweep is scoped to the dropped stores

- GIVEN the same workspace
- WHEN a purge completes
- THEN the sidecar sweep ran for the dropped stores and for no other store

A rebuilt store's `-wal`/`-shm` are NOT asserted to survive: the rebuild
reopens each store in WAL mode and SQLite removes its own sidecars on a
clean close, so their absence afterwards says nothing about `purge`. What
must hold is that `purge`'s own sweep never reaches them.

#### Scenario: A store whose delete failed keeps its sidecars

- GIVEN a purge in which one dropped store's `unlink` raises
- WHEN the purge completes
- THEN that store's `-wal` and `-shm` are still present

A sidecar sweep over a store that is still on disk is worse than leaving
litter: a `-wal` holds committed pages not yet checkpointed back, so
removing it out from under a live database can destroy data the purge was
never asked to touch.
### Requirement: Purge Withdraws The Deprecated-Status Export Of Resurrected Targets

Because `purge` resolves its purge set through `forget`'s Phase A, it MUST
compute the same deprecated-status export withdrawals `forget` does
(`forget-command` "Resurrection Interaction Disclosure"): for every concept
outside the purge set whose superseded-ness changes because the purge
set's edges disappear, the export projection is evaluated over the
post-purge bundle, and the preview names each resulting status change
before the typed confirmation. After the history rewrite, `purge` MUST
write each WITHDRAW or DROP-MARKER outcome as part of its live-tree
cleanup, and MUST stage every rewritten concept document in the SAME
post-rewrite auto-commit as `bundle/index.md` and `bundle/log.md`. A
failure writing an export rewrite MUST be reported as a non-fatal WARNING
naming the concept and `openkos repair`, and MUST NOT change `purge`'s
exit code, for the same reason the commit step is non-fatal: the
irreversible rewrite has already landed, and export drift never changes
retrieval (`deprecated-status-export`).

#### Scenario: A purged superseder's target is withdrawn in the same commit

- GIVEN purge-set member M holds the only `supersedes` edge to Y outside
  the set, and Y carries a valid export
- WHEN `openkos purge M` completes
- THEN Y carries `status: stable` and no `status_derived_from` key, and
  the post-rewrite commit contains `bundle/Y.md` beside `bundle/index.md`
  and `bundle/log.md`

#### Scenario: A failed withdrawal does not fail the purge

- GIVEN a purge whose history rewrite succeeded and whose write of Y's
  withdrawn export raises `OSError`
- WHEN the live-tree cleanup runs
- THEN a non-fatal WARNING names Y and `openkos repair`, and `purge` exits
  with its normal success code

### Requirement: Purge Removes The Workspace's Unattended Records And Logs

`openkos purge` MUST delete `.openkos/jobs.db` with the other stores it
deletes and name it among them, and MUST delete the workspace's daemon log
files in the per-user log directory (the files named by the sha256 of the
workspace's real path, including rotated ones). A log file it cannot delete
MUST be named in the incomplete-erasure report with its path and the manual
remedy, as for a store.

The `jobs.db` line MUST state that it also holds the inbox watch's
observation history, so the next watch pass re-checks every file in the
inbox, and name `openkos daemon --once` as the way to restart it.

#### Scenario: A purge leaves no daemon log naming the purged concept

- GIVEN a daemon log for the workspace that names `<id>`
- WHEN `openkos purge <id>` completes
- THEN no daemon log file for the workspace remains

#### Scenario: An undeletable log is reported

- GIVEN a daemon log file that cannot be deleted
- WHEN `openkos purge <id>` completes
- THEN the report names that file and the manual remedy
