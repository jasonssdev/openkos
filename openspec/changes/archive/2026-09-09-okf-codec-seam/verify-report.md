```yaml
schema: gentle-ai.verify-result/v1
# `bb1edaf` was the pre-rebase commit and no longer exists; this is the
# git commit the verification actually ran against, after the rebase onto
# main @ 17d2143. It is a git object id (SHA-1, 40 hex), NOT a sha256 --
# the earlier `sha256:` label on this value was simply wrong.
verified_against_commit: f46a713
verdict: pass
blockers: 0
critical_findings: 0
requirements: 0/0
scenarios: 0/0
test_command: uv run pytest
test_exit_code: 0
test_output_hash: sha256:a2d1eee42b082ea0529997780431f3498ff94536ada578bd593c97e793b55a4b
build_command: uv run mypy .
build_exit_code: 0
build_output_hash: sha256:242527e75d46a64dcab74afaf0afd8ced039074dc7768f5fe86beac84fc6b29e
```

## Verification Report

**Change**: okf-codec-seam
**Version**: N/A (deliberate no-delta-spec decision, `specs/README.md`)
**Mode**: Strict TDD

### Completeness
| Metric | Value |
|--------|-------|
| Tasks total | 14 |
| Tasks complete | 14 |
| Tasks incomplete | 0 |

### Build & Tests Execution
**Build**: PASS — `uv run mypy .` → `Success: no issues found in 297 source files`, exit 0.

**Tests**: PASS — `uv run pytest` (full suite, unpiped, background) → `6104 passed, 3 skipped in 305.73s (0:05:05)`, exit 0. Matches apply-progress's reported WU2 count exactly.

**Lint**: PASS — `uv run ruff check .` → `All checks passed!`. `uv run ruff format --check .` → `297 files already formatted`.

**Self-tests**: PASS — `uv run python evals/run_self_tests.py` → `42 of 42 harness self-test(s) run, 0 failing.`

**`git status --porcelain`**: clean (empty) at report time, after my own falsification cycle was fully reverted.

### Goal Verification (against #919 / proposal intent, not just the task checklist)

The proposal's own reshape of #919 is the goal: consolidate the ONE measured
duplicate (`_split_frontmatter_verbatim` + `_FRONTMATTER_RE`), not the four
files #919's title names — `log.py` was correctly identified as not a
duplicate and receives no code change (design D5, note only). Each of the
six specific claims below was checked against shipped code and, where
possible, re-derived independently rather than taken from apply-progress.

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 1 | Exactly one frontmatter-split implementation repo-wide | **PASS** | `grep -rn "_FRONTMATTER_RE" src/` → 2 hits, both `src/openkos/model/okf.py` (definition line 450, use line 468). Zero hits in `index.py`/`source_titles.py`. Both wrappers (`index.py:21`, `source_titles.py:147`) are one-line delegations to `okf.split_frontmatter_verbatim(text, label=_FRONTMATTER_LABEL)`. |
| 2 | Both error messages survived byte-for-byte, asserted in full including prefix | **PASS** | `okf.py:470` raises `f"{label}: missing or malformed frontmatter block"`. Full-string assertions exist in 3 places: `test_frontmatter_split_parity.py:86-89` (both `"index.md: ..."` and `"Source document: ..."` via parametrization), `test_source_titles.py:207` (`re.escape("Source document: missing or malformed frontmatter block")`, widened per task 1.4), and `test_okf.py:73` (direct-call message-carries-label test). Neither message was asserted as a full string before this change per the proposal's own risk table — now both are. |
| 3 | Ordering constraint held — WU1 touched zero `src/` files | **PASS** | `git diff-tree --no-commit-id --name-only -r 2a5cc1c` → 5 `openspec/` files + `tests/unit/bundle/test_frontmatter_split_parity.py`, `tests/unit/bundle/test_source_titles.py`, `tests/unit/model/fixtures/okf_framing_goldens.json`, `tests/unit/model/test_okf_framing_characterization.py`. Zero `src/` paths. `git show --stat 2a5cc1c` confirms the same 9-file, 821(+)/2(-) shape. |
| 4 | Golden generated from the pre-move tree | **PASS** | The golden fixture (`okf_framing_goldens.json`) and its characterization test were committed in `2a5cc1c` (WU1), which per claim 3 contains zero `src/` changes — so it was necessarily generated against the pre-move two-copy code. The test file's own docstring states this explicitly ("generated from `bundle.index._split_frontmatter_verbatim` on the pre-move tree"). Both `test_recorded_framing_matches_golden` and `test_recorded_framing_satisfies_the_concatenation_invariant` pass today (post-move), proving the recorded bytes match current behavior — the invariant a golden generated post-move could not prove. |
| 5 | Stale docstring removed | **PASS** | `grep -rn "deliberate separate copy"` and `grep -rn "cross-module private coupling"` across the tree (`.py` files) both return zero matches. Read of `source_titles.py:120-157` (current tree) confirms the paragraph is gone; the wrapper's docstring now correctly describes delegation to `model.okf.split_frontmatter_verbatim` (design D2/D3). |
| 6 | Keyword-only, no-default `label` | **PASS**, one SUGGESTION-level gap noted | Source confirms `def split_frontmatter_verbatim(text: str, *, label: str) -> tuple[str, str]:` (`okf.py:453`) — no default. Independently re-verified: (a) `uv run mypy` on a throwaway script calling `okf.split_frontmatter_verbatim("text")` (omission, no `label` at all) → `error: Missing named argument "label" for "split_frontmatter_verbatim"  [call-arg]`, confirming design D1's static-catch claim for the omission row; (b) the same omitted call at runtime → `TypeError: split_frontmatter_verbatim() missing 1 required keyword-only argument: 'label'`. The one existing pytest test (`test_split_frontmatter_verbatim_requires_label_as_keyword_only`) pins the *positional-slide* variant (`split_frontmatter_verbatim(text, "bad-call")`, 2 positional args) rather than pure omission — both are TypeErrors caused by the same keyword-only mechanism, but no pytest test exercises the omission call shape specifically. Not a defect (mypy strict, CI-gated, already gates the omission row per design's own stated mechanism), but a test-only asymmetry worth noting. |

