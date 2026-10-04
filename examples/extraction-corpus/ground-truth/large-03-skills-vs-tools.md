# Ground truth — `large-03-skills-vs-tools.md`

Source size: 16948 B

**Pre-filled from the maintainer's own analysis in
[#404](https://github.com/jasonssdev/openkos/issues/404), not authored fresh.**
That comment inspected which objects the cap discarded on a real run of this
exact file and judged them one by one. Reproduced here so the judgment lives
beside the fixture instead of only in an issue thread.

Nothing is left open here. An earlier draft deferred one item behind a
**NEEDS A CALL** marker; that judgment — whether positions 6 and 7 are genuine
subjects — is settled inline under "Genuinely distinct subjects" below, and the
marker was removed with it. The sentence pointing at it outlived the marker and
is corrected here.

This is also the anchor fixture: #404 measured raw pre-cap counts of
`20, 8, 9, 8` (default temperature) and `7, 7, 7, 7, 7` (temperature 0.0) on
this same document, so a new run can be compared against known numbers rather
than a fresh baseline.

## Genuinely distinct subjects

**Count: 7.**

- Concept | Pre-built Skills
- Concept | Skill Creator
- Concept | MCP Workflows
- Concept | Model Context Protocol (MCP)
- Concept | BigQuery Integration
- Concept | PowerPoint Presentation Skill
- Concept | Brand Guidelines Skill

The first five are the ones the measured run kept, judged in #404 as "exactly
the right five". Positions 6 and 7 were left unjudged by that comment (it
scoped its verdict to "positions 8–20") and were settled separately: both are
genuine subjects.

The supporting evidence is how much of the document each one owns.
`# Creating a Brand Guidelines Skill` runs 76 lines and carries its own
subsection; `# PowerPoint Skill` runs 61. Both get MORE space than
`# Pre-built Skills in Claude` (44 lines), which is already accepted as a
subject. The counter-reading — that each is an instance illustrating
`Pre-built Skills` or `Skill Creator`, since both sit inside arcs that frame
them as worked examples — was considered and rejected.

**This settles which defect the fixture exhibits.** With 7 genuine subjects
and `_MAX_OBJECTS_PER_SOURCE = 5`, the cap is discarding REAL material here,
not only the decayed tail — the same loss measured on
`9-productionize-agent.md` (`Agent Security`, `Agent Observability`). So this
file demonstrates both halves of #404 at once, and a fix that only truncates
the tail without raising the cap would still be wrong on it.

## Aliases

Alternate phrasings that name a subject above. `evals/extraction_cap/` matches
titles EXACTLY and never fuzzily, so a rephrasing scores as a miss until it is
adjudicated here by a human — the bias runs against the hypothesis on purpose.
Each line reads `Canonical Title | alias [| alias]`.

- PowerPoint Presentation Skill | PowerPoint Skill

`PowerPoint Skill` came out of the first measured run's adjudication queue, at
position 7 of a 7-object reply. It is the document's own heading for that
section (`# PowerPoint Skill`), and it names the same subject the canonical
title does. Judged the same subject, not a distinct one.

That judgment is load-bearing for this fixture's headline number: with it, that
run recovered all 7 subjects and the cap discarded TWO of them
(`Brand Guidelines Skill` and this one) rather than one.

- BigQuery Integration | BigQuery

`BigQuery` bare came out of the 2026-08-07 prompt-A/B adjudication queue. When
the model titles an object `BigQuery` on this source it is covering the
integration arc, which owns whole sections (`# Connecting Claude Desktop to
BigQuery with MCP`, `# Checking BigQuery Tables`, `# Updating the Skill to Use
BigQuery`). The out-of-scope reading (the MinerU precedent from
`medium-08-sdk-skills.md`) was considered and rejected: MinerU is an example
the procedure researches, while BigQuery is the developed TARGET of the
integration subject.

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

`MCP Workflows` is credited only where a title uses the document's own name
for that arc (the H1's `MCP Workflows`, or `# Combining Built-in Skills,
Custom Skills, and MCP`). `BigQuery Integration` takes the connect-and-update
titles its sections own (`# Connecting Claude Desktop to BigQuery with MCP`,
`# Updating the Skill to Use BigQuery`).

