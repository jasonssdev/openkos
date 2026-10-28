# Machine preparation: the clean Mac

The machine requirements are fixed by [`../PREREGISTRATION.md`](../PREREGISTRATION.md) ("Machine"); this checklist makes them doable and adds the facilitator-side steps the pre-registration leaves implicit. If they disagree, the pre-registration governs. Do this for the dry run and again for the binding session.

Do not run any model, and do not install anything beyond what is listed under "Facilitator-side", on the participant's machine before the session. The dry run is where models get pulled; the binding session must pull them itself.

## 1. The hardware

- [ ] Apple Silicon, **32 GB** unified memory (the floor measured in [ADR-0047](../../docs/adr/0047-the-contradiction-and-identity-judges-default-to-gemma4-26b-a4b-and-the-engine-runs-one-chat-model-at-a-time.md)). Record the model identifier and memory from About This Mac.
- [ ] Charger connected for the whole session.
- [ ] Free disk: at least **2 x the total size of the default models + the app** (the pre-registration's bar). Using the README's figures, the default models are about **25 GB** (`qwen3:8b` 5.2 GB, `bge-m3` 1.2 GB, `gemma4:26b-a4b` 18 GB; confirm the actual sizes in the dry run, which is when they are first measured), plus the app (the `.dmg` is about 30 MB, ADR-0056). So at least about **51 GB free**; record the real number, and prefer 100 GB or more so disk is never the reason a step fails.

## 2. What "clean" means

All of these, as written in the pre-registration:

- [ ] **Erased and reinstalled** with the current macOS release (Erase All Content and Settings, or recovery-mode erase and reinstall). Record the macOS version and build.
- [ ] A **new user account**, created in Setup Assistant, used for the session. No migration from another Mac or a backup.
- [ ] **Gatekeeper at its default setting** (System Settings > Privacy & Security > "Allow applications from": App Store and identified developers). Do not change it, and do not run `spctl`.
- [ ] **Not installed:** Ollama, OpenKOS, Homebrew, the Xcode command-line tools, any Python other than what macOS ships. Check by looking in Applications and in Finder's search; do not open Terminal as the participant's account to look.
- [ ] No Terminal in the Dock, and no recent-items traces that would prompt a participant towards it.
- [ ] No browser sign-in, saved passwords or history from the facilitator. A default browser, signed out. Safari is fine.
- [ ] Screen recording permission for the recorder is the only privacy permission granted in advance.

If any item cannot be met, the session is a pilot, never the binding session.

## 3. Network

- [ ] Home-style connection, Wi-Fi only, no Ethernet adapter attached (so that turning Wi-Fi off at the step 2 hand-off really takes the Mac offline).
- [ ] **Measure the bandwidth just before the session**: download a fixed large file from a public mirror, note the file, its size, the time and the resulting MB/s. Record in the results file. It sets the download allowance for step 2; compute that allowance now and write it in the results file before the session starts:

  `allowance = 2 x (total bytes of the default models / measured bandwidth) + 5 min`
- [ ] The Mac can reach `ollama.com`, GitHub (the download page and release asset) and Ollama's model registry.
- [ ] Automatic updates and notifications: turn off notification banners (Focus on) so nothing pops up mid-step.

## 4. The build under test

- [ ] The binding session installs the build **exactly as a user would get it**: from the download page, the unsigned `.dmg`, then the app's guided setup. Do not pre-download or pre-copy it.
- [ ] The published release asset and the download page exist and work (they are separate issues this one is blocked on).
- [ ] Record the build identity **before** the session from the published release, not from the Mac: the version, the installer's sha256 (from the release notes and `OpenKOS.dmg.sha256`), and the engine version the app reports once it runs. For the binding session this is the published v0.7.0 `.dmg`; a session on any other build is a pilot.

## 5. The session files

- [ ] Create a folder named exactly `OpenKOS session` on the Desktop of the session account.
- [ ] Copy into it the two files from [`../materials/`](../materials/), unchanged: `allotment-committee-notes.md` and `allotment-volunteers.md`.
- [ ] Nothing else is on the Desktop.
- [ ] The browser window the facilitator opens at step 1 is on the download page; no other tabs.

## 6. Facilitator-side

- [ ] Recording: start macOS's own screen recording (Screenshot app > Record Entire Screen, with the microphone) before step 1. Test it once beforehand on a different account and delete the test.
- [ ] A watch or phone clock off the Mac for the hand log; the recording's clock is authoritative.
- [ ] Printed copies of [`consent.md`](consent.md) and [`facilitator-script.md`](facilitator-script.md). The script is for you only; the participant sees only the consent and the task texts you read aloud.
- [ ] A seat behind and to the side of the participant. A second device (not the session Mac) for anything you need to look up.
- [ ] Results file created from [`results-template.md`](results-template.md), with everything in "Before the session" filled.

## Between the dry run and the binding session

The dry run changes the machine: it installs Ollama, pulls the models into `~/.ollama` of the dry-run account, installs the app and creates a library. None of that may survive into the binding session.

1. [ ] Apply any kit or protocol changes the dry run found. A protocol change goes through the pre-registration's amendment rule and must be committed **before** the binding session starts.
2. [ ] **Erase the Mac and reinstall macOS again**, then repeat section 2 in full. A new user account on the same install is **not** enough: Ollama, the app in `/Applications` and the Gatekeeper approval are system-wide, and a stale approval would hide the S1 walk-through.
3. [ ] Re-measure the bandwidth and recompute the allowance (section 3). Use the dry run's measured model sizes to fix the byte total.
4. [ ] Re-place the session files (section 5).

Between sessions of any other kind (a pilot, or a repeat after a failed binding session): same, with a **new** participant each time. A participant who has seen the app once is no longer a first-time user.

## After the session

- [ ] Restart the Mac for R1 (network still off), as the facilitator script says.
- [ ] Keep the recording private. Do not commit it, or any screenshot of the participant.
