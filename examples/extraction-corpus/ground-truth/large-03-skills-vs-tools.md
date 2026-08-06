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

## Notes

The file's H1 is *"Pre-built Skills, Skill Creator, and MCP Workflows"*, which
names the first three subjects outright. That makes it a source-title-twin
probe as well: `_drop_source_title_twins` should prevent an object that merely
restates the whole document, while the three subjects it names must still
survive individually.
