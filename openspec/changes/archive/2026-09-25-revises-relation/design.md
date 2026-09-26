# Design: revises-relation — record a refinement that hides nothing

Refs #1014 piece (a), sub-change 1 of 3. Proposal: `proposal.md` (including the
"Human confirmation (2026-09-25)" section). Decision record: ADR-0024
(`docs/adr/0024-revises-relation-and-resolved-pairs.md`).

## Technical Approach

`reconcile` gains a third resolution, `revision`. It is written as one directional
`revises` edge on the refining concept, and it is surfaced through the existing
two-id transaction (`_reconcile_pair`). No new module, no new write path, and no
new store are added. The edge type string lives in canonical `relations:`
frontmatter. `okf.Relation`/`encode_relations`/`decode_relations` already carry any
type string, so the OKF seam (`model/okf.py`) is untouched.

Three changes do the work:

1. **Vocabulary.** One constant, `RESOLUTION_RELATION_TYPES`, names the three
   resolution types. It lives in `model/relations.py`, outside `REGISTRY`.
2. **Reconcile.** It gets a `--revision <id>` option, a table-driven state
   classifier that knows three modes plus a `mixed` state, two new note roles,
   and the log/echo/commit shapes.
3. **Contradiction candidates.** `_candidate_pairs` subtracts every pair joined
   by a resolution edge before it counts and caps. Because `plan_candidates` is
   the single source for both `curate`'s cost gate and `find_contradictions`'
   spend (#446), the gate stays exact with no change in `curate.py`.

`lifecycle.py` and `bundle/listing.py` are NOT modified. They test
`relation.type == "supersedes"` (`lifecycle.py:78`, `bundle/listing.py:143`), so
a `revises` edge is already non-deprecating. The change adds tests that pin this
behavior.

## Architecture Decisions

### Decision 1: Where the shared resolution constant lives

**Choice**: `src/openkos/model/relations.py`, as
`RESOLUTION_RELATION_TYPES: frozenset[str] = frozenset({"supersedes", "reconciled_with", "revises"})`.
It is defined after `ASYMMETRIC_RELATION_TYPES`, and it is not a member of
`REGISTRY`, `SEEDED_RELATION_TYPES` or `SUGGESTABLE_RELATION_TYPES`.
**Alternatives considered**:
- `lifecycle.py`. It is a canonical leaf, but it owns deprecation, not
  vocabulary. Putting the constant there would suggest that all three types
  deprecate, and two of them do not.
- `resolution/contradiction.py`. The canonical-adjacent `cli/main.py` classifier
  would then import a derived LLM module for a string set.
- `cli/main.py`. The derived `resolution/` layer would then import the CLI, which
  inverts the layering.
**Rationale**: `model/relations.py` is already the zero-dependency home of the
relation vocabulary and its narrowing sets (`ENGINE_OWNED_`, `ASYMMETRIC_`). The
canonical layer never imports the derived layer, and `resolution/` may import
`model/`. `cli/main.py` already imports from this module (`main.py:70`), and
`resolution/contradiction.py` already imports `openkos.model` (`contradiction.py:80`).
Keeping the constant out of `REGISTRY` follows the verified precedent that
`supersedes`/`reconciled_with` are absent from it (`relations.py:29-38`). It also
means `SUGGESTABLE_RELATION_TYPES`, which is derived from `REGISTRY`
(`relations.py:80-82`), can never offer a resolution to an LLM suggester.
`validate_relation_type` is untouched: `relate --type revises` keeps its
advisory "not a seeded relation type" note, exactly as `supersedes` gets today.

### Decision 2: Classifier shape: table-driven, with a `mixed` state

**Choice**: Replace the body of `_existing_reconciliation_state`
(`main.py:9592-9630`) with one pass over both sides' relations. The pass collects
a set of `(mode, holder)` resolutions between the pair:

- `reconciled_with` maps to `("symmetric", None)`. Both sides collapse to one
  resolution, so a one-sided `reconciled_with` is still `symmetric`, as it is
  today.
- `supersedes` maps to `("directional", holder)`.
- `revises` maps to `("revision", holder)`.

The mapping is a module dict, `_MODE_BY_RESOLUTION_TYPE`, keyed by the
members of `RESOLUTION_RELATION_TYPES`. The result is:

- an empty set gives `("none", None)`;
- exactly one element gives that element;
- more than one gives `("mixed", None)`.

**Alternatives considered**:
- Keep the proposal's precedence order (`supersedes`, then `revises`, then
  `reconciled_with`) and classify a mixed pair by its highest-precedence edge.
