# Results template: MVP 6 exit session

Copy this file to `../results/YYYY-MM-DD-<dry-run|binding|pilot>.md` and fill it in. A results file is never edited after the fact; a repeated session is a new file. This template is not itself a result and lives outside `results/`.

The bars below are copied from [`../PREREGISTRATION.md`](../PREREGISTRATION.md) as registered. They are here so a scorer need not flip between files. **If this text and the pre-registration differ, the pre-registration governs.** Do not fill in a verdict that moves a bar; if a bar seems wrong, record the observation and leave the verdict as the bar reads (see "Amendment rule").

Do not record the participant's name. Do not paste the recording or its screenshots.

## Session

| Field | Value |
| --- | --- |
| Kind | dry run / pilot / binding |
| Date | |
| Facilitator | |
| Participant code (no name) | |
| Mac model, memory | |
| macOS version and build | |
| Free disk at start | |
| Build version | |
| Installer sha256 (from the release) | |
| Installer sha256 matched? (if the participant checked) | |
| Engine version the app reports | |
| Measured bandwidth (file, size, time, MB/s) | |
| Default models' total bytes (from the session) | |
| Step 2 download allowance (computed before the session) | |
| Recording started at | |

### Screening (verbatim answers)

1. "Have you ever opened Terminal, a command prompt or a shell, or typed a command into one?" (must be no):
2. "Have you used or read about OpenKOS before?" (must be no):
3. "Do you use a Mac regularly?" (must be yes):
4. "Are you comfortable reading English instructions on screen?" (must be yes):

Not a contributor, not in the owner's household, never watched it used: yes / no. Consent signed: yes / no.

## Steps

Times are participant time from the recording's clock, as `mm:ss`. **Help** = any intervention other than re-reading the task text. A step with help is FAIL. **Rescued** = the facilitator completed it.

| Step | Pass condition (as registered) | Limit | Start | End | Elapsed | Help? | Rescued? | PASS / FAIL / NOT_MEASURED |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| S1 Install | First screen shown via the download page, Gatekeeper warning and Open Anyway; no "damaged" dialog; no help; no terminal | 15 min | | | | | | |
| S2 Guided setup | Ollama detected as missing, official download linked, install noticed, every default model pulled with visible progress (changing at least every 10 s while bytes arrive), ends on a screen saying ready and works offline; no help; no terminal | 20 min active + allowance | | | | | | |
| S3 Add files | Both files added through the app and shown as added; network off; no help; no terminal | 5 min to add; each ready within 300 s | | | | | | |
| S4 Ask and cite | Answer on screen names `allotment-committee-notes.md` (or the title the app shows), participant points to or names it; network off | 10 min | | | | | | |
| S5 Settle a decision | Open decision reached and resolved by an action other than "Decide later"; queue shows it resolved; any check agreed to and finished inside the limit; debrief account matches the action's plain description in `docs/decision-wording.md` | 15 min incl. any check | | | | | | |

For each step add evidence below (timestamps from the recording, one line each).

### Per-step evidence

- **S1:**
- **S2:** models pulled and sizes; longest gap without a change in progress; the wording of the final screen:
- **S3:** time each file was added; time each was shown as ready:
- **S4:** each question typed (verbatim); each answer's latency; citation shown; did the participant name the file when asked; did the answer contain `AquaLift 330` (recorded, not scored):
- **S5:** wording of the decision as shown (copied); action taken; was a check needed and how long it took; queue state afterwards:

## Session-level checks

| Check | Pass condition (as registered) | Evidence | PASS / FAIL |
| --- | --- | --- | --- |
| T1 No terminal | No Terminal window, command prompt or shell opens at any point, by the participant or the app | | |
| L1 Launch time | Every launch shows a usable screen within 10 s of the click | each launch: | |
| R1 Cold start | After a restart, network off, app usable within 10 s and answers the step 4 question with the same citation | | |
| O1 Offline after setup | Steps 3 to 5 pass with the network off | | |
| O2 Says it is offline-ready | The screen ending setup says in words the app is ready and works without the internet | exact words: | |
| C1 Cloud model off by default | No screen offers or turns on a cloud model; settings show it off; the workspace config names the local backend at a loopback address | | |
| D1 Total time | Sum of S1 to S5 participant time, excluding S2's download allowance, is at most 75 min | sum: | |

## Rescue log

Every action the facilitator took, in order, with the time and the step it rescued. Empty if none.

## Debrief (verbatim)

1. "What did your choice in that last step do?"
2. "Was there anything you weren't sure about?"

## Quotes

Verbatim, with the recording time and the step. Include everything the participant said that bears on a task, a word on screen, or a feeling, not only the complaints.

| Time | Step | Quote |
| --- | --- | --- |
| | | |

## Searches the participant ran

Time, what they searched, where it led. A search is recorded, not scored; a search that led into a terminal fails T1.

## Notable moments

Hesitations over 30 s, wrong turns, dialogs that surprised the participant, anything the facilitator could not classify. Facts, not interpretations.

## Verdict

Apply the pre-registration's rules; do not add to them.

- Exit met: S1 to S5, T1, L1, R1, O1, O2, C1 and D1 **all** PASS in this one session, with no rescue.
- Not met: otherwise. File each failure as an issue with its timestamp and the participant's words, then repeat with a **new** participant on a freshly cleaned machine.
- S5 NOT_MEASURED (no actionable decision when step 5 started): the session does not count; repeat after the cause is fixed.
- Interrupted by something outside the app: does not count; repeat with a new participant.

**Verdict for this session:** exit met / not met / does not count
**Failures filed as issues:**

## Dry-run section (fill only in a dry-run file)

The dry run is not a session. Its times read no bar. It exists to find what in the kit or the protocol is wrong before the binding session, and to record two things the pre-registration needs.

### Confirmations the pre-registration requires

- [ ] The two materials files put an identity decision in the review queue on the build under test. Decision wording shown:
- [ ] The default models' total size, from the machine: each model and its bytes:
- [ ] The step 2 download allowance, computed from them:
- [ ] Whether the identity decision needed the model's check first, and how long the check took:

### Timings (informational, read no bar)

| Step | Elapsed | Anything slow or odd |
| --- | --- | --- |
| S1 | | |
| S2 | | |
| S3 | | |
| S4 | | |
| S5 | | |
| R1 cold start | | |

### Problems found

For each: what happened, which document or build it concerns, and whether it is a kit problem, a protocol problem (needs an amendment) or a build problem (needs an issue).

| # | What happened | Concerns | Class (kit / protocol / build) | Action |
| --- | --- | --- | --- | --- |

### What changed in the kit afterwards

Fill this **after** the dry run and **before** the binding session. One row per change, each with the commit that made it. If a change touches a task, a bar, a limit or the definition of help, it is a protocol change: it must be a dated amendment in `PREREGISTRATION.md` first, and is not allowed once a binding session has started.

| File changed | Change | Why (the problem # above) | Commit or PR | Needs a pre-registration amendment? |
| --- | --- | --- | --- | --- |

Confirmation that nothing under "Per-step bars" or "Verdict" in the pre-registration was changed outside the amendment list: yes / no.

### Reset to clean

- [ ] The machine was erased and reinstalled and section 2 of `machine-preparation.md` re-verified: date, macOS build
- [ ] The bandwidth was re-measured and the allowance recomputed:
- [ ] The session files were re-placed:
