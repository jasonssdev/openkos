# Apply Progress: `openkos ingest <path>` — null compiler

## Batch 1 (PR 1 — pure primitives + config reader + manifest)

**Branch**: `feat/ingest-primitives`
**Mode**: Strict TDD
**Scope**: Tasks phases 1-6 (Manifest, Config reader, fsio primitives, Bundle
index append, Bundle log append, OKF Source concept builder) + verification
gate run for this PR's surface (not full phase 9, since phase 9.4 `uv build`
+ wheel smoke and the CLI-level tests belong to PR 2).

### Completed Tasks

- [x] 1.1 `pyproject.toml` — declared `pyyaml>=6.0.3` as an explicit direct
  runtime dependency (was already transitive via `python-frontmatter`, no
  new install); ran `uv lock` to sync.
- [x] 2.1 RED — `tests/unit/test_config.py`: `read_config` required-fields,
  missing-keys defaults, `yaml.YAMLError` → `ValueError`, non-mapping root →
  `ValueError`.
- [x] 2.2 GREEN — `config.py`: frozen `Config` dataclass (`model`, `review`,
  `default_sensitivity`) + `read_config(root) -> Config` (PyYAML
  `safe_load`, packaged defaults, wraps `YAMLError`/non-mapping root as
  `ValueError`). `write_config` untouched (byte-identical contract intact).
- [x] 3.1 RED — `tests/unit/test_fsio.py` (new file): `write_atomic`
  overwrite + create, no-leftover-temp-on-success, interrupted-write
  (monkeypatched `os.replace`) leaves original byte-identical;
  `copy_exclusive` binary copy + collision refusal; regression —
  `write_exclusive` still refuses an existing file.
- [x] 3.2 GREEN — `fsio.py`: `write_atomic(path, content)` (temp file in
  `path.parent`, flush + `os.fsync`, `Path.replace` [ruff PTH105 — calls
  `os.replace` under the hood, same atomicity/monkeypatch surface], unlink
  temp on any pre-replace failure); `copy_exclusive(src, dst)` (`"xb"`
  create-only binary copy).
- [x] 4.1 RED — `tests/unit/bundle/test_index.py`: fresh-empty-body index
  gains `# Sources`; frontmatter preserved verbatim; second-entry append
  keeps first; existing `Concepts`/`Decisions`/`People` sections round-trip
  byte-for-byte; append to an already-existing `# Sources` section; plus 2
  defensive-path tests (missing frontmatter, malformed section chunk) added
  during the coverage pass.
- [x] 4.2 GREEN — `bundle/index.py`: `insert_source_entry(index_text, *,
  title, slug, description) -> str`. Splits the frontmatter block off
  verbatim via regex (never re-parses/re-dumps it, so `okf_version`'s quote
  style is never reformatted), parses the body into `# `-headed section
  chunks via `re.split(r"\n(?=# )", body)`, locates `# Sources` or appends a
  new one at the end (always correct per canonical order `[Concepts,
  Decisions, People, Sources]`, since Sources is last), re-renders by
  rejoining with `\n` prefixes.
- [x] 5.1 RED — `tests/unit/bundle/test_log.py`: creates today's section at
  the top when absent; prepends within an existing today's section, prior
  entries unchanged; prior dated sections stay byte-for-byte identical when
  a new day is added; plus 1 defensive-path test (malformed section chunk)
  added during the coverage pass.
- [x] 5.2 GREEN — `bundle/log.py`: `insert_log_entry(log_text, today, entry)
  -> str`. Same parse-then-render shape as `insert_source_entry` but keyed
  on `## YYYY-MM-DD` sections and prepending (not appending) within the
  matched section; creates a new section at the very top when today's date
  is absent.
- [x] 6.1 RED — `tests/unit/model/test_okf.py`: `build_source_concept`
  emits all required frontmatter fields + `# Citations` body; passes
  `check_conformance`; description makes no extraction claim
  (pass-through honesty test); `sensitivity` equals the passed value.
- [x] 6.2 GREEN — `model/okf.py`: `build_source_concept(*, title,
  description, resource, tags, timestamp, sensitivity, provenance) -> str`.
  Plain dict → `dump_frontmatter`, no pydantic (D4). `description` is
  passed through verbatim — the builder does not itself validate honesty,
  callers (the future `ingest` CLI, PR 2) must supply an honest
  null-compiler description.

