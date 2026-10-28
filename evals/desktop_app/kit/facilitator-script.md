# Facilitator script: MVP 6 exit session

This is the facilitator's copy of the protocol in [`../PREREGISTRATION.md`](../PREREGISTRATION.md). If this file and the pre-registration ever disagree, the pre-registration governs and this file is wrong. Nothing here changes a bar, a task or a limit; changing one is an amendment, and amendments are only possible before the first binding session.

The same script serves the dry run and the binding session. Only the participant differs (see "Dry run" in the pre-registration).

## Before the participant arrives

Tick every box in [`machine-preparation.md`](machine-preparation.md). Then:

- [ ] The participant has passed screening (below) and signed [`consent.md`](consent.md).
- [ ] The results file for this session is created from [`results-template.md`](results-template.md) and the build identity is filled in.
- [ ] Screen recording with audio is ready to start, using macOS's own recorder.
- [ ] You have a clock or notes app **off** the participant's Mac for your hand log.
- [ ] Wi-Fi is on and the Mac is not on a wired connection (so you can turn it off at the step 2 hand-off and confirm nothing else is connected).
- [ ] Your seat is behind and to the side of the participant.

## Screening (before the day; record answers verbatim)

Ask exactly these, in this order, and write the answers down word for word:

1. "Have you ever opened Terminal, a command prompt or a shell, or typed a command into one?" Must be **no**.
2. "Have you used or read about OpenKOS before?" Must be **no**.
3. "Do you use a Mac regularly?" Must be **yes**.
4. "Are you comfortable reading English instructions on screen?" Must be **yes**.

Also confirm, without leading: not a contributor to OpenKOS, not a member of the owner's household, has never watched it being used. Any other answer: do not run the binding session with this person.

## What you may say

Only these things:

- The opening statement and each task text, read **verbatim** from the script below.
- Re-reading the current step's task text aloud, when asked. Nothing added, nothing emphasised.
- "I can't help with that. Please do what you would do if you were on your own." in answer to any question during a step.
- At the step 2 hand-off: "I'm turning off the internet now, as if you were on a plane."
- The two debrief questions, verbatim.
- Safety and consent matters outside a step (a break, stopping, the recording).

## What you may not do

