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
timestamp: 2026-10-17T00:00:00Z
sensitivity: public
---

# Decision Wording

This brief settles how the desktop app words a pending decision for a person who has never used a terminal and has never heard "merge", "concept" or "tier". It is the design brief the [roadmap](roadmap.md#mvp-6--the-desktop-app) requires before the review screens are built. It fixes words and rules, not screens: layout, visuals and interaction belong to the app stage and to the [brand](brand.md).

The copy is **English first**. Spanish follows as a translation of this brief, written once the English wording has met a real reader; it is not a separate design.

Each decision maps onto the curation surface of `openkos.api` ([ADR-0053](adr/0053-curation-decisions-are-a-two-phase-application-surface.md), [ADR-0060](adr/0060-the-stable-python-api-is-a-versioned-facade-with-one-error-root.md), [ADR-0072](adr/0072-contradictions-and-revisions-are-previewed-as-findings-and-applied-as-decisions.md)) and its judging service ([ADR-0059](adr/0059-judging-is-a-quote-bound-application-surface-that-persists-per-chunk-and-never-merges.md)). The engine terms appear here, in code, only so the app stage can find what each wording belongs to; none of them reaches the screen.

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
7. **List, never hide.** A decision that cannot be acted on in the app is still listed, with a plain reason. A stale decision is shown as changed, not dropped. A decision about a page that no longer exists is not a decision: To review is brought up to date on every visit, with no AI model, and that removes it, so the app never asks about a page that isn't there.
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
| supersede, deprecated | **out of date** ("answers won't use it") | (H) |
| reconcile | **both are right**, "checked against each other" | (H) |
| decline (a contradiction) | **leave them as they are** | (H) |
| contradiction, revision | never named: the question says what was found | |
| query | **ask** | |
| model | **AI model**, and where it runs: "on this Mac" or the provider's name | (H) |

**What a page is about** is named from its OKF type, so the question can say "the same person" rather than "the same thing" **(H)**: Person → person; Organization → organization; Place → place; Event → event; Project → project; Decision → decision; Procedure → how-to; Concept → idea; Entity → thing; Insight → saved answer; Source → file.

## Evidence on every decision screen

- **The pages involved,** each by its title and a short opening excerpt, so the person can compare them side by side.
- **Where each came from:** "From: allotment-volunteers.md, added 12 October", one line per file. When a page draws on several files, all are listed. **(H)** that file names and dates are enough evidence for a first-time reader.
- **The model's reason,** when there is one, under "Why the AI model thinks so", in quotation marks, as written. Never paraphrased into the app's own voice, so the person can tell the app's statement from the model's guess.
- **A changed decision** (`fresh` is false) says: "Something about these pages changed since this was suggested. Check them again before you decide." Its only options are **Check again** — "We'll look at these pages as they are now. Checking doesn't change anything. You'll still decide." — and **Decide later**: the engine refuses every other choice on a changed decision (`Stale`, `row_inputs_changed`), so none is offered. Check again first brings To review up to date, which runs no AI model (`refresh_decision_queue`); a decision that is gone then says "These pages changed, and this question no longer applies.", one that is current is shown as it now is, with its own options, and one still changed opens the check's consent for its kind, below. A preview that returns `Stale` for a decision that is not changed says "Something about these pages changed since this was suggested. Here is the current version." and reloads, never applying the old one. A changed disagreement or newer decision (below) cannot be checked again: the check does not cover those kinds and bringing To review up to date does not look at them again, so it says "Something about these pages changed since this was found, so it can't be settled as it was. The app can't look at them again yet." and offers only **Decide later** (#1523). It is not counted among those a check makes ready.

## Identity — "Are these the same?"

`kind: identity`. Actions `merge`, `merge_group`, `keep_distinct`, `skip`. Neither merge is offered unless the model has judged the group the same. `merge` combines a pair of the same kind; `merge_group` takes its place for more than two pages, or for pages of different kinds, and asks the person to choose which pages are the same and which one stays.

**Question:** "Are these the same {person / place / …}?" with the two pages side by side, and "Are these the same thing?" when the pages are of different kinds. It is always the first question, for a pair, a group and pages of different kinds alike, and its two answers, **Same thing** and **Different things**, are shown side by side with the same weight: the app does not lean towards either. What happens next (which pages, or what it is) is asked only after **Same thing**. **(H)** that a person answers this question faster and more correctly than one about "duplicates", and that asking it first keeps a newcomer out of a wrong combine.

What the screen says above the question, by `evaluation`:

| Evaluation | Line above the question | Options |
| --- | --- | --- |
| `judged_same` | "The AI model thinks these are the same {person}." | Same thing · Different things · Decide later |
| `judged_different` | "The AI model thinks these are different {people}." | Different things · Decide later |
| `judged_uncertain` | "The AI model couldn't tell." | Different things · Decide later |
| `pending_evaluation` | "These haven't been checked yet." | Check these · Different things · Decide later |

For pages of different kinds the line names a thing: "The AI model thinks these are the same thing." A withheld combine is never shown as a disabled button with no reason. The line above the question is the reason. **(H)** that people accept "different things" being the only firm answer when the model is unsure, without feeling the app is broken.

**Options and what they do:**

- **Same thing** (a pair) — "We'll make one page with everything from both." It previews the combine, and the preview says: "We'll make one page with everything from both. The page keeps the name '{survivor title}'. {n} other pages that mention it will point to the combined page." Shown from the preview (`MergePlan`): the surviving title, the absorbed title and the number of files rewritten. With one such page the sentence reads "1 other page that mentions it will point to the combined page."; with none it is left out. Before the preview, the option shows only "We'll make one page with everything from both." When the plan reports that the pair came from different files, or a type concern, add one line: "These came from different files: check that they really are the same {person}." **(H)** that naming which title survives is enough, without explaining why.
- **Same thing** (a group, or pages of different kinds) — "Next, you'll mark which ones are the same." or "Next, you'll choose what it is." Nothing is previewed yet: the second question below takes the first one's place, under "You said these are the same.", with **Back** to answer the first one again.
- **Different things** — "We won't suggest combining these again." (for a group: "…any of these again."). It previews keeping them separate, shown from `KeepDistinctPlan`.
- **Decide later** — "Nothing changes. This stays in To review."
- **Check these** (pending evaluation only) — opens the check consent below. "Checking doesn't change anything. You'll still decide."
- **Check again** (changed decisions only, of every kind) — as above, under "A changed decision".

**Undo, and how it is shown:**

- After combining: the confirmation says "Combined into '{title}'." with an **Undo** button, which is **Split again** on that combine. Later, the combined page shows "Combined from '{other title}' on {date}" with **Split again**; the lines come from `merge_history`, which keeps each other page's title as it was when combined. Splitting restores both pages exactly as they were (`preview_unmerge`, then `apply_unmerge`; `unmerge` on the CLI). A single split asks "Split '{title}' out again? Both pages go back exactly as they were." and confirms "Split. '{title}' is back as it was." (several: "'{a}' and '{b}' are back as they were."). Until the app has a page view, these lines appear in To review under "Pages you combined", which lists every combined page, newest combine first (`merged_concepts`), so a combine made in an earlier session or from the terminal can be split from the app too. Only the most recent combine on a page can be split directly; when there are later ones, the preview lists them first and the app explains: "To split this, the later combines on this page are split first: {list}. Continue?" and does them in order, all in one apply (`unmerge --to`). **(H)** that "split again" is understood as the undo of "combine".
- After combining a group: the page shows one "Combined from '{other title}' on {date}" line per page that was combined into it, newest first, each with **Split again**. A whole group is undone by splitting its oldest combine: the preview lists every later one first, with the same "the later combines on this page are split first" wording.
- After keeping separate: "Kept separate." with **Undo** (`preview_reopen`, then `apply_reopen`; `duplicates --reopen` on the CLI). The undo confirms "Undone. We'll ask you about these again." and the pages come back to To review once the list is refreshed, which the app does at once. Later, under the library's settings, "Pages you kept separate" lists each pair with **Ask me again** (the same operation; the list is `kept_distinct`, `duplicates --kept-distinct` on the CLI).

### More than two pages — "Which of these are the same?"

`merge_group`, offered in place of `merge` when the group has more than two pages (`more_than_two_members`). **(H)** for the whole subsection.

The screen first asks "Are these the same {person}?" over every page, with **Same thing** and **Different things**. After **Same thing** it asks "Which of these are the same?" and shows each page with a mark the person can set or clear, none set. The marked pages are the ones that are the same thing; a second question, "Which one should the combined page keep its name from?", is asked over the marked pages only. The app never marks a page or picks the name for the person.

**Options and what they do:**

- **Combine the ones I marked** — "We'll make one page from the {n} pages you marked. It keeps the name '{survivor title}'. The pages you didn't mark stay as they are, and we won't suggest combining them with '{survivor title}' again." The button stays disabled, with the line "Mark at least two pages", until two are marked. Shown from the preview (`MergePlan` with its `steps` and `kept_distinct_ids`): the surviving title, the marked titles and the number of other pages that will point to the combined page. When every page is marked, the last sentence is left out.
- **Different things**, asked first — "We won't suggest combining any of these again." Shown from `KeepDistinctPlan`.
- **Decide later** — "Nothing changes. This stays in To review."

The "different files" line and the cross-type warning are shown as for a pair.

### Different kinds — "What is {title}?"

`merge_group`, offered in place of `merge` when the pages are of different kinds (`cross_type`). **(H)** for the whole subsection.

**Questions:** first "Are these the same thing?", with the pages side by side, each labelled by its kind in the type nouns above, and **Same thing** · **Different things** (keep separate) · **Decide later**. Only after **Same thing**: "What is {title}?", with one option per kind among the pages, worded as "It's a person", "It's an organization", and so on, and **Decide later**. A newcomer is never asked what something is before saying it is one thing.

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

## Two pages disagree — "Which is right?"

`kind: contradiction`. Actions `decline`, `reconcile`, `supersede`, `supersede_reversed`, `skip`, previewed with `preview_finding` (a `ReconcilePlan` or a `DeclinePlan`) and applied with `apply_decision`, like every other decision. `finding` is a `ContradictionDetail`: the two pages, the claims the AI model saw as conflicting, and its reason. "First" and "second" below are the detail's `first_id` and `second_id`. **(H)** for the whole section.

**Question:** "These pages say different things. Which is right?" with both pages side by side. Under "What doesn't match", the conflicting claims, each in quotation marks, as written; under "Why the AI model thinks so", the reason. Both are the model's words, never the app's.

**Options and what they do:**

- **'{first title}' is right** (`supersede`) — "We'll mark '{second title}' as out of date. It stays in your library, but answers won't use it." The page named comes from the plan's `deprecates_id`.
- **'{second title}' is right** (`supersede_reversed`) — the same sentence, the other way round.
- **Both are right** (`reconcile`) — "Both pages stay as they are, and each gets a short note that you checked them against each other."
- **Leave them as they are** (`decline`) — "Nothing on either page changes. We won't point this out again."
- **Decide later** — "Nothing changes. This stays in To review."

**Found inside a combined page.** When the detail has a `merged_absorbed_id`, the disagreement is between a page and one that was combined into it, and only `decline` and `skip` are offered (`reconcile` is withheld as `merged_content`). The question reads: "Part of '{first title}' disagrees with what was combined into it." Options: **Leave it as it is** ("Nothing changes on the page. We won't point this out again.") and **Decide later**, with one line under them: "If combining them was a mistake, you can split it again under 'Pages you combined'."

**Confirmations:** "Marked '{title}' as out of date." · "Noted that both are right." · "Left as they are." ("Left as it is." for one found inside a combined page). The page named is the plan's `deprecates_id`.

**In To review,** the item is named by both pages' titles, with the question under them; one found inside a combined page is named by that page alone.

**Undo:** every confirmation offers **Undo**, and every undo confirms "Undone. We'll point this out again the next time your pages are checked." It does not come back to To review at once: the engine finds it again on its next check of the pages, without asking the AI model again when neither page changed.

- After "Left as they are.": `preview_reopen_contradiction` with the pair and the detail's `merged_absorbed_id`, then `apply_reopen_contradiction` (`contradictions --reopen` on the CLI). Later, under the library's settings, "Disagreements you left as they are" lists each with **Point this out again** (the same operation; the list is `declined_contradictions`, `contradictions --declined` on the CLI).
- After "Marked '{title}' as out of date." and "Noted that both are right.": `preview_unreconcile` with the pair, then `apply_unreconcile`. It removes what the choice added to both pages: the link between them and the short note on each. When the preview's `withdraws_deprecation` is true, the confirmation starts "Undone. '{title}' is no longer marked out of date."; when it is false after marking a page out of date, another page still replaces it, and the confirmation adds "'{title}' stays out of date, because another page replaces it." A note the person reworded around its heading is left as they wrote it. Later, under the library's settings, "Pages you settled" lists each pair with **Undo** (the list is `reconciled_pairs`). The CLI has no undo for this; it is the app's.

## A newer decision — "Does this replace the earlier one?"

`kind: revision`. The engine found a later decision that may reverse or refine an earlier one. `finding` is a `RevisionDetail`: the two pages, `change` (`reverses` or `refines`), `later_id` when the dates say which came later, the quote from each page and the AI model's reason. Previewed and applied as a contradiction is. **(H)** for the whole section.

The pages are shown side by side, each with its quote in quotation marks in place of its excerpt and its date ("12 October", "No date" when there is none) beside its kind, and the model's reason under "Why the AI model thinks so".

**When the dates say which came later** (`later_id` set), one write is offered, the one the dates and the change name, held by the later page:

| `change` | Question | Option | What it does |
| --- | --- | --- | --- |
| `reverses` | "Does '{later}' replace '{earlier}'?" | **Yes, it replaces it** (`supersede…`) | "We'll mark '{earlier}' as out of date. It stays in your library, but answers won't use it." |
| `refines` | "Does '{later}' update '{earlier}'?" | **Yes, it updates it** (`revise…`) | "Both stay current, and '{later}' is noted as an update to '{earlier}'." |

The action is `supersede` or `revise` when the later page is the first, and its `_reversed` form when it is the second; the app reads which one the decision offers, never decides the direction itself.

**When the dates don't** (`later_id` is `None`), the line above the question says: "These have no dates we can compare, so we can't tell which came first." **Question:** "How do these two relate?" **Options:** "'{first}' replaces '{second}'" (`supersede`), "'{first}' updates '{second}'" (`revise`), "'{second}' replaces '{first}'" (`supersede_reversed`), "'{second}' updates '{first}'" (`revise_reversed`), each with the "what it does" line from the table.

**Always offered:**

- **Both still stand** (`reconcile`) — "Both pages stay current, and each gets a short note that you checked them against each other. We won't ask about these two again." This is how a revision is answered "no"; there is no other.
- **Decide later** — "Nothing changes. This stays in To review."

**Confirmations:** "Marked '{earlier}' as out of date." · "Noted that '{later}' updates '{earlier}'." · "Noted that both still stand." Each offers **Undo**, as for a disagreement settled with a write (`preview_unreconcile`, then `apply_unreconcile`), confirmed "Undone. We'll ask about these again the next time your pages are checked.", with the same out-of-date lines when the earlier page was marked.

## A file that wasn't added

`kind: watch_refusal`. It is listed with what happened and, where the app can add the file, an **Add it** action; never with a command. `finding` is a `RefusedFileDetail` with the file's name in your folder and a reason:

| `reason` | What the screen says | Action |
| --- | --- | --- |
| `changed_after_import` | "'{file_name}' wasn't added. A file with this name is already in your library, with different contents. To add this one too, give it a new name in your folder." | none: adding it would be refused the same way (raw files are never replaced). Followed by "This can't be settled in the app yet." |
| `exceeds_pass_budget` | "'{file_name}' wasn't added. It's too large to add automatically." | **Add it** — "We'll show you what adding it takes before anything is sent." It opens the consent screen of "Adding files" below for that one file (`quote_refused_file` with the decision's key). |
| no detail | "A file was not added." | **Add it**, as above; the consent screen shows the refusal if adding it would be refused. |

Adding the file clears the item. Once a file is renamed in the folder instead, OpenKOS adds it the next time it checks your folder, and the item leaves To review. A settled item answers `stale` and is dropped from view. **Add it** opens the Add files screen on that file's consent, so the person agrees there and watches the run there.

## Adding files

**Add files** is its own screen, second in the sidebar. Adding files runs the AI model too, so it follows the same quote → consent → run protocol (`quote_ingest`, `run_ingest`, [ADR-0073](adr/0073-adding-files-through-the-api-is-consented-against-a-worst-case-call-bound.md)); the quote is free and nothing is added before the person agrees. A file is named by its file name, never by its path or as a "source".

**Choosing.** The screen says "Choose files from your Mac. OpenKOS keeps them and makes pages from what is in them." and offers one button, **Choose files…**, which opens the system's file chooser, several files at once (the shell opens it; the page has no dialog permission). Cancelling changes nothing. The chooser takes files, not folders; a folder that reaches the quote anyway is refused with the line under "Not added". "Looking at your files…" shows while the quote is read.

**Consent screen,** one for everything chosen, in one card:

- Heading: "Add {n} files?" ("Add {file}?" when one; n counts the files that will be added, whether read or only kept). When none can be added the heading is "None of these can be added." with **OK**.
- One line per file, its name, and under it a note only when the file needs one:
  - It will be read: no note.
  - Already added and unchanged (`unchanged`): "Already in your library. Nothing to do."
  - Not a text file (`not_text`): "We'll keep this file, but the AI model can't read it, so no pages will be made from it."
  - Empty (`blank`): "This file is empty. We'll keep it, but there is nothing to make pages from."
  - Kept from the AI model by the library's setting (`confidential_floor`): "Your library keeps new files Only on this Mac, so the AI model won't read this one. We'll keep the file without making pages." The app does not offer to let the AI model read such a file (`include_confidential` is never sent).
  - Not added, with the reason, below.
- What leaves the Mac, in the same words as checking, and only when the AI model reads at least one file: "The AI model on this Mac will read {it / both / all of them / {k} of them} and make pages. Nothing leaves this Mac." When the backend is not local: "The text of {this file / these files} will be sent to {host} to make pages with {model}. The provider may charge you."
- With a cloud model only: "At most {calls} requests to {model}, usually fewer." The quote's number is an upper bound, never an estimate, so the copy says "at most". On this Mac the file count is enough and the number is left out.
- Buttons: **Add {n} files** (primary) · **Not now**.

**Not added** (`refusal`), shown on the consent screen and in the result, each with the one thing the person can do:

| Reason | Message |
| --- | --- |
| `changed_after_import` | "A different version of {file} is already in your library, and files you added are never replaced. To add this version too, rename the file and add it again." |
| `duplicate_name` | "Two of the files you chose are called {file}. Rename one of them and add it again." |
| `not_found` | "{file} isn't there any more." |
| `not_a_file` | "{file} is a folder, not a file. Choose the files inside it." |
| `inside_workspace` | "{file} is part of your library's own storage, so it can't be added." |
| `unreadable` | "{file} couldn't be opened. Check that you can open it, then add it again." |
| `inconsistent_workspace`, `symlinked_destination`, `not_absolute` | "{file} can't be added from the app right now." |

**While it runs:** the card says "Adding {index} of {total}: {file}" with the step under it and a progress bar: reading "Opening it…", extracting "The AI model is reading it…", writing "Saving…", and once at the end, refreshing "Almost done" with "Making it searchable…". **Stop** takes effect between files: the button reads "Stopping…" and the card says "Stopping after this file. Files already added are kept." A file the AI model is reading cannot be interrupted, so the stop waits for it. Leaving the screen does not stop the run; the screen is there on return. **The sidebar cue:** while a run goes, **Add files** carries a quiet word beside it, "Adding…"; when the run ends off the screen the word names how, until the person opens the screen: "Added", "Stopped", "Library busy" or "Problem". Nothing else interrupts them: no toast and no badge elsewhere.

**After** (`IngestRunOutcome`): a headline and one line per file, with **OK**. The headline is "Added {n} files." ("Nothing new was added." when none was), "Stopped. Added {n} files; the rest were left." after a stop, and "Your library was busy, so adding stopped." when the library was busy; the last two are warnings, not successes.

- `added`: "{file}: {created} new pages" (and "and {revised} pages updated" when the file added to existing pages rather than repeating them).
- `updated`: "{file}: finished adding."
- `unchanged`: "{file}: already in your library."
- `extraction_degraded`: "{file}: kept, but the AI model couldn't finish reading it. Add it again later to try again."
- `refused`: the reason, above.
- `interrupted`: "{file}: stopped before it was finished. Add it again later."
- `busy`: "{file}: your library was busy. Add it again in a moment."
- `failed`: "{file}: couldn't be added."
- `not_reached`: "{file}: wasn't reached. Add it again later."
- When the search could not be updated (`indexes` is `stale`), a warning under the list: "Your files are added, but search isn't up to date with them yet, so answers may miss them for now."

**Problems before or during a run** show an error with one action, and **Not now** where there is a choice:

- Changed since agreeing (`quote_stale`): "A file or your AI model changed since we looked. Look again before adding." with **Look again**, which quotes the same files again; nothing is added on an old agreement.
- Already adding (`ingest_active`): "OpenKOS is already adding files. Wait for it to finish, then try again." with **Try again**.
- Busy library (`workspace_busy`): "Your library is busy right now. Try again in a moment." with **Try again**.
- The engine restarted while it ran: "OpenKOS restarted. Check To review and Status to see what was added." with **OK**.

## The check: agreeing to spend

Judging runs the AI model, which costs time and, with a cloud model, money. It follows the quote → consent → run protocol of ADR-0059; the quote is free.

**Consent screen,** one per batch, never per item. It opens from **Check these** on an unchecked pair, from **Check again** on a changed decision, and from **Check all** at the top of To review whenever any pair is unchecked or changed ("Let the AI model compare the pages it hasn't checked yet, and the ones that changed. You'll still decide each one."). Before it is shown, To review is brought up to date (no AI model), so a pair whose check is already done is not counted again; after a check, it is brought up to date again, so what was checked can be decided:

- Heading: "Check {n} pairs?" ("Check these two?" when one). For connections, "Check {n} connections?" ("Check this connection?"); for how quickly something goes out of date, "Check {n} suggestions?" ("Check this suggestion?").
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
| Files are already being added | `ingest_active` | "OpenKOS is already adding files. Wait for it to finish, then try again." | **Try again** |
| The library can't be read to add files | `ingest_refused` | "Your library can't be read right now. Nothing was added." | **OK** |
| The pages changed under the person | `stale`, `write_drift` | "These pages changed while you were looking. Here is the current version." | reload |
| A page in the decision is gone | `member_missing` | "One of these pages no longer exists, so there's nothing to decide." | **OK** (removes it from view) |
| The AI model isn't running | `backend_unavailable` | "The AI model isn't running. OpenKOS needs it to check pages. Check setup, on Status, helps you start it." | **Try again** (the app does not start Ollama; setup, from **Check setup** on Status, offers **Get Ollama** and **Check again**) |
| The AI model isn't installed | `backend_model_not_found` | "The AI model isn't installed. OpenKOS needs it to check pages. Setup can download it." | **Open setup** (opens setup, where the model can be downloaded) |
| Saving the choice failed | `record_failed` | "Your choice couldn't be saved. Nothing was changed." | **Try again** |
| The marked pages don't fit the group | `invalid_selection` | "Something about your choice didn't match these pages. Please choose again." | reload **(H)** |
| Only part of a group was combined | `merge_group_interrupted` | "Only some of these were combined. Nothing else changed, and each combine can be split again." | reload **(H)** |
| The page to split was itself combined into another | `survivor_missing` | "This page was combined into another one. Split that first." | **OK** (opening the other page needs a page view) **(H)** |
| The combine is no longer on this page | `not_merged` | "These pages are already separate." | reload **(H)** |
| An undo finds the pages no longer kept separate | `not_kept_distinct` | "These pages aren't kept separate any more." | **OK** |
| An undo finds the connection already gone | `not_related` | "These pages aren't connected any more." | **OK** |
| An undo finds the setting already put back | `tier_unchanged` | "This setting is already as it was." | **OK** |
| An undo finds the disagreement no longer left as it is | `not_declined` | "This has already been undone." | **OK** |
| An undo finds the pages no longer settled that way | `not_reconciled` | "These pages aren't settled that way any more." | **OK** |
| The page was changed after it was combined | `survivor_edited` | "This page was changed after it was combined, so it can't be split here." | **OK** only: the app does not offer to split anyway (#1442) |
| Only some of the later combines were split | `unmerge_interrupted` | "Only some of these were split. Each page that was split is back as it was." | reload **(H)** |
| The pages changed and the question is gone | `stale`, then not listed | "These pages changed, and this question no longer applies." | back to To review |
| Two pages were already settled another way | `already_reconciled` | "You've already settled how these pages relate, in another way. Nothing was changed." | reload |
| The app asked the wrong way (a client defect) | `wrong_preview` | as "Any other failure, before a write" | **Try again** |
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

**Changing it** (`preview_sensitivity`, `apply_sensitivity`) is one action, previewed and applied in one step, as an undo is:

- **Where a page is shown in full** (its card in To review): "Who can see it" over the three labels as one choice, the page's own selected, and its one-line meaning under it. Choosing another changes it at once.
- **Where a page is only named** (the pages an answer came from, in Ask): **Keep only on this Mac** beside each one. Once it is, a quiet "Only on this Mac" takes the button's place.
- **After:** "“{title}” is now {label}." with **Undo**, which puts back the label the page had ("“{title}” is {label} again."). For a file's page, the pages made from it are raised with it, never lowered: "“{title}” is now Only on this Mac, and so is 1 page made from it. To change that page back, change it on its own." ("…so are {n} pages made from it. To change those pages back, change each one on its own."). Then there is no Undo, because putting the one page back would leave the others.
- **Letting a page out while a cloud AI model is on.** Changing a page marked Only on this Mac to Private or Shareable asks first, in a dialog that starts on its safe answer: "Let “{title}” be sent to {host}?" — "While a cloud AI model is on, pages marked {label} are sent to {host} whenever the AI model needs them. “{title}” will be too." — **Mark it {label}** · **Keep it only on this Mac**. Making a page more private never asks.

| Situation | Code | Message |
| --- | --- | --- |
| OpenKOS is busy, after five seconds | `workspace_busy` | "OpenKOS is finishing something else. Nothing was changed." |
| The page changed meanwhile | `stale`, `write_drift` | "This page changed while you were looking. Nothing was changed. Try again." |
| The page is gone | `page_not_found` | "This page no longer exists, so nothing was changed." |
| It already has that label | `sensitivity_unchanged` | no message: the label is shown as it is |
| OpenKOS does not answer | — | "OpenKOS isn't responding. Nothing was changed." ("OpenKOS stopped responding. Look at the page again to see whether it changed." once the change was sent) |
| Anything else | — | "Something went wrong. Nothing was changed." ("Something went wrong. Look at the page again to see whether it changed." once the change was sent) |

The cloud model's warning, in settings only and never during setup, uses the same words: what leaves the Mac (pages marked Shareable and Private), what never does (Only on this Mac), who receives it (the provider, by name) and that the provider may charge. While it is on, the app says so on every screen that asks the AI model.

## Setup

The words of the first run: choosing a folder for the library, then getting the AI model ready on this Mac ([ADR-0057](adr/0057-the-first-model-runtime-is-a-user-installed-ollama-behind-a-guided-setup.md), [ADR-0071](adr/0071-the-desktop-shell-remembers-the-chosen-workspace-in-its-own-config-directory-and-creates-one-with-the-engines-init.md)). Setup is the one time the app uses the internet, and it never sends anything the person added. A cloud AI model is never offered here ([roadmap](roadmap.md#mvp-6--the-desktop-app)): it is turned on in settings only, behind the warning under "Who can see a page". **(H)** for the whole section.

### Welcome — "Choose a folder for your library."

Shown instead of the app when no library is chosen yet (`no_workspace`), with the navigation held back. Title "Welcome to OpenKOS"; under it "Choose a folder for your library." and "Your library is a folder on this Mac. Everything you add to OpenKOS stays there."

- **Create a new library** — opens the system's folder window, then sets the library up in the chosen folder. While it runs: "Setting up your library…". Below the buttons: "A new library needs an empty folder. You can make one in the window that opens."
- **Open an existing library** — opens the folder window, then opens the library in it. While it runs: "Opening your library…".
- Once a folder is accepted: "Opening your library…" until the app is running on it. Closing the folder window changes nothing.
- When the library chosen before is no longer where it was: "The library you used before isn't at {folder} any more. Open it from where it is now, or create a new one."

A refused choice is an error callout worded from the shell's reason alone; its own report, where it has one, waits behind **Show details**:

| Reason | Message | Action |
| --- | --- | --- |
| `folder_not_empty` | "That folder already has files in it. Choose an empty folder, or make a new one, so nothing of yours is changed." | — |
| `already_a_workspace` | "That folder already holds an OpenKOS library." | **Open it** |
| `not_a_workspace` | "That folder doesn't hold an OpenKOS library. Choose another, or create a new library." | — |
| `invalid_path`, `not_a_folder` | "OpenKOS can't use that folder. Please choose another." | — |
| `set_by_environment` | "This copy of OpenKOS is set to use a library chosen outside the app, so it can't be changed here." | — |
| `init_failed` | "OpenKOS couldn't set up a library in that folder." | **Show details** |
| `init_timed_out` | "Setting up the library took too long, so OpenKOS stopped. Please try again." | — |
| `engine_not_located` | "Part of OpenKOS is missing, so it can't set up a library. Reinstalling OpenKOS may help." | — |
| `store_failed`, creating | "Your library was set up, but OpenKOS couldn't remember it. Open it to continue." | **Open it** |
| `store_failed`, opening | "OpenKOS couldn't remember your choice. Please try again." | — |
| anything else | "Something went wrong. Please try again." | — |

### Getting the AI model ready

Shown on its own, once per session, when the library is open but its AI model is not ready (`setup_readiness` with `ready` false), and from Status while that lasts. Title "Set up OpenKOS"; under it "OpenKOS needs the internet only for these steps. Nothing you add is sent." One step at a time, as a card with "Step {n} of 2". "Ready" is the engine's: Ollama answers and the models the library needs to add files and answer questions are installed. A model used only for checking pages does not hold setup back, and "Download all" leaves it out, so a first run never pulls it unasked.

**Step 1, when Ollama on this Mac does not answer:** "Install Ollama". "OpenKOS uses Ollama, a free app, to run AI models on this Mac. It isn't running yet." "Get it from Ollama's website, install it and open it. We'll notice when it's running."

- **Get Ollama** — opens Ollama's download page in the browser. When the browser does not open: "Your browser didn't open. You can go to {address} yourself."
- **Check again** — "Checking…". The app also checks on its own every few seconds while this step is shown.

When the AI model is not Ollama on this Mac (another address in the library's settings), the step is "Reach the AI model": "OpenKOS can't reach the AI model at {address}. Check that it's running, then check again.", with **Check again** only.

**Step 2, models missing:** "Download the AI models". "OpenKOS needs these AI models on this Mac. They're large, so this can take a while. Once they're here, they work without the internet." Each model is listed by its name, with:

- "✓ Installed", or **Download**, one model at a time; asking for another while one runs queues it ("Waiting…"). **Download all** downloads every missing one in turn, offered when more than one is missing.
- While it runs: a progress bar named "Downloading {model}" and "{done} of {size} ({percent}%)" ("{done} so far" before the size is known; "Getting ready…", "Checking the download…", "Finishing…" around it), with **Stop** ("Stopping…"). Sizes are in MB and GB as Finder shows them; the percent reaches 100 only when everything is there.
- After **Stop**: "Stopped at {done} of {size}. Downloading again continues from there." with **Continue download**. Stopping drops the downloads waiting behind it.
- When it fails, with **Try again**:

| Situation | Code | Message |
| --- | --- | --- |
| Ollama stopped answering | `backend_unavailable` | "OpenKOS lost touch with Ollama. Try again: the download continues from where it stopped." |
| Another download is running | `pull_active` | "Another download is already running. Try again in a moment." |
| Ollama has no such model | `backend_model_not_found` | "Ollama couldn't find this AI model to download." |
| The library no longer names it | `model_not_configured` | "Your library doesn't use this AI model any more." |
| The AI model can't be downloaded from the app | `pull_not_supported` | "This AI model can't be downloaded from here." (also shown, with no button, for such a model) |
| OpenKOS does not answer | — | "OpenKOS isn't responding. Try again in a moment." |
| Anything else | — | "The download didn't finish. Try again: it continues from where it stopped." |

**Memory below the floor** (ADR-0047's, as the Mac reports it) is a note on every step, never a block: "This Mac has {n} GB of memory. OpenKOS is made for {floor} GB or more, so answers may be slow and some steps may not finish. You can still continue."

**While it first reads:** a placeholder named "Checking your setup." **When the setup cannot be read:** "OpenKOS couldn't check its setup." with **Try again**.

**Ready:** "You're ready. OpenKOS works offline from now on." with **Start using OpenKOS**, which opens the app. While the model used for checking pages is not installed, a closed "One more AI model" holds it under the button: "This one lets OpenKOS check whether pages are the same or disagree. It's a large download, so it's up to you." with its **Download**. It opens while that download runs and is gone once it is installed. To review reaches it: when a check finds that model missing, its message ends "Setup can download it." with **Open setup**.

## Settings

Where the AI model runs and which library is open, reached from **Settings** at the foot of the sidebar. A view shows what each holds in one line, with the rest behind **Show details** or **Change**. **(H)** for the whole section.

### AI model

The card says where the AI model runs in one line: "On this Mac." or "In the cloud, at {host}." For a cloud AI model it adds "API key: Key saved" or "API key: No key", never the key, with **Remove key** when one is saved ("Removing…", then "The key was removed."). The model names wait behind **Show details** ("AI model", "AI model for search"). **Change** opens the form:

- **Where the AI model runs:** **On this Mac** — "OpenKOS uses Ollama on this Mac. Nothing you add leaves it." — or **A cloud AI model** — "A service on the internet runs the AI model. You'll see what is sent before you turn it on."
- For a cloud AI model: **Address of the service** ("For example https://api.openai.com/v1"), **AI model** ("The name the service gives it."), **AI model for search** ("The service's model for finding pages.") and **API key** ("Kept in your Mac's keychain, never in your library."; "Key saved." in front when one is). The key field is always empty; leaving it empty keeps the saved key. On a Mac that cannot keep a key: "This Mac can't keep a key for OpenKOS, so a service that needs one won't work from here."
- Coming back to this Mac uses the library's own AI models on this Mac; the form asks nothing more.
- **Save** ("Saving…") and **Cancel**. Nothing changes until Save.

**The warning.** Turning on a cloud AI model, or moving to a different service, asks first, in a dialog titled "Use a cloud AI model?" that starts on its safe answer. It states who receives the text, what is sent, what never is and the cost, using the words under "Who can see a page":

1. "OpenKOS will send text from your library to {host} whenever the AI model needs it: when it reads a file you add, checks your pages or answers a question."
2. "Pages marked Shareable or Private are sent. New pages start as Private, so most of your library is sent."
3. "Pages marked Only on this Mac are never sent."
4. "{host} may charge you for this. Check its prices before you continue."

**Use the cloud AI model** applies the change; **Keep it on this Mac** closes the dialog and changes nothing. Going back to this Mac, or changing only the model names or the key of the same service, needs no warning.

**After Save:** "Saved. OpenKOS now uses the AI model at {host}." or "Saved. OpenKOS now uses the AI model on this Mac." When the model for search changed, it adds "Search needs to be rebuilt for the new AI model. Until it is, answers may miss some pages." A key saved on its own: "The key was saved."

**Rebuilding search** follows the AI model card whenever search is behind, as under "Rebuilding search" below.

**While a cloud AI model is on,** the foot of the sidebar shows a quiet **Cloud AI model on**, on every screen, which opens Settings.

| Situation | Code or reason | Message |
| --- | --- | --- |
| The address is not a full web address | `invalid_base_url`, `invalid_embedding_base_url` | "That address doesn't look right. It should start with https:// and have no password in it." |
| No address for a cloud AI model | `base_url_required` | "Enter the address of the service." |
| A name the library cannot hold | `invalid_model` | "That AI model name can't be used. Names use letters, numbers, dots, dashes, colons and slashes." |
| The same, for search | `invalid_embedding_model` | "That search model name can't be used. Names use letters, numbers, dots, dashes, colons and slashes." |
| The settings changed meanwhile | `stale` | "Your settings changed while you were editing them. Here they are now." (and they are read again) |
| OpenKOS is busy, after five seconds | `workspace_busy` | "OpenKOS is busy. Try again in a moment. Nothing was changed." |
| The settings file has edits OpenKOS did not make | `write_refused` | "Your library's settings file has changes OpenKOS didn't make, so it can't change them here. Nothing was changed." |
| OpenKOS does not answer | — | "OpenKOS isn't responding. Nothing was changed." |
| Anything else | — | "Something went wrong. Nothing was changed." |
| The key is refused | `invalid_key` | "That key can't be used. Check that you copied all of it." |
| The keychain fails | `store_failed` | "Your Mac's keychain couldn't keep the key. Try again." |
| The key fails in another way | — | "The key couldn't be saved. Try again." |
| The settings were saved, the key was not | — | "Your AI model settings were saved, but the key wasn't." followed by the key's message |

Nothing changed and no key typed simply closes the form.

### Library

"Your library is the folder where OpenKOS keeps everything.", then "Folder" and the folder. **Open another library…** ("Opening…") and **Create a new library…** ("Setting up…") open the system's folder window; once a folder is accepted, "Opening your library…" until the app runs on it. A refused folder uses the welcome's messages (above). When the library was chosen outside the app: "This copy of OpenKOS is set to use a library chosen outside the app, so it can't be changed here.", with no buttons.

## The sidebar and Status

The words around the decisions, outside the decision screens.

- **Sidebar.** **Ask**, **To review** and **Status**. To review shows a count only when something waits. **Settings** sits at the foot of the sidebar, above the engine's badge, with **Cloud AI model on** above it while one is on.
- **The engine's badge**, at the foot of the sidebar, a symbol and a word: "Engine starting", "Engine running", "Engine restarting", "Engine not running" (it could not be started), "Engine stopped", "No library open" (before a library is chosen; Status then says "No library is open yet."), "Engine state unknown".
- **Check setup.** While the AI model is not ready, Status says "Some setup steps are left." under its title, with **Check setup**, which opens setup. Otherwise Status shows neither.
- **Search on Status.** While search is behind, a rebuild runs, or one ended this session, Your library's card holds it, worded as under "Rebuilding search".
- **Status disclosures.** What is rarely needed waits behind a disclosure named for what it holds: "Show details" for the engine's own report when it failed or restarted, "Show the folder" for where the files live.
- **Status words.** The library's card is **Your library**; its counts are **To review**, **Pages** and **Files you added**, and a status that cannot be read says "Your library's status could not be read." The engine's words (workspace, concept, source) never appear.
- **To review, as a list.** Each row names what it is about: an identity by its pages' titles ("Marguerite Osei · M. Osei"), with its question under it; a connection by its sentence; a how-quickly suggestion by its question; an item the app cannot act on by its plain sentence, with "This can't be settled in the app yet." under it. A badge says **Changed** only on a changed decision. With nothing open beside the list: "Nothing open. Choose something from the list to see its pages and decide."; with nothing to review: "Nothing to review right now. When OpenKOS finds pages that may be the same thing, or a connection to confirm, they wait here." The pages you combined are one row, "Pages you combined", with how many ("1 page", "{n} pages"). A confirmation closes with **Close**.

## Ask

`openkos.api.ask` through the local API's ask job ([ADR-0068](adr/0068-asking-through-the-api-applies-the-query-disclosure-rule-with-one-consent.md)). Asking writes nothing, so no message about it says whether anything changed. **(H)** for the whole section.

- **The screen.** Title "Ask"; under it "Ask about anything in your library. The answer comes from your pages and says which ones." The field is labelled "Your question", with "What would you like to know?" inside it. One button, **Ask**; while the engine starts it is unavailable, with "Waiting for OpenKOS to start…" beside it. A blank question gets "Type a question first." on the field.
- **While it runs.** The button reads "Asking…", and the question shows with one line for the step the engine is on: "Looking through your pages…", "Gathering what's relevant…", "Checking your pages hold an answer…", "Writing the answer…". No counts and no time estimate.
- **The answer** is shown as written, as text, with "From:" followed by the titles of the pages it drew on, each with **Keep only on this Mac** beside it (under "Who can see a page"). When it points to no page: "This answer doesn't point to a particular page."

What the screen says instead of an answer, by `outcome`:

| Outcome | Line under the question | Sentence |
| --- | --- | --- |
| `no_match` (`zero_hits`) | "Nothing matched" | "Nothing in your library matches this question. Try asking with other words." |
| `no_match` (`all_unreadable`) | "Nothing could be read" | "The pages that match this question couldn't be read." |
| `no_answer_in_context` | "No answer in your pages" | "Your library has pages about this, but none of them answers the question." |
| `withheld` | "This answer stays private" | "It drew on {n} pages marked Only on this Mac, so it isn't shown here, and neither are their names. You can show it while your AI model runs on this Mac." |

A withheld answer is a count, never a title: the API names no such page. Under it, **Show this answer** asks the same question again with `include_confidential` (ADR-0075), in place of the withheld answer. It first reads the AI model settings; if the AI model is not on this Mac, or they cannot be read, nothing is sent and the card says "Your AI model isn't on this Mac, so these pages stay private." The consent is never standing and is never offered with a cloud AI model.

**Notes,** small and under the answer, only when they apply:

- A search that may miss something (`stale_indexes`): "Some recent changes to your library may not be searched yet, so this answer could miss them." When part of the search could not run (`lexical_unavailable`, `dense_unavailable`) this one takes its place: "The search was limited this time, so this answer could miss some pages." Under either, the answer is followed by **Rebuild search** when search is behind (below).
- Pages left out (`withheld` above zero on any other outcome): "{n} pages marked Only on this Mac were left out." ("1 page … was left out.")
- An answer the engine could not check first (`sufficiency_degraded`): "This answer wasn't checked against your pages before it was written."

**When asking fails:**

| Situation | Code | Message | Action |
| --- | --- | --- | --- |
| Another question is being answered | `ask_active` | "OpenKOS is already answering another question. Wait a moment, then try again." | **Try again** |
| The AI model isn't running | `backend_unavailable` | "The AI model isn't running. OpenKOS needs it to answer questions." | **Try again** |
| The AI model isn't installed | `backend_model_not_found` | "The AI model isn't installed. OpenKOS needs it to answer questions." | none |
| The answer was cut short | `generation_capped` | "The AI model stopped before the answer was finished. Try again, or ask a shorter question." | **Try again** |
| The AI model failed | `backend_error` | "The AI model ran into a problem." | **Try again** |
| The library can't be read | `workspace_unreadable` | "Your library couldn't be read." | **Try again** |
| The library is gone | `workspace_not_found` | "Your library couldn't be found." | none |
| The engine restarted mid-answer | `not_found` (on the poll) | "OpenKOS restarted while it was answering. Ask again." | **Try again** |
| The question was refused | `invalid_request` | "This question couldn't be asked. Try writing it another way." | none |
| The question is too long | `request_too_large` | "This question is too long. Try a shorter one." | none |
| The engine does not answer | — | "OpenKOS isn't responding." | **Try again** |
| Anything else | — | "Something went wrong." | **Try again** |

**Earlier questions.** The session's last few questions wait behind "Earlier questions", each with one word for how it went: "Answered", "No answer", "Kept private", "Didn't finish". They are kept in memory only and are gone when the app closes.

## Rebuilding search

`openkos.api.search_index` and the rebuild job (`rebuild_search`). Search falls behind when the AI model for search changes, and when pages change after it was last brought up to date. **(H)** for the whole section.

- **Where.** Under the AI model card in Settings, under an Ask answer whose search may have missed something, and in Your library's card on Status: only while search is behind, a rebuild runs, or one ended this session.
- **Why, before.** "Search needs to be rebuilt for the new AI model. Until it is, answers may miss some pages." or, when only pages changed, "Search is behind your latest changes, so answers may miss them. Rebuilding brings it up to date." Then what it costs: "The AI model on this Mac reads your pages again. Nothing leaves this Mac.", or, with a cloud AI model for search, "You'll see what is sent to {host} before it starts." One button, **Rebuild search** ("Starting…").
- **With a cloud AI model for search,** it asks first, in a dialog that starts on its safe answer, with the cloud warning's send rule: "Rebuild search with {host}?" — "OpenKOS will send the text of your pages to {host} so search can find them by what they mean." — "Pages marked Shareable or Private are sent." "Pages marked Only on this Mac are never sent." "{host} may charge you for this." — **Rebuild search** · **Not now**.
- **While it runs:** "Getting ready…", then "Reading {done} of {n} pages…" with a progress bar, then "Finishing…", with **Stop** ("Stopping…"). Nothing is written until the end, so **Stop** loses no page: "Stopped. Search wasn't changed."
- **After:** "Search is up to date." When it could not cover every page: "Search was rebuilt." followed by what was left: "{n} pages marked Only on this Mac weren't sent, so search finds them only by their words." (a cloud AI model for search), "The AI model couldn't read {n} pages. Rebuilding again tries them again.", "{n} pages couldn't be opened." After such a rebuild with a new AI model for search, search still reads as behind, because those pages are not on the new AI model yet.

| Situation | Code | Message |
| --- | --- | --- |
| A rebuild is already running | `rebuild_active` | "Search is already being rebuilt." |
| The AI model isn't running | `backend_unavailable` | "The AI model isn't running. OpenKOS needs it to rebuild search. Search wasn't changed." |
| The AI model for search isn't installed | `backend_model_not_found` | "The AI model for search isn't installed. Search wasn't changed." |
| The AI model failed | `backend_error` | "The AI model ran into a problem. Search wasn't changed." |
| OpenKOS is busy when it writes | `workspace_busy` | "OpenKOS is busy. Try again in a moment. Search wasn't changed." |
| A search store can't be used | `search_unavailable` | "Search couldn't be rebuilt. Search wasn't changed." |
| The library can't be read | `workspace_unreadable` | "Your library couldn't be read. Search wasn't changed." |
| The engine restarted mid-rebuild | `not_found` (on the poll) | "OpenKOS restarted while it was rebuilding search. Search wasn't changed." |
| OpenKOS does not answer | — | "OpenKOS isn't responding." |
| Anything else | — | "Something went wrong. Search wasn't changed." |

Each failure offers **Rebuild search** again while search is still behind.

## Open questions

- **Splitting an edited page** is decided (#1442): the app shows the refusal only and never offers to discard the edit; the terminal's `unmerge --discard-survivor-edits` remains the way to do it.
- **Translation.** The Spanish copy follows this brief once the English wording is checked; the type nouns and the tier lines are the parts most likely to need more than a literal translation.
