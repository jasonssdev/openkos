# Tasks: preserve-source-frontmatter — keep an incoming file's frontmatter and lift a safe subset

Refs #1062. Proposal: `proposal.md` (decisions 1-11). Design: `design.md`
(decisions 1-10, ADR-0030). Exploration: Engram
`sdd/preserve-source-frontmatter/explore`.

Strict TDD is ON, runner `uv run pytest`. Every behavioral task pairs a
`[TEST]` task — observed RED with the reason it is RED today — with the
`[IMPL]` task that turns it GREEN, in that order. `[IMPL]` tasks introduce
only what their paired `[TEST]` already pins.

> A property test whose two sides call the same function under test is
> circular: pair it with a literal expected-value test built independently.
> Every absence assertion needs a precondition proving the thing existed
> first.

Revert every mutation with the inverse edit (never `git checkout --`), and
purge `__pycache__` before trusting a verdict. Every fixture built from a
real `ingest`/`merge`/`repair` call runs in `tmp_path`; no test reaches
Ollama (a fake `LLMBackend` stands in for extraction everywhere except the
existing eval harnesses).

**Threat Matrix**: design.md marks the skill's routing/shell/subprocess
matrix N/A — this change adds no routing, shell command, subprocess,
VCS/PR automation, executable-file classification, or process-integration
boundary. It DOES add one new boundary: parsing YAML this engine did not
write. Design's Decision 1 fail-closed table (10 ordered checks) and the
Testing Strategy's parser/lift rows are the applicable threat-matrix
equivalent for that boundary and MUST propagate unchanged into RED tests —
done below in Phase 1 (parser) and Phase 3 (lift).

**Tasks-phase decisions (this file, not settled by proposal/design):**

