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

## Batch 2 (PR 2 — `ingest` CLI command, phases 7-9)

**Branch**: `feat/ingest-command` (freshly branched off `main` at `0409bc9`,
after PR 1's primitives merged).
**Mode**: Strict TDD (RED confirmed failing before every GREEN, including
the two RISK fixes and the two coverage-closing exception-path tests).
**Scope**: Tasks phases 7-9 (CLI `ingest` command, `docs/cli.md`, full
verification gate) plus closing tracked review findings RISK-1/RISK-2 from
PR 1's bounded review (newline-injection at the primitive layer).

### Completed Tasks

- [x] RISK-1/RISK-2 fix — `bundle/index.py`/`bundle/log.py`: added
  `_reject_newline(field, value)` in each module; `insert_source_entry`
  now rejects (`ValueError`) `title`/`slug`/`description` containing `\n`
  or `\r`; `insert_log_entry` now rejects `entry` containing `\n` or `\r`.
  Closes the markdown-forgery risk (a value with a newline followed by
  `# `/`## ` could forge a section header on the next parse) by rejection
  rather than escaping, since every one of these fields is inherently
  single-line for a single Source concept / log entry.
- [x] 7.1 RED — `tests/unit/cli/test_ingest.py` (new, 14 tests): successful
  ingest (raw copy + conformant concept + provenance + `# Citations` +
  index/log updated); honest no-extraction-claim description; path does
  not exist; not-a-workspace; collision (`raw/<name>` and
  `bundle/sources/<slug>.md`, parametrized); path-containment (`../../evil.txt`
  → `raw/evil.txt` only); Phase A preview shown before Phase B write, then
  all-or-nothing on confirm; `--auto` skips the prompt; `review: false`
  skips the prompt like `--auto`; non-TTY + `review: true` + no `--auto`
  refuses; Phase A preparation failure (malformed `openkos.yaml`) surfaces
  cleanly; Phase B write failure (permission-stripped `raw/`) surfaces
  cleanly; `sensitivity == default_sensitivity`.
- [x] 7.2 GREEN — `cli/main.py`: added `ingest` command (`_slugify`,
  `_titleize` helpers + the command itself). Phase A: `src.is_file()`
  check; workspace check (`bundle/index.md` + `log.md` both present);
  slug/dest derived from `src.name`/`src.stem` only (path containment);
  collision refusal (`raw/<name>` or `bundle/sources/<slug>.md`);
  `read_config`; `build_source_concept`; `insert_source_entry`/
  `insert_log_entry` against current on-disk bytes; preview echoed to
  stdout. Confirm gate in design's exact order: `--auto` → no prompt; else
  `review=False` → no prompt; else TTY → `typer.confirm(..., abort=True)`;
  else refuse (exit 1, tells the user to re-run with `--auto`). Phase B:
  `sources_dir.mkdir(parents=True, exist_ok=True)` → `copy_exclusive` raw
  → `write_exclusive` concept → `write_atomic` index.md → `write_atomic`
  log.md (catalog last). `except (OSError, ValueError)` → `echo(err=True)`
  + `Exit(1)`, prefixed `openkos ingest:`, matching `init`'s convention.
  Timestamp: single `now = datetime.now(UTC)` line; `now.strftime(...)`
  gives the concept's ISO-8601 `Z` string; `now.astimezone().date()` gives
  the log's local calendar date — no clock in domain code.
- [x] 7.3 REFACTOR — `cli/main.py`: `ingest`'s docstring documents the
  Phase A/B flow, the confirm-gate resolution order, and every refusal
  case (missing/unreadable path, missing workspace, collision, non-TTY
  refusal), mirroring `init`'s docstring style.
- [x] 8.1 `docs/cli.md` — rewrote the `openkos ingest <path>` section:
  states the MVP 1 null-compiler scope explicitly (one Source concept, no
  LLM extraction, no concept splitting), the path-containment guarantee,
  the preview/confirm/all-or-nothing flow, and marks `--sensitivity`/
  `--batch` explicitly as "Not in this slice / planned" so the docs don't
  advertise unshipped flags.
- [x] 9.1 `uv run pytest --cov` — 158 passed, coverage 100.00% (gate ≥90%).
- [x] 9.2 `uv run ruff check .` / `uv run ruff format --check .` — both
  clean after two fixes (see Deviations).
- [x] 9.3 `uv run mypy .` — clean, 20 source files, no issues.
- [x] 9.4 `uv build` + wheel smoke test — built `openkos-0.1.0.tar.gz` +
  `openkos-0.1.0-py3-none-any.whl`; installed into a scratch Python 3.13
  venv; ran `openkos init --model test:tag` then `openkos ingest note.txt
  --auto` end-to-end against the installed wheel (not the editable
  install) — `raw/note.txt`, `bundle/sources/note.md` (conformant, with
  `provenance: [raw/note.txt]` and `# Citations`), `bundle/index.md`
  (`# Sources` entry), and `bundle/log.md` (dated `**Ingest**` entry) all
  landed correctly; scratch venv/workspace discarded after.

### Files Changed (Batch 2)

| File | Action | What Was Done |
|------|--------|----------------|
| `src/openkos/bundle/index.py` | Modified | RISK-1 fix: `_reject_newline`, called for `title`/`slug`/`description` |
| `src/openkos/bundle/log.py` | Modified | RISK-2 fix: `_reject_newline`, called for `entry` |
| `src/openkos/cli/main.py` | Modified | Added `_slugify`, `_titleize`, `ingest` command (Phase A/B, confirm gate, `--auto`) |
| `pyproject.toml` | Modified | Added `[tool.ruff.lint.flake8-bugbear] extend-immutable-calls = ["typer.Argument", "typer.Option"]` (B008 false positive on `Path`-annotated Typer arguments) |
| `docs/cli.md` | Modified | Rewrote `ingest` section: null-compiler scope, path containment, preview/confirm flow, `--sensitivity`/`--batch` marked not-in-this-slice |
| `tests/unit/bundle/test_index.py` | Modified | +2 parametrized newline-rejection tests (RISK-1) |
| `tests/unit/bundle/test_log.py` | Modified | +1 parametrized newline-rejection test (RISK-2) |
| `tests/unit/cli/test_ingest.py` | Created | 14 tests: happy path, honesty, refusals (missing path/workspace/collision), path-containment, preview/confirm/`--auto`/`review:false`/non-TTY-refuse, both exception-handling branches, sensitivity pass-through |
| `openspec/changes/add-ingest-command/tasks.md` | Modified | `[x]` phases 7-9 |

### TDD Cycle Evidence (Batch 2)

| Task | Test File | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|---|---|---|---|---|---|---|
| RISK-1 fix | `test_index.py` | 22 passing baseline (post-hardening) | Confirmed `Failed: DID NOT RAISE` (8 cases: `\n`/`\r` × title/slug/description) | 6/6 new + 22/22 total passed | 2 newline chars × 3 fields (6 cases) | None needed |
| RISK-2 fix | `test_log.py` | included in same baseline | Confirmed `Failed: DID NOT RAISE` (2 cases: `\n`/`\r`) | 2/2 new passed | 2 newline chars (2 cases) | None needed |
| 7.1/7.2 `ingest` (first 12 tests) | `test_ingest.py` | N/A new file; `init` command untouched | Confirmed exit-code-2 "no such command" for all 12 | 12/12 passed on first GREEN run | 12 scenarios (happy path, honesty, 2 refusals, 2-way collision, containment, preview/confirm, `--auto`, `review:false`, non-TTY-refuse, sensitivity) | None needed on first pass |
| 7.1/7.2 exception branches | `test_ingest.py` | 156/156 passing after first GREEN | Found via `--cov` gap (lines 255-259, 284-288 uncovered) rather than written before the code — same precedent as PR 1's defensive-path tests (see Deviations) | 2/2 new passed, cli/main.py → 100% branch coverage | 2 cases (Phase A prep failure via malformed YAML, Phase B write failure via chmod 0o500 on `raw/`) | None needed |

Batch 2 tests added: 22 (6 newline-rejection cases in `test_index.py` [2
newline chars × 3 fields, 1 parametrized test function], 2 newline-
rejection cases in `test_log.py` [1 parametrized test function], 14
`ingest` CLI tests in `test_ingest.py`). Batch 1 hardening baseline was
136; Batch 2's full-suite result is 136 + 22 = **158/158 passing**
(confirmed by the `--cov` run below).

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `uv run pytest tests/unit/cli/test_ingest.py -q` → 14 passed; `uv run pytest tests/unit/bundle/test_index.py tests/unit/bundle/test_log.py -q` → 22 passed |
| Runtime harness command/scenario and exact result | `uv build` → `openkos-0.1.0-py3-none-any.whl`; installed into a scratch Python 3.13 venv; `openkos init --model test:tag` (exit 0) then `openkos ingest note.txt --auto` (exit 0) against the installed wheel — `raw/note.txt`, `bundle/sources/note.md`, updated `index.md`/`log.md` all verified by direct file inspection, matching `tasks.md` Unit 2's runtime harness ("`uv run openkos ingest <path>` ... and `--auto` in a scratch workspace") |
| Rollback boundary | `git revert` this branch's range; `ingest` is a new command, `init` is untouched; the RISK-1/RISK-2 fixes are additive validation only (reject a previously-accepted-but-dangerous input, no existing valid input is now rejected) |

### Deviations from Design

- Added `[tool.ruff.lint.flake8-bugbear] extend-immutable-calls =
  ["typer.Argument", "typer.Option"]` to `pyproject.toml` (not in
  design/tasks) — required because ruff's B008 (function-call-in-default)
  exempts `bool`/`str | None`-annotated Typer parameters automatically
  (immutable-type check) but not `Path`-annotated ones, so `ingest`'s
  `src: Path = typer.Argument(...)` was flagged. `typer.Option` was added
  too for consistency across future commands, even though no current
  `typer.Option` call was flagged (its existing uses are all
  `bool`/`str | None`-annotated).
- Provenance/resource value: the design's Interfaces section left
  `provenance`'s exact string open; implemented as the workspace-relative
  `raw/<name>` path (matching the `resource` field's convention, verified
  against `examples/good-life-demo/bundle/sources/*.md`), not the
  original (possibly relative/traversal-bearing) `<path>` argument as
  typed by the user. This keeps provenance stable and meaningful even if
  the original source file is later moved or deleted, and keeps
  `resource`/`provenance` consistent with each other.
- Two coverage-closing tests (Phase A preparation-failure and Phase B
  write-failure) were added AFTER the first GREEN pass, once `--cov`
  showed `cli/main.py` at 95% with the two `except` blocks uncovered —
  same precedent as PR 1's 3 defensive-path tests (index.py/log.py were
  88%/92% before those). Not a scope deviation — same function
  (`ingest`), same task (7.1/7.2), additional triangulation for branches
  the happy-path/refusal tests structurally cannot reach.