- Keep today's two hand-written `any(...)` blocks and add a third.
**Rationale**: The proposal recommends that a mixed state refuse every
request, and this design settles on that. A pair carrying two disagreeing
resolutions (`revises` both ways, `supersedes` both ways, or two different types)
is already contradictory, and no write `reconcile` can make fixes it. Classifying
it by precedence would let an "idempotent" request of the winning kind run the
no-op path and write `"already reconciled; no change."` to `log.md`. That line
asserts a clean state that does not exist. Refusing costs nothing (zero writes)
and points the human at the manual fix. `lifecycle.py`'s R2 rule already treats
contradictory supersession as unresolved rather than guessing, so refusing here
matches it. The table-driven form is also shorter than today's code. Because the
dict is keyed by the shared constant, a test (`set(_MODE_BY_RESOLUTION_TYPE) ==
RESOLUTION_RELATION_TYPES`) makes classifier/filter drift a failing test instead
of a silent `KeyError`.

**Shipped-behavior note (hand-edited bundles only)**: Today a pair with
`supersedes` in both directions classifies as `directional(a)` (`main.py:9616`).
A pair with `supersedes` and `reconciled_with` also classifies as directional.
Both now classify as `mixed` and refuse. `reconcile` itself can no longer
produce either state, because the at-most-one gate prevents it. No existing test
pins the old precedence; codegraph reports no covering test for
`_existing_reconciliation_state`.

### Decision 3: Transition gate: unchanged predicate, one more description

**Choice**: Keep the refuse predicate at `main.py:9969-9971` exactly as it is,
with the renamed holder:
`existing_mode != "none" and (existing_mode != requested_mode or existing_holder != holder_canonical)`.
`"mixed"` never equals a requested mode, so it refuses with no extra branch.
`_reconciliation_state_description` (`main.py:9633`) gains two cases:
- `revision`: `f"a revision ({holder!r} revises its counterpart; both remain current)"`
- `mixed`: `"conflicting resolutions (more than one 'supersedes', 'revises' or 'reconciled_with' edge between the pair, and they disagree)"`

The refusal text is unchanged: `concepts {a!r} and {b!r} are already reconciled
as {description}; reconcile will not overwrite an existing resolution. To change
it, edit the concepts manually or revert with git, then re-run`. It is echoed as
`openkos reconcile: failed while preparing the reconcile -- <msg>.`, with exit 1
and zero writes.

**Alternatives considered**: A dedicated mixed-state error with its own wording
and exit code.
**Rationale**: One gate means one refusal contract. The existing tests match
`"already reconciled"`, and every new refusal keeps that substring.

Full transition table (existing state, then requested state; A/B are the pair
members, and X(A) means A holds the edge):

| Existing \ Requested | symmetric | directional(A) | revision(A) |
| --- | --- | --- | --- |
| none | write | write | write |
| symmetric | idempotent no-op | refuse | refuse |
| directional(A) | refuse | idempotent no-op | refuse |
| directional(B) | refuse | refuse | refuse |
| revision(A) | refuse | refuse | idempotent no-op |
| revision(B) | refuse | refuse | refuse |
| mixed (any) | refuse | refuse | refuse |

"Idempotent no-op" is today's path. Edges dedup on `(target, type)`, and notes
are anchor-suppressed, so both concept files are byte-identical. `log.md` gains
the `"... are already reconciled; no change."` line. `changed=False` skips the
#640 derived refresh.

### Decision 4: `_reconcile_pair` parameter shape

**Choice**: Rename the positional `winner_canonical, loser_canonical`
(`main.py:9908-9909`) to `holder_canonical, target_canonical`, and add a
keyword-only `edge_type: Literal["supersedes", "revises"] = "supersedes"`. When
`holder_canonical is None` the request is symmetric and `edge_type` is ignored.
Otherwise `requested_mode = _MODE_BY_RESOLUTION_TYPE[edge_type]`.
**Alternatives considered**:
- `revision: bool = False`, keeping the name `winner_canonical`. That shape
  reads "winner" for a refinement, which is exactly the misreading the proposal
  rejects for the CLI flag.
