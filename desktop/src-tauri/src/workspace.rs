//! The workspace the shell serves when `OPENKOS_WORKSPACE` is unset.
//!
//! A person who never opens a terminal chooses a folder in the app: an
//! existing workspace to open, or an empty folder to create one in. Creating
//! one runs the engine's own `init` there (#1487); the shell never writes a
//! workspace itself. The choice is remembered in one small file in the shell's
//! own configuration directory, never inside the workspace, and read before
//! each engine start. The variable still wins, for development.

use std::ffi::OsStr;
use std::fmt;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use serde::Serialize;

/// The file `init` writes last, so its presence means a complete workspace.
pub const WORKSPACE_MARKER: &str = "openkos.yaml";

/// The file, in the shell's configuration directory, holding the choice.
pub const CHOICE_FILE: &str = "workspace.json";

/// A file macOS writes into folders it shows; it does not make one non-empty.
const FINDER_METADATA: &str = ".DS_Store";

/// Whether `dir` is an OpenKOS workspace.
pub fn is_workspace(dir: &Path) -> bool {
    dir.join(WORKSPACE_MARKER).is_file()
}

/// Whether `dir` holds nothing but Finder's metadata.
fn is_empty_folder(dir: &Path) -> std::io::Result<bool> {
    for entry in std::fs::read_dir(dir)? {
        if entry?.file_name() != OsStr::new(FINDER_METADATA) {
            return Ok(false);
        }
    }
    Ok(true)
}

/// The remembered choice: one JSON file in the shell's configuration directory.
pub struct Choice {
    file: PathBuf,
}

#[derive(serde::Deserialize, Serialize)]
struct ChoiceFile {
    workspace: PathBuf,
}

impl Choice {
    /// The choice kept in `config_dir`, which need not exist yet.
    pub fn new(config_dir: &Path) -> Self {
        Self {
            file: config_dir.join(CHOICE_FILE),
        }
    }

    /// The remembered folder, if one is remembered. A missing, unreadable or
    /// malformed file, or a relative path in it, is no choice: the page asks
    /// again rather than serving a folder nobody chose.
    pub fn remembered(&self) -> Option<PathBuf> {
        let text = match std::fs::read_to_string(&self.file) {
            Ok(text) => text,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return None,
            Err(error) => {
                eprintln!("openkos-desktop: the remembered workspace is unreadable: {error}");
                return None;
            }
        };
        match serde_json::from_str::<ChoiceFile>(&text) {
            Ok(choice) if choice.workspace.is_absolute() => Some(choice.workspace),
            _ => {
                eprintln!(
                    "openkos-desktop: the remembered workspace in {} is malformed; ignoring it",
                    self.file.display()
                );
                None
            }
        }
    }

    /// Remember `workspace`, replacing any earlier choice. Written to a
    /// temporary file and renamed, so a crash never leaves half a choice.
    pub fn remember(&self, workspace: &Path) -> std::io::Result<()> {
        if let Some(dir) = self.file.parent() {
            std::fs::create_dir_all(dir)?;
        }
        let body = serde_json::to_string(&ChoiceFile {
            workspace: workspace.to_path_buf(),
        })
        .map_err(std::io::Error::other)?;
        let partial = self.file.with_extension("json.partial");
        std::fs::write(&partial, body)?;
        std::fs::rename(&partial, &self.file)
    }
}

/// Where the served workspace comes from.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ChoiceSource {
    /// `OPENKOS_WORKSPACE` names it; the app cannot change it.
    Environment,
    /// The user chose it in the app.
    Remembered,
    /// Nothing is chosen yet.
    None,
}

/// What the page learns about the workspace choice.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct ChoiceStatus {
    pub source: ChoiceSource,
    /// The folder, when there is one.
    pub path: Option<String>,
    /// Whether that folder is a workspace now; false when there is none.
    pub valid: bool,
}

