```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:68f985462f1f5865837587b31bd09fea9e52d97b5388e4df80e1551554c65223
verdict: pass
blockers: 0
critical_findings: 0
requirements: 5/5
scenarios: 7/7
test_command: uv run pytest tests/unit/cli/test_query.py tests/unit/cli/test_query_save.py tests/unit/application -q
test_exit_code: 0
test_output_hash: sha256:1e726be32cf64a3c4272ac730e205a90fc9be498935f66db1ef71c761b34ba25
build_command: uv run ruff check . && uv run ruff format --check . && uv run mypy .
build_exit_code: 0
build_output_hash: sha256:a8aad9fe14d790b31848ce6b1ae5a67de952deb153b6ce670e2e08f5849f5a62
```

## Verification Report

**Change**: query-application-service (issue #918)
**Version**: N/A
**Mode**: Strict TDD

### Re-verification note

This supersedes the prior report. Commit `30e411c` rewrote `spec.md`'s Requirement
"Non-CLI Callable Answer Composition" (and its two scenarios), the `query-command`
Purpose delta, and ADR-0018's Context to match the shipped D1 decision (LLM/embedder
as parameters, workspace gating stays the adapter's step). No production code or test
file changed — confirmed via `git diff-tree --no-commit-id --name-only -r 30e411c`,
3 files, all under `openspec/`/`docs/adr/`. The prior CRITICAL is resolved by spec
correction, matching the owner's ruling (constructing a concrete backend inside the
service would be an architectural regression against ADR-0018's D1, approved earlier
this cycle).

### Completeness

| Metric | Value |
|--------|-------|
| Tasks total | 29 |
| Tasks complete | 29 |
| Tasks incomplete | 0 |

### Build & Tests Execution

**Build**: Passed (unchanged from prior verification — no code touched by 30e411c)
```text
$ uv run ruff check . && uv run ruff format --check . && uv run mypy .
All checks passed!
282 files already formatted
Success: no issues found in 282 source files
```

**Tests**: Passed (unchanged from prior verification)
```text
$ uv run pytest tests/unit/cli/test_query.py tests/unit/cli/test_query_save.py tests/unit/application -q
201 passed in 19.62s
```

**Coverage**: 98.09% (application package) / 90% threshold → Above

### Spec Compliance Matrix

| Requirement | Scenario | Test | Result |
|-------------|----------|------|--------|
| Non-CLI Callable Answer Composition | A non-CLI caller answers a question | `tests/unit/application/test_query_service.py` (8 `run_query` tests) + standalone script re-run against this exact signature (question, layout, cfg, llm, embedder) | COMPLIANT |
| Non-CLI Callable Answer Composition | No concrete backend is bound inside the service | static inspection: `application/query.py:31-45` imports, `run_query`'s `llm: LLMBackend, embedder: Embedder` params (both `Protocol` types from `llm.base`) | COMPLIANT (static evidence — see Correctness) |
| Filing Composition Is Independently Callable | A filing plan is computed without writing | `tests/unit/application/test_query_filing.py::test_stage_filed_answer_builds_a_plan_from_a_readable_citation` | COMPLIANT |
| Filing Composition Is Independently Callable | Zero citations refuse at the service boundary | `tests/unit/application/test_query_filing.py::test_stage_filed_answer_refuses_empty_citations` | COMPLIANT |
| Shared Write Mechanics Are Called Through, Never Forked | Committing a plan uses the existing shared helpers | static evidence (single definitions; `main.py` call sites unchanged) | COMPLIANT |
| Adapter Owns Interaction, Presentation, And Exit Codes | The CLI still owns the confirmation gate | `tests/unit/cli/test_query_save.py` (confirm-gate cases, 107/107) | COMPLIANT |
| The Extraction Preserves Observable CLI Behavior | A previously-passing CLI scenario is unchanged | `tests/unit/cli/test_query.py` (58/58) + `tests/unit/cli/test_query_save.py` (107/107) | COMPLIANT |

**Compliance summary**: 7/7 scenarios compliant

### Correctness (Static Evidence) — rewritten Requirement 1

The rewritten requirement now reads: the service "MUST expose a synchronous callable
that composes index opening with degrade handling and the `answer()` call... It MUST
receive its workspace layout, configuration, LLM backend and embedder as parameters
rather than constructing them... Workspace gating stays the caller's step:
`config.require_workspace` already returns a refusal reason rather than printing or
exiting."

Verified against the code, all true:

- `run_query` (`src/openkos/application/query.py:108-166`) is synchronous (`grep -rn
  "async def" src/openkos/application/` returns nothing), composes only store-opening
  with degrade handling and the `answer()` call, and takes `layout:
  config.WorkspaceLayout`, `cfg: config.Config`, `llm: LLMBackend`, `embedder:
  Embedder` as parameters — matches the rewritten scenario's GIVEN/WHEN exactly
  (question, layout, cfg, llm, embedder). Re-ran the standalone script from the prior
  verification against this exact parameter set: imported only
  `openkos.application.query`, called `run_query`, asserted `"openkos.cli" not in
  sys.modules` throughout — passed.
