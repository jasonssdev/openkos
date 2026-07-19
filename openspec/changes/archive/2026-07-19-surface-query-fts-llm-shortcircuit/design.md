# Design: Surface the FTS -\> LLM short-circuit in `openkos query`

## Technical Approach
`answer()` computes retrieval metadata (raw hit count, whether the LLM ran, the
no-match cause, build-time skip notices) and returns it on an enriched
`AnswerResult`. `main.py` renders it: a uniform one-line summary + skip notices
to **stderr** every run, and a cause-specific message to **stdout** on no-match.
Layering preserved — `answer.py` returns data only (no config import, no
CLI-flavored prose beyond the stable `NO_MATCH`); `main.py` owns all rendering,
mirroring `doctor`/`status`.

## Architecture Decisions

### Decision: no_match_cause type — `Literal`, not `enum.Enum`
**Choice**: `NoMatchCause = Literal["none", "empty_query", "zero_hits", "all_unreadable"]` type alias.
**Alternatives**: `enum.Enum`/`StrEnum` (proposal said "enum" loosely).
**Rationale**: The codebase's existing closed-set convention is `Literal` (`CheckResult.status: Literal["pass","fail","skip"]`, main.py:800). Matching it avoids a new pattern and keeps `answer.py` stdlib-light. Layering test stays green.

### Decision: new AnswerResult fields are required (no defaults)
**Choice**: add `fts_hit_count: int`, `llm_invoked: bool`, `no_match_cause: NoMatchCause`, `skip_notices: list[str]` — all required.
**Alternatives**: give defaults to reduce test churn.
**Rationale**: `answer()` is the sole producer and always computes them; required fields make every test document retrieval reality and prevent silent-default bugs. Strict TDD absorbs the constructor churn.

### Decision: `cited_count` derived, not stored
**Choice**: render `len(result.citations)`; do NOT add a `cited_count` field.
**Rationale**: denormalized count could drift from `citations`; `fts_hit_count` (raw hits) is NOT derivable and is the only new count worth storing.

### Decision: cause-specific prose lives in `main.py`, not `answer.py`
**Choice**: `answer.answer` stays the stable `NO_MATCH`; `main.py` maps `no_match_cause` -> actionable stdout string.
**Rationale**: preserves render-free/config-free `answer.py`; CLI-flavored, `lint`-referencing guidance belongs with the other CLI messages.

### Decision: stderr summary carries no `openkos query:` prefix
**Rationale**: that prefix is the ERROR/refusal namespace; an informational line must not read as an error. Use a `retrieval:` label like `status`'s `Sources:`.

## Data Flow
    build_index -> hits ─┐                    (stderr) retrieval summary + skip notices
                         ├─ answer() -> AnswerResult ─ main.py render ┤
    _assemble_context ───┘  (+metadata)                              └ (stdout) answer+citations | cause msg

`answer()` reads `index.skipped` INSIDE the `with` block. `fts_hit_count=len(hits)`;
`_classify_no_match(question, hits)`: `not question.split()`->`empty_query`; `not hits`->`zero_hits`; else->`all_unreadable`. Success: `llm_invoked=True`, cause `none`.

## Interfaces / Contracts
```python
NoMatchCause = Literal["none", "empty_query", "zero_hits", "all_unreadable"]

@dataclass(frozen=True)
class AnswerResult:
    answer: str
    citations: list[Citation]
    fts_hit_count: int
    llm_invoked: bool
    no_match_cause: NoMatchCause
    skip_notices: list[str]
```

### Exact stderr strings (`typer.echo(..., err=True)`)
Summary (uniform, every run; `{s}`="" if count==1 else "s"):
`retrieval: {n} FTS hit{s} → LLM {invoked|skipped} → {m} source{s} cited`
- match: `retrieval: 3 FTS hits → LLM invoked → 2 sources cited`
- zero_hits/empty_query: `retrieval: 0 FTS hits → LLM skipped → 0 sources cited`
- all_unreadable: `retrieval: 2 FTS hits → LLM skipped → 0 sources cited`

Skip notices (only when non-empty; header then existing `{cid}.md: skipped ({reason})` lines):
`index: {k} doc{s} skipped while building the search index (whole-bundle, not this query's hits):`
`  concepts/foo.md: skipped (unreadable)`

### Exact stdout no-match messages (`typer.echo(...)`, exit 0)
- zero_hits: `No matching concepts were found in the compiled bundle for this question. Try different wording, or run `openkos status` to see what the bundle contains.`
- all_unreadable: `Found {n} matching concept{s}, but none could be read from the compiled bundle — it may be corrupted. Run `openkos lint` to check bundle health.`
- empty_query: `No question was provided. Pass a question to answer, e.g. openkos query "what is stoicism?".`

Render order: stderr summary + skip notices FIRST, then stdout answer/message. Success stdout unchanged (answer, then `Citations:` block).

## File Changes
| File | Action | Description |
|------|--------|-------------|
| `src/openkos/retrieval/answer.py` | Modify | Add `NoMatchCause`, 4 fields, `_classify_no_match`, read `index.skipped`, set fields on both returns |
| `src/openkos/cli/main.py` | Modify | `_plural` helper, stderr summary + skip-notice block, cause->stdout message map in `query` |
| `tests/unit/retrieval/test_answer.py` | Modify | Update constructors + `_RecordingIndex.skipped`; assert new fields; add empty_query + skip-notice tests |
| `tests/unit/cli/test_query.py` | Modify | Update fake `AnswerResult` constructors; assert stderr summary; 3 cause stdout messages; skip-notice stderr |
| `docs/cli.md` (~84) | Modify | Replace "single no-match line"; note always-on stderr summary + 3 cause messages |
| `docs/user-journey.md` (~134-146) | Modify | Keep stdout example; add brief note a retrieval summary prints to stderr |

## Testing Strategy
| Layer | What | Approach |
|-------|------|----------|
| Unit (answer) | metadata per path | direct `answer()` calls assert `fts_hit_count`/`llm_invoked`/`no_match_cause`/`skip_notices`; `_RecordingIndex` gains `skipped` |
| Unit (CLI) | stream separation | `CliRunner` already splits `result.stdout`/`result.stderr` (Click 8.2 default; existing tests prove it) — no `capsys` needed |
| Layering | config-free | `test_answer_module_does_not_import_config` stays green (`Literal`/`enum` add no `config` import) |

Breaking (update under TDD): test_query no-match (bare `NO_MATCH` -> cause msg + stderr), matching (add stderr assert), all fake-result constructors; test_answer frozen-dataclass + behavior constructors. New: empty_query cause, skip-notice surfacing, hit-vs-cited discrepancy, CLI 3-cause + skip-notice stderr + clean-stdout tests.

## Threat Matrix
N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or process-integration boundary. Pure dataclass + stdout/stderr rendering.

## Migration / Rollout
No migration required.

## Open Questions
None.
