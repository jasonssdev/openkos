# Design: deprecated-status-export — write `status: deprecated` on a superseded concept for OKF v0.2 consumers

Refs #1075. Proposal: `proposal.md`. ADR: `docs/adr/0032-deprecated-status-is-an-export-of-computed-supersession.md`.

## Technical Approach

Three layers, built bottom-up so that each one is safe before the next
exists:

1. **Projection** (`model/okf.py`, pure): frontmatter + `superseded: bool`
   → one outcome. Plus the read-back rule in `okf.declares_deprecated`.
2. **Superseded set** (`lifecycle.py`, pure over a mapping): which concept
   ids are targets of a non-self `supersedes` edge, and whether that answer
   is complete.
3. **Callers**: `lint` (report), `repair` (fix everything), and each
   relation-changing verb (fix what it changed, in its own write).

Nothing new reaches retrieval: after layer 1 the effective-deprecated set
is, by construction, the same for every bundle whether or not exports are
present.

## Architecture Decisions

### Decision 1: A marked export, invisible to the predicate

`lifecycle.deprecated_concept_ids` today returns
`own_deprecated ∪ superseded` (`src/openkos/lifecycle.py:81-87`), where
`own_deprecated` comes from `okf.declares_deprecated`
(`src/openkos/model/okf.py:2147-2154`). An unmarked export would join
`own_deprecated` and outlive its edge.

`declares_deprecated` becomes `status == "deprecated" and not
has_valid_export_marker(metadata)`. `bundle/listing.py:130` already goes
through the same helper, so `list` inherits the rule with no change.

**Rejected:** unmarked export (feeds back; ADR-0032); a separate sidecar
under `bundle/.state/` recording which statuses are exports (invisible to
anyone reading the document, and one more store to keep in sync); marking
with the superseder ids (goes stale under merge, which would force merge to
rewrite third-party files).

### Decision 2: The projection and its precedence

```python
# model/okf.py
STATUS_DERIVED_FROM_KEY: Final = "status_derived_from"
_EXPORT_MARKER_VALUE: Final = "supersedes"

class ExportOutcome(StrEnum):
    UNCHANGED = "unchanged"
    EXPORT = "export"
    WITHDRAW = "withdraw"
    DROP_MARKER = "drop-marker"
    BLOCKED = "blocked"

@dataclass(frozen=True)
class ExportDecision:
    outcome: ExportOutcome
    metadata: dict[str, object]   # the projected frontmatter (a copy)
    blocked_value: object | None  # the preserved human value when BLOCKED

def has_valid_export_marker(metadata: Mapping[str, object]) -> bool: ...
def project_deprecation_export(
    metadata: Mapping[str, object], *, superseded: bool
) -> ExportDecision: ...
def apply_deprecation_export(text: str, *, superseded: bool) -> tuple[ExportDecision, str]:
    """load_frontmatter -> project -> dump_frontmatter; returns the input
    text UNCHANGED (same object, no re-serialization) when the outcome is
    UNCHANGED or BLOCKED, mirroring migrate_document's `Unchanged` rule."""
```

The table is the spec's (`deprecated-status-export`, "One Deterministic
Projection Decides Every Export Write"). Notes on the choices:

- **Legacy `active`** toggles like `stable`: `repair` would migrate it to
  `stable` anyway, and a withdraw writes `stable`.
- **Withdraw writes `stable`**, not the prior bytes. Recording the prior
  value would add a second field for a distinction OKF v0.2 §5.4 defines as
  empty. The only place byte-exact restoration matters is `unmerge`, which
  restores snapshots (Decision 6).
- **Invalid markers are discarded first**, then the row is evaluated. This
  makes a hand-edited export (`deprecated` → `draft` with the marker left
  behind) resolve deterministically: the marker goes, the human value
  stays.
- **`status_derived_from` is never emitted without `status: deprecated`**
  and vice versa for an export, so the pair is atomic within one
  `dump_frontmatter` call.

**Rejected:** a projection per verb (they would drift apart; the issue's
"status_for" is exactly one function); overwriting `draft` (violates
"human curates"); refusing the supersede when the loser is `draft` (the
human asked for the edge; the engine hides it regardless; blocking is
reported instead).

### Decision 3: The superseded set, over disk or over a planned view

```python
# lifecycle.py
@dataclass(frozen=True)
class SupersededSet:
    ids: frozenset[str]
    complete: bool                 # False if any doc failed to read/parse or had malformed relations
    unreadable: tuple[str, ...]    # concept ids / paths that made it incomplete

def superseded_from_metadata(docs: Mapping[str, Mapping[str, object] | None]) -> SupersededSet: ...
def superseded_concept_ids(bundle_dir: Path) -> SupersededSet: ...  # one okf._iter_docs walk
```

