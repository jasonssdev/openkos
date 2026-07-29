# Design: Backfill sensitivity onto existing provenance descendants (#231)

## Technical Approach

Extract `set_sensitivity_cmd`'s inline scan (`main.py:3339-3411`) into **two pure functions**
in `bundle/provenance.py` — a per-Source raise resolver and the bundle-wide
unresolvable-provenance scan — over an extracted fixpoint core that the read-only
`lint`/`status` finding reuses without raw file text. Three chained slices: extract
(#235 + #233), detect, write verb.

> **Spec divergence**: D2, D3, D6 and D8 below intentionally diverge from the delta specs as
> currently drafted. Every divergence is listed with an exact replacement in
> **Required Spec Amendments** at the end. The design is the corrected artifact; the specs
> follow it.

## Architecture Decisions

### D1 — Helper home, name, and shape

| Option | Tradeoff | Decision |
|---|---|---|
| New module `bundle/sensitivity.py` | Splits one provenance closure across two modules | Rejected |
| Helper in `cli/main.py` | CLI layer, untestable without a Typer runner | Rejected |
| `bundle/provenance.py`, next to `find_provenance_descendants` | Canonical layer, pure, no `openkos.graph` import, already imports `okf` | **Chosen** |

```python
# src/openkos/bundle/provenance.py  (pure; no Path, no I/O, no typer)
def resolve_source_raises(
    files: Mapping[str, str], *, source_id: str, level: str
) -> list[okf.DescendantRaise]: ...          # sorted by concept_id

def find_unresolvable_provenance(
    files: Mapping[str, str], *, known_extra_ids: Collection[str] = ()
) -> list[tuple[str, str]]: ...              # (citing_id, unresolved_entry_id)

def provenance_closure(                       # extracted fixpoint core
    provenance_by_id: Mapping[str, frozenset[str]], *, root_ids: Collection[str]
) -> list[str]: ...
```

`okf.DescendantRaise` (frozen dataclass, alongside `okf.ProvenanceRewrite`) carries
`concept_id: str`, `current: object`, `new_level: str`, `content: str`. **No `path` field** —
`Path` is a filesystem concern; each caller derives `layout.bundle_dir / f"{id}.md"`.
`find_provenance_descendants` keeps its exact signature and semantics, now parse-then-delegate
to `provenance_closure` (`provenance.py:129-137` fixpoint moves verbatim).

### D2 — Three consumers, two entry points (**diverges from `specs/lint/spec.md:10-14`**)

`set_sensitivity_cmd` and the backfill verb call `resolve_source_raises`; both hold raw file
text. **The lint scan cannot call it.** `LintDoc` carries `body` (`lint.py:37`), not the full
file text, and `okf.DescendantRaise.content` can only be produced by re-rendering
`okf.dump_frontmatter(metadata, body)` from a metadata dict `LintDoc` does not keep. Rendering
write-ready bytes inside a read-only scan is also the wrong shape.

The lint scan therefore calls `provenance_closure` — the same fixpoint `resolve_source_raises`
is built on — over a map assembled from `LintDoc.provenance`, and compares levels with
`okf.combine_sensitivity`. The shared invariant across all three consumers is *one closure
algorithm and one rank comparator*, not one function object.

Rejected: adding a `text: str` field to `LintDoc` purely to satisfy the spec's wording. It
doubles `collect_docs`'s retained memory for every doc and invites write-path rendering into a
read-only module.

### D3 — Detection data without a fifth walk

`LintDoc` gains exactly two fields, defaulted like `extraction_status`/`resource` (#187):
`sensitivity: str = ""` and `provenance: tuple[str, ...] = ()` (`.md`-stripped, same shape as
`relations`). Defaults are mandatory: `tests/unit/resolution/test_volatility_typing.py:612`
constructs `LintDoc` with only the seven non-defaulted fields. `collect_docs` fills both from
the frontmatter it already parsed (`lint.py:140`). `check_below_source_sensitivity(docs) ->
list[LintFinding]` takes **only `docs`** — the structural no-fifth-walk guard
(`lint.py:556-560`) — and `lint` (`main.py:5352`) and `status` (`main.py:5108`) reuse their
existing single `docs` list.

Both categories are computed from `docs` alone, on one basis: **closure membership**.

| kind | Rule |
|---|---|
| `below-source-sensitivity` | `id` is in the closure of exactly one `type: Source` root, and `combine_sensitivity(doc.sensitivity, source_level) != doc.sensitivity` |
| `multi-source-uncovered` | non-empty `provenance`, **every** cited id resolves to a doc in `docs`, `id` is in **no** single-Source closure, and `doc.sensitivity` sits strictly below the high-water-mark of the cited docs' levels |

Consequences of the closure basis, all deliberate:

- A doc citing one Source plus one derived concept belonging to a *different* Source is in no
  single-Source closure and is correctly flagged `multi-source-uncovered`. A rule keyed on
  "cites two or more Sources" would silently miss it.
- A `query --save` answer (two-output rule) citing two objects derived from the *same* Source
  **is** inside that Source's closure and is therefore `below-source-sensitivity`, not
  uncovered — the subset rule already reaches it.
- The detail string can only name **cited concept ids**, not "citing Source ids": under this
  basis an uncovered doc's provenance need not contain any Source at all. The detail names the
  descendant, its current level, every cited concept id with that concept's level, and marks
  the finding as not covered by `backfill-sensitivity`.
- A doc citing any unresolvable id falls into neither category (fail-safe; it already surfaces
  as the `dangling` finding).

The `below-source-sensitivity` test mirrors the verb exactly (`main.py:3391-3393`), including a
doc with a missing or dirty `sensitivity` under a `public` Source — `combine_sensitivity` ranks
dirty input fail-closed (ADR-0003), so that doc *is* flagged even though it does not "strictly
rank below". Detection must match what the verb would write, or `lint` reports clean while
`backfill-sensitivity` still stages a write.

### D4 — Verb Phase A / Phase B

```
Phase A: require_workspace → read_config → snapshot bundle (one rglob, RESERVED skipped)
       → for source_id in sorted(ids where type == "Source"):
             resolve_source_raises(snapshot, source_id=…, level=source_level)
       → merge by concept_id: keep the highest-ranked new_level
       → if no raises: print the explicit no-op line, exit 0, no log, no commit
       → preview (sorted by concept_id) → --auto > cfg.review > TTY confirm > refuse

Phase B: every merged raise (sorted) → log.md → ONE _autocommit
```

Single pass is convergent for chains: `provenance_closure` is transitive
(`provenance.py:129-137`), so a Source derived from a higher Source *and* that Source's own
descendants all join the higher root's closure in the same call.

### D5 — Merge rule (**pin the comparator**)

Merge keeps, per `concept_id`, the record with the highest
`okf.SENSITIVITY_ORDER.index(new_level)`; ties resolve to the first Source in sorted order.
`okf.SENSITIVITY_ORDER` is public and correct here because `new_level` is always a canonical
level. `okf._rank` is **not** used: `okf.py:296-299` records the deliberate ADR-0003 decision
not to export it, and no call site may re-derive it.

Merge-by-max is exact, not an approximation: `combine_sensitivity(current, ·)` is monotone in
`level` (`okf.py:323`), so the winning record's already-rendered `content` is the correct final
bytes — no re-render.

### D6 — What the sweep may write (**diverges from `specs/sensitivity-backfill/spec.md:59-67`**)

`resolve_source_raises` stays byte-identical to `main.py:3389-3404`, which does **not** filter
descendants by `type`. No `type` filter is added, in the helper or in the merge step. Adding
one would break slice 1's byte-identical contract and would make backfill diverge from
`set-sensitivity` for no benefit.

The accurate claims are therefore:

1. The sweep never writes a Source **as its own root** — a root is excluded from its own
   closure (`main.py:3355-3357`).
2. A Source *can* be written when it is a genuine provenance descendant of another Source.
   That is correct semantics, not a leak. It is also **unreachable today**:
   `okf.build_source_concept` unconditionally emits `provenance: [<resource>]` (`okf.py:172`,
   called with `provenance=[resource]` at `main.py:1747`), and a raw resource path never
   normalizes to a bundle id, so no Source's provenance set can ever be a subset of a purge
   set.
3. A concept citing `{A, B}` where Source `B` lies inside Source `A`'s closure **is** written,
   because `{A, B} ⊆ closure(A)`. The spec's absolute "cites two or more Sources MUST NOT be
   written" is wrong; the correct rule is closure membership.

### D7 — Determinism and ordering

| Surface | Pinned order |
|---|---|
| Bundle snapshot | `sorted(rglob("*.md"))`, reserved names skipped (`main.py:3343-3349`) |
| Source iteration | `sorted()` by concept id |
| `provenance_closure` | already `sorted()` (`provenance.py:139`) |
| `resolve_source_raises` | sorted by `concept_id` |
| Merged preview, Phase-B writes, `_autocommit` paths | `sorted(concept_id)` |
| `find_unresolvable_provenance` | `files` iteration order, then each file's `provenance:` list order; **no dedupe**, one tuple per occurrence — byte-identical to `main.py:3369-3386` |

Warning order is currently unconstrained by the suite: the only test touching that scan is a
substring assertion (`tests/unit/cli/test_set_sensitivity.py:755-756`). Slice 1's
characterization test pins the order explicitly so it stops being accidental.

`find_unresolvable_provenance(files, *, known_extra_ids)` reproduces today's behaviour only
because `set_sensitivity_cmd` keeps passing the **target-excluding** snapshot
(`main.py:3346-3347`) together with `known_extra_ids={canonical_id}` (`main.py:3364-3366`) —
that pairing is why the target's own `provenance` is never warned about. The parameter exists
solely to preserve that pairing; it is not a general-purpose escape hatch.

### D8 — The backfill verb does not run the unresolvable-provenance scan

Every Source in the bundle cites `raw/<resource>`, which resolves to no bundle id (D6). A
bundle-wide `find_unresolvable_provenance` would therefore emit **one WARNING per Source on
every run**, including the "nothing to backfill" no-op path
(`specs/sensitivity-backfill/spec.md:128-141`) — turning a clean no-op into a wall of noise.

This is a **live defect in `set-sensitivity` today**, not a new one: with two or more Sources
in the bundle, `set-sensitivity <source> <higher>` already warns about every non-target Source.
It is invisible in the suite because `test_dangling_provenance_warns_and_never_lowers`
(`test_set_sensitivity.py:733-758`) has exactly one Source, and that Source is the target.

Decision: **`backfill-sensitivity` does not call `find_unresolvable_provenance` at all.** Its
unresolvable-provenance signal is the `dangling` lint finding, which already covers the bundle
read-only. `set_sensitivity_cmd` keeps calling it with byte-identical output. Slice 1 adds a
characterization test pinning the resource-shaped-entry behaviour so the defect is recorded
rather than silently inherited. Fixing it belongs to #232's warning-scope work, not here — no
scope expansion.

### D9 — Failure handling (#233) and the actual slice-1 guard

Phase B appends each path to a `landed: list[str]` **after** its `write_atomic` returns. The
message keeps its existing first sentence (`main.py:3483-3486`) and appends
`Already written (left over-classified, not rolled back): bundle/a.md, bundle/b.md.` — or
`No path was written.` when empty. Applies to both verbs.

**Correction**: the first sentence is *not* protected by the existing suite. No test in
`tests/unit/cli/test_set_sensitivity.py` exercises a Phase-B write failure, and the literal
`"failed while writing the set-sensitivity"` appears in zero tests. There is no inherited
guard, so the RED step must create one.

Pinned slice-1 commit order:

1. **RED** — characterization tests for `resolve_source_raises` and
   `find_unresolvable_provenance`, including warning order and resource-shaped entries
   (`tests/unit/bundle/test_provenance_source_raises.py`).
2. **GREEN** — add the helpers, rewire `set_sensitivity_cmd`. Zero edits to
   `tests/unit/cli/test_set_sensitivity.py`; its 29 tests guard the *success* paths.
3. **RED** — a new CLI test that **constructs the Phase-B partial-failure scenario** (patch
   `fsio.write_atomic` to fail on the Nth call), asserts the existing first sentence verbatim,
   **and** asserts the landed paths. Only the landed-path assertion is red; the first-sentence
   assertion exists to create the guard that the suite never had, in the same commit that is
   about to modify that message.
4. **GREEN** — landed tracking plus the appended sentence. Only this commit may touch that
   message.

## Data Flow

```
bundle/*.md ──snapshot──┬─→ resolve_source_raises(per sorted Source) ─→ merge(max rank) ─┐
                        │                                                                ▼
                        │                       preview → confirm → writes → log.md → _autocommit
                        └─→ find_unresolvable_provenance ─→ stderr WARNING (set-sensitivity ONLY)

collect_docs (single walk) ─→ [LintDoc(+sensitivity,+provenance)] ─→ provenance_closure
                                                     └─→ check_below_source_sensitivity → lint/status
```

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/bundle/provenance.py` | Modify | `provenance_closure`, `resolve_source_raises`, `find_unresolvable_provenance` |
| `src/openkos/model/okf.py` | Modify | `DescendantRaise` frozen dataclass |
| `src/openkos/cli/main.py` | Modify | Rewire `set_sensitivity_cmd`; drop `_DescendantRaise`; landed-path message; new `backfill-sensitivity`; lint/status wiring |
| `src/openkos/lint.py` | Modify | `LintDoc.sensitivity`/`.provenance`, `LintReport.below_source`/`.multi_source_uncovered`, `check_below_source_sensitivity` |
| `docs/adr/0012-sensitivity-backfill-per-source-sweep.md` | Create | Per-Source sweep + reported coverage limit |
| `docs/adr/README.md` | Modify | Index 0012 |
| `tests/unit/bundle/test_provenance_source_raises.py` | Create | Slice 1 characterization |
| `tests/unit/test_lint_below_source.py` | Create | Slice 2 pure-function tests |
| `tests/unit/cli/test_backfill_sensitivity.py` | Create | Slice 3 verb tests |

## Testing Strategy

| Layer | What | Approach |
|---|---|---|
| Unit (pure) | closure, per-Source raises, unresolvable scan incl. order + resource-shaped entries, merge-by-max | `Mapping[str, str]` fixtures, no filesystem |
| Unit (lint) | both categories; Source + foreign-derived cite is uncovered; same-Source multi-cite is covered; missing `sensitivity` under a `public` Source is flagged; unresolvable cite in neither | hand-built `LintDoc` lists |
| CLI | raise-all, never-lowers, idempotent second run (zero writes, no empty commit), `--auto` skips prompt only, non-TTY refuses, no-op line, Phase-B partial write names landed paths, `extraction_status: failed` Source still a valid root | `CliRunner` + tmp workspace |
| Regression | `tests/unit/cli/test_set_sensitivity.py` (29) unchanged and green through slice 1 | pinned |

## Threat Matrix

| Boundary | Applicability | Design response | Planned RED test |
|---|---|---|---|
| Documentation-like paths | N/A — only `bundle/**/*.md` OKF concepts, no classification/execution | — | — |
| Git repository selection | N/A — reuses `_autocommit(root, …)` unchanged, no new cwd authority | — | — |
| Commit state | **Applicable** — a sweep with zero raises must not produce an empty commit | Phase A exits before log/commit when the merged set is empty | `test_backfill_second_run_stages_nothing_and_creates_no_commit` |
| Push state | N/A — no push | — | — |
| PR commands | N/A — no PR automation | — | — |

## Migration / Rollout

No data migration. The gap is closed by an explicit operator-run sweep, never automatically
(ADR-0012). Slices ship independently; slices 2 and 3 both depend on 1, not on each other.

## Explicitly Not Changed

`find_provenance_descendants`'s conservative subset rule; `combine_sensitivity`; ADR-0009's
MVP-2/3 multi-source deferral; #232's bundle-wide warning scope, including the
resource-shaped-entry defect recorded in D8; #234's ambiguous "failed while preparing" message;
`extraction_status` (backfill is purely additive to `sensitivity` and never re-triggers
extraction).

## Open Questions

- None blocking. Fixed decisions are in `proposal.md`; spec divergences are enumerated in
  **Required Spec Amendments**.

## Required Spec Amendments

Ordered edits the delta specs need to agree with this design. The spec phase applies them; this
phase does not edit spec files.

1. **`specs/lint/spec.md:10-14`** — *Below-Source Sensitivity Scan, helper reuse.*
   Current: "The scan MUST reuse the SAME per-Source raise-resolution helper the
   `sensitivity-backfill` verb uses (`resolve_source_raises`), invoked read-only — computing
   would-be raises without ever writing a file — and MUST reuse `LintDoc`'s existing
   single-pass `collect_docs` walk".
   Replacement: "The scan MUST reuse the SAME closure algorithm and rank comparator the
   `sensitivity-backfill` verb uses (`bundle.provenance.provenance_closure` plus
   `okf.combine_sensitivity`) and MUST reuse `LintDoc`'s existing single-pass `collect_docs`
   walk; it MUST NOT introduce a new bundle walk and MUST NOT render write-ready file content."
   Why: `resolve_source_raises` returns `okf.DescendantRaise.content`, which requires the full
   file text and a metadata dict. `LintDoc` keeps `body` only (`lint.py:37`), so the mandate is
   unimplementable as written (design D2).

2. **`specs/lint/spec.md:7-9`** — *Below-source trigger condition.*
   Current: "whose `sensitivity` strictly ranks below its single citing Source's `sensitivity`".
   Replacement: "for which `okf.combine_sensitivity(descendant_sensitivity, source_sensitivity)`
   differs from the descendant's current value — the same test the sweep uses to stage a write,
   so a missing, blank, or unrecognized `sensitivity` is ranked fail-closed (ADR-0003) and is
   flagged".
   Why: `main.py:3391-3393` stages on `combine_sensitivity` inequality, not on a strict rank
   comparison. Under the narrower wording `lint` reports clean while the verb still writes
   (design D3).

3. **`specs/lint/spec.md:49-57`** — *Multi-Source Uncovered-Descendant Scan, definition.*
   Current: "any provenance descendant citing two or more Sources whose `sensitivity` ranks
   below at least one of those Sources … its detail MUST name every citing Source id".
   Replacement: "any doc with a non-empty `provenance` whose cited ids all resolve to bundle
   concepts, which is a member of no single-Source closure, and whose `sensitivity` sits
   strictly below the high-water-mark of its cited concepts' levels … its detail MUST name the
   descendant, its current level, and every cited concept id with that concept's level, and
   MUST mark the finding as not covered by `backfill-sensitivity`".
   Why: closure membership is the correct basis (design D3). The current wording misses a doc
   citing one Source plus one foreign derived concept, and requires naming "Source ids" that
   need not exist. The scenario at `:59-67` still passes; add a scenario for the
   Source-plus-foreign-derived case.

4. **`specs/lint/spec.md:49-57` (companion)** — add an explicit exclusion sentence: "A doc whose
   `provenance` cites two or more concepts that all fall inside a single Source's closure MUST
   be reported as `below-source-sensitivity`, not as `multi-source-uncovered`."
   Why: `query --save` (two-output rule) routinely writes such docs; the sweep *does* cover
   them, and reporting them as uncovered would be a false alarm (design D3).

5. **`specs/sensitivity-backfill/spec.md:59-67`** — *Multi-Source Descendants Are Skipped.*
   Current: "A derived concept whose `provenance` cites two or more Sources MUST NOT be written
   by the sweep".
   Replacement: "A derived concept that is a member of no single Source's provenance closure
   MUST NOT be written by the sweep, matching `find_provenance_descendants`'s existing per-root
   closure semantics. A concept citing two or more ids that all fall inside one Source's
   closure IS covered and MUST be raised."
   Why: `{A, B} ⊆ closure(A)` when Source `B` lies inside `A`'s closure, so the absolute MUST
   NOT contradicts the implementation the same requirement cites (design D6).

6. **`specs/sensitivity-backfill/spec.md:32` and `:118-119`** — *"MUST NOT write any Source's
   own `sensitivity` field" / "No Source's own frontmatter is written".*
   Replacement: "MUST NOT write a Source as its own closure root. A Source that is a genuine
   provenance descendant of another Source is raised like any other descendant; no `type`
   filter is applied, matching `main.py:3389-3404`."
   Why: the absolute claim is unenforced by the design and by the code it must stay
   byte-identical to. It is unreachable today only because `okf.build_source_concept` always
   emits `provenance: [<resource>]` (`okf.py:172`), which is an accident of ingest, not a
   guarantee (design D6).

7. **`specs/sensitivity-backfill/spec.md` — add to Non-Goals**: "emitting the
   unresolvable-provenance WARNING. `backfill-sensitivity` MUST NOT run
   `find_unresolvable_provenance`; every Source cites its raw `resource`, so a bundle-wide run
   would emit one WARNING per Source on every invocation, including the no-op path. That signal
   is delivered by `lint`'s existing `dangling` finding."
   Why: design D8; keeps the no-op path clean and avoids expanding into #232.

8. **`specs/status/spec.md:11-15`** — *Entry content.*
   Current: "each surfaced `multi-source-uncovered` entry MUST name the descendant and every
   citing Source id".
   Replacement: "…MUST name the descendant and every cited concept id".
   Why: consistency with amendment 3 — under closure membership an uncovered doc's provenance
   need not contain a Source (design D3).

9. **All three delta specs** — the finding kind stays spelled `below-source-sensitivity`
   everywhere; the design now matches. No spec edit required; recorded for closure.
