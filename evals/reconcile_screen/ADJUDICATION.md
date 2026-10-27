# Adjudication of the reconciliation fixture (#1563)

The first author labelled the 121 claims in `fixture.json`. On the owner's delegation a
Claude model labelled all 121 blind with the sheet from `run_reconcile_screen.py
blind-sheet` (`second_labels.json`), and, again on the owner's delegation, a third model
(Claude Opus) resolved both disagreements. **The owner labelled and resolved none of
them**; the owner may override either resolution at registration.

## Agreement

| | |
| --- | --- |
| claims | 121 |
| raw agreement | 119 / 121 = 0.983 |
| Cohen's kappa | 0.975 |
| disagreements | 2 |

`--self-test` reproduces these figures from the recorded `adjudication` records.

## How the disagreements were resolved

Blind to annotator identity. For each claim the adjudicator read the two notes, the sheet's
rule ("a note states a claim when it says it, in any wording; a note that only touches the
topic does not") and the two candidate labels in sorted order, without knowing which
annotator gave which and without reading any annotator's notes, any stored model run,
result or score. Its verdicts were written down before the sources were revealed.

| claim | first | second | resolved | matched | reason |
| --- | --- | --- | --- | --- | --- |
| protocol-seams-04 | A (`a_only`) | AB (`both`) | AB | second | Note B calls them "The Protocol seams", which states in other words that the seams are typing.Protocol classes, as Note A says outright. |
| stoicism-01 | AB (`both`) | A (`a_only`) | A | second | Only Note A ties the dichotomy of control to Enchiridion 1; Note B says the notes cover it while reading the Enchiridion and its translator's introduction, which touches the topic without stating its origin. |

Each resolution is the claim's `adjudication` record (`first`, `second`, `resolved`,
`reason`), with the first label kept as `alt_label`.

## Re-anchoring (the README's rule for a claim that changes sides)

Both claims move between one-sided and shared, so each needed new literals to be scored
(`integrity_problems` is clean after the change; neither claim was retired):

| claim | before | after |
| --- | --- | --- |
| protocol-seams-04 | anchor `` `typing.Protocol` `` (only in Note A) | anchor and `once` literal `Protocol`, exactly once in each note |
| stoicism-01 | anchor and `once` `*Enchiridion*` (once in each note) | anchor `*Enchiridion* 1`, only in Note A; no `once` |

The new anchors also change what `strict (1st)`, the first-author sensitivity, checks for
these two claims, since it scores the same anchors against the first author's kind.

## What it did to the floors (`forecast`)

| | first author's labels | adjudicated labels |
| --- | --- | --- |
| class balance (a_only / b_only / both) | 42 / 46 / 33 | 42 / 46 / 33 |
| `sentence_union` (the reference for rule J) | 93 of 121, 0.770 | 94 of 121, 0.777 |
| `stack` | 88 of 121, 0.729 | 88 of 121, 0.728 |
| `longer_only` | 81 of 121, 0.672 | 81 of 121, 0.673 |
| `absorbed_only` | 79 of 121, 0.654 | 79 of 121, 0.655 |
| `survivor_only` | 75 of 121, 0.617 | 75 of 121, 0.617 |
| cases no baseline merges perfectly | 14 of 15 | 13 of 15 |
| claims the strongest baseline gets wrong | 28 | 27 |
| claims flagged arguable | 2 | 4 |