`deprecated_concept_ids` is refactored to use the same edge rule (R2
fail-safe unchanged: every non-self target, cycles included) so the
predicate and the export can never disagree about who is superseded.
`None` in the mapping marks an unreadable doc.

Writers evaluate `superseded_from_metadata` over their **post-write view**:
the metadata they already hold for every document (forget and merge
already load `other_files` for the whole bundle), with their own planned
texts substituted and deleted documents removed. No extra walk.

**Complete-walk rule:** WITHDRAW is applied only when `complete` is true.
Otherwise it degrades to "skipped, reported". EXPORT/BLOCKED/DROP-MARKER
are justified by something actually observed and stay safe.

### Decision 4: Where the drift fixer lives — `repair`

Precedent: `repair` already rewrites derived frontmatter — `sources` from
`provenance`, `active` → `stable` (`src/openkos/cli/main.py:16082-16110`,
`application/repair.py`) — idempotently, behind its refusal gates and drift
guard, in one commit. `lint` is read-only by specification
(`openspec/specs/lint/spec.md:242`) and its fixable findings name a fixing
verb (`non-nfc-name` → `normalize-names`). So `lint` reports
`status-export-drift` naming `openkos repair`, and `repair` fixes it.

In `plan_repair`, each concept document is run through
`migrate_document` first, then `apply_deprecation_export` over the
migrated text, producing at most ONE `DocumentRewrite` per document. The
superseded set is computed from the POST-migration metadata (migration
never touches `relations:`, so this equals the pre-migration set; the test
pins that). `DocumentRewrite.changes` gains an `export: ExportOutcome`
field; the summary counts exports, withdrawals, marker drops, blocked, and
skipped withdrawals separately. `has_work` includes export rewrites, and
the "nothing to migrate" message becomes "nothing to repair".

**Rejected:** `lint --fix` (lint is read-only); a new verb (`repair` is
already the "make the bundle consistent" verb, and a second one splits
the refusal gates); `doctor` (read-only too).

### Decision 5: Writers apply the projection only to what they changed

Each verb projects exactly the documents whose superseded-ness it changes,
never the whole bundle — otherwise an unrelated `forget` would silently
rewrite pre-existing drift across the bundle, which is `repair`'s job and
its preview's.

| Verb | Documents projected | Write placement |
|---|---|---|
| `reconcile --winner` (all three walks via `_reconcile_pair`) | the loser, only when the edge is ADDED | folded into `new_text_{a,b}`; already under the drift guard and autocommit (`cli/main.py:10306-10398`) |
| `relate a supersedes b` | `b`, only when the edge is ADDED and `a != b` | `b` joins `PreparedRelate`'s writes and baselines |
| `forget` | every out-of-set target in `resurrection_pairs` (`application/lifecycle.py:1309-1320`) whose post-forget superseded-ness is false | after catalog, before deletes (forget-command delta) |
| `purge` | same set, from forget's Phase A | live-tree cleanup, staged in the post-rewrite commit; non-fatal |
| `merge` | the survivor | inside `build_merged_document`'s output, re-projected over the post-merge view |
| `unmerge` | restored survivor + restored absorbed | projected over the post-unmerge view, before `write_exclusive`/`write_atomic` |

