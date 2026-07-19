# Tasks: Align user-journey doc with MVP-1 reality + cover init next-step hint

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~110-170 (docs/user-journey.md ~90-140; test_init.py ~15-25; main.py 0) |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | single-pr |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Add exact-string TTY coverage test for `init` next-step hint | PR 1 | `uv run pytest tests/unit/cli/test_init.py -k exact_next_step_hint` | N/A — pure unit test via Typer CliRunner, no external process | `tests/unit/cli/test_init.py` new test function; delete to revert |
| 2 | Reframe `docs/user-journey.md` to MVP-1 reality with later-MVP fencing | PR 1 | N/A (docs only) — verified by grep sweep + manual read | N/A — documentation change, no runtime | `docs/user-journey.md`; revert file to prior commit |

## Phase 0: Verify Ground Truth (blocks Item B)

- [x] 0.1 Read `src/openkos/cli/main.py` `ingest()` (around line 129 and the `--auto` success path) and capture the VERBATIM success line text to use in doc spot 7 (Step 3, `--auto`).
- [x] 0.2 Confirm `src/openkos/cli/main.py` contains no git-commit call and no content-hash reconciliation logic; note this to ground doc spots 8 and 10.

## Phase 1: Item A — TTY exact-string hint test (RED n/a — pre-existing behavior)

- [x] 1.1 In `tests/unit/cli/test_init.py`, add `test_tty_init_prints_exact_next_step_hint`: `monkeypatch.chdir(tmp_path)` + `_simulate_tty(monkeypatch)` + `runner.invoke(app, ["init"], input="\n")`.
- [x] 1.2 Assert `result.exit_code == 0` and the full verbatim line: `"Next: run \`openkos ingest <path>\` to import your first source." in result.output` (not a substring match).
- [x] 1.3 Add a one-line docstring noting this test guards pre-existing `main.py:129` wording under a real TTY; confirm no edits to `src/openkos/cli/main.py`.
- [x] 1.4 Run `uv run pytest tests/unit/cli/test_init.py -k exact_next_step_hint -v` — expect GREEN on write (pre-existing behavior, not new).

## Phase 2: Item B — coordinated reframe pass over docs/user-journey.md

- [x] 2.1 Spot 1 (loop diagram, lines 23-30): keep loop as vision; add **In MVP 1:** note — `compile` = verbatim embed into a single `Source` concept, no LLM synthesis.
- [x] 2.2 Spot 2 (Step 1 command, line 69): drop `--sensitivity confidential` from the sample; add **In MVP 1:** sensitivity comes from `openkos.yaml` `default_sensitivity`.
- [x] 2.3 Spot 3 (batch/glob ingest, line 74): mark **Later MVPs:** batch/glob; state MVP-1 is one-source-at-a-time.
- [x] 2.4 Spot 4 (`--sensitivity` at capture, line 75): **In MVP 1:** set via `openkos.yaml`; per-source flag is **Later MVPs**.
- [x] 2.5 Spot 5 (Step 2 Compile, lines 77-79): rewrite to null-compiler — copy source, embed verbatim as one `Source` concept, update `index.md`/`log.md`; fence LLM/multi-concept narrative as **Later MVPs**.
- [x] 2.6 Spot 6 (Step 3 review panel, lines 82-113): rewrite sample panel to flat `+`/`~` list of the single Source concept + index/log, plain yes/no confirm; fence `[e]dit` + high-water-mark reclassification as **Later MVPs**.
- [x] 2.7 Spot 7 (`--auto` success line, line 110): replace with the VERBATIM MVP-1 success string captured in task 0.1 (no invented text).
- [x] 2.8 Spot 8 (Step 4 Commit, lines 115-117): **In MVP 1:** changes written to disk, git commit manual/optional; auto-commit is **Later MVPs**.
- [x] 2.9 Spot 9 (Step 5 query citations, lines 119-141): point citation chain to Source concept → `raw/` original, not `concepts/stoicism.md`; fence corrected-understanding synthesis as **Later MVPs**.
- [x] 2.10 Spot 10 (hand-edit reconciliation, lines 150-158): keep "files are yours, editable anywhere"; fence content-hash reconciliation / external-edit logging / merge-on-ingest as **Later MVPs**.
- [x] 2.11 Spot 11 ("text only", line 191): reword as MVP-1 intent — any file accepted and copied to `raw/`, text embeds UTF-8, non-text gets binary-fallback note; no enforced `.txt/.md` allowlist; format producers are **Later MVPs**.
- [x] 2.12 Cross-check the fully edited doc for internal consistency (no section contradicts another on MVP-1 vs later-MVP); confirm lines 17, 146, 180, and the `--auto` two-ways table remain unchanged.

## Phase 3: Verification

- [x] 3.1 Run `uv run pytest` (full suite) and confirm the 90% coverage gate passes. Result: 439 passed; `uv run pytest --cov` → 98.90% coverage (gate 90%).
- [x] 3.2 Grep sweep `docs/user-journey.md` for residual overclaims: `--sensitivity`, `[e]dit`, `concepts/stoicism`, `committed to git`, glob/batch phrasing, `v1→v2`, `.txt` allowlist wording — each hit must be either absent or explicitly fenced as **Later MVPs**. Result: clean — every remaining hit is fenced or an explicit negation; "committed to git" has zero occurrences.
- [x] 3.3 Confirm `git diff` shows zero changes to `src/openkos/cli/main.py`. Result: confirmed, `git diff --stat -- src/openkos/cli/main.py` is empty.

## Next Step
All tasks complete. Ready for `sdd-verify`.
