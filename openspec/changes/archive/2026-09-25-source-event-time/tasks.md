# Tasks: source-event-time — a Source's event date, separate from ingest time

Refs #1014 piece (c). Strict TDD is ON, runner `uv run pytest`. Every
behavioral task pairs a `[TEST]` task, observed RED with the reason it is
RED today, with the `[IMPL]` task that turns it GREEN — in that order.
`[IMPL]` tasks introduce only what their paired `[TEST]` already pins.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~600-680 (source ~255, tests ~400, docs+ADR ~6, ADR-0023 already written) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (okf seam + parser) → PR 2 (application resolution) → PR 3 (CLI + docs) |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

Per-slice estimate (design.md "Auto-chain slice plan"):

| Slice | Source | Tests | Docs | Total (est.) |
| --- | --- | --- | --- | --- |
| 1: OKF seam and parser | ~105 | ~130 | 0 | ~235 |
| 2: Application resolution | ~95 | ~120 | 0 | ~215 |
| 3: CLI and docs | ~55 | ~150 | ~6 | ~210 |

Total ≈ 660, over the ~400-line single-PR budget, hence three stacked
slices. **Slices 1 and 2 are inert on their own** — no production caller
passes `event_date` or `source_name` until slice 3's CLI wiring lands, per
design.md's "Auto-chain slice plan". Each slice's own test suite proves its
new code paths directly (parser tables, emission/reader/merge byte pins in
slice 1; `resolve_event_date`/threading/carried-status/short-circuit tables
in slice 2, exercised through unit calls, not through `ingest`), so each PR
is still green and coherent standing alone, and each can be reverted
independently (design.md "Migration / Rollout").

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `source_date.py` parser + `okf` key/emission/reader/merge special-case | PR 1 → `main` | `uv run pytest tests/unit/test_source_date.py tests/unit/model/test_okf.py` | N/A — pure functions and a builder/reader with no CLI or workspace surface; no runtime harness exists below `ingest`, and `event_date` is not yet wired to any caller | Revert `src/openkos/source_date.py`, the `okf.py` diff, and `tests/unit/test_source_date.py` / the `test_okf.py` diff; no caller references either yet |
| 2 | `resolve_event_date`, stored read-back, `compose_source_document`/`SourceDocumentPlan`/`compose_catalog_update` threading, `carried_extraction_status`, `stage_derived_objects(carried=...)` short-circuit | PR 2 → PR 1's branch | `uv run pytest tests/unit/application/test_ingest.py` | N/A — `application/ingest.py` functions called directly in unit tests with stub `LLMBackend`; `cli/main.py` does not yet pass `event_date_flag`/`source_name`, so no end-to-end `ingest` scenario exists yet | Revert the `application/ingest.py` diff and the `test_ingest.py` diff; `cli/main.py` still calls the pre-slice signatures with defaults, so `ingest` behavior is unchanged |
| 3 | `--event-date` option + two exit-2 refusals, `_ingest_single` threading, preview line, stderr warning, convergence condition + render/stage-notice guards, outcome flags, `docs/cli.md` row | PR 3 → PR 2's branch | `uv run pytest tests/unit/cli/test_ingest.py tests/unit/cli/test_ingest_characterization.py` | `uv run openkos ingest tests/fixtures/... --event-date 2026-07-14` against a scratch `openkos init` workspace (manual smoke, or the equivalent CliRunner invocation added in 3.2-3.10) — first real end-to-end exercise of the feature | Revert the `cli/main.py` diff, `docs/cli.md` row, and `test_ingest.py` diff; `ingest` goes back to unconditional `datetime.now(UTC)` stamping with no `--event-date` flag |

## Scenario → Task Coverage

Every delta-spec scenario mapped to at least one task below.

