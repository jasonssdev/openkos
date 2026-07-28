# Design: Propagate Source Sensitivity to Derived Objects

## Technical Approach

Two write-through points, one shared rule. At **creation time**, `ingest` reads the
built Source document's own `sensitivity` back out and stamps derived objects with
that value. At **set time**, `set-sensitivity` on a `type: Source` concept resolves
its provenance descendants and raises each via `okf.combine_sensitivity` inside the
existing Phase A / Phase B transaction. Read-time resolution is not touched:
`sensitivity-aware-llm` keeps reading each document's own stored field.

## Architecture Decisions

### Decision: Read the Source document back, and split the two `sensitivity` roles

**Choice**: build `concept_content` first (`cli/main.py:1654`), then
`source_sensitivity = okf.load_frontmatter(concept_content)[0]["sensitivity"]`, and
change `_stage_derived_objects`' single `sensitivity` parameter into **two**:
`workspace_floor` (still `cfg.default_sensitivity`) and `stamp_sensitivity` (the
read-back Source value).

**Alternatives considered**: (a) thread a local `source_sensitivity =
cfg.default_sensitivity` variable; (b) keep one parameter and pass the Source value.

**Rationale**: (a) is still the same constant — the edge stays coincidental and no
test can distinguish it. Reading back the built artifact makes the derived value
provably *originate from the Source document*, so a test that forges a Source
sensitivity different from the config makes the two worlds distinguishable. (b) is
unsafe: the parameter is used for **two different things** — the fail-closed
`extract` gate at `cli/main.py:1262` and the `build_concept` stamp at `:1357`.
`sensitivity-aware-llm` Requirement 4 pins that gate to the *workspace floor*;
feeding it a per-Source value would silently rewrite a requirement this change
declares unchanged. Splitting the parameter keeps Req 4 literally true.

### Decision: Reuse `find_provenance_descendants` unchanged

**Choice**: call `bundle_provenance.find_provenance_descendants(files,
root_ids={canonical_id})` (`bundle/provenance.py:34`) — already public, already
imported in `cli/main.py`, already used by `forget`/`purge` (`:2065`, `:2566`).
Descendant set = returned list minus the root id. Bundle snapshot is built with the
same `rglob`-into-`dict[rel_path, text]` pattern `forget` uses at
`cli/main.py:2555-2563`.

**Alternatives considered**: a narrower `find_direct_descendants` variant; the
`sqlite_graph` `derived_from` projection.

**Rationale**: no new primitive, no new tests for the closure itself. The graph
projection is body-link-derived and rebuilt per run — not a durable edge. Its
subset rule (a candidate joins only if its **entire** provenance is inside the set)
is conservative here: a derived object citing this Source *and* another is not
raised. That is the multi-source high-water-mark case the ingestion spec defers to
MVP-2/3, and today `build_concept` always writes single-entry provenance
(`cli/main.py:1356`), so the case cannot yet arise. Cost: one full-bundle read plus
one frontmatter parse per file, on a rare human verb only — and only when the
target is a Source.

**Unresolvable provenance**: after the closure, scan the parsed provenance map for
entries naming an id with no file in the snapshot. Each one emits a stderr WARNING
naming the dangling entry; the citing object is never raised and never lowered
(fail closed). Files whose frontmatter fails to parse are skipped by the helper by
construction and are reported the same way.

### Decision: Descendants are written BEFORE the target concept

**Choice**: Phase B order is `descendant writes → target concept → log.md → one
`_autocommit``.

**Rationale**: partial-write behavior must fail toward *more* restrictive. If the
run dies after the descendants, the bundle is over-classified (safe). Writing the
Source first and failing would leave exactly bug #219 — under-classified derived
objects. There is no cross-file rollback; `fsio.write_atomic` is per-file, matching
`relate`/`merge`. The existing "failed while writing" handler (`:3226`) is reused
and extended to name which paths did land.

### Decision: Source detection is `type: Source` frontmatter, not path

**Choice**: `metadata.get("type") == "Source"` — the value `okf.build_source_concept`
writes at `model/okf.py:115`.

**Alternatives considered**: `canonical_id.startswith("sources/")`.

**Rationale**: `type` is the OKF field of record; the path is a convention
`_resolve_concept_path` does not enforce and `merge` does not preserve. A non-Source
target skips the bundle scan entirely and behaves byte-identically to today,
including the original honesty message.

### Decision: Idempotence short-circuit stays first, unchanged

**Choice**: `current == level` still returns early (`:3137`) with no descendant work.

**Rationale**: that unstripped boundary is deliberately pinned by test (commit
16d22b0). Repairing descendants of an already-correct Source is the deferred bulk
backfill, not this change.

### Decision: `combine_sensitivity` for descendants only

