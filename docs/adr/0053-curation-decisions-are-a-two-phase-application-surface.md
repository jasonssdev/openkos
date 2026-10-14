---
type: Decision
title: "ADR-0053: Curation decisions are an application surface applied through a two-phase preview and commit"
description: Open curation decisions are listed across all six queue kinds and applied one item at a time by decision_key and a version token re-validated inside the commit section; consent and bulk eligibility are data, identity merges are never bulk-eligible, and the surface never computes a judgment.
status: Proposed
date: 2026-10-14
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-14T00:00:00Z
sensitivity: public
---

# ADR-0053: Curation decisions are an application surface applied through a two-phase preview and commit

- **Status:** Proposed
- **Date:** 2026-10-14

## Context

The pending queue (ADR-0037) already holds every open curation decision as durable data in `findings.db`: one open row per `decision_key`, each with a `payload_digest` over its payload and input digests. Acting on a row is possible only through the interactive `curate` walk in `cli/curate.py`. The per-item write sequence is inlined in three walks and interleaved with prompts, `typer.echo` and `typer.Exit`:

1. member presence;
2. the drift re-check against the bytes read before the prompt;
3. catalog recomposition;
4. read-dependency drift;
5. the write core;
6. the auto-commit.

MVP 6's desktop app is the first client of the stable Python API (ADR-0039). It needs to list what is waiting for the human and apply one answer to one item without importing `openkos.cli`.

The forces:

