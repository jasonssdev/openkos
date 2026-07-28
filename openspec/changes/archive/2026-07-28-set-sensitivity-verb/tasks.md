# Tasks: `set-sensitivity <concept-id> <level>`

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~470 (prod ~90, tests ~260, spec/ADR/docs ~120 — ADR ~90 of that already shipped) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (okf helper + unit tests) -> PR 2 (CLI verb + CLI/autocommit tests, depends on PR 1) -> PR 3 (specs + docs + ADR status flip) |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending — user must pick stacked-to-main or feature-branch-chain |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

Planning note: this `tasks.md` (and `design.md`) ship in a separate `chore(sdd):` branch/PR, never bundled into an implementation PR — a shared branch is what the review tool freezes against `main`, not the commit boundary.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `okf.sensitivity_direction` helper, private `_rank` untouched | PR 1 | `uv run pytest tests/unit/model/test_okf_sensitivity.py -q` | N/A — pure function, no CLI/workspace side effect to exercise | `src/openkos/model/okf.py` (new function only) + its test file, revertable alone; nothing else imports it yet |
| 2 | `set_sensitivity_cmd` CLI verb + its dedicated test file + the shared autocommit case | PR 2 (base: PR 1) | `uv run pytest tests/unit/cli/test_set_sensitivity.py tests/unit/cli/test_main_autocommit.py -q` | `openkos set-sensitivity <id> public` against a scratch workspace, interactive and `--auto` | `set_sensitivity_cmd` registration in `main.py` + `tests/unit/cli/test_set_sensitivity.py`, revertable without touching PR 1's helper |
| 3 | Specs (`sensitivity-config` new, `workspace-autocommit` delta), `docs/cli.md` section, ADR status flip to `Accepted` | PR 3 (base: PR 2) | N/A — docs/spec only, no test command | N/A — no runtime behavior change | The three doc/spec files, revertable independently; code behavior in PR 1/2 unaffected |

## Phase 1: `okf.sensitivity_direction` helper (PR 1)

- [ ] 1.1 RED — `tests/unit/model/test_okf_sensitivity.py`: `sensitivity_direction` returns `"raise"`/`"same"`/`"lower"` for all three `SENSITIVITY_ORDER` pairs, spec req "Lowering Requires Explicit Permission..." classification base
- [ ] 1.2 RED — same file: dirty current (`None`, `""`, `"  "`, `7`, unrecognized string) each rank fail-closed via existing `_rank`, target `public` classifies as `"lower"` — pins design test-row 3, ADR-0008
- [ ] 1.3 RED — same file: non-string / unrecognized target still resolves through `SENSITIVITY_ORDER.index`, confirming `_rank` stays the only fail-closed policy point
- [ ] 1.4 GREEN — add `sensitivity_direction(current: object, target: str) -> Literal["raise", "same", "lower"]` to `src/openkos/model/okf.py` beside `combine_sensitivity`, body `_rank(target)` vs `_rank(current)`; `_rank` stays private
- [ ] 1.5 Confirm branch coverage on the three-way comparison (raise/same/lower) satisfies the `--strict` mypy and `ruff` gates; run `uv run pytest tests/unit/model/ -q --cov`

## Phase 2: `set_sensitivity_cmd` CLI verb (PR 2, base PR 1)

