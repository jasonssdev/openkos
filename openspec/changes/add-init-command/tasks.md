# Tasks: `openkos init` — create an OKF workspace

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~830 (130 + 270 + 225 + 205) |
| 400-line budget risk | High (whole change); Low per slice (max ~270) |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 → PR 2 → PR 3 → PR 4 (4-way, revised from proposal's 3-way) |
| Delivery strategy | ask-on-risk |
| Chain strategy | stacked-to-main (decided) |

Decision needed before apply: Resolved — 4-PR stacked-to-main chain
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

**Split revision, justified**: the proposal's PR 2 lumped `config.py` (workspace layer) with `model/okf.py` + `bundle/*` (format layer) at ~300 lines, and PR 3 grew to ~280 for `init` wiring + templates. Combined, `config.py`'s `write_config`/`write_agents` need the templates to test byte-identity and generated fields (scenarios 4, 5) — testing them without templates is hollow. Splitting format (PR 2) from workspace-config+templates (PR 3) from CLI wiring (PR 4) follows the design's own module boundaries, keeps every slice self-contained and fully tested, and keeps every slice comfortably under 400 (max ~270). Residual YAGNI: `config.py` has no CLI caller until PR 4, but it is behavior-complete and tested — a materially smaller cost than shipping it untestable. A 3-way merge of PR 3+4 lands at ~428, itself over budget — reject that alternative.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Console entry → `openkos.cli.main:app`, bare Typer app | PR 1 | `uv run pytest tests/unit/test_main.py` | `uv run --isolated --no-project --with dist/openkos-*.whl openkos --help` | `git revert` restores stub `main()` (D6) |
| 2 | Format layer: `model/okf.py`, `bundle/{index,log,bundle}.py` | PR 2 | `uv run pytest tests/unit/model tests/unit/bundle` | N/A — no CLI caller yet, pure/fs-only modules | delete 4 files + tests, nothing imports them |
| 3 | Workspace layer: `config.py` + `templates/` | PR 3 | `uv run pytest tests/unit/test_config.py` | N/A — no CLI caller yet | delete `config.py`, tests, `templates/` |
| 4 | `init` command wiring + packaging proof + docs | PR 4 | `uv run pytest tests/unit/cli` | `openkos init` in `mktemp -d` against isolated wheel (CI) | `git revert`; additive only |

## Phase 1: Console-entry migration (PR 1, ~130)

- [x] 1.1 RED `tests/unit/test_main.py`: drop greeting test; entry-point test asserts `is openkos.cli.main.app`; add `test_app_help_exits_zero` (CliRunner)
- [x] 1.2 GREEN: create `src/openkos/cli/main.py` (bare `app = typer.Typer()`, `__init__.py`); delete `main()` from `src/openkos/__init__.py` (D6); `pyproject.toml:20` → `openkos.cli.main:app`; move `typer` dev→`dependencies`; `uv lock`
- [x] 1.3 GREEN: `.github/workflows/ci.yml:116` → `openkos --help` against isolated wheel; fix stale py.typed comment (ci.yml:97-98)
- [x] 1.4 Verify: `uv run pytest`, ruff, mypy green

## Phase 2: OKF format layer (PR 2, ~270)

- [x] 2.1 RED `tests/unit/model/test_okf.py`: `OKF_VERSION == "0.1"`, reserved filenames, frontmatter round-trip parses to `{"okf_version": "0.1"}`, §9 rules 1-2 pass/fail cases
- [x] 2.2 GREEN: `src/openkos/model/okf.py`; move `python-frontmatter` dev→runtime
- [x] 2.3 RED `tests/unit/bundle/test_index.py`: `render_index()` returns frontmatter + empty body (scenario 2)
- [x] 2.4 GREEN: `src/openkos/bundle/index.py::render_index() -> str`
- [x] 2.5 RED `tests/unit/bundle/test_log.py`: `render_log(today: date)` — heading, `## YYYY-MM-DD`, exact Initialization bullet, no frontmatter (scenario 3)
- [x] 2.6 GREEN: `src/openkos/bundle/log.py::render_log(today: date) -> str`
- [x] 2.7 RED `tests/unit/bundle/test_bundle.py`: `create(bundle_dir, today)` writes both files mode `"x"` under `tmp_path`; raises `FileExistsError` on collision (D2); bundle holds exactly two files (scenario 6)
- [x] 2.8 GREEN: `src/openkos/bundle/bundle.py::create()`

## Phase 3: Workspace config + templates (PR 3, ~225)

- [ ] 3.1 RED `tests/unit/test_config.py`: `is_workspace(root)` true/false over the 4 conditions (`openkos.yaml`, `AGENTS.md`, non-empty `raw/`, non-empty `bundle/`)
- [ ] 3.2 GREEN: `src/openkos/config.py::WorkspaceLayout`, `is_workspace()`
- [ ] 3.3 Add `src/openkos/templates/agents.md.template` (byte-identical target: `examples/good-life-demo/AGENTS.md`), `templates/openkos.yaml.template` (placeholder `{name}`, fixed `model: qwen3.5:9b`, D5); no `__init__.py`
- [ ] 3.4 RED: `test_write_agents_byte_identical`, `test_write_config_generated_fields` — read via `importlib.resources.files("openkos")/"templates"`, write mode `"x"` (scenarios 4, 5)
- [ ] 3.5 GREEN: `config.py::write_config()`, `write_agents()`

## Phase 4: `init` wiring, packaging proof, docs (PR 4, ~205)

- [ ] 4.1 RED `tests/unit/cli/test_init.py`: 4 refusal tests (existing `openkos.yaml`, existing `AGENTS.md`, non-empty `raw/`/`bundle/`, second run) — `CliRunner` + `monkeypatch.chdir`, exit 1, zero writes (scenarios 7-11)
- [ ] 4.2 GREEN: pre-flight (Phase A, D1) evaluating all 4 conditions before any write
- [ ] 4.3 RED: `test_fresh_empty_directory`, `test_adopt_non_workspace_directory` — exit 0, 5 artifacts exist (scenarios 1, 12)
- [ ] 4.4 GREEN: `init` sequencing (Phase B, D1): `bundle.create()` → `write_agents()` → `write_config()` last (D3, marker last)
- [ ] 4.5 RED: `test_raw_default_permissions` — `raw/` mode matches a fresh dir default (scenario 13)
- [ ] 4.6 RED: `test_fresh_bundle_is_conformant` — run `init`, call `okf` §9 check against produced `bundle/` (scenario 14)
- [ ] 4.7 Add CI build-job step: `mktemp -d`, run `openkos init` against isolated wheel, assert exit 0 + 5 artifacts (the only proof `templates/` ships)
- [ ] 4.8 If 4.7 fails: add `[tool.uv.build-backend]` include rule for `templates/` (fallback, not a redesign)
- [ ] 4.9 Docs: `AGENTS.md:64` ingest→init; `docs/cli.md:48` mark model-pick/concept-folder claims as honest gap; `docs/cli.md:99` `qwen3:8b`→`qwen3.5:9b`
- [ ] 4.10 Final verify: `uv run pytest --cov` (≥90% branch), ruff, mypy, `uv build`
