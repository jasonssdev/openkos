# Proposal: Reference-Aware Forget + Tombstones (Gap #8 · S2a)

## Intent

`forget` is a destructive write verb that today is reference-blind: it deletes a
concept even when OTHER concepts still link to or hold typed relations toward it,
leaving those third parties with dangling references (a Known Limitation the
current spec explicitly defers). It also records only a plain `**Forget**` log
line, so a delete leaves no durable trace — contradicting `docs/knowledge-object-model.md`
L325 ("from MVP-2, deletion also leaves a tombstone in log.md"). S2a makes
`forget` reference-aware (detect + refuse-unless-`--force`) and upgrades its log
line into a tombstone marker, closing the gap safely for the destructive path.

## Scope (S2a only)

### In Scope
- Detect every inbound markdown link and typed relation targeting the concept
  being forgotten, reusing merge's scanners (`bundle/links.py::find_inbound_link_rewrites`,
  `bundle/relations.py::find_inbound_relation_rewrites`) in DETECT-ONLY mode.
- Surface detected inbound references in the Phase A preview.
- REFUSE the forget when any inbound reference exists, unless `--force` is passed.
- Upgrade the plain `**Forget**` log line into a marked tombstone entry in `log.md`.
- Surface the S1 resurrection interaction (below) in preview/docs.

### Non-Goals (explicitly deferred / out of scope)
- **S2b**: descendant cascade — scope/depth over the provenance/derivation chain.
- Silent stripping/rewriting of inbound refs (would mutate innocent third-party
  concepts — refuse-not-strip is the deliberate choice).
- Any change to `lifecycle.py` or S1's retrieval predicate (zero coupling).
- Real transactionality / new transaction machinery.
- New `status` value, `status: tombstoned`, leftover files, or frontmatter field.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `forget-command`: **REVISE** two requirements — "Log Entry on Forget" (plain
  line → tombstone marker) and the "Known Limitation" (dangling inbound refs now
  detected + refused, not deferred). **ADD** requirements: inbound-reference
  detection, refuse-unless-`--force`, `--force` override semantics, and
  resurrection-interaction disclosure.

## Approach

Keep the existing non-transactional shape: Phase A computes everything from one
snapshot (path-safety gate `_resolve_concept_path` stays FIRST), previews, and a
confirm-gate precedes Phase B atomic writes with file-delete LAST
(catalog-before-file, git-recoverable, idempotent re-run). Add a detect-only
inbound scan in Phase A; if it finds references, refuse before any write unless
`--force`. On success, `log.md` gets a tombstone-marked line instead of the plain
one. No survivor exists, so nothing is retargeted.

## S1 Resurrection Interaction (must surface, not silence)

Forgetting concept X that carries an OUTBOUND `supersedes` edge (X supersedes Y)
deletes that edge, so Y is no longer effective-deprecated and RE-ENTERS retrieval
via S1's predicate. Defensible but a behavior change — must be shown in the
preview and/or docs. Exact placement is an open design decision.

## Open Design Decisions (need sign-off before spec)
1. Exact tombstone line format in `log.md` (marker syntax, back-reference).
2. Detect-only helper vs. overloading the merge scanners with a detect mode.
3. How/where to surface the resurrection interaction (preview line, docs, both).
4. Exact `--force` semantics (bypass refuse only, or also affect preview text /
   confirm precedence).

## Arc / Dependency Note (context, not S2a work)

Gap #8 is a 4-slice arc: S1 status-aware-retrieval (DONE) → S2 forget+tombstones
(**S2a this** + S2b cascade) → S3 sensitivity fail-closed filter (HARD GATE
before any cloud/export slice) → S4 export exclusion. S2a is independently
shippable; S2b (cascade) chains from it.

## Destructive-Verb Risk Framing

| Failure mode | Design defense |
|--------------|----------------|
| Third-party data loss | Refuse-not-strip; never mutate innocent concepts |
| Partial-write window | Merge ordering, catalog-before-file, delete-last, git-recoverable |
| Path traversal / wrong delete | `_resolve_concept_path` stays the FIRST Phase-A gate |
| Resurrection surprise | Surface X→Y `supersedes` deletion in preview/docs |

## Rollback Plan

Revert is a code-only change to the `forget` command and additive scanner reuse;
no data migration. Tombstone lines are ordinary `log.md` text; existing plain
`**Forget**` lines remain valid history.

## Dependencies

- Merge's inbound scanners (`bundle/links.py`, `bundle/relations.py`) — reused
  in detect-only mode, not modified.
- `docs/knowledge-object-model.md` L325 (tombstone-in-log semantics).

## Success Criteria

- [ ] Inbound markdown links + typed relations to the target are detected and
      shown in the preview.
- [ ] A forget with inbound references refuses (non-zero, no write) unless `--force`.
- [ ] A successful forget writes a tombstone-marked line in `log.md`.
- [ ] The X→Y `supersedes` resurrection case is surfaced, not silent.
- [ ] `lifecycle.py`, S1 retrieval, and reserved-file handling are untouched.
