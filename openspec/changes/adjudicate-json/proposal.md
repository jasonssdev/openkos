# Proposal: adjudicate --json (machine-readable verdicts)

Issue #137, Slice 2a of the #139 -> #137 arc.

## Intent

`adjudicate` prints human-only text today (tally, legend, per-group verdict,
`Next:` hint). A user or wrapper cannot pipe its verdicts into `openkos merge`
without scraping prose. Slice 2a adds a `--json` flag that emits the verdicts as
structured data on stdout so the SAME pairs can be fed downstream. This is
non-destructive, unblocks scripting/CI now, and DEFINES the codebase's first
`--json` convention (zero precedent today; `json` is not even imported in
`cli/main.py`).

## Scope

### In Scope
- Additive `--json` flag on `adjudicate`.
- Emit ONLY valid JSON to stdout when set; suppress all human output.
- Serialize already-computed `results`; no verdict-logic change.

### Out of Scope (future slices)
- Interactive apply (`--apply`, per-pair confirm) — Slice 2b; needs a
  merge-orchestration extraction + a survivor/absorbed rule (neither exists).
- Guarded/unattended batch apply (`--apply-same`) — deferred pending #138
  (confidence is uncalibrated; `verdict==SAME` is the only trustworthy gate).
- Any destructive action; changes to `adjudicate_candidates`, `merge`, ledger.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `entity-resolution-adjudication`: add machine-readable `--json` output mode
  (additive; human path unchanged when flag absent).

## Approach

Add `json_output: bool` Typer option. On the SUCCESS path, when set, build a
list of dicts from `results` (results order, stable) and `typer.echo(
json.dumps(payload, indent=2))` — no tally, legend, per-group text, or `Next:`
hint. Import `json` (first use in the file). Error handlers unchanged.

### LOCKED product decisions
1. **Confidence: EXCLUDED.** Consistent with #138 — the local model returns a
   flat, uncalibrated value; a JSON number revives misleading precision for
   machine consumers just as a two-decimal string does for humans. Kept on the
   dataclass for future thresholding, not exposed.
2. **Schema — one object per group, exact fields:**
   ```json
   [
     {
       "member_ids": ["concept-a", "concept-b"],
       "okf_type": "person",
       "tier": "HIGH",
       "verdict": "SAME",
       "rationale": "Same individual; identical canonical name and role."
     }
   ]
   ```
   No `survivor`/`absorbed` field — no heuristic exists; the consumer decides.
   `tier` and `verdict` are UPPERCASE strings.
3. **`--same-only` + `--json`:** `--json` emits the FULL array by default so
   consumers filter on `verdict`. If `--same-only` is ALSO passed, filter the
   emitted array to SAME-only (composability).
4. **Empty state:** emit `[]` (valid empty array), never "No candidates found."
5. **Formatting:** pretty-printed, `indent=2`; deterministic results order.
6. **Errors:** Ollama-unavailable / model-not-found / generic handlers
   unchanged — stderr + exit 1, never on the JSON stdout.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/cli/main.py` (adjudicate, ~3632-3780) | Modified | `--json` flag, `json.dumps` branch, human-output suppression, docstring |
| `import json` in `cli/main.py` | New | First structured-output use |
| `tests/unit/cli/test_adjudicate.py` | Modified | JSON shape, empty `[]`, `--same-only` filter, non-json byte-parity |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| First `--json` sets precedent for future commands | High | Lock exact schema/flag semantics here; document as the convention |
| Regressing existing human output | Med | Flag is additive; assert byte-identical non-json path in tests |
| Non-deterministic array order breaks scripts | Low | Emit in `results` order; `member_ids` already sorted |
| Consumers expecting `confidence` | Low | Documented exclusion with #138 rationale |

## Rollback Plan

Revert the single commit. The flag is purely additive; removing it restores the
prior read-only `adjudicate` with no data or ledger impact.

## Dependencies

- None. Independent of #138 and of any merge-core extraction.

## Success Criteria

- [ ] `adjudicate --json` emits valid JSON (array of group objects) to stdout, nothing else.
- [ ] Empty candidate set emits `[]`.
- [ ] `--json --same-only` filters the array to SAME verdicts.
- [ ] `confidence` never appears in output.
- [ ] Without `--json`, human output is byte-identical to today.
- [ ] Error paths still write to stderr with exit 1.