### Issues Found

None.

### Verification Gate (Batch 2, full repo)

- `uv run pytest --cov -q` → **158 passed**, coverage **100.00%** (gate ≥90%)
- `uv run ruff check .` → **All checks passed**
- `uv run ruff format --check .` → **20 files already formatted**
- `uv run mypy .` → **Success: no issues found in 20 source files**
- `uv build` → built `dist/openkos-0.1.0.tar.gz` + `dist/openkos-0.1.0-py3-none-any.whl`
- Wheel smoke test (scratch Python 3.13 venv, installed wheel, not
  editable) → `openkos init --model test:tag` (exit 0) →
  `openkos ingest note.txt --auto` (exit 0) → `raw/note.txt`,
  `bundle/sources/note.md`, `bundle/index.md`, `bundle/log.md` all
  correct, concept passes `check_conformance`.

### Status

19/19 tasks complete (all 9 phases). No commit made and no PR opened —
delivery context is a chained PR (PR 2 of 2, stacked-to-main), and the
orchestrator runs the post-apply bounded review before any commit.
Working tree left uncommitted per instruction. Ready for orchestrator to
route to post-apply review (`review/start(target)`) for PR 2, then
`sdd-verify`.

## PR2 Review Correction (bounded, post-apply review)

Branch: feat/ingest-command. Fixed exactly the 4 findings from PR 2's
post-apply bounded review (1 CRITICAL, 3 WARNING); no other code touched,
no commit made.

