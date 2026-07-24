# Design: adjudicate --json (machine-readable verdicts)

Issue #137, Slice 2a. Additive `--json` flag on `adjudicate`; non-json path stays byte-identical. Verified against real source, not anchors.

## Technical Approach

Add a Typer boolean option `json_output` bound to `"--json"`. After `results`
is produced (and after the three Ollama error handlers), a short-circuit branch
serializes `results` to JSON on stdout and returns, suppressing ALL human
output. A pure helper builds the payload; `json.dumps` + `typer.echo` stay at
the I/O boundary. No change to verdict logic, `adjudicate_candidates`, merge, or
ledger.

## Verified attribute paths (source-checked)

| JSON field | Source | Render | Evidence |
|---|---|---|---|
| `member_ids` | `CandidateGroup.member_ids` | `list(group.member_ids)` (sorted tuple → list) | candidates.py:63-66 |
| `okf_type` | `CandidateGroup.okf_type` (str) | as-is | candidates.py:61 |
| `tier` | `CandidateGroup.tier` (`Tier` enum) | **`group.tier.name`** → "HIGH"/"LOW" | candidates.py:41-48, 67 |
| `verdict` | `AdjudicatedCandidate.verdict` (`Verdict` enum) | **`result.verdict.value.upper()`** → "SAME"/"DIFFERENT"/"UNCERTAIN" | adjudication.py:70-79, 89 |
| `rationale` | `AdjudicatedCandidate.rationale` (str) | as-is | adjudication.py:93 |
| `confidence` | exists (`float`) | **OMITTED** | adjudication.py:91 |

`result.candidate` is the `CandidateGroup` (adjudication.py:87). Group fields are
reached via `result.candidate.<field>`.

### MISMATCH ALERT — enum rendering
`Tier.value` is `"high"/"low"` (lowercase); the human path uses the ternary
`"HIGH" if group.tier is Tier.HIGH else "LOW"` (main.py:3769). To emit the
proposal's UPPERCASE `tier`, use `.name` — NOT `.value`. `Verdict.value` is
`"same"/…`; the human path already uppercases via `.value.upper()` (main.py:3777),
so mirror that exactly for `verdict`. Using `.value` for `tier` would silently
produce lowercase and break the locked schema.

## Architecture Decisions

### Decision: Pure payload builder separate from I/O
**Choice**: `_adjudication_payload(results, *, same_only) -> list[dict]`; command
does `typer.echo(json.dumps(_adjudication_payload(results, same_only=same_only), indent=2))`.
**Alternatives**: helper returns the final `str` (couples structure to
formatting); inline dict-comp in the command (untestable without stdout parse).
**Rationale**: mirrors the existing pure `_format_verdict_tally` seam; a
`list[dict]` return lets unit tests assert structure and key-set directly, and
keeps `json.dumps`/indent owned by the command.

### Decision: Branch placement after error handlers, before human output
**Choice**: insert the `if json_output:` branch immediately after the
`except OllamaError` block (main.py:3743) and BEFORE the workspace echo
(main.py:3745).
**Alternatives**: branch at function top (would skip adjudication);
branch after the empty-guard (would print prose then JSON).
**Rationale**: the three Ollama handlers (main.py:3721-3743) must still run so a
degraded run exits 1 on stderr with no JSON. All human lines — workspace echo,
`"No candidates found."` guard (3747-3749), `--same-only` empty guard
(3754-3756), tally, legend, loop, `Next:` — sit AFTER 3745 and are skipped by the
`return`.

### Decision: `--same-only` composes inside the builder; empty → `[]`
**Choice**: builder filters `[r for r in results if not same_only or r.verdict is Verdict.SAME]`
(same predicate as main.py:3752), then maps to dicts. Empty input or empty filter
yields `[]`.
**Rationale**: single tested filter seam; `[]` is always parseable, replacing
both prose guards for JSON consumers.

## Data Flow

    find_candidates → adjudicate_candidates → results
                                                 │
                          (Ollama errors → stderr, exit 1)   ← unchanged
                                                 │
                   json_output? ──yes──▶ _adjudication_payload(results, same_only)
                                                 │              → list[dict]
                                                 │         json.dumps(indent=2) → stdout → return
                                                 └──no──▶ existing human render (byte-identical)

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/cli/main.py` | Modify | add `import json` (stdlib group, before `import re`); add `json_output` option; add `_adjudication_payload` helper; add short-circuit branch; update docstring (drop "no `--json`" claim at 3659) |
| `tests/unit/cli/test_adjudicate.py` | Modify | JSON shape, field-set incl. `confidence` absent, `--same-only` filter, empty→`[]`, error-path-still-stderr, non-json byte-parity |

## Interfaces / Contracts

```python
json_output: bool = typer.Option(
    False, "--json",
    help="Emit adjudication verdicts as JSON to stdout; suppress human output.",
)

def _adjudication_payload(
    results: Sequence[AdjudicatedCandidate], *, same_only: bool
) -> list[dict]:
    return [
        {
            "member_ids": list(r.candidate.member_ids),
            "okf_type": r.candidate.okf_type,
            "tier": r.candidate.tier.name,            # NOT .value
            "verdict": r.verdict.value.upper(),
            "rationale": r.rationale,
        }
        for r in results
        if not same_only or r.verdict is Verdict.SAME
    ]
```

Dict key order is insertion order (Python 3.7+); `member_ids` pre-sorted →
deterministic output in `results` order.

## Testing Strategy (Strict TDD — RED first)

| Layer | What | Approach |
|---|---|---|
| Unit | payload shape + field set | `json.loads(result.stdout)`; assert keys == {member_ids, okf_type, tier, verdict, rationale}; assert `"confidence"` NOT in every object |
| Unit | enum rendering | assert `tier` in {"HIGH","LOW"}, `verdict` in {"SAME","DIFFERENT","UNCERTAIN"} (guards `.name`/`.value.upper()`) |
| Unit | `--json --same-only` | monkeypatch mixed verdicts; assert emitted array is SAME-only |
| Unit | empty → `[]` | fresh bundle; `json.loads(result.stdout) == []`; no "No candidates found." |
| Unit | error path | Ollama-unavailable still writes stderr, exit 1, stdout has no JSON |
| Unit | byte-parity | run without `--json`; stdout byte-identical to pre-change golden |

Seams (verified): `runner = CliRunner()` with separated `result.stdout`/`result.stderr`
(test uses `result.stderr` at line 153); `_adjudicated(group, verdict=…)` helper
(lines 91-100) builds `AdjudicatedCandidate`; `monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake)`
(line 249) injects results; `CandidateGroup(...)` constructed inline (lines 274+).

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file
classification, or process-integration boundary. Change is stdout serialization
of already-computed in-memory data.

## Migration / Rollout

No migration. Purely additive; revert the single commit to remove.

## Open Questions

- None. All object attributes and enum renderings verified against source.
