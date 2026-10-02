# Proposal: Versions Revise, Deprecation Propagates, Retiring Is Clean (#1268 deliverable 2)

Refs #1268, #1259, #1263. Part of the arc "The Quiet Engine". Depends on the
sibling change `attach-at-ingest` (a new version revises its predecessor's
concepts through attach); this change is independently specifiable and
testable, but its retire path is only exercised end to end once attach has
landed. Apply `attach-at-ingest` first.

## Intent

The lifecycle the engine itself recommends for an edited watched source
(import the new version, `relate <new> supersedes <old>`, retire the old
Source) has no clean exit (#1259, #1263):

1. `supersedes` deprecates only the Source. The concepts whose entire
   provenance is that Source stay `stable` and keep competing in retrieval,
   so a question can still cite the version-1 concept.
2. The recommended command warns "`supersedes` is not a seeded relation
   type", although it is the lifecycle relation the engine itself derives
   status from.
3. `forget <old> --scope source` is refused because of the very edge the
   engine proposed, and `--force` leaves the new Source with a dangling
   `supersedes` reference that `lint` reports.

With attach landed there is a fourth dead end: a concept that both versions
support now lists the old Source in `provenance` and in its `## Related`
backlink. It survives the forget (it has another Source), but those
references to the forgotten Source also refuse it.

This change makes supersession complete: deprecation follows the provenance
of a superseded Source, the vocabulary stops warning on it, and a superseded
Source can be retired without `--force` and without leaving a dangling
reference.

## Scope

### In Scope

- **Provenance-orphan deprecation.** A concept is effective-deprecated when
  it has a non-empty `provenance` and every entry is, directly or through
  other such concepts, a Source that another concept supersedes. It is
  computed with the same orphan closure `forget --scope source` uses
  (`bundle.provenance.provenance_closure`), rooted at the superseded
  Sources. It lives in the one shared predicate, so retrieval, candidate
  generation, `answer` and `list` agree. A concept that also cites any live
  Source stays live (this is what attach produces for a revised concept).
- **No warning on `supersedes`.** The write path stops saying "not a
  seeded relation type" for the three lifecycle relation types
  (`supersedes`, `revises`, `reconciled_with`); the suggester's vocabulary
  and the seeded set are unchanged (an LLM still cannot propose them).
- **A clean retire path in `forget`.** WHEN the forgotten set contains a
  Source that another Source supersedes, the superseding Source's
  `supersedes` edge and the surviving concepts' references to the forgotten
  Source (their `provenance`/`sources` entry and the generated `## Related`
  bullet) are historical, not blocking: they are removed in the same
  confirmed forget, previewed as edits, and drift-guarded. Any other
  external reference still refuses without `--force`.
- Living-spec deltas, `docs/cli.md`, an ADR.

### Out of Scope

- **Exporting** the provenance-derived deprecation to frontmatter `status`.
  The export (ADR-0032) stays a projection of `supersedes` edges only; an
  external OKF reader sees a propagated concept as `stable`. See design D2.
- Writing the `supersedes` relation unattended: ADR-0041 stands, the watch
  still proposes it as a pending-work row and a person runs `relate`.
- Any change to `purge` (irreversible erasure keeps its strict rails and
  does not take the retire path).
- Changing which concepts attach creates (`attach-at-ingest`), merging
  existing `-N` families (arc deliverable 4), or any cross-type rule.
- Reviving a deprecated concept automatically when a live Source starts
  citing it.
- A new verb or flag: the retire path is the existing `forget` with a
  better Phase A.

## Approach

`lifecycle.deprecated_concept_ids` already walks every document once and
holds each one's metadata. The same walk additionally collects each
concept's normalized `provenance` ids and, after the edge-derived superseded
set is known, calls one new pure function that runs the existing
`provenance_closure` over `provenance_by_id` with the superseded Source ids
as roots, excluding the roots themselves. `bundle/listing.py`, which
replicates the predicate to keep `list` to one walk, calls the same pure
function over the data it already holds, so the two cannot drift.
`superseded_from_metadata` (the export's input) is deliberately left
edge-only.

For `forget`, Phase A already scans inbound references and already plans
out-of-set rewrites (the resurrection status withdrawals). It additionally
classifies two reference shapes as historical when, and only when, the
purge set contains a superseded Source, plans the rewrites with new pure
helpers beside the existing provenance rewrite helpers, and Phase B writes
them with the same drift-guarded path the withdrawals use. See `design.md`.

## Capabilities

### Modified Capabilities

- `status-aware-retrieval` -- MODIFIED "Effective Status Resolution".
- `list-command` -- MODIFIED "Deprecated and Superseded Visibility".
- `deprecated-status-export` -- MODIFIED "The Engine Never Reads Its Own
  Export"; ADDED "A Provenance-Derived Deprecation Is Not Exported".
- `typed-relationships` -- MODIFIED "Seeded-But-Extensible Relation
  Vocabulary".
- `forget-command` -- MODIFIED "Refuse Forget When Inbound References Exist,
  Unless `--force`"; ADDED "Retiring A Superseded Source Detaches Its
  Historical References".
- `privacy-purge` -- MODIFIED "Purge Set Resolution Reuses Forget Phase A"
  (purge does not take the retire path).

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/lifecycle.py` | Modified | Pure provenance-orphan function; `deprecated_concept_ids` collects provenance in its existing walk |
| `src/openkos/bundle/listing.py` | Modified | Calls the same pure function for the STATUS column |
| `src/openkos/bundle/provenance.py` | Modified | Pure detach helpers (remove one provenance entry and its `## Related` bullet) beside the merge rewrite helpers |
| `src/openkos/model/relations.py` | Modified | `relation_type_note` silent for the lifecycle relation types |
| `src/openkos/application/lifecycle.py` | Modified | `prepare_forget` historical-reference classification and rewrites; `forget_core` writes them |
| `src/openkos/cli/main.py` | Modified | Preview lines only (presentation) |
| `docs/cli.md`, `docs/adr/` | Modified | `forget`, `relate`; ADR (Proposed) |
| `tests/unit/...` | Modified | Predicate, listing parity, vocabulary, forget, purge-unchanged |

## Risks

| Risk | Mitigation |
|---|---|
| **A concept is hidden that a person still wants.** Propagation hides a concept whose only Source was superseded, though the new version may simply not mention it. | Deprecation is a read-time predicate, fully reversible: `unrelate` the supersession and the concept is live again, nothing was written. `list` shows `deprecated`; `--include-deprecated` shows it in retrieval. |
| **Transitive closure reaches an `Insight`** that cites only an orphaned concept. | Intended and consistent with `forget`'s closure (an insight resting solely on withdrawn knowledge is withdrawn); covered by a scenario. |
| **Retire path rewrites surviving concepts.** | Previewed as edits, count-confirmed, drift-guarded, and limited to the generated shapes (a `provenance`/`sources` entry and the exact generated `## Related` bullet); anything else, including a hand-written link, still refuses. Sensitivity is never lowered by it. |
| **`purge` semantics leak.** | `purge` is explicitly unchanged and has a refusal scenario. |
| **Layering.** `lifecycle` documents itself as importing only `model.okf`; the closure lives in `bundle.provenance`. | Decided in apply with an AST guard: import the closure from the canonical `bundle` package (allowed direction), or move the pure closure to a leaf both import. No new dependency on the derived layer. |
| **Edge cycles.** A `supersedes` cycle already deprecates every member (R2). | Roots are the same edge-derived set, so cycles behave as today; the closure adds nothing to a cycle's members. |

## Rollback Plan

Revert the commits. Nothing is migrated or written by propagation (it is
derived), so a revert restores today's retrieval at once. Forgets already
performed under the retire path are ordinary git commits and are reverted
like any forget.

## Open Questions (for the owner)

1. **What retiring a superseded Source does to the supersession record.**
   When the old Source is forgotten, (a) remove the `supersedes` edge from
   the new Source and detach the old Source from the surviving concepts'
   provenance (what this change specifies); (b) keep the edge as a
   historical record and exempt it from `forget`'s refusal and from
   `lint`'s dangling-reference scan; (c) keep today's refusal and make
   `--force` the documented path. (a) leaves nothing dangling and keeps
   `lint` simple, at the price of erasing the in-bundle record that the new
   Source replaced the old (git and `log.md` keep it). (b) preserves that
   record in frontmatter but teaches `lint` and `forget` a "historical
   edge" exception, and leaves a reference to a concept that no longer
   exists. (c) changes nothing and keeps the dead end. **Recommendation:
   (a).** The `forget` tombstone and the `**Relate**` log entry already
   record the supersession durably, and an edge to a deleted concept is by
   definition not a usable relation.

(The default for attach and the body-merge strategy are the other two
questions; they are in `attach-at-ingest`'s proposal.)

## Resolved Decisions (owner, #1268)

1. Retiring a superseded Source removes the `supersedes` edge and detaches
   the old Source from surviving concepts' provenance and `## Related`, in
   the same confirmed forget (option a, as specified).
2. In `attach-at-ingest`: `attach_at_ingest` defaults on for every path with a
   kill switch, and new evidence is a deterministic append.

## Success Criteria

- [ ] A concept whose every provenance entry is a superseded Source is
      excluded from retrieval, `answer` candidates and shown `deprecated`
      by `list`, with no frontmatter written.
- [ ] A concept citing a superseded Source and a live Source stays live.
- [ ] `unrelate` of the supersession makes the concepts live again.
- [ ] `relate <new> supersedes <old>` emits no "not a seeded relation type"
      note.
- [ ] `forget sources/<old> --scope source` succeeds without `--force`,
      leaves no dangling reference (`lint` clean), keeps concepts that cite
      a live Source (with the old Source detached), and still refuses an
      unrelated external reference.
- [ ] `purge` still refuses the same inputs.
- [ ] All gates green; the PR uses `Refs #1268`, `Refs #1259`,
      `Refs #1263`.