1. **Decision 4 (LLM-send floor) rides on #1087, not a new field.** The
   proposal/design session flagged a concurrent fix, #1086/PR #1087, that
   changes `cli/main.py`'s `_ingest_single` extraction-gate call from
   `workspace_floor=cfg.default_sensitivity` to
   `workspace_floor=source_plan.source_sensitivity` (currently still
   `cfg.default_sensitivity` at `src/openkos/cli/main.py:5450` — confirmed
   by direct read during this phase). `source_plan.source_sensitivity` is
   already a READ-BACK of the Source's fully resolved `sensitivity`
   (`application/ingest.py:1031-1032`, `SourceDocumentPlan` docstring:
   "guaranteed to equal `resolved_sensitivity` but read back rather than
   assumed"). Once Phase 3 folds the incoming `sensitivity` lift into
   `resolved_sensitivity` (Decision 3's sensitivity fold), and once #1087
   merges, the gate is ALREADY floored by the lift with no new
   `llm_send_floor` field, no new `SourceDocumentPlan` field, and no new
   `cli/main.py` gate-argument edit. Design's `llm_send_floor` dataclass
   field is therefore NOT implemented as a separate field; Phase 3 instead
   ships a `[TEST]`-only confirmation (3.13-3.14 below) that the gate is
   floored correctly through the existing `source_sensitivity` read-back,
   once rebased onto #1087. See task 3.0 (precondition) for the rebase
   itself.
2. **`IncomingLift.event_date` stays unset until both Phase 3 and Phase 4
   land; `read_incoming_date` is called directly, not through
   `lift_incoming_frontmatter`.** Design's slice table states Phases 3 and
   4 are independent of each other and both depend only on Phase 2, i.e.
   either may merge first. If `lift_incoming_frontmatter` (introduced in
   Phase 3) were the only way to reach a lifted date, Phase 4 could not
   land first. To preserve true either-order independence,
   `compose_source_document` calls `okf.lift_incoming_frontmatter(mapping)`
   for tags/sensitivity (Phase 3) and `okf.read_incoming_date(mapping)`
   directly, as a SEPARATE read of the same already-parsed mapping, for the
   date tier (Phase 4). `IncomingLift.event_date` remains `None` in the
   dataclass's actual runtime value (the field stays in the interface per
   design's contract, for forward compatibility, but nothing sets it to a
   real value in this change); nothing reads it. A follow-up MAY fold
   `read_incoming_date` into `lift_incoming_frontmatter` once both land, but
   nothing in this change requires it.
3. **Date-tier tests for `read_incoming_date`/`_tolerant_date` live in
   `tests/unit/model/test_okf.py`**, beside the existing `read_event_date`
   tests they refactor, not in the new `test_okf_incoming_frontmatter.py`
   file (which holds parser + lift tests only, matching design's File
   Changes table).

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~1,450-2,300 total (design.md "Migration / Rollout"), across 6 chained PRs |
| 400-line budget risk | High overall; each individual slice is scoped to land under or near 400 |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (Phase 1: parse seam + ADR-0030) → PR 2 (Phase 2: verbatim preserve) → PR 3 (Phase 3: tag + sensitivity lift, after PR 2) → PR 4 (Phase 4: date lift, after PR 2, independent of PR 3) → PR 5 (Phase 5: derived tag propagation, after PR 3) → PR 6 (Phase 6: docs, after PR 2, may run parallel with PRs 3-5) |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

`auto-chain` means the orchestrator proceeds with this slice order and
`stacked-to-main` with no further decision gate; the owner has pre-approved
`size:exception` for any slice that genuinely cannot be split further.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|-----------------------|------------------|--------------------|
| 1 (Phase 1) | `okf.frontmatter_block_end` (moved from `source_title._frontmatter_end`), `IncomingFrontmatter`/`parse_incoming_frontmatter` with the fail-closed status table, domain + round-trip gates, boundary parity test, ADR-0030 | PR 1 → `main` | `uv run pytest tests/unit/model/test_okf_incoming_frontmatter.py tests/unit/test_source_title.py` | N/A — pure library functions with no production caller yet beyond `source_title`, which is already exercised by `derive_source_title`'s existing suite | Revert `frontmatter_block_end`/`parse_incoming_frontmatter`/`IncomingFrontmatter`/the domain+round-trip helpers, restore `source_title._frontmatter_end`, and revert ADR-0030 + its README row; no bundle byte has changed |
| 2 (Phase 2) | `SOURCE_FRONTMATTER_KEY` emission in `build_source_concept`; `compose_source_document` wiring for BOTH `build_source_concept` calls (the fresh build and `compose_catalog_update`'s conditional rebuild); `source_frontmatter` in `_SPECIAL_KEYS`; `lift_changed` (frontmatter delta only) gating the CLI skip condition; `migrate_document` Unchanged pin | PR 2 → `main`, after PR 1 | `uv run pytest tests/unit/model/test_okf.py tests/unit/application/test_ingest.py tests/unit/cli/test_ingest.py -k frontmatter` | `uv run openkos ingest <fixture-with-frontmatter>` in `tmp_path` — the written Source carries `source_frontmatter`; a frontmatter-free fixture stays byte-identical | Revert the `source_frontmatter` parameter on `build_source_concept`, both `compose_source_document`/`compose_catalog_update` call sites, `_SPECIAL_KEYS`'s new entry, `lift_changed`, and the CLI skip-condition edit; Phase 1's parser is untouched and still passes its own suite |
| 3 (Phase 3) | `IncomingLift`, `normalize_tags`, `union_tags`, `lift_incoming_frontmatter` (tags + sensitivity), stored-tag read, sensitivity fold, `lift_changed` gains tags/sensitivity deltas, preview lines, raise advisory, Decision 4 gate confirmation (post-#1087 rebase) | PR 3 → `main`, after PR 2 | `uv run pytest tests/unit/model/test_okf_incoming_frontmatter.py tests/unit/application/test_ingest.py tests/unit/cli/test_ingest.py -k "tags or sensitivity"` | `uv run openkos ingest <fixture-with-tags-and-sensitivity>` in `tmp_path`, then re-ingest with a hand-added tag — union holds, and a re-ingest of a confidential-declaring fixture is `blocked-by-sensitivity` without `--include-confidential` | Revert `IncomingLift`/`normalize_tags`/`union_tags`/`lift_incoming_frontmatter`, the tag/sensitivity wiring in `compose_source_document`, the two `lift_changed` deltas, the preview lines, and the advisory; Phase 2's `source_frontmatter` emission is untouched |
| 4 (Phase 4) | `_tolerant_date` refactor of `read_event_date`'s body, `read_incoming_date`, `resolve_event_date(incoming=)`, `EventDateOrigin` gains `"frontmatter"`, origin-disclosure line | PR 4 → `main`, after PR 2 (independent of PR 3) | `uv run pytest tests/unit/model/test_okf.py -k event_date tests/unit/application/test_ingest.py -k resolve_event_date tests/unit/cli/test_ingest.py -k event_date` | `uv run openkos ingest <fixture-with-date-and-no-flag-or-dated-name>` in `tmp_path` — `event_date` resolves from `date:`, origin line names `frontmatter` | Revert `_tolerant_date`/`read_incoming_date`, `resolve_event_date`'s `incoming` parameter, `EventDateOrigin`'s new literal, and the origin-line branch; `read_event_date`'s public behavior is provably unchanged (pinned by 4.1) |
| 5 (Phase 5) | `build_concept(tags=)` byte-identical default, `stage_derived_objects(source_tags=)`, CLI threading `source_plan.tags`, merge-union pin test | PR 5 → `main`, after PR 3 | `uv run pytest tests/unit/model/test_okf.py -k build_concept tests/unit/application/test_ingest.py -k source_tags` | `uv run openkos ingest <fixture-with-tags>` in `tmp_path` with a fake LLM returning one candidate — the derived concept's `tags` include the Source's resolved tags | Revert `build_concept`'s `tags` parameter, `stage_derived_objects`'s `source_tags` parameter, and the CLI call-site edit; every existing `build_concept` caller (11, including `query --save`) keeps emitting `tags: []` unchanged |
| 6 (Phase 6) | `docs/knowledge-object-model.md` and/or `docs/okf-alignment.md` note on `source_frontmatter`; open follow-up issue F1 | PR 6 → `main`, after PR 2 (parallel-eligible with PRs 3-5) | N/A — prose-only; structural readback is the proportional check | N/A | Revert the docs edit; no code behavior depends on this slice |

## Scenario / Requirement → Task Coverage

Every requirement and scenario in the three delta specs, mapped to at least
one task.

| Spec | Requirement | Scenario(s) | Coverage |
|---|---|---|---|
| ingestion | Incoming Frontmatter Parse Is Fail-Closed And Bounded | non-mapping root; malformed YAML; TOML/JSON fence not auto-detected; oversized block; alias/anchor bomb; non-UTF-8 never parsed; parse boundary agrees with title-skip boundary | 1.4-1.17 |
| ingestion | The `source_frontmatter` Namespace Preserves The Incoming Mapping Verbatim | valid frontmatter preserved verbatim; no frontmatter → no key; body still verbatim | 2.4-2.9 |
| ingestion | Never-Lifted Incoming Frontmatter Keys | type/status/provenance never asserted; author/updated namespace-only; created never lifted for event-date | 3.15-3.16 (never-lifted fixture, parametrized over Decision 4's list) |
| ingestion | Source Tag Lift And Re-Ingest Union | list lifts each tag; single string lifts one tag; comma string not split; non-string item lifts none; re-ingest unions; hand-added tag survives; no incoming tags leaves Source's tags unaffected | 3.1-3.10 |
| ingestion | Converged Re-Ingest Source-Only Rewrite (renamed) | differing flag rewrites; pre-feature dated-name Source gains date; no-change converges; idempotent; newly-present frontmatter triggers rewrite; raised sensitivity triggers rewrite; tag-only difference triggers rewrite; preview names recorded frontmatter/tags-added/neither/both; sensitivity-raise advisory present/absent | 2.10-2.16 (frontmatter delta), 3.17-3.24 (tags/sensitivity deltas + preview + advisory), 4.14 (event_date delta already exists, regression-pinned) |
| ingestion | Ingest Raw Copy and Source Concept Generation (v0.2 field-set delta) | valid frontmatter yields source_frontmatter + tags; malformed yields neither, ingest still succeeds | 2.4-2.9 (shared with the namespace requirement above) |
| ingestion | Event-Date Precedence On First Ingest | flag wins over dated name; flag wins over frontmatter date; frontmatter date wins over dated name; file name used when neither given; neither leaves unset; created ignored | 4.5-4.13 |
| ingestion | Re-Ingest Event-Date Carry-Forward | stored value wins over frontmatter date; frontmatter date fills a Source with no stored value ahead of file name | 4.5-4.13 (shared table) |
| ingestion | Derived Object Provenance and Sensitivity Inheritance (tags half) | derived object created in the run inherits Source's tags; existing derived object unaffected by a later Source tag change | 5.5-5.8 |
| ingestion | Default Sensitivity from Config (sensitivity-lift half) | fresh ingest folds incoming sensitivity, raising above default; fresh ingest ignores lower incoming sensitivity; re-ingest folds on-disk+default+incoming together; unrecognized incoming sensitivity fails closed; incoming confidential blocks extraction; `--include-confidential` still allows send; lower incoming sensitivity does not lower the floor | 3.11-3.14 |
| ingestion | Event-Date Origin Disclosure Line | line names frontmatter origin | 4.11-4.13 |
| ingest-application-service | `compose_source_document` Accepts Parsed Incoming Frontmatter Lift Results | absent parameters byte-identical; given mapping/tags reach the document; service performs no parsing/lift decisions of its own | 2.4-2.9 (absent case), 3.5-3.10 (given case + no-parsing-in-service guard) |
| entity-resolution-merge | Frontmatter-Conflict Resolution (source_frontmatter exclusion) | absorbed source_frontmatter does not cross merge; survivor keeps its own | 2.17-2.20 |

---

## Phase 1 (PR 1 → `main`): Parse seam + ADR-0030

Design.md "Technical Approach" items 1-2 and Decisions 1-2, 10. Creates the
parser and the shared boundary rule; changes no writer and rewrites no
bundle byte.

### `model/okf.py` / `source_title.py` — shared boundary rule (Decision 10)

- [ ] **1.1** [TEST] `tests/unit/test_source_title.py` (existing file) — add
  `test_frontmatter_end_moved_to_okf_module`: import
  `okf.frontmatter_block_end` and assert
  `source_title._frontmatter_end is okf.frontmatter_block_end` (the module
  now re-exports/imports it rather than defining its own copy — confirm the
  exact binding style during implementation and assert that one, not a
  guessed name). **RED today**: `AttributeError` — `okf.frontmatter_block_end`
  does not exist and `source_title._frontmatter_end` is still its own
  function.
- [ ] **1.2** [TEST] Same file — add
  `test_frontmatter_block_end_parity_table`, parametrized over the edge
  table design.md Decision 10 names: no fence; an unterminated fence; an
  empty block (`---\n---`); `---` appearing inside a fenced code block
  (must NOT be treated as the frontmatter close); a UTF-8 BOM before the
  opening `---`; `--- ` with a trailing space (must NOT open a block); a
  bare `----` line (must NOT open a block); a closing fence on the very
  last line with no trailing newline; a CRLF-newline file (after
  `Path.read_text(encoding="utf-8")`'s universal-newline translation, so
  the function itself only ever sees `\n`). For each row, assert
  `okf.frontmatter_block_end(lines)` returns the exact same index that
  `source_title.derive_source_title`'s title-skip boundary used for the
  same text (call both through their real signatures, not by inlining the
  logic twice, so the two sides cannot drift). Covers ingestion scenario
  "The parse boundary agrees with title derivation's skip boundary".
  **RED today**: `AttributeError` — same missing function. **MUTATION**:
  change one boundary condition (e.g. `lines[0] != "---"` to
  `lines[0].strip() != "---"`) and confirm the BOM/trailing-space rows flip.
- [ ] **1.3** [IMPL] `src/openkos/model/okf.py`: add
  `frontmatter_block_end(lines: Sequence[str]) -> int`, moved verbatim from
  `source_title._frontmatter_end` (identical logic: `lines[0] == "---"` and
  the first later line equal to `"---"`; `0` otherwise). `src/openkos/
  source_title.py`: delete the private `_frontmatter_end` body and rebind
  the name to `okf.frontmatter_block_end` (or call it directly at both
  existing call sites — confirm which keeps `derive_source_title`'s
  existing behavior byte-identical, per design.md Decision 10). Update
  `source_title`'s module docstring from "no `openkos` imports" to "its
  only `openkos` import is the pure `okf.frontmatter_block_end`". Makes
  1.1-1.2 GREEN.

### `model/okf.py` — `parse_incoming_frontmatter`, the fail-closed status table (Decision 1)

- [ ] **1.4** [TEST] `tests/unit/model/test_okf_incoming_frontmatter.py`
  (new file) — add `test_parse_absent_cases`, parametrized: no leading
  `---`; an unterminated leading `---` (no closing fence anywhere); a
  leading UTF-8 BOM before `---`; a leading `+++` (TOML) fence; a leading
  `;;;` (JSON) fence — every case returns `IncomingFrontmatter(status="absent",
  mapping=None)`. Covers ingestion scenario "A TOML or JSON fence is not
  auto-detected" and the `absent` row of design.md Decision 1's table.
  **RED today**: `AttributeError` — `okf.parse_incoming_frontmatter` and
  `okf.IncomingFrontmatter` do not exist.
- [ ] **1.5** [TEST] Same file — add `test_parse_empty_cases`, parametrized:
  `---\n---` (nothing between the fences); a block containing only YAML
  comments; a block that parses to `{}`. Every case returns
  `status="empty"`. **RED today**: same `AttributeError`.
- [ ] **1.6** [TEST] Same file — add `test_parse_too_large_boundary`: a
  block whose UTF-8 byte length is exactly `INCOMING_FRONTMATTER_MAX_BYTES`
  (65,536) parses successfully (`status="parsed"`); a block one byte over
  that limit returns `status="too-large"`. Covers ingestion scenario "An
  oversized block lifts nothing". **RED today**: same `AttributeError`.
  **MUTATION**: change the byte-cap comparison from `>` to `>=` and confirm
  the exact-limit row flips to `too-large`.
- [ ] **1.7** [TEST] Same file — add `test_parse_alias_cases`, parametrized:
  a "billion laughs" alias-amplification block; a single anchor with no
  alias referencing it (must NOT be rejected — only an actual `AliasEvent`
  or a non-`None` `anchor` on an event triggers this); a self-referencing
  anchor/alias pair (`&a [*a]`). The amplification and self-reference cases
  return `status="alias"`; the lone-anchor case returns `status="parsed"`
  with the anchor resolved as an ordinary value. Covers ingestion scenario
  "An alias/anchor expansion bomb lifts nothing". **RED today**: same
  `AttributeError`. **MUTATION**: remove the anchor/alias pre-scan check
  entirely and confirm the billion-laughs row no longer returns `"alias"`
  (it must not hang or exhaust memory either, but the status assertion
  alone proves the guard fired).
- [ ] **1.8** [TEST] Same file — add `test_parse_too_deep_boundary`: a
  block with nested-collection depth exactly
  `INCOMING_FRONTMATTER_MAX_DEPTH` (32) parses (`status="parsed"`); depth
  33 returns `status="too-deep"`. **RED today**: same `AttributeError`.
- [ ] **1.9** [TEST] Same file — add `test_parse_malformed_cases`,
  parametrized: bad YAML indentation; `!!python/object/apply:os.system`
  (a Python-object tag `SafeLoader` refuses); an unknown custom `!tag`; two
  YAML documents separated by `...` inside the same block (more than one
  `DocumentStartEvent`). Every case returns `status="malformed"`. **RED
  today**: same `AttributeError`. Kills a parser that silently accepts a
  `!!python/*` tag instead of refusing it.
- [ ] **1.10** [TEST] Same file — add `test_parse_not_a_mapping_cases`,
  parametrized: a YAML list root; a bare scalar root (a string, an int).
  Both return `status="not-a-mapping"`. Covers ingestion scenario "A
  non-mapping root lifts nothing". **RED today**: same `AttributeError`.
- [ ] **1.11** [IMPL] `src/openkos/model/okf.py`: add
  `SOURCE_FRONTMATTER_KEY: Final = "source_frontmatter"`,
  `INCOMING_FRONTMATTER_MAX_BYTES: Final = 64 * 1024`,
  `INCOMING_FRONTMATTER_MAX_DEPTH: Final = 32`,
  `IncomingFrontmatterStatus = Literal["absent", "empty", "parsed",
  "too-large", "alias", "too-deep", "malformed", "not-a-mapping",
  "unsupported-value"]`, `@dataclass(frozen=True) class IncomingFrontmatter:
  status: IncomingFrontmatterStatus; mapping: Mapping[str, object] | None`,
  and `parse_incoming_frontmatter(text: str) -> IncomingFrontmatter`
  implementing design.md Decision 1's 10-check ordered table through checks
  1-8 only (checks 9-10, the plain-data domain and round-trip gates, land
  in 1.13 below): extract the block via `frontmatter_block_end`; size check
  via `.encode("utf-8")`; one `yaml.parse(..., Loader=yaml.SafeLoader)`
  event-stream pre-scan catching any non-`None` `anchor`/`AliasEvent`
  (`alias`), nesting depth > 32 (`too-deep`), and more than one
  `DocumentStartEvent` (`malformed`); `yaml.load(..., Loader=yaml.SafeLoader)`
  with every exception mapped to `malformed`; `None`/`{}` → `empty`;
  non-`dict` → `not-a-mapping`. The function never raises — every exception
  in the two PyYAML calls is caught and mapped to a status, matching the
  codebase's existing "`yaml.YAMLError` is not a `ValueError`" lesson
  (#942). Makes 1.4-1.10 GREEN.

### `model/okf.py` — plain-data domain + round-trip gate (Decision 2)

- [ ] **1.12** [TEST] Same file — add `test_parse_unsupported_value_cases`,
  parametrized: `!!binary` (bytes); `!!set`; a non-`str` key at the top
  level; a non-`str` key nested inside a value; `.nan`; `.inf`. Every case
  returns `status="unsupported-value"` for the WHOLE block, not a
  partially-lifted mapping. Covers design.md Decision 2's plain-data domain
  check. **RED today**: `AttributeError` — the domain check does not exist
  yet (naive parse would currently succeed with these values embedded,
  since 1.11 stops at `not-a-mapping`).
- [ ] **1.13** [TEST] Same file — add
  `test_parse_round_trip_gate_rejects_nan_accepts_dates`: a mapping
  containing `.nan` is caught by the DOMAIN check (1.12) before the
  round-trip gate is even reached — confirm this ordering explicitly by
  asserting `unsupported-value`, not a separate round-trip failure status;
  a mapping containing a `date`, a `datetime`, a nested map, and unicode
  text round-trips through `load_frontmatter(dump_frontmatter({KEY: m}))
  [0][KEY] == m` and returns `status="parsed"` with that exact mapping.
  **RED today**: `AttributeError`. **MUTATION**: skip the round-trip gate
  entirely and confirm a value that fails to round-trip (construct one via
  a monkeypatched `dump_frontmatter` that corrupts a key) still reports
  `"parsed"` instead of `"unsupported-value"` — proving the gate, not just
  the domain check, is load-bearing.
- [ ] **1.14** [IMPL] Same module: add a private `_is_plain_data(value:
  object) -> bool` recursive domain check (`str`, `bool`, `int`, finite
  `float`, `None`, `date`, `datetime`, `list` of domain values, `dict` with
  `str` keys and domain values), and wire checks 9-10 into
  `parse_incoming_frontmatter` after the `dict`-root check from 1.11: reject
  to `unsupported-value` on a domain failure, then reject to
  `unsupported-value` again if
  `load_frontmatter(dump_frontmatter({SOURCE_FRONTMATTER_KEY: mapping}))
  [0][SOURCE_FRONTMATTER_KEY] != mapping`. Only a mapping passing BOTH gates
  reaches `status="parsed"`. Makes 1.12-1.13 GREEN.
- [ ] **1.15** [TEST] Same file — add
  `test_parse_never_raises_on_non_utf8_or_binary_garbage`: feed
  `parse_incoming_frontmatter` a handful of adversarial strings (a lone
  surrogate escape decoded leniently upstream, a string with embedded NUL
  bytes inside the block) and assert no exception ever propagates —
  every case resolves to some member of `IncomingFrontmatterStatus`.
  **RED today**: passes vacuously if 1.11/1.14 already caught every
  exception path; this is a regression confirmation, not new behavior — if
  RED, a raise path was missed in 1.11/1.14 and must be closed there.

### Non-UTF-8 / blank guard (never parsed at all)

- [ ] **1.16** [TEST] Same file — add
  `test_parse_is_never_called_for_non_utf8_or_blank_sources`: this is a
  CALL-SITE contract, not a `parse_incoming_frontmatter` behavior — spy
  (`unittest.mock.patch`) on `okf.parse_incoming_frontmatter` and assert it
  is never invoked when `raw_content is None` (non-UTF-8/binary source) or
  `raw_content` is blank/whitespace-only, mirroring the EXISTING guard
  `compose_source_document` already applies before calling
  `source_title.derive_source_title` (`application/ingest.py:966-969`).
  Covers ingestion scenario "A source that is not valid UTF-8 is never
  parsed for frontmatter". **RED today**: `AttributeError` — the call site
  does not exist until Phase 2 wires it; write this test now as a
  precondition pin so Phase 2's wiring (2.5-2.7) is provably correct from
  its first commit, and mark it `xfail(strict=True, reason="wired in
  Phase 2")` until then, or defer this specific test to Phase 2 — implementer
  chooses whichever keeps Phase 1 self-contained; if deferred, note it
  explicitly in the Phase 2 section instead of silently dropping it.
- [ ] **1.17** [DOC] No implementation task here — 1.16's guard is
  satisfied by REUSING `compose_source_document`'s existing `raw_content
  is None or not raw_content.strip()` check (Phase 2 wires the new call
  behind the SAME guard, not a second one). Tracked here as a checklist
  item so Phase 2 does not silently drop it.

### ADR-0030

- [ ] **1.18** [DOC] Create `docs/adr/0030-untrusted-incoming-frontmatter.md`
  from `docs/adr/template.md`: Context (this is the first time the engine
  parses YAML it did not write), Decision (adopt a fail-closed, bounded,
  YAML-only parser with a closed per-key lift allow-list; store the whole
  mapping verbatim under `source_frontmatter`), Consequences (what becomes
  easier: users' existing curation metadata reaches retrieval/filtering;
  what becomes harder: tags on existing derived concepts can go stale until
  follow-up F1 ships), Alternatives Considered (from proposal.md's decision
  table and design.md Decision 1/2's "Alternatives considered" — `python-
  frontmatter`'s auto-detecting handler, a deny-list instead of an
  allow-list, byte-verbatim storage, comma-splitting tags). Status
  `Proposed`, dated today.
- [ ] **1.19** [DOC] `docs/adr/README.md`: add the ADR-0030 index row in
  numeric order.

### Phase 1 verification

- [ ] **1.20** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [ ] **1.21** Run `uv run pytest tests/unit/model/test_okf_incoming_frontmatter.py
  tests/unit/test_source_title.py` focused, then `uv run pytest --cov`
  (unpiped) full suite — must be green, 90% branch gate held.
- [ ] **1.22** Run `uv run python evals/run_self_tests.py` — must be green
  (no eval harness touches frontmatter parsing, but the sweep must still
  pass with `OLLAMA_HOST` poisoned).
- [ ] **1.23** Commit as one or more work-unit commits, scope `okf` (e.g.
  `feat(okf): parse and validate incoming source frontmatter, fail-closed`),
  staging ADR-0030 and its README index row in the same commit set. Open
  PR 1 (Phase 1: parse seam + ADR-0030) targeting `main`.

**Rollback boundary**: revert `frontmatter_block_end`/
`parse_incoming_frontmatter`/`IncomingFrontmatter`/`_is_plain_data`, restore
`source_title._frontmatter_end`'s own body, and revert both ADR files (or
leave the ADR in place — reverting it is optional and affects no later
slice, since no slice's code depends on the ADR file's presence). No bundle
byte has changed by this point.

---

## Phase 2 (PR 2 → `main`, after PR 1 merges): Verbatim preserve

Design.md Decisions 1's boundary consumer, Decision 2's storage rule,
Decision 7 (Source-only rewrite, frontmatter delta only), Decision 8
(`_SPECIAL_KEYS`), and Decision 9's `migrate_document` pin. No tag or
sensitivity lift yet — that is Phase 3.

### `model/okf.py` — `build_source_concept(source_frontmatter=...)`

- [ ] **2.1** [TEST] `tests/unit/model/test_okf.py` — add
  `test_build_source_concept_emits_source_frontmatter_when_given`: calling
  `build_source_concept(..., source_frontmatter={"tags": ["alpha"], "author":
  "A"})` yields frontmatter carrying `source_frontmatter` equal to that
  exact mapping, deep-copied (mutating the caller's dict after the call
  does not change the built document — assert via a follow-up mutation of
  the input after the call). Covers ingestion scenario "Valid frontmatter is
  preserved verbatim under source_frontmatter". **RED today**: `TypeError`
  — `build_source_concept` has no `source_frontmatter` parameter.
- [ ] **2.2** [TEST] Same file — add
  `test_build_source_concept_omits_source_frontmatter_when_none`: calling
  `build_source_concept(...)` with no `source_frontmatter` argument (the
  default) produces a document byte-identical to the CURRENT pre-Phase-2
  golden for the same inputs — no `source_frontmatter` key at all, not an
  empty one. Covers "No incoming frontmatter means no source_frontmatter
  key" and the ingest-application-service scenario "Absent frontmatter
  parameters produce a byte-identical Source" (builder half). **RED
  today**: `TypeError` — same missing parameter; write this test BEFORE
  2.4 adds the parameter so the byte-identity claim is provably pinned pre-
  change, not assumed. **MUTATION**: make the new code path always insert
  `source_frontmatter: None` even when absent, and confirm this test
  catches the extra key (a `None`-valued key is not "no key").
- [ ] **2.3** [TEST] Same file — add
  `test_build_source_concept_no_anchor_or_alias_when_tags_share_values_with_source_frontmatter`:
  build a document where a lifted tag list and `source_frontmatter["tags"]`
  reference the exact SAME list object at the call site, and assert the
  rendered YAML frontmatter text contains no `&` anchor and no `*` alias
  marker. Covers design.md Decision 2's "no shared Python object" guarantee
  — a real hazard because `SafeDumper` anchors a shared object and this
  engine's own parser would then reject it on the next read. **RED today**:
  passes vacuously today (no `source_frontmatter` parameter exists to share
  an object with) — write it to fail once 2.4 exists WITHOUT a `deepcopy`,
  then confirm it passes only after 2.4 does the copy; if it is green on
  first run after 2.4, temporarily remove the `copy.deepcopy` call and
  confirm this test THEN fails, to prove it is exercised (mutation-proof
  per this file's header rule).
- [ ] **2.4** [IMPL] `src/openkos/model/okf.py`: add `source_frontmatter:
  Mapping[str, object] | None = None` to `build_source_concept`'s
  signature; when not `None`, emit `metadata[SOURCE_FRONTMATTER_KEY] =
  copy.deepcopy(source_frontmatter)` (import `copy` at module top). Makes
  2.1-2.3 GREEN.

### `application/ingest.py` — `compose_source_document` wiring (both builds)

- [ ] **2.5** [TEST] `tests/unit/application/test_ingest.py` — add
  `test_compose_source_document_parses_and_forwards_frontmatter`: a fixture
  whose decoded `raw_content` opens with a well-formed YAML frontmatter
  block reaches `compose_source_document`, and the returned
  `SourceDocumentPlan.content`'s frontmatter carries `source_frontmatter`
  equal to the parsed mapping. Covers ingest-application-service scenario
  "A given frontmatter mapping and tag list reach the generated document"
  (frontmatter half; tags land in Phase 3) and ingestion's "Valid incoming
  frontmatter yields source_frontmatter and lifted tags" (frontmatter
  half). **RED today**: `AssertionError` — `compose_source_document` does
  not call `parse_incoming_frontmatter` yet.
- [ ] **2.6** [TEST] Same file — add
  `test_compose_source_document_malformed_frontmatter_lifts_nothing`: a
  fixture whose leading block is malformed YAML still produces a
  successful plan with no `source_frontmatter` key and unchanged
  `raw_content`/body. Covers "Malformed incoming frontmatter yields
  neither, and ingest still succeeds". **RED today**: `AssertionError` if
  wired naively to propagate an exception — 1.11 already guarantees
  `parse_incoming_frontmatter` never raises, so this test pins the CALL
  SITE also never raises, not just the parser.
- [ ] **2.7** [TEST] Same file — add
  `test_compose_source_document_frontmatter_free_is_byte_identical`: a
  fixture with NO leading frontmatter block produces a
  `SourceDocumentPlan.content` byte-identical to the plan computed by the
  SAME inputs before this parameter existed (compare against a frozen
  golden captured before 2.8 lands, or reuse an existing ingest golden
  fixture). Covers ingest-application-service's byte-identity scenario at
  the service-composition level (2.2 pinned it at the builder level).
  **RED today**: passes vacuously until 2.5 is wired — write it alongside
  2.5/2.6 and confirm it stays GREEN once 2.8 lands (a genuine regression
  pin, not a new behavior).
- [ ] **2.8** [IMPL] `src/openkos/application/ingest.py`: inside
  `compose_source_document`, after the existing `raw_content is None or not
  raw_content.strip()` guard (the SAME guard that already gates
  `derive_source_title`, per 1.16/1.17's plan), call
  `okf.parse_incoming_frontmatter(raw_content)` when the guard passes;
  when `result.status == "parsed"`, pass `source_frontmatter=result.mapping`
  to `build_source_concept`; otherwise pass `source_frontmatter=None`
  (every non-`"parsed"` status, including `"absent"`, is treated
  identically here — Phase 3 differentiates `"parsed"`'s mapping content
  for lifting, but no other status ever reaches the builder). Makes
  2.5-2.7 GREEN.
- [ ] **2.9** [TEST] `tests/unit/application/test_ingest.py` — add
  `test_compose_catalog_update_second_build_carries_source_frontmatter`:
  drive a run where `stage_derived_objects` returns a non-`None`
  `skip_reason` (forcing `compose_catalog_update`'s conditional rebuild
  branch at `application/ingest.py:1101-1115`) on a source WITH parsed
  frontmatter, and assert the REBUILT `concept_content`'s frontmatter still
  carries `source_frontmatter` — today's second `build_source_concept`
  call at that line hard-codes `tags=[]` and passes no
  `source_frontmatter=` argument at all, so it silently drops both on any
  run that takes this branch. **RED today**: `AssertionError` — the
  rebuild's `source_frontmatter` is absent even though the first build had
  it. **MUTATION**: after fixing, revert only this call site's
  `source_frontmatter=` argument and confirm this test fails (proves the
  test exercises the SECOND build, not the first).
- [ ] **2.10** [IMPL] `src/openkos/application/ingest.py`: give
  `SourceDocumentPlan` a `source_frontmatter: Mapping[str, object] | None =
  None` field, set it in `compose_source_document`'s return alongside
  `content`/`event_date` (same mapping value passed to the first
  `build_source_concept` call), and pass
  `source_frontmatter=source.source_frontmatter` into
  `compose_catalog_update`'s conditional rebuild call
  (`application/ingest.py:1101-1115`). Makes 2.9 GREEN. (The rebuild's
  `tags=[]` hard-code is fixed in Phase 3, alongside `SourceDocumentPlan.
  tags` — do not fix it here to keep this slice's diff scoped to
  `source_frontmatter` only.)

### CLI skip condition — frontmatter delta (Decision 7, part 1 of 3)

- [ ] **2.11** [TEST] `tests/unit/cli/test_ingest.py` — add
  `test_reingest_converged_source_with_new_frontmatter_triggers_rewrite`:
  build a Source via a real ingest with NO frontmatter, confirm it
  converges to a no-op on a plain re-ingest (existing behavior, asserted as
  a PRECONDITION before the new behavior is exercised), then re-ingest the
  SAME `raw/` file after replacing it with content that now opens with a
  well-formed frontmatter block the parser accepts — assert the Source
  concept is rewritten to carry the newly-parsed `source_frontmatter`, the
  fake LLM's `chat` method is asserted NEVER called (no extraction), and no
  derived object is created, modified, or removed. Covers ingestion
  scenario "Newly-present incoming frontmatter on an otherwise converged
  Source triggers a rewrite". **RED today**: `AssertionError` — the current
  skip condition (`converged is not None and not source_plan.event_date.
  changed`, `cli/main.py:5388`) fires regardless of a new
  `source_frontmatter`, so the run wrongly reports "skipping extraction"
  with nothing rewritten.
- [ ] **2.12** [TEST] Same file — add
  `test_reingest_converged_source_with_unchanged_frontmatter_stays_converged`:
  the PRECONDITION-then-check pair for the opposite case — a Source already
  carrying `source_frontmatter` equal to what THIS run would parse converges
  and writes nothing, exactly as before this feature existed. Covers
  "A converged Source with no changes writes nothing" (frontmatter half;
  Phase 3 extends this table with the tags/sensitivity rows). **RED
  today**: passes vacuously (no `lift_changed` exists yet, so nothing new
  could fire) — write it now as a regression pin so 2.13's new condition
  cannot accidentally widen the skip.
- [ ] **2.13** [IMPL] `src/openkos/application/ingest.py`: add a
  `lift_changed: bool = False` field to `SourceDocumentPlan`, computed in
  `compose_source_document` as (for THIS slice) exactly one delta: the
  freshly parsed `source_frontmatter` (from 2.8, `None` unless status is
  `"parsed"`) does not equal the on-disk Source's own stored
  `source_frontmatter` (read via `okf.load_frontmatter` on `concept_text`
  when `concept_text is not None`; absent reads as `None`; a
  frontmatter-free source whose Source has no key compares `None == None`
  → `False`, matching "never fires for" column in design.md Decision 7's
  table). `src/openkos/cli/main.py`: change the skip condition at line 5388
  from `if converged is not None and not source_plan.event_date.changed:`
  to `if converged is not None and not source_plan.event_date.changed and
  not source_plan.lift_changed:`. Makes 2.11-2.12 GREEN.
- [ ] **2.14** [TEST] `tests/unit/cli/test_ingest.py` — add
  `test_source_only_rewrite_preview_names_recorded_frontmatter`: the
  Source-only rewrite triggered SOLELY by a newly-parsed
  `source_frontmatter` mapping holding 3 top-level keys prints, before
  Phase B writes, a line reading exactly `source frontmatter recorded (3
  key(s))`. Covers "The preview names the recorded frontmatter when that
  delta fires". **RED today**: `AssertionError` — no such line is printed.
- [ ] **2.15** [IMPL] `src/openkos/cli/main.py`: inside the
  Source-only-rewrite branch (the `else` that falls through past the
  updated skip condition, before/alongside `_echo_event_date_preview_line`
  at lines 5600/5609), print `f"    source frontmatter recorded ({len(
  source_plan.source_frontmatter)} key(s))"` WHEN AND ONLY WHEN the
  frontmatter delta is the one that fired (guard on
  `converged is not None`, this being a Source-only-rewrite path, AND the
  specific delta condition from 2.13 — reuse or recompute the same
  boolean rather than re-deriving it, so the printed line and the skip
  decision can never disagree). Makes 2.14 GREEN. (The `tags added:` line
  and the sensitivity advisory are Phase 3's; do not add them here.)

### `_SPECIAL_KEYS` and merge exclusion (Decision 8)

- [ ] **2.16** [TEST] `tests/unit/model/test_okf.py` — add
  `test_build_merged_document_source_frontmatter_survivor_only`: a survivor
  with no `source_frontmatter` and an absorbed object declaring
  `source_frontmatter: {tags: [alpha]}` merges to a document with NO
  `source_frontmatter` key. Covers entity-resolution-merge scenario "The
  absorbed source_frontmatter does not cross the merge". **RED today**:
  `AssertionError` — the generic fill-the-gap branch currently imports it
  from the absorbed side, since `source_frontmatter` is not yet in
  `_SPECIAL_KEYS`.
- [ ] **2.17** [TEST] Same file — add
  `test_build_merged_document_source_frontmatter_survivor_wins`: both sides
  declaring a DIFFERENT `source_frontmatter` merges to the SURVIVOR's own
  value, unaffected by the absorbed side's. Covers "The survivor keeps its
  own source_frontmatter". **RED today**: passes vacuously today (scalar/
  dict fields already default to survivor-wins under the generic branch
  when the key already exists on the survivor) — confirm this explicitly as
  a regression pin once `SOURCE_FRONTMATTER_KEY` is added to
  `_SPECIAL_KEYS`, since the exclusion changes WHICH branch handles it, not
  just the gap-fill case.
- [ ] **2.18** [IMPL] `src/openkos/model/okf.py`: add
  `SOURCE_FRONTMATTER_KEY` to `build_merged_document`'s `_SPECIAL_KEYS`
  tuple (`model/okf.py:2052-2061`), beside `EVENT_DATE_KEY`. Makes
  2.16-2.17 GREEN.
- [ ] **2.19** [TEST] `tests/unit/bundle` (the existing merge/unmerge round-
  trip test module — confirm exact file name during implementation, likely
  `tests/unit/application/test_merge.py` or `tests/unit/cli/test_merge.py`)
  — add `test_unmerge_restores_absorbed_source_frontmatter`: after a real
  `merge` then `unmerge` round trip where the absorbed side carried
  `source_frontmatter`, the restored absorbed document's
  `source_frontmatter` is byte-identical to what it carried before the
  merge (PRECONDITION: assert the pre-merge value first, so the restore
  claim is provably against something that existed). **RED today**:
  confirm whether this is genuinely RED (if `unmerge` restores from a
  full-document snapshot rather than reconstructing field-by-field, this
  may already pass — if so, this becomes a regression pin like 2.17, note
  which case applies during implementation).

### `migrate_document` Unchanged pin (Decision 9)

- [ ] **2.20** [TEST] `tests/unit/model/test_okf_migrate_document.py`
  (existing file from the merged okf-v02-migration change) — add
  `test_migrate_document_unchanged_with_nested_engine_owned_keys_in_source_frontmatter`:
  a v0.2 Source document (already carrying `generated`, `status: stable`,
  and no projectable `provenance`) whose `source_frontmatter` nests
  `timestamp`, `status: active`, and `sources` keys — the exact three keys
  `migrate_document`'s rules read at the TOP level — still migrates to
  `Unchanged`, because those keys are nested one level down under
  `source_frontmatter`, never at the document's own top level.
  **PRECONDITION**: first assert the pre-migration document IS the
  document under test (parses to the described shape) before asserting the
  `Unchanged` outcome, so this is not a vacuous pass on a document that
  never had those nested keys. **RED today**: confirm during implementation
  — `migrate_document`'s rules already read `metadata.get(...)` at the top
  level only, so this SHOULD already be `Unchanged`; if RED, `migrate_document`
  is reading through a nested structure somewhere and needs a fix here, not
  a design change.

### Regression: MCP `get` discloses no new egress

- [ ] **2.21** [TEST] `tests/unit/mcp/test_gate.py` or `tests/unit/mcp/
  test_server.py` (confirm the exact existing test module covering the
  concept-payload field set) — add
  `test_mcp_concept_payload_never_discloses_source_frontmatter`: a Source
  carrying `source_frontmatter` on disk, fetched through the MCP `get`
  path, is NEVER exposed in the returned payload's field set.
  **PRECONDITION**: assert the on-disk document DOES carry the key before
  asserting the payload omits it. **RED today**: passes vacuously if MCP's
  payload builder already uses a curated allow-list of fields (per
  design.md Decision 9) — confirm this and record it as a regression pin;
  if MCP forwards the full frontmatter mapping instead, this is a genuine
  gap to close here.

### Phase 2 verification

- [ ] **2.22** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [ ] **2.23** Run `uv run pytest tests/unit/model/test_okf.py
  tests/unit/application/test_ingest.py tests/unit/cli/test_ingest.py
  tests/unit/model/test_okf_migrate_document.py tests/unit/test_sources_key_guard.py`
  focused, then `uv run pytest --cov` (unpiped) full suite — must be
  green, 90% branch gate held. `test_sources_key_guard.py` runs UNMODIFIED
  and must stay green — this slice reads no `"sources"` literal and writes
  no new `"provenance"` key.
- [ ] **2.24** Run `uv run python evals/run_self_tests.py` — must be green.
- [ ] **2.25** Commit as one or more work-unit commits, scope `okf`/`ingest`
  (e.g. `feat(ingest): preserve incoming frontmatter verbatim under
  source_frontmatter`). Open PR 2 (Phase 2: verbatim preserve) targeting
  `main`, branched from `main` after PR 1 merges.

**Rollback boundary**: revert the `source_frontmatter` parameter on
`build_source_concept`, both `compose_source_document`/
`compose_catalog_update` call sites, the `SourceDocumentPlan` field,
`_SPECIAL_KEYS`'s new entry, `lift_changed`, and the CLI skip-condition/
preview edit. Phase 1's parser is untouched and still passes its own suite;
no tag or sensitivity behavior exists yet to revert.

---

## Phase 3 (PR 3 → `main`, after PR 2 merges): Source lift — tags + sensitivity

Design.md Decision 3 (tags + sensitivity halves), Decision 4 (superseded by
this file's tasks-phase decision 1 above — a test-only confirmation, not a
new field), and Decision 7's remaining two deltas.

### 3.0 — Precondition: rebase onto #1087

- [ ] **3.0** [PRECONDITION] Before starting 3.11-3.14 (the Decision 4
  gate-confirmation tasks), confirm PR #1087 (issue #1086,
  `fix/1086-reextract-send-gate`) has merged to `main`, and rebase this
  slice's branch onto the resulting `main` so
  `src/openkos/cli/main.py`'s extraction-gate call already reads
  `workspace_floor=source_plan.source_sensitivity` (currently
  `workspace_floor=cfg.default_sensitivity` at line 5450, confirmed by
  direct read during this phase — see this file's header, tasks-phase
  decision 1). If #1087 has NOT merged yet when this slice is otherwise
  ready, land 3.1-3.10 and 3.15-3.24 first (they do not depend on #1087),
  and defer 3.11-3.14 until the rebase is possible — do not reimplement
  #1087's fix inside this change.

### `model/okf.py` — `normalize_tags`, `union_tags` (Decision 3, Decision E)

- [ ] **3.1** [TEST] `tests/unit/model/test_okf_incoming_frontmatter.py` —
  add `test_normalize_tags_shape_table`, parametrized per decision E: a
  YAML list of strings → each stripped, non-empty, deduped, order-
  preserving; a bare string → one tag, stripped (`"solo"` → `("solo",)`);
  a comma-separated string `"a, b"` → ONE tag `"a, b"`, never split; a list
  containing a non-string item (e.g. `["alpha", 3]`) → `()`, no partial
  lift; a mapping, a number, a boolean, `None`, or a result that trims to
  empty → `()`. Covers ingestion scenarios "A YAML list of tag strings
  lifts each tag", "A single string tag lifts as one tag", "A comma-
  separated string lifts as one tag, not split", "A non-string list item
  lifts no tags". **RED today**: `AttributeError` — `okf.normalize_tags`
  does not exist. **MUTATION**: change the non-string-item check to skip
  just that item instead of rejecting the whole list, and confirm the
  mixed-list row fails (proves "any other shape lifts NO tags at all", not
  a partial lift).
- [ ] **3.2** [TEST] Same file — add `test_union_tags_order_preserving`:
  `union_tags(["alpha"], ["beta"])` → `["alpha", "beta"]`;
  `union_tags(["alpha", "hand-added"], ["beta"])` →
  `["alpha", "hand-added", "beta"]` (on-disk order preserved, lifted tags
  appended in lift order, no reordering); `union_tags(["alpha"], ["alpha"])`
  → `["alpha"]` (no duplicate). **RED today**: `AttributeError`.
  **MUTATION**: swap the union order (lifted-first, on-disk-second) and
  confirm this test fails.
- [ ] **3.3** [IMPL] `src/openkos/model/okf.py`: add
  `normalize_tags(raw: object) -> tuple[str, ...]` and
  `union_tags(existing: Sequence[str], lifted: Sequence[str]) -> list[str]`
  per design.md Decision 3's table and the "Tag union order" note
  (existing on-disk tags first in stored order, lifted tags not already
  present follow in lift order). Makes 3.1-3.2 GREEN.

### `model/okf.py` — `IncomingLift`, `lift_incoming_frontmatter` (tags + sensitivity halves)

- [ ] **3.4** [TEST] Same file — add `test_lift_tags_and_sensitivity_shapes`,
  parametrized: `{"tags": ["alpha", "beta"]}` → `IncomingLift(tags=
  ("alpha", "beta"), sensitivity_present=False, sensitivity=None,
  event_date=None)`; `{"sensitivity": "confidential"}` → `sensitivity_
  present=True, sensitivity="confidential"`; `{"sensitivity": None}`
  (explicit YAML `null`) → `sensitivity_present=False` (Decision 3: "key
  absent or YAML `null` → no fold"); a mapping with neither key → the
  `NO_LIFT` sentinel; `event_date` is ALWAYS `None` on every row in this
  slice, per this file's tasks-phase decision 2 (date reading is wired
  independently in Phase 4). **RED today**: `AttributeError` —
  `okf.IncomingLift`/`okf.lift_incoming_frontmatter`/`okf.NO_LIFT` do not
  exist.
- [ ] **3.5** [IMPL] Same module: add
  `@dataclass(frozen=True) class IncomingLift: tags: tuple[str, ...];
  sensitivity_present: bool; sensitivity: object; event_date: date | None`,
  `NO_LIFT: Final = IncomingLift(tags=(), sensitivity_present=False,
  sensitivity=None, event_date=None)`, and
  `lift_incoming_frontmatter(mapping: Mapping[str, object] | None) ->
  IncomingLift` reading `tags` (via `normalize_tags`) and `sensitivity`
  (present/value per the null-vs-absent rule) ONLY — `mapping=None` (no
  parsed frontmatter) returns `NO_LIFT`; `event_date` is always `None` in
  this function's body (a one-line comment cites this file's tasks-phase
  decision 2 and Phase 4). Makes 3.4 GREEN.

### Stored-tag read, tag union wiring, sensitivity fold (`application/ingest.py`)

- [ ] **3.6** [TEST] `tests/unit/application/test_ingest.py` — add
  `test_compose_source_document_fresh_ingest_tags_are_exactly_lifted`: no
  prior Source (`concept_text is None`), incoming frontmatter carries
  `tags: [alpha, beta]` → the plan's resolved `tags` are exactly `(alpha,
  beta)` deduplicated/order-preserving; no incoming `tags` key → `()`.
  Covers "No incoming tags key leaves the Source's tags unaffected" (fresh
  half). **RED today**: `AttributeError`/`AssertionError` — no tag lift is
  wired yet.
- [ ] **3.7** [TEST] Same file — add
  `test_compose_source_document_reingest_tags_are_union`: an existing
  on-disk Source with `tags: [alpha]`, re-ingested with incoming
  frontmatter `tags: [beta]` → resolved `tags` are `[alpha, beta]`.
  Covers "Re-ingest unions lifted tags with on-disk tags". **RED today**:
  `AssertionError`.
- [ ] **3.8** [TEST] Same file — add
  `test_compose_source_document_hand_added_tag_survives_reingest`: an
  existing on-disk Source with `tags: [alpha, hand-added]` where
  `hand-added` never appeared in any incoming frontmatter, re-ingested with
  incoming frontmatter now carrying `tags: [beta]` → resolved `tags`
  include `hand-added`, `alpha`, and `beta`; nothing on disk is removed.
  Covers "A hand-added tag survives re-ingest even when the incoming
  file's tags changed". **RED today**: `AssertionError`.
- [ ] **3.9** [TEST] Same file — add
  `test_compose_source_document_sensitivity_fold_raise_only`,
  parametrized: on-disk/config resolve to `private`, incoming
  `sensitivity: confidential` → resolved `confidential`; on-disk/config
  resolve to `confidential`, incoming `sensitivity: public` → resolved
  stays `confidential` (lower incoming never lowers); incoming
  `sensitivity` absent or explicit `null` → resolved is whatever the
  existing on-disk+config fold already produced, UNCHANGED (byte-identity
  precondition: assert this equals the pre-Phase-3 fold result for the same
  inputs, computed once before this test existed, or by calling the
  pre-lift fold directly for comparison). Covers "An incoming sensitivity
  lower than the resolved value leaves it unchanged; a higher value raises
  it" (proposal Success Criteria) and ingestion's fold-order scenarios.
  **RED today**: `AssertionError` — the lift is never folded in yet.
  **MUTATION**: pass `None` instead of skipping the fold when `sensitivity_
  present` is `False`, and confirm a `public` workspace does NOT get
  wrongly floored to `private` (this is the exact `_rank(None)` hazard
  design.md's fold-order note calls out).
- [ ] **3.10** [IMPL] `src/openkos/application/ingest.py`: inside
  `compose_source_document`, after computing `parse_incoming_frontmatter`'s
  result (2.8), call `okf.lift_incoming_frontmatter(result.mapping if
  result.status == "parsed" else None)`. Read the on-disk Source's stored
  tags when `concept_text is not None` (a new small helper mirroring
  `_read_source_sensitivity`/`_read_source_title`'s shape, e.g.
  `_read_source_tags`, via `okf.normalize_tags(metadata.get("tags"))`).
  Compute `tags = okf.union_tags(stored_tags, lift.tags)` on re-ingest, or
  `tags = list(lift.tags)` on a fresh ingest (no stored tags to union
  against). Fold sensitivity: `resolved_sensitivity = okf.combine_sensitivity
  (pre_lift_resolved, lift.sensitivity) if lift.sensitivity_present else
  pre_lift_resolved`, where `pre_lift_resolved` is exactly today's existing
  on-disk+config fold (unchanged). Pass `tags=tags` into BOTH
  `build_source_concept` calls (the first one here, per design.md's fold-
  order note; the SECOND one is `compose_catalog_update`'s conditional
  rebuild — see 3.14 below, which finally removes the `tags=[]` hard-code
  Phase 2 deliberately left in place). Add `tags: tuple[str, ...] = ()` to
  `SourceDocumentPlan` (the FULL resolved tag list this run, for Phase 5's
  propagation). Makes 3.6-3.9 GREEN.

### Decision 4 — LLM-send floor, test-only confirmation (post-#1087)

- [ ] **3.11** [TEST] `tests/unit/cli/test_ingest.py` — add
  `test_incoming_confidential_declaration_blocks_this_runs_extraction`: a
  workspace with `default_sensitivity: private`, a source whose incoming
  frontmatter carries `sensitivity: confidential`, ingested WITHOUT
  `--include-confidential` — the fake LLM's `chat` is asserted NEVER
  called, `staged.skip_reason == "blocked-by-sensitivity"`, and only the
  Source concept is written. **PRECONDITION**: assert the same fixture
  WITHOUT the incoming `sensitivity: confidential` key DOES call the fake
  LLM (extraction proceeds), so the blocking claim is provably caused by
  the incoming declaration, not some other gate. Covers "An incoming
  confidential declaration blocks this run's extraction call". **RED
  today, PRE-#1087**: `AssertionError` — the gate reads
  `cfg.default_sensitivity` only, so a `private` workspace never blocks
  regardless of the incoming value. **Depends on 3.0**: only observe this
  RED reason, and only mark it GREEN, after the #1087 rebase — before that,
  this row would need the `llm_send_floor` field design.md originally
  specified, which this file's tasks-phase decision 1 replaces.
- [ ] **3.12** [TEST] Same file — add
  `test_include_confidential_still_allows_send_past_frontmatter_raised_floor`
  and `test_lower_incoming_sensitivity_does_not_lower_extraction_floor`:
  the `--include-confidential` override still allows the call through past
  a frontmatter-raised floor; a workspace at `default_sensitivity:
  confidential` with incoming `sensitivity: public` still blocks (the
  floor is never lowered). Covers both matching ingestion scenarios.
  **RED today, PRE-#1087**: same underlying cause as 3.11.
- [ ] **3.13** [TEST] Same file — add
  `test_unrecognized_incoming_sensitivity_also_raises_extraction_floor`: an
  incoming `sensitivity` value not in `SENSITIVITY_ORDER` (e.g. `sensitivity:
  banana`) also raises the gate to block, exactly as `confidential` does
  (`_rank`'s existing unrecognized-value fallback). **RED today,
  PRE-#1087**: same underlying cause.
- [ ] **3.14** [IMPL] No new production code for the gate itself — per this
  file's tasks-phase decision 1, `source_plan.source_sensitivity` already
  carries the raised value once 3.10's sensitivity fold lands AND #1087's
  `workspace_floor=source_plan.source_sensitivity` call-site change is on
  `main` (3.0). This task is the VERIFICATION step: after the 3.0 rebase,
  re-run 3.11-3.13 and confirm they are GREEN with no gate-specific code
  change in this change's own diff. If any of the three is still RED after
  the rebase, that is a genuine gap in the tasks-phase decision 1 analysis
  — stop and re-derive the wiring needed (likely a missed spot where the
  gate is still called with `cfg.default_sensitivity` directly) rather than
  reintroducing the `llm_send_floor` field.

### Preview lines and the raise advisory (Decision 7, remaining deltas)

- [ ] **3.15** [TEST] `tests/unit/model/test_okf_incoming_frontmatter.py`
  — add `test_never_lifted_keys_leave_sources_own_values_unchanged`,
  parametrized over `status`, `type`, `provenance`, `version`, `timestamp`,
  `generated`, `verified`, `sources`, `author`, `updated`, `created`
  (design.md Decision 4's exact list): for each key, run
  `lift_incoming_frontmatter({key: <a value that would visibly change the
  Source if lifted>})` and assert `IncomingLift` carries NOTHING derived
  from that key — i.e. the result equals `NO_LIFT` for every key in this
  list except when combined with `tags`/`sensitivity`, which stay
  independently lift-able. Covers ingestion's "Never-Lifted Incoming
  Frontmatter Keys" requirement and all three of its scenarios (type/
  status/provenance, author/updated, created-for-event-date). **RED
  today**: `AttributeError` until 3.5 exists; once it exists, this is a
  regression-proof PARAMETRIZED fixture guarding against any FUTURE
  accidental lift — it should already be GREEN by construction of 3.5's
  closed allow-list (only `tags`/`sensitivity` are read), so treat a RED
  result here as evidence the allow-list leaked.
- [ ] **3.16** [TEST] `tests/unit/cli/test_ingest.py` — add
  `test_source_only_rewrite_preview_names_tags_added_and_advisory`,
  covering three preview scenarios in one parametrized test: (a) a rewrite
  triggered solely by a tag-union delta adding `alpha`/`beta` prints
  `tags added: alpha, beta` and NO `source frontmatter recorded` line; (b)
  a rewrite triggered by BOTH a newly-parsed `source_frontmatter` and a
  tag-union delta prints BOTH lines; (c) a rewrite that RAISES resolved
  sensitivity prints a stderr advisory naming `openkos set-sensitivity`;
  (d) a rewrite that does NOT raise sensitivity prints no such advisory.
  Covers "The preview names the added tags when that delta fires", "A
  rewrite triggered by several deltas prints each fired delta's line", "A
  raised sensitivity on the Source-only rewrite advises set-sensitivity",
  "A rewrite that does not raise sensitivity prints no advisory". **RED
  today**: `AssertionError` — none of these lines exist yet (2.15 only
  added the frontmatter-recorded line).
- [ ] **3.17** [TEST] Same file — add
  `test_source_only_rewrite_event_date_only_prints_neither_new_line`: a
  rewrite triggered SOLELY by the (already-existing) event-date delta
  prints the existing origin-disclosure line and NEITHER of Phase 2/3's new
  lines. Covers "A rewrite triggered only by the event-date delta prints
  neither new line" — a regression pin protecting Phase 2/3's additions
  from over-firing on the pre-existing delta. **RED today**: passes
  vacuously until 3.18 exists; confirm GREEN after, as a genuine regression
  pin.
- [ ] **3.18** [IMPL] `src/openkos/application/ingest.py`: extend
  `SourceDocumentPlan.lift_changed`'s computation (2.13) with the two
  remaining deltas from design.md Decision 7's table: a tags delta (some
  lifted tag is not already in the on-disk tags, via `okf.normalize_tags`)
  and a sensitivity delta (`lift.sensitivity_present and resolved_
  sensitivity != pre_lift_resolved`). `src/openkos/cli/main.py`: add the
  `tags added: {a}, {b}` preview line (printed only when the tags delta is
  one of the deltas that fired, listing exactly the tags the union added,
  in order) beside 2.15's frontmatter line; add the stderr `set-
  sensitivity` advisory printed only when the sensitivity delta fired.
  Makes 3.16-3.17 GREEN.

### Phase 3 verification

- [ ] **3.19** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [ ] **3.20** Run `uv run pytest tests/unit/model/test_okf_incoming_frontmatter.py
  tests/unit/application/test_ingest.py tests/unit/cli/test_ingest.py`
  focused, then `uv run pytest --cov` (unpiped) full suite — must be
  green, 90% branch gate held.
- [ ] **3.21** Run `uv run python evals/run_self_tests.py` — must be green.
- [ ] **3.22** Commit as one or more work-unit commits, scope `ingest` (e.g.
  `feat(ingest): lift incoming tags and sensitivity onto the Source`). Open
  PR 3 (Phase 3: tag + sensitivity lift) targeting `main`, branched from
  `main` after PR 2 merges.

**Rollback boundary**: revert `IncomingLift`/`NO_LIFT`/`normalize_tags`/
`union_tags`/`lift_incoming_frontmatter`, the tag/sensitivity wiring and the
`tags`/`lift_changed` extensions in `compose_source_document`, the two new
preview lines, and the advisory. Phase 2's `source_frontmatter` emission is
untouched; the Decision 4 gate confirmation (3.11-3.14) reverts to whatever
#1087 alone already provides, with no `llm_send_floor` field to remove
because none was ever added.

---

## Phase 4 (PR 4 → `main`, after PR 2 merges, independent of PR 3): Source lift — `date:` tier

Design.md Decision 5. Depends only on Phase 2 (the parser + `SourceDocumentPlan`
shape); does not depend on Phase 3, per this file's tasks-phase decision 2.

### `model/okf.py` — `_tolerant_date` refactor, `read_incoming_date`

- [ ] **4.1** [TEST] `tests/unit/model/test_okf.py` — add
  `test_read_event_date_unchanged_after_tolerant_date_refactor`: run the
  FULL existing `read_event_date` test suite's assertions again (or import
  and re-run them) against the refactored implementation — a `datetime`
  value is still `malformed`; an unquoted `date` still resolves; a valid
  ISO string still resolves; an invalid string is still `malformed`; an
  absent key still returns `value=None, malformed=False`. This is a
  PRECONDITION-style regression pin: assert `read_event_date`'s PUBLIC
  behavior is provably unchanged before extracting its body into a shared
  helper. **RED today**: passes vacuously (nothing has changed yet) — this
  test's role is to stay GREEN through 4.4's refactor, not to start RED;
  if it goes RED after 4.4, the refactor broke something and must be fixed
  before continuing.
- [ ] **4.2** [TEST] Same file — add
  `test_read_incoming_date_shape_table`, parametrized per design.md
  Decision 3's date row: a bare (unquoted) `date` value → that date; a
  quoted `YYYY-MM-DD` string that is a real calendar date → that date; a
  `datetime` value → `None` (malformed, matching the stored-date reader's
  existing rule of never silently dropping a time component); an invalid
  calendar string (e.g. `2026-13-45`) → `None`; an absent `date` key → `None`.
  Covers the `date` row of design.md Decision 3's lift table and ingestion's
  event-date scenarios' data-shape half. **RED today**: `AttributeError`
  — `okf.read_incoming_date` does not exist.
- [ ] **4.3** [TEST] Same file — add
  `test_read_incoming_date_and_read_event_date_share_tolerance_rules`: for
  a representative set of raw values (valid string, valid bare date,
  malformed string, `datetime`, absent), assert
  `read_incoming_date({"date": v}) == read_event_date({"event_date": v}).value
  if not read_event_date({"event_date": v}).malformed else None` — i.e. the
  two readers agree on every input, since design.md requires "the same
  tolerant date shape checks... with a separate key". **RED today**: same
  `AttributeError`. **MUTATION**: after 4.4, make `read_incoming_date`
  accept a `datetime` value as its date part (dropping the time) instead of
  rejecting it, and confirm this parity test fails.
- [ ] **4.4** [IMPL] `src/openkos/model/okf.py`: extract `read_event_date`'s
  body into a private `_tolerant_date(raw: object) -> StoredEventDate`
  (identical logic, parameterized on the raw value instead of a metadata
  mapping); `read_event_date(metadata)` becomes a one-line wrapper:
  `_tolerant_date(metadata.get(EVENT_DATE_KEY)) if EVENT_DATE_KEY in
  metadata else StoredEventDate(value=None, malformed=False, raw=None)`
  (preserve the exact absent-key early return). Add
  `read_incoming_date(mapping: Mapping[str, object]) -> date | None` =
  `_tolerant_date(mapping.get("date")).value if "date" in mapping else
  None` — returns `value` only (never the malformed/raw detail; an incoming
  malformed date is silently not lifted, per design.md Decision 5, since it
  is untrusted input, not engine state). Makes 4.1-4.3 GREEN.

### `application/ingest.py` — `resolve_event_date(incoming=)`, origin tier

- [ ] **4.5** [TEST] `tests/unit/application/test_ingest.py` — add
  `test_resolve_event_date_four_tier_precedence_table`, parametrized over
  every combination design.md Decision 5 and the ingestion spec name: flag
  set → `flag` wins over stored/incoming/inferred; flag absent, stored
  valid → `kept` wins over incoming/inferred; flag absent, stored absent/
  malformed, incoming valid → `frontmatter` wins over inferred; all three
  absent, inferred valid → `file name`; all four absent → `value=None,
  origin=None`. Covers ingestion scenarios "The flag wins over a dated file
  name", "The flag wins over a frontmatter date", "A frontmatter date wins
  over the dated file name", "The file name is used when no flag or
  frontmatter date is given", "Neither source leaves the key unset", "A
  stored value wins over a frontmatter date", "A frontmatter date fills a
  Source with no stored value, ahead of the file name". **RED today**:
  `TypeError` — `resolve_event_date` has no `incoming` parameter.
- [ ] **4.6** [TEST] Same file — add
  `test_resolve_event_date_created_key_never_consulted`: a mapping's
  incoming frontmatter carries `created: 2026-01-01` and no `date:` key —
  `resolve_event_date`'s `incoming` argument (computed by the CALLER via
  `read_incoming_date`, which never reads `created`) is `None`, so the
  chain falls through to inference/unset exactly as if no incoming
  frontmatter existed at all. Covers "created is ignored for this
  precedence" and "created is never lifted for event-date resolution".
  **RED today**: this is really pinning 4.4's `read_incoming_date` (already
  covered by 4.2's absent-key row) PLUS this function's plumbing — write it
  here as an end-to-end confirmation at the `resolve_event_date` call
  boundary; RED until 4.7 threads the value through.
- [ ] **4.7** [IMPL] `src/openkos/application/ingest.py`: add `incoming:
  date | None = None` to `resolve_event_date`'s signature; insert it into
  the existing precedence chain between `previous` (stored) and `inferred`
  (file name): `elif incoming is not None: value = incoming; origin =
  "frontmatter"`. Change `EventDateOrigin` from `Literal["flag", "file
  name", "kept"]` to `Literal["flag", "file name", "kept", "frontmatter"]`.
  Makes 4.5-4.6 GREEN.
- [ ] **4.8** [TEST] Same file — add
  `test_compose_source_document_reads_incoming_date_independently_of_lift`:
  a fixture with incoming frontmatter carrying ONLY `date: 2026-07-14` (no
  `tags`, no `sensitivity`) still resolves `event_date` to `2026-07-14` —
  proving `compose_source_document` calls `okf.read_incoming_date` directly
  off the parsed mapping, per this file's tasks-phase decision 2, not
  through `lift_incoming_frontmatter` (which may not even be on `main` yet
  if Phase 3 has not merged — simulate that by NOT importing anything
  Phase-3-specific in this test). **RED today**: `AssertionError` — no call
  site exists yet.
- [ ] **4.9** [IMPL] `src/openkos/application/ingest.py`: inside
  `compose_source_document`, after `parse_incoming_frontmatter` (2.8), call
  `okf.read_incoming_date(result.mapping) if result.status == "parsed" else
  None` and pass it as `resolve_event_date`'s new `incoming=` argument,
  alongside the existing `flag=`/`stored=`/`inferred=` arguments. Makes 4.8
  GREEN.

### Origin-disclosure line

- [ ] **4.10** [TEST] `tests/unit/cli/test_ingest.py` — add
  `test_event_date_origin_disclosure_line_names_frontmatter`: a fresh
  ingest with no flag, no dated file name, and incoming frontmatter
  carrying `date: 2026-07-14` prints exactly one line naming `2026-07-14`
  and `frontmatter` as its origin. Covers "The line names the frontmatter
  origin". **RED today**: `AssertionError` (or a branch-not-covered
  failure in `_echo_event_date_preview_line`, which currently has no
  `"frontmatter"` branch and would raise/fall through unexpectedly for that
  origin value).
