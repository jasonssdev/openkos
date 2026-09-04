## Apply Progress: ingest-application-service — Slice 1 + Slice 2 + Slice 3 ALL COMPLETE (26/26 + 18/18 = 44/44 assigned tasks)

### Slice 1 (PR 1) — merged as commit 7ec516b (#940)
Phase 0 (boundary verification), Phase 1 (foundation — module + generalized
layering guard), Phase 2 (Slice 1 gate). `application/ingest.py` seeded with
`DerivedPlan` + 3 collision helpers. Full detail in observation #3141's
first revision (superseded content, still readable via mem history).

### Slice 2 (PR 2) — merged as commit e5a7682 (#941)
Phases 3-7 (Typed contracts, Adapter wiring, Test migration, Byte-identity
proof, Slice 2 gate). Full detail in observation #3141's second revision.

### Slice 3 (PR 3) — committed as `a9bee55` on branch `feat/918-ingest-service-slice3`, NOT pushed/PR'd (orchestrator opens the PR)

Scope executed: Phases 8-14 (`converged_reingest`, `compose_source_document`,
`compose_catalog_update`, adapter wiring, doc repointing, byte-identity
extension, Slice 3 gate, documentation). ALL 18 tasks complete. This is the
FINAL slice — the change's full task list (44 tasks across 3 slices) is now
100% done.

### Mode: Strict TDD

### Completed Tasks (Slice 3)
- [x] 8.1-8.3: RED→GREEN `ConvergedReingest` + `converged_reingest(concept_text,
  *, re_extract) -> ConvergedReingest | None` in `application/ingest.py`.
  Moved `_extraction_retry_due`→`extraction_retry_due` and
  `_carried_extraction_notice`→`carried_extraction_notice` (underscore
  dropped, matching `stage_derived_objects`'s public-service naming
  convention). `_reingest_will_skip` repointed to
  `application_ingest.extraction_retry_due`. Adapter's #773 mid-region
  `return` became a `converged_reingest` call; verbatim disclosure string
  and `_SingleIngestOutcome(...)` exit path preserved, proven byte-identical
  by the new `converged_reingest_773` golden AND a live Ollama smoke test.
  7 application-layer tests (RED-first + 3 triangulation cases: judge-notice
  fall-through, `--re-extract` fall-through, clean-convergence empty-tuple).
