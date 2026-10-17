//! Opening the model runtime's official download page in the system browser
//! (ADR-0057). The page names no address: the shell opens this one fixed URL.
//!
//! Tauri's core has no way to open a URL outside the web view, and its opener
//! plugin would add a dependency and a permission surface for one fixed
//! address. The operating system's own opener, run directly with the URL as a
//! single argument and no shell in between, does the same.

use std::process::{Command, Stdio};

/// Ollama's official download page: the engine's `OLLAMA_DOWNLOAD_URL`
/// (`src/openkos/application/setup.py`), which a test holds this equal to.
pub const RUNTIME_DOWNLOAD_URL: &str = "https://ollama.com/download";

/// The program and arguments that open `url` in the default browser.
pub fn opener(url: &str) -> (&'static str, Vec<String>) {
    #[cfg(target_os = "macos")]
    return ("open", vec![url.to_owned()]);
    #[cfg(target_os = "windows")]
    return (
        "rundll32",
        vec!["url.dll,FileProtocolHandler".to_owned(), url.to_owned()],
    );
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    return ("xdg-open", vec![url.to_owned()]);
}

/// Open the runtime's download page; an English detail when the opener fails.
pub fn open_runtime_download() -> Result<(), String> {
    let (program, args) = opener(RUNTIME_DOWNLOAD_URL);
    let status = Command::new(program)
        .args(&args)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map_err(|error| format!("the browser could not be opened: {error}"))?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("the browser could not be opened ({status})"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_address_is_the_one_the_engine_reports() {
        let setup = std::fs::read_to_string(
            std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../../src/openkos/application/setup.py"),
        )
        .expect("the engine's setup service");
        assert!(setup.contains(&format!(
            "OLLAMA_DOWNLOAD_URL: Final = \"{RUNTIME_DOWNLOAD_URL}\""
        )));
        assert!(RUNTIME_DOWNLOAD_URL.starts_with("https://"));
    }

    #[test]
    fn the_browser_is_opened_with_the_address_as_one_argument_and_no_shell() {
        let (program, args) = opener(RUNTIME_DOWNLOAD_URL);
        assert_eq!(args.last().map(String::as_str), Some(RUNTIME_DOWNLOAD_URL));
        #[cfg(target_os = "macos")]
        assert_eq!((program, args.len()), ("open", 1));
        #[cfg(target_os = "linux")]
        assert_eq!((program, args.len()), ("xdg-open", 1));
        assert!(!["sh", "bash", "cmd"].contains(&program));
    }
}