- BigQuery Integration | BigQuery Integration Workflow with Skills and MCP
- BigQuery Integration | Connecting Claude Desktop to BigQuery via MCP
- BigQuery Integration | Modifying Skills for BigQuery Integration
- BigQuery Integration | Workflow: Integrating Custom Skills with BigQuery via MCP
- BigQuery Integration | Workflow: Updating Marketing Campaign Skill to Use BigQuery via MCP
- Brand Guidelines Skill | Creating a Brand Guidelines Skill
- Brand Guidelines Skill | Workflow: Creating a Brand Guidelines Skill
- MCP Workflows | Combining Built-in Skills, Custom Skills, and MCP
- MCP Workflows | Combining Built-in Skills, Custom Skills, and MCP Workflows
- MCP Workflows | Creating and Combining Custom Skills with MCP Workflows
- MCP Workflows | Creating and Combining Skills with MCP Workflows
- MCP Workflows | Creating and Integrating Claude AI Skills with MCP Workflows
- MCP Workflows | Creating and Integrating Custom Skills with MCP Workflows
- MCP Workflows | MCP Workflows in Claude
- Model Context Protocol (MCP) | MCP (Model Context Protocol)
- Pre-built Skills | Anthropic Pre-built Skills
- Pre-built Skills | Pre-built Skills in Claude
- Skill Creator | Skill Creator (`skill-creator`)
- Skill Creator | skill-creator
- Skill Creator | Using Skill Creator to Build and Modify AI Skills
- Skill Creator | Using Skill Creator to Generate and Modify Skills

## Facets, not subjects

Positions 8–20 of the measured run. The #404 verdict: *"facets of one subject,
'Skills', shredded into attributes"* — `Skill Modifiability`,
`Skill Reusability`, `Skill Customization`, `Skill Collaboration` are
properties, not knowledge objects.

An extractor emitting any of these is decaying, not enumerating. This list is
what makes a run scorable instead of eyeballed.

- Skill Creation Process
- Skill Validation
- Skill Packaging
- Skill Initialization
- Skill Best Practices
- Skill Modifiability
- Skill Reusability
- Skill Integration
- Skill Customization
- Skill Documentation
- Skill Deployment
- Skill Management
- Skill Collaboration

Added in the first adjudication pass, from 10 measured runs. These are the
document's own workflow-demo headings, which the original list did not cover
because it was transcribed from #404's measured decay tail (all `Skill *`)
rather than from the file's structure.

- Presentation Generation Workflow
- Workflow Integration
- Marketing Campaign Analysis Skill

`Presentation Generation Workflow` and `Workflow Integration` name
`# Generating the Presentation` and `# Combining Skills into a Workflow` —
steps of the end-to-end demo, not subjects.

`Marketing Campaign Analysis Skill` is **the contestable one.** It is carried
in from a previous lesson and modified in one step
(`## Step 1: Modify the Marketing Campaign Skill`), which is why it is filed
here rather than promoted. The counter-reading is real: it does get
`# Updating the Skill to Use BigQuery`, so it has more development than a bare
step. It is judged a facet of the `BigQuery Integration` arc — that subject
exists precisely to hold this material — but a later reader may disagree.

Note the shape: every one of them leads with the word "Skill". That is the same
signal `corpus.py survey` reports as `skillx4` for this file — the document's
own headings cluster around one subject, and the model follows that cluster
past the point where it still names distinct things.

Added in the second adjudication pass (2026-08-07 prompt-A/B sweep, 10 runs per
arm). Same two families as above under yet more spellings — the matcher is
exact on purpose, so every observed phrasing needs its own line. The nine
`Skill Creator Best Practice for X` lines all come from ONE runaway run that
produced 25 objects; they are the decay tail made legible.

- Skill Development Best Practices
- Marketing Campaign Skill
- Skill Initialization Script
- Skill Packaging Script
- Skill Validation Script
- SKILL.md File
- YAML Frontmatter
- Skill Folder Structure
- Skill Assets Folder
- Skill Creator Scripts
- Skill Creator Best Practices
- Skill Creator Workflow
- Skill Creator Python Scripts
- Skill Creator Initialization Script
- Skill Creator Packaging Script
- Skill Creator Validation Script
- Skill Creator Best Practice for Date Range
- Skill Creator Best Practice for Prompting
- Skill Creator Best Practice for Asset Management
- Skill Creator Best Practice for Skill Integration
- Skill Creator Best Practice for Skill Reusability
- Skill Creator Best Practice for Skill Portability
- Skill Creator Best Practice for Skill Documentation
- Skill Creator Best Practice for Skill Testing
- Skill Creator Best Practice for Skill Optimization
- Presentation Creation Workflow
- Presentation Generation
- Presentation Workflow
- Presentation Creation
- Presentation Creation with Skills
- Presentation Generation with Brand Guidelines
- Skill Workflow Integration
- Skill Workflow
- Skill Packaging and Validation
- Skill Development Process
- Skill Development Workflow
- Skill Structure and Best Practices
- Skill Repository
- Data Integration with External Sources
- Brand Guidelines Integration

`Skill Repository` names `## Where the Document Skills Live` (a component of
the `Pre-built Skills` arc), `Data Integration with External Sources`
generalizes the BigQuery arc, and `Brand Guidelines Integration` names the
workflow step that USES the Brand Guidelines Skill, not the skill itself.

### The 2026-10-04 bake-off additions (#1269)

