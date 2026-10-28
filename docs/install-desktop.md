# Install the desktop app (macOS)

The OpenKOS desktop app is a **developer build**: it is not signed with an Apple Developer ID and not notarized, so macOS blocks it the first time you open it. Read this page before you download. It tells you what the app needs, why macOS objects, the exact clicks that get past it, and how to check that the file you got is the one the project published.

If you are comfortable in a terminal, the [command-line install](../README.md#quickstart) needs no Gatekeeper approval; the desktop app runs the same engine.

## What the app needs

- **A Mac with Apple Silicon.** Intel Macs, Windows and Linux are not covered ([roadmap](roadmap.md), MVP 6 constraints).
- **32 GB of unified memory for the default setup.** That is the floor measured for the default models: the judge model peaks near 21.5 GB while it runs, and the engine keeps one chat model resident at a time ([ADR-0047](adr/0047-the-contradiction-and-identity-judges-default-to-gemma4-26b-a4b-and-the-engine-runs-one-chat-model-at-a-time.md)). The [README's system requirements](../README.md#system-requirements) give the smaller setups and the disk needed for the models.
- **Ollama.** The app does not bundle a model runtime. Its setup step checks for [Ollama](https://ollama.com), sends you to its official download if it is missing, then pulls the models and shows the progress ([ADR-0057](adr/0057-the-first-model-runtime-is-a-user-installed-ollama-behind-a-guided-setup.md)). Setup needs the network; after it the app works offline. The one other request the app makes is its update check (below), which you can turn off.
- **Time and disk for the model download.** The default models are several gigabytes to tens of gigabytes; the pull resumes if the connection drops.

## Why macOS blocks it

macOS Gatekeeper opens only apps signed by a registered developer and checked by Apple. OpenKOS ships without that certificate. The app is still re-signed ad hoc so that macOS can verify it has not been altered, which is why you see a recoverable "Not Opened" message rather than a "damaged" one ([ADR-0056](adr/0056-the-engine-ships-frozen-with-pyinstaller-one-directory-under-contents-resources-deep-ad-hoc-signed.md)). Nothing is wrong with the file; macOS simply has no developer identity to trust. On current macOS this approval cannot be given from the Finder's right-click menu. It is given in System Settings.

Each new build has a new ad-hoc signature, so macOS asks again once per build you install from a download. Whether a build installed by the app's own updater asks again has not been measured yet.

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

Only after the sections above: [download `OpenKOS.dmg`](https://github.com/jasonssdev/openkos/releases/latest/download/OpenKOS.dmg), the asset of the [latest release](https://github.com/jasonssdev/openkos/releases/latest). Its checksum is at [`OpenKOS.dmg.sha256`](https://github.com/jasonssdev/openkos/releases/latest/download/OpenKOS.dmg.sha256).

## Updates

When it opens, the app asks the project's GitHub releases whether a newer version exists: one request for a small file (`latest.json` on the [latest release](https://github.com/jasonssdev/openkos/releases/latest)), with no account and nothing from your library. Prereleases are never offered. If a newer version exists, **Settings > Updates** and the foot of the sidebar say so, with the release's notes. Nothing is downloaded until you choose **Install and restart** and confirm. The app then downloads the update, checks that it was signed with the project's updater key, installs it and restarts ([ADR-0079](adr/0079-the-desktop-app-updates-itself-through-a-signed-feed-whose-private-key-lives-in-a-protected-release-environment.md)). An update that fails the signature check is refused and the app stays as it was.

To stop the check, choose **Stop checking** in **Settings > Updates**. The choice belongs to the app, not to a library, and is kept in `~/Library/Application Support/io.github.jasonssdev.openkos/updates.json`. With it off the app makes no request at all and works the same; **Check now** still asks once when you want it to, and you can always download a newer installer from the release page instead.

## What happens on first launch

The app asks you to pick or create an empty folder for your library, runs the engine's own setup in it, then walks you through installing Ollama and downloading the models ([ADR-0071](adr/0071-the-desktop-shell-remembers-the-chosen-workspace-in-its-own-config-directory-and-creates-one-with-the-engines-init.md)). Your files stay in that folder on your machine. The app asks for no account and sends nothing to a cloud service; it reaches the network only to pull models and, unless you turn it off, to check for updates. Later launches reopen the same folder.

## Uninstall

Drag **OpenKOS** from Applications to the Trash. That removes the app only. Your library folder is yours and stays where you put it. To also forget the remembered folder, delete `~/Library/Application Support/io.github.jasonssdev.openkos/`. If you stored a remote backend's API key in the app, remove the `io.github.jasonssdev.openkos` item from Keychain Access.

## This is a developer build

The app is early software. It is unsigned and not notarized, and the API under it may still change. Keep your own copies of the files you drop in: `raw/` is read-only by design, but a developer build earns no more trust than that. Report anything that surprises you as an [issue](https://github.com/jasonssdev/openkos/issues).

## For maintainers: the updater key

The update archive is signed by the `desktop-release` job in `.github/workflows/release.yml`, with a key that lives only in the protected GitHub `release` environment ([ADR-0079](adr/0079-the-desktop-app-updates-itself-through-a-signed-feed-whose-private-key-lives-in-a-protected-release-environment.md), [ADR-0081](adr/0081-the-update-archive-is-made-and-signed-by-the-build-script-after-the-ad-hoc-re-sign.md)). Until the steps below are done, `desktop/src-tauri/tauri.conf.json` carries a placeholder public key (`REPLACE-WITH-THE-OPENKOS-UPDATER-PUBLIC-KEY…`) and the release job refuses to run. They are done once, by the owner, on the owner's own machine:

1. **Generate the key pair, with a password.** Install the Tauri CLI (`cargo install tauri-cli --version 2.12.3 --locked`), then run `cargo tauri signer generate -w ~/.tauri/openkos-updater.key` and enter a strong, non-empty password when asked. It writes the private key to `~/.tauri/openkos-updater.key` and the public key to `~/.tauri/openkos-updater.key.pub`. Never commit either file, paste the private key into an issue or a chat, or keep it in the repository directory.
2. **Back it up offline.** Store the private key file's contents and its password as one entry in your password manager. Without this backup, losing the environment secret means no installed app can ever accept another update.
3. **Create the `release` environment.** In the repository's **Settings > Environments**, create an environment named exactly `release`. Under **Deployment protection rules**, turn on **Required reviewers** and add yourself; leave **Prevent self-review** off, since you approve your own releases. Under **Deployment branches and tags**, choose **Selected branches and tags** and add the tag rule `v*.*.*`.
4. **Add the two secrets to that environment**, not to the repository: `TAURI_SIGNING_PRIVATE_KEY`, whose value is the full contents of `~/.tauri/openkos-updater.key`, and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`, the password from step 1.
5. **Commit the public key.** Replace the placeholder `plugins.updater.pubkey` in `desktop/src-tauri/tauri.conf.json` with the full contents of `~/.tauri/openkos-updater.key.pub` (one base64 line), in a pull request. Only builds made after this merge can verify updates.
6. **Release as usual.** Push the `v*.*.*` tag. The `desktop-release` job waits for your approval, then builds the app as that version, makes the update archive from the re-signed, verified `.app`, signs it, and uploads `OpenKOS.app.tar.gz`, its `.sig` and `latest.json` with the `.dmg`. Before trusting the feed, install the previous build, let it offer this one, and confirm it installs and restarts.

To rotate the key while the old one is still trusted, ship one release signed with the old key whose `tauri.conf.json` carries the new public key, then switch the environment secrets to the new pair. ADR-0079 records the response to a lost or compromised key.