- **No lock across human time.** ADR-0036 holds the workspace lock only for a short commit phase, never across a prompt. The CLI survives that because it keeps its prepared plan, and so its byte baselines, in memory while the human reads it. A stateless caller cannot. Re-preparing at apply time would silently absorb any change made while the human looked, so the human would approve one plan and get another.
- **The row id is not a handle.** `findings.db` is a derived cache (reconstructibility). Its autoincrement id is not stable, and `decision_key` is.
- **Consent must not drift.** A merge deletes a concept. Spend may be consented in bulk (`--auto`, `gate()`), and so may some writes (`--accept`, `review: false`, `a`), but identity merges never are. The only exceptions are the separately specified accept-recommended answer (#1298) and the measured auto-merge class (ADR-0049). Today that rule lives in `Stage.auto_acceptable` and in which prompt helper a walk happens to call.
- **Judging is a separate concern.** An identity row can carry `adjudication: null`, because the daemon's stage is model-free. Deciding such a row needs a judge, and the judge has its own cost gate.
- **The layering invariant.** `application/` may not import `openkos.cli`, `typer`, `rich` or `openkos.vcs`. The auto-commit, the lock ContextVar and the reconciliation model call all live adapter-side.
- **The API is not frozen yet.** ADR-0039 puts the stable-API freeze in MVP 6's API stage, so this interface must be real enough to be shaped by a client, but not declared stable.

## Decision

We make curation decisions an `application/` surface, `application/curate_decisions.py`, that **lists** open decisions and **applies one action to one item**. It never prompts, prints, reads a TTY, raises `typer.Exit` or calls a model to compute a decision. The CLI `curate` walk is its first client, with byte-identical output.

1. **Listing covers all six queue kinds.** Every item carries an action set. Identity, relation-type and volatility items carry actions. Contradiction, revision and watch-refusal items list with no actions and name the command that resolves them. Only identity items judged the same are mergeable. An unjudged identity item is listed as pending evaluation, with merge withheld. Stale rows are marked, not hidden.
2. **Applying is two-phase.** A preview returns the prepared plan. A commit takes that preview.
   - The in-process caller (the CLI) commits against the baselines it holds, exactly as before, so its human-time window is still guarded by the drift check (exit 3).
   - A stateless caller commits against a **version token**: the `decision_key`, the row's `payload_digest`, and a digest of the plan's byte baselines.
     - The plan is re-previewed and compared before any model call.
     - The row is re-read and compared **inside the commit section**, where every queue writer also serializes.
     - A mismatch returns a typed `Stale` outcome and writes nothing.
     - A change that races the commit itself is the ordinary, retryable drift refusal.
3. **Outcomes are typed.** Outcomes that let a walk continue are returned: applied, not applied (skipped, member missing, not mergeable or not offered), and stale. Refusals that end a run are raised: drift, and a write failure that carries structured fields rather than CLI text.
4. **Effects are ports.** The auto-commit, the commit section, the reconciliation model call and the config-commit plan are ports the adapter supplies. The preview exposes whether reconciliation is planned, so the call is part of what the human approves.
5. **Consent and bulk eligibility are data.**
   - A per-kind table records bulk eligibility: identity never, relation types for symmetric types only, volatility always. The kinds without actions are never bulk-eligible.
   - The spend gate, the write-walk refusal and the per-item bulk answer call pure consent functions.
   - Accept-recommended and auto-merge keep their own, separately specified consent and stay outside the generic table.
6. **The surface is internal and owner-local.** It returns payload text that may derive from confidential concepts, so no `mcp/` or `api/` adapter imports it without its own disclosure gate. It is not the stable API.

This applies ADR-0018 (a narrow synchronous service, no `engine.py`), ADR-0021, ADR-0036, ADR-0037, ADR-0039 and ADR-0049, and supersedes none of them.

## Consequences

**What becomes easier:**

- A desktop client can show the open decisions and apply one, without importing the CLI or holding a lock while a person decides.
- A stale answer is a visible, typed result, never a silent write.
- The rule that identity merges are never bulk-consented is stated once as data and pinned by a test, rather than following from which prompt helper a walk calls.
- The per-item write sequences of the three walks exist once, in the application layer.

**What becomes harder, and what we accept:**

- The CLI and the stateless path guard human time differently: in-process baselines raise drift (exit 3), while a token returns `Stale`. Reviewers must keep both in view. The CLI keeps the in-process path because the token path would add a whole-bundle re-preview per merge, and would start refusing rows that a concurrent daemon pass superseded, which curate merges today.
- The token's content digest covers every byte the plan rests on, which for a merge is the whole-bundle reference scan. Hashing is in memory and is paid only on the token path.
- Drift refusals keep the message composed by the existing shared drift helpers, which take a surface label. Only the new refusals are structured.
- **Judging stays outside this surface.** Until "Judging as an application service" lands (the next MVP 6 stage), a client cannot start the judge, and unjudged identity items cannot become mergeable through this surface.
- **Declines are not recorded.** Structure and Metadata declines still record nothing, so a declined relation or tier is offered again on the next run, in every client.
- **This is not the stable API.** Freezing the token format, its serialization and a non-printing commit adapter belong to the MVP 6 API stage.

## Alternatives considered

- **Re-prepare at apply time with no token.** This is simpler, but it silently replaces the plan the human approved whenever the bundle moved in between, which is the failure ADR-0036's short lock makes possible. Rejected.
- **Hold the workspace lock from preview to commit.** This contradicts ADR-0036: a person deciding would block every other writer, including the daemon. Rejected.
- **`payload_digest` alone as the token.** It misses a referencing document edited after the preview, because the row's input digests cover only its members. **Content alone** misses a row resolved by another surface while the client waited. Both halves are needed.
- **Raise `Stale` as an exception.** A stale item is a "re-preview this one", like a vanished member, and must not end a walk. A returned union is matched exhaustively by the type checker.
- **Design a generic list/apply protocol first, then migrate the kinds.** That designs the abstraction before the destructive case is handled, which is the "designed in a vacuum" risk ADR-0039 warns about. Instead the protocol is set by identity, reused by relation and volatility, and only then generalized.
- **Include judging, so unjudged identity items become decidable.** That brings a model call and its cost gate into a surface that otherwise lists for free. It is deferred to its own change and stage.
- **Route `--auto-merge` through the per-item commit.** That would change ADR-0049's one commit per run. It already writes through an application service and stays there.
