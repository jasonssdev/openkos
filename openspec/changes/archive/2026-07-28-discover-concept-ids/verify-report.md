```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:ffc00a4993962ae4199fbb4ec1838d10305a163cdd6a2f9b5cdead352b22fb86
verdict: pass
blockers: 0
critical_findings: 0
requirements: 9/9
scenarios: 15/15
test_command: uv run pytest -q
test_exit_code: 0
test_output_hash: sha256:ffc00a4993962ae4199fbb4ec1838d10305a163cdd6a2f9b5cdead352b22fb86
build_command: uv run mypy .
build_exit_code: 0
build_output_hash: sha256:f5d9f37444f3063f27cf4a26a7ec61cd3fc393ec38e414ea697cc1da45923db1
```

## Verification Report

**Change**: discover-concept-ids
**Version**: N/A (spec.md at HEAD, no version header)
**Mode**: Strict TDD
**Scope**: Two-PR pair — PR1 `feat/list-enumerator` (commit `c4859b1`, targets `main`), PR2 `feat/list-cli-verb` (commits `e36adbd`, `5d14581`, `ced7820`, based on PR1 HEAD). Verified together as instructed.

> **SUPERSEDES** the previous verify-report on this file (verdict `fail`, `evidence_revision: sha256:03db001f5cf453db25727a1a94a3db8873b447b6e26a549d4fb5318c7de8e372`), which found 1 CRITICAL ("No mutation on any run" scenario had no runtime-observable test) and 1 WARNING (`--limit 0 --all` diverged from spec text). A bounded remediation on commit `ced7820` closed both gaps. This report re-verifies the WHOLE change — every requirement and scenario, not only the three remediated items — per the re-verification instructions, and finds no regression. New verdict: **PASS**.

### What changed since the previous FAIL
1. Added `_workspace_snapshot` helper plus `test_list_mutates_nothing_on_a_run_that_produces_rows` and `test_list_mutates_nothing_on_a_run_that_truncates_output` in `tests/unit/cli/test_list.py`.
2. Added `test_list_json_flag_is_rejected_as_unknown_option`.
3. Changed `src/openkos/cli/main.py`'s `list_objects_cmd` refusal condition from `not all_objects and limit <= 0` to `limit <= 0` (line 5150) — the orchestrator decided the spec text ("`--limit 0` and any negative `--limit` MUST be rejected as invalid input") was correct and the implementation had to conform, not the reverse. `spec.md` was deliberately left unedited. Added `test_list_limit_zero_with_all_still_refuses` and `test_list_limit_negative_with_all_still_refuses`. `tasks.md` task 9.2 wording corrected to match.

`src/openkos/bundle/listing.py` was confirmed untouched since PR1 commit `c4859b1` (`git diff --stat c4859b1..HEAD -- src/openkos/bundle/listing.py` is empty) — frozen as claimed.

### Completeness
| Metric | Value |
|--------|-------|
| Tasks total | 33 |
| Tasks complete | 33 |
| Tasks incomplete | 0 |

All 33 tasks across both PRs (Phases 1–14) are checked complete in `tasks.md`, matching the code state inspected below.

