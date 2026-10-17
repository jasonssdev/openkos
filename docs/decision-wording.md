---
type: Reference
title: OpenKOS Decision Wording
description: Design brief for how the desktop app words each pending decision for someone who has never heard the word "merge" — the question, the options, what each does, how it is undone, and the rules the copy follows.
tags:
  - openkos
  - design-system
  - user-experience
  - reference
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-16T00:00:00Z
sensitivity: public
---

# Decision Wording

This brief settles how the desktop app words a pending decision for a person who has never used a terminal and has never heard "merge", "concept" or "tier". It is the design brief the [roadmap](roadmap.md#mvp-6--the-desktop-app) requires before the review screens are built. It fixes words and rules, not screens: layout, visuals and interaction belong to the app stage and to the [brand](brand.md).

The copy is **English first**. Spanish follows as a translation of this brief, written once the English wording has met a real reader; it is not a separate design.

Each decision maps onto the curation surface of `openkos.api` ([ADR-0053](adr/0053-curation-decisions-are-a-two-phase-application-surface.md), [ADR-0060](adr/0060-the-stable-python-api-is-a-versioned-facade-with-one-error-root.md)) and its judging service ([ADR-0059](adr/0059-judging-is-a-quote-bound-application-surface-that-persists-per-chunk-and-never-merges.md)). The engine terms appear here, in code, only so the app stage can find what each wording belongs to; none of them reaches the screen.

Wording marked **(H)** is a hypothesis. The [MVP 6 exit-criteria session](../evals/desktop_app/PREREGISTRATION.md) checks the ones it reaches: the participant settles one decision and then says, in their own words, what it did, which is compared with the "what it does" line below. Hypotheses that session does not reach stay hypotheses until a later session does.

## Rules

1. **No engine words on screen.** Never: merge, concept, bundle, OKF, tier, volatility, provenance, ingest, adjudicate, judge, canonical, survivor, absorbed, relation, sensitivity, workspace, queue, pending, decision key, token, model call. The table under "Words" gives the plain replacement.
2. **Second person, plain verbs.** "You added", "Do you want to", "We'll keep". "We" is the app.
3. **One decision per screen.** The screen shows one question, its evidence and its options. A list may show many decisions, but deciding happens one at a time — except the bulk acceptances allowed in rule 6.
4. **Say what happens, before and after.** Every option has a one-line "what it does" under it. After the choice, the confirmation repeats it in the past tense and offers the undo where one exists.
5. **"Not now" is not "No".** Every decision offers "Decide later", which changes nothing and keeps the decision in the list. It is never styled as a refusal ([roadmap](roadmap.md), The Quiet Engine: "no" is distinguishable from "not now").
6. **Consent rules carry over unchanged** ([ADR-0053](adr/0053-curation-decisions-are-a-two-phase-application-surface.md), [ADR-0059](adr/0059-judging-is-a-quote-bound-application-surface-that-persists-per-chunk-and-never-merges.md)):
   - **Spending is agreed to in bulk.** One "Check all" agreement covers a batch of checks, with the batch's size shown first. The person is never asked per item to spend.
   - **Combining is never automatic and never in bulk.** Each "same thing?" question is answered on its own screen. No "accept all" ever reaches an identity decision, and checking never combines anything.
   - Bulk acceptance is offered only where the engine already allows it: suggested connections that read the same both ways, and "how quickly does this go out of date" suggestions.
7. **List, never hide.** A decision that cannot be acted on in the app is still listed, with a plain reason. A stale decision is shown as changed, not dropped.
8. **Never show a command.** Where the engine names a CLI command (`resolves_via`), the app shows a plain reason and, if the app cannot act, says so. A screen that tells the person to open a terminal is a defect.
9. **No confidence numbers.** The model's confidence is never shown or used to rank (ADR-0053). The model's reason, in its words, may be shown, labelled as the model's.

## Words

| Engine | On screen | |
| --- | --- | --- |
| workspace | **your library** | (H) |
| concept, document, object | **page** | (H) |
| source (a file in `raw/`) | **file** you added, by its file name | |
| pending queue, open decisions | **To review** | (H) |
| merge | **combine** | (H) |
| keep distinct | **keep separate** | (H) |
| skip | **decide later** | |
| judge, adjudicate, evaluate | **check** ("let the AI model on this Mac check") | (H) |
| relation, edge | **connection** | (H) |
| provenance, citations | **"From:"** followed by file names | (H) |
| volatility tier | **how quickly it goes out of date** | (H) |
| query | **ask** | |
| model | **AI model**, and where it runs: "on this Mac" or the provider's name | (H) |

**What a page is about** is named from its OKF type, so the question can say "the same person" rather than "the same thing" **(H)**: Person → person; Organization → organization; Place → place; Event → event; Project → project; Decision → decision; Procedure → how-to; Concept → idea; Entity → thing; Insight → saved answer; Source → file.

## Evidence on every decision screen

- **The pages involved,** each by its title and a short opening excerpt, so the person can compare them side by side.
- **Where each came from:** "From: allotment-volunteers.md, added 12 October", one line per file. When a page draws on several files, all are listed. **(H)** that file names and dates are enough evidence for a first-time reader.
- **The model's reason,** when there is one, under "Why the AI model thinks so", in quotation marks, as written. Never paraphrased into the app's own voice, so the person can tell the app's statement from the model's guess.
- **A changed decision** (`fresh` is false, or a preview returns `Stale`) says: "Something about these pages changed since this was suggested. Here is the current version." and reloads, never applying the old one.

## Identity — "Are these the same?"

`kind: identity`. Actions `merge`, `merge_group`, `keep_distinct`, `skip`. Neither merge is offered unless the model has judged the group the same. `merge` combines a pair of the same kind; `merge_group` takes its place for more than two pages, or for pages of different kinds, and asks the person to choose which pages are the same and which one stays.

**Question:** "Are these the same {person / place / …}?" with the two pages side by side. **(H)** that a person answers this question faster and more correctly than one about "duplicates".

What the screen says above the question, by `evaluation`:

| Evaluation | Line above the question | Options |
| --- | --- | --- |
| `judged_same` | "The AI model thinks these are the same {person}." | Combine them · Keep separate · Decide later |
| `judged_different` | "The AI model thinks these are different {people}." | Keep separate · Decide later |
| `judged_uncertain` | "The AI model couldn't tell." | Keep separate · Decide later |
| `pending_evaluation` | "These haven't been checked yet." | Check these · Keep separate · Decide later |

A withheld combine is never shown as a disabled button with no reason. The line above the question is the reason. **(H)** that people accept "keep separate" being the only firm answer when the model is unsure, without feeling the app is broken.

**Options and what they do:**

- **Combine them** — "We'll make one page with everything from both. The page keeps the name '{survivor title}'. {n} other pages that mention it will point to the combined page." Shown from the preview (`MergePlan`): the surviving title, the absorbed title and the number of files rewritten. With one such page the sentence reads "1 other page that mentions it will point to the combined page."; with none it is left out. Before the preview, the option shows only "We'll make one page with everything from both." When the plan reports that the pair came from different files, or a type concern, add one line: "These came from different files: check that they really are the same {person}." **(H)** that naming which title survives is enough, without explaining why.
- **Keep separate** — "We won't suggest combining these again." Shown from `KeepDistinctPlan`.
- **Decide later** — "Nothing changes. This stays in To review."
- **Check these** (pending evaluation only) — opens the check consent below. "Checking doesn't change anything. You'll still decide."

**Undo, and how it is shown:**

- After combining: the confirmation says "Combined into '{title}'." with an **Undo** button, which is **Split again** on that combine. Later, the combined page shows "Combined from '{other title}' on {date}" with **Split again**; the lines come from `merge_history`, which keeps each other page's title as it was when combined. Splitting restores both pages exactly as they were (`preview_unmerge`, then `apply_unmerge`; `unmerge` on the CLI). A single split asks "Split '{title}' out again? Both pages go back exactly as they were." and confirms "Split. '{title}' is back as it was." (several: "'{a}' and '{b}' are back as they were."). Until the app has a page view, these lines appear in To review under "Pages you combined", which lists every combined page, newest combine first (`merged_concepts`), so a combine made in an earlier session or from the terminal can be split from the app too. Only the most recent combine on a page can be split directly; when there are later ones, the preview lists them first and the app explains: "To split this, the later combines on this page are split first: {list}. Continue?" and does them in order, all in one apply (`unmerge --to`). **(H)** that "split again" is understood as the undo of "combine".
- After combining a group: the page shows one "Combined from '{other title}' on {date}" line per page that was combined into it, newest first, each with **Split again**. A whole group is undone by splitting its oldest combine: the preview lists every later one first, with the same "the later combines on this page are split first" wording.
- After keeping separate: "Kept separate." with **Undo** (`preview_reopen`, then `apply_reopen`; `duplicates --reopen` on the CLI). The undo confirms "Undone. We'll ask you about these again." and the pages come back to To review once the list is refreshed, which the app does at once. Later, under the library's settings, "Pages you kept separate" lists each pair with **Ask me again** (the same operation; the list is `kept_distinct`, `duplicates --kept-distinct` on the CLI).

### More than two pages — "Which of these are the same?"

`merge_group`, offered in place of `merge` when the group has more than two pages (`more_than_two_members`). **(H)** for the whole subsection.

The screen shows every page side by side, each with a mark the person can set or clear, and asks the question above them. The marked pages are the ones that are the same thing; a second question, "Which one should the combined page keep its name from?", is asked over the marked pages only. The app never picks the name for the person.

**Options and what they do:**

- **Combine the ones I marked** — "We'll make one page from the {n} pages you marked. It keeps the name '{survivor title}'. The pages you didn't mark stay as they are, and we won't suggest combining them with '{survivor title}' again." The button stays disabled, with the line "Mark at least two pages", until two are marked. Shown from the preview (`MergePlan` with its `steps` and `kept_distinct_ids`): the surviving title, the marked titles and the number of other pages that will point to the combined page. When every page is marked, the last sentence is left out.
- **Keep all separate** — "We won't suggest combining any of these again." Shown from `KeepDistinctPlan`.
- **Decide later** — "Nothing changes. This stays in To review."

The "different files" line and the cross-type warning are shown as for a pair.

### Different kinds — "What is {title}?"

`merge_group`, offered in place of `merge` when the pages are of different kinds (`cross_type`). **(H)** for the whole subsection.

**Question:** "What is {title}?" with the pages side by side, each labelled by its kind in the type nouns above. **Options:** one per kind among the pages, worded as "It's a person", "It's an organization", and so on; **They're different things** (keep separate); **Decide later**.

**What it does,** from the plan: "We'll keep one page, as a {person}, named '{survivor title}', and remember it was also described as {an organization}." When the plan says the other kind is not recorded because the page already notes one (`type_outcome: discarded`): "It was already noted as possibly something else, so we won't note {an organization}." The plan does not name the kind already noted, so the sentence does not either. When two pages share the chosen kind, the app first asks which of them keeps its name **(H)**. The "different files" line and the cross-type warning line are shown as for a pair: choosing what something is never happens without them. The app never chooses the kind for the person.

## Connection — "Is this right?"

`kind: relation_type`. Actions `relate`, `relate_reversed` (for one-way kinds only), `skip`. There is no "no" action in the engine: declining is "Decide later".

**Question:** "Is this right?" over one plain sentence built from the suggested type, the two page titles and, as evidence, both pages:

| Suggested type | Sentence | Offers "the other way round"? |
| --- | --- | --- |
| `related_to` | "{A} and {B} are related." | no |
| `references` | "{A} mentions {B}." | no |
| `part_of` | "{A} is part of {B}." | yes |
| `member_of` | "{A} is a member of {B}." | yes |
| `depends_on` | "{A} depends on {B}." | yes |
| `caused_by` | "{A} was caused by {B}." | yes |
| `produced_by` | "{A} was made by {B}." | yes |

**Options:**

- **Yes, connect them** — "We'll record that {sentence}."
- **Yes, the other way round** (where the table says so) — shown as the reversed sentence itself: "No — {B} is part of {A}." "We'll record that instead." **(H)** that showing the reversed sentence beats any "reverse direction" label. One-way suggestions are always decided one at a time, because the model's choice of direction is not reliable.
- **Decide later** — "Nothing changes."

**Bulk:** for `related_to` only, the list may offer "Accept all {n} 'related' suggestions", with the sentences listed before the agreement.

**Undo:** the confirmation, "We recorded that {sentence}.", offers **Undo**, which removes exactly that connection (`preview_unrelate`, then `apply_unrelate`; `unrelate` on the CLI) and confirms "Undone. That connection is removed." Later the page lists its connections, each with **Remove** (the same operation).

## How quickly it goes out of date

`kind: volatility`. Actions `set_tier`, `skip`. The engine suggests one tier for a whole kind of page; the person accepts it or decides later.

**Question:** "How quickly do facts about {kind, plural} go out of date?" with the suggestion: **(H)** for the whole section.

| Tier | Plain line | What it changes |
| --- | --- | --- |
| `static` | "Rarely — they stay true." | "We won't remind you to recheck them." |
| `slow` | "Slowly — over months or years." | "We'll remind you to recheck them now and then." |
| `volatile` | "Quickly — they can change within weeks." | "We'll remind you to recheck them often." |

The screen shows the current setting and the suggestion, each by its plain line, and the model's reason. **Options:** **Use this** ("From now on, {kinds} are treated as changing {slowly}.") and **Decide later**. Bulk acceptance of all such suggestions is allowed. **Undo:** **Undo** on the confirmation puts back the setting the kind had before (`preview_tier_change` with the plan's `configured_tier`, then `apply_tier_change`; when the kind had no setting of its own, the engine's default applies again) and confirms "Undone. {Kinds} are treated as before." When the earlier setting is not one the app can name, the confirmation offers no Undo. Later, the same setting is changeable under the library's settings. The windows behind "now and then" and "often" are the engine's, named in its configuration, and are not repeated in copy.

## Items the app lists but cannot act on

Contradictions, revisions and refused files (`contradiction`, `revision`, `watch_refusal`) carry no actions. They are listed in To review with a plain sentence of what was found ("Two pages say different things about {subject}."; "A newer file may replace an older one."; "A file was not added.") and no command, followed by "This can't be settled in the app yet." The subject is the first page's title. A refused file carries no plain reason in the decision, so none is shown. The app does not invent actions the engine does not offer.

## The check: agreeing to spend

Judging runs the AI model, which costs time and, with a cloud model, money. It follows the quote → consent → run protocol of ADR-0059; the quote is free.

**Consent screen,** one per batch, never per item. It opens from **Check these** on an unchecked pair, and from **Check all** at the top of To review whenever any pair is unchecked:

- Heading: "Check {n} pairs?" ("Check these two?" when one).
- "The AI model on this Mac will compare them. Nothing leaves this Mac." When the backend is not local: "The text of these pages will be sent to the provider of {model} to compare them. Pages marked 'Only on this Mac' are not sent. The provider may charge you." — the same send rule as the cloud warning below. The quote names the model, not its provider, so the provider is named through the model.
- When some are already checked: "{k} are already checked and won't be checked again."
- When the engine is busy in the background: "OpenKOS is also working in the background, so this may be slower."
- "Checking never combines anything. You'll still decide each one."
- When nothing is left to check: "These are already checked." with **OK**.
- Buttons: **Check {n}** · **Not now**. **(H)** that a count, without a time estimate, is enough to consent; the quote gives an upper bound on calls, not a duration.

**While it runs:** "Checking {done} of {n}…" and **Stop**. On stop: "Stopping after the current one. Everything already checked is kept." **After:** "Checked {n}. {m} are ready for you to decide." **Changed since agreeing** (`QuoteStale`): "Something changed since you agreed — the AI model or the pages. Please look again." with the consent screen reloaded; the check never runs on an old agreement.

## Busy, try again, and other refusals

Every message says whether anything changed, and offers the one action that helps. Codes are the local API's ([spec](../openspec/specs/local-http-api/spec.md)).

| Situation | Code | Message | Action |
| --- | --- | --- | --- |
| The library is busy with another change | `workspace_busy` | "OpenKOS is finishing something else. Nothing was changed." | **Try again** (H: automatic retry for a few seconds first) |
| A check is already running | `run_active` | "A check is already running." | **OK** (a check started elsewhere has no id the app can show) |
| The pages changed under the person | `stale`, `write_drift` | "These pages changed while you were looking. Here is the current version." | reload |
| A page in the decision is gone | `member_missing` | "One of these pages no longer exists, so there's nothing to decide." | **OK** (removes it from view) |
| The AI model isn't running | `backend_unavailable` | "The AI model isn't running. OpenKOS needs it to check pages." | **Try again** (**Start it** arrives with guided setup) |
| The AI model isn't installed | `backend_model_not_found` | "The AI model isn't installed. OpenKOS needs it to check pages." | **OK** |
| Saving the choice failed | `record_failed` | "Your choice couldn't be saved. Nothing was changed." | **Try again** |
| The marked pages don't fit the group | `invalid_selection` | "Something about your choice didn't match these pages. Please choose again." | reload **(H)** |
| Only part of a group was combined | `merge_group_interrupted` | "Only some of these were combined. Nothing else changed, and each combine can be split again." | reload **(H)** |
| The page to split was itself combined into another | `survivor_missing` | "This page was combined into another one. Split that first." | **OK** (opening the other page needs a page view) **(H)** |
| The combine is no longer on this page | `not_merged` | "These pages are already separate." | reload **(H)** |
| An undo finds the pages no longer kept separate | `not_kept_distinct` | "These pages aren't kept separate any more." | **OK** |
| An undo finds the connection already gone | `not_related` | "These pages aren't connected any more." | **OK** |
| An undo finds the setting already put back | `tier_unchanged` | "This setting is already as it was." | **OK** |
| The page was changed after it was combined | `survivor_edited` | "This page was changed after it was combined, so it can't be split here." | **OK** only: the app does not offer to split anyway (#1442) |
| Only some of the later combines were split | `unmerge_interrupted` | "Only some of these were split. Each page that was split is back as it was." | reload **(H)** |
| The pages changed and the question is gone | `stale`, then not listed | "These pages changed, and this question no longer applies." | back to To review |
| Any other failure, before a write | — | "Something went wrong. Nothing was changed." ("OpenKOS isn't responding. Nothing was changed." when the engine does not answer) | **Try again** |
| Any other failure, after a write was sent | — | "Something went wrong. Look at To review to see what changed." ("OpenKOS stopped responding. …" when the engine does not answer) | reload |

A busy write is retried for up to five seconds before the message is shown; a preview is never retried blindly. An undo is previewed and applied in one step, because the person has just seen the choice it reverses; a stale undo is never applied again on its own. After a write was sent, a failure the table does not name never says "Nothing was changed".

## Who can see a page

Sensitivity is shown as **who can see it**, never as the word "sensitivity" or its levels. **(H)** for the whole section.

| Level | Label | One-line meaning |
| --- | --- | --- |
| `public` | **Shareable** | "Fine to share or export." |
| `private` (default) | **Private** | "Stays in your library. Sent to a cloud AI model only if you turn one on." |
| `confidential` | **Only on this Mac** | "Never sent anywhere, even with a cloud AI model on." |

The middle line is load-bearing: the engine sends everything except `confidential` to a remote backend, so "Private" must not read as "never leaves". Changing the label is one action from any page. When a combined page holds a page marked "Only on this Mac" (`committed_confidential`), the confirmation adds: "The combined page is marked Only on this Mac."

The cloud model's warning, in settings only and never during setup, uses the same words: what leaves the Mac (pages marked Shareable and Private), what never does (Only on this Mac), who receives it (the provider, by name) and that the provider may charge. While it is on, the app says so on every screen that asks the AI model.

## Open questions

- **Splitting an edited page** is decided (#1442): the app shows the refusal only and never offers to discard the edit; the terminal's `unmerge --discard-survivor-edits` remains the way to do it.
- **Translation.** The Spanish copy follows this brief once the English wording is checked; the type nouns and the tier lines are the parts most likely to need more than a literal translation.
