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

## Near-duplicates

None identified.

The same call as `medium-08`: `Claude Agent SDK` against the `Procedure` was
considered there and rejected — the SDK is a tool, the procedure is what you
do with it, and the document develops both.

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
