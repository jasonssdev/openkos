# Tasks: Entity-Resolution Adjudication (Slice 2)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~650-750 (lib+tests ~460, CLI+tests ~230) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR1 library → PR2 CLI+layering+demo (stacked) |
| Delivery strategy | ask-on-risk |
| Chain strategy | feature-branch-chain (pending confirmation) |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | PR | Focused test | Runtime harness | Rollback boundary |
|---|---|---|---|---|---|
| 1 | Types + `adjudicate_candidates` | PR1 | `uv run pytest tests/unit/resolution/test_adjudication.py` | Fake `LLMBackend`, tmp bundle, no real Ollama | Delete `adjudication.py` + tests; no dependents |
| 2 | `adjudicate` CLI verb, layering, demo | PR2 (base=PR1) | `uv run pytest tests/unit/cli/test_adjudicate.py tests/unit/resolution/test_layering.py` | Typer `CliRunner`, tmp workspace, monkeypatched `OllamaClient` | Revert `main.py` verb + CLI test; library unaffected |

## Phase 1: Foundation

- [ ] 1.1 RED: `Verdict` enum SAME/DIFFERENT/UNCERTAIN; `AdjudicatedCandidate` exposes candidate/verdict/confidence/rationale.
- [ ] 1.2 GREEN: add the enum + frozen dataclass.
- [ ] 1.3 Add reply-queue `_FakeLLM` (records `.calls`, queued replies in order).

## Phase 2: Member Loading

- [ ] 2.1 RED: unreadable member skipped; group adjudicated from remaining readable members.
- [ ] 2.2 RED: all members unreadable → UNCERTAIN, confidence 0.0, "no readable member content", zero `chat` calls.
- [ ] 2.3 GREEN: `_load_members(bundle_dir, group)` mirrors `answer.py`'s guarded re-read; short-circuit all-unreadable before any `chat` call.

## Phase 3: Prompt And Fail-Closed Parse

- [ ] 3.1 RED: `_build_messages` yields 2-message prompt (system rubric + user turn: OKF type, tier, member title+body).
- [ ] 3.2 GREEN: implement `_build_messages`, mirrors `concept._build_messages`.
- [ ] 3.3 RED: verdict case-insensitive; unknown → UNCERTAIN; confidence clamped [0,1]; malformed reply → UNCERTAIN/0.0 with rationale, never raises.
- [ ] 3.4 GREEN: `_parse_verdict` fail-closed, mirrors `concept._extract_json_items`/`_validate`.

## Phase 4: Core Orchestration

- [ ] 4.1 RED: one result per input group, same order (3-group fixture).
- [ ] 4.2 RED: `OllamaError`-family from `llm.chat` propagates unswallowed.
- [ ] 4.3 RED: determinism — same input + queued replies, two runs equal.
- [ ] 4.4 GREEN: `adjudicate_candidates(candidates, *, bundle_dir, llm)` wires load, prompt, chat, parse.
- [ ] 4.5 Docstring note: "one LLM call per group with readable content"; reconcile spec.md/design.md if trivial.

## Phase 5: CLI Verb

- [ ] 5.1 RED: `adjudicate` wires `require_workspace`, `read_config`, `OllamaClient(model=cfg.model)`, `find_candidates`, `adjudicate_candidates`; prints grouped verdict/confidence/rationale.
- [ ] 5.2 RED: read-only — bundle bytes/mtime unchanged.
- [ ] 5.3 RED: `--same-only` hides non-SAME from output only; library still receives every group.
- [ ] 5.4 RED: 3-tier degrade — `OllamaUnavailable` → `OllamaModelNotFound` → generic `OllamaError`, actionable message, exit 1, zero writes.
- [ ] 5.5 GREEN: add the verb per design's File Changes.

## Phase 6: Layering And Integration Proof

- [ ] 6.1 Extend `test_layering.py`: `resolution` MAY import `openkos.llm`, still forbids `bundle`/`state`/`graph`.
- [ ] 6.2 Read-only demo: `adjudicate_candidates` end-to-end, fake backend, small bundle fixture, zero writes.
- [ ] 6.3 Run `uv run pytest --cov` (≥90% branch), `mypy --strict`, `ruff check`.