**Choice**: per descendant, `new = okf.combine_sensitivity(descendant_current,
level)`; stage only when `new != descendant_current`. The target concept's own
assignment is untouched and may still lower.

**Rationale**: ADR-0008 rejected `combine_sensitivity` for the **named concept**
because a human assignment must not be silently monotonic. A descendant value is
machine-derived — precisely ADR-0003's domain. Raise-only falls out for free: on a
lowering, `combine` returns the descendant's existing value, so the staged set is
empty with no direction special-casing.

## Data Flow

    ingest:
      build_source_concept ──→ concept_content ──(load_frontmatter)──→ source_sensitivity
                                                                             │
      cfg.default_sensitivity ──→ workspace_floor ─┐                         │
                                                   ▼                         ▼
                                _stage_derived_objects(workspace_floor=…, stamp_sensitivity=…)
                                   gate @1262 ◀────┘                         └──▶ build_concept @1357

    set-sensitivity (Phase A, no writes):
      resolve concept ──→ type == "Source"? ──no──▶ today's single-file path
                                │yes
                                ▼
      bundle snapshot ──→ find_provenance_descendants ──→ descendants (− root)
                                │                              │
                                │                              ▼
                                │        per id: combine_sensitivity(current, level)
                                │                              │ strict raise only
                                ▼                              ▼
                          dangling-provenance WARN        write set + preview lines
                                                               │
    (Phase B) descendants ──→ target concept ──→ log.md ──→ _autocommit(all paths)

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/cli/main.py:1653-1679` | Modify | Read back Source `sensitivity`; pass split params |
| `src/openkos/cli/main.py:1178-1367` | Modify | `sensitivity` → `workspace_floor` + `stamp_sensitivity`; docstring |
| `src/openkos/cli/main.py:3043-3245` | Modify | Source branch, descendant staging, preview, ordered writes, messages, `--help` |
| `src/openkos/bundle/provenance.py` | Unchanged | Reused as-is; no signature change |
| `openspec/specs/ingestion/spec.md` | Modify | Inheritance requirement backed by a real read (sdd-spec owns) |
| `openspec/specs/sensitivity-config/spec.md` | Modify | Scope requirement narrows (sdd-spec owns) |
| `docs/adr/0009-source-sensitivity-propagation.md` | Create | Supersedes ADR-0008's scope statement |
| `docs/adr/README.md` | Modify | One index row, status `Proposed` |
| `tests/unit/cli/test_ingest.py` | Modify | Distinguishing inheritance test |
| `tests/unit/cli/test_set_sensitivity.py` | Modify | Invert honesty tests; new propagation tests |

## Interfaces / Contracts

```python
def _stage_derived_objects(
    *,
    raw_content: str | None,
    source_title: str,
    source_slug: str,
    workspace_floor: str,   # cfg.default_sensitivity — gates `extract` (Req 4)
    stamp_sensitivity: str, # the Source document's own resolved value
    timestamp: str,
    bundle_dir: Path,
    llm: LLMBackend,
    include_confidential: bool = False,
) -> list[_DerivedPlan]: ...
```

```python
# Phase A staging record, local to set_sensitivity_cmd
@dataclass(frozen=True)
class _DescendantRaise:
    concept_id: str
    path: Path
    current: object
    new_level: str
    content: str  # already re-rendered via okf.dump_frontmatter
