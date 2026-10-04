# Ground truth — `medium-09-sdk-skills-notes.md`

Source size: 13595 B

## READ THIS FIRST — this is a paired variant, not an independent fixture

This file is a **synthetic container-title twin** of `medium-08-sdk-skills.md`,
created 2026-08-07 for
[#459](https://github.com/jasonssdev/openkos/issues/459). Below the H1 the two
sources are byte-identical; the ONLY difference is the first line:

    medium-08:  # Building a Research Agent with the Claude Agent SDK
    medium-09:  # Notes from the Agent SDK course, final session

`medium-08`'s H1 names the document's TOPIC. This one's H1 names the document
AS A CONTAINER — a notes artifact from a course session — the title shape #459
isolated as an extraction-collapse trigger on the AMI transcripts
(`TS3005b.transcript` produced ~10 candidates under a stem title and 1 under
its derived container title). The pair exists to test whether that effect
reproduces on prose, against known ground truth, with every other variable
held constant.

**Scoring rule.** A result here is NOT independent confirmation of a result on
`medium-08-sdk-skills.md`. The two are the same document under two titles.
Report them as a pair, never as two data points in the same average — the same
rule this corpus already applies to the `large-03-skills-vs-tools.md` /
`small-04-pre-build-skills.md` pair.

Provenance: the body (everything below the H1) is copied verbatim from
`medium-08-sdk-skills.md`; only the H1 was authored for this fixture. Because
the body is byte-identical, every subject, facet, near-duplicate and
out-of-scope judgment below is carried over from `medium-08`'s ground truth
unchanged — the knowledge content of the two documents is identical, and those
judgments are grounded in the body, not the title. The H1-dependent judgments
that could NOT be carried over are re-derived in the sections marked for it
below. Type calls apply Annotation guideline v1
(`examples/extraction-corpus/annotation-guidelines.md`), §1 — the nine-type
rubric frozen from `_SYSTEM_PROMPT` at commit `8c64081` — not an independent
taxonomy.

## Genuinely distinct subjects

**Count: 4.**

- Procedure | Building a Research Agent with the Claude Agent SDK
- Concept | Claude Agent SDK
- Concept | Human-in-the-Loop Guardrails
- Concept | Model Context Protocol (MCP)

The same four as `medium-08-sdk-skills.md`, for the same reasons — see that
file for the full argument on each (the contested `Procedure`, the promotion
of `Model Context Protocol (MCP)` in the first adjudication pass, the REJECTED
promotion of `Orchestrator-Workers Pattern`, and the exclusion of `MinerU`).
The body is byte-identical, so none of that reasoning moves.

One H1-dependent wrinkle, re-derived for this twin: the `Procedure`'s
canonical title was `medium-08`'s H1, and in THIS source that exact string
appears nowhere — the phrase "Research Agent" occurs only in `medium-08`'s
H1, never in the shared body, which says "multi-agent research application".
The subject stands (the procedure taught is identical and the title remains
its natural name), but a run on this fixture has no line to copy it from, so
the exact canonical title is an unlikely emission here. Expect the
body-grounded alias family (`Research Agent Application`, `Research Agent`,
`Research Agent Implementation`) or new phrasings to carry the credit; a new
phrasing that names the whole build scores as a miss until adjudicated into
the aliases below, per corpus policy.

## The medium-08 twin-rule collision does NOT exist here

`medium-08`'s ground truth documents (as resolved by #413) a collision:
`derive_source_title` on that file returns a string byte-identical to its
`Procedure` subject, putting the primary object in `_drop_source_title_twins`'
line of fire. Re-derived for this twin's H1:

`openkos.source_title.derive_source_title` returns, for this exact file:

    'Notes from the Agent SDK course, final session'

which matches NO subject above, exactly or under any curated alias. The twin
rule therefore has nothing of value to delete on this fixture, and the
harness's `twin_deleted_subjects` flags nothing here. A miss on any of the
four subjects — including the `Procedure` — is a plain extraction miss.
Score it as one, with no twin-rule caveat.

What the container title CAN do here is the very effect under measurement:
prime the model into emitting the document-as-artifact instead of its
knowledge, or into collapsing the reply to ~1 object. The H1 string itself is
pre-judged under `## Out of scope` below so a collapsed run scores as the
failure it is instead of sitting unjudged.

## Aliases

Alternate phrasings that name a subject above. `evals/extraction_cap/`
matches titles EXACTLY and never fuzzily, so a rephrasing scores as a miss
until it is adjudicated here by a human. Each line reads
`Canonical Title | alias [| alias]`.

- Building a Research Agent with the Claude Agent SDK | Research Agent Application | Research Agent
- Model Context Protocol (MCP) | Model Context Protocol (MCP) Server
- Building a Research Agent with the Claude Agent SDK | Research Agent Implementation
- Human-in-the-Loop Guardrails | Security and Safety Measures

All four lines are inherited from `medium-08`'s ground truth, where they were
adjudicated from measured runs (first pass and the 2026-08-07 prompt-A/B
queue). They are body-grounded — each names knowledge developed in the shared
body — so they hold here unchanged. If anything, the `Research Agent
Application` / `Research Agent` / `Research Agent Implementation` family
matters MORE on this fixture: with the topic H1 gone, these body-derived
names are the likely spellings of the `Procedure` (see the wrinkle noted in
the subjects section).

`Model Context Protocol (MCP) Server` is the protocol under its server noun,
deliberately distinct from `Notion MCP Server` (a FACET below — the one
concrete server this application mounts). `medium-08` flags that split as its
most contestable call; if a later reader rejudges it there, rejudge it here
identically — the pair must never disagree about the shared body.

### The 2026-10-04 bake-off additions (#1269)

Second #1269 bake-off pass: five 15-run sweeps (union+judge), one per
candidate model, judged from a BLIND list — titles pooled across every sweep,
de-duplicated with the harness's own `normalize`, sorted alphabetically, no
model name attached — against the source and the rulings already in this file.
Alias against near-duplicate follows the mechanical co-occurrence rule written
into `medium-10-reunion-plataforma.md` on 2026-08-15: a title that is the only
name for its subject in at least one reply is an alias; a title that never
appears without another name for the same subject in its reply is a
near-duplicate. One repair to the rule, needed once two NEW phrasings meet:
where every name a reply gave a subject is a new phrasing, the rule as written
charges all of them and credits none, so the first-positioned one is taken as
that reply's name for it (an alias) and the rest stay near-duplicates. No new
subject was minted.

- Building a Research Agent with the Claude Agent SDK | Building a Multi-Agent Research Application with Claude Agent SDK
- Building a Research Agent with the Claude Agent SDK | Building a Multi-Agent Research Application with the Claude Agent SDK
- Building a Research Agent with the Claude Agent SDK | Claude Agent SDK Research Application
- Building a Research Agent with the Claude Agent SDK | Constructing a Multi-Agent Research Application with Claude Agent SDK
- Building a Research Agent with the Claude Agent SDK | Implementation of the Research Agent Application
- Building a Research Agent with the Claude Agent SDK | Multi-agent Research Application Project
- Building a Research Agent with the Claude Agent SDK | Multi-agent research application using Claude Agent SDK
- Building a Research Agent with the Claude Agent SDK | Research-Agent-App
- Human-in-the-Loop Guardrails | Human-in-the-Loop Guardrails for AI Agents
- Human-in-the-Loop Guardrails | Human-in-the-Loop Interrupts
- Human-in-the-Loop Guardrails | Human-in-the-Loop Interrupts for Agent Safety
- Human-in-the-Loop Guardrails | Human-in-the-Loop Interrupts for Agent Security
- Human-in-the-Loop Guardrails | Human-in-the-Loop Safety Guardrails
- Human-in-the-Loop Guardrails | Human-in-the-Loop Security Guardrails for Autonomous Agents
- Human-in-the-Loop Guardrails | Implementation of Human-in-the-Loop Guardrails for Agent Safety
- Human-in-the-Loop Guardrails | Implementation of Human-in-the-Loop Guardrails for Agent Security
- Human-in-the-Loop Guardrails | Implementing Human-in-the-Loop Guardrails
- Human-in-the-Loop Guardrails | Implementing Human-in-the-Loop Guardrails for Agent Safety
- Human-in-the-Loop Guardrails | Implementing Human-in-the-Loop Guardrails for Agent Security
- Human-in-the-Loop Guardrails | Implementing Human-in-the-Loop guardrails for autonomous agents
- Human-in-the-Loop Guardrails | Implementing Human-in-the-Loop Guardrails in Agent SDK
- Human-in-the-Loop Guardrails | Implementing Human-in-the-Loop Security Guardrails
- Human-in-the-Loop Guardrails | Security Guardrails Implementation Strategy
- Model Context Protocol (MCP) | Model Context Protocol (MCP) Integration

## Facets, not subjects

Steps and components of the procedure above, not knowledge objects. An
extractor emitting these is decaying, not enumerating. The list is matched
EXACTLY, so a heading's other phrasings need their own lines. All entries are
inherited from `medium-08`'s ground truth (first adjudication pass and the
2026-08-07 prompt-A/B sweep); every one names a section, component, or phase
of the shared body, so the judgments carry over unchanged.

