# Ground truth — `medium-08-sdk-skills.md`

Source size: 13600 B

A single-arc tutorial: build one research agent, end to end. That shape is why
this fixture is in the corpus — `large-03-skills-vs-tools.md` surveys several
topics and tests whether the extractor keeps up, while this one tests the
opposite failure, over-extraction on a document that is fundamentally about one
procedure.

## Genuinely distinct subjects

**Count: 4.**

- Procedure | Building a Research Agent with the Claude Agent SDK
- Concept | Claude Agent SDK
- Concept | Human-in-the-Loop Guardrails
- Concept | Model Context Protocol (MCP)

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
example the procedure operates on, not something this document is about. It is
listed under `## Out of scope` below so a run emitting it is scored as a scope
error rather than left unjudged forever.

`Model Context Protocol (MCP)` was added in the first adjudication pass, after
9/9 measured runs produced it and the harness left it unjudged. Three pieces of
evidence, all from the source:

1. The document's own closing sentence names what it combined: *"By combining
   the **Claude Agent SDK**, **Model Context Protocol**, and proper **User
   Interrupt Guardrails**"* (line 247). Two of those three were already
   subjects here; MCP sat at the same level in the author's own summary.
2. It carries a section — `## Syncing Research to Notion via the MCP Server` —
   and appears in the opening thesis (line 3), not only in code comments.
3. `large-03-skills-vs-tools.md` already judged `Model Context Protocol (MCP)`
   a genuine subject. Leaving it off here made the two ground truths of one
   corpus contradict each other about the same concept.

`Orchestrator-Workers Pattern` was considered for the same promotion and
REJECTED. It gets exactly one bolded sentence in the whole file — *"Our
application relies on an **Orchestrator-Workers pattern**"* (line 11) — with no
section, no definition, and no development; the text immediately moves on to
listing this application's three child agents. Contrast `Claude Agent SDK`,
which owns `## Architecture Overview` outright. A reader would not want a
document on the pattern built from this source. It is a facet.

## KNOWN RULE COLLISION — this fixture exposed it on purpose, and it is fixed

**RESOLVED (#413).** `_drop_source_title_twins` now exempts `Procedure`
outright: a `Procedure` is never a twin, whatever its title. The primary
object below survives alongside `Claude Agent SDK` and
`Human-in-the-Loop Guardrails`, and a miss on it is once again a plain
extraction miss — score it as one. The rest of this section is kept as the
record of what the fixture caught and why the exemption keys on the type.

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

**MEASURED, first run set (9 responded runs, baseline + t0.1, `qwen3:8b`).**
The collision did NOT fire once. The model never emitted the exact title, so
`_drop_source_title_twins` never had anything to delete. What it emitted was
`Research Agent Application` (5 runs) and `Research Agent` (2), exactly the
escape this section predicted two paragraphs above.

The primary object was still lost in most of those runs — but by the CAP, not
by the twin rule: the variant landed at reply position 6 in 5 of the 7 runs
that produced one, outside `_MAX_OBJECTS_PER_SOURCE`. Two different mechanisms
with the same symptom, and the earlier reading of a missing Procedure as twin
deletion was wrong on this evidence. Check which one fired before attributing
a miss to either.

## Aliases

Alternate phrasings that name a subject above. `evals/extraction_cap/` matches
titles EXACTLY and never fuzzily, so a rephrasing scores as a miss until it is
adjudicated here by a human. Each line reads `Canonical Title | alias [| alias]`.

- Building a Research Agent with the Claude Agent SDK | Research Agent Application | Research Agent
- Model Context Protocol (MCP) | Model Context Protocol (MCP) Server

`Research Agent Application` and `Research Agent` name the artifact the whole
document builds. Nothing else here is a candidate for them, and treating them
as separate subjects would make the document's own topic into an object beside
itself — which is the twin the rule exists to suppress.

`Model Context Protocol (MCP) Server` is the protocol under its server noun.
Note the deliberate contrast with `Notion MCP Server`, filed as a FACET below:
that one names the one concrete server this application mounts, which is the
mechanism of the sync section, not the protocol concept. **This is the most
contestable call in this file** — if a later reader judges the two the same
thing, move `Notion MCP Server` up here and rescore.

- Building a Research Agent with the Claude Agent SDK | Research Agent Implementation
- Human-in-the-Loop Guardrails | Security and Safety Measures

Both from the 2026-08-07 prompt-A/B adjudication queue. `Research Agent
Implementation` joins the `Research Agent Application` / `Research Agent`
family — the artifact the whole document builds. `Security and Safety
Measures` names exactly the topic of `## Production Security: Implementing
Human-in-the-Loop Guardrails`, that subject's home section; the counter-reading
(too generic to credit) was considered and rejected because nothing else in
this document is a candidate for it.

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

Added in the first adjudication pass, from 9 measured runs. The list is matched
EXACTLY, so a heading's other phrasings need their own lines — the extractor
names the same section several ways across runs and each spelling must be
recognized or it inflates the unjudged count forever.

- Orchestrator-Workers Pattern
- Learning-a-Tool Skill
- Technical Implementation: agent.py
- Technical Implementation of `agent.py`
- agent.py
- Live Case Study: Researching MinerU
- Researching MinerU
- Notion MCP Server Integration
- Notion MCP Server

`Orchestrator-Workers Pattern` is judged above, in the subjects section: one
bolded sentence, no section of its own, rejected for promotion.

`Learning-a-Tool Skill` is `The Orchestration Skill` under its filename — the
heading is "### 3. The Orchestration Skill (`learning-a-tool.md`)", one thing
with two names.

Added in the second adjudication pass (2026-08-07 prompt-A/B sweep):

- Research Agent Architecture
- Progressive Leveling
- Progressive Learning Milestones
- Plan Verification (Plan Mode Activation)
- File Creation and Synthesis
- Parallel Investigation
- Main Agent (Orchestrator)
- Documentation Researcher (Sub-Agent)
- Repository Analyzer (Sub-Agent)
- Web Researcher (Sub-Agent)

`Progressive Leveling` is one bolded phase bullet inside the Orchestration
Skill's workflow (line 51) — the same shape as `Sub-Agent Toolkit
Assignments`. The four agent-role titles name the components of the
orchestrator-workers layout, which is itself a facet (judged in the subjects
section above). `Research Agent Architecture` names the layout of the app the
procedure builds; the SDK subject already owns `## Architecture Overview`'s
knowledge about the SDK itself.

## Out of scope

Things this document MENTIONS but is not ABOUT. Kept apart from facets on
purpose: a facet emission is decay (the model shredding a subject into
attributes), a scope error is not, and merging them would inflate the decay
figure — which is precisely the number that argues AGAINST raising the cap.

- MinerU

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