| Spec | Scenario | Task(s) |
|---|---|---|
| ingestion | No flag and no dated file name omits the key | 1.4 (okf absence pin), 3.14 (CLI end-to-end) |
| ingestion | event_date is never the ingest timestamp | 1.4, 3.14 |
| ingestion | Derived objects never carry an event date | 1.4 (builder only emits on Source path), 3.14 |
| ingestion | A valid flag value is accepted | 3.13, 3.14 |
| ingestion | An invalid calendar date is refused before any write | 3.2, 3.3 |
| ingestion | A malformed value is refused before any write | 3.2, 3.3 |
| ingestion | A directory input with --event-date is refused | 3.4, 3.5 |
| ingestion | A glob input with --event-date is refused, even matching one file | 3.4, 3.5 |
| ingestion | A single dated token yields that date | 1.2 (parser table) |
| ingestion | A non-ISO-ordered date token yields no date | 1.2 |
| ingestion | An invalid calendar date in the file name yields no date | 1.2 |
| ingestion | A leading digit-adjacent token yields no date | 1.2 |
| ingestion | A trailing digit-adjacent token yields no date | 1.2 |
| ingestion | Two distinct dated tokens yield no date | 1.2 |
| ingestion | The same dated token repeated yields that one date | 1.2 |
| ingestion | The flag wins over a dated file name | 2.2 (`resolve_event_date` precedence), 3.14 |
| ingestion | The file name is used when no flag is given | 2.2, 3.16 |
| ingestion | Neither source leaves the key unset | 2.2, 1.4 |
| ingestion | Re-ingest with no flag keeps the stored value | 2.2, 3.17 |
| ingestion | A differing flag overwrites the stored value | 2.2, 3.18 |
| ingestion | A stored date read back as a YAML date object is still recognized | 1.6 (reader tolerance table), 2.4 (stored read-back) |
| ingestion | A legacy Source with no stored value falls back to the file name | 2.2, 3.16 |
| ingestion | A legacy Source with no stored value and no dated file name stays unset | 2.2, 3.14 |
| ingestion | A malformed stored value is not carried forward and warns | 1.6, 2.4, 3.19, 3.20 |
| ingestion | A malformed stored value is filled by the flag or the file name | 2.2, 3.19 |
| ingestion | A differing flag on a converged Source rewrites it with no extraction | 2.8-2.11 (carried short-circuit), 3.21, 3.22 |
| ingestion | A pre-feature Source with a dated file name gains the date on plain re-ingest | 2.8-2.11, 3.23 |
| ingestion | A resolved date equal to the stored value writes nothing | 2.7 (convergence gate), 3.24 |
| ingestion | The date-only rewrite is idempotent | 3.25 |
| ingestion | The line names the flag origin | 3.13, 3.14 |
| ingestion | The line names the file-name origin | 3.16 |
| ingestion | The line names a carried-forward value as kept | 3.17 |
| ingestion | An overwrite names both the old and new value | 3.18 |
| ingestion | No line is printed when no event date is recorded | 3.14 |
| ingest-application-service | A None event_date produces a byte-identical Source | 2.3 (compose_source_document byte pin) |
| ingest-application-service | A given event_date reaches the generated document | 2.3 |
| ingest-application-service | The service performs no resolution of its own | 2.2 (resolution isolated in `resolve_event_date`, called by the CLI's resolved inputs — see design Decision 4) |
| ingest-application-service | A marker-only catalog rebuild keeps the resolved event_date | 2.5, 2.6 (`compose_catalog_update` regression) |
| entity-resolution-merge | Conflicting fields resolved and surfaced | pre-existing, unaffected |
| entity-resolution-merge | Survivor's type wins on a cross-type merge | pre-existing, unaffected |
| entity-resolution-merge | The absorbed `type_alternative` does not cross the merge | pre-existing, unaffected |
| entity-resolution-merge | The survivor keeps its own `type_alternative` | pre-existing, unaffected |
| entity-resolution-merge | The absorbed event_date does not cross the merge | 1.8, 1.9 |
| entity-resolution-merge | The survivor keeps its own event_date | 1.8, 1.9 |

---

## Slice 1 (PR 1 → `main`): OKF seam and parser

### `source_date.py` — pure leaf parser

- [x] **1.1** [TEST] Create `tests/unit/test_source_date.py` with
  `test_parse_event_date` parametrized over design.md's flag-parser table:
  `"2026-07-14"` → `date(2026, 7, 14)`; `"2026-02-30"`, `"20260714"`,
  `"2026-W28-2"`, `" 2026-07-14"`, `""` → `None` (calendar-invalid,
  ISO-8601-week form and Basic-format both accepted by
  `date.fromisoformat` since Python 3.11 but rejected by the required
  regex prefilter, and a leading space). **RED today**: `openkos.source_date`
  does not exist — `ModuleNotFoundError`.
- [x] **1.2** [TEST] Same file, add `test_event_date_from_name` parametrized
  over design.md's full name-inference table: `call-2026-07-14.txt` →
  `2026-07-14`; `2026-07-14.md` → `2026-07-14`; `notes.2026-07-14` →
  `2026-07-14`; `a-2026-07-14_b-2026-07-14.txt` → `2026-07-14`;
  `2026-07-14-15.txt` → `2026-07-14`; `03-04-2026.txt`,
  `2026-13-01.txt`, `12026-07-14.txt`, `2026-07-140.txt`,
  `2026-07-14_2026-08-01.txt`, `2026-13-01_2026-07-14.txt`,
  `20260714.txt`, `notes.txt`, `٢٠٢٦-٠٧-١٤.txt` (Arabic-Indic digits) →
  `None`. Covers ingestion-spec scenarios "A single dated token yields
  that date", "A non-ISO-ordered date token yields no date", "An invalid
  calendar date in the file name yields no date", "A leading/trailing
  digit-adjacent token yields no date", "Two distinct dated tokens yield
  no date", "The same dated token repeated yields that one date". **RED
  today**: `ModuleNotFoundError`.
- [x] **1.3** [TEST] Same file, add `test_parsers_never_raise` — a
  `@pytest.mark.parametrize` fuzz-lite sweep (empty string, very long
  string, strings with null bytes, surrogate-adjacent Unicode) asserting
  neither function raises. **RED today**: `ModuleNotFoundError`.
- [x] **1.4** [IMPL] Create `src/openkos/source_date.py` (pure stdlib, no
  `openkos` imports — mirrors `src/openkos/source_title.py`'s shape):
  `parse_event_date(text: str) -> date | None` using
  `re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text)` then
  `date.fromisoformat`, catching `ValueError`; `event_date_from_name(name:
  str) -> date | None` scanning the given basename with
  `(?<!\d)([0-9]{4}-[0-9]{2}-[0-9]{2})(?!\d)`, rejecting on any
  calendar-invalid token, returning the single element of the distinct
  valid-token set or `None` (design.md Decision 1). Makes 1.1-1.3 GREEN.

### `model/okf.py` — key, emission, reader

- [x] **1.5** [TEST] `tests/unit/model/test_okf.py` — add
  `test_build_source_concept_emits_no_event_date_key_by_default`, modeled
  on `test_build_source_concept_emits_no_volatility_key` (`:687`): call
  `build_source_concept` with no `event_date` argument, assert the
  produced frontmatter has no `event_date` key. **RED today**:
  `build_source_concept` has no `event_date` parameter —
  `TypeError` is not raised because the parameter does not exist yet, so
  this actually fails at collection/call time with `TypeError:
  unexpected keyword argument` once the call includes it — confirm by
  running before 1.6 lands and recording the observed failure.
- [x] **1.6** [TEST] Same file, add
  `test_build_source_concept_emits_event_date_key_with_exact_bytes` — a
  full-bytes pin: call `build_source_concept(..., event_date=date(2026, 7,
  14))`, assert the output contains the exact line `event_date:
  '2026-07-14'` sorted alphabetically between `description` and
  `freshness` (mirrors `timestamp`'s quoting pin at `test_okf.py:922`).
  **RED today**: `TypeError` (no such parameter).
- [x] **1.7** [TEST] Same file, add
  `test_build_source_concept_raises_on_datetime_event_date` — passing a
  `datetime.datetime` (not `date`) for `event_date` raises `TypeError`.
  **RED today**: `TypeError` (no such parameter), for the wrong reason —
  confirm the message differs before 1.9 lands.
- [x] **1.8** [TEST] Same file, add
  `test_build_source_concept_round_trips_event_date_through_load_frontmatter`
  — build a Source with `event_date=date(2026, 7, 14)`, run it through
  `load_frontmatter`, then `okf.read_event_date`, and assert
  `StoredEventDate(value=date(2026, 7, 14), malformed=False, raw=...)`.
  **RED today**: `TypeError` (no such parameter) and `read_event_date`
  does not exist — `AttributeError`.
- [x] **1.9** [IMPL] `src/openkos/model/okf.py`: add
  `EVENT_DATE_KEY: Final = "event_date"` with a docstring beside
  `ORIGIN_KEY_KEY`. Add `event_date: date | None = None` parameter to
  `build_source_concept` (`okf.py:587-588` region); when not `None`,
  raise `TypeError` if it is a `datetime.datetime` instance (checked
  before the `date` isinstance check would pass, since `datetime` is a
  `date` subclass), else emit
  `metadata[EVENT_DATE_KEY] = event_date.isoformat()` after `origin_key`,
  under `if event_date is not None`. Makes 1.5-1.7 GREEN and the
  emission half of 1.8 GREEN.
- [x] **1.10** [TEST] Same file, add `test_read_event_date` parametrized
  over design.md Decision 2's reader-tolerance table: key absent →
  `(None, False)`; valid quoted string → `(date(2026,7,14), False)`;
  unquoted date PyYAML loads as `datetime.date` → `(date(2026,7,14),
  False)`; a `datetime.datetime` value → `(None, True)`; a bad string
  (`"14/07/2026"`, `"2026-13-01"`, `""`), `None`, an int, a list → each
  `(None, True)`. Assert the `datetime.datetime` check runs before the
  `date` check (a dedicated case with a `datetime` at midnight, which
  would otherwise pass an `isinstance(x, date)` check first). **RED
  today**: `read_event_date` does not exist — `AttributeError`.
- [x] **1.11** [IMPL] `src/openkos/model/okf.py`: add
  `@dataclass(frozen=True) class StoredEventDate: value: date | None;
  malformed: bool; raw: object` and
  `read_event_date(metadata: Mapping[str, object]) -> StoredEventDate`,
  reusing the existing `_ISO_DATE_RE` (`okf.py:48`) plus a calendar-valid
  check, checking `datetime.datetime` before `date` per Decision 2.
  Makes 1.8 (reader half) and 1.10 GREEN.

### Merge special-case

- [x] **1.12** [TEST] Same file, add
  `test_build_merged_document_never_inherits_absorbed_event_date` (beside
  `test_build_merged_document_never_inherits_absorbed_type_alternative`,
  `:1519`) — survivor without `event_date`, absorbed with
  `event_date: '2026-07-14'` → merged document carries no `event_date`.
  Covers entity-resolution-merge spec scenario "The absorbed event_date
  does not cross the merge". **RED today**: the generic "adopt when
  absent" branch (`okf.py:1721-1722`) copies the absorbed value —
  `AssertionError`.
- [x] **1.13** [TEST] Same file, add
  `test_build_merged_document_keeps_survivors_own_event_date` — survivor
  with `event_date: '2026-07-14'`, absorbed with a different
  `event_date: '2026-08-01'` → merged document keeps `'2026-07-14'`.
  Covers spec scenario "The survivor keeps its own event_date". **RED
  today**: passes accidentally under the current generic scalar rule
  (survivor's value already wins when present) — confirm it is RED for
  the *right* reason by first asserting `EVENT_DATE_KEY` is NOT yet in
  `_SPECIAL_KEYS`, or skip straight to 1.12 as the meaningful RED case and
  treat this one as a regression pin written alongside 1.14.
- [x] **1.14** [IMPL] `src/openkos/model/okf.py`: add `EVENT_DATE_KEY` to
  `_SPECIAL_KEYS` (`okf.py:1705-1712`, beside `TYPE_ALTERNATIVE_KEY`);
  add one docstring sentence to `build_merged_document` beside the
  `type_alternative` note (`okf.py:1656-1667`). Add one sentence to the
  `EXTRACTION_STATUS_KEY`/`EXTRACTION_NOTICE_KEY` docstrings
  (`okf.py:113-120`) naming the carried-marker exception design Decision
  6 introduces (forward reference — the mechanism itself lands in slice
  2; this sentence documents the exception the key's "never read back"
  rule will gain). Makes 1.12 and 1.13 GREEN.

### Slice 1 verification

- [x] **1.15** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **1.16** Run `uv run pytest` (unpiped) — must be green, including
  every characterization file listed in design.md's Testing Strategy
  table (`test_ingest_characterization.py`,
  `test_frontmatter_split_parity.py`, `test_okf_framing_characterization.py`,
  `test_adr_index.py`, every existing `test_build_source_concept_*`
  golden) unedited.
- [x] **1.17** Commit as one or more work-unit commits, scope `model`
  (e.g. `feat(model): add event_date frontmatter key, parser, and merge
  exclusion`), tests alongside behavior. Open PR 1 targeting `main`.

---

## Slice 2 (PR 2 → PR 1's branch): Application resolution

This slice is inert: `cli/main.py` does not yet pass `event_date_flag` or
`source_name` to `compose_source_document`, so every new code path here is
exercised only by direct unit calls in `tests/unit/application/test_ingest.py`,
not through `ingest` end-to-end.

### `resolve_event_date` and stored read-back

- [x] **2.1** [TEST] `tests/unit/application/test_ingest.py` — add
  `test_resolve_event_date_precedence` parametrized over design.md
  Decision 4's precedence table: `flag` set → `(flag, "flag")` regardless
  of `stored`/`inferred`; `flag=None, stored.value` set → `(stored.value,
  "kept")`; `flag=None, stored=None (or malformed), inferred` set →
  `(inferred, "file name")`; all three `None`/absent → `(None, None)`.
  Also assert `.changed` in each case (`value != previous`, where
  `previous` is `stored.value` when `stored` is not malformed, else
  `None`). **RED today**: `resolve_event_date` and `EventDateResolution`
  do not exist — `ImportError`.
- [x] **2.2** [IMPL] `src/openkos/application/ingest.py`: add
  `EventDateOrigin = Literal["flag", "file name", "kept"]` and
  `@dataclass(frozen=True) class EventDateResolution` (fields `value`,
  `origin`, `previous`, `stored_malformed`, `stored_raw`, plus the
  `changed` property) and `resolve_event_date(*, flag, stored, inferred)`
  exactly per design.md Decision 4's precedence rules. Makes 2.1 GREEN.
- [x] **2.3** [TEST] Same file — add
  `test_compose_source_document_emits_event_date_when_given` and
  `test_compose_source_document_is_byte_identical_when_event_date_is_none`
  (ingest-application-service spec scenarios "A given event_date reaches
  the generated document", "A None event_date produces a byte-identical
  Source"). Also add
  `test_compose_source_document_reads_back_stored_event_date` — a
  re-ingest call where `concept_text` carries a prior `event_date`,
  asserting the returned `SourceDocumentPlan.event_date.previous` matches
  it. **RED today**: `compose_source_document` has no `event_date_flag`/
  `source_name` parameters, and `SourceDocumentPlan` has no `event_date`
  field — `TypeError`/`AttributeError`.
- [x] **2.4** [IMPL] `src/openkos/application/ingest.py`: add
  `_read_source_event_date(source_document_display_path, concept_text)`
  mirroring `_read_source_title` (`application/ingest.py:673-688`),
  calling `okf.read_event_date` and raising the same `ValueError` wording
  on unparseable frontmatter. Add `event_date_flag: date | None = None,
  source_name: str | None = None` parameters to `compose_source_document`;
  read `stored` via `_read_source_event_date` beside `on_disk_sensitivity`/
  `on_disk_title` (`application/ingest.py:785-796`), compute `inferred =
  source_date.event_date_from_name(source_name)` only when `source_name`
  is not `None`, call `resolve_event_date`, and pass `resolution.value`
  to `okf.build_source_concept`. Add `event_date: EventDateResolution`
  as a frozen-default field on `SourceDocumentPlan`
  (`application/ingest.py:691-718`) so the 14 existing test constructions
  in `test_ingest.py` stay valid unmodified. Makes 2.3 GREEN.
- [x] **2.5** [TEST] Same file — add
  `test_compose_catalog_update_preserves_event_date_on_marker_only_rebuild`
  (ingest-application-service spec scenario "A marker-only catalog
  rebuild keeps the resolved event_date" — the second call site design.md
  Decision 3 identifies as missed by the proposal). GIVEN a Source whose
  resolved `event_date` is set, and a catalog update that rebuilds the
  Source solely to record a new `extraction_status`/`extraction_notice`,
  THEN the rebuilt document still carries `event_date`. **RED today**:
  the `compose_catalog_update` call site at `application/ingest.py:881-893`
  does not pass `event_date=`, so the rebuilt document omits it —
  `AssertionError`.
- [x] **2.6** [IMPL] `src/openkos/application/ingest.py`: at the
  `compose_catalog_update` conditional-rebuild call site
  (`application/ingest.py:881-893`), pass
  `event_date=source.event_date.value`. Makes 2.5 GREEN.

### Carried-marker short-circuit (Decision 6)

- [x] **2.7** [TEST] Same file — add
  `test_stage_derived_objects_returns_carried_markers_without_llm_call`.
  GIVEN a `ConvergedReingest` whose `carried_status` and `carried_notices`
  are set, WHEN `stage_derived_objects(..., carried=converged)` runs with
  a stub `LLMBackend` whose `chat` raises `AssertionError("must not be
  called")`, THEN it returns
  `StagedDerivedObjects(plans=(), skip_reason=carried.carried_status,
  notices=carried.carried_notices, report=None, drops=(),
  lost_in_staging=0)` and the stub's `chat` was never invoked. **RED
  today**: `stage_derived_objects` has no `carried` parameter —
  `TypeError`.
- [x] **2.8** [TEST] Same file — add
  `test_carried_extraction_status_fails_closed` parametrized like
  `carried_extraction_notice` (`application/ingest.py:547-568`): a value
  inside `okf.EXTRACTION_STATUS_VALUES` narrows through; anything else
  (missing key, unrecognized string, wrong type) yields `None`. **RED
  today**: `carried_extraction_status` does not exist — `AttributeError`.
- [x] **2.9** [IMPL] `src/openkos/application/ingest.py`: add
  `carried_extraction_status(metadata) -> okf.ExtractionStatus | None`
  mirroring `carried_extraction_notice`'s fail-closed membership check.
  Add `carried_status: okf.ExtractionStatus | None` field to
  `ConvergedReingest` (`application/ingest.py:571-580`), populated via
  `carried_extraction_status`. Add `carried: ConvergedReingest | None =
  None` parameter to `stage_derived_objects`; when set, return the
  carried-markers `StagedDerivedObjects` **before** any LLM call — the
  same pre-extraction short-circuit shape as the `no-extractable-text`
  and `blocked-by-sensitivity` returns (`application/ingest.py:340-358`).
  Makes 2.7 and 2.8 GREEN.
- [x] **2.10** [TEST, both directions] Mutate 2.9's short-circuit guard.
  **Narrow-direction**: remove the `carried is not None` early return
  (fall through to ordinary extraction). Re-run 2.7 and confirm it goes
  RED — the stub `chat`'s `AssertionError` fires. **Broad-direction**:
  make the short-circuit trigger unconditionally (ignore `carried is
  None`). Re-run an existing ordinary-extraction test in
  `test_ingest.py` that exercises `stage_derived_objects` with `carried`
  omitted, and confirm it goes RED — extraction no longer runs on a
  normal ingest. Revert both mutations byte-exactly; purge
  `application/ingest.py`'s `__pycache__` before each re-run.

### Slice 2 verification

- [x] **2.11** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **2.12** Run `uv run pytest` (unpiped) — must be green, including the
  characterization files from design.md's Testing Strategy, unedited.
- [x] **2.13** Commit as one or more work-unit commits, scope `ingest`
  (e.g. `feat(ingest): resolve event_date precedence and carry it through
  catalog rebuilds`), tests alongside behavior. Open PR 2 targeting PR
  1's branch.

---

## Slice 3 (PR 3 → PR 2's branch): CLI and docs

This is the slice that wires `event_date` into a real `ingest` invocation
for the first time — every scenario in the `ingestion` delta spec becomes
end-to-end reachable here.

### Flag and refusals (Decision 5)

- [x] **3.1** [TEST] `tests/unit/cli/test_ingest.py` (`CliRunner`) — add
  `test_ingest_rejects_invalid_event_date_flag_before_any_write` (ingestion
  spec scenario "An invalid calendar date is refused before any write" /
  "A malformed value is refused before any write"). GIVEN a plain file,
  WHEN `openkos ingest <path> --event-date 2026-13-01` (and separately
  `not-a-date`) runs, THEN `exit_code == 2`, the exact wording `openkos
  ingest: --event-date must be a calendar date written YYYY-MM-DD, got
  '2026-13-01'.` is on stderr, and a tree snapshot before/after shows zero
  writes (`raw/`, `bundle/sources/`, `index.md`, `log.md` all untouched).
  **RED today**: `--event-date` is not a recognized option — Typer/Click
  reports an unrecognized-option usage error with a different exit
  behavior; confirm the actual observed exit code/stderr before 3.3
  lands.
- [x] **3.2** [TEST] Same file — add
  `test_ingest_rejects_event_date_with_a_directory_before_any_write` and
  `test_ingest_rejects_event_date_with_a_glob_before_any_write` (ingestion
  spec "A directory input..."/"A glob input..., even matching one file").
  Use a directory containing one file, and separately a glob matching
  exactly one file. Assert `exit_code == 2`, the exact wording from
  design.md Decision 5, and a zero-writes tree snapshot for every file
  the directory/glob would have touched. **RED today**: `--event-date` is
  unrecognized (same collection-time failure as 3.1).
- [x] **3.3** [IMPL] `src/openkos/cli/main.py`: add
  `event_date: str | None = typer.Option(None, "--event-date",
  metavar="YYYY-MM-DD", help=...)` to `ingest`. In the command body,
  before `_expand_batch_sources` (`cli/main.py:4909`): (a) if `event_date`
  is given, call `source_date.parse_event_date(event_date)`; on `None`,
  `typer.echo(..., err=True)` the exact wording from Decision 5 and
  `raise typer.Exit(code=2)`; (b) if the parsed flag is set and `not
  src.is_file() and (src.is_dir() or any(c in str(src) for c in
  _GLOB_MAGIC_CHARS))`, echo the directory/glob refusal wording and exit
  2. Makes 3.1 and 3.2 GREEN.

### Threading and preview/warning (Decisions 3, 7)

- [x] **3.4** [TEST] Same file — add
  `test_ingest_writes_event_date_from_a_valid_flag_and_prints_preview_line`
  (ingestion spec "A valid flag value is accepted" / "The line names the
  flag origin"). GIVEN a plain file with no dated name, WHEN `openkos
  ingest <path> --event-date 2026-07-14` completes, THEN the generated
  Source's frontmatter carries `event_date: '2026-07-14'` and stdout
  contains `event date 2026-07-14 (from --event-date)`. **RED today**:
  `_ingest_single` never forwards `event_date` to
  `compose_source_document` — the key is absent.
- [x] **3.5** [IMPL] `src/openkos/cli/main.py`: add `event_date: date |
  None = None` parameter to `_ingest_single`; the single-file branch at
  `cli/main.py:4918` passes the parsed flag from 3.3 and `source_name=
  src.name`; `_ingest_batch` never passes either. Thread
  `event_date_flag`/`source_name` into the `compose_source_document`
  call. Makes 3.4 GREEN.
- [x] **3.6** [TEST] Same file — add
  `test_ingest_writes_event_date_from_file_name_with_no_flag` (ingestion
  spec "The file name is used when no flag is given" / "The line names
  the file-name origin"). GIVEN a plain file named
  `call-2026-07-14.txt`, WHEN `openkos ingest <path>` completes with no
  flag, THEN `event_date: '2026-07-14'` is written and stdout contains
  `event date 2026-07-14 (from the file name)`. **RED today**: same gap
  as 3.4, plus the preview line does not exist regardless.
- [x] **3.7** [TEST] Same file — add
  `test_ingest_reingest_keeps_event_date_with_no_flag` and
  `test_ingest_reingest_overwrites_event_date_with_a_differing_flag`
  (ingestion spec "Re-ingest with no flag keeps the stored value" /
  "A differing flag overwrites the stored value" / "The line names a
  carried-forward value as kept" / "An overwrite names both the old and
  new value"). First ingest with `--event-date 2026-07-14`; re-ingest
  with no flag → stored value kept, stdout `event date 2026-07-14 (kept
  from the existing Source)`; re-ingest again with `--event-date
  2026-08-01` → stdout `event date 2026-08-01 (from --event-date,
  replacing 2026-07-14)`. **RED today**: no preview line exists yet.
- [x] **3.8** [IMPL] `src/openkos/cli/main.py`: add the preview line
  after the `bundle/sources/{slug}.md` line
  (`cli/main.py:5463-5466`/`5474`), printed only when `resolution.value
  is not None`, using the exact wording table from design.md Decision 7
  (flag / flag-replacing / file name / kept). Makes 3.6 and 3.7 GREEN.
- [x] **3.9** [TEST] Same file — add
  `test_ingest_warns_on_malformed_stored_event_date_and_does_not_carry_it_forward`
  (ingestion spec "A malformed stored value is not carried forward and
  warns"). GIVEN a Source with a hand-edited malformed `event_date` (e.g.
  a `datetime` value or `'2026-13-01'`), WHEN re-ingested with no flag and
  no dated file name, THEN the stderr warning
  `openkos ingest: ignoring the malformed event_date <repr> in
  'bundle/sources/<slug>.md' -- expected YYYY-MM-DD.` is printed exactly
  once, and the regenerated frontmatter carries no `event_date`. **RED
  today**: no warning is printed and `read_event_date`'s malformed
  signal is not surfaced by the CLI.
- [x] **3.10** [TEST] Same file — add
  `test_ingest_fills_malformed_stored_event_date_from_flag_or_file_name`
  (ingestion spec "A malformed stored value is filled by the flag or the
  file name"). Same malformed-stored setup, re-ingested with a valid
  flag (or a dated file name and no flag) → the resolved value fills the
  gap, and the same warning still prints. **RED today**: same gap as
  3.9.
- [x] **3.11** [IMPL] `src/openkos/cli/main.py`: after
  `compose_source_document` returns, when `resolution.stored_malformed`
  is true, print the exact stderr warning from design.md Decision 7.
  Makes 3.9 and 3.10 GREEN.
- [x] **3.12** [TEST] Same file — add
  `test_ingest_prints_no_event_date_line_when_none_is_recorded` (ingestion
  spec "No line is printed when no event date is recorded"). GIVEN a
  plain file with no dated name and no flag, THEN stdout contains no
  `event date` line. **RED today**: trivially green once 3.8 lands
  correctly (the `is not None` guard) — write this test BEFORE 3.8 to
  pin the negative case, and confirm it is RED only in the sense that the
  positive-case tests above are RED; if this specific assertion already
  passes on the unmodified CLI (no line exists at all yet), note that and
  treat it as a regression guard co-landed with 3.8, not a standalone RED
  task.

### Convergence and carried markers (Decision 6)

- [x] **3.13** [TEST] Same file — add
  `test_ingest_converged_reingest_with_differing_flag_rewrites_with_no_extraction`
  (ingestion spec "A differing flag on a converged Source rewrites it with
  no extraction"). GIVEN a Source that is unchanged, already extracted,
  carries `origin_key`, stored `event_date: '2026-07-10'`, WHEN
  `openkos ingest <path> --event-date 2026-07-14` runs with a stub LLM
  backend whose `chat` raises, THEN the Source is rewritten with the new
  date, `chat` is never called, no derived file changes, and the
  existing `extraction_status`/`extraction_notice` markers survive
  unchanged. **RED today**: the convergence gate at `cli/main.py:5264-5293`
  returns early on every unchanged/extracted/keyed Source regardless of
  `event_date`, so nothing is rewritten and the preview never appears —
  `AssertionError` on the unwritten frontmatter.
- [x] **3.14** [TEST] Same file — add
  `test_ingest_converged_reingest_backfills_event_date_from_file_name`
  (ingestion spec "A pre-feature Source with a dated file name gains the
  date on plain re-ingest"). GIVEN a pre-feature converged Source (no
  stored `event_date`) whose file name carries a single dated token, WHEN
  re-ingested with no flag, THEN the Source is rewritten with the
  inferred date, no LLM call is made, no derived object changes. **RED
  today**: same convergence-gate gap as 3.13.
- [x] **3.15** [TEST] Same file — add
  `test_ingest_converged_reingest_with_equal_resolved_date_writes_nothing`
  (ingestion spec "A resolved date equal to the stored value writes
  nothing"). GIVEN a converged Source stored with `event_date:
  '2026-07-14'`, WHEN re-ingested with `--event-date 2026-07-14` (or a
  plain re-ingest resolving the same stored value), THEN a tree snapshot
  shows zero writes — today's convergence behavior preserved exactly.
  This is a regression pin against today's behavior and should already
  be GREEN once 3.16 correctly gates on `changed`; write it alongside
  3.16 and confirm it stays GREEN through the change, not RED-then-GREEN.
- [x] **3.16** [IMPL] `src/openkos/cli/main.py` +
  `src/openkos/application/ingest.py`: change the convergence gate
  (`cli/main.py:5264-5293`) to skip only when `not
  source_plan.event_date.changed`. When converged and `changed`, call
  `stage_derived_objects(carried=converged)` (2.9's short-circuit) instead
  of returning early, then proceed through `compose_catalog_update` with
  the carried markers and the new `event_date`, the ordinary preview,
  confirm gate, drift guard, `write_atomic`, and autocommit. Guard
  `observability.stage_notice(...)` and
  `_render_staged_derived_objects(staged)` with `converged is None`, so no
  extraction wording prints when nothing was extracted. Set
  `_SingleIngestOutcome.extraction_skipped = converged is not None` and
  `extraction_degraded = skip_reason is not None and converged is None`.
  Makes 3.13 and 3.14 GREEN, and keeps 3.15 GREEN.
- [x] **3.17** [TEST] Same file — add
  `test_ingest_converged_date_only_rewrite_is_idempotent` (ingestion spec
  "The date-only rewrite is idempotent"). GIVEN a Source just rewritten
  by 3.13's date-only rewrite, WHEN the identical `--event-date` command
  runs again, THEN a tree snapshot shows zero writes on the second run
  (resolution is `kept`, `changed` is `False`). **RED today**: N/A —
  this becomes meaningfully testable only once 3.16 exists; write and
  confirm GREEN immediately after 3.16 lands, as its own regression pin
  rather than a RED-first task (the property being tested is emergent
  from 2.2 + 3.16 together, not a new code path of its own).
- [x] **3.18** [TEST] Same file — add
  `test_ingest_never_writes_event_date_on_a_derived_concept` (ingestion
  spec "Derived objects never carry an event date"). GIVEN a source
  ingested with a resolved `event_date` whose extraction yields one or
  more derived objects, THEN none of the derived documents' frontmatter
  carries `event_date`. **RED today**: trivially true today since no
  derived-object code path ever receives `event_date` (design.md "Out of
  Scope" #2/#7) — confirm by running before any slice-3 code lands, and
  treat this as a permanent regression guard rather than a RED-then-GREEN
  pin.

### Layering docstring and docs

- [x] **3.19** [IMPL] `docs/cli.md`: add one row to the `ingest` flag table
  (`:134-138`) for `--event-date`, covering the YYYY-MM-DD validation,
  the flag/stored/file-name precedence on re-ingest, and the directory/
  glob refusal — matching the wording style of the existing
  `--re-extract` row.

### Slice 3 verification

- [x] **3.20** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **3.21** Run `uv run pytest --cov` (unpiped) — must be green at the
  90% branch-coverage gate, including every characterization file from
  design.md's Testing Strategy table, unedited.
- [x] **3.22** Manually smoke the runtime harness named in the Suggested
  Work Units table: `openkos init` a scratch workspace, `openkos ingest
  <fixture> --event-date 2026-07-14`, confirm the frontmatter and preview
  line; re-ingest with no flag and confirm `kept`; re-ingest with a
  differing flag and confirm `replacing` plus the rewrite-with-no-LLM-call
  behavior on a converged Source.
- [x] **3.23** Commit as one or more work-unit commits, scope `cli` (e.g.
  `feat(cli): add --event-date to ingest with refusals, carry-forward,
  and the converged date-only rewrite`) and a `docs` commit for the
  `docs/cli.md` row, tests alongside behavior. Open PR 3 targeting PR 2's
  branch.

---

## Post-merge (archive phase, not a task here)

Per `openspec/config.yaml`'s `rules.archive`, the archive phase — not this
task list — merges the three delta specs
(`openspec/changes/source-event-time/specs/{ingestion,ingest-application-service,entity-resolution-merge}/`)
into their living `openspec/specs/{domain}/spec.md` files, and flips
ADR-0023's status from `Proposed` to `Accepted` in both the frontmatter and
the body `**Status:**` line, plus its `docs/adr/README.md` index row. No
task above performs either of these; they are explicitly out of scope for
`sdd-apply`.
