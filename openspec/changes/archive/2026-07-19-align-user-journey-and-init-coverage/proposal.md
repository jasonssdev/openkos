# Proposal: Align user-journey doc with MVP-1 reality + cover init next-step hint

## Intent
MVP-1 is complete and testable end-to-end. Two small, independent, non-product-logic gaps remain:
- **A (coverage):** `init`'s unconditional next-step hint (`main.py:129`) is only substring-asserted under a non-TTY runner, so a regression to the exact wording under real TTY use goes uncaught.
- **B (doc honesty):** `docs/user-journey.md` presents the MVP-2 vision as today's experience in ~10 line-cited spots, so a new MVP-1 user would act on capabilities that do not exist (a `--sensitivity` flag, batch/glob ingest, LLM compile, edit/reclassify review panel, git auto-commit, extracted `concepts/` pages, hand-edit content-hash reconciliation, an enforced `.txt/.md` allowlist).

Success: one TTY test asserts the exact hint string; the doc honestly describes MVP-1 behavior while clearly fencing the richer flow as later-MVP.

## Scope
### In Scope
- **Item A:** add ONE test to `tests/unit/cli/test_init.py` reusing `_simulate_tty` + `input="\n"`, asserting exit_code 0 and the verbatim hint `Next: run \`openkos ingest <path>\` to import your first source.`
- **Item B:** reframe `docs/user-journey.md` covering **all** cited spots together (lines 23-30, 69, 74, 75, 77-79, 82-113, 115-117, 119-141, 150-158, 191) — MVP-1 "what happens today" made accurate; MVP-2 narrative preserved under explicit later-MVP framing.

### Out of Scope / Non-goals
- Any product/CLI code change; no new `--sensitivity` flag or behavior.
- Automated doc-vs-CLI-help consistency check (flagged as possible follow-up).
- Sections already honest (lines 17, 146 lint, 180 forget, `--auto` row) — leave unchanged.

## Approach
- A: mirror `test_tty_prompt_accepts_default`; exact-string assertion. Pure coverage, zero behavior change.
- B: keep the product vision, do NOT delete it. Per exploration fix-column: rewrite Steps 2-4 + Editing-by-hand to describe actual MVP-1 (null-compiler, single `Source` concept, verbatim embed, flat `+`/`~` preview, plain yes/no confirm, exact `--auto` success line, sensitivity via `openkos.yaml` only, no git automation), fence richer flow as later-MVP, and reword the "text only" line as MVP-1 intent (any file accepted; UTF-8 embed or binary-fallback note; no enforced allowlist).

**Reframe-vs-delete rationale:** the MVP-2 narrative is the product's real roadmap and has onboarding value as vision; deleting loses intent. Fencing removes actionable overclaims while preserving direction — matches the doc's own stated intent (line 17).

## Risks
- **Internal consistency (primary):** Item B must fix ALL cited spots in one change; a partial fix leaves the doc half-honest (e.g., Step 3 fixed but Step 4 commit claim, Step 5 `concepts/stoicism.md`, or the two `--sensitivity` mentions still misleading).
- Doc drift is untested; a future `--sensitivity` addition could coincidentally re-align or re-drift — follow-up only.

## Rollback
Revert the doc edit and delete the one test; both are additive/isolated, no product impact.

## Success Criteria
- [x] New TTY test passes and asserts the exact hint string.
- [x] Every cited line in `docs/user-journey.md` is accurate for MVP-1 or explicitly marked later-MVP.
- [x] No product code changed; `uv run pytest` green.