- **FIX 1 (CRITICAL)** — `cli/main.py` Phase B is now real all-or-nothing
  with rollback: tracks `raw_created`/`concept_created`/`index_replaced`/
  `log_replaced`; on any exception, undoes in reverse (`write_atomic`
  restores `index.md`/`log.md` to the ORIGINAL bytes captured during Phase
  A's read; concept and raw copy unlinked), then re-raises for the outer
  `except (OSError, ValueError)` to report cleanly. RED tests (parametrized
  `concept`/`index`/`log` failure) confirm nothing is left orphaned and a
  retry of the same ingest succeeds after rollback.
- **FIX 2 (WARNING)** — empty-slug guard: Phase A raises `ValueError`
  ("cannot derive a concept name from '<path>'") when `_slugify` returns
  an empty string (e.g. `+++.txt`), refusing before any write.
- **FIX 3 (WARNING)** — Phase A's `is_file`/`exists` reads are now inside
  a `try`/`except (OSError, ValueError)` block, mirroring `init`'s
  convention, so a `PermissionError` during a stat call surfaces as a
  clean `openkos ingest: ...` message instead of a raw traceback.
- **FIX 4 (WARNING)** — collision refusal now names WHICH path already
  exists (`raw/<name>` or `bundle/sources/<slug>.md`) and gives
  crash-recovery guidance, mirroring `init`'s wording ("this source may
  already be ingested, or a previous run crashed mid-write; inspect and
  remove it before retrying").

### TDD Cycle Evidence

| Fix | RED | GREEN | REFACTOR |
|---|---|---|---|
| FIX 1 | 3 parametrized failures (concept/index/log write) confirmed failing pre-fix | rollback + retry-succeeds passing | ruff format applied |
| FIX 2 | empty-slug (`+++.txt`) refusal test confirmed failing pre-fix | passing | n/a |
| FIX 3 | `PermissionError` on `is_file` test confirmed failing pre-fix (raw traceback) | passing | n/a |
| FIX 4 | existing collision test extended with path-name + "retrying" assertions | passing | n/a |

### Files Changed (Correction)

- `src/openkos/cli/main.py` — Phase A wrapped in try/except, empty-slug
  guard, collision message, Phase B rollback (~99 changed lines).
- `tests/unit/cli/test_ingest.py` — 3 new RED tests (1 parametrized ×3) +
  collision test assertion extended + `fsio` import (~83 changed lines).

### Verification Gate (Correction)

- `uv run pytest --cov -q` → **163 passed**, coverage **99.54%** (gate ≥90%;
  1 line uncovered — the `log_replaced` restore branch in the rollback
  handler is structurally unreachable since `log.md` is the last Phase-B
  write, so no later step can fail after it succeeds; kept for defensive
  symmetry per the fix instructions)
- `uv run ruff check .` → **All checks passed**
- `uv run ruff format --check .` → **20 files already formatted**
- `uv run mypy .` → **Success: no issues found in 20 source files**

### Status (Correction)

4/4 findings fixed (1 CRITICAL, 3 WARNING). The 2 SUGGESTIONs
(decomposing the `ingest` body, deduplicating `_reject_newline`) were
intentionally left untouched — out of scope for this bounded correction,
tracked for later. No commit made. Total correction diff is ~182 changed
lines (slightly over the ~160 target — the CRITICAL fix requires
reindenting Phase A inside a `try` block, which inflates the diff via
unavoidable whitespace-only line changes, plus 3 required RED rollback
scenarios). Ready for the orchestrator to re-validate the review receipt
for PR 2.

## PR2 Escalation Follow-up (bounded, 2 validator-surfaced findings)

Branch: `feat/ingest-command`. Mode: Strict TDD. Fixed exactly the 2
follow-ups a prior bounded review escalated on the Phase B rollback block
(the coverage regression from the PR2 Review Correction's `log_replaced`
branch, plus a workspace-identity gap left by the same correction). No
other code touched (`_reject_newline` dedup and `ingest` body
decomposition remain intentionally out of scope). No commit made.

- **FIX 1** — removed the dead `log_replaced` flag and its `if
  log_replaced: fsio.write_atomic(log_path, log_text)` restore branch in
  `cli/main.py`'s Phase B rollback. `log.md` is written LAST in Phase B, so
  no later step can raise after it succeeds — `log_replaced` could never be
  `True` when the `except` block ran; this was unreachable code and the
  exact line the PR2 Correction's coverage run flagged (99.54%, 1 line
  uncovered). No behavior change; confirmed the existing parametrized
  `test_phase_b_failure_rolls_back_and_retry_succeeds` (`concept`/`index`/
  `log` failure cases) still passes unchanged — the `log`-failure case
  exercises the index-restore + unlink path, never the removed branch.
