# Tasks: okf-codec-seam — consolidate the OKF frontmatter-split duplicate

## Review Workload Forecast

| Field | Value |
|---|---|
| Estimated changed lines | ~150-250 (4 test files: 3 new, 1 widened; 3 source files) |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR, two commits: WU1 (pins) then WU2 (move) |
| Delivery strategy | auto-chain |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Low

WU1 and WU2 together stay well inside the 400-line budget — ship as one PR,
two commits, WU1 first per the ordering constraint below.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|---|---|---|---|---|---|
| 1 | Land parity, golden, and full-string pins; zero `src/` changes | PR 1, commit 1 | `uv run pytest tests/unit/bundle/test_frontmatter_split_parity.py tests/unit/model/test_okf_framing_characterization.py tests/unit/bundle/test_source_titles.py` | N/A — pure unit tests, no CLI/runtime scenario | revert commit 1; additive, no `src/` impact |
| 2 | Move helper into `model/okf.py`; wrappers delegate | PR 1, commit 2 | `uv run pytest tests/unit/model/test_okf.py tests/unit/bundle/` | N/A — pure-function move, no process/CLI boundary | `git revert` commit 2; both copies return, WU1 pins stay green |

## Phase 1: WU1 — Pins (zero `src/` changes)

- [x] 1.1 Create `tests/unit/bundle/test_frontmatter_split_parity.py`: parametrize over `(index._split_frontmatter_verbatim, "index.md")` and `(source_titles._split_frontmatter_verbatim, "Source document")` crossed with a shared corpus (CRLF body, quoted `okf_version: '0.1'`, `---` inside body, no trailing newline, empty body, non-ASCII, absent block). Assert identical `(block, body)` and the FULL exact refusal string per callable — both prefixes, not the shared half.
- [x] 1.2 Create `tests/unit/model/fixtures/okf_framing_goldens.json` (new `fixtures/` dir): record exact `(block, body)` per scenario, generated from `bundle.index._split_frontmatter_verbatim`, shaped after `ingest_characterization_goldens.json`.
- [x] 1.3 Create `tests/unit/model/test_okf_framing_characterization.py`: load the golden once at import, keyed by scenario; assert recorded `(block, body)` and `block + body == text` for every scenario.
- [x] 1.4 Widen `tests/unit/bundle/test_source_titles.py:194` match to the full string `"Source document: missing or malformed frontmatter block"` (this call path reaches `retitle_document` directly, so the prefix is not swallowed here).
- [x] 1.5 Run `uv run pytest tests/unit/bundle/test_frontmatter_split_parity.py tests/unit/model/test_okf_framing_characterization.py tests/unit/bundle/test_source_titles.py`; confirm GREEN against today's correct two-copy code — no `src/` change is needed to pass.
- [x] 1.6 Falsification transcript (one task, three mutate/revert cycles, `__pycache__` purged before AND after each): (a) mutate `index.py`'s `_FRONTMATTER_RE` (drop `re.DOTALL`), rerun 1.5's command, confirm RED, revert with the exact inverse edit; (b) mutate `index.py:30`'s prefix, rerun, confirm RED, revert; (c) mutate `source_titles.py:157`'s prefix, rerun 1.5's command plus `test_source_titles.py`, confirm RED, revert. Record each RED/GREEN pair in the PR description as falsification evidence.
- [x] 1.7 Run WU1 verification: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy .`, `uv run python evals/run_self_tests.py`. Commit WU1.

## Phase 2: WU2 — Move and delegate (only after WU1 is committed)

- [x] 2.1 In `src/openkos/model/okf.py`, add `_FRONTMATTER_RE: Final` and public `split_frontmatter_verbatim(text: str, *, label: str) -> tuple[str, str]` immediately after `load_frontmatter` (currently `:444-447`); `re` is already imported (`okf.py:15`). Raises `ValueError(f"{label}: missing or malformed frontmatter block")`.
- [x] 2.2 In `src/openkos/bundle/index.py`, delete the local `_FRONTMATTER_RE` and the body of `_split_frontmatter_verbatim`; replace with `_FRONTMATTER_LABEL = "index.md"` plus a one-line delegation to `okf.split_frontmatter_verbatim(text, label=_FRONTMATTER_LABEL)`. Five call sites (`index.py:168,284,327,396,488`) stay unchanged.
- [x] 2.3 In `src/openkos/bundle/source_titles.py`, delete the local `_FRONTMATTER_RE` and the body of `_split_frontmatter_verbatim`; replace with `_FRONTMATTER_LABEL = "Source document"` plus a one-line delegation. The one call site (`source_titles.py:234`) stays unchanged; `_patch_title_line` and its regexes/width constant are untouched.
- [x] 2.4 Delete the now-stale docstring paragraph at `source_titles.py:149-153` ("A deliberate separate copy... to avoid cross-module private coupling") — the move makes it false.
- [x] 2.5 Add a direct test in `tests/unit/model/test_okf.py`: `label` is required and keyword-only (positional/omitted call is a static `mypy` error; assert the dynamic `TypeError`), and the raised message carries the passed label verbatim.
- [x] 2.6 Rerun 1.5's full pin command plus 2.5's new test; confirm all GREEN. Note in the PR: parity now proves label-only equivalence, not implementation — its divergence-detection power was only on the pre-move tree (design D4).
- [x] 2.7 Run WU2 verification: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy .`, `uv run python evals/run_self_tests.py`. Confirm `grep -rn _FRONTMATTER_RE src/` returns exactly one match. Commit WU2.

## Notes for apply

- WU1 MUST be committed before any `src/` edit in WU2 — scope, not sequencing preference (design D4).
- This file is documentation; keep its edits outside the counted attempt-budget window, or cap for them explicitly — `tasks.md` line counts have historically been charged to the attempt budget in this repo.
- Post the exploration's measurement/narrowing on #919 before opening the PR (proposal risk row).