- A single `requested_mode` parameter plus the two ids. It is redundant, because
  the mode is derivable from `holder is None` plus the edge type, and mismatched
  combinations would become representable.
**Rationale**: This is the smallest shape that keeps illegal states
unrepresentable. The default leaves `_run_reconcile_from_findings`' call
(`main.py:10238-10251`, which passes `None, None`) byte-unchanged.
`--from-findings` stays symmetric-only.

The edge branch (`main.py:9986-10003`) becomes the unchanged symmetric branch
plus one directed branch driven by
`_DIRECTED_ROLES: dict[str, tuple[_ReconcileRole, _ReconcileRole]] = {"supersedes": ("supersedes", "superseded"), "revises": ("revises", "revised")}`.
The holder gets `okf.Relation(target=<counterpart>, type=edge_type)` and the
holder role. The counterpart gets no edge (no back-edge) and the target role.

### Decision 5: `--revision <id>` wiring and refusals

**Choice**: Add a Typer option beside `--winner` (`main.py:9660-9667`):

```python
revision: str | None = typer.Option(
    None,
    "--revision",
    help=(
        "Concept id (must resolve to id_a or id_b) that revises (refines) its "
        "counterpart; both remain current. Cannot be combined with --winner."
    ),
),
```

Validation happens in this order, and each step refuses with exit 1 and zero
writes. Every refusal is echoed as
`openkos reconcile: refusing to reconcile -- <msg>.` through the existing
`except (OSError, ValueError)` blocks.

1. **`--from-findings` gate** (`main.py:9800-9806`). The condition gains
   `or revision is not None`. The message becomes:
   `--from-findings takes no concept ids, no --winner, no --revision, and no --auto; use the two-id form for a directional, revision, or unattended reconciliation`.
   The existing test only matches `"--from-findings"`, so it stays green.
2. **Flag conflict**. This is new, in the same first `try`, after the
   `--from-findings` branch and before the two-ids check:
   `if winner is not None and revision is not None: raise ValueError("--winner and --revision are mutually exclusive: a reconciliation is either a reversal (--winner) or a refinement (--revision), never both")`.
   It runs before any id resolution, so the refusal does not depend on whether
   the ids exist.
3. **Pair membership**. Extract the `--winner` block (`main.py:9853-9865`) into
   `_resolve_pair_member(layout, flag: str, value: str, canonical_a: str, canonical_b: str) -> tuple[str, str]`.
   It returns `(holder, counterpart)` and raises
   `f"{flag} {value!r} must resolve to one of the pair ({canonical_a!r}, {canonical_b!r}), got {resolved!r}"`.
   It is called once for `--winner` (with `edge_type="supersedes"`) or once for
   `--revision` (with `edge_type="revises"`). The winner message is
   byte-identical to today's, with `flag="--winner"`. Traversal, a reserved
   basename or a nonexistent id refuse through `resolve_concept_path`, exactly
   as `--winner` does.

**Alternatives considered**: Typer mutual-exclusion callbacks, or a usage error
(exit 2).
**Rationale**: This matches the existing `--from-findings` combination gate
(`ValueError`, exit 1). The shared helper keeps `--winner` and `--revision`
validation identical by construction instead of by copy.

### Decision 6: Note sentences, log, echo, commit, preview

**Choice**:
- `_ReconcileRole = Literal["reconciled", "supersedes", "superseded", "revises", "revised"]`.
  `_reconcile_sentence` (`main.py:9535`) gains two branches, still ahead of the
  defensive `raise`:
  - `revises`: `f"Revises {link} as of {date_str} (refinement; both remain current)."`
  - `revised`: `f"Revised by {link} as of {date_str} (refinement; both remain current)."`
  `_reconciliation_note` and `_append_reconciliation_note` are unchanged: an
  h2 `## Reconciliation` heading and an anchor
  `<!-- okos:reconcile target=<id> role=revises|revised -->`. `_RECONCILE_ANCHOR_RE`'s
  `role=(\w+)` already matches both roles.
