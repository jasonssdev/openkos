# Tasks: Per-Type Default Sensitivity (Person born above the workspace floor)

Issue #669.

```yaml
delivery_strategy: auto-chain
chain_strategy: stacked-to-main
review_budget_changed_lines: 400
strict_tdd: true
test_runner: python -m pytest
```

Strict TDD is ACTIVE for every work unit below: each slice writes the failing
test(s) FIRST (`python -m pytest <path> -x` red), then the minimal
implementation to turn them green, then the advisory/wiring pass, re-running
the full slice's test file(s) plus `python -m pytest` for the whole affected
module before moving to the next WU. No WU's implementation commit may
precede its own test commit.

The attempt window counts EVERY changed line in the window, including test
files, docs, and this tasks.md's own future edits during apply — the Review
Workload Forecast below is sized on that basis, not code-only.

---

## WU1 — `okf.raise_by` helper (model layer, pure)

**Satisfies**: `type-sensitivity-defaults` spec, Requirement "Floor-Relative
Raise, Never A Bypass Of Source Inheritance" (the `raise_by` primitive the
formula depends on); design D2.

**Depends on**: none. **Parallel-safe with**: nothing meaningfully — this is
the first slice everything else imports.

1. [ ] RED: create `tests/unit/model/test_okf_sensitivity.py` with a
   parametrized table covering: each `SENSITIVITY_ORDER` floor x offset
   `0`/`1`/`2`; clamp at `confidential` for an offset that would overflow;
   fail-closed ranking on a missing/malformed/non-string `level` (reuses
   `_rank`'s existing fail-closed behavior); `ValueError` on a negative
   offset. Run `python -m pytest tests/unit/model/test_okf_sensitivity.py -x`
   and confirm it fails (no `raise_by` yet).
2. [ ] GREEN: add `raise_by(level: object, offset: int) -> str` to
   `src/openkos/model/okf.py`, beside `SENSITIVITY_ORDER`/
   `combine_sensitivity`, exactly as specified in design D2 (negative offset
   raises `ValueError`; overflow clamps at the ceiling; reuses `_rank`).
3. [ ] Run `python -m pytest tests/unit/model/test_okf_sensitivity.py -v`
   green, then `python -m pytest tests/unit/model/ -q` to confirm no
   regression in the sibling model tests.

---

## WU2 — Config seam: `type_sensitivity_defaults` + eager validation + `type_birth_sensitivity` resolver

**Satisfies**: `type-sensitivity-defaults` spec, Requirements "Per-Type
Offset Config Shape", "Eager Validation At Config Load", and the resolver
half of "One-Line Extension To Add A Type"; design D1, D3.

**Depends on**: WU1 (`type_birth_sensitivity` calls `raise_by`).
**Parallel-safe with**: nothing else — WU3 and WU4 both import this seam.

1. [ ] RED: extend `tests/unit/test_config.py` with:
   - `read_config` validation table: non-mapping value, unknown type key,
     `Source` key explicitly refused (not in `BUILDABLE_TYPES`), non-int
     value, `bool` value refused (checked BEFORE the numeric-tower
     coercion), negative offset refused, offset `3` refused (unreachable at
     every floor), offset `0` LOADS and is inert, offset `2` LOADS.
   - Absence semantics: field absent -> `{"Person": 1}`; explicit YAML
     `null` -> same; explicit `{}` -> total opt-out; the returned dict is a
     **copy** of `DEFAULT_TYPE_SENSITIVITY_DEFAULTS`, never the shared
     module object (mutate the returned dict, assert the module constant is
     unchanged).
   - `type_birth_sensitivity(cfg, doc_type, base)` table: `public`->
     `private`, `private`->`confidential`, `confidential`->`confidential`;
     `base` already above `floor+offset` wins (high-water-mark preserved);
     a `doc_type` absent from the mapping returns `base` canonicalized
     unchanged.
   Run `python -m pytest tests/unit/test_config.py -x` and confirm every new
   case fails (attribute/name errors — nothing exists yet).
2. [ ] GREEN: in `src/openkos/config.py` add
   `DEFAULT_TYPE_SENSITIVITY_DEFAULTS: Final[dict[str, int]] = {"Person": 1}`,
   the `Config.type_sensitivity_defaults: dict[str, int]` field, and the
   eager per-entry validation block in `read_config` mirroring the `models:`
   precedent (`config.py:808-846`): key domain = `model.types.BUILDABLE_TYPES`,
   `bool` excluded first and explicitly, `0 <= offset <= len(SENSITIVITY_ORDER) - 1`,
   `ValueError` messages prefixed `f"{layout.config_path.name}: ..."`.
3. [ ] GREEN: add `type_birth_sensitivity(cfg: Config, doc_type: str, base: object) -> str`
   beside `resolve_task_model`, implementing
   `combine_sensitivity(base, raise_by(cfg.default_sensitivity, offset))` per
   design D3, returning `base` canonicalized when `doc_type` has no entry.
4. [ ] Run `python -m pytest tests/unit/test_config.py -v` green, then
   `python -m pytest tests/unit/ -q` to confirm no cross-module regression
   (in particular no `models:`/`type_tiers:` test broke).

---

## WU3 — Ingest seam: `_stage_derived_objects` + run-summary advisory + count plumbing

**Satisfies**: `type-sensitivity-defaults` spec, the ingest half of
"Both `build_concept` Birth Seams Consult The Type Default" and "Write-Time
Advisory Names Type-Defaulted Objects And The Retrieval Consequence";
"No Backfill Of Existing On-Disk Concepts"; "Sources Are Never
Type-Defaulted"; "`set-sensitivity` Downgrade Remains Unaffected". `ingestion`
spec, modified Requirement "Derived Object Provenance and Sensitivity
Inheritance". Design D3 (count plumbing), D4 (ingest advisory wording).

**Depends on**: WU2 (`cfg.type_sensitivity_defaults`,
`type_birth_sensitivity`). **Parallel-safe with**: WU4 (disjoint call sites
and disjoint test files — safe to run as sibling stacked PRs on top of WU2,
but land in a defined order per `chain_strategy: stacked-to-main`).

1. [ ] RED: extend `tests/unit/cli/test_ingest.py` with:
   - Birth seam: `_stage_derived_objects` births a `Person` above the floor
     given a `public`-resolved Source and the shipped `{"Person": 1}`
     mapping (asserts `private`); a non-defaulted type (e.g.
     `Organization`) is untouched; the Source document itself is untouched
     (Requirement "Sources Are Never Type-Defaulted"); `_DerivedPlan.type_floor_raised`
     is set `True` on the raised object and `False` otherwise.
     **Twin-rule guard**: this is one of the two independent site tests
     required by design D6 — it must fail if ONLY the ingest call site is
     reverted to `sensitivity=base`, independent of WU4's `--save` site
     test.
   - Advisory: aggregate line fires with the correct raised-count and
     type(s) named; silent when nothing was raised in the run; adds the
     #569 confidential-exclusion consequence line only when the raised
     level is `confidential`, not at `private`.
   - No-backfill: an existing on-disk `Person` concept's `sensitivity`
     field is byte-identical after an unrelated `ingest` run under a newly
     configured type default (Requirement "No Backfill Of Existing On-Disk
     Concepts").
   Run `python -m pytest tests/unit/cli/test_ingest.py -x` and confirm the
   new cases fail.
2. [ ] RED: extend `tests/unit/cli/test_set_sensitivity.py` with the
   Requirement-9 downgrade test: `set-sensitivity <person-id> private
   --allow-downgrade` succeeds on a type-defaulted-`confidential` Person,
   the frontmatter reads `private` afterward, and nothing re-raises it.
   Run `python -m pytest tests/unit/cli/test_set_sensitivity.py -x` and
   confirm it fails (or passes vacuously only if the fixture cannot yet
   produce a type-defaulted Person — in that case fix the fixture first,
   not the assertion).
3. [ ] GREEN: wire `_stage_derived_objects` (`cli/main.py:3249`) to call
   `config.type_birth_sensitivity(cfg, doc_type, stamp_sensitivity)` and set
   `_DerivedPlan.type_floor_raised = (resolved != stamp_sensitivity)`.
4. [ ] GREEN: add `_SingleIngestOutcome.type_floor_pairs: tuple[tuple[str, str], ...]`
   built at `main.py:4595` the same way `alternative_pairs` is; add
   `_echo_type_floor_summary(derived_count, pairs)` beside
   `_echo_type_alternative_summary`, silent when `pairs` is empty, emitting
   the two-line stderr advisory from design D4 (aggregate line, then the
   confidential-consequence line only when applicable); call it from both
   `main.py:3874` (batch aggregate) and `main.py:3976` (single).
5. [ ] Confirm the WU2 `set-sensitivity` downgrade path needs no code
   change (design: "Independent write path; never consults the type
   default") — the test from step 2 should now pass with zero
   `set-sensitivity` implementation edits. If it does not, the failure is in
   the fixture/staging path, not in `set-sensitivity` itself.
6. [ ] Run `python -m pytest tests/unit/cli/test_ingest.py tests/unit/cli/test_set_sensitivity.py -v`
   green, then `python -m pytest tests/unit/cli/ -q` for the full CLI suite.

---

## WU4 — `query --save` seam: `_stage_filed_answer` + preview disclosure + success-message advisory

**Satisfies**: `type-sensitivity-defaults` spec, the `--save` half of "Both
`build_concept` Birth Seams Consult The Type Default" and "Write-Time
Advisory Names Type-Defaulted Objects And The Retrieval Consequence".
`query-command` spec, modified Requirement "Sensitivity Is The High-Water-Mark
Of Cited Concepts". Design D3, D4 (preview + success-message wording).

**Depends on**: WU2. **Parallel-safe with**: WU3 (see note above).

1. [ ] RED: extend `tests/unit/cli/test_query_save.py` with:
   - Birth seam: `--type Person` births above the floor given a
     `public`-resolved citation high-water-mark and the shipped mapping
     (asserts `private`); a higher citation high-water-mark still wins over
     the type-defaulted floor (high-water-mark preserved).
     **Twin-rule guard**: this is the second of the two independent site
     tests — it must fail if ONLY the `--save` call site is reverted to
     `sensitivity=base`, independent of WU3's ingest site test.
   - Preview wording, all three branches: type-default-raised
     (`raised by the Person type default`), citation-inherited-confidential
     (existing #569 wording, byte-for-byte preserved), and unaffected (no
     annotation); the `!` confidential-consequence line prints whenever the
     resolved level is `confidential` regardless of which branch produced
     it.
   - Success-message advisory (spec Requirement 6 / `query-command` spec):
     fires immediately after the `filed answer as ...` line when
     `plan.type_floor_raised`, names count + type + level, adds the #569
     consequence line only at `confidential`, is silent when nothing was
     raised, and STILL fires under `--auto` even though the preview block
     itself is skipped in that mode.
   Run `python -m pytest tests/unit/cli/test_query_save.py -x` and confirm
   the new cases fail.
2. [ ] GREEN: wire `_stage_filed_answer` (`cli/main.py:12993`) to call
   `config.type_birth_sensitivity(cfg, doc_type, cited_high_water_mark)` and
   set `_FiledAnswerPlan.type_floor_raised = (resolved != cited_high_water_mark)`.
3. [ ] GREEN: replace the two-way preview branch at `main.py:13443-13457`
   with the three-way branch from design D4 (type-default cause outranks
   the citation cause when both could apply; citation wording preserved
   byte-for-byte for the cases it still owns).
4. [ ] GREEN: add the success-message advisory immediately after
   `main.py:13514-13517`'s `filed answer as ...` line, before `_autocommit`,
   gated on `plan.type_floor_raised`, per design D4's two-line wording.
5. [ ] Run `python -m pytest tests/unit/cli/test_query_save.py -v` green,
   then `python -m pytest tests/unit/cli/ -q` for the full CLI suite (must
   still be green alongside WU3's changes once both are stacked).

---

## WU5 — ADR-0015 + docs

**Satisfies**: design D5 (ADR); no spec Requirement directly (documentation
task), but the `openkos.yaml` reference table in `docs/cli.md` documents
every other config seam this change's sibling specs assume operators can
discover (`models:`, `type_tiers:` precedent).

**Depends on**: WU1–WU4 conceptually complete (the ADR and docs describe
shipped behavior), but has no code dependency — safe to draft in parallel
and land last in the stack.

1. [ ] Create `docs/adr/0015-per-type-default-sensitivity.md` using the full
   draft text in `design.md`'s Appendix verbatim (frontmatter, Context,
   Decision, Consequences, Alternatives considered), status `Proposed`.
2. [ ] Add a commented `# type_sensitivity_defaults:` line to the
   `openkos.yaml` example block in `docs/cli.md` (`docs/cli.md:557-583`),
   alongside the existing `type_tiers:`/`models:` commented examples, and a
   short subsection (matching the `chat_timeout`/`max_generation_tokens`
   style at `docs/cli.md:585-608`) documenting: shape (`{Type: offset}`),
   shipped default `{"Person": 1}`, eager-validation-fails-closed behavior,
   and that it is birth-time only with no backfill.
3. [ ] Structural readback of both files (no automated test — documentation
   only): confirm the ADR number (`0015`) is genuinely the next free one and
   the `docs/cli.md` addition renders as valid Markdown/YAML.

---

## Review Workload Forecast

| WU | Code (impl) | Tests | Docs | Est. total changed lines |
|---|---|---|---|---|
| WU1 — `raise_by` | ~15 | ~85 | 0 | **~100** |
| WU2 — config seam | ~70 | ~180 | 0 | **~250** |
| WU3 — ingest seam | ~90 | ~230 | 0 | **~320** |
| WU4 — `--save` seam | ~85 | ~200 | 0 | **~285** |
| WU5 — ADR + docs | 0 | 0 | ~150 | **~150** |
| **Total (all WUs)** | ~260 | ~695 | ~150 | **~1105** |

- **Chained PRs recommended: Yes.** No single WU is designed to exceed the
  400-line budget on its own, but the combined change is ~2.7x the budget;
  `delivery_strategy: auto-chain` / `chain_strategy: stacked-to-main` is
  required, not optional.
- **400-line budget risk: High for WU3, Medium for WU2 and WU4, Low for WU1
  and WU5.** WU3's estimate (~320) sits closest to the 400 cap: it carries
  the two-file test surface (`test_ingest.py` + `test_set_sensitivity.py`)
  plus the new `_echo_type_floor_summary` function and count-plumbing
  through both the batch and single call sites. If the twin-rule site test
  or the no-backfill fixture setup runs longer than estimated, WU3 alone
  could approach the cap — the attempt window counts test lines exactly
  like implementation lines, and this repo has hit that limit before
  (`sdd-attempt` window counted every line including docs and blocked two
  otherwise-passed WUs on #668's budget).
- **Decision needed before apply: No** for the slicing itself — the five-WU
  order matches the design's seam boundaries and each stays under budget
  individually. A decision MAY become needed mid-apply only if WU3's actual
  diff exceeds ~380 lines once written; in that case split it at the
  advisory-plumbing boundary (birth-seam-wiring + no-backfill test as 3a,
  `_echo_type_floor_summary` + aggregate/silent/consequence tests as 3b)
  rather than trimming test coverage.

## Ordering / Dependency Summary

```
WU1 (raise_by)
  └─> WU2 (config seam: type_sensitivity_defaults, type_birth_sensitivity)
        ├─> WU3 (ingest seam)   ─┐  parallel-safe test-writing,
        └─> WU4 (--save seam)   ─┘  sequential landing in the stack
              └─> WU5 (ADR + docs, describes WU1-4's shipped behavior)
```

WU3 and WU4 touch disjoint files (`_stage_derived_objects` vs.
`_stage_filed_answer`, `test_ingest.py`/`test_set_sensitivity.py` vs.
`test_query_save.py`) and can be drafted in parallel, but
`stacked-to-main` still lands them as two sequential PRs on top of WU2 —
"parallel-safe" here means no file-content conflict, not simultaneous merge.