### Falsification — independently re-derived, not taken on trust

Re-ran the described falsification myself, on the current (post-move) tree,
using an inverse-string-replace mutate/revert cycle with `__pycache__`
purged before and after:

1. Mutated `src/openkos/model/okf.py:450` to drop `re.DOTALL` from
   `_FRONTMATTER_RE` (the now-single, consolidated regex).
2. `find . -name __pycache__ -type d -prune -exec rm -rf {} +`
3. `uv run pytest tests/unit/bundle/test_frontmatter_split_parity.py tests/unit/model/test_okf_framing_characterization.py tests/unit/bundle/test_source_titles.py tests/unit/model/test_okf.py`
   → **RED: 16 failed, 264 passed**. The two directly-targeted tests failed
   exactly as apply-progress predicted:
   `test_both_copies_split_identically[multiline_frontmatter]` and
   `test_recorded_framing_matches_golden[multiline_frontmatter]`
   (`AssertionError` — block/body no longer match the recorded/expected
   values because the multi-line frontmatter body can no longer be bridged
   without `DOTALL`). The other 14 failures are collateral in
   `test_source_titles.py`'s retitle/backfill suite (e.g.
   `test_retitle_document_patches_only_two_lines...`,
   `test_resolve_source_title_backfill_orders_and_carries_scan_entries`),
   because post-move both bundle modules now share the one mutated regex —
   a **stronger** falsification signal than the pre-move WU1 transcript
   (apply reported 2/70 on the two-copy tree; I observed 16/280 on the
   post-move tree, which is expected and correct: the consolidation means
   one regression now blasts through both call sites instead of one).
4. Reverted with the exact inverse string replace (added `, re.DOTALL`
   back). Purged `__pycache__` again.
5. Reran the same four test files → **GREEN: 280 passed**. `git status --porcelain` and `git diff --stat` both empty — the revert left zero residual diff.

The apply agent's reported vacuous-verdict finding for the original 7-scenario
corpus (zero failures without `re.DOTALL`, because no scenario had embedded
`\n` inside the frontmatter block) is corroborated: the 8th
`multiline_frontmatter` scenario is the only one of the 8 whose block content
spans multiple lines (`okf_version`, `author`, `tags` each on their own
line), which is exactly what makes it the only scenario capable of
distinguishing `DOTALL` from its absence. Removing that scenario from the
corpus (mentally) reproduces the original vacuous-verdict bug.

### Corpus blind-spot review (a finding, not a failure)

