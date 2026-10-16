# Pre-registration: MVP 6 exit criteria (The Desktop App)

**Status: Registered; not run.** Written before any app stage starts. The bars below are binding as written, subject only to the amendment rule at the end. The arc is [MVP 6 in the roadmap](../../docs/roadmap.md#mvp-6--the-desktop-app); its record is [#1408](https://github.com/jasonssdev/openkos/issues/1408). There is no harness script: the measurement is one observed session with a person, and this document is its protocol.

## Question

Can a person who has never used a terminal install OpenKOS on a clean machine, add a file, get an answer that cites it, and settle a pending decision, without help and without opening a terminal? That is the roadmap's minimum exit criterion. This document makes each part of it a step with a pass/fail bar, and adds the product constraints the roadmap fixes for the arc: offline after setup, and a cloud model that is off by default and never offered during setup.

## Decisions fixed by the owner (2026-10-16)

1. **One participant.** The exit criteria are validated with one real person who has never used a terminal, on a clean Mac. One session cannot estimate a rate; it can show that the path exists for someone outside the project, which is what the roadmap's criterion asks. Every bar reads that one session.
2. **English first.** The app's copy is English for the session. Spanish follows as a translation and is not measured here.

## The build under test

The binding session runs the app build that closes the arc's last stage, installed exactly as a user would get it: the download page, the unsigned installer and the app's guided setup. The build is identified in the results by its version, its installer's sha256 and the engine version it reports. A session on any other build is a pilot.

## Participant

- **Profile.** An adult who uses a Mac for everyday tasks (a browser, files, email) and has **never used a terminal**: never opened Terminal, a command prompt or any shell, and never typed a command into one, including by following a tutorial.
- **Not a contributor.** Has never contributed to OpenKOS, has not read its documentation or code, and has not watched it being used. Not a member of the owner's household. A friend or acquaintance is acceptable.
- **Recruitment.** By personal invitation. The screening questions, asked before the session and recorded verbatim: "Have you ever opened Terminal, a command prompt or a shell, or typed a command into one?" (must be no); "Have you used or read about OpenKOS before?" (must be no); "Do you use a Mac regularly?" (must be yes); "Are you comfortable reading English instructions on screen?" (must be yes).
- **Consent.** Written consent before the session to the screen recording and to the anonymized results being published. The participant may stop at any point; the recording is deleted on request. No name is published.

## Machine

- A Mac on **Apple Silicon with 32 GB of unified memory**, the floor [ADR-0047](../../docs/adr/0047-the-contradiction-and-identity-judges-default-to-gemma4-26b-a4b-and-the-engine-runs-one-chat-model-at-a-time.md) measured.
- **Clean:** erased and reinstalled with the current macOS release, a new user account, Gatekeeper at its default setting. Not installed: Ollama, OpenKOS, Homebrew, the Xcode command-line tools, any Python other than what macOS ships. Free disk space at least twice the total size of the default models plus the app, recorded.
- A home-style network connection. Its download bandwidth is measured just before the session by downloading a fixed large file from a public mirror and recorded; it sets the model download limit below.
- The two session files in [`materials/`](materials/) are placed in a folder named `OpenKOS session` on the Desktop before the participant sits down. They are synthetic, hold no personal data, and are designed so that: the notes file holds one unique fact (the pump model, `AquaLift 330`) that only it states; and the same person (`Marguerite Osei`) is named in both, which the engine does not attach at ingest for the Person type, so an identity decision appears in the review queue. The facilitator's dry run (below) confirms this on the build under test.

## Roles and observer protocol

- **Facilitator.** One person, who reads the script, starts and stops the recording, operates the network switch, and otherwise stays silent. The facilitator sits behind and to the side of the participant and does not touch the machine during a step.
- **Answering questions.** To any question during a step the facilitator answers only: "I can't help with that. Please do what you would do if you were on your own." Re-reading the current step's task text aloud on request is allowed and is not help.
- **Recording.** The whole session is screen-recorded with audio, using the operating system's own screen recording, started before step 1. The recording's clock is authoritative for every time below. The facilitator also logs, by hand, each step's start and end and any notable moment. The participant is invited, not required, to think aloud.
- **Step start and end.** A step starts when the facilitator finishes reading its task text. It ends when its pass condition is visible on screen, when the participant says they give up, or when its time limit runs out.
- **Storage.** The recording stays private with the owner and is never committed. The committed results hold only timestamps, verdicts, the build identity, the bandwidth, the screening answers and anonymized notes.

### What counts as help

Any intervention by anyone, during a step, other than re-reading that step's task text, **fails that step**. That includes: a hint, a pointed finger, a nod, answering a question about the task or the app, touching the keyboard, trackpad or mouse, or changing a setting for the participant. The participant may use anything on the machine and on the web, as they would at home: the download page, the app's own text, Ollama's official site, and a web search. A web search is recorded and is not help; following one into a terminal fails the session (see T1).

### Rescue

When a step fails, the facilitator completes it in the fewest actions possible, noting each one, so the later steps can still be observed. A rescued step stays failed. Steps after a rescue are measured and reported, but their passes cannot make the session pass.

## Task script

Read to the participant verbatim. The text in brackets is for the facilitator.

> "Thank you for helping. I'm testing an app, not you: if something is hard, that's what I need to find. I can't help you or answer questions while you work, so please do what you would do on your own at home. You can use the web if you want. Say 'I give up' at any point and we'll move on. You're welcome to think aloud."

1. **Install.** [Open a new browser window on the download page.] "This is the page for an app called OpenKOS. Please install it and open it."
2. **Set up.** [Starts when the app's first screen is visible.] "Please follow what the app asks until it says it's ready to use."
   [When the app says it is ready, the facilitator says: "I'm turning off the internet now, as if you were on a plane," turns Wi-Fi off and confirms there is no wired connection. This is protocol, not help.]
3. **Add files.** "In the folder called 'OpenKOS session' on the Desktop there are two files. Please add both of them to OpenKOS."
4. **Ask.** "One of those files says which water pump the allotment committee chose. Please ask OpenKOS which pump they chose, and show me which file the answer came from."
5. **Review.** "OpenKOS may have something it wants you to decide. Please find it and make one decision, whichever you think is right."
6. **Debrief.** [After step 5, recording continues.] "What did your choice in that last step do?" and "Was there anything you weren't sure about?" [Answers recorded verbatim.]

## Per-step bars

Each limit is either **measured** (derived from a recorded number, named) or **chosen** (a judgment fixed now, before any data, with its reason). Times are participant time from the recording.

| Step | Pass condition (all must hold) | Time limit | Basis |
| --- | --- | --- | --- |
| **S1 Install** | The app's first screen is shown, reached from the download page through the Gatekeeper warning and Privacy & Security's Open Anyway; no "is damaged" dialog appears; no help; no terminal. | 15 min | **Chosen.** The installer is about 30 MB (**measured**, [ADR-0056](../../docs/adr/0056-the-engine-ships-frozen-with-pyinstaller-one-directory-under-contents-resources-deep-ad-hoc-signed.md), Python 3.14 `.dmg`), so its download is not the binding term; the limit is for reading the download page's walk-through and finding Open Anyway, which the spike reached but did not time, and did not re-run on the 3.14 build. |
| **S2 Guided setup** | The app detects that Ollama is missing, sends the participant to its official download, notices when it is installed, pulls every default model with **visible progress** (a percentage or byte count that changes at least every 10 s while bytes arrive), and ends on a screen that says it is ready and works offline; no help; no terminal. | 20 min of active time, plus the download allowance | **Chosen** for the active time (the absent-Ollama path and the polling after install are not measured, [ADR-0057](../../docs/adr/0057-the-first-model-runtime-is-a-user-installed-ollama-behind-a-guided-setup.md)). The **download allowance** is computed, not chosen: 2 × (total bytes of the default models ÷ the bandwidth measured before the session) + 5 min. The streamed progress and resumption were **measured** in the spike; the default models' sizes were not, so the bytes are recorded from the session. |
| **S3 Add files** | Both files are added through the app (dropping them on it, or any way the app offers), and the app shows each one as added; no help; no terminal; network off. | 5 min to add; each file shown as ready to ask about within 300 s of being added | **Chosen** for adding. The 300 s is **measured precedent**: The Quiet Engine's B5 bar (every run cites a dropped file within 300 s) was met on the same inbox-watch path ([results](../quiet_engine/results/)). |
| **S4 Ask and cite** | An answer is on screen that names the notes file (`allotment-committee-notes.md`, or the title the app shows for it) as where it came from, and the participant points to or names that file when asked; no help; no terminal; network off. Rephrasing and retrying are allowed within the limit. Whether the answer contains `AquaLift 330` is recorded, not scored. | 10 min | **Chosen.** Answer latency was **measured** at 1.75 to 6.3 s in the app on Python 3.14, including model load (ADR-0057), so the limit is for finding where to ask and reading the citation, not for the model. Each answer's latency is recorded. |
| **S5 Settle a decision** | The participant reaches an open decision and applies an action that resolves it (anything but "decide later"); the queue then shows it resolved. If the decision needs the model's check first, the participant agrees to the check and the check finishes inside the limit. In the debrief, the participant's account of what their choice did matches, in substance, the plain description of that action in the [decision-wording brief](../../docs/decision-wording.md); no help; no terminal; network off. | 15 min, including any check | **Chosen.** No measurement exists of a judge check's duration on a clean machine at this size; it is recorded. |

### Session-level checks

| Check | Pass condition | Basis |
| --- | --- | --- |
| **T1 No terminal** | No Terminal window, command prompt or shell opens at any point in the session, whether the participant or the app opens it. A failure here fails the session. | The roadmap's criterion. |
| **L1 Launch time** | Every launch of the app in the session shows a usable screen within 10 s of the click that opened it. | **Chosen**, with margin over the **measured** engine start: spawn to first successful request, median 0.078 s and max 0.102 s on Python 3.14 ([ADR-0055](../../docs/adr/0055-the-desktop-shell-is-tauri-2-with-the-engine-as-a-supervised-sidecar.md)), with a warm page cache. The margin is for the shell's own window and for the cold start the spike did not measure. |
| **R1 Cold start** | After the participant leaves, the facilitator restarts the Mac, keeps the network off, opens the app, and it shows a usable screen within 10 s and answers the step 4 question with the same citation. | **Chosen**, as L1. This is the first true post-reboot cold start, which the spike named as not measured. |
| **O1 Offline after setup** | Steps 3 to 5 pass with the network off. If any of them fails because the app needed the network, O1 fails even when the step's other conditions would hold. | Roadmap: "online to set up, offline to work". |
| **O2 Says it is offline-ready** | The screen that ends setup says, in words, that the app is ready and works without the internet. | Roadmap. |
| **C1 Cloud model off by default** | From the recording: no screen during install or setup offers, mentions as a choice, or turns on a cloud model. After the session: the app's settings show the cloud model off, and the workspace's configuration names the local backend at a loopback address. | Roadmap: optional, off by default, never offered during setup. |
| **D1 Total time** | The sum of the participant time of S1 to S5, excluding S2's download allowance, is at most 75 min. | **Chosen:** the per-step limits sum to 65 min; the 10 min margin covers moving between steps. |

## Verdict

- **Exit met** when S1 to S5, T1, L1, R1, O1, O2, C1 and D1 all read PASS in one session, with no rescue.
- **Not met** otherwise. The arc does not exit. Each failure is recorded in the results with its timestamp and the participant's words, filed as an issue, and fixed. The session is then repeated with a **new participant** who meets the same profile, on a freshly cleaned machine, under the same bars. A participant who has seen the app once is no longer a first-time user.
- **NOT_MEASURED** for S5 when the review queue holds no actionable decision by the time step 5 starts. That is a failure of the materials or the build, never a pass; the session does not count and is repeated after the cause is fixed.
- A session interrupted by something outside the app (a power cut, a participant who has to leave) does not count and is repeated with a new participant.

## Before the binding session

- **Dry run.** The facilitator runs the whole script alone on a clean machine of the same specification with the build under test, timing each step. It confirms the materials put an identity decision in the review queue, and records the default models' total size. The dry run is not a session: its times read no bar, and the facilitator may use any tool for diagnosis. If the dry run forces a protocol change, it goes through the amendment rule.
- **Pilots.** Sessions with a participant on an earlier build are pilots. They can find defects and are encouraged; they never read a bar, and a pilot's participant cannot take the binding session.

## What is explicitly not measured

- Windows and Linux, and any Mac below the 32 GB floor or on Intel.
- A signed or notarized build; the app's own update path (the spike measured it, [ADR-0055](../../docs/adr/0055-the-desktop-shell-is-tauri-2-with-the-engine-as-a-supervised-sidecar.md)).
- How many people in general can do this: one session is an existence check, not a rate.
- Answer quality beyond the citation; retrieval and extraction quality have their own harnesses under `evals/`.
- Whether the decision taken was the right one. S5 measures that the participant could decide and understood what the choice did, not the decision's correctness.
- Decisions other than the one the participant takes; the other kinds' wording stays a hypothesis of the brief until a later session reaches them.
- Turning a cloud model on, the API-key storage, export and import, and any feature outside the app's minimum scope (add files, ask, review).
- The Spanish translation, accessibility, and long-term use.
- Antivirus behaviour and a machine where Ollama is already installed or another client is saturating it ([ADR-0057](../../docs/adr/0057-the-first-model-runtime-is-a-user-installed-ollama-behind-a-guided-setup.md)).

## Results

The session's record is a new file under `results/` (timestamps, verdicts, build identity, bandwidth, model bytes, the screening answers and anonymized notes). Like every result under `evals/`, it is not edited after the fact; a repeated session is a new file.

## Amendment rule

This document may be amended only **before the first binding session**. The dry run and pilots may prompt an amendment; each amendment is dated, gives its reason, and is listed below; the original text it changes stays visible in git history. Once the first binding session has started, the bars are frozen: a bar that proves wrong is replaced by a new, dated pre-registration written before the next session, never by editing this one, and never by lowering a bar after a session has failed it.

### Amendments

None.
