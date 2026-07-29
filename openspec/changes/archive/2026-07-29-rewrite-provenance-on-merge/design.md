# Design: Rewrite inbound provenance on merge

## Technical Approach

Extend `bundle/provenance.py` with a find/apply/reverse trio shaped exactly like
`bundle/relations.py`'s (whole-file snapshot, drift-checked absolute reversal —
not offset math, because `provenance:` is a YAML list field with no stable
positional disambiguator). Wire it as a **third pass over the snapshot
`prepare_merge` already builds**, a third link in `merge_core`'s per-file
transform chain, and a third reversal kind in `unmerge`. Carry it in the ledger
via `MERGE_LEDGER_SCHEMA_V3` + `okf.ProvenanceRewrite`, with v1/v2 still
readable. Merge and unmerge ship as one design because the ledger schema and the
plan dataclasses are shared by both directions.

**ADR gate: MET.** Recorded as [ADR-0011](../../../docs/adr/0011-provenance-retarget-on-merge.md)
(status `Accepted` — flipped during archive phase). Condition 1 — this decides an interface (the v3 on-disk
ledger contract) and a trade-off (referential integrity vs. literal
provenance history). Condition 2 — hard to reverse: the ledger is a durable
on-disk contract (ADR-0002), and a v3 entry written into a user's survivor
frontmatter **outlives the code that wrote it**; `decode_merge_ledger_entry`
branches on a closed schema set and rejects v3 outright once the code is
reverted. That is not the "additive, revert-and-forget" shape the last two
changes correctly declined an ADR for. Precedent confirms it: the v1 → v2 bump
got ADR-0005.

## Architecture Decisions

| # | Decision | Alternatives rejected | Rationale |
|---|---|---|---|
| P1 | Trio lives in `bundle/provenance.py`, docstring widened from "orphan-closure helper for forget" | New `bundle/provenance_rewrite.py`; route via `references.py` | Colocates all `provenance:` list-field semantics; `references.py` is documented detect-only and forget-owned |
| P2 | Retarget-then-dedupe, first-occurrence-wins | Blind substring replace; append survivor without dropping absorbed | Mirrors `build_merged_document`/`apply_relation_rewrites`; a naive replace duplicates when a file already cites both |
| P3 | Scan **ungated by `absorbed_id`'s `type`** | Copy `set-sensitivity`'s `type == "Source"` gate | `query --save` writes `provenance=[cited concept ids]` — any concept can be a legitimate provenance target |
| P4 | Ledger v3, `provenance_rewrites` REQUIRED on v3; v1/v2 decode to `[]` | Optional key on v2 | An optional key cannot distinguish "none happened" from "dropped by an older writer" — the exact silent misread the fail-closed decoder exists to prevent |
| P5 | Unmerge precedence **provenance > relations > links** | Apply all three reversals | Each snapshot is a whole-file restore; applying a second would clobber the first or fail closed on a now-absent occurrence |
| P6 | Match/normalize provenance entries with the module's existing `_normalize_id` (`.md`-stripped) | Exact string equality | Consistent with `find_provenance_descendants`, which already normalizes; reversal stays deterministic because it is snapshot-based |

## Interfaces / Contracts

```python
# src/openkos/model/okf.py
MERGE_LEDGER_SCHEMA_V3: Final = "openkos.merge_ledger/v3"

@dataclass(frozen=True)
class ProvenanceRewrite:      # mirrors RelationRewrite exactly
    file: str
    snapshot: str             # full verbatim pre-merge bytes

# MergeLedgerEntry gains, after relation_rewrites:
    provenance_rewrites: list[ProvenanceRewrite] = field(default_factory=list)
```

```python
# src/openkos/bundle/provenance.py
def find_inbound_provenance_rewrites(
    files: Mapping[str, str], *, absorbed_id: str, survivor_id: str
) -> list[okf.ProvenanceRewrite]: ...

def apply_provenance_rewrites(
    text: str, *, file: str, survivor_id: str, absorbed_id: str,
    rewrites: list[okf.ProvenanceRewrite],
) -> str: ...

def reverse_provenance_rewrites(
    text: str, *, file: str, survivor_id: str, absorbed_id: str,
    rewrites: list[okf.ProvenanceRewrite],
    link_rewrites: list[okf.LinkRewrite],
    relation_rewrites: list[okf.RelationRewrite],
) -> str: ...
```

