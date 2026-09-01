---
type: Decision
title: "ADR-0018: An application layer for bounded-context services"
description: Introduce src/openkos/application/ as the home for composition that is neither an adapter nor a domain package, starting with the query context.
status: Proposed
date: 2026-08-31
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-08-31T00:00:00Z
sensitivity: public
---

# ADR-0018: An application layer for bounded-context services

- **Status:** Proposed
- **Date:** 2026-08-31

## Context

`docs/architecture.md` promises that `cli`, `api` and `mcp` are thin adapters
over one engine, so no logic is duplicated across the three. The engine it names
is `engine.py`. **That file does not exist.** There is currently no named home
for logic that is neither an adapter nor a domain package, and the vacuum has a
measurable cost: `src/openkos/cli/main.py` is 18,847 lines across 18 command
verbs, and the `query` command alone spans 833 lines with roughly 1,357 lines of
query-exclusive code around it.

Most of what a query needs is already properly delegated.
`retrieval/answer.py::answer()` owns lexical and dense retrieval, sensitivity
filtering, RRF fusion, context assembly, citation building, the sufficiency gate
and attribution parsing; `Citation` and `AnswerResult` are its typed contract,
and it already has callers outside the CLI. The gap is narrower than it looks
and has two parts:

1. The **orchestration around** `answer()` — store opening with
   degrade-to-`None` handling, and the `answer()` call itself. Workspace
   gating, chat-client and embedder construction, and the exception ordering
   that maps to exit codes surround this orchestration; the decision below
   deliberately leaves those with the adapter, since a service that bound a
   concrete backend could not serve every adapter.
2. The **entire `query --save` staging and filing flow**, which has no non-CLI
   home at all.

The MVP 3 `api` and `mcp` adapters need both. Today the only way to get them is
to import from `openkos.cli`, which inverts the intended dependency direction:
adapters would depend on another adapter.

Three placements were available, and the choice is hard to reverse. Once
`api/` and `mcp/` import a module, moving it costs a coordinated change across
every adapter — and the first placement sets the precedent that ingest and
lifecycle will follow.

A second force constrains any option: layering here is **an unenforced
convention**. `docs/architecture.md` states plainly that the canonical /
derived boundary has no automated guard and that import-linter is not wired.
A placement that depends on a check nobody runs is a placement that will drift.

## Decision

We adopt `src/openkos/application/` as the layer for **bounded-context
services**, and land `application/query.py` as its first member.

- **Granularity is one module per bounded context**, not per verb. `query.py`
  owns the query context; ingest and lifecycle get their own modules **when
  their code arrives**, never as empty scaffolding.
- **`application/` may import** `model`, `bundle`, `state`, `retrieval`,
  `graph`, `resolution`, `llm`, `config` and `fsio`. Services sit *above* both
  the canonical and the derived layer, so composing the two is legal here and
  only here.
- **Nothing in those packages may import `openkos.application`, and
  `openkos.application` must never import `openkos.cli`.** This is the whole
  invariant. It is enforced by construction rather than by a guard: a lower
  layer importing upward produces an obvious cycle, and the adapter direction is
  the one thing every reviewer of this layer must check.
- **The layer is synchronous.** Async lives only at the MVP 3 edge, which calls
  in through a thread pool.
- **Services compose and decide; they do not render and do not prompt.** A
  service returns typed data carrying everything an adapter needs to render.
  It never calls `typer.echo`, `typer.confirm`, `sys.stdin.isatty()` or
  `input()`. Interaction, exit codes and protocol error shapes belong to the
  adapter.
- **Services stage; adapters write.** Cross-cutting write infrastructure —
  drift re-validation, atomic and exclusive writes, workspace autocommit,
  derived-store refresh — stays where its 17 to 24 call sites already are, and
  is not duplicated into a single context's service.
- **`application/__init__.py` exports nothing** beyond a docstring, matching
  `retrieval/__init__.py`. Callers import the context module directly.