impl ChoiceStatus {
    fn of(source: ChoiceSource, path: Option<&Path>) -> Self {
        Self {
            source,
            path: path.map(|p| p.to_string_lossy().into_owned()),
            valid: path.is_some_and(is_workspace),
        }
    }
}

/// The choice as the page sees it: the variable wins over the remembered one.
pub fn status(env_workspace: Option<&Path>, remembered: Option<&Path>) -> ChoiceStatus {
    match (env_workspace, remembered) {
        (Some(path), _) => ChoiceStatus::of(ChoiceSource::Environment, Some(path)),
        (None, Some(path)) => ChoiceStatus::of(ChoiceSource::Remembered, Some(path)),
        (None, None) => ChoiceStatus::of(ChoiceSource::None, None),
    }
}

/// What a workspace command answers the page when it refuses: a reason a
/// screen can branch on and an English detail.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct WorkspaceCommandError {
    pub reason: &'static str,
    pub detail: String,
}

impl WorkspaceCommandError {
    pub fn new(reason: &'static str, detail: impl Into<String>) -> Self {
        Self {
            reason,
            detail: detail.into(),
        }
    }
}

impl fmt::Display for WorkspaceCommandError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.reason, self.detail)
    }
}

type CommandResult<T> = Result<T, WorkspaceCommandError>;

/// Runs the engine's `init` in a folder.
pub type Init<'a> = &'a dyn Fn(&Path) -> CommandResult<()>;

/// The workspace commands, serialised: one open or create at a time.
pub struct Workspaces {
    choice: Choice,
    serial: Mutex<()>,
}

impl Workspaces {
    pub fn new(choice: Choice) -> Self {
        Self {
            choice,
            serial: Mutex::new(()),
        }
    }

    /// The remembered folder, read afresh.
    pub fn remembered(&self) -> Option<PathBuf> {
        self.choice.remembered()
    }

    /// The choice as the page sees it, given the variable's workspace.
    pub fn status(&self, env_workspace: Option<&Path>) -> ChoiceStatus {
        status(env_workspace, self.choice.remembered().as_deref())
    }

    /// Open the existing workspace at `path`: remember it and `restart` the
    /// engine on it.
    pub fn open(
        &self,
        env_workspace: Option<&Path>,
        path: &str,
        restart: &dyn Fn(),
    ) -> CommandResult<ChoiceStatus> {
        let _serial = self.serial.lock().expect("workspace lock");
        refuse_under_environment(env_workspace)?;
        let dir = existing_folder(&absolute(path)?)?;
        if !is_workspace(&dir) {
            return Err(WorkspaceCommandError::new(
                "not_a_workspace",
                format!(
                    "{} is not an OpenKOS workspace: it has no {WORKSPACE_MARKER}",
                    dir.display()
                ),
            ));
        }
        self.remember_and_restart(&dir, restart)
    }

    /// Create a workspace in the empty or missing folder `path` by running the
    /// engine's `init` there, then remember it and `restart` the engine on it.
    pub fn create(
        &self,
        env_workspace: Option<&Path>,
        path: &str,
        init: Init<'_>,
        restart: &dyn Fn(),
    ) -> CommandResult<ChoiceStatus> {
        let _serial = self.serial.lock().expect("workspace lock");
        refuse_under_environment(env_workspace)?;
        let requested = absolute(path)?;
        if !requested.exists() {
            // Only the folder itself: a missing parent is a mistyped path.
            std::fs::create_dir(&requested).map_err(|error| {
                WorkspaceCommandError::new(
                    "not_a_folder",
                    format!("{} could not be created: {error}", requested.display()),
                )
            })?;
        }
        let dir = existing_folder(&requested)?;
        if is_workspace(&dir) {
            return Err(WorkspaceCommandError::new(
                "already_a_workspace",
                format!("{} is already an OpenKOS workspace; open it", dir.display()),
            ));
        }
        // `init` would turn any folder into a git repository with a workspace
        // in it; only an empty one is safe to hand it from a picker.
        let empty = is_empty_folder(&dir).map_err(|error| {
            WorkspaceCommandError::new(
                "not_a_folder",
                format!("{} could not be read: {error}", dir.display()),
            )
        })?;
        if !empty {
            return Err(WorkspaceCommandError::new(
                "folder_not_empty",
                format!(
                    "{} is not empty; choose an empty folder for a new workspace",
                    dir.display()
                ),
            ));
        }
        init(&dir)?;
        if !is_workspace(&dir) {
            return Err(WorkspaceCommandError::new(
                "init_failed",
                format!(
                    "the engine's init finished but {} has no {WORKSPACE_MARKER}",
                    dir.display()
                ),
            ));
        }
        self.remember_and_restart(&dir, restart)
    }

