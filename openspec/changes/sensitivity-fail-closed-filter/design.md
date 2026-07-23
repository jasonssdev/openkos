# Design: Sensitivity Fail-Closed Filter (Gap #8 · S3)

## Technical Approach

Clone the S1 `lifecycle.py` shape for a new security axis: one shared fail-closed
predicate `sensitive_concept_ids(bundle_dir, *, threshold)` computes the blocked
id set in a single `okf._iter_docs` walk, and the six `llm.chat` sites filter
against it before sending. Four sites reuse `lifecycle.filter_hits` unchanged
(it is already set/axis-agnostic); two divergent seams get small plumbing; extract
gates on the workspace floor. Predicate + escape land in S3a, the divergent seams
in S3b, the hygiene fold-in (#1606/#1592) in S3c.

## Architecture Decisions

### Decision: Predicate home — new `src/openkos/sensitivity.py` leaf

| Option | Tradeoff | Decision |
|---|---|---|
| Extend `lifecycle.py` | Shares the `_iter_docs` walk + fail-safe shape, but dilutes lifecycle's tight status/R2 docstring; mixes a security axis into a retrieval-status leaf | Rejected |
| New `sensitivity.py` leaf | Distinct axis, own fail-CLOSED contract; canonical-layer leaf importing only `okf` + stdlib (same no-cycle rule); reuses `lifecycle.filter_hits` (import direction `sensitivity` → nothing new; seams import both leaves) | **Chosen** |

Rationale: sensitivity fails **closed** (block on doubt) whereas lifecycle fails
**safe** (skip on doubt); co-locating two opposite fail-directions in one module
invites future edits to cross the invariant. `filter_hits[H]` stays in
`lifecycle.py`, reused verbatim.

### Decision: Predicate semantics — stricter than `okf._rank` on absent/blank

`okf._rank(None)` and `_rank("")` return **private**, not confidential. The locked
rule "missing → confidential → blocked" therefore CANNOT be delegated to `_rank`
alone. The predicate fails closed explicitly:

```python
def sensitive_concept_ids(bundle_dir: Path, *, threshold: str = "confidential") -> frozenset[str]:
    floor = okf._rank(threshold)
    blocked: set[str] = set()
    for scan in okf._iter_docs(bundle_dir):
        cid = scan.path.relative_to(bundle_dir).with_suffix("").as_posix()
        if scan.read_error is not None or scan.parse_error is not None:
            blocked.add(cid); continue            # unreadable/unparseable → blocked
        raw = (scan.metadata or {}).get("sensitivity")
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            blocked.add(cid); continue            # absent/blank → blocked
        if okf._rank(raw) >= floor:               # unknown/non-str → _rank=confidential
            blocked.add(cid)
    return frozenset(blocked)
```

`threshold="confidential"` (rank 2) blocks only confidential + `_rank`'s
confidential-fallbacks (unknown-str, non-str); `private`(1)/`public`(0) are sent.
Predicate is stricter than `_rank` on absent/blank *by design* — a security signal
absent must fail closed, whereas `_rank`'s private-default serves merge's floor
combine. `filter_hits` is reused **unchanged** (param name `deprecated` is just a
`frozenset[str]`).

### Decision: `--include-confidential` mirrors `--include-deprecated`

Every verb that today carries `--include-deprecated` gains a sibling
`--include-confidential` (zero-cost: skip the walk when `True`). `ingest` gains it
to force extraction past a confidential floor.

## Data Flow

```
okf._iter_docs(bundle_dir) ──→ sensitivity.sensitive_concept_ids ──→ frozenset[str]
                                                                          │
   query/contradictions/adjudicate/suggest-relations ──lifecycle.filter_hits──┘
   suggest-volatility (LintDoc.sensitivity) ── drop blocked types
   extract ── okf._rank(cfg.default_sensitivity) gate ──→ skip llm.chat
```

## File Changes

| File | Action | Change |
|---|---|---|
| `src/openkos/sensitivity.py` | Create | `sensitive_concept_ids(bundle_dir,*,threshold)` |
| `retrieval/answer.py` | Modify | hit seam (:368-381) filters `sensitive` too; `_assemble_context` (:161-183) guarded re-read skips blocked cid (defense-in-depth) |
| `resolution/contradiction.py` | Modify | :408-413 compute predicate, pass to `_candidate_pairs`; drop pairs touching a blocked id |
| `resolution/adjudication.py` | Modify | `run` (:236) drops blocked `member_ids` before `_load_members`/:251 |
| `resolution/edge_typing.py` | Modify | `suggest_relations` (:267) drops edges whose endpoint is blocked before :250 |
| `resolution/volatility_typing.py` | Modify | filter blocked docs from `collect_docs` output before `_sample_bodies_by_type` (:207) |
| `lint.py` | Modify | thread `sensitivity` field through `LintDoc` (:113-123) |
| `extraction/concept.py` + `cli/main.py` | Modify | floor gate in `_stage_derived_objects` after :316, before :324 |
| `src/openkos/llm/parsing.py` | Create | `extract_json_object` + `extract_json_items` (#1606) |
| `config.py` | Modify | :375 `except (yaml.YAMLError, TypeError)` (#1592) |

### Per-seam notes

- **query**: reuse `filter_hits` at the hit seam beside the existing `deprecated`
  filter (fts/vec/graph); `_assemble_context` re-reads `sensitivity` and skips a
  blocked cid — redundant post-filter, defense-in-depth.
- **contradictions/adjudicate/suggest-relations**: predicate computed once per
  verb; blocked ids removed from the candidate pair/member/edge set so the guarded
  re-read helpers never build a prompt from confidential bodies.
- **suggest-volatility**: `LintDoc` currently DROPS sensitivity (lint.py:119-121);
  add `sensitivity: str = str(metadata.get("sensitivity",""))`, then drop blocked
  docs before type-sampling. A type whose docs are all blocked yields no suggestion.
- **extract** (outlier): `_stage_derived_objects` already receives
  `sensitivity=cfg.default_sensitivity`; if `okf._rank(sensitivity) >= _rank("confidential")`
  → emit the existing "keeping the Source only" degrade and return `[]` WITHOUT
  calling `extract_concept` → no `llm.chat`. `--include-confidential` bypasses.

### #1606 hygiene (S3c)

New `src/openkos/llm/parsing.py` exposes **public** `extract_json_object` +
`extract_json_items` (public names resolve the original "no cross-import of
`_`-prefixed symbols / design D4" note that justified the clones). Migrate 5 call
sites, deleting their local clones: `adjudication.py:131-163`,
`edge_typing.py:169-201`, `volatility_typing.py:132-164`,
`contradiction.py:250-290`, and the list variant `extraction/concept.py:166-220`.

## Interfaces / Contracts

`sensitive_concept_ids(bundle_dir: Path, *, threshold: str = "confidential") -> frozenset[str]`
— fail-closed, never raises. `filter_hits` reused as-is.

## Slice / PR Structure (feature-branch-chain, 800-line budget)

| Slice | Scope | Est. lines | Chain |
|---|---|---|---|
| **S3a** | `sensitivity.py` + 4 S1-pattern seams + `--include-confidential` + predicate tests | ~330 | root of chain |
| **S3b** | `LintDoc.sensitivity` + volatility seam + extract floor gate + `_assemble_context` defense-in-depth + seam tests | ~300 | on S3a |
| **S3c** | `llm/parsing.py` + migrate 5 sites + #1592 + tests | ~250 | on S3b |

S3a+S3b together (~630) fit under 800 and MAY co-merge if reviewer prefers one
spine PR; S3c stays a **separate** slice (pure refactor+bugfix, isolable rollback)
and must chain after. No single slice approaches 800; chain keeps each reviewable.

## Testing Strategy (Strict TDD, `uv run pytest`)

| Layer | What | Approach |
|---|---|---|
| Unit predicate | `sensitive_concept_ids` | mirror `tests/unit/test_lifecycle.py::_write_doc`: tmp_path `.md` fixtures; assert confidential/absent/blank/malformed/unreadable blocked, private/public sent |
| Unit seams | each of 6 sites | inject fake `LLMBackend` + real `.md` fixtures; spy `llm.chat` NOT called for confidential; called for private/public |
| Unit escape | `--include-confidential` | confidential fixture + flag → `llm.chat` called |
| Unit #1606 | `llm/parsing.py` | new `tests/unit/llm/test_parsing.py`: object + list variants, fence/brace recovery, non-str fail-closed |
| Unit #1592 | `read_config` | `tests/unit/test_config.py`: YAML with unhashable complex key → `ValueError` not `TypeError` |

RED tests per slice: **S3a** predicate matrix + query/contradictions/adjudicate/
suggest-relations spy tests + escape. **S3b** volatility `LintDoc.sensitivity`,
extract floor-gate short-circuit, `_assemble_context` skip. **S3c** `test_parsing.py`
+ config TypeError test.

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file
classification, or process-integration boundary. The `llm.chat` send is an
in-process backend call, not a shell/subprocess; the sole security property is the
read-side fail-closed exclusion covered above.

## Migration / Rollout

No migration. Additive read-side filter; defaulting the predicate to an empty set
restores today's sensitivity-blind sends. `sensitivity` frontmatter stays written
as before.

## Open Questions

- [ ] None blocking. (Threshold and extract seam locked; predicate-vs-`_rank`
  absent/blank strictness resolved above as a deliberate fail-closed choice.)

## Known follow-ups (harden before cloud/export slice)

Recorded by the correction batch (post-4R-review) that fixed R1/R2/R3/R4 —
NOT implemented now, deliberately deferred:

(a) **Repeated full-bundle walk per invocation (perf).** Every `llm.chat`
seam that filters both axes (`contradictions`, `adjudicate`, `query`) runs
TWO separate whole-bundle `okf._iter_docs` walks per invocation —
`lifecycle.deprecated_concept_ids` and `sensitivity.sensitive_concept_ids`
each do their own pass. `suggest-volatility` also re-walks via
`lint.collect_docs` on top of that. A future optimization could share ONE
`_iter_docs` pass across both predicates (and `lint.collect_docs`), each
consuming the same `DocScan` stream, rather than each predicate walking the
bundle independently. Deferred: no measured perf problem yet at MVP-3 scale,
and correctness (fail-closed on every axis) took priority over this slice's
correction budget.

(b) **Directory-walk silent-drop observability.** The walk-bypass leak (R4)
is now mitigated for the `query`/`answer()` seam specifically by FIX 2's
independent per-doc re-check at `_assemble_context`'s actual send point —
but the OTHER five `llm.chat` seams (`contradictions`, `adjudicate`,
`suggest-relations`, `suggest-volatility`, and the `ingest` extract floor
gate) all still derive their candidate sets EXCLUSIVELY from the same
walk-based predicates, with no independent re-check at their own send
points, because (unlike `query`) none of them re-reads a doc by direct path
outside the walk's own candidate set — a walk-invisible doc is simply never
a candidate for them, so there is no second read to re-check. A bundle-wide
observability signal (surfacing `okf._walk_errors`, e.g. as a stderr notice
on every read command whenever the current run's walk hit an unlistable
subtree) remains a genuine follow-up before the cloud/export slice, so an
operator can at least detect "this bundle has a subtree the walk cannot
see" rather than the silence being indistinguishable from "the bundle has
no such subtree at all."