- Log line (new-write, revision):
  `**Reconcile**: [<h>](/<h>.md) revises [<t>](/<t>.md) (recorded 'revises'; both remain current).`
- Success echo:
  `openkos reconcile: recorded '<h>' as revising '<t>'; both remain current (<log> updated).`
  Like #389 for `--winner`, it names the resulting status, which here is that
  nothing is hidden.
- Commit: `openkos: reconcile <h> revises <t>`.
- Preview: when a holder is set (both directed modes), add one line after
  `openkos reconcile: proposed changes:`, reading
  `  = '<h>' supersedes '<t>'` or `  = '<h>' revises '<t>'`. The confirm prompt
  then names the direction before consent.

**Alternatives considered**: Leaving the preview unchanged, so that direction is
visible only in the post-write echo.
**Rationale**: The proposal's direction-mistake mitigation assumes the preview
names who revises whom, but today it only says "relation added". The line is
additive. No test pins reconcile preview bytes (`"proposed changes"` goldens
exist only for ingest, query-save and set-sensitivity).

### Decision 7: Resolved-pair exclusion in `_candidate_pairs`

**Choice**: In `resolution/contradiction.py` `_candidate_pairs`, insert the
following between `typed_edges` (`:344-350`) and `ordered` (`:352`):

```python
resolved = {
    _pair_key(edge.source_id, edge.target_id)
    for edge in typed_edges
    if edge.relation_type in RESOLUTION_RELATION_TYPES
}
pair_keys = {_pair_key(e.source_id, e.target_id) for e in typed_edges} - resolved
```

This comes before the deprecation filter, the `total_count` and the cap slice,
so the "filter before cap" rule (`:317-327`) holds on both `return`s. The
docstrings of `_candidate_pairs`, `CandidatePlan.edge_total`
("…resolution-filtered…") and the module header are updated in the same style.
**Store read**: the graph's typed edges (`store.edges()`, `relation_type`). This
is the same walk `_candidate_pairs` already does, and the same store every
caller passes in. The graph projects `relations:` frontmatter as typed edges
(pass 2), so the source of truth is still canonical frontmatter, read through
the derived projection.
**Alternatives considered**:
- A frontmatter walk (like `lifecycle.deprecated_concept_ids`), passed in beside
  `excluded`. That is a second bundle walk per plan, and it bypasses the injected
  `store`. The `_FakeGraphStore`-based unit tests could then no longer express
  the exclusion.
- A post-cap filter. This is the bug the deprecation filter already fixed:
  resolved pairs would consume cap slots and starve live ones.
- An `--include-resolved` escape flag. The owner's confirmation says to remove
  the relation to re-judge the pair, so no flag is added.
**Rationale**: One predicate over one read. `plan_candidates` (`:927`) calls
`_pairs_and_types`, which calls `_candidate_pairs`. That one path feeds both
`curate._contradiction_plan` (`curate.py:1615-1646`, the cost gate) and
`find_contradictions` (the spend), so the gate's `llm_calls` and `edge_total` fall
by exactly the excluded count with no `curate.py` change. The exclusion is
unconditional. It applies under `--include-deprecated` too, because a
`supersedes`-joined pair is resolved whether or not its loser is shown.
Merged-body candidates are unaffected: they are intra-document ledger entries,
and an absorbed concept no longer exists as a pair member. `_pair_relation_types`
is unaffected: it only labels pairs that survive.

### Decision 8: ADR

**Choice**: ADR-0024, status Proposed, dated 2026-09-25. It records `revises` as a
persisted relation type plus a CLI flag, the reversal-versus-refinement
semantics, mixed-state refusal, and resolved pairs leaving candidacy. Both ADR
gate conditions hold: this is an interface decision, and it is hard to reverse,
because renaming the type needs a bundle migration and the exclusion is an
owner-confirmed behavior change.

## Data Flow

Two-id reconcile, revision mode:

