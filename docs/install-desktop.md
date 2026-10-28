# Install the desktop app (macOS)

The OpenKOS desktop app is a **developer build**: it is not signed with an Apple Developer ID and not notarized, so macOS blocks it the first time you open it. Read this page before you download. It tells you what the app needs, why macOS objects, the exact clicks that get past it, and how to check that the file you got is the one the project published.

If you are comfortable in a terminal, the [command-line install](../README.md#quickstart) needs no Gatekeeper approval; the desktop app runs the same engine.

## What the app needs

- **A Mac with Apple Silicon.** Intel Macs, Windows and Linux are not covered ([roadmap](roadmap.md), MVP 6 constraints).
- **32 GB of unified memory for the default setup.** That is the floor measured for the default models: the judge model peaks near 21.5 GB while it runs, and the engine keeps one chat model resident at a time ([ADR-0047](adr/0047-the-contradiction-and-identity-judges-default-to-gemma4-26b-a4b-and-the-engine-runs-one-chat-model-at-a-time.md)). The [README's system requirements](../README.md#system-requirements) give the smaller setups and the disk needed for the models.
- **Ollama.** The app does not bundle a model runtime. Its setup step checks for [Ollama](https://ollama.com), sends you to its official download if it is missing, then pulls the models and shows the progress ([ADR-0057](adr/0057-the-first-model-runtime-is-a-user-installed-ollama-behind-a-guided-setup.md)). Setup needs the network; after it the app works offline.
- **Time and disk for the model download.** The default models are several gigabytes to tens of gigabytes; the pull resumes if the connection drops.

## Why macOS blocks it

macOS Gatekeeper opens only apps signed by a registered developer and checked by Apple. OpenKOS ships without that certificate. The app is still re-signed ad hoc so that macOS can verify it has not been altered, which is why you see a recoverable "Not Opened" message rather than a "damaged" one ([ADR-0056](adr/0056-the-engine-ships-frozen-with-pyinstaller-one-directory-under-contents-resources-deep-ad-hoc-signed.md)). Nothing is wrong with the file; macOS simply has no developer identity to trust. On current macOS this approval cannot be given from the Finder's right-click menu. It is given in System Settings.

Each new build has a new ad-hoc signature, so macOS asks again once per build you install.

## Open it past the warning

1. Open the downloaded `OpenKOS.dmg` and drag **OpenKOS** onto the **Applications** folder.
2. Open **OpenKOS** from Applications. macOS shows **"Not Opened"** with the choices **Move to Trash** and **Done**. Choose **Done**. Do not choose Move to Trash.
3. Open **System Settings**, then **Privacy & Security**, and scroll down to the **Security** section.
4. Find the line saying OpenKOS was blocked and click **Open Anyway**.
5. Confirm with your password or Touch ID, and click **Open Anyway** once more in the dialog that follows.

The app starts. Later launches open without the warning until you install a different build.

## Check the download

The release notes list the **sha256** of the installer, and the release carries the same value in `OpenKOS.dmg.sha256`. Before you open the file, compute it in Terminal and compare:

```bash
shasum -a 256 ~/Downloads/OpenKOS.dmg
```

The two values must match exactly. If they do not, delete the file and download it again; if they still differ, do not open it and [open an issue](https://github.com/jasonssdev/openkos/issues).

## Download

Only after the sections above: [download `OpenKOS.dmg`](https://github.com/jasonssdev/openkos/releases/latest/download/OpenKOS.dmg), the asset of the [latest release](https://github.com/jasonssdev/openkos/releases/latest). Its checksum is at [`OpenKOS.dmg.sha256`](https://github.com/jasonssdev/openkos/releases/latest/download/OpenKOS.dmg.sha256). The app does not update itself; to move to a newer build, download the new installer and replace the app.

## What happens on first launch

The app asks you to pick or create an empty folder for your library, runs the engine's own setup in it, then walks you through installing Ollama and downloading the models ([ADR-0071](adr/0071-the-desktop-shell-remembers-the-chosen-workspace-in-its-own-config-directory-and-creates-one-with-the-engines-init.md)). Your files stay in that folder on your machine. The app asks for no account and sends nothing to a cloud service; it reaches the network only to pull models. Later launches reopen the same folder.

## Uninstall

Drag **OpenKOS** from Applications to the Trash. That removes the app only. Your library folder is yours and stays where you put it. To also forget the remembered folder, delete `~/Library/Application Support/io.github.jasonssdev.openkos/`. If you stored a remote backend's API key in the app, remove the `io.github.jasonssdev.openkos` item from Keychain Access.

## This is a developer build

The app is early software. It is unsigned and not notarized, and the API under it may still change. Keep your own copies of the files you drop in: `raw/` is read-only by design, but a developer build earns no more trust than that. Report anything that surprises you as an [issue](https://github.com/jasonssdev/openkos/issues).