- Operational Blueprints
- Main Agent Orchestration Guidelines
- Sub-Agent Toolkit Assignments
- The Orchestration Skill
- Technical Implementation (`agent.py`)
- Plan Verification / Plan Mode Activation
- Parallel Investigation & Document Synthesis
- Syncing Research to Notion
- Live Case Study
- Orchestrator-Workers Pattern
- Learning-a-Tool Skill
- Technical Implementation: agent.py
- Technical Implementation of `agent.py`
- agent.py
- Live Case Study: Researching MinerU
- Researching MinerU
- Notion MCP Server Integration
- Notion MCP Server
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

`Orchestrator-Workers Pattern` is judged in `medium-08`'s subjects section:
one bolded sentence, no section of its own, rejected for promotion.
`Learning-a-Tool Skill` is `The Orchestration Skill` under its filename. The
four agent-role titles name the components of the orchestrator-workers
layout, itself a facet.

### The 2026-10-04 bake-off additions (#1269)

Each family follows a ruling inherited from `medium-08` and must stay
identical there: the `Orchestrator-Workers` layout, `Research Agent
Architecture`, the code, steps and environment setup of `Technical
Implementation: agent.py`, the orchestration skill, the MinerU case study and
the Notion sync.

CONTESTED, recorded here in the reading that does NOT credit the subject, with
the other reading stated so it is never resolved silently. Three families are,
in many replies, the only procedure-shaped title — which matters more here
than on `medium-08`, since the canonical title appears nowhere in this source:

  * Setting up the application (`Setting Up the Research Agent Application`,
    `Research Agent Application Setup and Execution` and kin): filed as the
    environment-setup step. The other reading is that setting up the
    application is building it, which would make them aliases of the
    `Procedure`.
  * Researching an open-source tool (`Researching an Open-Source Tool with
    Claude Agent SDK` and kin): filed as the live case study generalised, as
    `Researching MinerU` is a facet. The other reading is that they name the
    procedure by its stated purpose (the lede: the application exists "to
    research an open-source tool").
  * The application's workflow (`Multi-Agent Research Application Workflow`,
    `Multi-Agent Research Workflow using Claude Agent SDK`): filed as the
    orchestration skill's two-phase workflow. The other reading is the
    procedure itself.