- `application/query.py`'s import list (lines 31-45) names no concrete backend —
  `grep -n "OllamaClient\|Ollama("` returns zero matches. `llm`/`embedder` are typed
  as `LLMBackend`/`Embedder`, the `Protocol` types from `openkos.llm.base`, not a
  concrete class.
- The coordinator's correction to my prior evidence is verified as accurate:
  `config.require_workspace` (`src/openkos/config.py:690-712`) has signature `(root:
  Path) -> str | None` and its own docstring states "config stays free of `typer`
  (layering)" — confirmed `config.py` has no `typer` import at all. It returns a
  refusal reason string; the printing (`typer.echo`) and process exit
  (`typer.Exit(1)`) happen in `main.py:16361-16364`, the adapter, wrapped around this
  pure function — exactly as the rewritten requirement now describes ("stays the
  caller's step", "already returns a refusal reason rather than printing or
  exiting"). My prior report's phrasing ("`require_workspace` still prints to stderr
  and calls `typer.Exit(1)` directly") was imprecise about *which* function does the
  printing; the underlying architecture-vs-spec gap I flagged was real (spec.md
  claimed the SERVICE would report this via return contract, and it didn't), and it
  is what commit `30e411c` fixed by rewriting the requirement rather than the code.

**Scenario 2 test-coverage note (SUGGESTION-level, not blocking)**: "No concrete
backend is bound inside the service" is verified here by direct source inspection
(imports + signature), the same standard already applied to "Shared Write Mechanics
Are Called Through" in this report. Neither has a dedicated automated regression
test analogous to `test_layering.py`'s AST scan for the `openkos.cli` import
invariant — a future edit that imports a concrete backend into `application/query.py`
would not be caught by a named test the way an `openkos.cli` import would be. This is
consistent with, not worse than, the standard already used elsewhere in this
verification; noted for completeness only.

### Coherence (Design)

All D1–D5 decisions and both documented implementation deviations remain verified as
in the prior report (unchanged — no code was touched by `30e411c`); re-confirmed:
`test_layering.py` passes, `_slugify` delegates cleanly, the 124th patch site is
fully migrated, ADR-0018 Context (lines 37-42) now agrees with its own
Decision/Consequences sections instead of contradicting them.

| Decision | Followed? | Notes |
|----------|-----------|-------|
| D1 — hybrid test-injection seam (inject `llm`/`embedder`, migrate `answer`) | Yes | Unchanged from prior report; now also the basis the rewritten spec.md Requirement 1 correctly describes. |
| D2 — exceptions propagate unwrapped, ordering stays in the adapter | Yes | Unchanged. |
| D3 — `--save` splits on the existing Phase A / Phase B line | Yes | Unchanged. |
| D4 — consent gates stay in the adapter; service supplies facts only | Yes | Unchanged. |
| D5 — `application/__init__.py` exports nothing | Yes | Unchanged. |
| Layering invariant | Yes | `test_layering.py` re-run, passes. |
| ADR-0018 internal consistency (Context vs. Decision/Consequences) | Yes, now | Prior Context named "chat-client and embedder construction" as part of the gap; Decision/Consequences already excluded it. `30e411c` corrected the Context to match, removing the internal contradiction the coordinator identified. |

### Proposal Success Criteria

Unchanged from the prior report — all six items remain Met; `30e411c` touched no code
this checklist depends on.

### Issues Found

**CRITICAL**: None. (Prior CRITICAL — Requirement 1 overstating workspace/config
resolution, LLM/embedder construction, and return-contract uninitialized-workspace
reporting — resolved by `30e411c`'s spec rewrite, verified above against the code.)

**WARNING**: None.

**SUGGESTION**:
1. Neither "No concrete backend is bound inside the service" (Requirement 1) nor
   "Committing a plan uses the existing shared helpers" (Requirement 3) has a
   dedicated automated regression test; both are currently verified only by static
   source inspection during this review. `test_layering.py` is the precedent for
   turning this kind of invariant into an AST-scan test — an equivalent guard (e.g.
   asserting `application/query.py` never imports `openkos.llm.ollama.OllamaClient`,
   or a duplicate-definition grep wrapped in a test) would make both invariants
   self-checking rather than review-dependent.

### Verdict

PASS — 0 CRITICAL, 0 WARNING, 1 SUGGESTION. All 5 requirements and 7 scenarios are
compliant against the code as it stands, verified independently rather than assumed:
the rewritten Requirement 1 and its two scenarios hold exactly (synchronous callable,
parameters-only LLM/embedder, no concrete backend named, workspace gating correctly
attributed to the adapter around a `typer`-free `config.require_workspace`); the
`query-command` Purpose Update and ADR-0018 Context correction both removed the same
class of overstatement without introducing a new mismatch. Ready for archive.
