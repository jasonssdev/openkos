# Design: Record and Surface Sources Whose Extraction Was Skipped (#187)

## Technical Approach

`_stage_derived_objects` returns `(plans, skip_reason)`. `ingest` builds the Source once as
today, stages, and — **only when `skip_reason is not None`** — calls `okf.build_source_concept`
a second time with `extraction_status=skip_reason`. Both renders are from-scratch builds of the
same trusted local inputs, so the proposal's named constraint ("stamp the freshly built content,
never merge onto on-disk frontmatter") holds by construction, and the healthy path stays
byte-identical to today with zero extra work. Slice 2 then reads the key off frontmatter inside
`lint.collect_docs`'s existing single walk and folds it into `lint` and `status`.

## The ordering conflict (central decision)

The value is discovered at `main.py:1735` (staging), after the build at `:1717`, before the write
at `:1865`.

| Option | Tradeoff | Decision |
|---|---|---|
| **Conditional re-render**: build → stage → rebuild with `extraction_status=` when a reason exists | One extra pure, I/O-free, clock-free builder call, on the degrade path only. Healthy path unchanged. Every field is regenerated, so no drift is possible | **Chosen** |
| Reorder: stage before the build | **Impossible without regressing #219.** `stamp_sensitivity` is read *back* from the built Source's frontmatter (`:1733-1734`) precisely so a derived object provably inherits its Source's actual value, not a shared config constant — guarded by `test_derived_object_inherits_source_document_value_not_config`. Staging cannot precede the build | Rejected |
| Patch the built content: `load_frontmatter` → mutate dict → `dump_frontmatter` | Cheaper than a rebuild, but `frontmatter.loads` strips body whitespace, and the Source body embeds arbitrary verbatim raw text. Body drift on a canonical document, for a saving measured against a single in-memory string build. Same shape #229's design already rejected | Rejected |
| Mutable carrier / callback into `_stage_derived_objects` | Hidden control flow into a function whose contract is "returns the COMPLETE write set"; untestable in isolation | Rejected |

To avoid duplicating an 8-argument call, `ingest` binds a local closure immediately before the
first build:

```python
def _build_source_document(extraction_status: str | None) -> str:
    return okf.build_source_concept(
        title=title, description=description, resource=resource, tags=[],
        timestamp=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        sensitivity=resolved_sensitivity, provenance=[resource],
        raw_content=raw_content, extraction_status=extraction_status,
    )

concept_content = _build_source_document(None)          # replaces :1717-1726
source_metadata, _ = okf.load_frontmatter(concept_content)   # :1733 unchanged
source_sensitivity = str(source_metadata["sensitivity"])     # :1734 unchanged
derived_plans, skip_reason = _stage_derived_objects(...)      # :1735
if skip_reason is not None:
    concept_content = _build_source_document(skip_reason)
```

`:1733-1734` stay byte-unchanged, so #219's and #229's read-back tests are untouched.

### Two fields, two opposite rules — do not unify them