- Agent Definition
- Agent Execution Loop
- Agent Implementation
- Agent Implementation (agent.py)
- Agent Initialization
- Agent Initialization and Setup
- Agent Orchestration Guidelines
- Agent Setup and Execution
- Agent.py Implementation
- Claude Agent SDK Environment Setup
- Claude Agent SDK Multi-Agent Architecture
- Claude Agent SDK Orchestrator-Workers Pattern
- Implementing the Agent Framework in Python
- Implementing the Claude Agent SDK in Python
- Initializing and running the Claude Agent SDK research environment
- Learning-a-Tool Skill Workflow
- MinerU Research Application Implementation
- MinerU Research Case Study
- Orchestrator-Workers Pattern for Autonomous Agents
- Orchestrator-Workers pattern for multi-agent systems
- Orchestrator-Workers Pattern in Agent SDKs
- Orchestrator-Workers Pattern in Agent Systems
- Orchestrator-Workers Pattern in Autonomous Agents
- Orchestrator-Workers Pattern in Claude Agent SDK
- Progressive Learning Guide Structure
- Research Agent Environment Setup
- Researching MinerU Case Study
- Researching MinerU Using the Agent Framework
- Researching MinerU Using the Claude Agent SDK
- Researching MinerU with Claude Agent SDK
- Researching MinerU with the Claude Agent SDK
- Setting up a Claude Agent SDK Research Environment
- Setting Up the Claude Agent SDK Environment
- Setting up the Claude Agent SDK Research Environment
- Setting Up the Research Environment
- Syncing Research to Notion via MCP Server
- Syncing Research to Notion via the MCP Server

The CONTESTED rows, in the non-crediting reading:

