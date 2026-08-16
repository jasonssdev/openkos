# Tasks: Always Identify Named People (#712)

## Review Workload Forecast

| Field | Value |
|---|---|
| Estimated changed lines | S1 ~450-550, S2 ~250-320, S3 ~350-420, S4 ~200-280 |
| 400-line budget risk | Medium (S1 near/over alone; S2-S4 individually under) |
| Chained PRs recommended | Yes |
| Suggested split | PR1 (tracker base) → PR2 (PR1 base) → PR3 (PR2 base) → PR4 (PR3 base) |
| Delivery strategy | auto-chain |
| Chain strategy | feature-branch-chain |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: Medium

### Suggested Work Units

| Unit | Goal | PR | Focused test | Runtime harness | Rollback boundary |
|---|---|---|---|---|---|
| 1 | Volume eval, capacity number | PR1→tracker | `uv run pytest evals/named_person_volume -k self_test` | `python evals/named_person_volume/run.py --self-test -u` | delete `evals/named_person_volume/` |
| 2 | Prompt rewrite, conjunct delete, rename | PR2→PR1 | `uv run pytest tests/unit/extraction/test_concept.py -k readmit or anchor` | N/A — prompt/logic change, no live model call in unit tests | revert prompt string + conjunct + rename diff |
| 3 | Two-lane budget | PR3→PR2 | `uv run pytest tests/unit/extraction/test_concept.py -k lane` | `openkos ingest` over a stubbed meeting source with >lane participants | revert constant, slice split, report fields, notice |
| 4 | Advisory grounding | PR4→PR3 | `uv run pytest tests/unit/extraction/test_concept.py -k grounding` | `openkos ingest` over AMI fixture, label-only exemption check | revert `_names_absent_from_source` + call site |

## Phase 0: Gate — Slice 1 Report (blocks Phase 2+)

- [x] 0.1 Enumerate every reader of `ExtractionReport.produced`/`.retained`/`.discarded_titles` (grep `src/`, `cli/main.py`, `evals/`) BEFORE any D3 code change; list in PR3 description. (Read-only; enumeration recorded in `evals/named_person_volume/report.md` "Task 0.1" section and in apply-progress for slice 3 to carry into PR3.)
- [x] 0.2 Gate: slice 2 code changes MUST NOT start until `evals/named_person_volume/report.md` exists and D2's REJECT rule (recall, latency, count-gain, fabrication) has been evaluated against it — name the report as the gating artifact in PR2. (Report shipped in PR #716; the rule returned **REJECT** on conditions 2 and 4, which is what closed task 2.4 unshipped. Slice 2 proceeded on the non-prompt half only.)

## Phase 1: Slice 1 — Volume Eval (`evals/named_person_volume/`)

