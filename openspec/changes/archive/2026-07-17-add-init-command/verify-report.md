# Verification Report: add-init-command (REOPENING — D5 revert)

**Change**: add-init-command | **Branch**: feat/init-command | **Mode**: Strict TDD | **Verified**: 2026-07-17

This report supersedes the prior verify-report for this change (which was
deleted during un-archiving and is fully re-derived here). It covers the
**reopened** state only: `openkos.yaml`'s `name` field is removed,
`write_config` collapses into a byte-copy shape identical to `write_agents`,
`_yaml_scalar` and the `ruamel.yaml` runtime import are deleted, `ruamel-yaml`
reverts to dev-only, and `model:` is unified to `qwen3:8b` across template,
docs, and examples. Prior verify claims about `test_write_config_generated_fields`,
`_yaml_scalar` tests, and `qwen3.5:9b` no longer apply — do not carry them
forward.

## Completeness

| Artifact | Status |
|---|---|
| Spec | Present, `specs/workspace-init/spec.md` (11 requirements, 18 scenarios) + Engram #790 |
| Design | Present, D1-D7 + reopening D5-revert narrative, `design.md` |
| Tasks | 33/33 checked (27 original + 6 reopening), `tasks.md` + Engram #794 |
| Apply progress | Present, Phases 5-8 complete, Engram #799 |

All tasks in `tasks.md` are checked `[x]`; no unchecked task blocks full
verification.

## Test / build evidence (re-run independently, not taken on faith)

- `uv run pytest --cov --cov-branch`: **50/50 passed**, **100.00% branch
  coverage** (122 stmts / 20 branches, 0 missed; gate is 90%). `config.py`:
  48 stmts / 10 branches, 100% covered.
- `uv run ruff check .`: **All checks passed!**
- `uv run ruff format --check .`: **17 files already formatted**
- `uv run mypy .`: **Success: no issues found in 17 source files**
- `uv build` (clean `dist/`): **Successfully built** `openkos-0.1.0.tar.gz`
  and `openkos-0.1.0-py3-none-any.whl`.
- Wheel smoke test 1: `uv run --isolated --no-project --with dist/openkos-*.whl
  openkos --help` → exit 0, `init` command listed. Ran on local venv Python
  3.13.13 — **no `--python 3.13` override was needed** (default interpreter
  already 3.13.13, not the anaconda 3.12 the instructions warned about). This
  covers only one interpreter; no CI matrix was run as part of this
  verification, so no CI parity is claimed.
- Wheel smoke test 2: same isolated wheel, `openkos init` run in a fresh
  `mktemp -d` → exit 0, all 5 artifacts present (`raw/`, `bundle/index.md`,
  `bundle/log.md`, `AGENTS.md`, `openkos.yaml`). `diff` between the
  freshly-written `openkos.yaml` and the packaged
  `src/openkos/templates/openkos.yaml.template` is **empty** — byte-identical,
  confirmed on disk by this verification run, not just by the unit test.
  Same empty-diff check performed for `AGENTS.md`.
- `uv sync --locked`: succeeds with no changes (`uv.lock` is in sync with
  `pyproject.toml`).

## Claims verified against actual code (not taken on faith)

1. **`write_config` is a pure byte copy.** Read `src/openkos/config.py`
   directly: `write_config(root)` calls `_read_template("openkos.yaml.template")`
   and writes it under `"x"` with `newline=""` — no `.format()`, no
   substitution, no parameter beyond `root`. `_yaml_scalar` does **not**
   exist anywhere in the file (confirmed absent). `rg -n "from ruamel|import
   ruamel" src -g '*.py'` returns **zero matches** — ruamel is genuinely gone
   from `src/`, not just "per the design."
2. **Template has no `name:` line, `model: qwen3:8b`.**
   `src/openkos/templates/openkos.yaml.template` read directly: line 1 is
   `model: qwen3:8b`; no `name:` line anywhere in the file.
3. **Test file matches the described shape.** `tests/unit/test_config.py`
   contains `test_write_config_byte_identical` (byte-identity test, present).
   `_yaml_scalar`-related tests (`test_yaml_scalar_handles_values_no_directory_can_hold`,
   `test_write_config_escapes_yaml_significant_characters_in_name`,
   `test_write_config_generated_fields`,
   `test_write_config_relative_root_uses_real_directory_name`) are **absent**
   — confirmed by full-file read, not grep-guessed. The `newline=""` spy test
   (`test_write_agents_and_write_config_open_with_newline_empty`) remains and
   covers both writers.
4. **`ruamel-yaml` is dev-only.** `pyproject.toml`: `[project].dependencies`
   spans lines 11-29 and does **not** contain `ruamel-yaml`; the only
   occurrence (`ruamel-yaml>=0.19.1`) is at line 36, inside
   `[dependency-groups]` (dev). `uv sync --locked` succeeds.