During a step, any of the following **fails that step** (the pre-registration's definition of help):

- A hint, a pointed finger, a nod or shake of the head, a sigh, a look at the screen that draws attention to something.
- Answering a question about the task or the app, including "yes, that's right" or "keep going".
- Naming a button, a screen, a menu or a setting. Saying what a word on screen means.
- Touching the keyboard, trackpad or mouse, or changing any setting for the participant.
- Telling the participant how long they have, or how they are doing against a limit.
- Opening or showing the participant any document of this kit.

Also never: ask a leading question ("did you see the...?"), fill a silence with encouragement that carries information, or react to an answer in a way that signals right or wrong. Neutral acknowledgement that carries no information ("thank you") is fine between steps.

The participant may use anything on the machine and the web, as at home: the download page, the app's own text, Ollama's official site, a web search. Record each search; a search is not help. If the participant follows a search into a terminal, that fails T1 and so the session; do not intervene to prevent it.

## When a step fails

A step fails when its time limit runs out, the participant says "I give up", or any help occurred. Then:

1. Note the time and the reason in the hand log.
2. **Rescue:** complete the step yourself in the fewest actions possible, noting each action, so later steps can still be observed. A rescued step stays failed, and later steps cannot make the session pass.
3. Tell the participant only: "Thanks. I'm going to get us to the next part." Then read the next task.

## Opening statement (read verbatim, then start the recording)

> "Thank you for helping. I'm testing an app, not you: if something is hard, that's what I need to find. I can't help you or answer questions while you work, so please do what you would do on your own at home. You can use the web if you want. Say 'I give up' at any point and we'll move on. You're welcome to think aloud."

Start the recording before step 1. The recording's clock is authoritative for every time.

## Tasks

A step starts when you finish reading its task text. It ends when its pass condition is visible on screen, when the participant says they give up, or when its limit runs out. Log start and end by hand and read the exact times off the recording afterwards.

### 1. Install

[Open a new browser window on the download page.]

> "This is the page for an app called OpenKOS. Please install it and open it."

- Starts: after you finish reading.
- Pass condition (S1): the app's first screen is shown, reached from the download page through the Gatekeeper warning and System Settings' Privacy & Security, Open Anyway; no "is damaged" dialog; no help; no terminal. Limit **15 min**.
- Watch for, and log without reacting: how long the participant spends on the download page's walk-through; any "damaged" dialog; any Terminal window.

### 2. Set up

[Starts when the app's first screen is visible.]

> "Please follow what the app asks until it says it's ready to use."

- Pass condition (S2): the app detects Ollama is missing, sends the participant to its official download, notices when it is installed, pulls every default model with visible progress (a percentage or byte count changing at least every 10 s while bytes arrive), and ends on a screen that says it is ready and works offline (the app's words: "You're ready. OpenKOS works offline from now on."). Limit **20 min active time plus the download allowance**.
- Download allowance = 2 x (total bytes of the default models / the bandwidth measured before the session) + 5 min. Compute it **before** the session from the numbers in the results file and write it down; do not compute it afterwards.
- Active time excludes time spent only waiting on a download whose progress is visibly advancing.
- Watch for: progress that stalls for more than 10 s while bytes should be arriving; any screen that offers, mentions as a choice or turns on a cloud model (C1); the exact words of the final screen (O2).
- Log which models the app actually pulled and their sizes (see the open question in the PR; the pre-registration says "every default model").

**Hand-off when the app says it is ready.** Say: "I'm turning off the internet now, as if you were on a plane." Turn Wi-Fi off and confirm there is no wired connection. This is protocol, not help. Write down the time.

### 3. Add files

> "In the folder called 'OpenKOS session' on the Desktop there are two files. Please add both of them to OpenKOS."

- Pass condition (S3): both files are added through the app and the app shows each as added; network off. Limit **5 min** to add; each file shown as ready to ask about within **300 s** of being added.
- Note the time each file is added and the time it is shown as ready.

### 4. Ask

> "One of those files says which water pump the allotment committee chose. Please ask OpenKOS which pump they chose, and show me which file the answer came from."

- Pass condition (S4): an answer is on screen that names the notes file (`allotment-committee-notes.md`, or the title the app shows for it) as where it came from, **and the participant points to or names that file when asked** (the task text asks it). Rephrasing and retrying are allowed. Limit **10 min**.
- Record, not scored: whether the answer contains `AquaLift 330`; each answer's latency; each question the participant typed, verbatim.

### 5. Review

> "OpenKOS may have something it wants you to decide. Please find it and make one decision, whichever you think is right."

- Pass condition (S5): the participant reaches an open decision and applies an action that resolves it (anything but **Decide later**); the queue then shows it resolved. If the decision needs the model's check first, the participant agrees to the check and it finishes inside the limit. In the debrief, their account of what the choice did matches, in substance, the description in the table below. Limit **15 min**, including any check.
- If, when step 5 starts, the review queue holds no actionable decision: the result is **NOT_MEASURED**, not a pass; the session does not count (see Verdict in the pre-registration). Do not rescue by creating one.
- Record: the wording the app showed (copy it), which action was taken, whether a check was needed and how long it took.

**Reference for scoring the debrief (facilitator only; never show or paraphrase it to the participant).** The app's description of each action, from [`docs/decision-wording.md`](../../../docs/decision-wording.md), Identity:

| Action taken | What the app says it does | A matching account says, in substance |
| --- | --- | --- |
| Same thing | "We'll make one page with everything from both." After it: "Combined into '{title}'." with **Undo**. | The two pages became one, with everything from both. |
| Different things | "We won't suggest combining these again." After it: "Kept separate." with **Undo**. | They stay separate and the app will not suggest combining them again. |
| Decide later | "Nothing changes. This stays in To review." | Does not resolve the step. It cannot pass S5. |

A match is judged on substance, not on the app's exact words. Judge it only after the debrief, from the recording, and write the participant's words verbatim beside your verdict.

### 6. Debrief

[Recording continues.] Ask, and record verbatim:

1. "What did your choice in that last step do?"
2. "Was there anything you weren't sure about?"

Do not correct, confirm or follow up on either answer. If they ask whether they were right, say: "I can't say yet. Thank you."

## After the participant leaves

1. **R1 cold start.** Restart the Mac with the network still off. Open the app and time it to a usable screen (limit 10 s). Ask the step 4 question and check it gives the same citation. Record both.
2. **C1 after the session.** Open the app's settings and confirm the cloud model is off. Read the workspace's `openkos.yaml` and confirm `backend` names the local backend at a loopback address. (You may use any tool for this after the participant has left; it is not part of the session.)
3. Stop the recording. Keep it private with the owner; never commit it.
4. Fill the results file the same day, from the recording. Judge each step from the recording's clock, not from memory.
5. Apply the Verdict rules. Then clean the machine again before any further session ([`machine-preparation.md`](machine-preparation.md), "Between sessions").