### Files Changed

| File | Action | What Was Done |
|------|--------|----------------|
| `pyproject.toml` | Modified | Declared `pyyaml>=6.0.3` direct dependency; later added `types-pyyaml` to `dev` group for mypy strict (stub resolution) |
| `uv.lock` | Modified | Re-locked after both manifest edits (no new installs for `pyyaml`; `types-pyyaml` added) |
| `src/openkos/config.py` | Modified | Added `DEFAULT_REVIEW`, `DEFAULT_SENSITIVITY`, `Config` (frozen dataclass), `read_config(root) -> Config` |
| `src/openkos/fsio.py` | Modified | Added `write_atomic(path, content)`, `copy_exclusive(src, dst)`; `write_exclusive` untouched |
| `src/openkos/bundle/index.py` | Modified | Added `_split_frontmatter_verbatim`, `_section_header`, `insert_source_entry(index_text, *, title, slug, description) -> str` |
| `src/openkos/bundle/log.py` | Modified | Added `insert_log_entry(log_text, today, entry) -> str` |
| `src/openkos/model/okf.py` | Modified | Added `build_source_concept(*, title, description, resource, tags, timestamp, sensitivity, provenance) -> str` |
| `tests/unit/test_config.py` | Modified | +4 tests for `read_config` |
| `tests/unit/test_fsio.py` | Created | 7 tests for `write_atomic`/`copy_exclusive` + `write_exclusive` regression |
| `tests/unit/bundle/test_index.py` | Modified | +7 tests for `insert_source_entry` (incl. 2 defensive-path tests) |
| `tests/unit/bundle/test_log.py` | Modified | +4 tests for `insert_log_entry` (incl. 1 defensive-path test) |
| `tests/unit/model/test_okf.py` | Modified | +4 tests for `build_source_concept` |

### TDD Cycle Evidence

| Task | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|------|-----------|-------|------------|-----|-------|-------------|----------|
| 2.1/2.2 `read_config` | `tests/unit/test_config.py` | Unit | ✅ 44/44 (baseline) | ✅ Written, confirmed `AttributeError` | ✅ 48/48 passed | ✅ 4 cases (required fields, defaults, YAMLError, non-mapping root) | ➖ None needed — already minimal |
| 3.1/3.2 `write_atomic`/`copy_exclusive` | `tests/unit/test_fsio.py` | Unit | N/A (new file); `write_exclusive` regression test included and passed | ✅ Written, confirmed `AttributeError` | ✅ 7/7 passed | ✅ 6 cases (overwrite, create, no-leftover-temp, interrupted-write, binary-copy, collision-refusal) | ✅ `os.replace` → `Path.replace` (ruff PTH105); docstring updated to match |
| 4.1/4.2 `insert_source_entry` | `tests/unit/bundle/test_index.py` | Unit | ✅ 1/1 (baseline `render_index` test) | ✅ Written first; confirmed RED via `git stash` of the production diff (`ImportError`) | ✅ 6/6 passed on first GREEN run | ✅ 6 cases + 2 defensive-path cases added during coverage pass (8 total) | ✅ `ruff format` reformatted long line; re-verified green after |
| 5.1/5.2 `insert_log_entry` | `tests/unit/bundle/test_log.py` | Unit | ✅ 2/2 (baseline `render_log` tests) | ✅ Written, confirmed `ImportError` | ✅ 5/5 passed on first GREEN run | ✅ 4 cases + 1 defensive-path case added during coverage pass (6 total) | ✅ `ruff format` reformatted; re-verified green after |
| 6.1/6.2 `build_source_concept` | `tests/unit/model/test_okf.py` | Unit | ✅ 11/11 (baseline) | ✅ Written, confirmed `AttributeError` | ✅ 15/15 passed | ✅ 4 cases (required fields, `check_conformance`, honest description, sensitivity pass-through) | ➖ None needed — already minimal |

### Test Summary

