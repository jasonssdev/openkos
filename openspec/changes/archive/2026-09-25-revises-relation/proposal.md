# Proposal: revises-relation — record a refinement that hides nothing

## Intent

Refs #1014, piece **(a)**, sub-change 1 of 3 (umbrella exploration:
`openspec/changes/decision-revision-detection/exploration.md`).

`openkos reconcile` can record two outcomes for a pair today: a symmetric
`reconciled_with` (both coexist, no order) and a directional `supersedes`
(`--winner`), which hides the loser as current through
`lifecycle.deprecated_concept_ids`. Neither fits the most common way a decision
evolves: a later decision **refines** an earlier one. Both stay valid, and the
order between them matters. Recording that as `supersedes` would hide a decision
that is still in force. Recording it as `reconciled_with` would lose the
direction.

This change adds the missing third outcome: a directional **`revises`**
relation, written by `reconcile`, that records "this later decision refines that
earlier one" and **hides nothing**. It is useful on its own for recording
refinements by hand. It is also the write target that the revision detector
(sub-change 2) will propose. There is no LLM, detector, or harness in this change.

The human decision is already taken and is not reopened here. A **reversal** is
`supersedes`: the old decision is hidden as current. A **refinement** is
`revises`: nothing is hidden.

## Scope

### In Scope

1. **A `revises` relation type** in `relations:` frontmatter. The later,
   refining concept holds one outbound `revises` edge pointing at the earlier
   concept. There is no back-edge, which mirrors `supersedes`.
2. **A `reconcile --revision <id>` option.** `<id>` MUST resolve to exactly one
   member of the pair; that member holds the edge. It refuses (exit 1, zero
   writes) when combined with `--winner` or with `--from-findings`, or when
   `<id>` is not a pair member.
3. **Body notes and output.** Two new `_ReconcileRole` values, `revises` and
   `revised`, each with a `## Reconciliation` sentence:
   - `Revises [<id>](/<id>.md) as of <date> (refinement; both remain current).`
   - `Revised by [<id>](/<id>.md) as of <date> (refinement; both remain current).`

   There is also a new `**Reconcile**` log line shape, a success line that says
   both remain current, and a commit message
   (`openkos: reconcile <a> revises <b>`).
4. **A third reconciliation mode**, `revision`, with a defined result for every
   transition (see the transition table below). The at-most-one-resolution
   invariant is extended to cover it.
5. **Hides nothing, as a tested requirement.** `lifecycle.deprecated_concept_ids`
   (`lifecycle.py:78`) and the `list` STATUS predicate
   (`bundle/listing.py:143`), the two places that special-case the
   `"supersedes"` string, MUST treat a `revises` edge as non-deprecating for
   both ends. A test pins each.
6. **Resolved pairs leave contradiction candidacy.** `_candidate_pairs`
   (`resolution/contradiction.py:278`) drops any pair joined, in either
   direction, by a `supersedes`, `reconciled_with` or `revises` edge. It does
   this before the count and the cap, so the `curate` cost gate stays honest.
   See the verification note below.
7. **One shared constant** naming the three resolution relation types. Both the
   reconcile classifier and the contradiction filter read it, so the two cannot
   drift.
8. Delta specs, a `docs/cli.md` `reconcile` update, ADR-0024 (written in the
   design phase), and tests.

### Out of Scope

- The detector, subject derivation, the judge prompt, and a `curate` stage or
  verb (sub-change 2). The fixture and harness (sub-change 3).
- **`reconcile --from-findings`.** It stays symmetric-only. It walks
  contradiction findings and offers only `reconciled_with`. Revision findings do
  not exist until sub-change 2, which owns the directional offer.
- An upgrade path between modes, for example turning `revises` into
  `supersedes`. It is refused (see below). The recorded way to change a
  resolution stays a manual edit or a git revert.
- An `unreconcile` verb, a ledger, and any `status` write. `reconcile` stays
  additive-only.