- [ ] **4.11** [IMPL] `src/openkos/cli/main.py`: in
  `_echo_event_date_preview_line` (line 4278), add an `elif
  resolution.origin == "frontmatter":` branch printing
  `f"    event date {value} (from the source's frontmatter date)"`.
  Makes 4.10 GREEN.

### Phase 4 verification

- [ ] **4.12** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [ ] **4.13** Run `uv run pytest tests/unit/model/test_okf.py
  tests/unit/application/test_ingest.py tests/unit/cli/test_ingest.py`
  focused, then `uv run pytest --cov` (unpiped) full suite — must be
  green, 90% branch gate held.
- [ ] **4.14** [TEST, regression] `tests/unit/cli/test_ingest.py` — confirm
  (or add if missing) a test that a Source-only rewrite triggered SOLELY by
  the (pre-existing) event-date delta still short-circuits before any LLM
  call and creates/modifies/removes no derived object — the "Converged
  Re-Ingest Source-Only Rewrite" requirement's original event-date case,
  now exercised alongside Phase 2/3's two new deltas. Must stay GREEN;
  this task exists to make the coverage table's "4.14" cell concrete, not
  to introduce new behavior.
- [ ] **4.15** Run `uv run python evals/run_self_tests.py` — must be green.
- [ ] **4.16** Commit as one or more work-unit commits, scope `ingest` (e.g.
  `feat(ingest): resolve event_date from incoming frontmatter's date tier`).
  Open PR 4 (Phase 4: date lift) targeting `main`, branched from `main`
  after PR 2 merges (independent of PR 3 — either may merge first).