| Field | On re-ingest | Why |
|---|---|---|
| `sensitivity` | **Read from disk** and combined via `okf.combine_sensitivity` **before** the build (`:1708-1716`, #229/ADR-0010) | A security classification a human raised must never be silently downgraded by a mechanical re-run |
| `extraction_status` | **Never read from disk.** Recomputed from this run alone; absent unless this run skipped | It is a fact about *this* run. Merging an on-disk value would make a stale "failed" marker sticky forever — the exact failure mode the proposal's clearing constraint forbids |

A future reader who "fixes" `extraction_status` into the `sensitivity` pattern breaks clearing; one
who does the reverse reintroduces silent declassification. Both regressions are test-guarded.

## Sequence

```
ingest()
  │ cfg = read_config                                        :1696
  │ resolved_sensitivity = combine(on_disk, cfg.default)     :1708-1716   [#229: reads DISK]
  ├─> _build_source_document(extraction_status=None) ────────:1717        [provisional bytes]
  │ source_sensitivity = load_frontmatter(...)["sensitivity"]:1733-1734   [#219 read-back]
  ├─> _stage_derived_objects(...)                            :1735
  │      ├─ blank/None content ......... return [], "no-extractable-text"     :1298
  │      ├─ blocks_llm_send(floor) ..... return [], "blocked-by-sensitivity"  :1305
  │      ├─ except OllamaError ......... return [], "failed"                  :1316
  │      ├─ extractions == [] .......... return [], "no-concepts-found"       :1329
  │      └─ else ....................... return plans, None                   :1426
  │                                    ▲ REASON PRODUCED
  ├─> if skip_reason: concept_content = _build_source_document(skip_reason)
  │                                    ▲ REASON STAMPED (fresh bytes, no merge)
  │ preview / confirm gate                                   :1807-1857
  └─> write_atomic(concept_path, concept_content)            :1865
                                       ▲ REASON PERSISTED
```

`plans == [] and skip_reason is None` is a real, deliberate state: every candidate was dropped
per-candidate (empty slug, collision, `build_concept` failure). It writes **no** key — those drops
are out of scope per the proposal and are already reported individually.

## `_stage_derived_objects` return shape

```python
) -> tuple[list[_DerivedPlan], okf.ExtractionStatus | None]:
```

A bare 2-tuple, matching `lint.collect_docs`'s established `(docs, skip_notices)` idiom; no new
dataclass for two values. Ripple: **one call site** (`main.py:1735`) and its four `return []`
statements become `return [], "<value>"`; the terminal `return plans` becomes `return plans, None`.
Nothing else in the repo calls it (verified: no test constructs or invokes it directly).

The two named degrade-path tests (`test_confidential_default_sensitivity_floor_skips_extraction`,
`test_spinner_cleared_on_ollama_error_and_degrade_proceeds`, `tests/unit/cli/test_ingest.py:945`
and `:3088`) drive the CLI through `runner.invoke`, never the helper, so both must pass
**unmodified**. Each gains a *sibling* test asserting its new frontmatter value rather than being
edited — leaving them untouched is itself evidence the return-shape change is behavior-preserving.

## Vocabulary placement

`src/openkos/model/okf.py` — the canonical layer, alongside `RELATIONS_KEY` /
`MERGED_FROM_KEY` / `SENSITIVITY_ORDER`. Both consumers (`lint.py`, `cli/main.py`) already import
`okf`; the dependency direction is derived → canonical, so the layering rule holds. Nothing is
added to `lint.py` that `okf` must know about.

```python
EXTRACTION_STATUS_KEY: Final = "extraction_status"
ExtractionStatus = Literal[
    "no-extractable-text", "blocked-by-sensitivity", "failed", "no-concepts-found"
]
EXTRACTION_STATUS_VALUES: Final[tuple[ExtractionStatus, ...]] = get_args(ExtractionStatus)
EXTRACTION_STATUS_FAILED: Final[ExtractionStatus] = "failed"
```

| Option | Tradeoff | Decision |
|---|---|---|
| `Literal` alias + `Final` constants | Matches every existing on-disk vocabulary in this module; mypy-strict enforces the writer at compile time; the value serializes as the plain string already required on disk | **Chosen** |
| `enum.Enum` | Zero precedent in the codebase; forces `.value` at every write site and a decode step at every read site, for a four-token closed set | Rejected |
| Bare module constants, no type | Loses the compile-time gate on the writer | Rejected |

**Validation policy — write-side typed, read-side fail-silent.** `build_source_concept` keeps its
documented no-runtime-validation stance (engine-derived, trusted inputs; the `Literal` is the gate,
enforced by mypy). It emits the key only when `extraction_status is not None`, so a healthy Source's
frontmatter is byte-identical to today. On read, `okf` does **not** validate at all: `lint` matches
`doc.extraction_status == okf.EXTRACTION_STATUS_FAILED` and nothing else, so an unrecognized or
hand-edited value is structurally ignored — forward-compatible and fail-silent with no membership
test to keep in sync. `EXTRACTION_STATUS_VALUES` exists for specs and tests, not for a runtime gate.

## `LintDoc` / `collect_docs` (Slice 2)

Two frozen-dataclass fields after `relations` (defaults keep every existing fixture valid):

```python
extraction_status: str = ""
resource: str = ""
```

and two lines inside the existing `LintDoc(...)` construction at `lint.py:137-146`:

```python
extraction_status=str(metadata.get("extraction_status", "")),
resource=str(metadata.get("resource", "")),
```

`metadata` is already in hand from the `okf.load_frontmatter` call at `lint.py:125`. **Single walk
confirmed**: no new `rglob`, no new `read_text`, no new `_iter_docs` — the added cost is two dict
lookups per doc, inside the loop that already exists. Same `str(metadata.get(...))` idiom as
`freshness` / `type` / `volatility`.

```python
def check_unextracted(docs: list[LintDoc]) -> list[LintFinding]:
    """kind="unextracted", emitted ONLY for extraction_status == "failed"."""
```

Detail text: ``concept extraction failed during ingest — retry with `openkos ingest <resource>` ``,
falling back to ``re-run `openkos ingest` on this source's raw file`` when `doc.resource` is empty.
`LintReport` gains `unextracted: list[LintFinding] = field(default_factory=list)`; `lint` renders
`Unextracted sources:` / `No unextracted sources.` after `Dangling references:` (`main.py:5280`),
exit 0 unchanged.

## `status` integration — the no-fifth-walk guard

The guard is **structural, not procedural**: `check_unextracted` takes `list[LintDoc]` and has no
`bundle_dir` parameter, exactly like `check_dangling_targets`. It is incapable of walking. Apply
therefore cannot add a walk without changing a signature this design pins. Two lines at
`main.py:5011-5013`, reusing the `docs` already bound at `:5010`:

```python
docs, _skip_notices = lint_check.collect_docs(layout.bundle_dir)   # :5010 unchanged
dangling = lint_check.check_dangling_targets(docs)
unextracted = lint_check.check_unextracted(docs)                   # same in-memory list
needs_attention.extend(f"{f.path}: {f.detail}" for f in unextracted)
```

**Proving test** (`tests/unit/cli/test_status.py`):
`test_status_unextracted_reuses_the_single_collect_docs_call` — wrap `lint_check.collect_docs` in a
counting spy via `monkeypatch.setattr(main.lint_check, "collect_docs", spy)`, run `status` on a
workspace holding one `extraction_status: failed` Source, then assert `spy.calls == 1` **and** that
the retry line appears in stdout. A second `collect_docs` (or a `bundle_dir`-taking check) turns it
RED immediately.

## Self-clearing

There is no clearing code, and that is the design. The key is a pure function of the current run's
`skip_reason`: the Source is rebuilt from scratch on every ingest and re-ingest and written with
`fsio.write_atomic` (`:1865`, the regenerate branch), and no code path anywhere reads
`extraction_status` off disk. A successful re-ingest therefore omits it.

**Proving test** (`tests/unit/cli/test_ingest.py`):
`test_successful_reingest_clears_a_previous_failed_extraction_status`

1. `_patch_llm(monkeypatch, raises=OllamaUnavailable("boom"))`; `ingest notes.txt --auto`.
2. Assert `metadata["extraction_status"] == "failed"` on `bundle/sources/notes.md`.
3. Re-patch with `_patch_llm(monkeypatch, _concept_reply())`; re-run `ingest notes.txt --auto`
   (byte-identical source → `regenerate=True`, raw copy reused).
4. Assert `"extraction_status" not in metadata`, `exit_code == 0`, and
   `bundle/concepts/stoic-dichotomy-of-control.md` exists.

Step 4's first assertion is the anti-merge guard: any implementation that read the on-disk value
and merged it forward leaves the key present and fails here.

## ADR gate — evaluated, and declined

| Condition | Verdict |
|---|---|
| (1) Decides a technology, pattern, interface, or trade-off? | **Yes.** A closed-vocabulary key on a canonical, git-tracked document, plus an "absence means healthy" convention |
| (2) Hard to reverse? | **No** |

The deciding test is **recomputable vs. authoritative**. `relations:` (ADR-0004) and `merged_from:`
(ADR-0002) got ADRs because they carry information no other process can regenerate — deleting a
merge ledger destroys the ability to unmerge. `extraction_status` carries nothing that re-running
`openkos ingest` does not reproduce from `raw/`, which is immutable. Reverting Slice 1 leaves inert
keys that §4.1 tolerates, §9 ignores, every reader skips, and the next re-ingest deletes: the
proposal's own rollback section concludes **no migration required**. Changing the vocabulary later
self-heals on re-ingest for the same reason. Both conditions must hold; the config rule says "when
in doubt, do not create one." **No ADR.**

Boundary for a future change: if the key ever gains a value ingest cannot rederive — an attempt
count, a first-failure timestamp, a captured error string — it becomes authoritative state and
crosses the gate. `rules.apply`'s emergent-decision clause applies if apply discovers that.

## File Changes

| Slice | File | Action | Description |
|---|---|---|---|
| 1 | `src/openkos/model/okf.py` | Modify | Key + `Literal` vocabulary; `extraction_status: ExtractionStatus \| None = None` param on `build_source_concept`, emitted only when non-`None` |
| 1 | `src/openkos/cli/main.py` | Modify | `_stage_derived_objects` → `tuple[list[_DerivedPlan], ExtractionStatus \| None]` (4 returns + terminal); `_build_source_document` closure + conditional re-render at `:1717-1745` |
| 1 | `tests/unit/model/test_okf.py` | Modify | Builder tests (4 values + omission) |
| 1 | `tests/unit/cli/test_ingest.py` | Modify | 6 tests (4 paths + clearing + per-candidate-drop negative) |
| 1 | `openspec/specs/ingestion/spec.md` | Modify | Delta (owned by `sdd-spec`) |
| 2 | `src/openkos/lint.py` | Modify | `LintDoc.extraction_status`/`.resource`, `check_unextracted`, `LintReport.unextracted` |
| 2 | `src/openkos/cli/main.py` | Modify | `lint` section render (`:5280+`); `status` fold-in (`:5011-5013`) |
| 2 | `docs/cli.md` | Modify | New `lint` section + retry guidance |
| 2 | `tests/unit/test_lint.py`, `tests/unit/cli/test_lint.py`, `tests/unit/cli/test_status.py` | Modify | ~9 tests |
| 2 | `openspec/specs/{lint,status}/spec.md` | Modify | Deltas (owned by `sdd-spec`) |

## Testing Strategy

Strict TDD, RED first. `uv run pytest`; branch coverage `fail_under = 90`. Every new branch below
is paired with a test, which is what carries the gate.

**Slice 1**

| # | Test | Branch proved |
|---|---|---|
| 1 | `test_build_source_concept_omits_extraction_status_by_default` | `extraction_status is None` → key absent, output byte-identical to today |
| 2 | `test_build_source_concept_emits_each_extraction_status` (parametrized ×4) | Non-`None` → key present with the exact token |
| 3 | `test_empty_source_records_no_extractable_text` | `:1298` |
| 4 | `test_confidential_floor_records_blocked_by_sensitivity` | `:1305`; sibling to `:945`, which stays unmodified |
| 5 | `test_ollama_error_records_failed` | `:1316`; sibling to `:3088`, which stays unmodified |
| 6 | `test_empty_extraction_result_records_no_concepts_found` | `:1329` |
| 7 | `test_successful_ingest_writes_no_extraction_status` | `skip_reason is None` → no key, no second render |
| 8 | `test_all_candidates_dropped_writes_no_extraction_status` | `plans == [] and skip_reason is None` (empty-slug reply) — the state that must stay unmarked |
| 9 | `test_successful_reingest_clears_a_previous_failed_extraction_status` | Self-clearing (see above) |
| 10 | `test_reingest_that_fails_keeps_sensitivity_and_records_failed` | **Cross-guard**: on-disk `confidential` + config `private` + `OllamaError` → `sensitivity == "confidential"` (read from disk, #229) **and** `extraction_status == "failed"` (not read from disk). The two opposite rules, in one run |

**Slice 2**

| # | Test | Branch proved |
|---|---|---|
| 11 | `test_collect_docs_reads_extraction_status_and_resource` | New `LintDoc` fields populated |
| 12 | `test_collect_docs_defaults_both_fields_when_absent` | `.get(..., "")` fallback |
| 13 | `test_check_unextracted_flags_only_failed` (parametrized over all four values + an unknown token) | The fail-silent read: only `failed` produces a finding |
| 14 | `test_check_unextracted_falls_back_when_resource_is_missing` | Empty-`resource` branch |
| 15 | `test_lint_renders_unextracted_section` / `..._empty_state` | Both render branches |
| 16 | `test_lint_exits_zero_with_unextracted_findings` | Non-Gating Exit Contract |
| 17 | `test_status_lists_unextracted_under_needs_attention` | Fold-in |
| 18 | `test_status_unextracted_reuses_the_single_collect_docs_call` | No fifth walk (spy, see above) |
| 19 | `test_status_ignores_blocked_by_sensitivity` | The proposal's explicit non-debt guarantee |

Integration/E2E: none — `testing.layers` enables `unit` only; `CliRunner` invocation over a
`tmp_path` workspace is this repo's established end-to-end substitute.

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or
process-integration boundary is changed. `_autocommit` is downstream on the ingest path but
untouched.

## Changed-Line Forecast and Slice Boundaries

| | Slice 1 (PR1) | Slice 2 (PR2) |
|---|---|---|
| Prod | 45-60 | 70-90 |
| Tests | 140-180 | 200-250 |
| Docs/specs | 30-40 | 55-70 |
| **Authored total** | **~215-280** | **~325-410** |

Confirmed, not revised: each slice sits at or under the shared protocol's 400-line default, and the
pair sits inside the session's 800-line review budget. Delivery is `auto-chain` /
`stacked-to-main`: PR1 targets `main`, PR2 targets PR1's branch. Ordering is mandatory — PR2 reads a
key only PR1 writes, and the rollback ordering is the mirror (revert PR2 first).

## Migration / Rollout

No migration. Pre-existing Sources simply have no key until their next ingest. Rollback per the
proposal: revert Slice 2, then Slice 1; residual keys are inert and self-delete on re-ingest.

## Open Questions

- [ ] None blocking. The proposal's three `auto`-mode resolutions (all four paths write a value;
      no raw `OllamaError` text in git-tracked frontmatter; `lint` stays non-gating) are carried
      forward unchanged and remain the only items warranting user review.
