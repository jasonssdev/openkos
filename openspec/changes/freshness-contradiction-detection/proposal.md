# Proposal: Contradiction Detection (S3 of freshness-lint-v1)

## Intent

A bundle can accumulate concepts that assert conflicting facts (e.g. two concepts stating different values for the same attribute). Nothing surfaces these today: `lint` checks only mechanical stamp-age staleness (S1/S2), and no verb reasons about semantic conflict between concepts. S3 adds a read-only, advisory `contradictions` verb that judges already-related concept pairs and flags genuine conflicts for a human to reconcile. Precision is the make-or-break concern — a report that cries wolf is worse than none.

## Scope

### In Scope
- Read-only `contradictions` CLI verb, cloning `adjudicate`'s wiring exactly (workspace gate → `read_config` → `OllamaClient` → engine → ordered 3-tier `OllamaError` handler → grouped render). Zero writes.
- New config-free engine leaf `src/openkos/resolution/contradiction.py` (clones `edge_typing.py`/`adjudication.py`): owns its `build_graph` read, one `llm.chat` per candidate pair.
- Candidate signal = **graph typed-edge pairs only**: every edge with `relation_type is not None`. Symmetric/duplicate pairs deduped to a single judgement via an unordered `frozenset({source_id, target_id})` key.
- Precision controls: verdict vocab `CONTRADICTS`/`CONSISTENT`/`UNCERTAIN`; a required `conflicting_claims` citation (CONTRADICTS without it degrades to UNCERTAIN); high-confidence-CONTRADICTS-only default display with a `--all` flag; a hard pair cap with an explicit "capped" report line (no silent truncation). Threshold and cap defaults pinned in design.

### Out of Scope (deferred)
- Embedding near-neighbor and stamp-divergence candidate signals.
- Enhanced / contradiction-inferred staleness (S1/S2 already cover mechanical/volatility staleness).
- Any write path, auto-reconcile, or config writes (S4).

## Capabilities

### New Capabilities
- `contradiction-detection`: read-only `contradictions` verb + config-free engine leaf that judges typed-edge concept pairs for factual conflict, advisory-only.

### Modified Capabilities
- None.

## Approach

Mirror the proven `edge_typing.suggest_relations` pattern one layer over. The engine `find_contradictions(bundle_dir, *, llm)` opens `build_graph` internally, derives deduped typed-edge pairs (cap-bounded), loads each concept body via the guarded `_load_doc` re-read, and issues one `llm.chat` per pair. Parsing reuses `adjudication.py`'s fail-closed machinery verbatim: 3-step JSON extraction, case-insensitive verdict map (unknown → UNCERTAIN), `_coerce_confidence` clamp to `[0,1]`. A CONTRADICTS reply lacking a non-empty `conflicting_claims` field degrades to UNCERTAIN. Per-pair parse failures degrade that pair only; `OllamaError`-family exceptions propagate unswallowed. The CLI never imports `openkos.graph` (engine owns the read).

**Precision strategy (first-class):** false positives are the primary risk, so three levers compound — (1) candidate set restricted to already-related typed edges (few, high-signal pairs, not O(n·k) neighbors), (2) the LLM must cite the specific conflicting claims or the verdict is downgraded, (3) only high-confidence CONTRADICTS surface by default. No seeded `contradicts` relation type is needed: we check *all* typed edges as topical pairs, not conflict-typed edges.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/resolution/contradiction.py` | New | Config-free engine leaf: candidate pairs + fail-closed judging |
| `src/openkos/cli/main.py` | Modified | New `contradictions` verb (clone of `adjudicate` wiring) |
| `openspec/specs/contradiction-detection/spec.md` | New | Capability spec |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| False-positive flood (primary) | Med | Typed-edge-only candidates + required claim citation + high-confidence-only default display |
| LLM cost / pair explosion | Low | Hard pair cap with explicit "capped" report line; typed edges are few |
| Empty-graph bundle (no relations) | Med | Empty candidate set → clear "No contradiction candidates found." message, never a crash |
| No `contradicts` relation seeded | N/A | Not needed — all typed edges are candidates, confirmed against `model/relations.py` |

## Rollback Plan

Fully reversible with zero data impact. Revert the two source files and the new spec (`git revert` of the change PR). Because the verb performs zero writes and adds no persisted schema, no migration, cleanup, or workspace repair is required.

## Dependencies

- Ollama available for live use (degrades gracefully via the 3-tier handler when absent).
- Existing `openkos.graph.build_graph` typed-edge projection (already shipped, S2b).

## ADR Evaluation

**Verdict: No new ADR.** Two-limb gate: (1) no hard-to-reverse or cross-cutting architectural commitment — a read-only advisory verb + isolated config-free engine leaf, `git revert`-reversible; (2) no data-model, schema, or public-contract change — zero persistence, no config writes. The confidence threshold and pair cap are module-level tuning constants (design-pinned), not schema; even if later promoted to a config knob, a tuning value is not ADR-worthy.

## Success Criteria

- [ ] `contradictions` verb runs read-only against a workspace with zero file writes.
- [ ] Only deduped typed-edge concept pairs are judged; symmetric/duplicate pairs judged once.
- [ ] CONTRADICTS verdicts cite specific conflicting claims; uncited CONTRADICTS degrades to UNCERTAIN.
- [ ] Default display shows high-confidence CONTRADICTS only; `--all` shows every verdict.
- [ ] Pair cap enforced and reported when hit; empty-graph bundle yields a clear message, not a crash.
- [ ] Ollama-absent runs degrade via the ordered 3-tier handler, exit 1, zero writes.