    fn remember_and_restart(&self, dir: &Path, restart: &dyn Fn()) -> CommandResult<ChoiceStatus> {
        self.choice.remember(dir).map_err(|error| {
            WorkspaceCommandError::new(
                "store_failed",
                format!("the workspace choice could not be saved: {error}"),
            )
        })?;
        restart();
        Ok(status(None, Some(dir)))
    }
}

fn refuse_under_environment(env_workspace: Option<&Path>) -> CommandResult<()> {
    match env_workspace {
        Some(path) => Err(WorkspaceCommandError::new(
            "set_by_environment",
            format!(
                "OPENKOS_WORKSPACE names {}; unset it to choose a workspace in the app",
                path.display()
            ),
        )),
        None => Ok(()),
    }
}

fn absolute(path: &str) -> CommandResult<PathBuf> {
    let path = PathBuf::from(path);
    if path.is_absolute() {
        Ok(path)
    } else {
        Err(WorkspaceCommandError::new(
            "invalid_path",
            format!("{} is not an absolute path", path.display()),
        ))
    }
}

/// `path` as a real directory with no symlinked segment: the engine refuses a
/// workspace path with one.
fn existing_folder(path: &Path) -> CommandResult<PathBuf> {
    match std::fs::canonicalize(path) {
        Ok(dir) if dir.is_dir() => Ok(dir),
        Ok(_) => Err(WorkspaceCommandError::new(
            "not_a_folder",
            format!("{} is not a folder", path.display()),
        )),
        Err(error) => Err(WorkspaceCommandError::new(
            "not_a_folder",
            format!("{} is not a folder: {error}", path.display()),
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::Cell;

    fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "openkos-workspace-{name}-{}-{:?}",
            std::process::id(),
            std::time::SystemTime::now()
        ));
        std::fs::create_dir_all(&dir).expect("scratch");
        std::fs::canonicalize(dir).expect("canonical")
    }

    fn make_workspace(dir: &Path) {
        std::fs::create_dir_all(dir).expect("dir");
        std::fs::write(dir.join(WORKSPACE_MARKER), b"backend: ollama\n").expect("marker");
    }

    fn workspaces(root: &Path) -> Workspaces {
        Workspaces::new(Choice::new(&root.join("config")))
    }

    fn text(path: &Path) -> &str {
        path.to_str().expect("utf-8 path")
    }

    /// An init that writes the marker, as the engine's does, and counts runs.
    fn fake_init(runs: &Cell<u32>) -> impl Fn(&Path) -> CommandResult<()> + '_ {
        move |dir| {
            runs.set(runs.get() + 1);
            make_workspace(dir);
            Ok(())
        }
    }

    fn never_init(_: &Path) -> CommandResult<()> {
        panic!("init must not run")
    }

    #[test]
    fn a_fresh_shell_remembers_nothing() {
        let root = scratch("fresh");
        assert_eq!(Choice::new(&root.join("config")).remembered(), None);
        assert_eq!(
            workspaces(&root).status(None),
            ChoiceStatus {
                source: ChoiceSource::None,
                path: None,
                valid: false
            }
        );
    }

    #[test]
    fn a_choice_is_remembered_in_the_config_directory_only() {
        let root = scratch("remember");
        let workspace = root.join("kb");
        make_workspace(&workspace);
        let choice = Choice::new(&root.join("config"));
        choice.remember(&workspace).expect("remembered");
        assert_eq!(choice.remembered(), Some(workspace.clone()));
        assert!(root.join("config").join(CHOICE_FILE).is_file());
        let mut written: Vec<_> = std::fs::read_dir(&workspace)
            .expect("workspace")
            .map(|e| e.expect("entry").file_name())
            .collect();
        written.sort();
        assert_eq!(
            written,
            [WORKSPACE_MARKER],
            "nothing written into the workspace"
        );
    }

    #[test]
    fn a_malformed_or_relative_choice_is_no_choice() {
        let root = scratch("malformed");
        let config = root.join("config");
        std::fs::create_dir_all(&config).expect("config");
        for body in ["not json", "{}", r#"{"workspace": "relative/kb"}"#] {
            std::fs::write(config.join(CHOICE_FILE), body).expect("write");
            assert_eq!(Choice::new(&config).remembered(), None, "{body}");
        }
    }

    #[test]
    fn the_variable_wins_over_the_remembered_choice() {
        let root = scratch("status");
        let env = root.join("env");
        make_workspace(&env);
        let gone = root.join("gone");
        assert_eq!(
            status(Some(&env), Some(&gone)),
            ChoiceStatus {
                source: ChoiceSource::Environment,
                path: Some(text(&env).to_owned()),
                valid: true
            }
        );
        assert_eq!(
            status(None, Some(&gone)),
            ChoiceStatus {
                source: ChoiceSource::Remembered,
                path: Some(text(&gone).to_owned()),
                valid: false
            }
        );
    }

    #[test]
    fn the_status_serialises_as_the_page_reads_it() {
        let json = serde_json::to_value(status(None, None)).expect("json");
        assert_eq!(
            json,
            serde_json::json!({"source": "none", "path": null, "valid": false})
        );
    }

    #[test]
    fn opening_a_workspace_remembers_it_and_restarts_the_engine_once() {
        let root = scratch("open");
        let workspace = root.join("kb");
        make_workspace(&workspace);
        let ws = workspaces(&root);
        let restarts = Cell::new(0);
        let answer = ws
            .open(None, text(&workspace), &|| restarts.set(restarts.get() + 1))
            .expect("opened");
        assert_eq!(restarts.get(), 1);
        assert_eq!(answer.source, ChoiceSource::Remembered);
        assert!(answer.valid);
        assert_eq!(ws.remembered(), Some(workspace));
    }

    #[cfg(unix)]
    #[test]
    fn opening_a_symlinked_path_remembers_the_real_folder() {
        let root = scratch("symlink");
        let workspace = root.join("kb");
        make_workspace(&workspace);
        let link = root.join("link");
        std::os::unix::fs::symlink(&workspace, &link).expect("symlink");
        let ws = workspaces(&root);
        ws.open(None, text(&link), &|| {}).expect("opened");
        assert_eq!(ws.remembered(), Some(workspace));
    }

    #[test]
    fn opening_refuses_what_is_not_a_workspace_and_changes_nothing() {
        let root = scratch("open-refused");
        let plain = root.join("plain");
        std::fs::create_dir_all(&plain).expect("plain");
        let file = root.join("file");
        std::fs::write(&file, b"x").expect("file");
        let ws = workspaces(&root);
        for (path, reason) in [
            ("relative/kb", "invalid_path"),
            (text(&root.join("missing")), "not_a_folder"),
            (text(&file), "not_a_folder"),
            (text(&plain), "not_a_workspace"),
        ] {
            let err = ws
                .open(None, path, &|| panic!("no restart"))
                .expect_err("refused");
            assert_eq!(err.reason, reason, "{path}: {err}");
        }
        assert_eq!(ws.remembered(), None);
    }

    #[test]
    fn the_variable_makes_both_commands_refuse_before_anything_is_written() {
        let root = scratch("env-refused");
        let env = root.join("env");
        make_workspace(&env);
        let folder = root.join("new");
        let ws = workspaces(&root);
        let open = ws
            .open(Some(&env), text(&env), &|| panic!("no restart"))
            .expect_err("refused");
        assert_eq!(open.reason, "set_by_environment");
        let create = ws
            .create(Some(&env), text(&folder), &never_init, &|| {
                panic!("no restart")
            })
            .expect_err("refused");
        assert_eq!(create.reason, "set_by_environment");
        assert!(!folder.exists());
        assert_eq!(ws.remembered(), None);
    }

    #[test]
    fn creating_in_an_empty_folder_runs_init_then_remembers_and_restarts() {
        let root = scratch("create");
        let folder = root.join("kb");
        std::fs::create_dir_all(&folder).expect("folder");
        std::fs::write(folder.join(FINDER_METADATA), b"").expect("finder");
        let ws = workspaces(&root);
        let runs = Cell::new(0);
        let restarts = Cell::new(0);
        let answer = ws
            .create(None, text(&folder), &fake_init(&runs), &|| {
                restarts.set(restarts.get() + 1)
            })
            .expect("created");
        assert_eq!((runs.get(), restarts.get()), (1, 1));
        assert!(answer.valid);
        assert_eq!(ws.remembered(), Some(folder));
    }

    #[test]
    fn creating_in_a_missing_folder_makes_it_first() {
        let root = scratch("create-missing");
        let folder = root.join("kb");
        let ws = workspaces(&root);
        let runs = Cell::new(0);
        ws.create(None, text(&folder), &fake_init(&runs), &|| {})
            .expect("created");
        assert!(is_workspace(&folder));
        let deep = root.join("no/such/parent");
        let err = ws
            .create(None, text(&deep), &never_init, &|| {})
            .expect_err("refused");
        assert_eq!(err.reason, "not_a_folder");
        assert!(!root.join("no").exists());
    }

    #[test]
    fn creating_refuses_a_non_empty_folder_or_an_existing_workspace() {
        let root = scratch("create-refused");
        let busy = root.join("busy");
        std::fs::create_dir_all(&busy).expect("busy");
        std::fs::write(busy.join("notes.txt"), b"mine").expect("notes");
        let existing = root.join("existing");
        make_workspace(&existing);
        let ws = workspaces(&root);
        for (path, reason) in [
            (busy.as_path(), "folder_not_empty"),
            (existing.as_path(), "already_a_workspace"),
        ] {
            let err = ws
                .create(None, text(path), &never_init, &|| panic!("no restart"))
                .expect_err("refused");
            assert_eq!(err.reason, reason, "{err}");
        }
        assert_eq!(
            std::fs::read(busy.join("notes.txt")).expect("untouched"),
            b"mine"
        );
        assert_eq!(ws.remembered(), None);
    }

    #[test]
    fn a_failed_init_is_reported_and_nothing_is_remembered() {
        let root = scratch("create-failed");
        let folder = root.join("kb");
        let ws = workspaces(&root);
        let failing = |_: &Path| -> CommandResult<()> {
            Err(WorkspaceCommandError::new("init_failed", "boom"))
        };
        let err = ws
            .create(None, text(&folder), &failing, &|| panic!("no restart"))
            .expect_err("failed");
        assert_eq!(err, WorkspaceCommandError::new("init_failed", "boom"));
        let silent = |_: &Path| -> CommandResult<()> { Ok(()) };
        let err = ws
            .create(None, text(&folder), &silent, &|| panic!("no restart"))
            .expect_err("no marker");
        assert_eq!(err.reason, "init_failed");
        assert_eq!(ws.remembered(), None);
    }
}
