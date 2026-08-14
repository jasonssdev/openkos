# Verify Report: per-type-default-sensitivity (issue #669)

Branch: `feat/669-s4-adr-docs`. Slices 1-3 merged to `main` via PRs #679/#680/#681
(commits `5055a5e`, `7301daa`, `d90dc65`); slice 4 (ADR + docs) committed locally
(`8fb9634`), working tree clean, up to date with origin.

## 1. Spec-by-spec verification (code evidence, never task-list-only)

### `type-sensitivity-defaults` spec (9 requirements)

| # | Requirement | Verdict | Evidence |
|---|---|---|---|
| 1 | Per-Type Offset Config Shape (absent -> `{"Person":1}`, `{}` opts out) | PASS | `src/openkos/config.py:98` `DEFAULT_TYPE_SENSITIVITY_DEFAULTS = {"Person": 1}`; `config.py:959-962` absence fallback to a **copy** (`dict(...)`); `config.py:759` reads raw value, `is not None` predicate preserves explicit `{}`. Pinned by `tests/unit/test_config.py` (absence/null/`{}` cases, per apply-progress slice 1 evidence: 19/19 new). |
| 2 | Eager Validation At Config Load | PASS | `config.py:877-921`: validates BEFORE `Config(...)` is constructed (i.e. before any concept build), fails closed on unknown type key (`config.py:891-899`, gate = `types.BUILDABLE_TYPES`) and out-of-range/bool/non-int offset (`config.py:900-919`, `0 <= offset <= len(SENSITIVITY_ORDER)-1`, `bool` excluded first). No partial-fallback path exists — the `for` loop raises on first bad entry, so a malformed entry can't be silently dropped. |
| 3 | Floor-Relative Raise, Never A Bypass Of Source Inheritance | PASS | `config.py:1029-1054` `type_birth_sensitivity`: formula is exactly `combine_sensitivity(base, raise_by(cfg.default_sensitivity, offset))`, offset applied to `cfg.default_sensitivity` (the floor), never to `base`. `model/okf.py:553-570` `raise_by` clamps at ceiling via `min(_rank(level)+offset, len(SENSITIVITY_ORDER)-1)`. Since `combine_sensitivity` is a max/high-water-mark fold (`okf.py:578-583`), a higher `base` always wins. Pinned by `test_config.py::TestTypeBirthSensitivity` table (public->private, private->confidential-clamp, base-already-higher-wins, unmapped-type-passthrough per apply-progress slice 1). |
| 4 | Both `build_concept` Birth Seams Consult The Type Default | PASS | Ingest seam: `cli/main.py:3269-3271` calls `type_birth_sensitivity(cfg, extraction.type, stamp_sensitivity)` before `okf.build_concept(...)` at `main.py:3273-3282`. `--save` seam: `cli/main.py:13093-13097` calls the same resolver with `cited_high_water_mark`, feeding `okf.build_concept` at `main.py:13098-13106`. Both call the identical `config.type_birth_sensitivity`, so identical-input parity holds by construction (not by a parity test alone). Twin-rule mutation-tested independently — see Section 3. |
| 5 | Write-Time Advisory Names Type-Defaulted Objects | PASS | Ingest: `_echo_type_floor_summary` (`main.py:3622-3651`), called from batch aggregate (`main.py:3943`) and single (`main.py:4046`); silent when `pairs` empty (`main.py:3634-3635`); confidential-consequence line gated on `any(level == "confidential" ...)` (`main.py:3645-3651`). `--save`: success-message advisory at `main.py:13650-13661`, gated on `plan.type_floor_raised`, consequence line gated on `plan.sensitivity == "confidential"`. Both fire in the spec-required locations (ingest run summary, `query --save` success message). |
| 6 | One-Line Extension To Add A Type | PASS | Verified by inspection: `type_birth_sensitivity`, `_stage_derived_objects`, and `_stage_filed_answer` all key off `cfg.type_sensitivity_defaults.get(doc_type)` / `.items()` generically — no `Person`-specific branch anywhere in `config.py`, `main.py`, or `okf.py` (grep confirms zero hardcoded `"Person"` string outside the packaged-default constant and its docstring/tests). Adding `{"Organization": 1}` requires only a config data change. |
| 7 | No Backfill Of Existing On-Disk Concepts | PASS | Birth-time only by construction (both seams only run at document-write time, no scan-and-rewrite verb touches this mapping). Pinned by `tests/unit/cli/test_ingest.py::test_ingest_no_backfill_of_existing_person_after_unrelated_ingest` (line 1282), asserting byte-identical `sensitivity` after an unrelated `ingest` run under a newly configured type default. |
| 8 | Sources Are Never Type-Defaulted | PASS | `build_source_concept` (in `okf.py`) contains no call to `type_birth_sensitivity` or reference to `type_sensitivity_defaults` (confirmed via grep — zero hits outside `config.py`/`main.py`/`okf.py`'s `raise_by`/`type_birth_sensitivity`/`combine_sensitivity` definitions). Enforced structurally too: the validation domain is `BUILDABLE_TYPES`, which excludes `Source` (`config.py:891-897` comment states this explicitly), so even a malicious config entry naming `Source` fails config load rather than reaching a Source build. |
| 9 | `set-sensitivity` Downgrade Remains Unaffected | PASS | Zero lines changed in the `set-sensitivity` command path across all 3 code slices (confirmed via `git log --oneline` + `git show --stat` on the 3 merged commits — no `set_sensitivity` function touched). Pinned by `tests/unit/cli/test_set_sensitivity.py::test_type_defaulted_confidential_person_can_still_be_downgraded` (line 359). |

### `ingestion` delta spec — Requirement "Derived Object Provenance and Sensitivity Inheritance" (MODIFIED)

PASS. `_stage_derived_objects` reads `stamp_sensitivity` from the actually-staged Source
(not `cfg.default_sensitivity` — this was already true pre-#669 and remains unchanged),
then applies the type-default raise on top: `main.py:3269-3271`. The derived object's
frontmatter `sensitivity` is `resolved_sensitivity`, strictly >= `stamp_sensitivity` via
`combine_sensitivity`'s max-fold. The run summary advisory fires per Requirement 5 above.
Pinned by the twin-rule ingest-site test plus the pre-existing (unmodified) provenance
tests in `test_ingest.py`.

### `query-command` delta spec — Requirement "Sensitivity Is The High-Water-Mark Of Cited Concepts" (MODIFIED)

PASS. The pre-existing high-water-mark fold (`main.py:13066-13087`, unbroken by this
change — confirmed no lines in that block were touched by the #669 commits) still
seeds at `cfg.default_sensitivity` and folds every cited concept, fail-closed to
`confidential` on unreadable/unparseable citations. The type-default raise composes on
top at `main.py:13093-13097`, strictly above the citation high-water-mark, never below.
Success-message advisory per Requirement 5. Pinned by the twin-rule `--save`-site test
plus 2 additive (non-mutating) assertions appended to the pre-existing #569 preview
tests, confirmed still green with original assertions byte-identical.

### `participant-coverage-probe` delta spec — Requirement "No Per-Type Sensitivity Behavior in Probe Scope" (MODIFIED)

PASS (by absence). `grep -rn "type_sensitivity_defaults\|type_birth_sensitivity" src/openkos/`
returns matches ONLY in `config.py` (definition site) and `main.py` (the two `build_concept`
birth seams for `ingest`/`query --save`) — zero references in any probe/eval module. The
probe's own measurement path is therefore structurally untouched by this capability, and
the delta spec's narrowing (probe scope only, not workspace-wide) is accurate to what
shipped.

## 2. Tasks verification

All 25 checkboxes across WU1-WU5 in `tasks.md` are ticked `[x]`. Spot-checked against
the diff and apply-progress.md's per-slice evidence tables:

- WU1 (`raise_by`): `model/okf.py:553-570` matches design D2 exactly (negative-offset
  `ValueError`, ceiling clamp, `_rank` reuse). Truthful.
- WU2 (config seam): `config.py:98,705,759,877-921,959-962,1029-1054` all present and
  match design D1/D3. Truthful.
- WU3 (ingest seam): `_DerivedPlan.sensitivity`/`type_floor_raised` (`main.py:2964-2970`),
  wiring at `main.py:3269-3310`, `_echo_type_floor_summary` (`main.py:3622-3651`) called
  from both call sites (`main.py:3943`, `4046`). Truthful.
- WU4 (`--save` seam): `_FiledAnswerPlan.type_floor_raised` (`main.py:12830`), wiring at
  `main.py:13093-13119`, three-way preview branch (`main.py:13560-13580`), success-message
  advisory (`main.py:13650-13661`). Truthful.
- WU5 (ADR + docs): `docs/adr/0015-per-type-default-sensitivity.md` exists, is byte-identical
  to `design.md`'s Appendix (`diff` confirmed empty). `docs/cli.md:577,587-593` documents
  the field with a semantics match to shipped validation. Truthful.

No overclaiming found — every ticked box corresponds to code present on disk.

## 3. Test run + twin-rule mutation spot-check

Scoped suite: `python -m pytest tests/unit/model/test_okf_sensitivity.py
tests/unit/test_config.py tests/unit/cli/test_ingest.py
tests/unit/cli/test_set_sensitivity.py tests/unit/cli/test_query_save.py -q`

**Result: 655 passed** (0 failed, 0 skipped, 32-35s).

Twin-rule guard mutation spot-check (per repo's proven practice — a same-size mutation
can run stale bytecode, so `__pycache__` was purged before each run):

- **Ingest site** (`main.py:3269-3271`): reverted `resolved_sensitivity =
  config.type_birth_sensitivity(cfg, extraction.type, stamp_sensitivity)` to
  `resolved_sensitivity = stamp_sensitivity`. Ran `test_ingest.py` after purging
  `__pycache__`: **5 tests failed** (`test_stage_derived_objects_births_person_above_the_floor`,
  `test_ingest_source_sensitivity_is_never_type_defaulted`, both advisory tests, batch
  aggregation test). Restored exact original bytes; `git diff --stat` confirmed clean;
  re-ran the full scoped suite: 655/655 passed again.
- **`--save` site** (`main.py:13093-13097`): reverted the `sensitivity = (... if cfg is
  not None else cited_high_water_mark)` block to `sensitivity = cited_high_water_mark`.
  Ran `test_query_save.py` after purging `__pycache__`: **4 tests failed**
  (`test_stage_filed_answer_type_default_raises_above_the_floor`, both preview tests, the
  success-message test). Restored exact original bytes; `git diff --stat` confirmed clean;
  re-ran the full scoped suite: 655/655 passed again.

Both mutations were caught independently, by disjoint test sets, confirming the twin-rule
guard (design D6) is genuine — a single shared resolver-level test would NOT have caught
either mutation in isolation, but each call site has its own independent failing test.

## 4. Design conformance — deviations recorded in apply-progress.md

| Deviation | Assessment |
|---|---|
| `_DerivedPlan.sensitivity` extra field (not named in design's File Changes table, only `type_floor_raised` was) | **Acceptable.** Design under-specified how `_SingleIngestOutcome.type_floor_pairs`'s `(type, resolved_level)` should obtain `resolved_level` without a second frontmatter parse. Storing the already-computed value on the plan avoids re-parsing `plan.content` and mirrors the existing `doc_type`/`type_alternative` carrier pattern already used for `alternative_pairs`. No behavioral divergence from the spec or design intent — purely an implementation-detail addition that keeps the code DRY. Confirmed present at `main.py:2964-2966,3309`. |
| `_stage_filed_answer` gained `cfg: config.Config \| None = None` (nullable, defaulted) instead of a required `cfg: config.Config` like WU3's `_stage_derived_objects` | **Acceptable, and well-justified.** `_stage_filed_answer` is exercised directly by 15+ pre-existing unit tests unrelated to #669 (title/slug/citation-fold logic) that construct no `Config`. Making `cfg` required would force touching every one of those call sites for a field they don't exercise. `cfg=None` is defined as exactly pre-#669 behavior (`main.py:13092-13097`: `if cfg is not None else cited_high_water_mark`), so it is behaviorally inert, not a silent default-security-weakening — the one real production call site (`query --save`) always passes a real `cfg` (confirmed at `main.py:13506` region). No spec requirement is violated: the `query --save` seam still consults the type default in the actual runtime path. |

Both deviations are implementation-detail accommodations, not spec or design departures.
Neither introduces a security gap (the nullable `cfg` never reaches production without a
real value) or contradicts any of the 9 `type-sensitivity-defaults` requirements.

## 5. Docs verification

- `docs/adr/0015-per-type-default-sensitivity.md`: EXISTS, status `Proposed`
  (frontmatter `status: Proposed` + body `**Status:** Proposed`), byte-identical to
  `design.md`'s Appendix (`diff` empty). ADR number `0015` confirmed genuinely next-free
  (`0014` was highest pre-existing, per `ls docs/adr/`).
- `docs/cli.md`: commented `# type_sensitivity_defaults:` example present in the
  `openkos.yaml` reference block (`docs/cli.md:577-578`), alongside `type_tiers:`/`models:`
  precedent. Dedicated subsection (`docs/cli.md:587-593`) documents: shape (`{Type: offset}`),
  shipped default `{Person: 1}`, the floor-relative formula (matches
  `type_birth_sensitivity`'s exact formula in code), both birth seams, absent/null/`{}`/`0`
  semantics (matches `config.py:877,900-921` exactly — `{}` opts out entirely, `0` is a legal
  inert per-type decline, absent/null both fall back to the packaged default), eager
  fail-closed validation wording, and birth-time-only/no-backfill. Semantics match the
  shipped validation code exactly — no discrepancy found between doc prose and `config.py`'s
  actual `raise ValueError` conditions.

## Verdict

**PASS — ready for archive.**

All 9 `type-sensitivity-defaults` requirements, both modified delta-spec requirements
(`ingestion`, `query-command`), and the `participant-coverage-probe` non-interference
requirement verify PASS against shipped code with file:line evidence and pinning tests.
All 25 tasks.md checkboxes are truthful. The scoped test suite is 655/655 green. The
twin-rule guard was independently mutation-verified at both birth-seam call sites (5
failures at the ingest site, 4 at the `--save` site, both restored byte-identical
afterward). Both recorded design deviations are acceptable implementation-detail
accommodations with no spec or security impact. ADR-0015 and `docs/cli.md` accurately
and completely describe the shipped behavior, including exact absent/null/`{}`/`0`
semantics and eager fail-closed validation.

No CRITICAL, WARNING, or SUGGESTION findings.
