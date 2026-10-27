# Adjudication of the edge-typing fixture (#1563)

The first author labelled the 49 items in `fixture.json`. On the owner's delegation a
Claude model labelled all 49 blind with the sheet from `run_edge_typing_screen.py
blind-sheet` (`second_labels.json`), and, again on the owner's delegation, a third model
(Claude Opus) resolved every disagreement. **The owner labelled and resolved none of
them**; the owner may override any resolution at registration.

## Agreement

| | |
| --- | --- |
| items | 49 |
| raw agreement | 38 / 49 = 0.776 |
| Cohen's kappa | 0.696 |
| disagreements | 11 (in all 11 the second label was the first author's flagged alternative) |

`--self-test` reproduces these figures from the recorded `adjudication` records.

## How the disagreements were resolved

Blind to annotator identity. For each item the adjudicator read the two documents, the
production rule verbatim (the definitions, tie-breaks and guard of the system prompt)
and the two candidate labels in sorted order. It did not know which annotator gave which,
did not read either annotator's notes or `how` reasoning, and looked at no stored model
run, result or score. Its verdicts were written down before the sources were revealed.
The adjudicated label could have been a third type; none was.

Each resolution is the item's `adjudication` record (`first`, `second`, `resolved`,
`reason`). The losing label stays as `alt_label`, so lenient scoring still accepts it, and
`adjudicate` keeps comparing a sheet with the first author's labels (`first_label`).

| item | first | second | resolved | matched | reason |
| --- | --- | --- | --- | --- | --- |
| e21 | references | related_to | references | first | SOURCE names TARGET as its output; authorship runs TARGET->SOURCE, so no origin type holds in this direction and SOURCE is not made by, inside or dependent on TARGET, which is the rule's definition of references. |
| e22 | references | related_to | references | first | SOURCE names TARGET as a record it authors; produced_by holds only the other way round, so the explicit naming is references by the rule's own definition. |
| e23 | references | related_to | references | first | SOURCE brought TARGET about, not the reverse, so caused_by does not hold as SOURCE -> TARGET and SOURCE's explicit naming of the outage is references. |
| e27 | references | related_to | references | first | SOURCE produced TARGET (the reverse of produced_by); SOURCE explicitly names TARGET and owes it nothing, which is the rule's definition of references. |
| e29 | references | related_to | references | first | SOURCE is TARGET's author, so produced_by holds only TARGET -> SOURCE; SOURCE explicitly names the document, which is references under the rule. |
| g01 | related_to | references | references | second | SOURCE explicitly links TARGET in its Related list (contrasted with); no specific structural type holds, but the explicit naming satisfies references, and related_to is reserved for when none of the listed types holds. |
| g02 | related_to | references | references | second | Same as the reverse link: SOURCE explicitly names TARGET as a contrasted school, which satisfies references and leaves related_to, the answer when none holds, unused. |
| r01 | depends_on | references | depends_on | first | SOURCE declares it Extends TARGET and that everything else in TARGET stands, so its decision holds only on top of TARGET's view-model architecture. |
| r02 | depends_on | references | references | second | SOURCE cites TARGET for the engine's interpreter, but its Tauri sidecar decision and its measurements (on 3.12 and 3.14) would hold without TARGET, so it is a citation, not a dependency. |
| r03 | depends_on | references | depends_on | first | SOURCE states its decision is forced by TARGET's constraint that the local API adapter may import only openkos.api, so remove TARGET and its reason for gating inside the API falls away. |
| r04 | caused_by | references | caused_by | first | SOURCE exists to close the gap TARGET's set-time-only propagation left (TARGET itself says no bulk backfill ships), so TARGET brought SOURCE about. |

8 resolved to the first author's label, 3 to the second.

## What it did to the floors (`forecast`)

| | first author's labels | resolved labels |
| --- | --- | --- |
| `always references` (the reference for rule J) | 21 / 49 = 0.429 | 24 / 49 = 0.490 |
| `always related_to` | 9 / 49 = 0.184 | 7 / 49 = 0.143 |
| `type signature` | 12 / 49 = 0.245 | 10 / 49 = 0.204 |
| items right under no baseline | 16 | 15 |
| items wrong for the reference | 28 | 25 |

The rule the adjudication applied on the `references`-versus-`related_to` boundary: an
explicit link whose SOURCE names TARGET, where no specific type holds in the direction
shown, is `references`, because the rule defines `references` by that naming and reserves
`related_to` for "when none of the above holds". That reading raises the reference floor.
It is the first of the owner's open questions in the pre-registration draft; if the owner
answers it the other way, the seven `references`/`related_to` resolutions (the five
reversed probes, `g01`, `g02`) flip together.

Known gap: `adjudicate` marks a disagreement as "the first author's flagged alternative"
by comparing with `alt_label`, which for the three items resolved to the second label now
holds the first label. Rerunning `adjudicate` on the second labeller's sheet still reports
38 of 49 and kappa 0.696, but no longer flags those three as the first author's
alternative.