`reconcile`'s holder cannot change superseded-ness by adding an outbound
edge; mutual supersession through `reconcile` is already refused ("At Most
One Resolution Per Pair"). A longer cycle closed through `reconcile` still
exports only the target, which is exactly what changed.

**Sequence (reconcile --winner):**

```
user ─ reconcile a b --winner a ─▶ _reconcile_pair
  Phase A: snapshot_read(a, b, log)
           add supersedes(a→b)  ── edge_added_a?
           if edge_added: okf.apply_deprecation_export(text_b, superseded=True)
           preview: "~ bundle/b.md (… ; status → deprecated)" | "(own status draft kept)"
  confirm gate
  _reject_drifted_targets({a, b, log})
  Phase B: write a, write b, write log   (b carries edge-side note + export)
  _autocommit([a, b, log])
```

**Sequence (forget M, M supersedes Y):**

```
prepare_forget:
  load other_files (existing)          ─▶ post view = other_files − purge set
  lifecycle.superseded_from_metadata(post view) ─▶ Y not superseded, complete?
  okf.apply_deprecation_export(Y, superseded=False) ─▶ WITHDRAW
  plan.status_rewrites = {Y: new_text}; preview line names "status → stable"
forget_core:
  write index, write log, write Y (sorted), remove M (sorted)  ── deletes LAST
```

### Decision 6: Merge and unmerge

`build_merged_document` adds `STATUS_DERIVED_FROM_KEY` to `_SPECIAL_KEYS`
(`model/okf.py:2405`) and skips the absorbed `status` when the absorbed
side has a valid marker. The caller (`prepare_merge`) then projects the
survivor over the post-merge view (inbound retargets applied, self-loops
dropped). Only the survivor can change: an inbound retarget moves "B is
superseded" to "S is superseded", an outbound move keeps its target
superseded, and a dropped self-loop can only un-supersede S.

`unmerge` restores snapshots verbatim and THEN projects the survivor and
absorbed over the post-unmerge view. On a consistent bundle with no
intervening edge change, the projection is the identity (pinned by a
parity test over an exported fixture), so the existing byte-parity
guarantee is untouched. The only deviation is the spec's carve-out.

The merge ledger stores `survivor_before` and `absorbed_snapshot` as
before; the exported `status` is simply part of those bytes. No ledger
schema change.

### Decision 7: ADR

Both ADR gate conditions hold: it decides an interface (a frontmatter
extension key external readers see) and a trade-off (edges stay
authoritative, frontmatter carries a marked copy), and it is hard to
reverse once bundles in the wild carry the marker. ADR-0032, status
Proposed, amends ADR-0029 Decision 5 only (precedent: ADR-0016's
**Amends:** line). ADR-0029 is not edited.

## File Changes

| File | Change |
|---|---|
| `src/openkos/model/okf.py` | marker constant, `has_valid_export_marker`, `ExportOutcome`/`ExportDecision`, `project_deprecation_export`, `apply_deprecation_export`; `declares_deprecated` excludes marked values; `_SPECIAL_KEYS` + absorbed-status skip in `build_merged_document` |
| `src/openkos/lifecycle.py` | `SupersededSet`, `superseded_from_metadata`, `superseded_concept_ids`; `deprecated_concept_ids` reuses the edge rule |
| `src/openkos/lint.py` | `check_status_export` → `status-export-drift` / `status-export-blocked` / not-run |
| `src/openkos/application/repair.py` | export pass composed after `migrate_document`; counts; `has_work` |
| `src/openkos/cli/main.py` | `_reconcile_pair` export + preview/echo; `repair` report; `purge` live cleanup + commit paths; merge/unmerge/forget/relate previews |
| `src/openkos/application/lifecycle.py` | `prepare_relate`/`relate_core`, `prepare_forget`/`forget_core`, purge plan, `prepare_merge`, `prepare_unmerge`/`unmerge_core` |
| `docs/knowledge-object-model.md`, `docs/cli.md` | one sentence each (shape change: frontmatter now carries the export) |

## Interfaces / Contracts

- `status_derived_from` is read or written ONLY in `model/okf.py`. A test
  greps `src/openkos` for the string outside that file and for raw
  `.get("status")` reads outside it.
- `okf.declares_deprecated(meta)` keeps its signature; only its truth table
  changes for marked values.
- `lint` finding kinds: `status-export-drift`, `status-export-blocked`.

## Testing Strategy

Strict TDD, runner `uv run pytest`. Unit tests for the projection table
(one test per row, plus idempotence as a parametrized sweep over every
row); predicate tests that a marked `deprecated` without an edge is live;
`deprecated_concept_ids` equality before/after `repair` over
`examples/good-life-demo` and the v0.1 fixture; per-verb tests built from
real verb calls in `tmp_path`. Every test that goes green on first run is
mutation-proven (memory: first-try-green tests assert nothing until a
mutation fails them), with `__pycache__` purged between mutation and
verdict.

## Threat Matrix

N/A — no routing, shell, subprocess, VCS automation, or process-integration
boundary is added. `purge` passes a longer path list to the existing
`_autocommit`, as argv, as every verb does.

## Migration / Rollout

Existing bundles: nothing happens until a writer touches a superseded pair
or the user runs `repair`. `lint` makes the gap visible
(`status-export-drift`). `examples/good-life-demo` gets `repair` run in the
last phase if it contains any `supersedes` edge (the task checks first).

## Open Questions

- **Does `unmerge` refuse on a survivor edited after the merge?** The spec
  carve-out holds either way; if it does not refuse, a post-merge
  `reconcile` export on the survivor is discarded by the snapshot restore
  and then re-derived by the projection. Phase 5 pins whichever is true
  with a test before implementing.
- **Should `status-export-blocked` be suppressible?** Chosen: no — it is
  informational and non-gating, like every lint finding; revisit if users
  keep superseded drafts deliberately.