`reverse_*` takes **both** other rewrite lists because it must recompute the
expected post-merge bytes forward from the snapshot in `merge_core`'s exact
order (link → relation → provenance) before comparing against the current
on-disk `text`. Mismatch ⇒ `ValueError` (drift, fail closed). More than one
recorded rewrite for the same `file` ⇒ `ValueError` (construction bug — the
scanner records at most one per file per merge). No recorded rewrite ⇒ returns
`text` unchanged.

`find_*` excludes `file_id in (survivor_id, absorbed_id)` and skips a file whose
frontmatter fails to parse or whose `provenance` is not a list (broad `except`,
same rationale as `relations.py`).

## The retarget-then-dedupe algorithm

```python
absorbed = _normalize_id(absorbed_id)
merged: list[str] = []
seen: set[str] = set()
for entry in raw_provenance:                  # source order preserved
    value = str(entry)
    retargeted = survivor_id if _normalize_id(value) == absorbed else value
    key = _normalize_id(retargeted)
    if key in seen:
        continue                              # first occurrence wins
    seen.add(key)
    merged.append(retargeted)
metadata["provenance"] = merged
```

Properties this guarantees:

- A list naming both ids collapses to **one** `survivor_id` entry at the
  **earlier** of the two positions; all other entries keep their relative order.
  `[absorbed, x, survivor]` → `[survivor, x]`; `[survivor, x, absorbed]` →
  `[survivor, x]`.
- **The absorbed id named more than once** (e.g. `[absorbed, x, absorbed.md]`,
  reachable because entries may or may not carry `.md`) collapses to a single
  `survivor_id` at the first position: `[survivor, x]`. The dedupe key is the
  normalized id, so `.md`-variant duplicates collapse too.
- A retained entry keeps its **original string form**; only a retargeted entry is
  re-emitted as the bare `survivor_id`. Pre-existing duplicates collapse, exactly
  as `apply_relation_rewrites` already does.
- The result is **never empty** — `find_*` only records files holding at least
  one absorbed entry — so there is no "pop the key" branch, unlike
  `apply_relation_rewrites`. Assert this rather than defend it.

## Ledger v3 encode / decode

`encode_merge_ledger_entry` gains a **second fail-closed guard mirroring the
existing V1 one**: raise `ValueError` when `entry.schema` is V1 *or* V2 and
`entry.provenance_rewrites` is non-empty. Same reasoning as the current V1 check
— the decoder's V1/V2 branches discard the key unconditionally, so silently
encoding it would let it round-trip to `[]` with no signal. The key itself is
always emitted (`[]` for older schemas), matching how `relation_rewrites` is
already emitted unconditionally.

`decode_merge_ledger_entry` branches:

| schema | `relation_rewrites` | `provenance_rewrites` |
|---|---|---|
| v1 | `[]` (key ignored if present) | `[]` (key ignored if present) |
| v2 | REQUIRED, fails closed | `[]` (key ignored if present) |
| v3 | REQUIRED, fails closed | REQUIRED, fails closed |
| other | `ValueError: unsupported merged_from schema version` | — |

`_decode_provenance_rewrite` mirrors `_decode_relation_rewrite`. `plan_merge`
always writes v3. `MergePlan`/`UnmergePlan` gain
`provenance_rewrites: list[okf.ProvenanceRewrite]`, threaded exactly as
`relation_rewrites` was.

## Data flow — merge (three passes, ONE snapshot)

```
prepare_merge (Phase A, no writes)
  bundle_dir.rglob("*.md")  ── exactly ONE walk ──▶ other_files: dict[str, str]
                                                        │  (pre-merge bytes)
        ┌───────────────────────────────────────────────┼───────────────┐
        ▼                       ▼                       ▼               │
  find_inbound_link_     find_inbound_relation_   find_inbound_          │
  rewrites               _rewrites                provenance_rewrites    │
        │                       │                       │               │
        └──── link_rewrites ────┴─ relation_rewrites ───┴── provenance_rewrites
                                    │
                             plan_merge(...) ──▶ MergePlan + v3 ledger entry
                                    │
        touched_files = links ∪ relations ∪ provenance   (sorted union of THREE)

merge_core (Phase B, writes)
  index.md ▶ log.md ▶ for each rel in touched_files:
        other_files[rel]
          │ apply_link_rewrites (idempotent)
          ▼ apply_relation_rewrites
          ▼ apply_provenance_rewrites
          └─▶ fsio.write_atomic(bundle_dir/rel)      ← still ONE write per file
  ▶ merged survivor (carries v3 merged_from) ▶ remove absorbed
```

The three transforms touch disjoint regions (body link / frontmatter
`relations:` / frontmatter `provenance:`), so chaining them on the same
in-memory text is safe — the same argument D5 already makes for two.

