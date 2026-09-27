---
type: Decision
title: "ADR-0028: Disclosure to an MCP client is its own sensitivity boundary -- hidden by default, opened only at launch"
description: A confidential object is never disclosed to an MCP client unless the server was launched with --expose-confidential; the check is a separate fail-closed predicate in sensitivity.py with no LLM-send escape hatches, applied only at the adapter boundary, and withheld objects are reported as counts.
status: Proposed
date: 2026-09-26
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-09-26T00:00:00Z
sensitivity: public
---

# ADR-0028: Disclosure to an MCP client is its own sensitivity boundary -- hidden by default, opened only at launch

- **Status:** Proposed
- **Date:** 2026-09-26

## Context

Every sensitivity gate in OpenKOS protects one boundary: **LLM egress**. `sensitivity.blocks_llm_send` ranks a raw `sensitivity` value fail-closed (absent, blank, unrecognized, unreadable and unparseable all count as confidential), and `should_block` and `sensitive_concept_ids` add two escape hatches on top of it. `include_confidential` is a human overriding the policy for one run. `local_exemption` asserts that the chat backend is verifiably this machine, so nothing leaves it (issue #240, ADR-0003).

An MCP server creates a second boundary. A tool result goes to a chat client, and the client usually forwards it to a remote model provider that the server cannot observe. Neither hatch has an honest meaning there:

- `include_confidential` is a per-run human decision about a model send. A tool call is issued by a model, not by the human, so there is no one to take that decision per request.
- `local_exemption` rests on the locality of the peer. A stdio client is always a local process, and it still forwards what it receives to wherever it sends its prompts. The locality of the peer says nothing about egress.

The exploration for this change also found that disclosure leaks exist today, in structured channels that were never meant to be gated:

- `application/next_action.py` imports nothing from `sensitivity`, and its reasons interpolate concept ids and lint's free-text detail.
- The graph projection is sensitivity-blind by construction.
- `ProvenanceSources.rows` is the whole bundle.
- `AnswerResult`'s title lists do not say why a title was held back.
- Skip notices and `NotRun` reasons name unreadable documents, which the fail-closed rank treats as confidential.

The owner decided that confidential objects are hidden over MCP by default, that the opt-in is taken at launch and never per request, and that withheld objects are reported as counts. The owner also decided that a note which is not confidential is returned as written, even when its prose mentions a confidential note: sensitivity is per object, and a note that talks about something sensitive should itself be marked confidential.

## Decision

**Disclosure to a program other than the LLM is a separate boundary with its own predicate.** `sensitivity.py` gains `blocks_disclosure(value, *, expose_confidential=False)` and a set-producing sibling, `disclosable_concept_ids(bundle_dir, *, expose_confidential=False)`. Both reuse the strict fail-closed rank of `blocks_llm_send` and stay in the same pure leaf, so there is one fail-closed authority. Neither takes `include_confidential` or `local_exemption`. The only policy input is whether the launch opt-in is on.

**The set sibling returns the ids that may be disclosed, not the ids that may not.** An id that one bundle walk did not reach, such as a dangling provenance target or a file created after the walk, is therefore withheld. It is never disclosed by being absent from a block list.

**The opt-in is a launch flag, `openkos mcp --expose-confidential`, off by default.** It is deliberately not named `--include-confidential`, which on `query` means "send confidential content to the LLM". No request parameter can change it.

**The predicate is applied in exactly one place: `src/openkos/mcp/gate.py`.** The services stay complete, the CLI is unchanged, and there is one call site to audit. The gate filters every structured channel: ids, titles, relations, graph edges, provenance and its source ancestors, `pending` subjects, citations, and the answer's title lists. It never mutates a service result. Every result carries `withheld`, a count, and a `get` or `navigate` on a withheld id returns an empty result with `withheld: 1`. Skip notices and document-labelled `NotRun` entries are reported only as counts, and every error message that crosses the boundary is a fixed string, because an exception's text can carry a confidential path or content.

**Prose is not redacted.** The body of a disclosable object is returned as written. This is the same rule the terminal and the local model already follow ("exclusion, not redaction").

**`query` needs both boundaries, and they compose as a conjunction.** With the flag off, `run_query` is called with `include_confidential=False` and `local_exemption=False`, so confidential content never enters the prompt and the answer is clean by construction. With the flag on, `local_exemption` is resolved exactly as the CLI resolves it, and `include_confidential` is always `False`. A confidential object therefore reaches an answer only when the launch opt-in is on **and** the chat backend is verifiably local. A remote chat backend never receives one through MCP.

**`pending` is gated on structure, never on text.** Each `NextAction` and each declination names its subject concept ids in a structured field. The gate withholds an item unless every subject is disclosable, and it withholds an item whose tier declared no subjects at all. Matching on free text cannot be made fail-closed, because lint's detail is free prose.

**An enumeration guard is part of the decision.** Every registered tool, including its error paths, runs against a fixture holding a confidential canary, and the guard asserts that the canary's id, title and body marker appear in no response byte. A deliberately leaking test tool proves the guard can fail. A tool that is not in the guard's matrix fails the guard.

## Consequences

Easier:

- One auditable call site for disclosure, and one fail-closed rank shared with LLM egress.
- The CLI's behavior is untouched, and `openkos next`'s output stays byte-identical.
- A new tool or channel cannot ship without passing through the guard.

Harder, or accepted:

- With the flag off, MCP answers are less complete than `openkos query` on a local backend, which does include confidential content. This is the intended default, and `docs/cli.md` must say so.
- A note that is not confidential but names a confidential note in its prose discloses that name. We accept this by the owner's decision: the remedy is to mark the note itself confidential.
- A `get` or `navigate` on an id the caller supplied answers `withheld: 1` for a hidden object and `concept_not_found` for a missing one. That discloses only that an id the caller already knew exists.
- Every future structured field added to a tool result is a potential leak channel, and the gate must learn it in the same change. The enumeration guard catches a leak only for fixtures that exercise it, so a new channel needs a new fixture row.
- A dangling provenance target is withheld even under `--expose-confidential`, because there is no document behind it to disclose.

## Alternatives considered

- **Reusing `should_block` with its two hatches.** Rejected: neither hatch has an honest meaning at this boundary (see Context), and reusing them would let a local backend silently widen what a remote-forwarding client sees.
- **A locality exemption for stdio clients.** Rejected: the peer is always local, and it forwards to a provider the server cannot see.
- **A per-request opt-in.** Rejected: the request is written by a model, so a per-request flag is an opt-in any prompt can talk its way into.
- **Filtering inside the services.** Rejected: it would change the CLI's output and spread the predicate across many call sites, which is the duplication `sensitivity.py` exists to prevent.
- **Redacting confidential names from prose.** Rejected by the owner's decision. It would make the MCP surface disagree with the terminal and the local model, and name matching over prose cannot be made complete.
- **A block list instead of an allowed set.** Rejected: an id the walk did not reach would pass by default, which fails open.
- **Exposing everything and relying on the client's own controls.** Rejected: the server cannot observe what the client forwards, and the project's principle is that confidential content never leaves the device unless a gate admits it.