```
reconcile a b --revision a [--auto]
  │
  ├─ gate 1: workspace?                         ─ refuse exit 1
  ├─ gate 2: --from-findings + (ids|winner|revision|auto)? ─ refuse exit 1
  ├─ gate 3: --winner AND --revision?           ─ refuse exit 1
  ├─ gate 4: both ids present?                  ─ refuse exit 1
  ├─ resolve a, b (distinct, samefile)          ─ refuse exit 1
  ├─ _resolve_pair_member("--revision", a)      ─ refuse exit 1 (not in pair)
  ▼
_reconcile_pair(..., holder=a, target=b, edge_type="revises")
  │  snapshot a, b, log.md
  │  _existing_reconciliation_state ──► (mode, holder)
  │       none ─────────────► build edge + notes
  │       revision(a) ──────► idempotent path (no-op)
  │       anything else ────► refuse exit 1, zero writes
  │  a.relations += {target: b, type: revises}   (b: no edge)
  │  a.body += note(role=revises) ; b.body += note(role=revised)
  │  preview (+ "= 'a' revises 'b'") → confirm gate → drift guard (exit 3)
  │  write a, b, log.md → echo → autocommit "openkos: reconcile a revises b"
  ▼
_refresh_derived_after_write (only if changed)
```

Contradiction candidacy:

```
store.edges() ─► typed, non-derived_from, non-self-loop edges
                     │
                     ├─► pair_keys ───────────────┐
                     └─► resolved (type ∈ RESOLUTION_RELATION_TYPES)
                                                  ▼
                                pair_keys − resolved ─► sort ─► − deprecated/confidential
                                                  ─► total_count ─► cap
plan_candidates ─► CandidatePlan ─► curate cost gate (llm_calls)  ═ same list ═  find_contradictions
```

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `src/openkos/model/relations.py` | Modify | Add `RESOLUTION_RELATION_TYPES` with a docstring saying why it is outside `REGISTRY` |
| `src/openkos/cli/main.py` | Modify | `--revision` option; flag-conflict gate; `--from-findings` gate text; `_resolve_pair_member`; `_ReconcileRole` + two sentences; `_MODE_BY_RESOLUTION_TYPE`, `_DIRECTED_ROLES`; table-driven `_existing_reconciliation_state` with `mixed`; two new descriptions; `_reconcile_pair` rename + `edge_type`; directed edge branch; revision log/echo/commit; preview direction line; docstring updates |
| `src/openkos/resolution/contradiction.py` | Modify | Resolved-pair subtraction in `_candidate_pairs` before count/cap; docstrings |
| `tests/unit/cli/test_reconcile.py` | Modify | Revision write/notes/log/echo/commit; flag refusals; parametrized transition matrix; mixed states; hides-nothing through the verb |
| `tests/unit/model/test_relations.py` | Modify | Constant membership; disjoint from `REGISTRY`/`SEEDED_`/`SUGGESTABLE_` |
| `tests/unit/test_lifecycle.py` | Modify | `revises` deprecates neither end |
| `tests/unit/bundle/test_listing.py` | Modify | `revises` leaves both rows `active` |
| `tests/unit/resolution/test_contradiction.py` | Modify | Exclusion of all three types in both directions; before-cap; `total_count`; `plan_candidates` edge_total/llm_calls pin |
| `docs/cli.md` | Modify | `reconcile` section (three shapes, flag row, refusal sentence); one sentence in `contradictions` |
| `docs/adr/0024-revises-relation-and-resolved-pairs.md` | Create | ADR, Proposed |
| `docs/adr/README.md` | Modify | Index row 0024 |

`lifecycle.py`, `bundle/listing.py`, `cli/curate.py`, `model/okf.py` and
`graph/` are not modified.

## Interfaces / Contracts

```python
# model/relations.py
RESOLUTION_RELATION_TYPES: frozenset[str] = frozenset(
    {"supersedes", "reconciled_with", "revises"}
)

# cli/main.py
_ReconcileRole = Literal["reconciled", "supersedes", "superseded", "revises", "revised"]
_RequestedMode = Literal["symmetric", "directional", "revision"]
_ResolutionMode = Literal["none", "symmetric", "directional", "revision", "mixed"]
_MODE_BY_RESOLUTION_TYPE: dict[str, _RequestedMode] = {
    "reconciled_with": "symmetric",
    "supersedes": "directional",
    "revises": "revision",
}
_DIRECTED_ROLES: dict[str, tuple[_ReconcileRole, _ReconcileRole]] = {
    "supersedes": ("supersedes", "superseded"),
    "revises": ("revises", "revised"),
}

def _existing_reconciliation_state(
    *, relations_a, relations_b, canonical_a, canonical_b
) -> tuple[_ResolutionMode, str | None]: ...   # holder for directional/revision

def _reconciliation_state_description(mode: _ResolutionMode, holder: str | None) -> str: ...

def _resolve_pair_member(
    layout: config.WorkspaceLayout, flag: str, value: str,
    canonical_a: str, canonical_b: str,
) -> tuple[str, str]: ...                         # (holder, counterpart)

def _reconcile_pair(
    root, layout, log_path, cfg, path_a, canonical_a, path_b, canonical_b,
    holder_canonical: str | None, target_canonical: str | None,
    *, auto: bool, announce_preview: bool = True,
    edge_type: Literal["supersedes", "revises"] = "supersedes",
) -> bool: ...
```