- **Total tests written this batch**: 25 (4 config + 7 fsio + 8 index + 6 log + 4 okf, note index/log counts include 3 defensive-path tests added during the coverage tightening pass)
- **Total tests passing (full suite)**: 127/127
- **Layers used**: Unit (127)
- **Approval tests** (refactoring): None — no refactoring tasks in this batch, only additive functions
- **Pure functions created**: 5 (`read_config`, `write_atomic`, `copy_exclusive`, `insert_source_entry`, `insert_log_entry`, `build_source_concept` — 6 total, `write_atomic`/`copy_exclusive` are the only ones with filesystem side effects by necessity)

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `uv run pytest tests/unit/test_config.py tests/unit/test_fsio.py tests/unit/bundle/test_index.py tests/unit/bundle/test_log.py tests/unit/model/test_okf.py -q` → 84 passed |
| Runtime harness command/scenario and exact result | N/A — pure/fs primitives only, exercised via `tmp_path` fixtures; no CLI entrypoint wired yet (per tasks.md Suggested Work Units table for Unit 1) |
| Rollback boundary | `git revert` this branch's range; every change is purely additive (new functions/fields), nothing outside these tests imports them yet — `init`/`cli/main.py` untouched |

### Deviations from Design

- D1 specified `os.replace(tmp, path)`; implemented as `tmp_path.replace(path)`
  instead, to satisfy ruff `PTH105` (pathlib preference). `Path.replace`
  calls `os.replace` internally in CPython, so the atomicity guarantee and
  the `monkeypatch.setattr(os, "replace", ...)` test technique both hold
  unchanged — confirmed by the interrupted-write test passing. Noted here
  since it's a literal deviation from the design's stated call, even though
  behavior is identical.
- Added `types-pyyaml` to the `dev` dependency group (not mentioned in
  design/tasks) — required for `mypy --strict` to resolve PyYAML's types;
  otherwise `import-untyped` fails the verification gate.
- Added 3 tests beyond the RED tasks explicitly listed (2 in
  `test_index.py`, 1 in `test_log.py`) covering the defensive
  `ValueError` branches in the parse helpers, to close per-file branch-coverage
  gaps found during the `--cov` run (index.py was 88%, log.py 92% before
  these; both now 100%). Not a scope deviation — same functions, same task,
  additional triangulation.

### Issues Found

None.

### Remaining Tasks (PR 2 — not in this batch)

- [ ] 7.1 RED — `tests/unit/cli/test_ingest.py`
- [ ] 7.2 GREEN — `cli/main.py`: `ingest` command
- [ ] 7.3 REFACTOR — `cli/main.py` docstring
- [ ] 8.1 `docs/cli.md`
- [ ] 9.1-9.4 Full verification gate (this batch already ran 9.1-9.3 for its
  own surface; full-suite 9.1-9.3 rerun and 9.4 `uv build` + wheel smoke
  belong to PR 2's close-out)

### Verification Gate (run for this PR's full changed surface, i.e. the whole repo since no other work is in flight)

- `uv run pytest --cov -q` → **127 passed**, coverage **100.00%** (gate requires ≥90%)
- `uv run ruff check .` → **All checks passed**
- `uv run ruff format --check .` → **19 files already formatted**
- `uv run mypy .` → **Success: no issues found in 19 source files**

Not run this batch (explicitly out of scope per orchestrator instructions):
`uv build` + wheel smoke test (task 9.4) — deferred to PR 2 close-out.

### Status

11/19 tasks complete (phases 1-6 of 9). Ready for orchestrator to route to
post-apply review (`review/start(target)`) for PR 1, then PR 2's `sdd-apply`
batch (CLI wiring, phases 7-9).

## Review Hardening (PR 1 — post-approval, non-blocking findings)

Branch: `feat/ingest-primitives`. Mode: Strict TDD (RED confirmed failing
before each GREEN). Scope: exactly 4 non-blocking findings from PR 1's
bounded review, requested by the user to fix before merge. No other files
touched; no commit made (per instruction).

### Fixes Applied

1. **`config.read_config` present-but-null** (`config.py`) — `raw.get(key,
   DEFAULT)` only falls back when `key` is ABSENT; a key present with an
   explicit YAML `null` (`model: null` / bare `model:`) parsed to `None`,
   violating `Config`'s typed fields. Fixed: each field now checked
   `is not None` (not truthiness) before falling back, so `review: false`
   (a real, non-`None` value) is preserved, only `None` (absent or null)
   falls back.
2. **`fsio.copy_exclusive` partial-write cleanup** (`fsio.py`) — a write
   failure after `dst.open("xb")` already created `dst` left a partial file
   behind; since `dst` is create-only, every retry then raised
   `FileExistsError`, blocking recovery. Fixed: the `f.write(content)` call
   is now wrapped in `try`/`except BaseException`, unlinking `dst` before
   re-raising — mirrors `write_atomic`'s pattern. (First attempt wrapped
   `dst.open("xb")` too, which wrongly unlinked a genuinely pre-existing
   `dst` on `FileExistsError` — caught by the existing regression test
   `test_copy_exclusive_raises_on_existing_destination`; narrowed the `try`
   to only the write.)