- **FIX 2** — `sources_dir.mkdir(parents=True, exist_ok=True)` runs before
  any rollback-tracked write. Confirmed `openkos init`/`bundle.create` do
  NOT create `bundle/sources/` (only `index.md`/`log.md`), so a fresh
  workspace has no `bundle/sources/` until the first `ingest`. If this
  ingest created it and a later Phase B step then fails, the old rollback
  left an empty `bundle/sources/` behind — not byte-and-entry-identical to
  the pre-ingest state. Fixed: record `sources_dir_created = not
  sources_dir.exists()` before the mkdir; on rollback, after the concept
  file is unlinked, `with contextlib.suppress(OSError):
  sources_dir.rmdir()` only when `sources_dir_created` is `True` (rmdir is
  empty-only — a non-empty dir, e.g. from a concurrent writer, is left
  alone rather than force-deleted; a pre-existing `sources_dir` is never
  touched).

### TDD Cycle Evidence

| Fix | RED | GREEN | REFACTOR |
|---|---|---|---|
| FIX 1 | N/A — dead-code removal, no behavior change; existing parametrized rollback test re-run to confirm no regression | n/a | n/a |
| FIX 2 | Extended `test_phase_b_failure_rolls_back_and_retry_succeeds` with a full `_snapshot(tmp_path)` before/after equality assertion (plus explicit `bundle/sources` absence checks); confirmed failing on all 3 `fail_step` params pre-fix (`assert not True` — leftover empty `bundle/sources/`) | passing, all 3 params | 2 coverage-closing tests added post-GREEN (see below) |