**Rollback boundary**: revert `_tolerant_date`/`read_incoming_date`,
`resolve_event_date`'s `incoming` parameter and its new precedence branch,
`EventDateOrigin`'s `"frontmatter"` literal, the `compose_source_document`
call site, and the origin-line branch. `read_event_date`'s public behavior
is provably unchanged by 4.1's precondition pin, so no legacy re-ingest
path is affected by the revert.

---

## Phase 5 (PR 5 → `main`, after PR 3 merges): Derived tag propagation

Design.md Decision 6.

### `model/okf.py` — `build_concept(tags=)`

- [ ] **5.1** [TEST] `tests/unit/model/test_okf.py` — add
  `test_build_concept_default_tags_byte_identical`: calling `build_concept`
  with NO `tags` argument produces a document byte-identical to today's
  golden output for the same other arguments (`tags: []` in the rendered
  frontmatter, exactly as today). **PRECONDITION**: capture or reuse an
  EXISTING golden fixture from before this task, so the byte-identity claim
  is against real pre-change bytes, not a freshly regenerated one. **RED
  today**: `TypeError` once 5.2 is written without a default — write this
  test FIRST, confirm it is GREEN against the CURRENT builder (no `tags`
  parameter exists, so there is nothing to diverge from yet), then confirm
  it STAYS GREEN after 5.2 lands with the default in place; if it is ever
  RED after 5.2, the default broke byte-identity.
