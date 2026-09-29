# Delta for Entity-Resolution Merge

## ADDED Requirements

### Requirement: Merge Metadata's Generation-Time Rule Reads `generated.at` With Legacy `timestamp` Fallback

`merge_metadata`'s most-recent-wins rule for a concept's generation time
MUST resolve each side's generation time through the shared generation-time
helper (see `okf-format-migration`'s "Generation-Time Resolution With
Legacy `timestamp` Fallback") — reading `generated.at` when present,
falling back to a legacy `timestamp` value when `generated` is absent —
rather than reading either key directly, and MUST write the resolved,
more-recent value into the merged document's `generated` field, regardless
of which field either side originally carried it in.

#### Scenario: A v0.2 survivor merges with a legacy-timestamped absorbed object

- GIVEN a survivor carrying `generated: { at: <later> }` and an absorbed
  object carrying only a legacy `timestamp: <earlier>`
- WHEN `merge <survivor> <absorbed>` runs
- THEN the merged document carries `generated: { at: <later> }`, the
  survivor's own value, resolved via the shared helper on both sides

#### Scenario: A legacy-timestamped survivor absorbs a more recent v0.2 object

- GIVEN a survivor carrying only a legacy `timestamp: <earlier>` and an
  absorbed object carrying `generated: { at: <later> }`
- WHEN `merge <survivor> <absorbed>` runs
- THEN the merged document carries `generated: { at: <later> }` — the
  absorbed object's more recent value — even though the survivor itself
  was legacy-shaped before the merge

## MODIFIED Requirements

### Requirement: The Stacked Form Keeps One Document Root

The APPEND stacks the absorbed body under a
`## Merged content (<absorbed-id>)` delimiter. The absorbed body carries
its own `# ` title heading, so appending it verbatim produced a merged
document with TWO level-1 headings — two document roots in one file
(issue #803).

The absorbed body's LEADING `# ` heading MUST therefore be demoted to
`### ` before it is stacked. The delimiter directly above already names
the absorbed document, so that heading is both the redundant one and the
one creating the second root; `### ` nests it under the level-2 delimiter.
The demotion MUST fail closed, exactly as the reconciled-body heading pin
does: only an exact leading `# ` ATX heading is rewritten, and a body that
opens with prose is stacked unchanged — this demotes a heading that
exists, it never invents or relocates one.

The absorbed document's DEEPER sections MUST be left verbatim. Folding
them requires section-merging semantics this engine does not provide
(deduping `## Related` bullets, renumbering `[N]` citation markers), and
shifting them byte-wise would silently change meaning; `# Citations` is a
legacy OKF v0.1 body convention, superseded by frontmatter `sources` (OKF
v0.2 §13.1), that MAY still appear — hand-authored or not yet migrated — on
an absorbed document, and MUST NOT be demoted blind. Reconciliation is
what folds two documents into one; this requirement only stops the
unreconciled fallback from asserting two roots.
(Previously: described `# Citations` as "an OKF §8 RESERVED heading"; OKF
v0.2 retires that heading in favor of frontmatter `sources` and no longer
reserves it, though a legacy or hand-authored section may still exist and
is still never demoted blind.)

The demotion is PRESENTATION-ONLY and MUST NOT affect reversibility.
`unmerge` restores from the ledger's verbatim pre-merge snapshots, and
every other consumer of a merged body locates the absorbed segment by the
`## Merged content (` marker rather than by the absorbed body's bytes.

#### Scenario: The absorbed leading heading is demoted
- GIVEN an absorbed body opening with a `# ` heading
- WHEN the merged body is built
- THEN that heading is stacked as `### ` and the merged body carries
  exactly one level-1 heading, the survivor's

#### Scenario: Deeper absorbed sections are stacked verbatim
- GIVEN an absorbed body carrying a `## Related` section and a
  `# Citations` section below its title heading
- WHEN the merged body is built
- THEN both are present unchanged, at their original heading levels

#### Scenario: A heading-less absorbed body is stacked unchanged
- GIVEN an absorbed body that opens with prose
- WHEN the merged body is built
- THEN the absorbed text is stacked byte-identically, with no heading
  invented

#### Scenario: A demoted stack still unmerges to byte parity
- GIVEN a merge whose absorbed body carried a demoted leading heading
- WHEN `unmerge` reverses it
- THEN both documents are restored byte-for-byte from the ledger snapshots

### Requirement: Repair Verb Refuses On Any Sign Of Cross-Survivor Pollution Risk (Slice 1b)

The migration/repair verb (extracting a pre-fix, frontmatter-embedded
`merged_from` history into a `bundle/.state/ledger/` sidecar) MUST refuse
the WHOLE run (exit non-zero, write nothing) whenever the run has any
pre-relocation, frontmatter-embedded ledger to extract (`scan_unmigrated`
is non-empty) AND any survivor in the bundle — migrated or unmigrated —
carries 2 or more merge-ledger entries, rather than running the doctor
merge-ledger-integrity check (Check B, nested-prefix equality) per concept
and refusing only the flagged ones. When the run has nothing to extract
(`scan_unmigrated` is empty), this gate is NOT evaluated, and the OKF v0.1
→ v0.2 migration (see `okf-format-migration`) proceeds for that bundle
regardless of how many merge-ledger entries any survivor's
already-relocated sidecar carries. This bundle-wide, entry-count gate is
deliberately COARSER than Check B: Check B has two honest false negatives
it cannot see past — a single-entry ledger has nothing nested to compare,
and cross-survivor pollution is invisible at any index, because
`merge_core`'s `other_files` scan touches every non-reserved bundle file,
so a merge of X into Y can rewrite bytes inside a THIRD survivor Z's
embedded snapshot without Z's own ledger ever showing a nested-prefix
mismatch. A per-concept, Check-B-only gate would let exactly that
corruption through; the bundle-wide ≥2-entries gate does not, at the cost
of also refusing some concepts Check B alone would have cleared. A
mechanical verbatim migration of a corrupted, already-mutated ledger would
convert a git-revertible bug into permanent durable fact, so this refusal
has no override flag of any kind when it applies. The refusal message MUST
state that the only path forward is `git reset --hard <first-merge>~1`
followed by `openkos reindex`, and that reversibility of merges made
before this fix is not guaranteed.
(Previously: the ≥2-entries gate was evaluated, and could refuse the whole
run, on every repair-verb invocation regardless of whether it had any
pre-relocation ledger left to extract; the gate is now scoped to runs that
have ledger extraction to do, so a bundle whose ledgers are already
relocated to sidecars — including a twice-merged survivor's sidecar — can
still be OKF v0.1 → v0.2 migrated.)

#### Scenario: Repair verb migrates a clean ledger verbatim
- GIVEN a bundle where no survivor (migrated or unmigrated) carries 2 or
  more merge-ledger entries, and a concept has an embedded `merged_from`
  history
- WHEN the repair verb runs
- THEN that concept's entries are extracted verbatim into a new
  `bundle/.state/ledger/` sidecar and the frontmatter `merged_from` key is
  removed

#### Scenario: Repair verb refuses the whole run when extraction meets the pollution-risk gate
- GIVEN a bundle with at least one pre-relocation, frontmatter-embedded
  `merged_from` history to extract, and any survivor in the bundle —
  migrated or unmigrated — carries 2 or more merge-ledger entries
- WHEN the repair verb runs
- THEN it refuses the ENTIRE run, exits non-zero, writes nothing for any
  concept, and states the reset-and-replay path is the only remedy — no
  `--force` or equivalent flag bypasses this refusal, even for concepts
  whose own ledger Check B alone would have cleared

#### Scenario: A sidecar-only bundle with 2 or more entries proceeds to OKF migration
- GIVEN a bundle with no pre-relocation, frontmatter-embedded ledger left to
  extract, and a survivor whose already-relocated sidecar carries 2 or more
  merge-ledger entries (for example, a twice-merged survivor)
- WHEN the repair verb runs
- THEN the pollution-risk gate is NOT evaluated, and the OKF v0.1 → v0.2
  migration proceeds for that bundle
