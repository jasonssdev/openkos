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
- [ ] 0.2 Gate: slice 2 code changes MUST NOT start until `evals/named_person_volume/report.md` exists and D2's REJECT rule (recall, latency, count-gain, fabrication) has been evaluated against it — name the report as the gating artifact in PR2.

## Phase 1: Slice 1 — Volume Eval (`evals/named_person_volume/`)

- [x] 1.1 Write `--self-test` harness skeleton (no model) proving fixture loading, arm application, scoring — `uv run pytest`-visible, mirrors `evals/language_leak` shape. (`evals/named_person_volume/run.py` + `test_run.py`; RED confirmed via `ModuleNotFoundError` before `run.py` existed, GREEN after.)
- [x] 1.2 Add `es-bare` and AMI `TS3005a` fixtures; write hand-authored `adjudication.json` (never regex-derived). (Fixtures in `run.py::build_fixtures`; `adjudication.json` hand-labeled from real candidate description/body text after the sweep.)
- [x] 1.3 Implement metrics A-D + `p_max` capacity derivation; monkeypatch `_PARTICIPANT_CAPTURE_SYSTEM_PROMPT` for treatment, production untouched. (`_TreatmentPatch`, `compute_combo_metrics`, `compute_p_max`, `derive_backstop`.)
- [x] 1.4 Run real qwen3:8b sweep (2 fixtures × 2 arms × 3 runs), store JSONL under `results/`. (12 runs attempted, 10 completed; `results/named-person-volume-20260815T023755Z-qwen3-8b.jsonl`.)
- [x] 1.5 Write `report.md` with derived `_PARTICIPANT_BACKSTOP = max(8, ceil(1.5*p_max))` and the REJECT-rule verdict. (Verdict: REJECT — latency 1.92x baseline, no merely-named increase. `_PARTICIPANT_BACKSTOP = 8`.)
- [x] 1.6 Mutation-confirm scoring function against its exact target line (purge `__pycache__` first). (`evaluate_reject_rule`'s subject-recall comparison at its exact line; mutated `<`→`<=`, confirmed self-test failure, reverted, re-confirmed green with purged bytecode both times.)

## Phase 2: Slice 2 — Reverse the Stub Rule (gated by 0.2)

- [ ] 2.1 RED: rewrite `test_concept.py:2887` — bare-name Person is re-admitted, no anchor check.
- [ ] 2.2 RED: rewrite `test_concept.py:2925` — anchorless discard list is `()`.
- [ ] 2.3 RED: NEW test — bare-name Person on NON-meeting-shaped source still not re-admitted.
- [ ] 2.4 GREEN: rewrite `_PARTICIPANT_CAPTURE_SYSTEM_PROMPT` per D2 table (`concept.py:1956`).
- [ ] 2.5 GREEN: delete `and _has_participant_anchor(c)` at `concept.py:2772`; keep `_has_participant_anchor`/`_PARTICIPANT_ANCHOR_RE` exported, rewrite their docstrings (D6).
- [ ] 2.6 Rename `participant_anchorless_discarded_titles` → `participant_unreadmitted_discarded_titles`; update `_participant_stub_notice` wording (`cli/main.py`).
- [ ] 2.7 Update read site `run_participant_anchor_probe.py:376` (`_bucket_of`); rename bucket `anchorless-discarded` → `unreadmitted-discarded`; note old label in `README.md`.
- [ ] 2.8 Update read site `run_type_coverage.py:257` (`anchorless_discarded_total`).
- [ ] 2.9 Grep `docs/` for stale "Person needs an anchor" prose; correct.
- [ ] 2.10 Update spec deltas per `extraction-union-judge/spec.md` scenarios (already authored — verify code matches).
- [ ] 2.11 Mutation-confirm 2.1-2.3 against exact target lines; purge `__pycache__`.

## Phase 3: Slice 3 — Two-Lane Budget (after 0.1)

- [ ] 3.1 Add `RunRecord.schema: int` field to `run_participant_anchor_probe.py`, stamped at write time (absent⇒1). MUST land before any post-change eval run writes a record.
- [ ] 3.2 RED: `--rescore` refuses mixed `schema` 1/2 in one comparison, names reason.
- [ ] 3.3 RED: lane isolation — participant overflow doesn't evict a subject and vice versa; discarded lists disjoint.
- [ ] 3.4 RED: `_participant_lane_notice` and `_extraction_cap_notice` both fire in one run, neither text substrings the other.
- [ ] 3.5 GREEN: add `_PARTICIPANT_BACKSTOP` (value from 1.5) and split `retained` at `concept.py:2809` per D3; narrow `produced`/`retained`/`discarded_titles` to subject-lane only.
- [ ] 3.6 GREEN: add `participant_produced`/`participant_retained`/`participant_discarded_titles` fields; `_participant_lane_notice()` beside `cli/main.py:3055`.
- [ ] 3.7 Wire notice into `ingest` output.
- [ ] 3.8 Mutation-confirm 3.2-3.4 against exact target lines; purge `__pycache__`.

## Phase 4: Slice 4 — Advisory Name Grounding

- [ ] 4.1 RED: absent name flagged; accented variant (Germán/German) not flagged (NFD strip); label-only source computes `()`.
- [ ] 4.2 GREEN: `_names_absent_from_source(results, *, source_text)` per D5 comparison idiom + NFD/combining-mark strip.
- [ ] 4.3 GREEN: wire label-only exemption via `_transcript_shaped_text` label regex check.
- [ ] 4.4 Mutation-confirm 4.1 against exact target lines; purge `__pycache__`.

## Phase 5: Verification

- [ ] 5.1 Assert `_SYSTEM_PROMPT` (`concept.py:127-139`) and `test_concept.py:1488` are byte-unchanged (diff check) at end of chain.
- [ ] 5.2 Assert `evals/participant_anchor/report.md` and `results/**` unchanged; only `run_participant_anchor_probe.py`, `README.md`, `adjudication.json` show diffs.
- [ ] 5.3 `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy .` over full repo including `evals/`.
- [ ] 5.4 Full `uv run pytest` green.