- [ ] **5.2** [TEST] Same file — add
  `test_build_concept_emits_given_tags`: `build_concept(..., tags=["alpha",
  "beta"])` emits `tags: [alpha, beta]` in place of the default `[]`. **RED
  today**: `TypeError` — no `tags` parameter exists.
- [ ] **5.3** [IMPL] `src/openkos/model/okf.py`: add `tags: Sequence[str] =
  ()` to `build_concept`'s signature; change the hard-coded
  `metadata["tags"] = []` (currently emitted unconditionally in the
  metadata dict literal at line ~950) to `metadata["tags"] = list(tags)`.
  Makes 5.1-5.2 GREEN. Confirm all 11 existing callers (`application/
  ingest.py`, `application/query.py`) still pass no `tags=` argument and
  therefore still emit `[]` — do not add `tags=` to any existing call site
  in this task; that is 5.5's job for the ingest path only (`query --save`'s
  `stage_filed_answer` call stays byte-identical, unchanged in this
  change).

### `application/ingest.py` — `stage_derived_objects(source_tags=)`, CLI threading

- [ ] **5.4** [TEST] `tests/unit/application/test_ingest.py` — add
  `test_stage_derived_objects_threads_source_tags_to_every_build_concept_call`:
  a fake LLM returns two candidates; `stage_derived_objects(...,
  source_tags=("alpha", "beta"))` stages BOTH candidates' plans with
  `tags=("alpha", "beta")` reaching each `build_concept` call (assert via
  the staged plan's rendered document, or a spy on `build_concept`). **RED
  today**: `TypeError` — `stage_derived_objects` has no `source_tags`
  parameter.
- [ ] **5.5** [TEST] Same file — add
  `test_stage_derived_objects_carried_path_ignores_source_tags`: the
  `carried=` short-circuit (pre-extraction return, `stage_derived_objects`
  lines 352-360) returns immediately regardless of `source_tags`, staging
  NO plans and calling NO `build_concept` — a Source-only rewrite creates
  nothing, so tags never reach it. **PRECONDITION**: assert `carried` is
  genuinely set (a real converged-reingest object) before asserting the
  short-circuit fired. **RED today**: passes vacuously (the carried branch
  already returns before any staging logic runs) — confirm as a regression
  pin once `source_tags` exists, so a future refactor cannot accidentally
  route it through the carried path.
- [ ] **5.6** [IMPL] `src/openkos/application/ingest.py`: add
  `source_tags: tuple[str, ...] = ()` to `stage_derived_objects`'s
  signature; pass `tags=source_tags` into every `build_concept` call inside
  the staging loop, beside the existing `sensitivity=resolved_sensitivity`
  argument (the exact shape sensitivity inheritance already uses). Makes
  5.4-5.5 GREEN.
- [ ] **5.7** [TEST] `tests/unit/cli/test_ingest.py` — add
  `test_derived_object_created_in_run_inherits_sources_tags`: a source
  whose incoming frontmatter lifts tags onto the Source, ingested with a
  fake LLM returning one candidate — the WRITTEN derived object's `tags`
  include the Source's resolved (unioned) tags. Covers ingestion scenario
  "A derived object created in the run inherits the Source's tags". **RED
  today**: `AssertionError` — the CLI does not pass `source_plan.tags`
  through yet.
- [ ] **5.8** [TEST] Same file — add
  `test_existing_derived_object_unaffected_by_later_source_tag_change`: a
  Source with ONE existing derived object on disk, re-ingested with
  incoming frontmatter that lifts a NEW tag not previously on the Source —
  the EXISTING derived object's file is byte-unchanged (create-only
  reconciliation). **PRECONDITION**: capture the existing derived object's
  bytes before the re-ingest, compare after. Covers "An existing derived
  object's tags are unaffected by a later Source tag change". **RED
  today**: passes vacuously today (create-only reconciliation already
  applies to every field) — confirm as a regression pin.