5. **No living doc/example contradicts the spec.**
   `rg -n "qwen3\.5:9b" --glob '!openspec/changes/**' .` → **zero matches**.
   `rg -n "^name:"` against `openkos.yaml`/template files outside
   `openspec/changes/**` → **zero matches**. `docs/cli.md` (lines 27, 50,
   100-101), `docs/user-journey.md` (lines 50, 55, 88), and
   `examples/good-life-demo/openkos.yaml` (2 lines, `model: qwen3:8b`, no
   `name:`) are all consistent with the spec. Historical `qwen3.5:9b`/`name:`
   text remains only inside `openspec/changes/add-init-command/{proposal,design,tasks}.md`
   as intentional archaeology (the design.md D5 note explicitly marks it as
   history, not current behavior) — correctly excluded from the "living doc"
   sweep.

## Requirement / Scenario Traceability (18 scenarios)

All test function names below were confirmed to exist via `rg -n "^def
test_"` across `tests/`; none are invented.

| # | Requirement | Scenario | Covering test | Real assertion? |
|---|---|---|---|---|
| 1 | Workspace Creation | Fresh empty directory | `tests/unit/cli/test_init.py::test_fresh_empty_directory` | Yes — CliRunner invoke, exit 0, all 5 paths asserted to exist |
| 2 | Workspace Creation | Success message names what was created | `tests/unit/cli/test_init.py::test_fresh_empty_directory` | **Partial** — asserts `stdout.strip() != ""` and `"openkos.yaml" in stdout`, but does not assert the other four artifact names appear. See WARNING-1 |
| 3 | Bundle Index Shape | Exact parsed frontmatter, empty body | `tests/unit/bundle/test_index.py::test_render_index_returns_version_frontmatter_and_empty_body` | Yes — parses `render_index()`, asserts `metadata == {"okf_version": "0.1"}`, `body == ""` |
| 4 | Bundle Log Shape | Initialization entry | `tests/unit/bundle/test_log.py::test_render_log_has_heading_dated_section_and_initialization_bullet` + `test_render_log_has_no_frontmatter` | Yes — exact heading, exact bullet text, no `---` frontmatter marker |
| 5 | Bundle Log Shape | Dated section reflects local date, not UTC | `tests/unit/cli/test_init.py::test_log_dated_section_uses_local_date_not_utc[utc_minus_12]` / `[utc_plus_14]` | Yes — parametrized over the two most extreme UTC offsets |
| 6 | Static openkos.yaml Template | Byte-identical template | `tests/unit/test_config.py::test_write_config_byte_identical` | Yes — compares `write_config()`'s output bytes against the packaged template's bytes read independently via `importlib.resources` |
| 7 | Static openkos.yaml Template | No directory-derived field, regardless of directory name | `tests/unit/test_config.py::test_write_config_ignores_directory_name` | Yes — runs `write_config` in a directory named `"a"*40 + "  " + "b"*40` (the exact shape that folded past ruamel's column and lost the space run when `name` was interpolated) and asserts byte-identity to the template. Added after verification closed WARNING-2; RED-confirmed by reintroducing `root.name` interpolation |
| 8 | Static AGENTS.md Template | Byte-identical template | `tests/unit/test_config.py::test_write_agents_byte_identical` | Yes |
| 9 | No Concept-Type Folders | Bundle holds only reserved files | `tests/unit/bundle/test_bundle.py::test_create_writes_exactly_index_and_log` | Indirect but real — tests `bundle.create()` directly, not through a full `openkos init` run. `cli/main.py` (read directly) shows `bundle.create()` is the only writer that touches `bundle_dir`, so the guarantee holds transitively |
| 10 | Refusal Idempotency | Existing openkos.yaml | `tests/unit/cli/test_init.py::test_refuses_when_openkos_yaml_exists` | Yes — exit 1, byte-snapshot of whole tree unchanged |
| 11 | Refusal Idempotency | Existing AGENTS.md | `tests/unit/cli/test_init.py::test_refuses_when_agents_md_exists` | Yes — same snapshot technique |
| 12 | Refusal Idempotency | Non-empty raw/ or bundle/ | `tests/unit/cli/test_init.py::test_refuses_when_dir_non_empty[raw]` / `[bundle]` | Yes |
| 13 | Refusal Idempotency | raw or bundle exists as a non-directory | `tests/unit/cli/test_init.py::test_refuses_when_dir_is_a_file[raw]` / `[bundle]` | Yes — exit 1, no uncaught exception, snapshot unchanged |
| 14 | Refusal Idempotency | Second run on an initialized workspace | `tests/unit/cli/test_init.py::test_refuses_on_second_run` | Yes |
| 15 | Refusal Idempotency | No partial output kept on refusal | Same 5 refusal tests above (each asserts `_snapshot(tmp_path) == before`) | Yes — covered across all refusal tests, not a separate test function |
| 16 | Write Failure Handling | Write failure surfaces a clean error | `tests/unit/cli/test_init.py::test_write_failure_surfaces_cleanly` | Yes (POSIX/non-root only, `skipif`-guarded — honestly scoped in the test's own docstring) |
| 17 | Adoption of Non-Workspace Directories | Adopt a folder of notes | `tests/unit/cli/test_init.py::test_adopt_non_workspace_directory` | Yes |
| 18 | Default raw/ Permissions | Default permissions | `tests/unit/cli/test_init.py::test_raw_default_permissions` | Yes — compared against a sibling directory's mode, not a hardcoded value |
| 19 | OKF Conformance | Mechanical check reports no violations on a fresh bundle | `tests/unit/cli/test_init.py::test_fresh_bundle_is_conformant` | Yes |
| 20 | OKF Conformance | Rule 3 holds by construction, not by mechanical check | *(intentionally no mechanical test — spec explicitly forbids one)* | N/A by design — rule 3 is enforced by construction via `tests/unit/bundle/test_index.py` and `test_log.py`'s shape assertions, and the spec explicitly states this slice must not claim a mechanical rule-3 check |

Not shown as a numbered spec scenario but also covered: `test_phase_a_read_failure_surfaces_cleanly` (Phase A read failure, POSIX/non-root only) — supports the Write Failure Handling requirement's spirit even though it is a pre-flight read failure, not a Phase-B write failure.

## Correctness / Design Coherence

| Design decision | Code matches? |
|---|---|
| D5 (reopened): `openkos.yaml` byte-identical copy, same shape as `write_agents`, no `ruamel.yaml` at runtime | **Yes** — verified directly in `config.py` and via `rg` for ruamel imports (zero) |
| D5 round-trip note: `_yaml_scalar` and ruamel import deleted | **Yes** — confirmed absent |
| Dependency revert: `ruamel-yaml` dev-only | **Yes** — confirmed in `pyproject.toml` |
| `model: qwen3:8b` unification across template/docs/examples | **Yes** — confirmed via repo-wide grep, zero `qwen3.5:9b` outside historical `openspec/changes/**` artifacts |
| Reopening ADR gate: no ADR needed (undoing a decision, no new tech/pattern) | Consistent — matches `docs/adr/README.md`'s "when in doubt, do not create one," and no new ADR file exists |

No design deviations found in this reopening slice.

## Issues

**CRITICAL**: None.

**WARNING**:
1. Scenario "Success message names what was created" (Workspace Creation) has
   a real covering test (`test_fresh_empty_directory`), but the test only
   asserts one artifact name (`"openkos.yaml"`) appears in stdout and that
   stdout is non-empty — it does not assert all five artifact names (`raw/`,
   `bundle/index.md`, `bundle/log.md`, `AGENTS.md`) are named, even though
   `cli/main.py`'s success message does include all five. Recommend
   strengthening the assertion in a follow-up, not blocking.
2. Scenario "No directory-derived field, regardless of directory name"
   (Static openkos.yaml Template) has no dedicated test using an edge-case
   directory name (long, or containing consecutive spaces) — the exact shape
   of the original corruption bug that motivated this reopening. The gap is
   low-risk because `write_config` structurally never reads any
   directory-derived value (confirmed by source inspection), making the
   scenario true by construction rather than by a targeted regression test.
   **RESOLVED after verification**: `test_write_config_ignores_directory_name`
   now exercises the exact original bug pattern (a name with a run of two
   spaces past the fold column) and was RED-confirmed by reintroducing
   `root.name` interpolation into `write_config`. The scenario no longer
   rests on "by construction" alone.

**SUGGESTION**: None beyond the two WARNINGs above.

## Final Verdict

**PASS WITH WARNINGS** — 0 CRITICAL, 2 WARNING, 0 SUGGESTION. All 33/33 tasks
are complete, all 4 gate commands (pytest, ruff check, ruff format, mypy) are
green with 100% branch coverage, `uv build` succeeds, both wheel smoke tests
pass with independently-reproduced byte-identical output, `ruamel.yaml` is
confirmed absent from runtime by direct grep, and every spec requirement maps
to real, existing, passing tests or an explicit by-construction justification
(OKF Conformance rule 3, explicitly deferred by the spec itself). The two
WARNINGs are test-strength gaps, not implementation defects — neither blocks
archive, but both are worth a follow-up before this code sees further churn.
