# Ground truth — `large-03-skills-vs-tools.md`

Source size: 16948 B

**Pre-filled from the maintainer's own analysis in
[#404](https://github.com/jasonssdev/openkos/issues/404), not authored fresh.**
That comment inspected which objects the cap discarded on a real run of this
exact file and judged them one by one. Reproduced here so the judgment lives
beside the fixture instead of only in an issue thread.

One item still needs a human decision — marked **NEEDS A CALL** below. Settle
it before running any measurement against this file.

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

Note the shape: every one of them leads with the word "Skill". That is the same
signal `corpus.py survey` reports as `skillx4` for this file — the document's
own headings cluster around one subject, and the model follows that cluster
past the point where it still names distinct things.

## Near-duplicates

**None.**

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

This matters for what the fixture measures. With no near-duplicate pair here,
this file isolates the other two failure modes — cap-too-low and enumeration
decay — cleanly. A run producing 7 correct subjects and nothing else is exactly
right on this source, with no third mode muddying the score.

## Notes

The file's H1 is *"Pre-built Skills, Skill Creator, and MCP Workflows"*, which
names the first three subjects outright. That makes it a source-title-twin
probe as well: `_drop_source_title_twins` should prevent an object that merely
restates the whole document, while the three subjects it names must still
survive individually.
