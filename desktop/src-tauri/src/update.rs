//! The app's own updates (ADR-0079).
//!
//! The shell asks the project's feed, a static `latest.json` on the latest
//! GitHub release, whether a newer build exists, and tells the page what it
//! found. Nothing is downloaded or installed until the person confirms the
//! exact version they were shown: the page sends that version back and the
//! shell installs only the update it is holding for it. The updater plugin
//! verifies the archive's signature against the public key compiled into the
//! app before it replaces anything.
//!
//! The check can be turned off. The choice lives in the shell's own
//! configuration directory ([`UpdateChoice`]), not in a workspace: it is about
//! the app, whichever library is open. With it off the shell makes no request
//! at all, and the app works the same.

use std::path::{Path, PathBuf};
use std::sync::Mutex;

use serde::{Deserialize, Serialize};

/// The file in the shell's configuration directory that keeps the choice.
pub const CHOICE_FILE: &str = "updates.json";

/// Whether the person lets the app check for updates. On unless they turned it
/// off: ADR-0079 makes the check the default and the off switch a choice.
pub struct UpdateChoice {
    file: PathBuf,
    serial: Mutex<()>,
}

#[derive(Deserialize, Serialize)]
struct ChoiceFile {
    check: bool,
}

impl UpdateChoice {
    pub fn new(config_dir: &Path) -> Self {
        Self {
            file: config_dir.join(CHOICE_FILE),
            serial: Mutex::new(()),
        }
    }

    /// Whether checking is on. A missing file is the default, on; a file that
    /// cannot be read or parsed is off, so no request goes out on a guess.
    pub fn is_on(&self) -> bool {
        match std::fs::read_to_string(&self.file) {
            Ok(text) => match serde_json::from_str::<ChoiceFile>(&text) {
                Ok(choice) => choice.check,
                Err(_) => {
                    eprintln!(
                        "openkos-desktop: {} is malformed; update checks stay off",
                        self.file.display()
                    );
                    false
                }
            },
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => true,
            Err(_) => false,
        }
    }

    /// Remember `on`. Written to a temporary file and renamed, so a crash never
    /// leaves half a choice.
    pub fn set(&self, on: bool) -> std::io::Result<()> {
        let _serial = self.serial.lock().expect("update choice lock");
        if let Some(dir) = self.file.parent() {
            std::fs::create_dir_all(dir)?;
        }
        let body =
            serde_json::to_string(&ChoiceFile { check: on }).map_err(std::io::Error::other)?;
        let partial = self.file.with_extension("json.partial");
        std::fs::write(&partial, body)?;
        std::fs::rename(&partial, &self.file)
    }
}

/// The update settings as the page reads them.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct UpdateSettings {
    pub check: bool,
}

/// What one check found, as the page reads it. A failure carries no detail:
/// an unreachable feed is not the person's problem, and the app keeps working.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum UpdateCheck {
    /// The person turned checking off; nothing was asked.
    Off,
    UpToDate,
    Available {
        version: String,
        current_version: String,
        notes: Option<String>,
    },
    Failed,
}

/// A newer build the feed announced, before anything is downloaded.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Found {
    pub version: String,
    pub current_version: String,
    pub notes: Option<String>,
}

/// Ask `fetch` only when checking is `on`. Off makes no request at all.
pub async fn check_when_on<U, E, F, Fut>(on: bool, fetch: F) -> (UpdateCheck, Option<U>)
where
    F: FnOnce() -> Fut,
    Fut: std::future::Future<Output = Result<Option<(Found, U)>, E>>,
{
    if !on {
        return (UpdateCheck::Off, None);
    }
    match fetch().await {
        Ok(None) => (UpdateCheck::UpToDate, None),
        Ok(Some((found, update))) => (
            UpdateCheck::Available {
                version: found.version,
                current_version: found.current_version,
                notes: found.notes,
            },
            Some(update),
        ),
        Err(_) => (UpdateCheck::Failed, None),
    }
}

/// What an update command answers the page when it refuses: a reason a screen
/// can branch on and an English detail.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct UpdateCommandError {
    pub reason: &'static str,
    pub detail: String,
}

impl UpdateCommandError {
    pub fn new(reason: &'static str, detail: impl Into<String>) -> Self {
        Self {
            reason,
            detail: detail.into(),
        }
    }
}

/// The update the page was last shown, held until the person confirms it.
pub struct Pending<U> {
    held: Mutex<Option<(String, U)>>,
}

impl<U> Default for Pending<U> {
    fn default() -> Self {
        Self {
            held: Mutex::new(None),
        }
    }
}

impl<U> Pending<U> {
    /// Hold what a check found, replacing anything older; a check that found
    /// nothing leaves nothing to install.
    pub fn hold(&self, version: Option<String>, update: Option<U>) {
        let mut held = self.held.lock().expect("pending update lock");
        *held = version.zip(update);
    }

    /// The update for `version`, the one the person confirmed. Refused when
    /// none is held, or when the one held is not the version they saw.
    pub fn take(&self, version: &str) -> Result<U, UpdateCommandError> {
        let mut held = self.held.lock().expect("pending update lock");
        match held.take() {
            Some((shown, update)) if shown == version => Ok(update),
            Some(other) => {
                *held = Some(other);
                Err(UpdateCommandError::new(
                    "update_changed",
                    "the update on offer is not the one confirmed; check again",
                ))
            }
            None => Err(UpdateCommandError::new(
                "no_update",
                "there is no update to install; check again",
            )),
        }
    }
}