- [x] 1.1 Write `--self-test` harness skeleton (no model) proving fixture loading, arm application, scoring — `uv run pytest`-visible, mirrors `evals/language_leak` shape. (`evals/named_person_volume/run.py` + `test_run.py`; RED confirmed via `ModuleNotFoundError` before `run.py` existed, GREEN after.)
- [x] 1.2 Add `es-bare` and AMI `TS3005a` fixtures; write hand-authored `adjudication.json` (never regex-derived). (Fixtures in `run.py::build_fixtures`; `adjudication.json` hand-labeled from real candidate description/body text after the sweep.)
- [x] 1.3 Implement metrics A-D + `p_max` capacity derivation; monkeypatch `_PARTICIPANT_CAPTURE_SYSTEM_PROMPT` for treatment, production untouched. (`_TreatmentPatch`, `compute_combo_metrics`, `compute_p_max`, `derive_backstop`.)
- [x] 1.4 Run real qwen3:8b sweep (2 fixtures × 2 arms × 3 runs), store JSONL under `results/`. (12 runs attempted, 10 completed; `results/named-person-volume-20260815T023755Z-qwen3-8b.jsonl`.)
- [x] 1.5 Write `report.md` with derived `_PARTICIPANT_BACKSTOP = max(8, ceil(1.5*p_max))` and the REJECT-rule verdict. (Verdict: REJECT — latency 1.92x baseline, no merely-named increase. `_PARTICIPANT_BACKSTOP = 8`.)
- [x] 1.6 Mutation-confirm scoring function against its exact target line (purge `__pycache__` first). (`evaluate_reject_rule`'s subject-recall comparison at its exact line; mutated `<`→`<=`, confirmed self-test failure, reverted, re-confirmed green with purged bytecode both times.)

## Phase 2: Slice 2 — Reverse the Stub Rule (gated by 0.2)

- [x] 2.1 RED: rewrite `test_concept.py:2887` — bare-name Person is re-admitted, no anchor check.
- [x] 2.2 RED: rewrite `test_concept.py:2925` — anchorless discard list is `()`.
- [x] 2.3 RED: NEW test — bare-name Person on NON-meeting-shaped source still not re-admitted.
- [~] 2.4 SKIPPED: D2's rewrite was REJECTED by the Phase 0.2 gate (latency 1.92x, no merely-named increase). Per D2 a rejection ships nothing prompt-level, so this task is closed unshipped, not deferred.
- [x] 2.5 GREEN: delete `and _has_participant_anchor(c)` at `concept.py:2772`; keep `_has_participant_anchor`/`_PARTICIPANT_ANCHOR_RE` exported, rewrite their docstrings (D6).
- [x] 2.6 Rename `participant_anchorless_discarded_titles` → `participant_unreadmitted_discarded_titles`; update `_participant_stub_notice` wording (`cli/main.py`).
- [x] 2.7 Update read site `run_participant_anchor_probe.py:376` (`_bucket_of`); rename bucket `anchorless-discarded` → `unreadmitted-discarded`; note old label in `README.md`.
- [x] 2.8 Update read site `run_type_coverage.py:257` (`anchorless_discarded_total`).
- [x] 2.9 Grep `docs/` for stale "Person needs an anchor" prose; correct.
- [x] 2.10 Update spec deltas per `extraction-union-judge/spec.md` scenarios (already authored — verify code matches).
- [x] 2.11 Mutation-confirm 2.1-2.3 against exact target lines; purge `__pycache__`.

## Phase 3: Slice 3 — Two-Lane Budget (after 0.1)

**CLOSED UNSHIPPED 2026-08-16.** D4's own reopen trigger — "a stored run whose
participant lane actually truncates" — was tested against every stored run of
every participant-bearing harness and does not exist. `_UNION_BACKSTOP` is 20
and the largest retained set ever recorded is 9 objects (45 runs,
`stage_attrition`), 7 with `--participants` on (9 runs, `participant_anchor`),
against a measured `p_max` of 3. The lane would bound nothing, and tasks 3.1/3.2
exist only to protect eval comparisons from the re-basing tasks 3.5/3.6 cause.
Owner ruling: close unshipped with the evidence, not deferred. Full reasoning in
`STATUS.md`.

- [~] 3.1 UNSHIPPED: `RunRecord.schema` marker — needed only because 3.5 re-bases the stored counts, which is not happening.
- [~] 3.2 UNSHIPPED: `--rescore` mixed-schema refusal — same dependency as 3.1.
- [~] 3.3 UNSHIPPED: lane isolation tests — no lane.
- [~] 3.4 UNSHIPPED: two-notice distinctness test — `_participant_lane_notice` not built.
- [~] 3.5 UNSHIPPED: `_PARTICIPANT_BACKSTOP` and the `retained` split — the ceiling it would relieve has never bound.
- [~] 3.6 UNSHIPPED: participant-lane report fields and notice — nothing to report without 3.5.
- [~] 3.7 UNSHIPPED: notice wiring — no notice.
- [~] 3.8 UNSHIPPED: mutation confirmation for 3.2-3.4 — no code to mutate.

## Phase 4: Slice 4 — Advisory Name Grounding

- [x] 4.1 RED: absent name flagged; accented variant (Germán/German) not flagged (NFD strip); label-only source computes `()`.
- [x] 4.2 GREEN: `_names_absent_from_source(results, *, source_text)` per D5 comparison idiom + NFD/combining-mark strip.
- [x] 4.3 GREEN: wire label-only exemption via `_transcript_shaped_text` label regex check.
- [x] 4.4 Mutation-confirm 4.1 against exact target lines; purge `__pycache__`.

## Phase 5: Verification

- [x] 5.1 Assert `_SYSTEM_PROMPT` (`concept.py:127-139`) and `test_concept.py:1488` are byte-unchanged (diff check) at end of chain. (`git diff ab33970^ ca84660 -- src/openkos/extraction/concept.py` shows no change to `_SYSTEM_PROMPT`; the only matches are docstring mentions of the separate `_PARTICIPANT_CAPTURE_SYSTEM_PROMPT`. NOTE for future readers: `_SYSTEM_PROMPT` DID change later, in #715/PR #723, which added `TRANSCRIPT_SUBJECTS_CLAUSE` deliberately — that is a different change and does not invalidate this assertion over this chain's range.)
- [x] 5.2 Assert `evals/participant_anchor/report.md` and `results/**` unchanged; only `run_participant_anchor_probe.py`, `README.md`, `adjudication.json` show diffs. (Verified over `ab33970^..ca84660`: exactly two files touched, `README.md` and `run_participant_anchor_probe.py`. `report.md`, `results/**` and `adjudication.json` untouched — so #706's verdict stays re-derivable, which is the whole reason D6 kept `_has_participant_anchor` exported.)
- [x] 5.3 `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy .` over full repo including `evals/`. (All checks passed; 215 files formatted; mypy clean over 215 source files. Run at archive, 2026-08-16.)
- [x] 5.4 Full `uv run pytest` green. (4813 passed, 1 skipped, 183s. Run unpiped at archive, 2026-08-16.)
