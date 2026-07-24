# Tasks: adjudicate --json (#137, Slice 2a)

Strict TDD: every task RED (failing test committed/observed first) then GREEN
(minimal implementation) before moving to the next task. All artifacts
English. Non-json path must stay byte-identical throughout.

## 1. Pure payload builder — `_adjudication_payload`

Satisfies: "Machine-Readable `--json` Output Mode", "`--same-only` Composes
With `--json`", "Empty State Emits Valid Empty Array", "Deterministic,
Pretty-Printed JSON" (ordering/structure part).

- [x] 1.1 RED — `tests/unit/cli/test_adjudicate.py`: add unit tests for
      `_adjudication_payload(results, same_only=...)` calling the function
      directly (no CliRunner):
      - empty `results` → `[]`
      - single SAME result → list with one dict, keys exactly
        `{member_ids, okf_type, tier, verdict, rationale}`, `tier` asserted
        UPPERCASE (`"HIGH"`/`"LOW"`), `"confidence"` NOT in dict
      - mixed verdicts (SAME/DIFFERENT/UNCERTAIN) → all fields per object
        assert exact values (no re-derivation), preserves `results` order
      - `same_only=True` → only `Verdict.SAME` entries retained
      - `member_ids` rendered as `list(...)` of the already-sorted tuple
      Run `uv run pytest tests/unit/cli/test_adjudicate.py -k payload` and
      confirm failure (function does not exist yet / ImportError).
- [x] 1.2 GREEN — `src/openkos/cli/main.py`: add `import json` to the stdlib
      import group (before `import re`); add
      `_adjudication_payload(results: Sequence[AdjudicatedCandidate], *, same_only: bool) -> list[dict]`
      per the design's locked field mapping (`group.tier.name`,
      `result.verdict.value.upper()`, no `confidence`, no survivor/absorbed).
      Run the same test subset until green.

Parallelizable: No — foundation for all following tasks.

## 2. `adjudicate --json` wiring (success path, full suppression)

Satisfies: "Machine-Readable `--json` Output Mode" (CLI integration),
"`--json` Fully Suppresses Human Output".

- [x] 2.1 RED — add CliRunner test(s): invoke `adjudicate --json` with
      `monkeypatch.setattr` faking `adjudicate_candidates` to return mixed
      verdicts; assert:
      - `result.exit_code == 0`
      - `json.loads(result.stdout)` parses cleanly and matches expected
        array shape/order
      - stdout does NOT contain: the tally substring (`"adjudicated "`),
        legend line, per-group detail lines, `"Next: openkos merge"`
      Run and confirm failure (flag/branch not implemented).
- [x] 2.2 GREEN — add `json_output: bool = typer.Option(False, "--json", help=...)`
      parameter to the `adjudicate` command; insert the short-circuit branch
      immediately after the Ollama error handlers (main.py:3743) and BEFORE
      the workspace echo (main.py:3745):
      `if json_output: typer.echo(json.dumps(_adjudication_payload(results, same_only=same_only), indent=2)); return`
      Update the command docstring to drop the stale "no `--json`" claim
      (main.py:3659). Run until green.

Parallelizable: No — depends on Task 1; sequential with Task 3-4 (same branch).

## 3. `--json --same-only` composability

Satisfies: "`--same-only` Composes With `--json`".

- [x] 3.1 RED — CliRunner test: `adjudicate --json --same-only` with mixed
      verdicts fixture; assert parsed array contains only
      `"verdict": "SAME"` objects. Confirm failure before wiring (or confirm
      it already passes once Task 2 lands — if so, mark this RED step as
      "verify existing coverage," not a new failure).
- [x] 3.2 GREEN — no new production code expected (composability already
      flows through `_adjudication_payload(same_only=same_only)` from Task
      1-2); if the test fails, fix the `same_only` wiring in the CLI branch.

Parallelizable: Yes, with Task 4 (independent test additions once Task 2 lands).

## 4. Empty state → `[]` under `--json` (both guards)

Satisfies: "Empty State Emits Valid Empty Array Under `--json`".

- [x] 4.1 RED — CliRunner tests:
      - no candidate groups at all (empty `find_candidates`/`results`) with
        `--json` → `json.loads(result.stdout) == []`, and stdout does NOT
        contain "No candidates found." (bypasses guard at main.py:3747-3749)
      - `--same-only` filters every result out, with `--json` → same
        assertion (bypasses guard at main.py:3754-3756)
      Confirm failure if the short-circuit branch does not yet precede both
      guards.
- [x] 4.2 GREEN — confirm branch placement from Task 2.2 precedes BOTH
      prose guards (main.py:3747-3749 and 3754-3756); adjust ordering if
      needed so `--json` always returns before either prose message.

Parallelizable: Yes, with Task 3.

## 5. Error path unaffected by `--json`

Satisfies: "Error Paths Unaffected By `--json`".

- [x] 5.1 RED — CliRunner test: simulate Ollama-unavailable (existing error
      fixture/monkeypatch pattern) with `--json` passed; assert:
      - `result.stderr` contains the existing unavailability message
      - `result.exit_code == 1`
      - `result.stdout` does not parse as JSON (or is empty) — no partial
        payload
      Confirm this fails only if `--json` handling were mistakenly placed
      before the error handlers (regression guard — may already pass given
      Task 2 placement; treat as verification RED if so).
- [x] 5.2 GREEN — no production change expected if Task 2.2 placement is
      correct (branch sits after all three Ollama handlers); fix ordering
      if the test reveals a regression.

Parallelizable: Yes, with Tasks 3-4.

## 6. Non-regression: human output byte-identical

Satisfies: "Non-JSON Output Stays Byte-Identical".

- [x] 6.1 Run the full pre-existing `tests/unit/cli/test_adjudicate.py`
      suite (all tests without `--json`) plus the full project test suite;
      confirm zero changes to existing assertions/output. No new test
      needed unless coverage gaps are found; if found, add a golden-output
      regression test asserting byte-for-byte stdout parity for a
      representative non-`--json` invocation.

Parallelizable: No — final confirmation step, depends on Tasks 1-5.

## 7. Quality gate

- [x] 7.1 `uv run pytest` — full suite green.
- [x] 7.2 `ruff check` — clean.
- [x] 7.3 `ruff format --check` — clean.
- [x] 7.4 `mypy` — clean (typed `Sequence[AdjudicatedCandidate]` return
      `list[dict]` on `_adjudication_payload`).

Parallelizable: No — final gate, depends on all prior tasks.

## Review Workload Forecast

- Estimated changed lines: ~120-180 (production: ~30-40 lines — `import json`,
  one `typer.Option`, one pure helper function, one short-circuit branch,
  one docstring edit; tests dominate: ~90-140 lines across 6 new test
  groups in `tests/unit/cli/test_adjudicate.py`).
- 800-line budget risk: Low. Single file touched in production
  (`src/openkos/cli/main.py`), single test file touched
  (`tests/unit/cli/test_adjudicate.py`). Well under the 800-line review
  budget even with generous test padding.
- Chained PRs recommended: No. Change is additive, atomic, and isolated to
  one command's flag; no natural seam justifies splitting into multiple PRs.
- Suggested split: None. Single PR covering payload builder + CLI wiring +
  full test suite is the natural unit — splitting would leave either the
  builder untested in isolation or the CLI flag without its test coverage
  in the same PR.
- Decision needed before apply: None. Design is fully locked (enum
  rendering, field set, branch placement, test seams all source-verified).
  Proceed directly to `sdd-apply`.

delivery_strategy: auto-forecast
review_budget: 800
Single PR expected: Yes