On-disk contract (holder `a`, target `b`):

```yaml
# a.md frontmatter
relations:
  - target: b
    type: revises
```

`a.md` body gets `## Reconciliation` / `<!-- okos:reconcile target=b role=revises -->` / `Revises [b](/b.md) as of YYYY-MM-DD (refinement; both remain current).`
`b.md` body gets the mirror note with `role=revised` and `Revised by [a](/a.md) ...`. `b.md`
gets no `relations:` change.

## Testing Strategy

Strict TDD applies, with runner `uv run pytest`. Write each test RED first. The
**mutation** column is the line a test must kill. Revert every mutation with the
inverse edit (never `git checkout --`), and purge `__pycache__` before trusting a
verdict.

| Layer | What to Test | Approach / mutation killed |
|-------|-------------|----------|
| Unit (relations) | `RESOLUTION_RELATION_TYPES == {"supersedes","reconciled_with","revises"}`; disjoint from `SEEDED_RELATION_TYPES` and `SUGGESTABLE_RELATION_TYPES`; `"revises"` not in any `REGISTRY` name | Kills seeding `revises` into `REGISTRY` |
| Unit (reconcile drift guard) | `set(main._MODE_BY_RESOLUTION_TYPE) == RESOLUTION_RELATION_TYPES`; `set(main._DIRECTED_ROLES) == RESOLUTION_RELATION_TYPES - {"reconciled_with"}` | Kills a fourth type added to one side only |
| CLI (write) | `reconcile a b --revision a --auto`: `relations(a) == [Relation(b, "revises")]`, `relations(b) == []`; anchors `role=revises` on a and `role=revised` on b; exact sentences; log line; stdout echo contains `as revising` and `both remain current`; also `--revision b` (holder is id_b; covers the `else` branch) | Kills swapped `_DIRECTED_ROLES`, a missing `else` branch, and a symmetric fallthrough |
| CLI (refusals) | `--revision c` (not in pair), `--revision sources/nonexistent`, `--revision ../x`: exit 1, `_snapshot` unchanged, stderr contains `--revision`; `--winner a --revision a`: exit 1, `mutually exclusive`, zero writes; `--from-findings --revision a`: exit 1, `--from-findings` in stderr | Kills removal of each gate. Assert the MESSAGE, not only the exit code |
| CLI (transition matrix) | One parametrized test over all 18 cells of the table (6 existing states × 3 requests). Set up via real `reconcile` runs (states none/symmetric/directional(A|B)/revision(A|B)). Assert write → edge present; no-op → concept bytes unchanged + `"already reconciled; no change."` in log; refuse → exit 1 + `"already reconciled"` in stderr + `_snapshot` unchanged | Kills a holder-blind comparison and a `revision` holder returned as `None` |
| CLI (mixed) | Hand-set relations with a `_set_relations(tmp_path, cid, [...])` helper (`load_frontmatter`/`encode_relations`/`dump_frontmatter`): revises both ways; supersedes both ways; `revises`+`supersedes`; `revises`+`reconciled_with`; `supersedes`+`reconciled_with`. For each, all three requests refuse, stderr contains `conflicting resolutions`, and bytes are unchanged | Kills removal of the `len(found) > 1` branch. Without it, tuple-unpacking raises `ValueError`, which still exits 1, so only the message assertion kills it |
| CLI (one-sided symmetric) | Only a has `reconciled_with → b`; symmetric request proceeds (adds b's edge); `--revision a` refuses | Pins that one-sided `reconciled_with` stays `symmetric` |
| CLI (hides nothing, end to end) | After `--revision a`, `openkos list` shows both `active`; `lifecycle.deprecated_concept_ids` contains neither | Integration of the pins below |
| Unit (lifecycle) | Two docs, `a` with `revises → b`: `deprecated_concept_ids(bundle) == frozenset()` | Kills `relation.type in {"supersedes","revises"}` at `lifecycle.py:78` |
| Unit (listing) | Same bundle: both rows `status == "active"` | Kills the same mutation at `listing.py:143` |
| Unit (contradiction) | Parametrize over the three types × both directions: `_FakeGraphStore([Edge(a,b,type), Edge(c,d,"related_to")])` returns `pairs == [("c","d")]` and `total == 1`; a pair carrying BOTH `related_to` and a resolution edge is excluded; before-cap: resolved pair sorts first, `cap=1` still returns the live pair and `total == 1`; `cap=None` branch too | Kills removal of `- resolved`, narrowing the constant to `{"supersedes"}`, and a post-cap filter |
| Unit (cost gate) | `plan_candidates(tmp_path/"bundle", store=_FakeGraphStore([reconciled a-b, related c-d]))` gives `edge_total == 1`, `llm_calls == 1`. This is the exact number `curate._contradictions_probe` states (`curate.py:1664-1667`) | Kills an exclusion applied only in `find_contradictions` |

No integration or e2e layers are configured (`openspec/config.yaml` testing
layers).

## Threat Matrix

N/A. There is no routing, shell, subprocess, VCS/PR automation,
executable-file classification, or process-integration boundary. The existing
autocommit is reused unchanged with a new message string. Write safety remains
the confirm gate, drift guard, atomic writes, additive-only edits, and git undo.

## Migration / Rollout

No migration is required. A `revises` edge is a legal `relations:` entry for any
build. Pre-change code projects it as an ordinary typed edge and never
deprecates on it.

Delivery is `auto-chain`, `stacked-to-main`. The authored estimate (delta specs
excluded) is:

| Slice | Content | Authored lines (est.) |
|---|---|---|
| 1 | constant + reconcile plumbing + note/log/echo/commit/preview + transition/mixed/refusal tests + lifecycle/listing pins + relations tests + `docs/cli.md` reconcile section + ADR-0024 + index row | ~330-380 |
| 2 | `_candidate_pairs` exclusion + docstrings + contradiction/plan tests + `docs/cli.md` contradictions sentence | ~90-120 |

The total is about 420-500, which is over the ~400 budget. Ship the two slices as
chained PRs. Slice 1 is useful on its own. Slice 2 depends only on the constant
from slice 1. If slice 1 comes in well under its estimate and the total is at or
below ~400, a single PR is acceptable.

`docs/cli.md` (describe the shape, not the diff):

- **`reconcile`**: "There are three shapes". Add a bullet for **`--revision <id>`**
  (a directional refinement: one `revises` edge on the refining concept, both
  remain current, nothing is hidden). Add a flag row. Rewrite the idempotency
  sentence to cover three modes and to state that a pair carrying disagreeing
  resolutions refuses every request. Extend the Batch-mode refusals to name
  `--revision`, and add one sentence that `--winner` and `--revision` refuse
  together.
- **`contradictions`**: one sentence stating that a pair a human already resolved
  with `reconcile` (joined by `supersedes`, `reconciled_with` or `revises`, in
  either direction) is never a candidate, including under
  `--include-deprecated`, and that removing the relation makes it a candidate
  again.

## Open Questions

- [ ] None blocking. Known limitation, accepted and not fixed here:
  `vacuous_coverage_notice` (`contradiction.py:896-924`) keys on
  `edge_total == 0`. A bundle whose only typed edges are resolution edges now
  gets "the graph has no applied relations", which is inaccurate wording, since
  relations exist but are all resolved. Coverage is still genuinely empty, so the
  warning's substance holds. Rewording belongs to a follow-up if it is reported.
- [ ] Persisted findings for a newly resolved pair are not deleted. `reconcile`'s
  rewrite makes them digest-stale, which already removes them from
  `--from-findings`, and the pair is never re-judged. This matches "reconcile
  never writes findings". A sweep is out of scope.