Checked the 8-scenario corpus (`crlf_body`, `quoted_okf_version`,
`triple_dash_in_body`, `no_trailing_newline`, `empty_body`, `non_ascii`,
`multiline_frontmatter`, plus the absent-block refusal case) against the
four framing shapes named in the verify task:

| Shape | Covered? | Notes |
|---|---|---|
| CRLF | **Partial** | `crlf_body` covers CRLF *inside the body* only. CRLF *in the frontmatter delimiters themselves* (`---\r\n...\r\n---\r\n`) is exercised only in `test_source_titles.py`'s `test_retitle_document_normalizes_a_crlf_first_line`-adjacent CRLF-refusal test, on the `source_titles` call path alone — not in the cross-module parity/golden corpus, and not for `index.py`'s copy. |
| No trailing newline after the closing `---` | **Not covered** | `no_trailing_newline` tests a missing trailing newline on the *body*, not on the closing delimiter line. Independently verified: `_FRONTMATTER_RE.match("---\nokf_version: 0.1\n---")` (no newline after the second `---`) returns `None` — the whole document is **refused**, not split. This is plausibly the correct/safe (fail-closed) behavior, but it is a genuinely untested framing shape in both the parity and golden corpora. |
| An empty body | **Covered** | `empty_body` scenario, both files. |
| A `---` inside the body | **Covered** | `triple_dash_in_body` scenario, both files. |

Neither gap changes the PASS verdict on claims 1-6 above — the shipped
behavior for both gaps is refuse-closed, and no test currently claims
otherwise — but they are real, unexercised edges in a fixture whose stated
purpose is byte-identity protection at this exact boundary.

### Issues Found

**CRITICAL**: None.

**WARNING**:
1. The 8-scenario parity/golden corpus does not cover "no trailing newline immediately after the closing `---` delimiter" (as distinct from "no trailing newline on the body," which is covered) or cross-module CRLF-in-delimiters framing. Both are refuse-closed today; recommend adding scenarios (or an explicit "why not" note) before the next time this boundary is touched.

**SUGGESTION**:
1. `test_split_frontmatter_verbatim_requires_label_as_keyword_only` exercises the positional-slide TypeError (2 positional args), not the pure-omission TypeError (`split_frontmatter_verbatim(text)` alone). Both are caught statically by `mypy .` today, so this is not a coverage gap in the sense the design worries about, but a dedicated omission-case test would make the pytest suite self-sufficient without relying on mypy also being run.

### Correctness (Static Evidence)
| Requirement | Status | Notes |
|------------|--------|-------|
| Single `_FRONTMATTER_RE` repo-wide | ✅ Implemented | `okf.py:450` only |
| `split_frontmatter_verbatim(text, *, label)`, no default | ✅ Implemented | `okf.py:453` |
| Both wrappers delegate, 6 call sites unchanged | ✅ Implemented | `index.py:21-29`, `source_titles.py:147-156` |
| Stale docstring paragraph deleted | ✅ Implemented | zero repo hits for either phrase |
| `_patch_title_line` and its regex/width constants untouched | ✅ Implemented | `source_titles.py:159+` unchanged in shape/behavior |
| `log.py` unchanged | ✅ Implemented | design D5 note only, no code change; not in either commit's diff |

### Coherence (Design)
| Decision | Followed? | Notes |
|----------|-----------|-------|
| D1 — signature and home | ✅ Yes | `okf.py:453`, keyword-only `label`, no default |
| D2 — both private wrappers stay | ✅ Yes | one-line delegations in both bundle modules |
| D3 — public, stale docstring removed | ✅ Yes | confirmed by grep |
| D4 — pins before move, in WU1, falsified | ✅ Yes | confirmed by commit diff (claim 3) and independent falsification re-derivation |
| D5 — `log.py` stays a sibling, no code change | ✅ Yes | no diff to `log.py` in either commit |
| D6 — `_patch_title_line` untouched | ✅ Yes | confirmed in source read |
| D7 — no ADR | ✅ Yes | no ADR created; consistent with proposal's own gate verdict |

### Verdict
**PASS WITH WARNINGS**
All 14 tasks complete, all 6 goal-level claims independently re-verified as PASS, full test/lint/type/self-test suite green, falsification independently re-derived and confirmed (stronger post-move signal than apply reported). Two non-blocking findings recorded: an untested "no trailing newline after closing delimiter" framing shape, and a test-only omission-case gap already covered by strict mypy.