`application/` does not resolve `engine.py`'s absence by becoming it. It is a
layer of context-scoped services, not one thin orchestrator; whether a top-level
wiring module is ever needed is a later question this ADR deliberately leaves
open.

## Consequences

**Easier.** An `api` or `mcp` adapter can answer a question and file the result
with no import from `openkos.cli`. Application rules — the sensitivity
high-water-mark at filing, the title-derivation cascade, the synthesis-share
threshold, the grounding predicate — become directly unit-testable without
driving a Typer runner, which is materially cheaper against the 90% branch
coverage gate. `cli/main.py` shrinks. Ingest and lifecycle now have an obvious
destination rather than a debate.

**Harder.** There is one more layer to place code in, and "adapter or service?"
becomes a real question on every future change; the rendering-versus-policy line
in particular is a judgement call that will be relitigated. The layering
invariant above is stated and unguarded, so it is only as strong as review
attention until import-linter lands — this ADR makes the rule explicit precisely
because nothing else will catch a breach.

**Accepted risk, and its cost.** Moving the `answer()` call site into a service
module breaks the CLI test suite's injection seam. Test doubles are installed
with `monkeypatch.setattr("openkos.cli.main.answer", ...)` at 123 sites across
five files; that works only because `main.py` imports the name at module level
and calls it unqualified. Once the service imports and calls it in its own
namespace, those patches become **silent no-ops** and the real networked
`answer()` runs. All 123 targets are therefore repointed at
`openkos.application.query.answer` in the same change that moves the call.

The three patched names are not symmetric, and the resolution differs by name:

| Name | Sites | Disposition |
| --- | --- | --- |
| `OllamaClient` | 118, of which 113 belong to `doctor`, `init`, `ingest`, `contradictions` and other verbs | Stays constructed by the CLI and is passed in as an `LLMBackend`; it cannot move, and a service should not bind a concrete backend anyway |
| `stale_derived_stores` | 1, but read through `_stale_index_names`, which `status` and `next` also call | Stays in the CLI; the staleness warning is presentation and its ordering is load-bearing for stderr |
| `answer` | 123, one production call site | Moves with the call; patch targets migrate |

Future contributors must keep in mind that **a default argument is not an
injection seam**: `answer_fn: AnswerFn = answer` binds the default object at
`def` time, so patching the module attribute afterwards changes nothing while
appearing to work.

## Alternatives considered

**`cli/query_service.py`.** Follows an existing, proven precedent — `cli/curate.py`
and `cli/next_action.py` already hold non-trivial extracted logic with `main.py`
keeping only the thin command. Zero new packages and the lowest-risk shape.
Rejected because by that precedent's own stated intent it is a **CLI-layer**
module, so the MVP 3 adapters would import `openkos.cli.*`. That reproduces the
exact inversion this work exists to remove, one file lower, and it would set the
precedent for ingest and lifecycle too.

**Inside `retrieval/`.** Co-locates the composition with `answer()` and adds no
package. Rejected as the clearest layering breach of the three: `retrieval/` is
documented as a **derived, rebuildable** layer, and `query --save` writes
canonical state (`fsio`, `bundle.index`, `bundle.log`). Putting canonical writes
into a package whose contract is "delete it and rebuild it" is a contradiction
the unenforced convention would never catch.

**Injecting `answer` as a callable parameter.** Would preserve all 123 patch
sites and cost no test churn. Rejected because a service whose central operation
is supplied by its caller has no contract: every adapter would import `answer`
from `retrieval` and pass it in, which is the coupling this decision removes,
and the service would no longer own its own composition.

**Creating `application/ingest.py` and `application/lifecycle.py` now.**
Rejected: `AGENTS.md` and `openspec/config.yaml` both require that a package be
created when its code arrives. Empty scaffolding would assert a shape for two
contexts nobody has yet examined.