## Data flow — unmerge (precedence)

```
plan.provenance_rewrites ─┐
plan.relation_rewrites  ──┤   provenance_files = {r.file for r in provenance_rewrites}
plan.link_rewrites     ───┘   relation_files   = {relations} − provenance_files
                              link_files       = {links} − provenance_files − relation_files

  provenance_files ─▶ reverse_provenance_rewrites(text, …, link_rewrites, relation_rewrites)
                        └ expected = apply_link ▸ apply_relation ▸ apply_provenance(snapshot)
                          text != expected ⇒ ValueError (drift, refuse before any write)
  relation_files   ─▶ reverse_relation_rewrites   (today's rule, unchanged)
  link_files       ─▶ reverse_link_rewrites       (exact offset, unchanged)

Write order: index.md ▸ log.md ▸ reversed files ▸ absorbed ▸ survivor ▸ log.md (+audit line)
```

Correctness rests on the provenance and relation snapshots for a shared file
being **byte-identical** (all three scanners read the same `other_files`). That
is asserted, not assumed — see T4.

## File Changes

| File | Action | Description | Slice |
|---|---|---|---|
| `src/openkos/model/okf.py` | Modify | `ProvenanceRewrite`, `MERGE_LEDGER_SCHEMA_V3`, entry field, encode guard + key, `_decode_provenance_rewrite`, v3 decode branch | PR1 |
| `src/openkos/bundle/provenance.py` | Modify | Trio + widened module docstring | PR1 |
| `src/openkos/bundle/merge.py` | Modify | `MergePlan`/`UnmergePlan.provenance_rewrites`, `plan_merge`/`plan_unmerge` threading, v3 | PR1 |
| `tests/unit/bundle/test_provenance.py` | Modify | Trio unit tests (mirror `test_relations.py`) | PR1 |
| `tests/unit/model/test_okf.py`, `tests/unit/bundle/test_merge.py` | Modify | v3 codec + plan threading | PR1 |
| `src/openkos/cli/main.py` | Modify | `prepare_merge` third scanner + `PreparedMerge` field + preview; `merge_core` third transform; `unmerge` precedence + reversal + preview | PR2 |
| `docs/cli.md` | Modify | Merge/unmerge provenance behaviour, v3 note, rollback caveat | PR2 |
| `tests/unit/cli/test_merge.py`, `test_merge_core.py`, `test_merge_roundtrip.py`, `test_unmerge.py` | Modify | CLI wiring, walk-count, byte-identity, round-trip | PR2 |
| `docs/adr/0011-provenance-retarget-on-merge.md` | Create | ADR (status Accepted as of archive phase) | PR1 |
| `docs/adr/README.md` | Modify | Index row | PR1 |

`src/openkos/bundle/references.py`, `src/openkos/lint.py`, and
`set_sensitivity_cmd` are explicitly **untouched**.

## Testing Strategy

Strict TDD (RED → GREEN → REFACTOR), `uv run pytest`, branch coverage ≥ 90.
Every `except`/branch below is a named test so the gate is met by design, not by
luck.

| ID | Layer | What | Approach |
|---|---|---|---|
| T1 | Unit | `find_*` records a third party citing the absorbed id; excludes survivor & absorbed themselves; skips malformed frontmatter; skips non-list `provenance` | In-memory `dict[str, str]`, `_doc()` helper, mirroring `test_relations.py` |
| T2 | Unit | **Non-Source absorbed concept is recorded** (P3 guard) | `absorbed` is `type: Decision`; assert a rewrite is still produced |
| T3 | Unit | Dedupe matrix, parametrized: `[a]`→`[s]`; `[a,x,s]`→`[s,x]`; `[s,x,a]`→`[s,x]`; `[a,x,a.md]`→`[s,x]`; `[x,y]` (no rewrite) → unchanged text | `apply_provenance_rewrites`, assert exact list AND order |
| T4 | CLI | **Snapshot byte-identity**: a third party carrying an inbound link, a `relations:` entry AND a `provenance:` entry to the absorbed id ⇒ after merge, read the survivor's `merged_from` tail off disk and assert `entry.provenance_rewrites[0].snapshot == entry.relation_rewrites[0].snapshot` and both equal the file's pre-merge bytes captured by the test | Real temp workspace; asserts the P5 precedence premise instead of trusting it |
| T5 | CLI | **Zero extra walks**: `prepare_merge` calls `Path.rglob(bundle_dir, "*.md")` exactly once | See below — plain-function counting wrapper |
| T6 | Unit | Reverse: clean case returns snapshot; drifted `text` raises `ValueError`; two rewrites for one file raises `ValueError`; unrecorded file returns `text` | `reverse_provenance_rewrites` |
| T7 | Unit | v3 codec: round-trip; v3 missing `provenance_rewrites` fails closed; malformed item fails closed; **v1 and v2 entries decode to `provenance_rewrites=[]`**; encoding a V1 or V2 entry carrying non-empty `provenance_rewrites` raises | `tests/unit/model/test_okf.py` |
| T8 | CLI | Round-trip parity: merge → unmerge is byte-identical for a file carrying all three rewrite kinds, and for provenance-only, relations-only, links-only files | Extend `test_merge_roundtrip.py`'s snapshot comparison |
| T9 | CLI | A pre-existing v1 and a v2 ledger entry still unmerge exactly (no regression) | Hand-built fixture survivor, `test_unmerge.py` |
| T10 | CLI | Unmerge refuses (exit 1, no write) when a provenance-rewritten file drifted | Edit the file post-merge, assert bundle snapshot unchanged |

