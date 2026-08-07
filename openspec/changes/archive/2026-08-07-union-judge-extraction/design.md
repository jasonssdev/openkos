# Design: Union-of-Runs + Selector Judge Replaces the Blind Extraction Cap

## Technical Approach

Exploration approach 3. `extract_concept` stays the untouched single-run primitive (evals, `run_spike.py`). A sibling orchestrator `extract_concept_union` in the SAME module composes `_extract_once` runs → per-run twin-drop → richer-body merge → pre-judge ceiling → judge → deterministic `Procedure` re-admission → backstop cap LAST. A new leaf module owns the judge's prompt/parse/validate. `main.py` selects the function from a config flag.

## Architecture Decisions

| # | Decision | Alternatives rejected | Rationale |
|---|---|---|---|
| D1 | Orchestrator lives in `extraction/concept.py` as `extract_concept_union(source_text, *, source_title, llm)` — same signature as `extract_concept` | Sibling `extraction/union.py` | It needs `_extract_once`, `_drop_source_title_twins`, `_normalize_title`, `_dedup_merged`, the report types — all `_`-private. The project forbids cross-importing `_`-prefixed symbols (`llm/parsing.py` docstring); the alternative is publicizing five internals to save ~120 lines. Cohesion wins over line count. |
| D2 | Judge is a NEW leaf module `extraction/judge.py`, mirroring `retrieval/answer.py`: `_JUDGE_SYSTEM_PROMPT`, `_build_judge_messages`, `select(...) -> tuple[str, ...] \| None` | Judge prompt inside `concept.py` | `_SYSTEM_PROMPT` must not grow (A/B-proven regression). No cycle: judge takes/returns plain strings via its own `JudgeCandidate(type, title, description)`, never imports `concept`. |
| D3 | Reply shape `{"keep": ["<exact title>", ...]}` parsed by `parsing.extract_json_object` | JSON array of titles | `extract_json_items` keeps only *dict* elements, so a bare string array is unparseable by the shared layer. An object reuses `llm/parsing.py` verbatim — no new parsing code. |
| D4 | Closed-set enforcement and title matching happen in `concept.py` via `_normalize_title`, not in the judge | Judge does its own matching | One title-normalization function is a standing invariant (`_normalize_title` docstring). Judge titles not matching any candidate are dropped silently. |
| D5 | `Procedure` exemption is a deterministic POST-filter, never a prompt clause: rebuild the kept list by filtering merged candidates in order on `title in selected OR type == _TWIN_EXEMPT_TYPE` | Prompt rule "never drop a Procedure" | #413 + the 5.5-5.6 priming evidence: a prompt-carried version of this rule made the defect worse. A filter predicate also keeps ONE deterministic output order (merged order). |
| D6 | Richer-body merge `_merge_union`: key `(type, _normalize_title(title))`; on collision keep the whole `ExtractionResult` with the longer `body`, tie → longer `description`, tie → first. Output position = first occurrence of the key | `_dedup_merged` keep-first | Keep-first would discard run 2's fuller body. Whole-object swap avoids field-mixed Frankenstein objects. |
| D7 | Judge failure is total: `select()` catches every exception from its own `llm.chat` and returns `None` | Let it propagate; catch in the orchestrator | The judge is an optional refinement — its failure must never destroy validated extraction work. Extraction failures still propagate unswallowed (module contract). The broad catch sits in ONE named place with the rationale in its docstring. Empty/unparseable/`None` selection ⇒ same fail-closed path. |
| D8 | `_MAX_JUDGE_CANDIDATES = 24` applied after merge, before the judge; `_UNION_BACKSTOP = 12` applied ONCE after re-admission. Both internal constants | Reuse `_MAX_OBJECTS_PER_SOURCE = 6` | 12 never binds on a measured genuine set (7 unchunked, 9 on TS3005b); 24 = 2×backstop, bounds judge prompt growth on ~10-chunk sources. `_MAX_OBJECTS_PER_SOURCE` stays for the single-run path. |
| D9 | `_stage_derived_objects` gains `union_judge: bool = False`; the CLI passes `cfg.union_judge` explicitly | Required keyword-only | The product ON default belongs in `config.DEFAULT_UNION_JUDGE` alone, not duplicated. 39 existing test call sites keep exercising the untouched single-run path as a regression guard. |

**ADR gate: NO ADR.** Condition (1) holds — this decides a pattern and a trade-off. Condition (2) does NOT: the opt-out flag is a one-line rollback (flip `DEFAULT_UNION_JUDGE` to `False` → byte-identical to today), the modules are additive and deletable, and nothing about the on-disk OKF format, sensitivity model, or public CLI contract changes. BOTH conditions are required; recorded here instead.

## Data Flow

Unchunked (`len <= _CHUNK_THRESHOLD`), 3 chat calls:

    _extract_once ──┐
    _extract_once ──┤ (each twin-dropped on its OWN output)
                    ↓
               _merge_union  ──→ ceiling(24) ──→ judge.select
                                                    │
                        Procedure re-admission ←────┘
                                    ↓
                           backstop(12) ──→ ExtractionOutcome

Chunked (`> _CHUNK_THRESHOLD`), `chunks + 1` calls — judge-only, no second pass:

    _extract_once × N chunks ──→ _dedup_merged ──→ twin-drop
        ──→ ceiling(24) ──→ judge.select ──→ Procedure re-admit ──→ backstop(12)