Families follow rulings already here: `Skill Creation Process` (the README's
pinned facet of `Skill Creator`) for the creation process and workflow,
`Workflow Integration` for `# Combining Skills into a Workflow`, `Marketing
Campaign Analysis Skill` for the analysis step, `SKILL.md File`, `Skill
Repository`. Titles fusing two or three subjects into one object — the H1
restated — are scaffolding, the `Puntos pendientes` shape in `medium-10`.

CONTESTED, recorded here in the reading that does NOT credit the subject, with
the other reading stated so it is never resolved silently. The same three
families are contested identically in `small-04-pre-build-skills.md`, and the
pair must not disagree:

  * The end-to-end demo described as a workflow (`Marketing Campaign Analysis
    and Presentation Workflow`, `Building a Data-Driven Presentation Workflow
    with Claude Skills and MCP` and kin): filed as the demo whose steps this
    file already lists as facets (`Presentation Workflow`, `Workflow
    Integration`). The other reading is that the demo IS the `MCP Workflows`
    arc — the H1's third subject is exactly a workflow combining skills and
    MCP, and `small-04` already aliases its own frame for it (`Workflows
    empresariales`) — which would make them aliases of `MCP Workflows`. This
    is the largest contested family in the pass.
  * The umbrella `skills` concept (`Claude Skills`, `Claude Skills System`,
    `Skills in Claude` and kin): filed as a generalisation above this lesson's
    subjects — the lesson presupposes it ("now that we have seen how skills
    fit in the entire AI ecosystem") and #404 read the decay tail as facets of
    it. The other reading is that it names the skills this lesson surveys, an
    alias of `Pre-built Skills`.
  * The marketing-analysis workflow on BigQuery (`Marketing Campaign Analysis
    Workflow using BigQuery and MCP`): filed as the analysis step. The other
    reading is the `BigQuery Integration` arc named by the skill it modifies.

- Anthropic Pre-built and Custom Skills
- Anthropic Pre-built Skills and Skill Creator Framework
- Claude AI Skills and the Skill Creator Tool
- Claude AI Skills Repository
- Combining Custom Skills with Built-in Skills in a Workflow
- Combining Pre-built Skills, Skill Creator, and MCP Workflows
- Combining Skills into a Workflow
- Combining Skills into an Automated Workflow
- Creating a Brand Guidelines Skill and Marketing Workflow
- Creating a Marketing Campaign Analysis Workflow
- Creating Skills with Skill Creator
- Marketing Analysis Workflow with Claude
- Marketing Campaign Analysis Workflow
- Pre-built Skills and Skill Creator
- Pre-built Skills, Skill Creator, and MCP Workflows
- Skill Creation and Integration Workflow
- Skill Creation and Workflow Integration
- Skill Creation Best Practices
- Skill Creation Process using Skill Creator
- Skill Creation Process using skill-creator
- Skill Creation Workflow
- Skill Creator Workflow for Custom Skills
- SKILL.md
- Workflow: Creating and Applying Brand Guidelines Skill for PowerPoint Generation

The CONTESTED rows, in the non-crediting reading:

- Anthropic Claude Skills
- Automated Data-Driven Presentation Workflow
- Automated Marketing Presentation Workflow
- Building a Data-Driven PowerPoint Workflow with Claude Skills and MCP
- Building a Data-Driven Presentation Workflow with Claude
- Building a Data-Driven Presentation Workflow with Claude Skills and MCP
- Building an Automated Marketing Reporting Workflow with Claude
- Building an Automated Presentation Workflow with Claude Skills and MCP
- Building and Orchestrating Skills in Claude with MCP
- Claude Skills
- Claude Skills Framework
- Claude Skills System
- Combining Custom Skills with MCP for Presentation Workflow
- Creating a Data-Driven PowerPoint Workflow with Claude Skills and MCP
- Creating a Data-Driven PowerPoint Workflow with MCP and Custom Skills
- Creating a Data-Driven Presentation Workflow with Claude
- Creating Data-Driven Presentations with MCP and Skills
- Data-Driven Presentation Workflow
- Integrating BigQuery, Brand Guidelines, and PowerPoint via MCP and Custom Skills
- Integrating Skills and MCP for Data-Driven Presentations
- Integrating Skills with MCP Servers for Data-Driven Presentations
- Marketing Analysis to PowerPoint Workflow
- Marketing Campaign Analysis & Presentation Workflow
- Marketing Campaign Analysis and Presentation Workflow
- Marketing Campaign Analysis to PowerPoint Workflow
- Marketing Campaign Analysis Workflow using BigQuery and MCP
- Marketing Data Analysis and Presentation Workflow
- MCP Workflow for Data-Driven Presentation Generation
- Multi-Skill Workflow for Data-Driven Presentations
- Multi-Skill Workflow: Data Analysis to Presentation
- Skill Integration with MCP
- Skills in Claude
- Workflow for Combining Skills with MCP and BigQuery
- Workflow for Creating Data-Driven PowerPoint Presentations
- Workflow for Creating Data-Driven PowerPoint Presentations Using Skills and MCP
- Workflow for Data-Driven PowerPoint Generation via MCP
- Workflow for Generating a Data-Driven PowerPoint Presentation using Claude Skills and MCP
- Workflow for Generating Branded Presentations from BigQuery Data
- Workflow for Generating Data-Driven Presentations using Claude, MCP, and Custom Skills
- Workflow for Marketing Campaign Analysis and Presentation Generation
- Workflow: Combining Custom Skills with MCP and PowerPoint
- Workflow: Creating a Data-Driven Presentation with MCP
- Workflow: Creating Data-Driven Presentations with Custom Skills and MCP
- Workflow: Creating Data-Driven Presentations with MCP and Custom Skills
- Workflow: Data Analysis to PowerPoint Generation via MCP and Skills
- Workflow: Data Analysis to Presentation Generation
- Workflow: Generating a Data-Driven PowerPoint Presentation from BigQuery
- Workflow: Generating a Data-Driven Presentation
- Workflow: Marketing Campaign Analysis and Presentation via Skills and MCP

## Out of scope

Things this document MENTIONS but is not ABOUT — the MinerU rule from
`medium-08-sdk-skills.md`, applied here.

- GitHub Repository

The repository hosting the skills gets passing mentions only ("These skills
live inside the repository", "how GitHub is rendering this Markdown file") —
never a section, never development.

`Anthropic` (the vendor) and `Claude Code` (a host it names once, "install
yourself with tools like Claude Code") are mentions, never developed
(2026-10-04 bake-off pass, #1269).

- Anthropic
- Claude Code

## Near-duplicates

Pairs are written `Canonical Subject | the duplicate phrasing`. The canonical
side must already be a subject above; the other side is a redundant re-naming
that costs a cap slot without adding knowledge.

- Pre-built Skills | Document Skills

**This section read "None." until the first measurement pass, and that was
wrong.** `Document Skills` appeared in 3 of 10 runs, and in every one of them
the SAME run also emitted `Pre-built Skills` — the model spent two of its five
cap slots naming one subject twice. The document itself equates them: the
Excel/PowerPoint pre-installed skills "are known as **document skills**"
(line 52), and `## Where the Document Skills Live` sits inside
`# Pre-built Skills in Claude`.

That makes it a near-duplicate rather than an alias. An alias would be free;
this is not, and scoring it as one would have hidden the cost. It is the first
instance of this failure mode anywhere in the corpus — both ground-truth files
declared it absent, and only the measurement found it.

One candidate was examined and rejected: `Model Context Protocol (MCP)` against
`MCP Workflows`, which the measured run produced as separate objects in
positions 3 and 4. The case for calling them a pair was that the document never
gives MCP a defining section of its own — it appears only as the mechanism
being used (`# Combining Built-in Skills, Custom Skills, and MCP`,
`# Connecting Claude Desktop to BigQuery with MCP`), with its one definitional
mention being a bullet inside a list.

Rejected: the protocol and the workflows built on it are separate things, and
the document developing one through the other does not merge them. Both stay in
the subject list above.

This matters for what the fixture measures. The claim that once stood here —
that with no near-duplicate pair this file isolates cap-too-low and decay
cleanly — no longer holds: the `Document Skills` pair above means all three
failure modes are live on this source. A run producing the 7 correct subjects
and nothing else is still exactly right; a run producing 7 subjects plus
`Document Skills` is spending cap budget on redundancy, and that has to be
read separately from spending it on decay.

### The 2026-10-04 bake-off additions (#1269)

Every line below failed the co-occurrence rule in EVERY reply that emitted it
in the 2026-10-04 bake-off pass: each time, the same reply already named the
subject another way.

**Flagged, not changed: `Document Skills` now fails the rule this line was
decided by.** In the 2026-10-04 pass it is the ONLY name for `Pre-built
Skills` in nine replies (emitted beside `Skill Creator` and `Model Context
Protocol (MCP)`, never beside `Pre-built Skills`). Under the co-occurrence
rule it would be an alias there. The line is left as it is because re-deciding
an existing ruling is a separate change from working a queue; a rescore with
it moved to `## Aliases` is the measurement that would settle whether it
matters.

- Skill Creator | Using Skill Creator to Programmatically Generate Skills

## Notes

The file's H1 is *"Pre-built Skills, Skill Creator, and MCP Workflows"*, which
names the first three subjects outright. That makes it a source-title-twin
probe as well: `_drop_source_title_twins` should prevent an object that merely
restates the whole document, while the three subjects it names must still
survive individually.