#[cfg(test)]
mod tests {
    use std::cell::Cell;

    use super::*;

    fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "openkos-update-{name}-{}-{:?}",
            std::process::id(),
            std::time::SystemTime::now()
        ));
        std::fs::create_dir_all(&dir).expect("scratch");
        dir
    }

    fn found(version: &str) -> Found {
        Found {
            version: version.to_owned(),
            current_version: "0.6.0".to_owned(),
            notes: Some("Notes.".to_owned()),
        }
    }

    #[test]
    fn checking_is_on_until_the_person_turns_it_off() {
        let choice = UpdateChoice::new(&scratch("default"));
        assert!(choice.is_on());
        choice.set(false).expect("kept");
        assert!(!choice.is_on());
        choice.set(true).expect("kept");
        assert!(choice.is_on());
    }

    #[test]
    fn the_choice_outlives_the_app() {
        let dir = scratch("reopen");
        UpdateChoice::new(&dir).set(false).expect("kept");
        assert!(!UpdateChoice::new(&dir).is_on());
    }

    #[test]
    fn a_malformed_choice_is_off_so_nothing_is_asked_on_a_guess() {
        let dir = scratch("malformed");
        std::fs::write(dir.join(CHOICE_FILE), "{not json").expect("write");
        assert!(!UpdateChoice::new(&dir).is_on());
    }

    #[test]
    fn off_makes_no_request() {
        let asked = Cell::new(false);
        let (check, update) = tauri::async_runtime::block_on(check_when_on(false, || {
            asked.set(true);
            async { Ok::<_, ()>(Some((found("0.7.0"), ()))) }
        }));
        assert_eq!(check, UpdateCheck::Off);
        assert!(update.is_none());
        assert!(!asked.get());
    }

    #[test]
    fn on_reports_what_the_feed_announced() {
        let (check, update) = tauri::async_runtime::block_on(check_when_on(true, || async {
            Ok::<_, ()>(Some((found("0.7.0"), 7u8)))
        }));
        assert_eq!(
            check,
            UpdateCheck::Available {
                version: "0.7.0".to_owned(),
                current_version: "0.6.0".to_owned(),
                notes: Some("Notes.".to_owned()),
            }
        );
        assert_eq!(update, Some(7));
    }

    #[test]
    fn nothing_newer_and_a_failed_check_hold_nothing() {
        let (check, update) = tauri::async_runtime::block_on(check_when_on(true, || async {
            Ok::<Option<(Found, ())>, ()>(None)
        }));
        assert_eq!(check, UpdateCheck::UpToDate);
        assert!(update.is_none());
        let (check, update) = tauri::async_runtime::block_on(check_when_on(true, || async {
            Err::<Option<(Found, ())>, _>("offline")
        }));
        assert_eq!(check, UpdateCheck::Failed);
        assert!(update.is_none());
    }

    #[test]
    fn a_failure_tells_the_page_nothing_but_that_it_failed() {
        let json = serde_json::to_value(UpdateCheck::Failed).expect("json");
        assert_eq!(json, serde_json::json!({ "state": "failed" }));
    }

    #[test]
    fn only_the_version_the_person_saw_is_installed() {
        let pending = Pending::default();
        pending.hold(Some("0.7.0".to_owned()), Some(7u8));
        let refused = pending.take("0.8.0").expect_err("not the one shown");
        assert_eq!(refused.reason, "update_changed");
        // Still held: the person can confirm what they were shown.
        assert_eq!(pending.take("0.7.0"), Ok(7));
        assert_eq!(
            pending.take("0.7.0").expect_err("taken").reason,
            "no_update"
        );
    }

    fn config() -> serde_json::Value {
        serde_json::from_str(include_str!("../tauri.conf.json")).expect("tauri.conf.json")
    }

    #[test]
    fn the_feed_is_the_latest_github_release_over_https_only() {
        let updater = &config()["plugins"]["updater"];
        assert_eq!(
            updater["endpoints"],
            serde_json::json!([
                "https://github.com/jasonssdev/openkos/releases/latest/download/latest.json"
            ])
        );
        let keys = updater.as_object().expect("updater config");
        assert!(
            !keys.keys().any(|key| key.starts_with("dangerous")),
            "{keys:?}"
        );
    }

    #[test]
    fn an_update_must_be_signed_for_the_version_the_feed_announces() {
        // Refuses a feed that pairs a newer version number with an older,
        // genuinely signed archive (a downgrade); build-app.sh signs with
        // --app-version for exactly this.
        assert_eq!(config()["plugins"]["updater"]["requireSignedVersion"], true);
    }

    #[test]
    fn the_public_key_is_committed_in_the_config() {
        let pubkey = config()["plugins"]["updater"]["pubkey"]
            .as_str()
            .expect("pubkey")
            .to_owned();
        assert!(!pubkey.trim().is_empty());
    }

    #[test]
    fn the_page_gains_no_network_reach_the_check_runs_in_the_shell() {
        // The CSP governs the web view only; the shell's request is not the
        // page's, so the page's connect-src stays IPC and loopback.
        let csp = config()["app"]["security"]["csp"]
            .as_str()
            .expect("csp")
            .to_owned();
        assert!(!csp.contains("github"), "{csp}");
    }

    #[test]
    fn a_check_that_found_nothing_leaves_nothing_to_install() {
        let pending = Pending::default();
        pending.hold(Some("0.7.0".to_owned()), Some(7u8));
        pending.hold(None, None);
        assert_eq!(pending.take("0.7.0").expect_err("none").reason, "no_update");
    }
}