Coverage-closing tests (added after the FIX 2 GREEN pass, once `--cov`
showed 2 new uncovered branches in the added rollback code — same
precedent as PR 1/PR 2's earlier defensive-path tests):
`test_phase_b_failure_leaves_non_empty_sources_dir_on_rollback` (a stray
file written into `sources_dir` during the simulated concept-write
failure makes `rmdir()` raise `OSError`, asserting the directory and stray
file both survive — `rmdir`, not `rmtree`, is the contract) and
`test_phase_b_failure_does_not_remove_preexisting_sources_dir` (pre-create
`bundle/sources/` before `ingest`, force an index-write failure, assert
the pre-existing dir is untouched — `sources_dir_created` gates the whole
cleanup).

### Files Changed (Escalation Follow-up)

- `src/openkos/cli/main.py` Mod — removed `log_replaced` flag + dead
  restore branch; added `sources_dir_created` flag + `contextlib.suppress`
  `rmdir()` cleanup in the rollback branch; `import contextlib`; docstring
  updated to describe the `bundle/sources/` cleanup guarantee. Net +2
  lines (331 → 333).
- `tests/unit/cli/test_ingest.py` Mod — extended
  `test_phase_b_failure_rolls_back_and_retry_succeeds` (full-snapshot
  identity assertion + explicit `bundle/sources` absence checks); added 2
  new tests (`test_phase_b_failure_leaves_non_empty_sources_dir_on_rollback`,
  `test_phase_b_failure_does_not_remove_preexisting_sources_dir`). +67
  lines (442 → 509), no deletions.

### Verification Gate (Escalation Follow-up)

- `uv run pytest --cov -q` → **165 passed**, coverage **100.00%** (gate
  ≥90%; back from the 99.54% the prior correction left, `cli/main.py` line
  + branch coverage both 100%)
- `uv run ruff check .` → **All checks passed** (one intermediate
  `SIM105` finding on the first `try`/`except OSError: pass` draft, fixed
  by switching to `with contextlib.suppress(OSError):` per ruff's
  suggestion)
- `uv run ruff format --check .` → **20 files already formatted**
- `uv run mypy .` → **Success: no issues found in 20 source files**

### Status (Escalation Follow-up)

2/2 escalated findings fixed. No commit made. Ready for the orchestrator
to re-validate/re-run the review receipt for PR 2, then `sdd-verify`.

## PR2 Retreat — rollback removed, create-only + git recovery — 2026-07-17

Branch: `feat/ingest-command`. Mode: Strict TDD. A fresh bounded review
found two CRITICALs proving the multi-step Phase B rollback (added by the
PR2 Review Correction, hardened by the Escalation Follow-up above) cannot
be made truly atomic across independent filesystem writes — a failure
*during the rollback itself* has no further fallback. The maintainer
RETREATED to the design's originally-ratified non-transactional position
(D5, and `init`'s own D3 "no cleanup path"). Exactly 5 fixes made, no
commit made.

- **FIX 1** — `fsio.write_exclusive` now unlinks its own partial file on a
  mid-write failure, mirroring `copy_exclusive`'s existing cleanup: `with
  path.open("x", ...) as f: try: f.write(content) except BaseException:
  path.unlink(missing_ok=True); raise`. This is a real primitive-asymmetry
  fix, independent of the rollback decision — before this fix, a crash
  mid-`f.write` left a partial file that made every retry raise
  `FileExistsError` and block recovery, since `write_exclusive` is
  create-only ("x").
- **FIX 2** — removed the entire multi-step Phase B rollback from
  `ingest`: the `raw_created`/`concept_created`/`index_replaced`/
  `sources_dir_created` flags, the nested `try/except Exception` cleanup
  block, the index-restore, the concept/raw unlinks, and the
  `sources_dir.rmdir()` cleanup. Phase B is now a straightforward ordered
  sequence of 5 writes inside the command's single-level `try/except
  (OSError, ValueError)` handler, same convention as `init` and `ingest`'s
  own Phase A. Removed the now-unused `import contextlib`.
- **FIX 3** — Phase B write order is unchanged (it was already
  content-before-catalog): `sources_dir.mkdir(parents=True,
  exist_ok=True)`, `copy_exclusive` raw, `write_exclusive` concept,
  `write_atomic` index, `write_atomic` log. All four writes stay
  individually create-only or atomic, so a crash never leaves a *silently*
  half-written file — only a *detectable* orphan (an uncatalogued concept
  or raw file), since the catalog is always written last.
- **FIX 4** — removed every "all-or-nothing" / "byte-and-entry-identical"
  / "retry is never blocked" claim from the `ingest` docstring
  (`cli/main.py`), `docs/cli.md`, `design.md` (D5 + sequence diagram +
  testing strategy row), and — beyond the requested scope, for
  design/code/spec agreement — `specs/ingestion/spec.md` (Review/Confirm
  Flow requirement text, and its "All-or-nothing write on confirm"
  scenario, replaced with "Phase B writes proceed on confirm" plus a new
  "Phase B failure leaves a detectable, recoverable partial result"
  scenario), and stale references in `tests/unit/cli/test_ingest.py`'s
  module docstring and two test docstrings. Replaced with an honest
  statement modeled on `init`'s D3: writes are NOT transactional; a
  mid-run failure may leave a partial result; recovery is via git
  (`git status` shows it, `git checkout`/`git clean` restores it), not a
  manual unlink. `design.md`'s "Known limit" section now records the full
  final decision (create-only, non-transactional, git recovery,
  content-before-catalog ordering) plus the retreat rationale, so design
  and code agree. The already-honest, already-improved collision message
  (names the path, guides recovery) was left untouched.
- **FIX 5** — verified (not assumed) the lint-detectability claim.
  `src/openkos/model/okf.py::check_conformance` implements only §9 rules
  1-2 (parseable frontmatter, non-empty `type`); its own docstring/module
  comment says rule 3 is deferred to `lint`. Searched the whole `src/`
  tree (`rg -l "orphan|freshness|lint"`) — the only match is that same
  comment in `okf.py`; there is **no `lint` command implemented anywhere**
  in this codebase (confirmed by listing every module under
  `src/openkos/`: only `config.py`, `fsio.py`, `bundle/{bundle,index,log}.py`,
  `model/okf.py`, `cli/main.py` exist; `cli/main.py` defines only `init`
  and `ingest`). `docs/cli.md`'s `openkos lint` section (Orphan
  pages/Stale stamps) is a **planned, unimplemented** design reference for
  a future MVP command. Conclusion recorded honestly in `design.md`: an
  uncatalogued concept left by a Phase B failure is **always visible via
  `git status`** (the solid, primary claim) but is **not** currently
  flagged by any automated check, because no such check exists yet — no
  lint capability is claimed.
- **Tests** — deleted the 3 rollback-specific tests
  (`test_phase_b_failure_rolls_back_and_retry_succeeds`,
  `test_phase_b_failure_leaves_non_empty_sources_dir_on_rollback`,
  `test_phase_b_failure_does_not_remove_preexisting_sources_dir`). Added
  `test_write_exclusive_unlinks_partial_file_on_write_failure` (FIX 1,
  `tests/unit/test_fsio.py`, mirrors `copy_exclusive`'s existing
  partial-cleanup test) and, replacing the deleted rollback test,
  `test_phase_b_failure_surfaces_cleanly_and_leaves_detectable_orphan`
  (parametrized `concept`/`index`/`log`, `tests/unit/cli/test_ingest.py`):
  asserts exit 1, `openkos ingest:` + `failed` in stderr, no
  `Traceback` in stderr, the raw copy present (it always lands before any
  parametrized failing step), and — for `index`/`log` failures only — the
  concept document present as a detectable orphan (deliberately NOT
  byte-identical/snapshot-equal, per the retreat). Kept unchanged:
  happy-path, empty-slug, path-containment, collision, non-TTY-refuse,
  `--auto`, and the pre-existing permission-based
  `test_phase_b_write_failure_surfaces_cleanly`.

### TDD Cycle Evidence (Retreat)

| Fix | RED | GREEN | REFACTOR |
|---|---|---|---|
| FIX 1 | `test_write_exclusive_unlinks_partial_file_on_write_failure` written first; ran red (`assert not target.exists()` failed — partial file was left) against the unmodified `write_exclusive` | wrapped `f.write` in `try/except BaseException: path.unlink(missing_ok=True); raise`, all 10 `test_fsio.py` tests pass | docstring updated to document the cleanup guarantee |
| FIX 2/3 | `test_phase_b_failure_surfaces_cleanly_and_leaves_detectable_orphan` written first against the still-rollback-carrying code; ran red for all 3 params (`assert (tmp_path / "raw" / "notes.txt").is_file()` failed — rollback had already unlinked the raw copy) | removed the rollback block; all 19 `test_ingest.py` tests pass, including the new one | docstring + `import contextlib` cleanup |
| FIX 4/5 | N/A — docs/spec/design text only, no behavior change; verified by re-running the full suite after the edits | n/a | n/a |

### Files Changed (Retreat)

- `src/openkos/fsio.py` Mod — `write_exclusive` cleanup-on-failure (+8 lines net: 76 → 84 total).
- `src/openkos/cli/main.py` Mod — removed the entire rollback block (flags +
  nested try/except + restore/unlink/rmdir, ~25 lines), removed `import
  contextlib`, rewrote the `ingest` docstring's Phase B paragraph (honest,
  non-transactional wording).
- `tests/unit/test_fsio.py` Mod — +36 lines (1 new test, FIX 1).
- `tests/unit/cli/test_ingest.py` Mod (untracked/new on this branch — no
  prior commit exists to diff against) — module docstring + one test
  docstring reworded; 3 rollback tests deleted (~140 lines removed), 1 new
  parametrized test added (~45 lines, FIX 2/3), net smaller.
- `docs/cli.md` Mod — `ingest` section: replaced the all-or-nothing
  sentence with an honest non-transactional/git-recovery paragraph.
- `openspec/changes/add-ingest-command/design.md` Mod — D5 alternatives
  column + a fully rewritten "Known limit" section recording the retreat
  decision and rationale; sequence-diagram Phase B label; testing-strategy
  row wording.
- `openspec/changes/add-ingest-command/specs/ingestion/spec.md` Mod —
  Review/Confirm Flow requirement text; "All-or-nothing write on confirm"
  scenario replaced with "Phase B writes proceed on confirm" + a new
  "Phase B failure leaves a detectable, recoverable partial result"
  scenario.

### Verification Gate (Retreat)

- `uv run pytest --cov -q` → **164 passed**, coverage **100.00%** (gate
  ≥90%; branch coverage also 100%, `src/openkos/cli/main.py` at 112
  statements/18 branches, all covered — the rollback removal shrank the
  module, no coverage gap opened)
- `uv run ruff check .` → **All checks passed**
- `uv run ruff format --check .` → **20 files already formatted**
- `uv run mypy .` → **Success: no issues found in 20 source files**
- `uv build` → built `openkos-0.1.0.tar.gz` + `openkos-0.1.0-py3-none-any.whl`
  cleanly
- Wheel smoke test → installed the wheel into a scratch venv, ran `openkos
  init --model qwen3:8b` then `openkos ingest notes.txt --auto` against a
  real filesystem: `raw/notes.txt` and `bundle/sources/notes.md` created,
  `bundle/index.md`/`bundle/log.md` updated with the new entry — verified
  end-to-end against the installed package, not just `CliRunner`.

### Status (Retreat)

5/5 fixes complete, gate fully green (164 passed, 100% coverage, ruff/mypy
clean, build + wheel smoke passed). No commit made. Ready for the
orchestrator to re-validate/re-run the review receipt for PR 2 (the prior
receipt is invalidated by this code change), then `sdd-verify`.

## Verify CRITICAL + WARNING Closed — 2026-07-17

Branch: `feat/ingest-command`. Mode: Strict TDD. Closes the two findings
raised by `sdd-verify` against PR 2: 1 CRITICAL (missing spec-scenario
test), 1 WARNING (stale "all-or-nothing" doc drift after the D5 retreat).
No commit made.

- FIX 1 (CRITICAL) — added the missing test for the `ingestion` spec's
  Config Reader "No workspace config" scenario. Two characterization tests,
  both GREEN immediately (no production code change needed — the existing
  `read_config`/`ingest` error handling already satisfies the scenario):
  `tests/unit/test_config.py::test_read_config_raises_clear_error_when_config_missing`
  (`read_config` on a directory with no `openkos.yaml` raises `OSError`
  naming the missing file, writes nothing) and
  `tests/unit/cli/test_ingest.py::test_missing_config_refuses_via_ingest`
  (a workspace whose `openkos.yaml` was removed post-init, reached via
  `ingest --auto`, exits 1 with a clear `openkos ingest:` stderr message
  naming `openkos.yaml`, no traceback, snapshot-unchanged). One ruff fix
  needed: `match="openkos.yaml"` → `match=r"openkos\.yaml"` (RUF043, `.` is
  a regex metacharacter) — matches the existing convention already used
  elsewhere in `test_config.py`.
- FIX 2 (WARNING) — reconciled stale "all-or-nothing" phrasing in
  `proposal.md` (4 spots: Review/confirm-flow bullet, `ingest` command
  approach paragraph, Testing Expectations paragraph, Success Criteria
  checklist) and `tasks.md` (task 7.1) with the ratified D5 retreat
  (non-transactional, create-only/atomic per-file writes, git recovery for
  a partial result). Each spot reworded in place with a
  "superseded — see design D5" note; no other content changed.
  `apply-progress.md`'s own historical language left untouched per
  instruction (it is a correct chronological log of what was decided and
  retreated from, not a live contract).

### Files Changed (Verify Closure)

- `tests/unit/test_config.py` Mod — +21/-0 lines (1 new test).
- `tests/unit/cli/test_ingest.py` Mod — +31/-0 lines (1 new test).
- `openspec/changes/add-ingest-command/proposal.md` Mod — +23/-9 lines (4
  stale "all-or-nothing" spots reworded).
- `openspec/changes/add-ingest-command/tasks.md` Mod — +1/-1 line (task 7.1
  reworded).

### Verification Gate (Verify Closure)

- `uv run pytest --cov -q` → **166 passed**, coverage **100.00%** (line +
  branch; gate ≥90%)
- `uv run ruff check .` → **All checks passed**
- `uv run ruff format --check .` → **20 files already formatted**
- `uv run mypy .` → **Success: no issues found in 20 source files**

### Status (Verify Closure)

2/2 verify findings closed (1 CRITICAL, 1 WARNING). Gate fully green (166
passed, 100% coverage, ruff/mypy clean). No commit made. The two tracked
code SUGGESTIONs (decompose `ingest` body, dedup `_reject_newline`) were
intentionally left untouched, per scope. Ready for the orchestrator to
re-run/validate the review receipt, then archive.