- Surfacing `revises` in `query` answers or history views (#1014 piece b).
- Changes to `examples/good-life-demo/`.

## Decisions

| Decision | Chosen | Rejected | Why |
| --- | --- | --- | --- |
| Relation name and direction | `revises`, held by the later (refining) concept, pointing at the earlier one | `revised_by` on the earlier concept; a back-edge on both | Mirrors `supersedes`: the newer concept asserts the relationship. One outbound edge per resolution keeps the state classifier simple. |
| CLI surface | `--revision <id>`, which names the pair member holding the edge | `--refines <id>`; `--relation revises` combined with `--winner`; a positional mode word | It has the same shape as `--winner`: a noun naming a pair member, validated the same way. `--refines alpha` is ambiguous about direction ("alpha refines" or "refines alpha"), and a wrong direction here is silent. Reusing `--winner` would imply that something loses, which a refinement does not. The noun shares its root with the written relation, so help, log and edge read consistently. |
| Flag conflicts | `--revision` with `--winner` refuses with exit 1; `--revision` with `--from-findings` refuses with exit 1 | Typer usage error (exit 2); last flag wins | Matches the existing `--from-findings` combination gate (`ValueError`, exit 1, zero writes). |
| Registry | `revises` stays OUTSIDE `model/relations.py` `REGISTRY` and `SUGGESTABLE_RELATION_TYPES` | Seeding it | This follows the verified `supersedes`/`reconciled_with` precedent: they are written directly as `okf.Relation` and are absent from `REGISTRY` (`relations.py:29-38`). An LLM edge suggester must never propose a resolution, because that is a human judgment. The open vocabulary already accepts the string. |
| Note roles | Two roles: `revises` (holder) and `revised` (target) | A single role on both sides | Mirrors `supersedes`/`superseded`. Each note must name its own side of a directional relation. |
| Mode transitions | Only exact repeats are idempotent; every change of mode or direction refuses | Allowing an "upgrade" from `revises` to `supersedes` | See the table below. The note anchor is role-blind (`_reconcile_anchor_present`), so an upgrade would leave a stale note that no later run can repair. This is the same reason the existing mode switch refuses. |
| Contradiction exclusion | Exclude pairs joined by any of the three resolution edges | Exclude only `revises`; leave it to the decisions ledger | Verified gap, not a duplicate (see below). A reconcile edge is itself a typed edge, so today the act of reconciling a pair *manufactures* its contradiction candidacy. `revises` would have the same problem. |
| ADR | Yes, ADR-0024, written in design | No ADR | Both gate conditions hold. It fixes a persisted interface (a relation type in users' bundles, plus a CLI flag), and it is hard to reverse, because renaming needs a bundle migration. It also records a semantic trade-off: reversal hides and refinement does not, and resolved pairs are never re-judged. |

### Transition table (existing state, then requested state)

`none` means the pair has no resolution edge. `revision(X)` means X revises its
counterpart, and `directional(X)` means X supersedes it.

| Existing \ Requested | symmetric | directional(A) | revision(A) |
| --- | --- | --- | --- |
| none | write | write | write |
| symmetric | idempotent no-op | refuse | refuse |
| directional(A) | refuse | idempotent no-op | refuse |
| directional(B) | refuse | refuse | refuse |
| revision(A) | refuse | refuse | idempotent no-op |
| revision(B) | refuse | refuse | refuse |

Every refusal exits 1 with zero writes. The error message names the existing
resolution through `_reconciliation_state_description`, which gains a
`revision` description.

**Classification precedence** for a pair that already carries several kinds of
resolution edge (only possible by hand editing or `relate`): `supersedes`, then
`revises`, then `reconciled_with`. This keeps today's `supersedes`-first
behavior unchanged. Design decides whether a *mixed* state, meaning more than
one kind of edge or `revises` in both directions, should instead refuse every
request. That is the recommended, more conservative option.

### Verification notes (checked in code, not assumed)

- **Reconciled pairs are NOT excluded from contradiction candidates today.**
  `find_contradictions` excludes nodes only: `excluded = deprecated |
  confidential` (`contradiction.py:975`). A `supersedes` pair drops out only
  because the loser is deprecated. A `reconciled_with` pair stays a candidate.
  Its own `reconciled_with` edge is enough to nominate it, since the graph
  projects `relations:` as typed edges (`graph/sqlite_graph.py:488-496`).
  `is_contradiction_declined` is a display filter over the decisions sidecar,
  and `reconcile` never writes a decision record, so it does not cover this.
  Once `reconcile` rewrites the bytes, the finding goes stale, and the next run
  judges the pair again. The pair-level exclusion is therefore new behavior, not
  a duplicate of an existing mechanism.
- **Graph and retrieval.** A `revises` edge projects as a typed edge in
  `graph.db` (pass 2, `relations:` frontmatter). It counts toward
  `asserted_relations_exist` and the typed-edge summary, as `reconciled_with`
  does. Neither end is deprecated, so retrieval treats both as live and needs no
  change.
- **merge, unmerge, forget and purge** need no change. `bundle/merge.py` does
  not special-case any relation type; `okf.merge_relations` retargets every
  edge generically and drops a newly synthesized self-loop. Purge's only
  `supersedes`-specific logic (`application/lifecycle.py:1309-1321`) discloses a
  target that would *resurrect* into retrieval. A `revises` target is never
  hidden, so nothing resurrects. Inbound-reference detection is already
  type-agnostic.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `reconcile-command`:
  - ADDED: "Refinement Reconciliation via --revision". This covers the edge, the
    notes, the log line, pair-member validation, and the `--winner` and
    `--from-findings` refusals.
  - ADDED: "At Most One Resolution Per Pair". The existing refuse-on-conflict
    gate is in the code and in `docs/cli.md` but missing from the spec. It is
    added with the full transition table.
  - MODIFIED: the existing requirements' `# Reconciliation` wording is changed
    to `## Reconciliation`, which is what the code writes (`main.py:9563`).
- `status-aware-retrieval`: MODIFIED "Effective Status Resolution". A scenario
  is added: a `revises` edge deprecates neither end, in retrieval or in `list`
  STATUS.
- `contradiction-detection`: MODIFIED "Candidate Generation From Typed Graph
  Edges, Deduped". A pair joined by `supersedes`, `reconciled_with` or `revises`
  in either direction is not a candidate. It is dropped before the total count
  and the cap. The Non-Goals line "all typed edges are candidates" is narrowed
  to match.

`typed-relationships` gets no delta. Its open vocabulary already admits
`revises`, and `relate` is unchanged.

## Approach

- **`cli/main.py`** (reconcile block, about lines 9511-10135):
  - Extend `_ReconcileRole` and `_reconcile_sentence`.
  - Extend the `_existing_reconciliation_state` return type to
    `Literal["none", "symmetric", "directional", "revision"]`, with the holder
    returned as it is for a winner. Extend `_reconciliation_state_description`.
  - Add the `--revision` option and its validation, beside `--winner`.
  - Thread a requested mode through `_reconcile_pair`. Today that mode is
    derived from `winner_canonical is None`, so design picks the smallest
    parameter shape.
  - Add the `revises` edge branch, the log, echo and commit shapes, and extend
    the `--from-findings` combination gate.
- **Resolution constant.** One frozenset of the three resolution type names. It
  is defined beside the relation vocabulary but deliberately NOT in `REGISTRY`,
  and design picks the module. The reconcile classifier and `_candidate_pairs`
  both read it.
- **`resolution/contradiction.py`**: in `_candidate_pairs`, collect the resolved
  pair keys from the same `store.edges()` walk and subtract them before counting
  and capping. `_pair_relation_types` is unaffected.
- **No change** to `lifecycle.py` or `bundle/listing.py` code. Only the tests
  that pin their non-deprecation of `revises` are added.
- The core stays synchronous. There are no new dependencies, and the OKF seam is
  untouched: `okf.Relation` already carries any type string. Links in body
  prose stay untyped, and the type lives only in `relations:` (ADR-0004).

## Affected Areas

| Area | Impact | Description |
| --- | --- | --- |
| `src/openkos/cli/main.py` | Modified | `--revision`, third mode, roles, sentences, log/echo/commit shapes |
| `src/openkos/model/relations.py` (or design's choice) | Modified | resolution-type constant, outside `REGISTRY` |
| `src/openkos/resolution/contradiction.py` | Modified | resolved-pair exclusion in `_candidate_pairs` |
| `tests/unit/cli/test_reconcile.py` | Modified | flag validation, the full transition matrix, notes, idempotency |
| `tests/unit/test_lifecycle.py`, listing tests | Modified | `revises` deprecates neither end |
| `tests/unit/resolution/test_contradiction*.py` | Modified | exclusion of all three types, in both directions, before the cap |
| `openspec/changes/revises-relation/specs/{reconcile-command,status-aware-retrieval,contradiction-detection}/` | New | delta specs |
| `docs/cli.md` (`reconcile` section, about line 371) | Modified | bullet, flag row, refusal sentence covering three modes |
| `docs/adr/0024-*.md`, `docs/adr/README.md` | New / Modified | ADR, status Proposed |

## Principles Impact

- **Adopt OKF:** a relation type string in `relations:` is an existing §4.1
  extension. It is untyped in prose and degrades gracefully.
- **Human curates, engine maintains:** `revises` is written only by an explicit
  human `reconcile` behind the existing confirm gate, and the LLM suggester can
  never propose it.
- **Representation, not truth:** both decisions stay visible. The engine records
  the relationship and adjudicates nothing.
- **Reconstructible:** the edge lives in canonical frontmatter, and `graph.db`
  re-projects it.
- **Local-first, sensitivity, immutable `raw/`, sync core:** untouched.

## Risks

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| Excluding `reconciled_with` pairs changes shipped behavior: a symmetrically reconciled pair is no longer re-judged, even after an edit | Med | This is the stated purpose of `reconcile` ("so the decision is kept rather than repeated"). Undoing the reconciliation (git revert or edge removal) restores candidacy. The orchestrator should confirm this widening beyond `revises`. |
| `curate` Contradictions cost-gate counts drop on bundles with reconciled pairs | Low | Intended. The exclusion happens before the count, so the gate and the spend stay one computation (#446). |
| A mixed, hand-edited state is classified surprisingly | Low | Explicit precedence. Design may choose "mixed refuses all". A test covers each mixed case. |
| A direction mistake at the CLI records the wrong holder | Low | The flag names the holder explicitly. The preview and success line name who revises whom before the confirm gate. |
| The spec heading correction (`#` to `##`) is read as a behavior change | Low | Code already writes `##`, so this is a correction of the spec to match the code. |

## Rollback Plan

Revert the change's commits. There is no migration. A `revises` edge written in
the interim stays a legal `relations:` entry. Old code projects it as an
ordinary typed edge and never deprecates on it. Its `## Reconciliation` notes
remain as prose. Old `reconcile` classifies such a pair as `none` for that edge
type, which is the pre-change behavior. Removing the edges and notes, if
wanted, is a hand edit committed like any other. Reverting the exclusion makes
reconciled pairs contradiction candidates again, exactly as they are today.

## Dependencies

None. `source-event-time` (#1016-#1019) is merged, but this change does not read
`event_date`. The human names the direction. Sub-changes 2 and 3 depend on this
one.

## Size

About 380-460 authored changed lines: roughly 120 source, 230-280 tests (the
transition matrix is parametrized), and 30-60 for `docs/cli.md` and the ADR.
Delta specs are excluded. That is near the ~400 review budget. If it runs over,
auto-chain has a natural seam:

1. The `revises` relation, the reconcile plumbing, and the hides-nothing pins.
2. Resolved-pair contradiction exclusion.

## Success Criteria

- [ ] `reconcile a b --revision a --auto` writes exactly one `revises` edge on
      `a` targeting `b`, and nothing on `b`. Both get a `## Reconciliation`
      note with the correct role, and `log.md` gains a `**Reconcile**` line.
- [ ] After that write, `deprecated_concept_ids` contains neither `a` nor `b`,
      and `list` shows both as `active`.
- [ ] Every cell of the transition table is pinned by a test: writes, no-ops,
      and refusals with zero bytes changed.
- [ ] `--revision` with `--winner`, with `--from-findings`, or naming a
      non-member exits 1 with zero writes.
- [ ] `_candidate_pairs` returns no pair joined by `supersedes`,
      `reconciled_with` or `revises`, in either direction. `total_count`
      excludes them, and an unrelated live pair is still judged.
- [ ] `revises` is absent from `REGISTRY` and `SUGGESTABLE_RELATION_TYPES`.
- [ ] `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy .`,
      and `uv run pytest --cov` are green, with the 90% branch gate.
- [ ] ADR-0024 exists with status `Proposed` and is indexed.

## Human confirmation (2026-09-25)

The owner confirmed the shipped-behavior change: a pair joined by `reconciled_with`, `supersedes` or `revises` is no longer offered as a contradiction candidate, even after a later edit. To re-judge such a pair, remove the relation.