- [ ] **5.9** [IMPL] `src/openkos/cli/main.py`: pass `source_tags=
  source_plan.tags` into the `stage_derived_objects` call
  (`cli/main.py:5446-5460`), beside `stamp_sensitivity=source_plan.
  source_sensitivity`. Makes 5.7-5.8 GREEN.

### Merge-union pin (no code change expected)

- [ ] **5.10** [TEST] `tests/unit/model/test_okf.py` — add
  `test_build_merged_document_tags_generic_union_unaffected`: a survivor
  with `tags: [alpha]` and an absorbed object with `tags: [beta]` merge to
  `tags: [alpha, beta]` — `tags` is NOT in `_SPECIAL_KEYS`, so the EXISTING
  generic list-union branch already handles it; this test pins that no
  later change accidentally adds `tags` to `_SPECIAL_KEYS`. **RED today**:
  passes vacuously (the generic union already exists and already covers
  `tags`) — this is a pure regression pin, per design.md Decision 6 ("The
  existing generic list union... already unions `tags` on merge. A test
  pins that behavior."). No `[IMPL]` task pairs with this one.

### Phase 5 verification

- [ ] **5.11** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [ ] **5.12** Run `uv run pytest tests/unit/model/test_okf.py
  tests/unit/application/test_ingest.py tests/unit/cli/test_ingest.py`
  focused, then `uv run pytest --cov` (unpiped) full suite — must be
  green, 90% branch gate held.
- [ ] **5.13** Run `uv run python evals/run_self_tests.py` — must be green.
- [ ] **5.14** Commit as one or more work-unit commits, scope `ingest` (e.g.
  `feat(ingest): propagate a Source's resolved tags onto derived objects
  created in the same run`). Open PR 5 (Phase 5: derived tag propagation)
  targeting `main`, branched from `main` after PR 3 merges.

**Rollback boundary**: revert `build_concept`'s `tags` parameter,
`stage_derived_objects`'s `source_tags` parameter, and the CLI call-site
edit. All 11 existing `build_concept` callers keep emitting `tags: []`
unchanged, and `query --save`'s output stays byte-identical throughout.

---

## Phase 6 (PR 6 → `main`, after PR 2 merges, parallel-eligible with PRs 3-5): Docs + follow-up issue

Design.md "Migration / Rollout" and proposal.md's follow-up list.

- [ ] **6.1** [DOC] `docs/knowledge-object-model.md` and/or
  `docs/okf-alignment.md`: add one short note (no counts, per AGENTS.md's
  "docs describe the shape, not the diff") stating that `source_frontmatter`
  is a frontmatter extension key on a Source concept, holding the incoming
  raw file's own frontmatter verbatim, and that a closed set of keys
  (`tags`, `sensitivity`, `date`) is additionally lifted onto the Source
  under documented merge rules — pointing to ADR-0030 for the trust-
  boundary rationale rather than restating it.
- [ ] **6.2** [PROCESS] Open follow-up issue **F1: Re-sync tags onto
  existing derived concepts**, per proposal.md's follow-up list: when a
  Source's incoming tags change on re-ingest, existing derived concepts
  keep their creation-time tags; propose an explicit, reviewable verb (or
  an option on an existing one) that unions the Source's tags onto its
  provenance descendants, modeled on `set-sensitivity` (ADR-0009). Record
  the issue number here once opened. (F2 and F3 from proposal.md are
  explicitly optional/deferred — not required to be filed in this change.)
- [ ] **6.3** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green (docs-only change; confirms no code was
  accidentally touched).
- [ ] **6.4** Run `uv run pytest --cov` (unpiped) full suite — must be
  green; docs-only change should need no test changes.
- [ ] **6.5** Commit as one work-unit commit, scope `docs` (e.g. `docs:
  describe source_frontmatter as an OKF extension key`). Open PR 6 (Phase
  6: docs) targeting `main`, branched from `main` after PR 2 merges.

**Rollback boundary**: revert the docs edit file-by-file; no code behavior
depends on this slice. Closing follow-up issue F1 is independent of any
code revert.