### Command Evidence
| Command | Exit | Result |
|---|---|---|
| `uv run pytest -q` | 0 | 2484 passed. (One earlier run in this same verification session showed `1 failed, 2483 passed` — `tests/unit/cli/test_relate.py::test_traversal_target_id_refuses` — but it passed in isolation and passed on a clean re-run of the full suite immediately after. This is pre-existing order-dependent test pollution unrelated to any file this change touches (`test_relate.py` does not appear in either PR's diff). Not a blocker; flagged as a SUGGESTION below.) |
| `uv run pytest --cov=openkos --cov-branch -q` | 0 | 2484 passed, 97.56% total branch coverage against the 90% gate. |
| `uv run ruff check .` | 0 | All checks passed. |
| `uv run ruff format --check .` | 0 | 146 files already formatted. |
| `uv run mypy .` | 0 | Success: no issues found in 146 source files. |

### Diff Scope
| Comparison | Stat |
|---|---|
| `main...feat/list-enumerator` (PR1) | 2 files, +588/-0 (`listing.py` + `test_listing.py`) |
| `feat/list-enumerator...HEAD` (PR2) | 9 files, +1910/-1 |
| `main...feat/list-cli-verb` (combined) | 11 files, +2498/-1 |

Both PRs individually and combined stay reasonably close to the reviewable-slice intent set out in `tasks.md`'s forecast (PR1 ~588, PR2 ~490 estimated vs. the actual larger PR2 total, driven mostly by design/proposal/explore/apply-progress markdown, not code).

### Spec Compliance Matrix — `list-command` (9 requirements, 15 scenarios, 15/15 covered)

| Requirement | Scenario | Covering test | Verdict |
|---|---|---|---|
| Workspace Presence Check | Run outside a workspace | `test_list_outside_workspace_with_valid_arguments_refuses_via_workspace` | PASS |
| Workspace Presence Check | Bad argument outside a workspace reports the argument | `test_list_unknown_type_outside_workspace_reports_the_type` | PASS |
| Exactly One Bundle Walk | Single walk regardless of filter | `test_list_walks_the_bundle_exactly_once_regardless_of_filter` (plain-function counting wrapper, not `yield from` — confirmed by reading the helper; it records the call at call time and returns `original(bundle_dir)` directly) | PASS |
| Type Filter Vocabulary | Filter by link_dir | `test_list_filters_by_canonical_link_dir` | PASS |
| Type Filter Vocabulary | Filter by REGISTRY.name alias | `test_list_filters_by_registry_name_alias_matches_link_dir_result` | PASS |
| Type Filter Vocabulary | Unknown type filter | `test_list_unknown_type_outside_workspace_reports_the_type` (covers unknown-type refusal generally; error text confirmed to enumerate only canonical `link_dir` names) | PASS |
| Output Bounding | Default limit truncates with footer | `test_list_default_limit_truncates_with_footer` | PASS |
| Output Bounding | --all bypasses the limit | `test_list_all_prints_every_row_with_no_footer` | PASS |
| Output Bounding | Invalid limit rejected | `test_list_limit_zero_refuses_before_any_access`, `test_list_limit_negative_refuses_before_any_access`, plus remediation tests `test_list_limit_zero_with_all_still_refuses` / `test_list_limit_negative_with_all_still_refuses` (the unconditional reading, confirmed against `limit <= 0` in code at `cli/main.py:5150`, run BEFORE `config.require_workspace` at line 5159) | PASS |
| Deprecated and Superseded Visibility | Deprecated object shown by default | `test_list_deprecated_object_shown_by_default_with_no_flag` | PASS |
| Column Layout | Row layout | `test_list_column_layout_is_id_sensitivity_status_title_in_order` | PASS |
| Confidential Titles Are Printed in Full | Confidential title printed in full | `test_list_confidential_title_printed_in_full` (asserts full unredacted title present, no `[REDACTED]`/`***` anywhere in output, and a public row alongside it for shape comparison) | PASS |
| Empty Bundle and Unparseable Document Handling | Empty bundle | `test_list_empty_bundle_prints_friendly_message_and_exits_zero` | PASS |
| Empty Bundle and Unparseable Document Handling | Unparseable document does not abort the walk | `test_list_unparseable_document_still_prints_a_row_and_exits_zero` | PASS |
| Read-Only, No Structured Output | No mutation on any run | `test_list_mutates_nothing_on_a_run_that_produces_rows`, `test_list_mutates_nothing_on_a_run_that_truncates_output`, `test_list_json_flag_is_rejected_as_unknown_option` — see independent test-quality judgment below | PASS |

### Independent Judgment: Are the Remediated Tests Load-Bearing?

The apply agent's RED evidence for the no-mutation tests came from a temporary mutation probe that no longer exists in the tree and cannot be re-observed. I therefore read `_workspace_snapshot` and judged its sensitivity independently rather than trusting the prior RED claim:

```python
def _workspace_snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*")) if path.is_file()
    }
```

- **Captures file contents, not merely the file set**: the dict value is `(content_bytes, mtime_ns)`, keyed by relative path. Any file created or deleted changes the key set, so `before == after` fails. Any in-place edit of an *existing* file — the case the previous FAIL explicitly worried about — changes `content_bytes` in the tuple for that same key, so `before == after` fails even with an unchanged file set and even if mtime happened not to tick. The content comparison alone is sufficient; the added `mtime_ns` makes it strictly more sensitive, not less.
- **Verdict**: this is a genuine, load-bearing assertion. It is not a test that "cannot fail" — a single stray `Path.write_text` anywhere under `root` during the `list` call would break it. I did not need to reinsert the probe to reach this conclusion; the comparison's structure is sufficient on its own to prove it would catch a write.
- **`test_list_json_flag_is_rejected_as_unknown_option`**: asserts `exit_code != 0`, `isinstance(result.exception, SystemExit)`, AND `"no such option" in result.stderr.lower()`. The third assertion is the one that matters — it pins the failure to Typer/Click's specific "no such option" rejection path, not merely "some non-zero exit from any cause" (e.g. it would NOT pass if `--json` were silently swallowed by some other error path that also happened to exit non-zero with unrelated stderr text). This is a real, specific refusal assertion.

### Ordering and Regression Checks (explicit re-verification, not assumed from the remediation summary)

1. **Ladder ordering survived**: read `cli/main.py:5136-5162` directly. Order is (a) `resolve_link_dir` unknown-type refusal, (b) `limit <= 0` refusal, (c) `config.require_workspace`. The `limit` check still sits BEFORE the workspace check — confirmed by code inspection, not by trusting the remediation note.
2. **"Bad TYPE outside a workspace" scenario still passes**: `test_list_unknown_type_outside_workspace_reports_the_type` passed in the full run (2484 passed) and independently asserts `"workspace" not in result.stderr.lower()`, i.e. it fails if a future edit ever reordered the ladder.
3. **`--limit 0 --all` / `--limit -1 --all` now refuse, and `--all` happy path did not regress**: `test_list_limit_zero_with_all_still_refuses` and `test_list_limit_negative_with_all_still_refuses` passed; `test_list_all_prints_every_row_with_no_footer` (60 rows, no footer) also passed in the same run — no regression.
4. **Other previously-passing items re-confirmed by direct inspection, not just re-running the suite**:
   - Exit ladder: confirmed above.
   - Confidential titles printed in full, byte-identical shape, no display gate: `--include-confidential` is not referenced anywhere in `list_objects_cmd`; the docstring makes this explicit ("this command never touches" the LLM-send gate).
   - Exactly one bundle walk with a plain-function counting wrapper (NOT `yield from`): confirmed in both `test_listing.py:227` (PR1) and `test_list.py:195` (PR2) — both wrappers call `original(...)` and `return` it directly, recording the call at call time.
   - `lifecycle.deprecated_concept_ids` never called from the `list` path: `test_list_never_calls_lifecycle_deprecated_concept_ids` monkeypatches it to raise `AssertionError` and asserts exit 0 — genuinely load-bearing.
   - `listing.py` free of derived-layer imports: `grep` confirms only `from openkos.model import okf` and `from openkos.model.types import REGISTRY` plus stdlib (`dataclasses`, `pathlib`).
   - Alias map built from `types.REGISTRY`, `list Source` covered: `_NAME_TO_LINK_DIR` is built from `REGISTRY` directly (not `TYPE_TO_LINK_DIR`, which omits `Source`), with an explicit design-D7-gotcha comment explaining why; PR1's `test_listing.py` parametrizes over all 10 `REGISTRY.name` values including `Source`.
   - Status drift guard intersected with row ids: present in PR1 (`tasks.md` Phase 4, unmodified since `c4859b1`).
   - Fail-visible contract for unreadable documents: `test_list_unparseable_document_still_prints_a_row_and_exits_zero` passed, asserting both rows print, `(unreadable)` marker present, no traceback, exit 0.

### Design Coherence
No design deviations found. `listing.py` remains untouched since PR1 as claimed (verified via `git diff --stat c4859b1..HEAD -- src/openkos/bundle/listing.py`, empty output). The one code change in this remediation (`limit <= 0` instead of `not all_objects and limit <= 0`) is a direct, minimal conformance of implementation to already-approved spec text — not a design deviation.

### Issues

**CRITICAL**: None.

**WARNING**: None. Both WARNING/CRITICAL items from the prior report are closed and independently re-verified above.

**SUGGESTION**:
1. `tests/unit/cli/test_relate.py::test_traversal_target_id_refuses` failed once in a full-suite run during this verification session, then passed both in isolation and on an immediate full-suite re-run. This is pre-existing test-order flakiness unrelated to `discover-concept-ids` (the file is untouched by either PR's diff). Out of scope for this change but worth a separate, low-priority investigation (likely shared mutable state or monkeypatch leakage between CLI tests).

### Verdict: **PASS**

Requirements: 9/9. Scenarios: 15/15 covered by a passing runtime test. Tasks: 33/33 complete. Build/lint/type gates clean. Coverage 97.56% against a 90% gate. No CRITICAL or WARNING findings remain. This report supersedes the prior `fail` verdict on this file.
