# Proposal: transcript-decision-pass — one Decision per decision a meeting reaches

Refs #1348. Related: #1014 (decision evolution), #1231, #1318.

## Intent

On a raw multi-speaker transcript the general extraction pass returns a few
topic-bundle Decisions whose body is a pasted transcript excerpt. On AMI
ES2015 a-d it produced 4 Decisions against 29 hand-annotated decision
sentences, and `openkos revisions` found no related pair. Decision evolution
needs decision-level objects: what was decided (subject + value) and one
verbatim evidence line.

This change adds an opt-in, dedicated decision pass for meeting-shaped
sources.

## Scope

### In scope

- A config key `transcript_decisions` (boolean, default `false`). While it is
  off, ingest output is byte-identical to today.
- When it is on and the source is meeting-shaped, a decision pass runs over
  each chunk window (or over the whole text when the source is under the
  chunk threshold) and returns items `{subject, value, evidence}`.
- Fail-closed admission: an item whose evidence is not verbatim in its window
  is dropped; items are deduplicated by normalized subject; a per-source cap
  bounds the pass's Decisions.
- Each admitted item becomes a Decision whose body is composed
  deterministically from the value and the quoted evidence. No transcript
  excerpt is carried.
- Replacement rule: when the pass yields at least one Decision for a source,
  those replace the main pass's Decision candidates for that source. When it
  yields none, or fails (backend error, unparseable reply), the main pass's
  Decisions are kept unchanged and the failure is reported, never raised.

### Out of scope

- Flipping the default. That is a later change, gated by a pre-registered
  measurement (per-decision recall on adjudicated AMI ground truth).
- Decision revision pairing (#1349, #1350).

## Why a separate pass

A prompt-sentence alternative (asking the general prompt for finer Decisions)
was already measured and rejected in `evals/decision_granularity/`: +0.13
against a 0.25 bar. A dedicated pass asks one narrow question per window and
admits only evidence that survives a deterministic check.

## Domain

The delta lands in `extraction-union-judge`: the pass is gated by the same
meeting-shaped predicate as the participant capture and judge re-admission
already specified there, and it runs inside the union path.

## Approach and slices (stacked to main)

1. This proposal and delta spec, plus the inert core (prompt, parsing,
   verbatim filter, dedup, cap, body composition), reachable from nowhere.
2. The config key and wiring into the union path, with the template and docs.
3. The measurement harness, with `--self-test`.

The key ships off, so every slice is mergeable on its own.

## Principles impact

- Local-first: one extra local LLM call per window, only on opt-in.
- Provenance: every Decision carries a verbatim evidence line.
- Representation, not truth: the pass records what the transcript says was
  decided; it does not validate it.
- Human curates: opt-in, off by default.

## Rollback

Leave the key off. Reverting the wiring slice removes the surface; the core
is dormant code.
