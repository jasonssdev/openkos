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

### Test Summary

- **Total tests**: full suite 166 passed (committed across all batches + hardening + retreat + verify closure)
- **Coverage**: 100% line and branch (gate ≥90%)
- **Quality gates**: ruff/mypy/build all clean

### Status

19/19 tasks complete. PR #11 (primitives) merged to main at 0409bc9. PR #12 (`ingest` CLI) merged to main per current main branch head. All PRs merged, issue #10 closed, both PRs approved and delivered.
