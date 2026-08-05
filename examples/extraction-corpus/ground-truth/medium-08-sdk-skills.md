# Ground truth — `medium-08-sdk-skills.md`

Source size: 13600 B

A single-arc tutorial: build one research agent, end to end. That shape is why
this fixture is in the corpus — `large-03-skills-vs-tools.md` surveys several
topics and tests whether the extractor keeps up, while this one tests the
opposite failure, over-extraction on a document that is fundamentally about one
procedure.

## Genuinely distinct subjects

**Count: 3.**

- Procedure | Building a Research Agent with the Claude Agent SDK
- Concept | Claude Agent SDK
- Concept | Human-in-the-Loop Guardrails

The `Procedure` is listed deliberately, and it is the contested one. A
knowledge base has to be able to answer "how do I build a research agent with
the Agent SDK?", and no other object here carries that. The Source concept is a
bibliographic anchor — the thing the bundle points back at — not a knowledge
object about the procedure it teaches. Those are different roles, and collapsing
them loses the how-to.

`Claude Agent SDK` earns its own object from `## Architecture Overview`, which
describes the SDK itself rather than the steps. `Human-in-the-Loop Guardrails`
comes from `## Production Security`, a topic in its own right rather than a step
in the build.

`MinerU` is excluded: it is the tool the live case study *researches*, an
example the procedure operates on, not something this document is about.

## KNOWN RULE COLLISION — this fixture exposes it on purpose

`openkos.source_title.derive_source_title` returns, for this exact file:

    'Building a Research Agent with the Claude Agent SDK'

which is byte-identical to the `Procedure` title above. Two of the system's own
rules then disagree about it.

The prompt instructs the model to produce it:

> An instructional document — a how-to, tutorial, guide, reference page, or FAQ
> — still has a primary subject: choose "Procedure" when it teaches a repeatable
> how-to

`_drop_source_title_twins` then deletes it, because a candidate whose title
matches the source title is dropped whenever at least one non-matching object
also survives — and here two do.

The collision runs in the wrong direction. A single-subject tutorial KEEPS its
Procedure, protected by the floor ("if every object matches, or only one object
exists at all, the list is returned unchanged"). A richer tutorial — one that
also yields `Claude Agent SDK` and `Human-in-the-Loop Guardrails` — loses its
primary object precisely BECAUSE it is richer.

It is conditional, not guaranteed: the comparison is exact after strip,
casefold and whitespace collapse, so a model emitting `Building a Research
Agent` or `Research Agent Construction` survives. But the prompt asks for the
primary subject, and for a tutorial the natural name for that is the title.

**Scoring consequence.** A run on this source that returns 2 objects
(`Claude Agent SDK`, `Human-in-the-Loop Guardrails`) is NOT a correct run
scoring 2 of 3 — it is very likely a correct extraction whose primary object was
deleted downstream by the twin rule. Whoever scores a run here must check which
of the two happened before recording a miss, or the number will blame the
extractor for a rule interaction.

This is tracked separately from #404: that issue is about HOW MANY objects
survive, this is about WHICH one is lost.

## Facets, not subjects

Steps and components of the procedure above, not knowledge objects. An
extractor emitting these is decaying, not enumerating.

- Operational Blueprints
- Main Agent Orchestration Guidelines
- Sub-Agent Toolkit Assignments
- The Orchestration Skill
- Technical Implementation (`agent.py`)
- Plan Verification / Plan Mode Activation
- Parallel Investigation & Document Synthesis
- Syncing Research to Notion
- Live Case Study

## Near-duplicates

None identified.

`Claude Agent SDK` against the `Procedure` was considered and rejected — the SDK
is a tool, the procedure is what you do with it, and the document develops both.
That is the same reasoning that kept `Model Context Protocol (MCP)` and
`MCP Workflows` separate in `large-03-skills-vs-tools.md`.

## Notes

Several `#`-prefixed lines in this file are shell and Python comments inside
fenced code blocks, not headings. `corpus.py survey` strips fences before
counting (17 headings, not 23); a naive heading count over the raw text
overstates how multi-subject this document is.
