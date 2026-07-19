# Design: Align user-journey doc with MVP-1 reality + cover init next-step hint

## Technical Approach
Two additive, independent changes, zero product-code change:
- **A (test):** one exact-string TTY test in `tests/unit/cli/test_init.py`.
- **B (doc):** one coordinated edit pass over `docs/user-journey.md` reframing every MVP-2 overclaim as current MVP-1 reality while fencing the vision as later-MVP.

## Architecture Decisions

### Decision: Coverage test written straight to GREEN (no RED)
**Choice**: Item A asserts behavior that already exists (`cli/main.py:129` unconditionally echoes the hint). Write the test and confirm it passes immediately; document in the docstring that this is a coverage-hardening test for pre-existing behavior. Do NOT touch `main.py`.
**Alternatives considered**: Contrive a RED by first breaking `main.py` — rejected: violates zero-product-change scope. Skip the test — rejected: leaves exact wording unguarded under real TTY.
**Rationale**: Strict TDD's RED-first rule targets *new* behavior. A regression-guard for existing behavior is legitimately GREEN-on-write; the value is locking the verbatim string against future drift under a TTY runner (existing tests only substring-assert under non-TTY).

### Decision: Reframe, not delete, the MVP-2 narrative (Item B)
**Choice**: Keep the roadmap vision; add explicit MVP-1/later-MVP fencing so no reader acts on absent capabilities.
**Alternatives considered**: Delete MVP-2 content — rejected: loses real roadmap/onboarding value and the doc's stated intent (line 17).
**Rationale**: Removes actionable overclaims while preserving direction.

### Decision: Framing convention — mirror the doc's existing style
**Choice**: Use inline bold labels **In MVP 1:** and **Later MVPs:** as sentence/paragraph prefixes, matching the doc's own existing phrasing (lines 146, 180). Apply uniformly across all spots so the doc stays internally consistent in ONE pass.

## File Changes
| File | Action | Description |
|------|--------|-------------|
| `tests/unit/cli/test_init.py` | Modify | Add one TTY test (below) |
| `docs/user-journey.md` | Modify | Single coordinated reframe pass |

No other files change. `src/openkos/cli/main.py` is READ-ONLY here.

## Item A — exact test spec
Mirror `test_tty_prompt_accepts_default` (lines 290-302): `monkeypatch.chdir(tmp_path)` + `_simulate_tty(monkeypatch)` (helper lines 25-34) + `runner.invoke(app, ["init"], input="\n")`. Assertions: `result.exit_code == 0` and the VERBATIM full line:
`assert "Next: run \`openkos ingest <path>\` to import your first source." in result.output`
(full line, not the `openkos ingest` substring the fresh-dir test already uses). Name e.g. `test_tty_init_prints_exact_next_step_hint`; docstring notes it guards pre-existing `main.py:129` wording under a real TTY.

## Item B — spots and correct MVP-1 statements (fix ALL in one pass)
1. **ASCII loop diagram (23-30):** keep the loop as vision; add an **In MVP 1:** note that `compile` = verbatim embed into a single `Source` concept (no LLM synthesis).
2. **Step 1 command (line 69):** drop `--sensitivity confidential` from the sample; **In MVP 1:** sensitivity comes from `openkos.yaml` `default_sensitivity`, not a flag.
3. **Batch/glob ingest (line 74):** **Later MVPs:** batch/glob; MVP-1 is one-at-a-time only.
4. **`--sensitivity` at capture (line 75):** **In MVP 1:** set via `openkos.yaml`; per-source `--sensitivity` flag is **Later MVPs**.
5. **Step 2 Compile (77-79):** rewrite to null-compiler — copy source, embed verbatim as one `Source` concept, update `index.md`/`log.md`; no LLM, no extracted people/concepts/decisions pages, no correction detection. Fence the model-driven narrative.
6. **Step 3 review panel (82-113):** rewrite sample panel to MVP-1 — flat `+`/`~` list of the single Source concept + index/log; plain yes/no confirm (no `[e]dit`, no `v1→v2` reclassification). Fence the rich panel + `[e]dit` as later-MVP.
7. **`--auto` success line (line 110):** apply must use the VERBATIM MVP-1 success string from the ingest command implementation — do NOT invent. Derive it from `src/openkos/cli` at apply time.
8. **Step 4 Commit (115-117):** **In MVP 1:** changes are written to disk; git commit is manual/optional. Auto-commit is **Later MVPs**.
9. **Step 5 query citations (119-141):** MVP-1 chain points to the Source concept → `raw/` original (not extracted `concepts/stoicism.md`). Fence the corrected-understanding synthesis narrative as later-MVP.
10. **Hand-edit reconciliation (150-158):** keep true part (files are yours, editable anywhere). **Later MVPs:** content-hash reconciliation, external-edit logging in `log.md`, merge-on-ingest.
11. **"text only" (line 191):** reword as MVP-1 intent — any file is accepted and copied to `raw/`; text embeds as UTF-8, non-text falls back to a binary note; no enforced `.txt/.md` allowlist. Format producers are later-MVP.

**Leave unchanged (already honest):** lines 17, 146 (lint), 180 (forget), the `--auto` two-ways table.

## Testing Strategy
| Layer | What | Approach |
|-------|------|----------|
| Unit | exact TTY hint | `uv run pytest tests/unit/cli/test_init.py` — GREEN on write |
| Doc | no residual overclaims | manual read + grep for `--sensitivity`, `[e]dit`, `concepts/stoicism`, `committed to git`, `.txt`, glob/batch, `v1→v2` |

## Threat Matrix
N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or process-integration boundary. Doc + one test only.

## Migration / Rollout
No migration. Rollback: revert the doc edit and delete the one test.

## Open Questions
- [x] Exact MVP-1 `--auto` success line (spot 7) — apply must read it from the ingest implementation, not guess.