- Claude Agent SDK Research Application Setup
- Multi-Agent Research Application Workflow
- Multi-Agent Research Workflow using Claude Agent SDK
- Research Agent Application Setup
- Research Agent Application Setup and Execution
- Research Agent Application Setup and Implementation
- Researching a Tool with Claude Agent SDK
- Researching an open-source tool using Claude Agent SDK
- Researching an Open-Source Tool via Claude Agent SDK
- Researching an Open-Source Tool with Claude Agent SDK
- Setting up a Multi-Agent Research Application
- Setting up a Multi-Agent Research Application with Claude Agent SDK
- Setting Up the Research Agent Application
- Setup and Implementation of Claude Agent SDK Application

## Out of scope

Things this document MENTIONS but is not ABOUT — plus, on this fixture, the
document's own container name. Kept apart from facets on purpose: a facet
emission is decay (the model shredding a subject into attributes), a scope
error is not, and merging them would inflate the decay figure.

- MinerU
- Notes from the Agent SDK course, final session

`MinerU` is inherited from `medium-08`: the tool the live case study
*researches*, an example the procedure operates on.

`Notes from the Agent SDK course, final session` is the twin's own H1,
pre-judged 2026-08-07 (#459) rather than adjudicated from runs: it names the
document AS AN ARTIFACT — a container — and carries no knowledge from the
body, so it can never credit a subject. This is precisely the collapse mode
the fixture exists to measure, and crediting it would score the failure as a
success. `_drop_source_title_twins` will usually delete this candidate before
scoring (it is the exact derived title); one that survives — a Procedure-typed
emission under the #413 exemption, or a sole-object reply under the floor —
scores here as a scope error.

`Agent SDK Course Final Session` is the container H1 above, reworded
(2026-10-04 bake-off pass, #1269).

- Agent SDK Course Final Session

## Near-duplicates

Pairs are written `Canonical Subject | the duplicate phrasing`. The section
read "None identified." until the 2026-10-04 bake-off pass (#1269).

The same call as `medium-08`: `Claude Agent SDK` against the `Procedure` was
considered there and rejected — the SDK is a tool, the procedure is what you
do with it, and the document develops both.

### The 2026-10-04 bake-off additions (#1269)

Every line below failed the co-occurrence rule in EVERY reply that emitted it
in the 2026-10-04 bake-off pass: each time, the same reply already named the
subject another way.

- Building a Research Agent with the Claude Agent SDK | Agent SDK Multi-Agent Research Application
- Building a Research Agent with the Claude Agent SDK | Building a Multi-Agent Research App with Claude Agent SDK
- Building a Research Agent with the Claude Agent SDK | Building a Multi-Agent Research Application
- Building a Research Agent with the Claude Agent SDK | Building Multi-Agent Applications with Claude Agent SDK
- Building a Research Agent with the Claude Agent SDK | Implementing a Claude Agent SDK Research Application
- Building a Research Agent with the Claude Agent SDK | Implementing a Multi-Agent Research Application with Claude Agent SDK
- Building a Research Agent with the Claude Agent SDK | Implementing a Research Agent with Claude Agent SDK
- Human-in-the-Loop Guardrails | Human-in-the-Loop Guardrails for Agentic Shell Access
- Human-in-the-Loop Guardrails | Human-in-the-Loop Guardrails for Agentic Workflows

## Notes

**H1-dependent judgments re-derived for this twin (everything else is
inherited verbatim).** Three judgments in `medium-08`'s ground truth depended
on its H1 and could not be carried over: (1) the twin-rule collision section
— re-derived above into its opposite, since the new derived title matches no
subject; (2) the observation that the `Procedure`'s canonical title is a
likely verbatim emission — reversed above, since the string no longer appears
anywhere in this source; (3) `medium-08`'s MEASURED paragraphs (9-run set,
`qwen3:8b`) describing how the collision did and did not fire — those runs
saw the topic H1 and their title-emission behavior does not transfer, so they
are cited only through the aliases and facets they produced, which are
body-grounded. No measured runs exist yet on THIS fixture.

**Expected measurement use (#459).** `--title-mode both` on this fixture
pairs the container title (derived, `Notes from the Agent SDK course, final
session`) against the stem (`medium-09-sdk-skills-notes`); its twin
`medium-08` pairs a topic title against its stem. The delta-of-deltas
isolates container-title priming on prose: if the container title collapses
extraction the way it did on the AMI transcripts, this fixture's derived arm
degrades against its stem arm by more than `medium-08`'s does. The same
pairing serves as the A/B bed for any prompt fix.

Several `#`-prefixed lines in the source are shell and Python comments inside
fenced code blocks, not headings. `corpus.py survey` strips fences before
counting (17 headings, not 23; the H1 swap is one-for-one and changes no
count); a naive heading count over the raw text overstates how multi-subject
this document is.