- [ ] 2.1 RED — `tests/unit/cli/test_set_sensitivity.py`: copy fixtures (`_init_workspace`, `_ingest_source`, `_simulate_tty`, `_snapshot`) from `tests/unit/cli/test_relate.py`; add `_sensitivity_of` analog
- [ ] 2.2 RED — invalid `<level>` refused before any read/write, exit non-zero, `_snapshot` unchanged (spec "Strict Level Validation")
- [ ] 2.3 RED — bad `<concept-id>` (absolute, `..`, reserved, missing) refused, one parametrized case (spec "Concept-Id Resolution And Refusals", design test-row 10)
- [ ] 2.4 RED — idempotent exact-equal current == level: no-op message, exit 0, no write, no commit, `_snapshot` unchanged (spec "Idempotent No-Op")
- [ ] 2.5 RED — raise under `--auto`, no flag: writes, one commit (spec "Lowering Requires Explicit Permission...", raise arm)
- [ ] 2.6 RED — **load-bearing**: lowering under workspace `review: false`, no `--auto`, no `--allow-downgrade`: refuses exit 1, message names the flag, bundle byte-identical (spec scenario "Lowering under `review: false` without the flag is refused"; pins the security decision this change exists for)
- [ ] 2.7 RED — **load-bearing**: dirty current `sensitivity` (missing/blank/malformed) + target `public` under `--auto`, no flag: classified as lowering, refused exit 1, nothing written (spec scenario "A dirty current value ranks fail-closed for lowering purposes")
- [ ] 2.8 RED — lowering under `--auto` without the flag (clean current value): refused exit 1 (spec scenario "Lowering under `--auto` without the flag is refused")
- [ ] 2.9 RED — lowering under `--auto --allow-downgrade`: writes, one commit (spec scenario "Lowering under `--auto` with the flag succeeds")
- [ ] 2.10 RED — lowering on a TTY, confirm accepted, no flag: writes (spec scenario "Interactive lowering with accepted confirm needs no extra flag")
- [ ] 2.11 RED — declined TTY confirm: no write, `_snapshot` unchanged (spec "Preview And Confirm Gate", decline scenario)
- [ ] 2.12 RED — setting `confidential` emits the existing one-time transparency NOTICE, no new notice code added in the verb (spec cross-reference, design test-row 13)
- [ ] 2.13 RED — success message and `--help` both contain the honesty line ("no sibling and no derived object is updated..."), one assertion each (spec "Scope Is Exactly One Named Concept")
- [ ] 2.14 RED — commit message `openkos: set-sensitivity <id> -> <level>` and staged paths `[bundle/{id}.md, bundle/log.md]` exact, no `index.md` change (spec "Auto-Commit On Successful Write")
- [ ] 2.15 RED — `tests/unit/cli/test_main_autocommit.py`: add the shared-contract case for `set-sensitivity` (one commit, correct paths) alongside the existing per-verb cases
- [ ] 2.16 GREEN — implement `set_sensitivity_cmd` in `src/openkos/cli/main.py`, placed between `relate` and `set_volatility_cmd`, mirroring `relate`'s Phase A/Phase B shape: level-vocab check -> `require_workspace`/`read_config` -> `_resolve_concept_path` -> `load_frontmatter` -> idempotence short-circuit -> `okf.sensitivity_direction` -> downgrade gate (`direction == "lower" and not prompt_will_run and not allow_downgrade`) -> preview (direction word + `current!r`) -> confirm gate -> `dump_frontmatter` + `insert_log_entry` -> `write_atomic` x2 -> `_autocommit`; use the literal strings from design.md's Interfaces table verbatim (help, idempotent, downgrade refusal, success, log line, commit, error ladder)
- [ ] 2.17 REFACTOR — confirm no dead branches, `ruff check`/`ruff format --check`/`mypy --strict` clean on `main.py` and the new test file
- [ ] 2.18 Run `uv run pytest tests/unit/cli/test_set_sensitivity.py tests/unit/cli/test_main_autocommit.py -q --cov` and confirm both arms covered for: `direction == "lower"`, `prompt_will_run`, `allow_downgrade`, idempotence, `auto`/`cfg.review`/`isatty`, and the preview direction word

## Phase 3: Specs, docs, ADR (PR 3, base PR 2)

- [ ] 3.1 Confirm `openspec/specs/sensitivity-config/spec.md` matches shipped behavior (already drafted in this change folder; move/apply to `openspec/specs/` at archive per repo convention)
- [ ] 3.2 Confirm `openspec/changes/set-sensitivity-verb/specs/workspace-autocommit/spec.md` delta (enumeration + paths clause + message table + `set-sensitivity` scenario) matches shipped commit message and staged paths from task 2.16
- [ ] 3.3 Add `### openkos set-sensitivity <id> <level>` section to `docs/cli.md`, modeled on the `set-volatility` entry, stating the concept-only-touch honesty line and the `--allow-downgrade` flag
- [ ] 3.4 Flip `docs/adr/0008-human-sensitivity-override.md` status from `Proposed` to `Accepted` (frontmatter `status` + body `**Status:**` line) — do this only at archive time, per AGENTS.md's append-only-but-status-flips-on-acceptance convention; `docs/adr/README.md` row already present, no further edit needed

## Rules Carried Forward (do not re-derive at apply time)

- Public helper signature: `okf.sensitivity_direction(current: object, target: str) -> Literal["raise", "same", "lower"]`; `okf._rank` stays private.
- Flag: `--allow-downgrade`.
- Gate order is step 7 of 10 (see design.md Data Flow); a refused unattended downgrade prints no preview.
- Idempotence is exact `==`, no `.strip()`.
- Preview renders `current` with `!r`; direction words are `raise`->`raising`, `lower`->`lowering`, `same`->`normalizing`.
- All literal user-facing strings come from design.md's Interfaces table — do not restate or paraphrase them in code or tests.