3. **`fsio.write_atomic` non-unique temp name** (`fsio.py`) — the temp name
   was the deterministic `.{path.name}.tmp`; two concurrent same-path writes
   could share/clobber it. Fixed: temp name now includes `os.getpid()` and a
   `uuid.uuid4().hex` token, still created in `path.parent` (same
   filesystem, atomic rename preserved).
4. **`fsio.write_atomic` docstring overclaim** (`fsio.py`, docstring-only) —
   softened the durability claim to match D1: visibility atomicity comes
   from the rename (a reader sees the whole old or whole new file); content
   is fsynced before rename; a directory fsync is deliberately deferred, so
   the rename's cross-crash durability is NOT guaranteed — only that `path`
   is never left half-written. No directory fsync added (design deferred it
   intentionally).

### TDD Cycle Evidence (hardening batch)

| Fix | Test File | RED | GREEN | Notes |
|---|---|---|---|---|
| 1. present-but-null | `test_config.py` | 6 parametrized cases + regression test written first, confirmed failing (6 failed) | All pass | Regression test `review: false` stays `False` also covered by pre-existing `test_read_config_reads_required_fields` |
| 2. `copy_exclusive` cleanup | `test_fsio.py` | Written first, confirmed failing (`dst.exists()` was `True`) | Passes after narrowing `try` scope | One iteration: first GREEN attempt broke a pre-existing regression test, caught immediately by full-suite rerun, fixed by scoping `try` to the write only |
| 3. unique temp name | `test_fsio.py` | Written first, confirmed failing (both calls recorded `.index.md.tmp`) | Passes, names differ (pid+uuid) | Pre-existing `os.replace` monkeypatch interrupted-write test still passes unchanged |
| 4. docstring | N/A (docs-only) | N/A | N/A | No behavior change; full suite + mypy/ruff rerun to confirm no regression |

### Verification Gate (full repo, rerun after hardening)

- `uv run pytest --cov -q` → **136 passed** (127 baseline + 9 new: 6 null-fallback + 1 regression + 1 copy_exclusive cleanup + 1 unique-temp-name), coverage **100.00%** (gate ≥90%)
- `uv run ruff check .` → All checks passed
- `uv run ruff format --check .` → 19 files already formatted
- `uv run mypy .` → Success: no issues found in 19 source files

### Files Changed (hardening batch only)

| File | Action | What Was Done |
|------|--------|----------------|
| `src/openkos/config.py` | Modified | `read_config`: `is not None` fallback per field instead of `dict.get` default |
| `src/openkos/fsio.py` | Modified | `write_atomic`: unique temp name (pid+uuid) + softened docstring; `copy_exclusive`: unlink `dst` on write failure |
| `tests/unit/test_config.py` | Modified | +7 tests (6 null-fallback parametrized cases + 1 explicit `review: false` regression) |
| `tests/unit/test_fsio.py` | Modified | +2 tests (`copy_exclusive` cleanup-on-failure, `write_atomic` unique-temp-name) |

### Scope Note

Did NOT touch: markdown-escaping findings (`index.py`/`log.py`), `okf.py`
literals, or anything in PR2 scope (phases 7-9) — those were explicitly
excluded from this hardening pass by the user. Working tree left
uncommitted per instruction.
