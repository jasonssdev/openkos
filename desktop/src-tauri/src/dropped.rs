//! Files dropped on the window (#1626).
//!
//! The operating system's drop reaches the shell, not the page, and carries the
//! files' absolute paths. The shell hands them to the page as a plain DOM event,
//! so the page needs no event, window or file-system permission and no
//! capability is added. The page only quotes the paths (ADR-0073); nothing is
//! read or added from here.

use std::path::PathBuf;

/// The DOM events the page listens for (`desktop/ui/add/drop.ts`).
pub const DROP_EVENT: &str = "openkos:files-dropped";
pub const OVER_EVENT: &str = "openkos:files-over";

/// The script that tells the page these paths were dropped. The paths are
/// serialised as a JSON array of strings, so a file name cannot break out of it.
pub fn drop_script(paths: &[PathBuf]) -> String {
    let names: Vec<String> = paths
        .iter()
        .map(|path| path.to_string_lossy().into_owned())
        .collect();
    let detail = serde_json::to_string(&names).expect("a list of strings serialises");
    dispatch(DROP_EVENT, &detail)
}

/// The script that tells the page files are (or no longer are) held over it.
pub fn over_script(over: bool) -> String {
    dispatch(OVER_EVENT, if over { "true" } else { "false" })
}

fn dispatch(event: &str, detail: &str) -> String {
    format!("window.dispatchEvent(new CustomEvent('{event}', {{ detail: {detail} }}));")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_drop_carries_every_path_as_json() {
        let script = drop_script(&[
            PathBuf::from("/Users/me/a.md"),
            PathBuf::from("/Users/me/b c.txt"),
        ]);
        assert_eq!(
            script,
            "window.dispatchEvent(new CustomEvent('openkos:files-dropped', { detail: [\"/Users/me/a.md\",\"/Users/me/b c.txt\"] }));"
        );
    }

    #[test]
    fn a_hostile_file_name_stays_inside_its_string() {
        let name = "/x/\"]});alert(1);//'.md";
        let script = drop_script(&[PathBuf::from(name)]);
        let json = serde_json::to_string(&vec![name]).unwrap();
        assert!(script.contains(&format!("detail: {json} }}")));
        assert!(script.contains("\\\"]});alert"), "the quote is escaped");
    }

    #[test]
    fn hovering_says_whether_files_are_over_the_window() {
        assert!(over_script(true).contains("'openkos:files-over', { detail: true }"));
        assert!(over_script(false).contains("{ detail: false }"));
    }

    #[test]
    fn the_event_names_are_the_ones_the_page_listens_for() {
        let page = std::fs::read_to_string(
            std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../ui/add/drop.ts"),
        )
        .expect("the page's drop module");
        assert!(page.contains(&format!("DROP_EVENT = \"{DROP_EVENT}\"")));
        assert!(page.contains(&format!("DRAG_EVENT = \"{OVER_EVENT}\"")));
    }
}
