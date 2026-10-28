//! The macOS notification for a stopped inbox watch (ADR-0078, "Five").
//!
//! A watch that stops on a failure or on the AI model's limit is also shown in
//! the app, but the person may not be looking at it, so the shell raises a
//! notification as well. The text is fixed here, in the wording of
//! `docs/decision-wording.md`: it never carries the daemon's own words (which
//! can hold paths and engine terms), and the page cannot choose it, so the
//! notification permission is never given to the page: only Rust shows one.

use std::sync::Arc;

use crate::watch::OnFailed;

/// What a notification says.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Notice {
    pub title: &'static str,
    pub body: &'static str,
}

/// The daemon failed and the restart budget is spent.
pub fn watch_failed() -> Notice {
    Notice {
        title: "OpenKOS stopped watching your folder",
        body: "Something went wrong, so new files in your folder aren't being added. Open OpenKOS to start watching again.",
    }
}

/// The daemon is idling on the AI model's limit.
pub fn watch_budget() -> Notice {
    Notice {
        title: "OpenKOS paused watching your folder",
        body: "The AI model reached the limit set for it. OpenKOS will add the rest of your files when the limit allows.",
    }
}

/// Where a notice goes: the operating system's notification centre in the app,
/// a recorder in tests.
pub type Show = dyn Fn(&Notice) + Send + Sync;

/// The supervisor's failure callback: log the daemon's last words to stderr
/// (never to a notification) and show the fixed failure notice once.
pub fn on_failed(show: Arc<Show>) -> Box<OnFailed> {
    Box::new(move |reason, detail| {
        eprintln!("openkos-desktop: the inbox watch failed ({reason}): {detail}");
        show(&watch_failed());
    })
}

/// Show `notice` in the operating system's notification centre. A refusal (the
/// person turned notifications off for the app) is not an error: the status in
/// the app says the same.
pub fn show_native<R: tauri::Runtime>(app: &tauri::AppHandle<R>, notice: &Notice) {
    use tauri_plugin_notification::NotificationExt;

    if let Err(error) = app
        .notification()
        .builder()
        .title(notice.title)
        .body(notice.body)
        .show()
    {
        eprintln!("openkos-desktop: the notification could not be shown: {error}");
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Mutex;

    use super::*;

    const ENGINE_WORDS: [&str; 12] = [
        "workspace",
        "daemon",
        "queue",
        "pending",
        "ingest",
        "model call",
        "budget",
        "concept",
        "merge",
        "token",
        "stderr",
        "exit",
    ];

    fn recorder() -> (Arc<Show>, Arc<Mutex<Vec<Notice>>>) {
        let seen = Arc::new(Mutex::new(Vec::new()));
        let sink = Arc::clone(&seen);
        let show: Arc<Show> =
            Arc::new(move |notice: &Notice| sink.lock().unwrap().push(notice.clone()));
        (show, seen)
    }

    #[test]
    fn the_notices_say_something_and_say_different_things() {
        for notice in [watch_failed(), watch_budget()] {
            assert!(!notice.title.is_empty());
            assert!(!notice.body.is_empty());
        }
        assert_ne!(watch_failed(), watch_budget());
    }

    #[test]
    fn the_notices_use_no_engine_word() {
        for notice in [watch_failed(), watch_budget()] {
            let text = format!("{} {}", notice.title, notice.body).to_lowercase();
            for word in ENGINE_WORDS {
                assert!(!text.contains(word), "{word:?} in {text:?}");
            }
        }
    }

    #[test]
    fn a_failure_shows_the_fixed_notice_once_and_never_the_daemons_words() {
        let (show, seen) = recorder();
        let callback = on_failed(show);

        callback(
            "exited",
            "the daemon exited (1).\n/Users/ana/secret-project failed",
        );

        let seen = seen.lock().unwrap();
        assert_eq!(*seen, vec![watch_failed()]);
        assert!(!seen[0].body.contains("secret-project"));
    }
}