```

## ADR-0009 content plan

- **Title**: "Source sensitivity propagates to provenance descendants, raise-only".
  Status `Proposed`, date, index row added. **ADR-0008 is not edited.**
- **Context**: ADR-0008 recorded `set-sensitivity` as touching exactly one concept
  ("no sibling and no derived object"), and rejected `combine_sensitivity` for the
  verb. Issue #219 shows the ingestion spec (`spec.md:414-427`) already claims
  inheritance that no code backs; derived objects go stale and stay LLM-reachable.
- **Superseded, precisely**: only ADR-0008's *scope* sentence — the single-concept
  write set. Its downgrade gate, `--allow-downgrade` contract, and
  `sensitivity_direction` rule remain in force verbatim.
- **Decision**: the write set becomes the named concept plus, when it is a
  `type: Source`, its provenance descendants, each combined raise-only via ADR-0003's
  `combine_sensitivity`. ADR-0008's rejection of `combine_sensitivity` is preserved
  for the *human-assigned* target value; it now applies to machine-derived
  descendant values, which is ADR-0003's own domain. Unresolvable provenance warns
  and is excluded.
- **Consequences** — easier: the ingestion spec's inheritance claim becomes true; a
  privacy correction is one verb. Harder: `set-sensitivity` becomes a multi-file
  write and a full-bundle read for Sources; already-stale descendants of an
  unchanged Source still need bulk backfill; merge-orphaned provenance silently
  hides a descendant (follow-up issue).
- **Alternatives considered**: read-time computed sensitivity (rewrites
  `sensitivity-aware-llm` Req 1, 8 requirements / 6 call sites, leaves files
  mislabeled on disk); creation-time only (today's coincidence); cascading
  downgrades (silent declassification); a new reverse index (`find_provenance_
  descendants` already exists).

## Testing Strategy (Strict TDD — RED first, `uv run pytest`)

| Layer | RED test | Approach |
|---|---|---|
| Unit / ingest | `test_derived_object_inherits_source_document_value_not_config` | config `default_sensitivity: public`; monkeypatch `main.okf.build_source_concept` to return a doc stamped `confidential`; assert derived == `confidential`. Fails today (gets `public`). This is the test that makes the two worlds distinguishable. |
| Unit / ingest | `test_extract_gate_still_reads_workspace_floor` | config floor `confidential`, forged Source `public`; assert extraction is skipped and `llm.chat` never called — pins Req 4 against the parameter split. |
| Unit / ingest | `test_derived_object_inherits_source_sensitivity` (`:1824`) | Kept, retitled as the same-value baseline; it alone can no longer prove inheritance. |
| Unit / set | `test_raising_source_raises_derived_objects` | ingest → `set-sensitivity <source> confidential --auto` → assert derived file's stored `sensitivity`. |
| Unit / set | `test_lowering_source_never_lowers_derived` | raise both, then lower the Source with `--allow-downgrade`; derived unchanged. |
| Unit / set | `test_non_source_concept_touches_only_itself` | target a derived object; assert byte-identical single-file behavior and no bundle scan effect. |
| Unit / set | `test_preview_lists_every_derived_raise` | non-TTY `--auto`; assert one preview line per descendant. |
| Unit / set | `test_dangling_provenance_warns_and_never_lowers` | hand-write a concept citing a missing source; assert WARNING on stderr, no write. |
| Unit / set | `test_descendants_written_before_target_on_failure` | make the target write fail; assert descendants already raised (fail-closed ordering). |
| Unit / set | `test_commit_stages_every_changed_path` | assert `_autocommit` path list includes each descendant. |
| Unit / set | `test_success_message_contains_honesty_line` (`:355`) | **Inverted, not deleted**: on a Source it must now name the propagated objects; a companion `test_non_source_success_message_keeps_only_this_concept_line` keeps the original assertion for the non-Source path. |
| Unit / set | `test_help_contains_honesty_line` (`:373`) | Reworded: `--help` must state the bounded new scope (named concept + Source provenance descendants, raise-only). |

Branch coverage `fail_under = 90` — the new Source branch, the empty-descendant
branch, and the dangling-provenance branch each need a test, listed above.

## Threat Matrix

| Boundary | Applicability | Design response | Planned RED tests |
|---|---|---|---|
| Documentation-like paths | N/A — no file classification or execution; only `bundle/**.md` frontmatter is rewritten | — | — |
| Git repository selection | N/A — `_autocommit` (`cli/main.py:407`) resolves the repo via existing `vcs_git.repo_root(root)`, unchanged | — | — |
| Commit state | **Applicable** — the scoped `git add -- <paths>` set grows from 2 paths to 2 + N | Paths stay workspace-relative POSIX, built only from disk-discovered descendant ids; never `-A`/`-a`; unrelated dirty files never swept in | `test_commit_stages_every_changed_path`; a case asserting an unrelated dirty file stays unstaged |
| Push state | N/A — no push is performed | — | — |
| PR commands | N/A — no PR automation | — | — |

## Migration / Rollout

No migration. No bulk backfill: existing bundles are corrected at the next
`set-sensitivity` on their Source. Rollback is a branch revert; propagation only
raised values, so no data is lost.

## Slice forecast (delivery_strategy `auto-chain`, budget 800)

| Slice | Contents | Prod | Tests | Specs/docs | Total |
|---|---|---|---|---|---|
| 1 | Ingest read-back + parameter split + ingestion spec delta | ~40 | ~70 | ~40 | **~150** |
| 2 | Set-time propagation, preview/messages/`--help`, sensitivity-config spec delta, ADR-0009 + index row | ~150 | ~280 | ~160 | **~590** |

Confirmed: the proposal's two-way split holds. Total ~740, under the 800 budget;
slice 2 alone is well over the default 400, so it must ship as its own PR chained
on slice 1. If slice 2 overruns ~700, split it into 2a (engine + tests) and 2b
(spec delta + ADR + docs) — the ADR and spec are additive and independently
reviewable.

## Open Questions

- [ ] None blocking. Deferred by the proposal and re-affirmed here: merge-orphaned
      provenance (fail closed only), bulk backfill, multi-source high-water-mark.