**T5 wrapper — the shape matters:**

```python
original = Path.rglob
calls: list[tuple[Path, str]] = []

def _counting_rglob(self: Path, pattern: str):   # plain function — NOT a generator
    calls.append((self, pattern))
    return original(self, pattern)               # returns the generator; call recorded eagerly

monkeypatch.setattr(Path, "rglob", _counting_rglob)
...
assert [c for c in calls if c == (bundle_dir, "*.md")] == [(bundle_dir, "*.md")]
```

A `yield from original(...)` body would make the wrapper itself a generator, so
`calls.append` would not run until the first `next()` — the assertion would
measure iteration, not invocation, and would prove nothing. Follows the existing
`_counting_build_graph` precedent (`tests/unit/cli/test_contradictions.py:1041`).

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file
classification, or process-integration boundary. The two adversarial surfaces
here are drift (T6, T10, fail-closed `ValueError` before any write) and
malformed third-party frontmatter (T1, skip-not-crash), both covered above.

## Migration / Rollout

No data migration: existing v1/v2 entries are read unchanged and unmerge
exactly. New merges write v3.

**Rollback failure mode (explicit).** `git revert` of both PRs is *not* purely
additive. A v3 entry already written into a survivor's `merged_from` frontmatter
survives the revert. Restored v2 code reaches `decode_merge_ledger_entry`'s
`else` branch and raises `ValueError: unsupported merged_from schema version:
'openkos.merge_ledger/v3'`. Blast radius is bounded and known: only
`bundle/merge.py:117` (`plan_merge`) and `:176` (`plan_unmerge`) decode the
ledger, so for an affected survivor **both `merge` and `unmerge` refuse** with
that error; every other verb is unaffected. Operator recovery, in order of
preference:

1. **Before reverting** — run `unmerge` on every pair merged under v3. This
   removes the v3 entries and reverses the provenance retarget properly.
2. **After reverting** — hand-edit the survivor's frontmatter: set
   `schema: openkos.merge_ledger/v2` and delete the `provenance_rewrites` key.
   `merge`/`unmerge` work again, but the provenance retarget **stays applied on
   disk** and v2 `unmerge` will not reverse it; that must be undone by hand (or
   via git) using the deleted `provenance_rewrites` snapshots as the reference.

## Delivery Slices

Two stacked PRs to `main`, confirmed but **revised downward**. The proposal
forecast ~350 / ~400; this repo's dense docstring style has made the last
several forecasts land 40–60% high, so the realistic figures are:

- **PR1 — primitives + ledger v3 + ADR (~210–260 lines).** `okf.py`,
  `bundle/provenance.py`, `bundle/merge.py`, their unit tests, ADR-0011 +
  index row. Self-contained: pure functions and codec, fully tested, no CLI
  behaviour change (nothing calls the trio yet), trivially revertible.
- **PR2 — CLI wiring + unmerge + docs (~240–300 lines).** `cli/main.py`
  (`prepare_merge`, `merge_core`, `unmerge`), `docs/cli.md`, CLI tests
  T4/T5/T8/T9/T10. Targets PR1's branch.

`Decision needed before apply: No` — both slices sit well inside the 800-line
review budget under the cached `auto-chain` / `stacked-to-main` strategy.

## Open Questions

- [ ] None blocking. Follow-ups deliberately excluded: a `lint`
      `check_dangling_targets` provenance axis, and issue #232's
      `set-sensitivity` warning scope.