- [x] 9.1-9.2: `SourceDocumentPlan` + `compose_source_document(...)`. Moved
  title/description derivation, the #229 sensitivity high-water-mark
  resolution, and the on-disk title read-back out of `_ingest_single`.
  6 application-layer tests incl. binary-source (`raw_content=None`)
  triangulation and a malformed-prior-frontmatter `ValueError` triangulation
  (genuinely reachable — `_read_source_sensitivity`'s broad `except
  Exception` catches `yaml.YAMLError`, unlike `converged_reingest`'s
  narrower `except ValueError`, see Deviations #3 below).
- [x] 10.1-10.2: `CatalogUpdate` + `compose_catalog_update(...)`. Owns the
  conditional Source re-render (skip_reason/notices) and the derived-plans
  index/log loop incl. disambiguation audit bullet. 6 application-layer
  tests incl. a multi-plan triangulation (one disambiguated + one ordinary
  plan in the same loop, proving the `if` is per-plan not all-or-nothing).
- [x] 11.1-11.2: Adapter wiring complete — `compose_source_document`,
  `converged_reingest`, `stage_derived_objects`, `compose_catalog_update`
  called in the documented order; write/guard/preview/confirm shell
  unchanged. `_chat_client` hoist was ALREADY done by Slice 2 (its deviation
  #4) — confirmed still correctly ordered, no further move needed. Docstring
  repoint: `lint.py` (2 sites), `extraction/evidence.py` (1), `model/okf.py`
  (1) → `application.ingest.extraction_retry_due`/`carried_extraction_notice`;
  `rg` verify returns 0 matches. Also swept 12 stale `main.py` prose refs +
  2 `config.py` refs to the OLD private names → `application_ingest.*`
  (2 genuinely historical narration lines in `main.py` deliberately left
  unchanged — they describe the PAST correctly). Deleted now-dead
  `main._read_source_sensitivity`/`_read_source_title` (zero remaining
  callers after the move).
- [x] 12.1-12.2: Extended characterization goldens with 4 new scenarios
  (`converged_reingest_773`, `empty_slug_lost_in_staging`,
  `already_exists_create_only`, `raw_immutability_refusal` as negative
  control) generated on pre-Slice-3 tree (`e5a7682`) via `git worktree add`.
  **Repeated Slice 2's exact v1 mistake on the first attempt**: hand-rolled
  a `_FakeLLM` instead of importing the REAL one, causing (a) a crash
  (`'str' object has no attribute 'is_local'` — `locality` needs the real
  `LOCAL_BACKEND_LOCALITY` object, not a bare string) then (b) a false
  golden mismatch (missing "embed() not implemented" advisory lines).
  Fixed by importing `_init_workspace`/`_patch_llm`/`_concept_reply`/
  `runner` DIRECTLY from the worktree's `tests.unit.cli.test_ingest`
  module. All 10 goldens (6 Slice-2 + 4 Slice-3) verified in BOTH
  environments (default git config and `GIT_CONFIG_GLOBAL=/dev/null
  GIT_CONFIG_SYSTEM=/dev/null`). Falsification: mutated
  `"unchanged"`→`"unchangedX"` in the #773 disclosure line, purged
  `__pycache__`, confirmed RED, reverted with exact inverse replace,
  purged again, confirmed GREEN, `git diff --stat` showed no residual
  mutation.
- [x] 13.1-13.4: Slice 3 gate. `tests/unit/cli/test_ingest.py`: 322 passed
  (unchanged from Slice 2 baseline — 2 direct-call sites for
  `_carried_extraction_notice` repointed to
  `application_ingest.carried_extraction_notice`). `tests/unit/application`:
  78 passed, `ingest.py` 96% branch coverage (up from 93%/96.98% baseline;
  38→? — actually 36 Slice-1/2 tests + 12 new Slice-3 tests incl.
  triangulation = 78 total in the file across all 3 slices), gate requires
  90%, reached. Two branches remain genuinely UNREACHABLE (not a gap I
  introduced, see Deviations #4): `converged_reingest`'s
  `except ValueError:` (yaml.YAMLError on malformed input is NOT a
  ValueError subclass — confirmed via `issubclass(yaml.parser.ParserError,
  ValueError) == False`) and `_read_source_title`'s `except Exception:`
  (unreachable because `_read_source_sensitivity` always runs first on the
  SAME text and raises first for identical malformed input). Whole-repo:
  `uv run pytest` 6019 passed/1 skipped (307.76s); `ruff check .` clean;
  `ruff format --check .` clean (291 files); `mypy .` clean (291 source
  files). Live smoke test against real Ollama (qwen3:8b/bge-m3): fresh
  ingest extracted 1 Concept; a SECOND identical ingest hit `converged_
  reingest`'s #773 short-circuit live, printing the EXACT golden-pinned
  disclosure string; a THIRD `--re-extract` ingest hit the `already-exists`
  create-only drop; `openkos ingest <directory>` (batch path — a directory
  `src`, NOT a `--batch` flag; that literal flag does not exist, task
  13.4's wording was imprecise) ingested successfully, confirming
  `_ingest_batch`'s unchanged body still works end to end.
- [x] 14.1-14.3: `docs/adr/0018-...md` reads `status: Accepted`, NOT
  `Proposed` as task 14.1 assumed — flipped by the PRIOR unrelated `docs(cli):
  archive the query application service change (#936)` commit, because
  ADR-0018 is SHARED across both the query and ingest bounded-context
  slices; confirmed correct and pre-existing, left untouched (a task
  assumption that turned out stale, not a defect). `specs/ingestion/spec.md`
  Purpose delta confirmed unedited (deferred to archive-time merge). #918
  confirmed OPEN via `gh issue view 918`; every commit (all 3 slices) used
  `Refs #918`, never `Closes`/`Fixes`/`Resolves`.

### Files Changed (Slice 3)
| File | Action | What Was Done |
|------|--------|----------------|
| `src/openkos/application/ingest.py` | Modified | +437/-1: added `ConvergedReingest`, `converged_reingest`, `extraction_retry_due`, `carried_extraction_notice`, `SourceDocumentPlan`, `compose_source_document`, `CatalogUpdate`, `compose_catalog_update`, `_read_source_sensitivity`/`_read_source_title` (ported, string-identifier variant); new imports `Mapping`, `date`, `source_title`, `bundle.log` |
| `src/openkos/cli/main.py` | Modified | +108/-359 (net -251): `_ingest_single` rewritten (title/description/sensitivity resolution, #773 gate, catalog composition all delegate to the service); deleted dead `_extraction_retry_due`/`_carried_extraction_notice`/`_read_source_sensitivity`/`_read_source_title`; 12 docstring/comment repoints |
| `src/openkos/config.py`, `lint.py`, `extraction/evidence.py`, `model/okf.py` | Modified | 4 docstring repoints total (2+1+1) |
| `tests/unit/application/test_ingest.py` | Modified | +380/-2: 24 new Slice-3 tests |
| `tests/unit/cli/test_ingest.py` | Modified | +12/-6: 2 direct-call sites repointed to `application_ingest.carried_extraction_notice`, plus ruff-format wrapping |
| `tests/unit/cli/fixtures/ingest_characterization_goldens.json` | Modified | +20/-0: 4 new scenarios merged in |
| `tests/unit/cli/test_ingest_characterization.py` | Modified | +98/-9: 4 new test functions + module docstring update |

Real changed lines (`git diff --numstat` on the commit, all 11 files):
1082 additions + 401 deletions = 1483 total — above the design's 700-1,000
estimate but well within the session's 2,000-line PR budget override.

### TDD Cycle Evidence (Slice 3)
| Task | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|---|---|---|---|---|---|---|---|
| 8.1-8.3 | `tests/unit/application/test_ingest.py` | Unit | ✅ 36/36 pre-existing | ✅ collection-time `AttributeError` | ✅ 7 pass | ✅ 3 extra cases (judge-notice, re-extract, clean-convergence) | ➖ none needed |
| 9.1-9.2 | `tests/unit/application/test_ingest.py` | Unit | ✅ (cumulative) | ✅ | ✅ 6 pass | ✅ 2 extra (binary source, malformed frontmatter) | ➖ none needed |
| 10.1-10.2 | `tests/unit/application/test_ingest.py` | Unit | ✅ (cumulative) | ✅ | ✅ 6 pass | ✅ 3 extra (healthy-reuse, regenerate-dedup, multi-plan) | ➖ none needed |
| 11.1-11.2 | `tests/unit/cli/test_ingest.py` (322 as oracle) | Integration/CLI | ✅ 322/322 baseline | N/A (adapter wiring) | ✅ 322 pass | ✅ live smoke (3 real scenarios) | ✅ deleted 2 dead functions |
| 12.1-12.2 | `tests/unit/cli/test_ingest_characterization.py` | Characterization | ✅ | ✅ (1 falsification cycle went RED) | ✅ 10/10 goldens match, both env | ➖ 4-scenario subset per design's matrix, not exhaustive | ➖ none needed |

### Deviations from Design
1. **`SourceDocumentPlan` carries two fields beyond design's literal
   Interfaces/Contracts list**: `raw_content: str | None` and
   `origin_key: str | None`. Design's `compose_catalog_update` signature has
   no `raw_content`/`origin_key` parameters, yet it must rebuild the Source
   document via `okf.build_source_concept` when `skip_reason`/`notices` are
   set (design: "owns the conditional re-render"), and that rebuild needs
   both. Necessary, not optional — flagged honestly per design's own
   "the search method matters more than the count" spirit.
2. **`origin_key: str` in design's signature → `str | None` in the actual
   implementation**: `_RawDestination.origin_key` (the adapter's real value)
   is documented as "possibly `None`" in its own docstring; mypy strict
   would reject passing `str | None` to a param typed `str`. A mechanical,
   necessary correction, not a design deviation in substance.
3. **`_read_source_sensitivity`/`_read_source_title` error messages changed
   wording**: the originals named the concept file's `Path` (e.g.
   `'bundle/sources/notes.md'`); the ported versions name
   `source_display_path` (e.g. `'notes.txt'`, the ORIGINAL external path)
   because `application/ingest.py` never holds a `Path` to the concept file
   (D2). This means a malformed-prior-frontmatter refusal is NOT
   byte-identical pre/post move — discovered while generating goldens (my
   first attempt at a "malformed_prior_source_refusal" scenario proved this
   wording literally cannot match, so it was dropped from the golden matrix
   and replaced with `raw_immutability_refusal`, a refusal genuinely
   untouched by the move). No existing test pinned the old wording (0
   matches via `grep` before this slice), so nothing broke; the new wording
   is exercised by a dedicated application-layer unit test instead.
4. **Two branches are provably unreachable, not merely uncovered**:
   `converged_reingest`'s `except ValueError:` never fires because
   malformed YAML raises `yaml.parser.ParserError` (a `yaml.YAMLError`
   subclass), which is NOT a `ValueError` subclass
   (`issubclass(yaml.parser.ParserError, ValueError) is False`, confirmed
   directly) — this is INHERITED, byte-identical behavior from the
   pre-move `_ingest_single`'s own `except ValueError:` at the #773 gate,
   not a regression. `_read_source_title`'s `except Exception:` is
   unreachable because `_read_source_sensitivity` always runs first on the
   identical `concept_text` and would already raise for the same malformed
   input. Both pre-exist the move; the move only made them independently
   testable (and thus visible in per-function coverage) for the first time.
5. **Task 14.1's premise was stale**: assumed ADR-0018 would still read
   `status: Proposed`; it already reads `Accepted`, flipped by the prior
   `#936` archive of the SIBLING query-service change (the ADR is shared
   across both bounded contexts). Confirmed correct, left untouched.

### Issues Found
- None blocking. The two unreachable branches (Deviation #4) and the
  malformed-prior-frontmatter wording change (Deviation #3) are the only
  loose ends, both documented and neither test-breaking nor gate-failing.

### Native runtime attempt
Acquired (`acq20260903233453`, token
`sha256:50175f6fddcdf2a90d50feb32cc8fd5e11e3d6348d63cbab0c70af5a6e51475d`)
and settled `outcome=passed` (`settle20260904064839`,
evidence-revision `sha256:129d49ea0d5215671a813d7e00fb040d0a90a3222a01c9d9aacf7d1a54359103`
— sha256 of commit `a9bee55`); STATUS returned `state: complete`.

### Workload / PR Boundary
- Mode: chained (stacked-to-main), auto-chain
- Current work unit: Slice 3 / PR 3 — plan-composition core (FINAL slice)
- Boundary: starts from `e5a7682` (Slice 2 merged); ends with commit
  `a9bee55` on `feat/918-ingest-service-slice3`, ready for PR 3 against
  `main`
- Real changed lines: 1483 (1082 additions + 401 deletions) across 11
  files — within the session's 2,000-line PR budget override
- Estimated review budget impact: moderate — the goldens fixture (20 lines)
  is generated/mechanical; the bulk of authored risk is the `_ingest_single`
  rewrite (net -251 lines in main.py) and the 24 new application-layer tests

### Status
ALL 44/44 tasks across all 3 slices complete (Slice 1: 8, Slice 2: 18,
Slice 3: 18). Ready for PR 3. This is the LAST apply batch for this change
— next SDD phase is `sdd-verify`, then archive after PR 3 merges (and
after PR 1/PR 2 merge, if not already).

---

**Final-state note (per sdd-archive Final-State Authority hierarchy):**
This snapshot's own text says "Ready for PR 3" / "next SDD phase is
sdd-verify, then archive after PR 3 merges" — that was true when this
snapshot was written. As of archive time, all three PRs merged to `main`
(PR #940 `7ec516b`, PR #941 `e5a7682`, PR #943 `8dcefbe`), `sdd-verify`
returned PASS (7/7 requirements, 9/9 scenarios, 0 CRITICAL, 0 WARNING),
and this archive phase is the terminal step of that already-completed
sequence. See `archive-report.md` for the authoritative final state.