Sequence (unchunked):

    main._stage_derived_objects   concept.extract_concept_union   judge.select   llm
      │  cfg.union_judge is True          │                          │           │
      ├──────────────────────────────────>│  run 1                   │           │
      │                                   ├──────────────────────────────────────>│
      │                                   │  run 2                   │           │
      │                                   ├──────────────────────────────────────>│
      │                                   │  merge + ceiling         │           │
      │                                   ├─────────────────────────>│  1 call   │
      │                                   │                          ├──────────>│
      │                                   │  titles | None (failed)  │           │
      │                                   │<─────────────────────────┤           │
      │  ExtractionOutcome + report       │  re-admit + backstop     │           │
      │<──────────────────────────────────┤                          │           │
      │  notices → stderr; exit 0 either way                                     │

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/extraction/judge.py` | Create | `JudgeCandidate`, `_JUDGE_SYSTEM_PROMPT`, `_build_judge_messages`, `_validate_selection`, `select()` |
| `src/openkos/extraction/concept.py` | Modify | `extract_concept_union`, `_merge_union`, `_MAX_JUDGE_CANDIDATES`, `_UNION_BACKSTOP`, 4 new `ExtractionReport` fields |
| `src/openkos/config.py` | Modify | `DEFAULT_UNION_JUDGE = True`, `Config.union_judge: bool`, `is not None` + `isinstance(bool)` guard |
| `src/openkos/cli/main.py` | Modify | flag branch in `_stage_derived_objects`; `_judge_failure_notice`, `_judge_selection_notice` |
| `openkos.yaml.template` | Modify | document `union_judge:` |
| `tests/unit/extraction/test_judge.py` | Create | judge unit suite |
| `tests/unit/extraction/test_concept.py`, `tests/unit/cli/test_ingest.py`, `tests/unit/test_config.py` | Modify | union/failure/backstop/flag cases |
| `evals/extraction_cap/run_cap_eval.py` | Modify | before/after arm |

## Interfaces / Contracts

```python
# extraction/judge.py — leaf: llm.base, llm.parsing, stdlib only.
@dataclass(frozen=True)
class JudgeCandidate:
    type: str
    title: str
    description: str

def select(
    source_text: str, candidates: Sequence[JudgeCandidate], llm: LLMBackend
) -> tuple[str, ...] | None:
    """Titles the judge keeps, echoed verbatim from the closed candidate list.
    `None` == unusable (raised, unparseable, wrong shape, or empty) -> caller
    fails closed to the whole candidate set. Never raises."""

# extraction/concept.py — ExtractionReport gains (all defaulted, so every
# existing construction site keeps working, per the `chunks: int = 1` precedent):
runs: int = 1                      # full passes over the source: 2 unchunked union, 1 chunked
judge_status: str = "skipped"      # "skipped" | "ok" | "failed"
judged_out_titles: tuple[str, ...] = ()
pre_judge_dropped: int = 0         # candidates cut by the 24 ceiling
```

`produced`/`retained`/`discarded_titles` keep their exact meaning — the count entering and leaving the FINAL cap — so `_extraction_cap_notice` renders unchanged and never blames the cap for a judge or ceiling drop.

## Testing Strategy (STRICT TDD — RED first; tasks phase orders these)

`_SequencedLLM` (test_concept.py:1720) covers per-call differing replies and mid-sequence exceptions; no new fixture infrastructure.

| Layer | Cases |
|---|---|
| `judge.py` | prompt embeds source + every candidate title verbatim; valid `{"keep": [...]}` → titles in reply order; non-JSON / missing `keep` / non-list / non-string elements / empty list → `None`; `llm.chat` raises → `None`, nothing escapes |
| `concept.py` union | 3 calls with identical extraction messages; run-2-only object survives (recall claim); richer body wins collision; description tie-break; both-equal → first; twin-drop is PER RUN; judged-out title absent from objects and named in `judged_out_titles`; judge-rejected `Procedure` re-admitted and NOT named; judge raises → all candidates kept + `judge_status == "failed"`; garbage/empty judge → same; extraction run 2 raises → propagates; backstop binds only above 12 (`produced > retained`, `discarded_titles`); chunked → exactly `chunks + 1` calls, `runs == 1`, no second pass; >24 merged → judge sees 24, `pre_judge_dropped` counts the rest |
| `concept.py` regression | existing `extract_concept` suite green untouched; `_SYSTEM_PROMPT` byte-identical |
| `config.py` | key absent → `True`; `union_judge: false` survives (`is not None`, not truthiness); non-bool → guarded error |
| `cli` | flag off → `extract_concept`, 1 chat call; flag on → union path; judge failure → stderr notice, exit 0, objects still staged; judged-out titles rendered; ceiling suffix; extraction `OllamaError` still degrades to `skip_reason="failed"` |

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or process-integration boundary. The change adds an LLM call inside an existing, already-gated `llm.chat` seam; the sensitivity gate ahead of it is untouched.

## Migration / Rollout

No data migration. Default ON via `DEFAULT_UNION_JUDGE = True`; rollback is flipping that constant to `False`. Derived objects are reconstructible; `raw/` untouched.

## Open Questions

- [ ] `_MAX_JUDGE_CANDIDATES = 24` is reasoned (2× backstop, above the 9-object measured max), not measured. Eval should confirm it never binds on the corpus.
- [ ] Judge prompt wording is unmeasured; the eval gate in the proposal's success criteria is the only check on judge permissiveness.
